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
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_report import format_line  # noqa: E402  (a count states its reach)
import merge_chain as mc  # noqa: E402  (one rule for a valid merge, shared with both gates)

ZERO = "0" * 40
# Env vars that turn a gate dry run into a recorded waiver. A read-only check must never
# carry them through, or running it from a shell that has one set would write a ledger row.
WAIVER_ENV = ("CODEX_VERIFY_WAIVE", "AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SUITE_WAIT_S = 3600  # how long a landing suite queues behind a running builder suite
MAIN_NAMES = {"main", "origin/main", "refs/heads/main", "refs/remotes/origin/main"}


# ---------------------------------------------------------------- process helpers
def run(cmd: list[str], cwd: str | Path | None = None, stdin: str | None = None,
        timeout: int = 600) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if k not in WAIVER_ENV}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONDONTWRITEBYTECODE"] = "1"     # a cold run must not create __pycache__ (Codex r2)
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


NO_TRUSTED = "no trusted checkers: the main checkout's common git dir could not be resolved"


def trusted_tools(root: Path) -> Path | None:
    """tools/ in the main checkout: the parent of the common git dir, the same for every
    worktree. Steps 6 to 8 run from here so a candidate branch never runs its own judge.
    None when that cannot be resolved: never fall back to the candidate's own tools/
    (Codex r2 F2)."""
    rc, common, _ = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], root)
    return Path(common.strip()).parent / "tools" if rc == 0 and common.strip() else None


def checker_provenance(tools: Path | None, main: dict) -> dict:
    """Which copies of the audit, PII and suite checkers ran. They are whatever the main
    checkout has checked out, which can lag GitHub main (2026-11-16: local main at f58251d6
    while GitHub main was 23a3251b). Never let a report imply they ran at current main:
    "current" needs GitHub main known, HEAD equal to it, and a clean, readable tools/."""
    if tools is None:
        return {"path": None, "head": None, "dirty": [], "current": False,
                "warnings": [NO_TRUSTED]}
    checkout = tools.parent
    rc, head, _ = git(["rev-parse", "HEAD"], checkout)
    head = head.strip() if rc == 0 else None
    want = main.get("github_main")
    warnings = []
    if want is None:
        warnings.append("GitHub main unknown, so the checkers' commit could not be compared "
                        "with it: do not read their results as current")
    elif head != want:
        warnings.append(f"checkers (audit, PII, suite) ran from {checkout} at {short(head) or '?'}, "
                        f"not GitHub main {short(want)}: those results come from that "
                        "older or different copy")
    rc, st, err = git(["--no-optional-locks", "status", "--porcelain", "--", "tools"], checkout)
    dirty = [ln[3:] for ln in st.splitlines() if ln.strip()] if rc == 0 else []
    if rc != 0:
        warnings.append(f"could not inspect {checkout}/tools for uncommitted changes "
                        f"(git status rc {rc}): {err.strip()[:200]}")
    elif dirty:
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


def audit_check(tools: Path | None, wt: Path, tip: str) -> dict:
    if tools is None:
        return {"rc": None, "ok": False, "error": NO_TRUSTED}
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


def pii_scan(tools: Path | None, wt: Path, files: list[str]) -> dict:
    if not files:
        return {"rc": None, "ok": True, "hits": [], "ambiguous": [], "note": "no A/M files"}
    if tools is None:
        return {"rc": None, "ok": False, "hits": [], "ambiguous": [], "error": NO_TRUSTED}
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


def suite_check(tools: Path | None, wt: Path, tip: str) -> dict:
    if tools is None:
        return {"rc": None, "ok": False, "error": NO_TRUSTED}
    tool = tools / "suite_record.py"
    if tool.is_file():
        rc, out, err = run([sys.executable, str(tool), "check", "--tip", tip], cwd=wt)
        return {"rc": rc, "ok": rc == 0, "tool": str(tool), "tail": tail(out + err)}
    return {"rc": None, "ok": True,
            "note": "suite_record.py not in the main checkout (suite-gate-1109 not landed); "
                    "suite step skipped"}


def tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def check_branch(root: Path, branch: str, worktrees: dict, gh_state: dict | None,
                 main_ref: str | None, remote_url: str, want_suite: bool,
                 stale_base: str | None = None, allowlist_ok: bool = False) -> dict:
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
        r["refusals"].append(f"upstream is {up}: this branch is configured to push to main, "
                             "so a bare `git push` from its worktree targets main")
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
        resolved = None
        if not m["clean"] and allowlist_ok and m["files"] == [mc.ALLOWLIST]:
            try:
                resolved = mc.expected_tree(root, main_ref, tip)[0]
            except mc.GitError as e:     # unknown is not resolvable (grok 10.F3)
                m = {**m, "files": [], "error": str(e)}
        r["steps"]["merge_main"] = m
        if resolved:
            # Nick, 2026-11-18: the allowlist is resolved by key (allowlist_merge), and
            # the push gate recomputes that resolution. Only --merge-push passes this.
            r.setdefault("notes", []).append(
                f"conflicts with main only in {mc.ALLOWLIST}; --merge-push resolves it by key")
        elif not m["clean"]:
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


def check(root: Path, branches: list[str], want_suite: bool = False, gh=gh_json, *,
          allowlist_ok: bool = False) -> dict:
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
                            stale, allowlist_ok) for b in branches]
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
    return {"repo": slug, "remote_url": remote_url, "main": main, "checkers": checkers,
            "branches": results,
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
    out.append(f"checkers: {c['path'] or 'UNRESOLVED'} at {c['head'] or '?'} "
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
    ready = sum(1 for r in rep["branches"] if r["ready"])
    out.append(format_line("branches named on the command line", len(rep["branches"]), ready,
                           {"not ready": len(rep["branches"]) - ready}))
    out.append("Push commands and PR pages, one per ready branch (gh is read-only by design, "
               "so open each page and click Create):")
    for x in rep["landing"]:
        state = "  [already on GitHub at this tip]" if x["pushed"] else ""
        out += [f"  {x['push']}{state}", f"    {x['compare_url']}"]
    out.append("")
    out.append("ALL READY" if rep["all_ready"] else "NOT ALL READY")
    return "\n".join(out)


# ================================================================ --merge-push
# Nick, 2026-11-18: "It should not be something that I am doing." One command builds every
# ready branch into a chain of merge commits on GitHub main and prints ONE push. Rulings
# (data/workstreams/orchestration.md, 2026-11-18 15:42): no PRs, each merge message is the
# branch's reviewed description; a mutation-allow.json conflict is resolved by key, any other
# conflict holds that branch; SSH fetch only; the full suite on the final tree, 0 failures.
DESC_DIR = Path("output") / "landing-descriptions"
TITLE_MAX = 72
EM_DASH = "—"
# The gitignored files the push gate reads. The preview copies exactly these from the main
# checkout, so it judges with the same evidence the real push will.
GATE_DATA = (".cross-model-ledger.jsonl", ".audit-pii-ledger.jsonl", ".pii-denylist.txt",
             ".pii-denylist-targetwords.txt", ".pii-denylist-ambiguous.txt")
CROSS_LEDGER, AUDIT_LEDGER = GATE_DATA[0], GATE_DATA[1]
_BUSY_RE = re.compile(r"\bssh\b.*git@github\.com|(?:^|[\s/])git(?:\s+-C\s+\S+)?\s+"
                      r"(?:push|fetch|pull)\b")
WEDGE_HELP = ('diagnose: ps -ax -o pid,etime,command | grep -E "ssh git@github.com|SecretAgent"; '
              "a wedged agent is fixed by Nick: kill the waiting push pids, then "
              "pkill -f SecretAgent; sleep 2; open -a Secretive")


def busy_processes(ps_output: str | None = None) -> list[str]:
    """Running pushes, fetches or GitHub ssh sessions. A second push while one waits on a
    Touch ID prompt is how the Secretive agent wedged on 2026-11-17 (F3)."""
    if ps_output is None:
        rc, ps_output, _ = run(["ps", "-ax", "-o", "pid,etime,command"], timeout=30)
        if rc != 0:
            return [f"? ps failed rc {rc}: cannot tell whether a push is running"]
    out = []
    for line in ps_output.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] != str(os.getpid()) and _BUSY_RE.search(parts[2]):
            out.append(line.strip())
    return out


def description_path(main: Path, branch: str) -> Path:
    return main / DESC_DIR / f"{branch}.md"


def check_description(main: Path, branch: str) -> tuple[str | None, list[str]]:
    """The branch owner's reviewed description, or the problems that hold the branch.

    Format: a title line (1-72 chars, no surrounding whitespace), a blank line, a body, and
    PR_FOOTER as the last line. No em dash; no denylist or target-word hit (F5: thin titles
    from commit subjects, leading-space and typo'd titles, >72-char titles cut by GitHub)."""
    path = description_path(main, branch)
    if not path.is_file():
        return None, [f"no reviewed description at {path}"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = lines[0] if lines else ""
    probs = []
    if not title.strip():
        probs.append("description has no title line")
    elif title != title.strip():
        probs.append("title has leading or trailing whitespace")
    if len(title) > TITLE_MAX:
        probs.append(f"title is {len(title)} chars, longer than {TITLE_MAX}")
    if len(lines) < 2 or lines[1].strip():
        probs.append("line 2 of the description must be blank")
    nonblank = [ln.strip() for ln in lines if ln.strip()]
    if not nonblank or nonblank[-1] != PR_FOOTER:
        probs.append(f"last line must be the footer: {PR_FOOTER}")
    if not [ln for ln in lines[2:] if ln.strip() and ln.strip() != PR_FOOTER]:
        probs.append("description has no body")
    if EM_DASH in text:
        probs.append("description contains an em dash")
    import check_public_pii as cp
    tokens = cp.load_denylist(main)
    if not tokens:
        probs.append("no denylist to check the description against (tools/.pii-denylist.txt)")
    else:
        hits = cp.find_pii(text, tokens) + cp.find_target_words(text, cp.load_target_words(main))
        if hits:
            probs.append(f"description has denylist hits: {', '.join(sorted(set(hits)))}")
    return (None if probs else text), probs


def merge_message(desc: str, branch: str, tip: str) -> str:
    """The reviewed description, with a Merge-push trailer before its footer line."""
    lines = desc.rstrip("\n").splitlines()
    last = max(i for i, ln in enumerate(lines) if ln.strip())
    head = "\n".join(lines[:last]).rstrip()
    return f"{head}\n\nMerge-push: {branch} {tip}\n\n{lines[last].strip()}\n"


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _audit_key(row: dict) -> tuple:
    return (row.get("kind"), row.get("verdict"), row.get("tree"), row.get("tip"),
            row.get("recorded"))


def collect_backfill(main: Path, built: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rows the built branches' worktrees hold that the main checkout does not: review
    rows by codex_verify.row_identity (the key mirror_to_main dedups on; rows before #49
    were never mirrored) and clean /audit-pii records by content. Each copy names its
    source in `copied_from`. Waivers are not copied: a waiver clears only its own push."""
    from codex_verify import row_identity
    have = {row_identity(r) for r in _jsonl(main / "tools" / CROSS_LEDGER)}
    have_audit = {_audit_key(r) for r in _jsonl(main / "tools" / AUDIT_LEDGER)}
    cross, audit = [], []
    for b in built:
        wt = Path(b["worktree"])
        for row in _jsonl(wt / "tools" / CROSS_LEDGER):
            if row_identity(row) not in have:
                have.add(row_identity(row))
                cross.append({**row, "copied_from": str(wt)})
        for row in _jsonl(wt / "tools" / AUDIT_LEDGER):
            if row.get("kind") == "audit" and row.get("verdict") == "clean" \
                    and _audit_key(row) not in have_audit:
                have_audit.add(_audit_key(row))
                audit.append({**row, "copied_from": str(wt)})
    return cross, audit


def write_backfill(main: Path, cross: list[dict], audit: list[dict]) -> None:
    import cross_model_gate as gate
    for row in cross:
        gate.append_row(main, row)          # takes the ledger lock finding_write uses
    if audit:
        path = main / "tools" / AUDIT_LEDGER
        with path.open("a", encoding="utf-8") as fh:
            for row in audit:
                fh.write(json.dumps(row) + "\n")


def preview_gate(root: Path, main: Path, checkers: Path, base: str, tip: str, url: str,
                 extra_cross: list[dict], extra_audit: list[dict]) -> dict:
    """Run the pre-push hook exactly as `git push origin <tip>:refs/heads/main` would, in a
    throwaway no-checkout worktree whose tools/ is `checkers` plus the main checkout's gate
    data (GATE_DATA) plus rows not yet written there. Nothing in the main checkout changes."""
    tmp = Path(tempfile.mkdtemp(prefix="merge-push-preview."))
    wt = tmp / "wt"
    try:
        rc, _, err = git(["worktree", "add", "--no-checkout", "--detach", str(wt), base], root)
        if rc != 0:
            return {"rc": None, "ok": False, "reason": f"preview worktree: {err.strip()[:200]}"}
        shutil.copytree(checkers, wt / "tools", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".*", "__pycache__", "logs", "*.log"))
        for name in GATE_DATA:
            src = main / "tools" / name
            if src.is_file():
                shutil.copy2(src, wt / "tools" / name)
        for name, rows in ((CROSS_LEDGER, extra_cross), (AUDIT_LEDGER, extra_audit)):
            # Zero rows leaves at most an empty ledger, which the gate reads as no rows.
            with (wt / "tools" / name).open("a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
        hook = wt / "tools" / "hooks" / "pre-push"
        if not hook.is_file():
            return {"rc": None, "ok": False, "reason": f"no hook at {checkers}/hooks/pre-push"}
        rc, out, err = run(["bash", str(hook), "origin", url], cwd=wt,
                           stdin=f"{tip} {tip} refs/heads/main {base}\n")
        return {"rc": rc, "ok": rc == 0, "tail": tail(out + err, 12),
                "reason": None if rc == 0 else gate_reason(out + err)}
    finally:
        git(["worktree", "remove", "--force", str(wt)], root)
        shutil.rmtree(tmp, ignore_errors=True)


def hook_installed_matches(main: Path) -> tuple[bool, str]:
    rc, hooks, _ = git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], main)
    installed = Path(hooks.strip()) / "pre-push" if rc == 0 else None
    tracked = main / "tools" / "hooks" / "pre-push"
    if installed is None or not installed.is_file() or not tracked.is_file() \
            or installed.read_bytes() != tracked.read_bytes():
        return False, (f"the installed pre-push hook ({installed}) is not tools/hooks/pre-push "
                       f"of {main}: the push would run a different gate. Run: "
                       f"! bash {main}/tools/hooks/install.sh")
    return True, ""


def held_inside(root: Path, held: list[dict], base: str, tip: str) -> list[str]:
    """The held branches whose commits `tip` would carry into the push.

    A held branch is named by its ref, not by its worktree (grok F1, 2026-11-19): a branch
    held for "no worktree" has no tip in its check row, and skipping it let a ready branch
    stacked on it land its commits. A held tip already in `base` adds nothing, so it is not
    counted. A git error is not "not an ancestor": the branch is reported as if inside."""
    out = []
    for h in held:
        ht = h.get("tip")
        if not ht:
            rc, sha, _ = git(["rev-parse", "--verify", "--quiet",
                              f"refs/heads/{h['branch']}^{{commit}}"], root)
            ht = sha.strip() if rc == 0 else ""
        if not ht or git(["merge-base", "--is-ancestor", ht, base], root)[0] == 0:
            continue
        rc = git(["merge-base", "--is-ancestor", ht, tip], root)[0]
        if rc == 0:
            out.append(h["branch"])
        elif rc != 1:
            out.append(f"{h['branch']} (ancestry unreadable, git rc {rc})")
    return out


def merge_push(root: Path, branches: list[str], *, dry_run: bool, gh=None,
               checkers: Path | None = None, busy=None, want_suite: bool | None = None) -> dict:
    """Build the merge chain and decide whether ONE push may land it. See the block above.

    A real run writes: the copied ledger rows into the main checkout, and the suite record.
    A dry run writes neither (objects in the store aside) and judges with the tools/ next
    to this file, so a new gate can be tried before it lands; a real run judges only with
    the main checkout's tools/ and only when they are current GitHub main."""
    gh = gh or gh_json
    busy = busy or busy_processes
    want_suite = (not dry_run) if want_suite is None else want_suite
    branches = list(dict.fromkeys(branches))
    rep = {"dry_run": dry_run, "ok": False, "refusals": [], "held": [], "built": [],
           "base": None, "chain_tip": None, "gate": None, "suite": None,
           "backfill": {"cross": 0, "audit": 0, "written": False}, "commands": [],
           "fetch_command": None, "verify_resolved": []}
    tools = trusted_tools(root)
    if tools is None:
        rep["refusals"].append(NO_TRUSTED)
        return rep
    main = tools.parent
    rep["main_checkout"] = str(main)
    # check() runs the INSTALLED pre-push hook per branch, so it must be the tracked one
    # before anything executes it, dry run included (codex F3, 2026-11-19).
    same, why = hook_installed_matches(main)
    if not same:
        rep["refusals"].append(why)
        return rep
    running = busy()
    if running:
        rep["refusals"].append("another push or fetch is running (one at a time, F3): "
                               + "; ".join(running) + ". " + WEDGE_HELP)
    base_rep = check(root, branches, want_suite=False, gh=gh, allowlist_ok=True)
    rep["check"] = base_rep
    m = base_rep["main"]
    base = rep["base"] = m["github_main"]
    if base is None:
        rep["refusals"].append("GitHub main unreadable: " + "; ".join(m["warnings"]))
    elif m["method"] != "local":
        rep["fetch_command"] = f"! git -C {main} fetch origin main"
        rep["refusals"].append(f"GitHub main {short(base)} is not in the local object store; "
                               "the chain must start on its exact sha. Fetch it (SSH, one "
                               "Touch ID), then re-run")
    if not dry_run:
        if not base_rep["checkers"]["current"]:
            rep["refusals"].append("checkers are not current GitHub main: "
                                   + "; ".join(base_rep["checkers"]["warnings"]))
    if rep["refusals"]:
        return rep
    gate_tools = checkers or (tools if not dry_run else Path(__file__).resolve().parent)
    rep["gate_tools"] = str(gate_tools)

    results = {r["branch"]: r for r in base_rep["branches"]}
    candidates = {}
    for b in branches:
        r = results[b]
        reasons = list(r["refusals"])
        desc, probs = check_description(main, b)
        if reasons or probs:
            rep["held"].append({"branch": b, "tip": r.get("tip"), "reasons": reasons + probs})
        else:
            candidates[b] = (r["tip"], desc, r["worktree"])
    first = [b for b in base_rep["merge_order"]["first"] if b in candidates]
    order = first + [b for b in candidates if b not in first]
    cur = base
    for b in order:
        tip, desc, wt = candidates[b]
        # A held branch must not land inside another one (codex F1, 2026-11-19): a ready
        # branch stacked on a held tip would carry the held commits into the push.
        inside = held_inside(root, rep["held"], base, tip)
        if inside:
            rep["held"].append({"branch": b, "tip": tip, "reasons": [
                "contains held branch(es) " + ", ".join(inside) + ": landing it would land them"]})
            continue
        try:
            cur = mc.build(root, cur, tip, merge_message(desc, b, tip))
        except (mc.ChainError, mc.GitError) as e:
            rep["held"].append({"branch": b, "tip": tip, "reasons": [str(e)]})
            continue
        rep["built"].append({"branch": b, "tip": tip, "merge": cur, "worktree": wt})
    if not rep["built"]:
        rep["refusals"].append("nothing to land: every branch is held")
        return rep
    try:
        chain = mc.walk(root, base, cur)
    except (mc.ChainError, mc.GitError) as e:
        rep["refusals"].append(f"the built chain does not verify: {e}")
        return rep
    for b, c in zip(rep["built"], chain):
        b["resolved_allowlist"] = c.resolved_allowlist
    rep["chain_tip"] = cur
    if any(c.resolved_allowlist for c in chain):
        rep["verify_resolved"] = [mc.ALLOWLIST]

    cross, audit = collect_backfill(main, rep["built"])
    rep["backfill"] = {"cross": len(cross), "audit": len(audit), "written": False}
    if not dry_run:
        write_backfill(main, cross, audit)
        rep["backfill"]["written"] = True
        cross, audit = [], []
    g = preview_gate(root, main, gate_tools, base, cur, base_rep["remote_url"], cross, audit)
    rep["gate"] = g
    if not g["ok"]:
        rep["refusals"].append(f"push gate rc {g['rc']}: {g.get('reason')}")
        return rep
    if want_suite:
        tool = gate_tools / "suite_record.py"
        # --wait: queue behind a builder's suite (suite_record's lock) instead of failing.
        rc, out, err = run([sys.executable, str(tool), "run", "--branch", cur, "--base", base,
                            "--wait", str(SUITE_WAIT_S), "--repo-root", str(main)], cwd=main,
                           timeout=SUITE_WAIT_S + 4 * 3600)
        rep["suite"] = {"rc": rc, "ok": rc == 0, "tail": tail(out + err, 40)}
        if rc != 0:
            rep["refusals"].append(f"full suite on the merged tree did not pass (rc {rc}); "
                                   "failing output is in suite.tail")
            return rep
    rep["commands"] = [
        f"! git -C {main} push origin {cur}:refs/heads/main",
        f"! git -C {main} merge --ff-only {cur}",
        f"! PYTHONIOENCODING=utf-8 python3 {main}/tools/land_branch.py --verify-landed "
        f"{base}..{cur}",
    ]
    rep["ok"] = True
    return rep


def render_merge_push(rep: dict) -> str:
    out = [("DRY RUN (judged with " + rep.get("gate_tools", "?") + "; nothing written)")
           if rep["dry_run"] else "MERGE-PUSH"]
    if rep.get("base"):
        out.append(f"base: GitHub main {rep['base']}")
    for b in rep["built"]:
        extra = "  (allowlist resolved by key)" if b.get("resolved_allowlist") else ""
        out.append(f"BUILT  {b['branch']}  {short(b['tip'])} -> merge {short(b['merge'])}{extra}")
    for h in rep["held"]:
        out.append(f"HELD   {h['branch']}")
        out += [f"         - {x}" for x in h["reasons"]]
    bf = rep["backfill"]
    out.append(f"evidence copied to the main checkout: {bf['cross']} review row(s), "
               f"{bf['audit']} audit row(s) ({'written' if bf['written'] else 'not written'})")
    if rep.get("gate"):
        g = rep["gate"]
        out.append(f"push gate preview: {'ok' if g['ok'] else 'BLOCKED'} (rc {g['rc']})")
        if not g["ok"] and g.get("tail"):
            out += ["  " + ln for ln in g["tail"].splitlines()]
    if rep.get("suite"):
        s = rep["suite"]
        out.append(f"full suite on {short(rep['chain_tip'])}: {'pass' if s['ok'] else 'FAIL'} "
                   f"(rc {s['rc']})")
        if not s["ok"]:
            out += ["  " + ln for ln in s["tail"].splitlines()]
    elif rep["chain_tip"]:
        out.append("full suite: not run (dry run; pass --suite to run it)")
    out += [f"!!! {r}" for r in rep["refusals"]]
    if rep.get("fetch_command"):
        out += ["", "Run this first, then re-run:", f"  {rep['fetch_command']}"]
    if rep["ok"]:
        out += ["", "One push lands every BUILT branch. Run from the main checkout, in order:"]
        out += [f"  {c}" for c in rep["commands"]]
    return "\n".join(out)


def verify_landed(root: Path, rng: str, gh=None) -> dict:
    """After the push: every merged tip and the chain tip are on GitHub main (compare
    behind_by 0, repo named explicitly, F14); a key-resolved allowlist parses on GitHub
    main and holds every key and reason of the merged one; then a landing record is
    appended to data/workstreams/orchestration.md in the main checkout. Read-only on GitHub."""
    gh = gh or gh_json
    base, sep, tip = rng.partition("..")
    res = {"ok": False, "problems": [], "branches": []}
    if not sep or not base or not tip:
        res["problems"].append(f"expected BASE..TIP, got {rng!r}")
        return res
    try:
        chain = mc.walk(root, base, tip)
    except (mc.ChainError, mc.GitError) as e:
        res["problems"].append(f"not a merge-push chain: {e}")
        return res
    rc, url, _ = git(["remote", "get-url", "--push", "origin"], root)
    slug = repo_slug(url) if rc == 0 else None
    if not slug:
        res["problems"].append("origin is not a GitHub URL")
        return res
    for sha in [c.p2 for c in chain] + [tip]:
        try:
            behind = gh(f"repos/{slug}/compare/{sha}...main", root).get("behind_by")
        except (RuntimeError, json.JSONDecodeError, AttributeError) as e:
            res["problems"].append(f"{short(sha)}: compare failed: {e}")
            continue
        if behind != 0:
            res["problems"].append(f"{short(sha)} is not on GitHub main (behind_by {behind})")
    if any(c.resolved_allowlist for c in chain):
        import base64
        try:
            blob = gh(f"repos/{slug}/contents/{mc.ALLOWLIST}?ref=main", root)
            remote = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
            local = json.loads(git(["show", f"{tip}:{mc.ALLOWLIST}"], root)[1])
            lost = [k for k, v in local.items() if remote.get(k) != v]
            if lost:
                res["problems"].append(f"{mc.ALLOWLIST} on GitHub main lacks {len(lost)} "
                                       f"merged key(s): {', '.join(lost[:5])}")
        except (RuntimeError, KeyError, ValueError, AttributeError) as e:
            res["problems"].append(f"{mc.ALLOWLIST} on GitHub main did not parse: {e}")
    for c in chain:
        msg = git(["log", "-1", "--format=%B", c.sha], root)[1]
        name = next((ln.split()[1] for ln in msg.splitlines()
                     if ln.startswith("Merge-push: ") and len(ln.split()) >= 3), "?")
        res["branches"].append({"branch": name, "tip": c.p2, "merge": c.sha,
                                "resolved_allowlist": c.resolved_allowlist})
    if res["problems"]:
        return res
    tools = trusted_tools(root)
    record = tools.parent / "data" / "workstreams" / "orchestration.md" if tools else None
    lines = [f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} by `date` | merge-push landed "
             f"{len(chain)} branch(es) (land_branch.py --verify-landed)\n",
             f"- GitHub main contains {tip} (pushed onto {base}); every merged tip compares "
             "behind_by 0 against GitHub main."]
    lines += [f"- {b['branch']} at {b['tip']} via merge {b['merge']}"
              + (" (allowlist resolved by key; parsed on GitHub main)"
                 if b["resolved_allowlist"] else "") for b in res["branches"]]
    if record is None or not record.is_file():
        res["problems"].append(f"landing record not written: {record} not found")
        return res
    with record.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    res["record"] = str(record)
    res["ok"] = True
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check branches for landing; --merge-push builds "
                                             "the merge chain and prints one push.")
    ap.add_argument("branches", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--suite", action="store_true",
                    help="also run the suite (check mode: suite_record check; dry-run "
                         "merge-push: the full suite on the merged tree)")
    ap.add_argument("--merge-push", action="store_true",
                    help="build every ready branch into one merge chain on GitHub main")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --merge-push: judge with this file's tools/, write nothing")
    ap.add_argument("--verify-landed", metavar="BASE..TIP", default=None)
    ap.add_argument("--repo-root", default=".")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    if args.dry_run and not args.merge_push:
        print("--dry-run needs --merge-push", file=sys.stderr)
        return 2
    rc, top, _ = git(["rev-parse", "--show-toplevel"], args.repo_root)
    if rc != 0:
        print(f"not a git repository: {args.repo_root}", file=sys.stderr)
        return 2
    root = Path(top.strip())
    if args.verify_landed:
        res = verify_landed(root, args.verify_landed)
        print(json.dumps(res, indent=2) if args.json else
              ("LANDED; record appended to " + res["record"]) if res["ok"] else
              "NOT LANDED:\n" + "\n".join(f"  - {p}" for p in res["problems"]))
        return 0 if res["ok"] else 1
    if not args.branches:
        print("usage: land_branch.py <branch> [<branch> ...] [--json] [--suite] "
              "[--merge-push [--dry-run]] | --verify-landed BASE..TIP", file=sys.stderr)
        return 2
    if args.merge_push:
        rep = merge_push(root, args.branches, dry_run=args.dry_run,
                         want_suite=True if (args.suite or not args.dry_run) else False)
        print(json.dumps(rep, indent=2) if args.json else render_merge_push(rep))
        return 0 if rep["ok"] else 1
    rep = check(root, list(dict.fromkeys(args.branches)), args.suite)
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 0 if rep["all_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
