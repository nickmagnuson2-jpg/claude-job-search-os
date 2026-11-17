#!/usr/bin/env python3
"""land_branch.py: check branches for landing. Read-only. Never rebases, never fetches.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/land_branch.py <branch> [<branch> ...] [--json] [--suite]

For each branch:
  1. Its worktree, from `git worktree list --porcelain`. Refused when there is none, or when the
     worktree has uncommitted changes to tracked files.
  2. Its upstream. Refused when the upstream is main: a bare `git push` from that worktree
     would push main (guard-edit-fp-1116, 2026-11-16).
  3. GitHub's heads, read with `gh api` (read-only). Local origin/main is stale whenever
     nobody fetched, and fetch is Touch-ID gated, so local refs are never trusted for this.
  4. GitHub main rebuilt locally when it is not in the object store: if every parent of
     GitHub's main commit is local, `merge-tree` of the parents must reproduce GitHub's tree
     sha exactly; `commit-tree` then gives an unreferenced commit with main's exact content.
     Otherwise the check falls back to local origin/main and says so. Each branch is then
     merge-tree'd against that main, and every pair of branches against each other.
  5. The push gate dry run: the pre-push hook git would run (`git rev-parse --git-path hooks`),
     called from the branch's worktree with the refs line and push URL git would hand it.
     That hook runs the BRANCH'S OWN tools/cross_model_gate.py, exactly as a real push does,
     so this step is as trustworthy as the push itself and no more.
  6. `tools/audit_pii_record.py check --tip <sha>`, pointed at the worktree.
  7. `tools/check_public_pii.py --scan --stdin-paths` over the branch's added/modified files.
  8. Optional (--suite): `tools/suite_record.py check --tip <sha>` when that tool exists;
     skipped with a notice when it does not.

Steps 6 to 8 run the MAIN CHECKOUT's copies of those tools, never the branch's: a branch that
edits its own audit or PII scanner must not get to judge itself (Codex r1 F1, P0). Those
copies are whatever the main checkout has checked out, so the report prints that commit and
warns when it is not GitHub main, and the PR text says which copies ran.

A branch is also refused when GitHub already has it at a sha that is not an ancestor of the
local tip (the printed push would be rejected as non-fast-forward), and when the merge check
could only run against a local origin/main that differs from GitHub main.

Then it prints a push command with an explicit refspec for each ready branch, and a merge
order: branches in no conflicting pair first, then each conflicting pair with its files.

Writes: none, except the objects `git merge-tree --write-tree` and `git commit-tree` put in
the object store (unreferenced, collected by gc). It never rebases: a rebase changes the tree
and voids the reviews keyed to it (Nick's tree-hash ruling, 2026-11-16).

Exit: 0 every branch ready, 1 any branch not ready (or GitHub unreadable), 2 usage error.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

ZERO = "0" * 40
# Env vars that turn a gate dry run into a recorded waiver. A read-only check must never
# carry them through, or running it from a shell that has one set would write a ledger row.
WAIVER_ENV = ("CODEX_VERIFY_WAIVE", "AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MAIN_NAMES = {"main", "origin/main", "refs/heads/main", "refs/remotes/origin/main"}


# ---------------------------------------------------------------- process helpers
def run(cmd: list[str], cwd: str | Path | None = None, stdin: str | None = None,
        timeout: int = 600) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if k not in WAIVER_ENV}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, input=stdin, text=True,
                           capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", f"{type(e).__name__}: {e}"
    return p.returncode, p.stdout, p.stderr


def git(args: list[str], cwd: str | Path) -> tuple[int, str, str]:
    return run(["git", *args], cwd=cwd)


# ---------------------------------------------------------------- worktrees and refs
def parse_worktrees(porcelain: str) -> dict[str, str]:
    """{branch name: worktree path} from `git worktree list --porcelain`."""
    out: dict[str, str] = {}
    path = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch ") and path:
            ref = line[len("branch "):]
            out[ref.removeprefix("refs/heads/")] = path
    return out


def repo_slug(url: str) -> str | None:
    """owner/repo from a GitHub remote URL (ssh or https)."""
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def upstream_is_main(upstream: str) -> bool:
    return upstream.strip() in MAIN_NAMES or upstream.strip().endswith("/main")


def configured_upstream(wt: Path, branch: str) -> str | None:
    """`<remote>/<branch>` from git config. Read from config, not `@{upstream}`: that fails
    when the remote-tracking ref is absent, and a bare push still targets the configured
    merge ref then (Codex r1 tests F2)."""
    rc, merge, _ = git(["config", "--get", f"branch.{branch}.merge"], wt)
    if rc != 0 or not merge.strip():
        return None
    rc, remote, _ = git(["config", "--get", f"branch.{branch}.remote"], wt)
    remote = remote.strip() if rc == 0 and remote.strip() else "."
    return f"{remote}/{merge.strip().removeprefix('refs/heads/')}"


def trusted_tools(root: Path) -> Path:
    """tools/ in the main checkout: the parent of the common git dir, the same for every
    worktree. Steps 6 to 8 run from here so a candidate branch never runs its own judge."""
    rc, common, _ = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], root)
    return Path(common.strip()).parent / "tools" if rc == 0 else root / "tools"


def checker_provenance(tools: Path, main: dict) -> dict:
    """Which copies of the audit, PII and suite checkers ran. They are whatever the main
    checkout has checked out, which can lag GitHub main (2026-11-16: local main at f58251d6
    while GitHub main was 23a3251b). Never let a report imply they ran at current main."""
    checkout = tools.parent
    rc, head, _ = git(["rev-parse", "HEAD"], checkout)
    head = head.strip() if rc == 0 else None
    rc, st, _ = git(["--no-optional-locks", "status", "--porcelain", "--", "tools"], checkout)
    dirty = [ln[3:] for ln in st.splitlines() if ln.strip()] if rc == 0 else []
    want = main.get("github_main") or main.get("ref")
    warnings = []
    if head != want:
        warnings.append(f"checkers (audit, PII, suite) ran from {checkout} at {short(head) or '?'}, "
                        f"not GitHub main {short(want) or '?'}: those results come from that "
                        "older or different copy")
    if dirty:
        warnings.append(f"checkers ran with uncommitted changes in {checkout}/tools: "
                        f"{', '.join(dirty)}")
    return {"path": str(tools), "head": head, "dirty": dirty, "current": not warnings,
            "warnings": warnings}


# ---------------------------------------------------------------- GitHub (read-only)
def gh_json(path: str, root: Path, paginate: bool = False):
    cmd = ["gh", "api", *(["--paginate"] if paginate else []), path]
    rc, out, err = run(cmd, cwd=root, timeout=60)
    if rc != 0:
        raise RuntimeError(f"gh api {path} failed rc={rc}: {err.strip()[:300]}")
    # --paginate concatenates JSON arrays as `][`; join them into one.
    text = out.strip()
    if paginate and "][" in text:
        text = text.replace("][", ",")
    return json.loads(text)


def github_state(root: Path, slug: str, gh=gh_json) -> dict:
    """Remote heads plus GitHub main's parents and tree."""
    refs = gh(f"repos/{slug}/git/matching-refs/heads", root, paginate=True)
    heads = {r["ref"].removeprefix("refs/heads/"): r["object"]["sha"] for r in refs}
    if "main" not in heads:
        raise RuntimeError("GitHub has no refs/heads/main")
    c = gh(f"repos/{slug}/commits/{heads['main']}", root)
    return {"heads": heads, "main": heads["main"],
            "parents": [p["sha"] for p in c.get("parents", [])],
            "tree": c["commit"]["tree"]["sha"]}


def has_commit(root: Path, sha: str) -> bool:
    return git(["cat-file", "-e", f"{sha}^{{commit}}"], root)[0] == 0


def resolve_main_ref(root: Path, gh_state: dict | None) -> dict:
    """The commit to merge-check against, and how it was obtained.

    method: "local" (GitHub main is in the object store), "rebuilt" (parents local and
    merge-tree reproduced GitHub's tree exactly), or "fallback" (local origin/main; loud)."""
    rc, local_origin, _ = git(["rev-parse", "origin/main"], root)
    local_origin = local_origin.strip() if rc == 0 else None
    res = {"local_origin_main": local_origin, "github_main": None, "ref": local_origin,
           "method": "fallback", "warnings": []}
    if gh_state is None:
        res["warnings"].append("GitHub unreadable: checking against LOCAL origin/main, "
                               "which may be stale")
        return res
    gm = gh_state["main"]
    res["github_main"] = gm
    if local_origin != gm:
        res["warnings"].append(f"LOCAL origin/main {short(local_origin)} != GitHub main "
                               f"{short(gm)}: local is stale (fetch is Touch-ID gated)")
    if has_commit(root, gm):
        res.update(ref=gm, method="local")
        return res
    parents = gh_state["parents"]
    missing = [p for p in parents if not has_commit(root, p)]
    if len(parents) != 2 or missing:
        why = (f"parents missing locally: {', '.join(map(short, missing))}" if missing
               else f"GitHub main has {len(parents)} parent(s); only a 2-parent merge "
                    "can be rebuilt from local objects")
        res["warnings"].append(f"cannot rebuild GitHub main ({why}); FALLING BACK to local "
                               f"origin/main {short(local_origin)}")
        return res
    rc, out, err = git(["merge-tree", "--write-tree", parents[0], parents[1]], root)
    tree = out.splitlines()[0].strip() if out else ""
    if rc != 0 or tree != gh_state["tree"]:
        res["warnings"].append(
            f"rebuild of GitHub main gave tree {short(tree) or '?'} (rc {rc}), GitHub says "
            f"{short(gh_state['tree'])}; FALLING BACK to local origin/main {short(local_origin)}")
        return res
    rc, out, err = git(["commit-tree", tree, "-p", parents[0], "-p", parents[1],
                        "-m", f"land_branch rebuild of GitHub main {gm}"], root)
    if rc != 0:
        res["warnings"].append(f"commit-tree failed: {err.strip()[:200]}; FALLING BACK")
        return res
    res.update(ref=out.strip(), method="rebuilt", tree=tree)
    return res


def short(sha: str | None) -> str:
    return (sha or "")[:8]


# ---------------------------------------------------------------- merge checks
def merge_check(root: Path, a: str, b: str) -> dict:
    """Clean merge of a and b? Names each conflicting file. Writes only object-store blobs."""
    rc, out, err = git(["merge-tree", "--write-tree", "--name-only", "--no-messages", a, b], root)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # rc 1 means conflict ONLY when a tree sha was printed: git also exits 1 for a ref it
    # cannot resolve ("not something we can merge"), with nothing on stdout.
    if rc not in (0, 1) or not lines or not SHA_RE.fullmatch(lines[0]):
        return {"clean": False, "error": err.strip()[:300] or f"merge-tree rc {rc}", "files": []}
    return {"clean": rc == 0, "tree": lines[0] if lines else None,
            "files": sorted(set(lines[1:])) if rc == 1 else []}


# ---------------------------------------------------------------- per-branch steps
def gate_dry_run(root: Path, wt: Path, branch: str, tip: str, remote_sha: str,
                 remote_url: str) -> dict:
    """The pre-push hook git would run for `git push origin refs/heads/b:refs/heads/b`."""
    rc, hooks, _ = git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], wt)
    hook = Path(hooks.strip()) / "pre-push" if rc == 0 else None
    if hook is None or not hook.is_file():
        return {"rc": None, "ok": False, "hook": str(hook), "error": "no pre-push hook installed"}
    line = f"refs/heads/{branch} {tip} refs/heads/{branch} {remote_sha}\n"
    rc, out, err = run([str(hook), "origin", remote_url], cwd=wt, stdin=line)
    return {"rc": rc, "ok": rc == 0, "hook": str(hook), "tail": tail(out + err),
            "reason": None if rc == 0 else gate_reason(out + err)}


def gate_reason(text: str) -> str:
    """The gate's first BLOCKED line in full: that line says why. The last lines are the
    remedy, which is no use without it. Falls back to the first non-empty line."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blocked = [ln for ln in lines if "BLOCKED" in ln]
    return (blocked or lines or ["(no output)"])[0]


def audit_check(tools: Path, wt: Path, tip: str) -> dict:
    tool = tools / "audit_pii_record.py"
    if not tool.is_file():
        return {"rc": None, "ok": False, "error": f"missing {tool}"}
    rc, out, err = run([sys.executable, str(tool), "--repo-root", str(wt), "check", "--tip", tip],
                       cwd=wt)
    return {"rc": rc, "ok": rc == 0, "tool": str(tool), "tail": tail(out + err)}


def changed_files(wt: Path, base: str, tip: str) -> list[str]:
    rc, mb, _ = git(["merge-base", base, tip], wt)
    if rc != 0:
        return []
    rc, out, _ = git(["diff", "--name-only", "--diff-filter=AM", f"{mb.strip()}..{tip}"], wt)
    return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def pii_scan(tools: Path, wt: Path, files: list[str]) -> dict:
    if not files:
        return {"rc": None, "ok": True, "hits": [], "ambiguous": [], "note": "no A/M files"}
    tool = tools / "check_public_pii.py"
    if not tool.is_file():
        return {"rc": None, "ok": False, "hits": [], "ambiguous": [], "error": f"missing {tool}"}
    paths = "\n".join(str(wt / f) for f in files) + "\n"
    rc, out, err = run([sys.executable, str(tool), "--scan", "--stdin-paths"], cwd=wt, stdin=paths)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"rc": rc, "ok": False, "hits": [], "ambiguous": [],
                "error": "scan output not JSON: " + tail(out + err)}
    hits = [f"{h['file']}: {', '.join(h['tokens'])}" for h in data.get("hits", [])]
    amb = list(data.get("ambiguous_unresolved", []))
    res = {"rc": rc, "hits": hits, "ambiguous": amb, "scanned": data.get("scanned", 0)}
    # Every file skipped as non-public or gitignored: nothing public ships, so nothing to scan.
    only_private = (data.get("scanned", 0) == 0 and not data.get("unreadable")
                    and data.get("skipped"))
    # rc 3 = no denylist hit, only ambiguous-tier matches without a verdict. That is not a
    # scan failure: check_branch settles it against the recorded /audit-pii pass.
    res["ok"] = rc in (0, 3) or (rc == 1 and bool(only_private))
    if only_private:
        res["note"] = "no public A/M files"
    if not res["ok"]:
        res["error"] = data.get("error", f"scan rc {rc}")
    return res


def suite_check(tools: Path, wt: Path, tip: str) -> dict:
    tool = tools / "suite_record.py"
    if tool.is_file():
        rc, out, err = run([sys.executable, str(tool), "check", "--tip", tip], cwd=wt)
        return {"rc": rc, "ok": rc == 0, "tool": str(tool), "tail": tail(out + err)}
    return {"rc": None, "ok": True, "skipped": True,
            "note": "suite_record.py not in the main checkout (suite-gate-1109 not landed); "
                    "suite step skipped"}


def tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def check_branch(root: Path, branch: str, worktrees: dict, gh_state: dict | None,
                 main_ref: str | None, remote_url: str, want_suite: bool,
                 stale_base: str | None = None) -> dict:
    r = {"branch": branch, "ready": False, "refusals": [], "steps": {}}
    if branch == "main":
        r["refusals"].append("refusing to land main itself")
        return r
    wt = worktrees.get(branch)
    if not wt:
        r["refusals"].append("no worktree for this branch")
        return r
    wt = Path(wt)
    r["worktree"] = str(wt)
    rc, tip, _ = git(["rev-parse", f"refs/heads/{branch}"], wt)
    if rc != 0:
        r["refusals"].append("branch does not resolve")
        return r
    tip = tip.strip()
    r["tip"] = tip
    remote_sha = (gh_state or {}).get("heads", {}).get(branch, ZERO)
    r["github_tip"] = None if remote_sha == ZERO else remote_sha
    rc, st, err = git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=no"],
                      wt)
    if rc != 0:
        r["refusals"].append(f"git status failed in worktree: {err.strip()[:200]}")
    elif st.strip():
        dirty = [ln[3:] for ln in st.splitlines() if ln.strip()]
        where = ("tip is already on GitHub at this sha, so the pushed content is unaffected"
                 if r["github_tip"] == tip else "tip is not on GitHub at this sha")
        r["refusals"].append(f"worktree has uncommitted changes: {', '.join(dirty)} "
                             f"(not in tip {short(tip)}; {where})")
    up = configured_upstream(wt, branch)
    r["upstream"] = up
    if up and upstream_is_main(up):
        r["refusals"].append(f"upstream is {up}: a bare `git push` would push main")
    if r["github_tip"] and r["github_tip"] != tip:
        ff = git(["merge-base", "--is-ancestor", remote_sha, tip], wt)[0] == 0
        if ff:
            r["notes"] = [f"GitHub has {branch} at {short(remote_sha)}, behind local "
                          f"{short(tip)}; the push fast-forwards"]
        else:
            r["refusals"].append(
                f"GitHub has {branch} at {short(remote_sha)}, which is not an ancestor of local "
                f"{short(tip)} (or is not in the local object store): the push would be "
                "rejected as non-fast-forward. Never force it; find out which tip is right")
    if stale_base:
        r["refusals"].append(stale_base)
    if main_ref:
        m = merge_check(root, main_ref, tip)
        r["steps"]["merge_main"] = m
        if not m["clean"]:
            r["refusals"].append("conflicts with main: " + (", ".join(m["files"]) or
                                                          m.get("error", "?")))
    else:
        r["refusals"].append("no main ref to check against")
    g = gate_dry_run(root, wt, branch, tip, remote_sha, remote_url)
    r["steps"]["gate"] = g
    if not g["ok"]:
        r["refusals"].append(f"push gate rc {g['rc']}: {g.get('reason') or g.get('error')}")
    tools = trusted_tools(root)
    a = audit_check(tools, wt, tip)
    r["steps"]["audit"] = a
    if not a["ok"]:
        r["refusals"].append(f"audit_pii_record check rc {a['rc']}")
    p = pii_scan(tools, wt, changed_files(wt, main_ref or "origin/main", tip))
    r["steps"]["pii"] = p
    if p["hits"]:
        r["refusals"].append(f"PII denylist hits: {len(p['hits'])}")
    elif not p["ok"]:
        r["refusals"].append(f"PII scan rc {p['rc']}: {p.get('error', '')}")
    elif p["ambiguous"] and not a["ok"]:
        # An ambiguous-tier match is settled by a recorded /audit-pii pass for this tree.
        r["refusals"].append(f"ambiguous PII matches with no clean audit record: "
                             f"{len(p['ambiguous'])}")
    if want_suite:
        s = suite_check(tools, wt, tip)
        r["steps"]["suite"] = s
        if not s["ok"]:
            r["refusals"].append(f"suite_record check rc {s['rc']}")
    if gh_state is None:
        r["refusals"].append("GitHub unreadable: base not confirmed")
    r["ready"] = not r["refusals"]
    return r


# ---------------------------------------------------------------- PR pages
# gh is read-only BY DESIGN: every repo write needs Nick's Touch ID, so `gh pr create`
# always fails. The tool prints a prefilled compare page for Nick to click instead, and
# never suggests widening the token.
PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


def commit_subjects(root: Path, base: str, tip: str) -> list[str]:
    """Subjects of the branch's own commits, newest first."""
    rc, mb, _ = git(["merge-base", base, tip], root)
    if rc != 0:
        return []
    rc, out, _ = git(["log", "--no-merges", "--format=%s", f"{mb.strip()}..{tip}"], root)
    return [s for s in out.splitlines() if s.strip()] if rc == 0 else []


def pr_text(r: dict, main: dict, subjects: list[str], conflicts: list[dict],
            checkers: dict) -> tuple[str, str]:
    """Title and body. States only what this run checked, and with which checker copies."""
    title = subjects[-1] if subjects else r["branch"]      # oldest commit names the feature
    against = f"main {short(main.get('github_main') or main.get('ref'))}"
    at = (f"checkers at {short(checkers['head'])}" if checkers["current"] else
          f"checkers at {short(checkers['head']) or '?'}, NOT current main")
    lines = ["## Summary", *[f"- {s}" for s in subjects], "", "## Verification",
             f"- Merges cleanly onto {against} (land_branch.py, git merge-tree, "
             f"{main['method']}).",
             "- Pre-push gate dry run rc 0; /audit-pii record present for this tip; "
             f"PII denylist scan: no hits ({at})."]
    mine = [c for c in conflicts if r["branch"] in (c["a"], c["b"])]
    if mine:
        lines += ["", "## Merge note"]
        for c in mine:
            other = c["b"] if c["a"] == r["branch"] else c["a"]
            order = "before" if c["a"] == r["branch"] else "after"
            lines.append(f"- Conflicts with {other} in {', '.join(c['files'])}. Merge {order} "
                         f"{other}; resolve on GitHub, not with a local rebase.")
    lines += ["", PR_FOOTER]
    return title, "\n".join(lines)


def compare_url(slug: str, branch: str, title: str, body: str) -> str:
    q = urllib.parse.quote
    return (f"https://github.com/{slug}/compare/main...{q(branch, safe='')}?quick_pull=1"
            f"&title={q(title, safe='')}&body={q(body, safe='')}")


# ---------------------------------------------------------------- the report
def merge_order(results: list[dict], conflicts: list[dict]) -> dict:
    in_pair = {b for c in conflicts for b in (c["a"], c["b"])}
    ready = [r["branch"] for r in results if r["ready"]]
    return {"first": [b for b in ready if b not in in_pair],
            "pairs": [c for c in conflicts if c["a"] in ready or c["b"] in ready]}


def check(root: Path, branches: list[str], want_suite: bool = False, gh=gh_json) -> dict:
    rc, porcelain, err = git(["worktree", "list", "--porcelain"], root)
    worktrees = parse_worktrees(porcelain) if rc == 0 else {}
    # The PUSH url: the hook receives it on a real push, and it can differ from the fetch url.
    rc, url, _ = git(["remote", "get-url", "--push", "origin"], root)
    remote_url = url.strip() if rc == 0 else ""
    slug = repo_slug(remote_url)
    gh_state, gh_error = None, None
    try:
        if not slug:
            raise RuntimeError(f"origin is not a GitHub URL: {remote_url!r}")
        gh_state = github_state(root, slug, gh)
    except (RuntimeError, KeyError, json.JSONDecodeError) as e:
        gh_error = str(e)
    main = resolve_main_ref(root, gh_state)
    if gh_error:
        main["warnings"].insert(0, f"GitHub read failed: {gh_error}")
    stale = None
    if gh_state is not None and main["method"] == "fallback" and \
            main["ref"] != main["github_main"]:
        stale = (f"checked against local origin/main {short(main['ref'])}, not GitHub main "
                 f"{short(main['github_main'])}: a clean result here proves nothing about GitHub")
    results = [check_branch(root, b, worktrees, gh_state, main["ref"], remote_url, want_suite,
                            stale) for b in branches]
    tips = {r["branch"]: r["tip"] for r in results if r.get("tip")}
    conflicts = []
    for a, b in itertools.combinations([r["branch"] for r in results if r.get("tip")], 2):
        m = merge_check(root, tips[a], tips[b])
        if not m["clean"]:
            conflicts.append({"a": a, "b": b, "files": m["files"], "error": m.get("error")})
    checkers = checker_provenance(trusted_tools(root), main)
    landing = []
    for r in results:
        if not r["ready"]:
            continue
        title, body = pr_text(r, main, commit_subjects(root, main["ref"], r["tip"]), conflicts,
                              checkers)
        landing.append({"branch": r["branch"],
                        "push": f"cd {r['worktree']} && git push origin "
                                f"refs/heads/{r['branch']}:refs/heads/{r['branch']}",
                        "compare_url": compare_url(slug, r["branch"], title, body),
                        "pushed": r.get("github_tip") == r["tip"]})
    return {"repo": slug, "main": main, "checkers": checkers, "branches": results,
            "conflicts": conflicts, "merge_order": merge_order(results, conflicts),
            "landing": landing,
            "push_commands": [x["push"] for x in landing],
            "all_ready": bool(results) and all(r["ready"] for r in results)
                         and gh_state is not None}


def post_merge_checks(conflicts: list[dict]) -> list[str]:
    """What a hand-resolved conflict needs before the landing counts as done. On 2026-11-16
    a hand resolution of mutation-allow.json in the GitHub editor broke main."""
    files = sorted({f for c in conflicts for f in c["files"]})
    if not files:
        return []
    out = ["A hand-resolved conflict is not landed until the merged file is checked on "
           "GitHub main:"]
    for f in files:
        how = ("parses as JSON (python3 -m json.tool) and keeps every key from both sides"
               if f.endswith(".json") else "keeps both sides' changes and its tests pass")
        out.append(f"  - {f}: {how}")
    return out


def render(rep: dict) -> str:
    m = rep["main"]
    out = [f"GitHub main: {m['github_main'] or 'UNREADABLE'}",
           f"local origin/main: {m['local_origin_main']}",
           f"checked against: {m['ref']} ({m['method']})"]
    c = rep["checkers"]
    out.append(f"checkers: {c['path']} at {c['head']} "
               f"({'current' if c['current'] else 'NOT current main'})")
    out += [f"!!! {w}" for w in m["warnings"] + c["warnings"]]
    out.append("")
    for r in rep["branches"]:
        verdict = "READY" if r["ready"] else "NOT READY"
        s = r.get("steps", {})
        rcs = " ".join(f"{k}={'ok' if v.get('ok', v.get('clean')) else 'FAIL'}"
                       + (f"(rc {v['rc']})" if v.get("rc") is not None else "")
                       for k, v in s.items())
        out.append(f"{verdict:9}  {r['branch']}  {short(r.get('tip'))}  {rcs}")
        for x in r["refusals"]:
            out.append(f"           - {x}")
        for n in r.get("notes", []):
            out.append(f"           note: {n}")
        p = s.get("pii", {})
        if p.get("hits"):
            out.append(f"           PII hits: {'; '.join(p['hits'])}")
        if p.get("ambiguous"):
            settled = "settled by audit record" if s.get("audit", {}).get("ok") else "UNSETTLED"
            out.append(f"           ambiguous PII ({settled}): {', '.join(p['ambiguous'])}")
        if s.get("suite", {}).get("note"):
            out.append(f"           suite: {s['suite']['note']}")
    out.append("")
    out.append("Pairwise conflicts:" if rep["conflicts"] else "Pairwise conflicts: none")
    for c in rep["conflicts"]:
        out.append(f"  {c['a']} x {c['b']}: {', '.join(c['files']) or c.get('error')}")
    out.append("")
    mo = rep["merge_order"]
    out.append("Merge order (on GitHub, no rebase):")
    for i, b in enumerate(mo["first"], 1):
        out.append(f"  {i}. {b}")
    for c in mo["pairs"]:
        out.append(f"  then {c['a']}, then {c['b']} (conflict in {', '.join(c['files'])}; "
                   "resolve the second after the first lands)")
    out += post_merge_checks(rep["conflicts"])
    out.append("")
    out.append(f"Push commands and PR pages ({len(rep['landing'])}; gh is read-only by design, "
               "so open each page and click Create):")
    for x in rep["landing"]:
        state = "  [already on GitHub at this tip]" if x["pushed"] else ""
        out += [f"  {x['push']}{state}", f"    {x['compare_url']}"]
    out.append("")
    out.append("ALL READY" if rep["all_ready"] else "NOT ALL READY")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check branches for landing (read-only).")
    ap.add_argument("branches", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--suite", action="store_true",
                    help="also run suite_record.py check when that tool exists")
    ap.add_argument("--repo-root", default=".")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    if not args.branches:
        print("usage: land_branch.py <branch> [<branch> ...] [--json] [--suite]", file=sys.stderr)
        return 2
    rc, top, _ = git(["rev-parse", "--show-toplevel"], args.repo_root)
    if rc != 0:
        print(f"not a git repository: {args.repo_root}", file=sys.stderr)
        return 2
    rep = check(Path(top.strip()), list(dict.fromkeys(args.branches)), args.suite)
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 0 if rep["all_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
