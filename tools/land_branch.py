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
     called from the branch's worktree with the refs line git would hand it.
  6. `tools/audit_pii_record.py check --tip <sha>` from the worktree.
  7. `tools/check_public_pii.py --scan --stdin-paths` over the branch's added/modified files.
  8. Optional (--suite): `tools/suite_record.py check --tip <sha>` when that tool exists;
     skipped with a notice when it does not.

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
        elif not line.strip():
            path = None
    return out


def repo_slug(url: str) -> str | None:
    """owner/repo from a GitHub remote URL (ssh or https)."""
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def upstream_is_main(upstream: str) -> bool:
    return upstream.strip() in MAIN_NAMES or upstream.strip().endswith("/main")


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
    if rc not in (0, 1):
        return {"clean": False, "error": err.strip()[:300] or f"merge-tree rc {rc}", "files": []}
    lines = [ln for ln in out.splitlines() if ln.strip()]
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
    return {"rc": rc, "ok": rc == 0, "hook": str(hook), "tail": tail(out + err)}


def audit_check(wt: Path, tip: str) -> dict:
    tool = wt / "tools" / "audit_pii_record.py"
    if not tool.is_file():
        return {"rc": None, "ok": False, "error": f"missing {tool}"}
    rc, out, err = run([sys.executable, str(tool), "--repo-root", str(wt), "check", "--tip", tip],
                       cwd=wt)
    return {"rc": rc, "ok": rc == 0, "tail": tail(out + err)}


def changed_files(wt: Path, base: str, tip: str) -> list[str]:
    rc, mb, _ = git(["merge-base", base, tip], wt)
    if rc != 0:
        return []
    rc, out, _ = git(["diff", "--name-only", "--diff-filter=AM", f"{mb.strip()}..{tip}"], wt)
    return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def pii_scan(wt: Path, files: list[str]) -> dict:
    if not files:
        return {"rc": None, "ok": True, "hits": [], "ambiguous": [], "note": "no A/M files"}
    tool = wt / "tools" / "check_public_pii.py"
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


def suite_check(main_root: Path, wt: Path, tip: str) -> dict:
    for cand in (wt / "tools" / "suite_record.py", main_root / "tools" / "suite_record.py"):
        if cand.is_file():
            rc, out, err = run([sys.executable, str(cand), "check", "--tip", tip], cwd=wt)
            return {"rc": rc, "ok": rc == 0, "tool": str(cand), "tail": tail(out + err)}
    return {"rc": None, "ok": True, "skipped": True,
            "note": "suite_record.py not found (suite-gate-1109 not landed); suite step skipped"}


def tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def check_branch(root: Path, branch: str, worktrees: dict, gh_state: dict | None,
                 main_ref: str | None, remote_url: str, want_suite: bool) -> dict:
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
    rc, st, err = git(["status", "--porcelain", "--untracked-files=no"], wt)
    if rc != 0:
        r["refusals"].append(f"git status failed in worktree: {err.strip()[:200]}")
    elif st.strip():
        dirty = [ln[3:] for ln in st.splitlines() if ln.strip()]
        where = ("tip is already on GitHub at this sha, so the pushed content is unaffected"
                 if r["github_tip"] == tip else "tip is not on GitHub at this sha")
        r["refusals"].append(f"worktree has uncommitted changes: {', '.join(dirty)} "
                             f"(not in tip {short(tip)}; {where})")
    rc, up, _ = git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], wt)
    r["upstream"] = up.strip() if rc == 0 else None
    if rc == 0 and upstream_is_main(up):
        r["refusals"].append(f"upstream is {up.strip()}: a bare `git push` would push main")
    if r["github_tip"] and r["github_tip"] != tip:
        r["notes"] = [f"GitHub has {branch} at {short(remote_sha)}, local is {short(tip)}"]
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
        r["refusals"].append(f"push gate rc {g['rc']}")
    a = audit_check(wt, tip)
    r["steps"]["audit"] = a
    if not a["ok"]:
        r["refusals"].append(f"audit_pii_record check rc {a['rc']}")
    p = pii_scan(wt, changed_files(wt, main_ref or "origin/main", tip))
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
        s = suite_check(root, wt, tip)
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


def pr_text(r: dict, main: dict, subjects: list[str], conflicts: list[dict]) -> tuple[str, str]:
    """Title and body. States only what this run checked."""
    title = subjects[-1] if subjects else r["branch"]      # oldest commit names the feature
    against = f"main {short(main.get('github_main') or main.get('ref'))}"
    lines = ["## Summary", *[f"- {s}" for s in subjects], "", "## Verification",
             f"- Merges cleanly onto {against} (land_branch.py, git merge-tree, "
             f"{main['method']}).",
             "- Pre-push gate dry run rc 0; /audit-pii record present for this tip; "
             "PII denylist scan: no hits."]
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
    rc, url, _ = git(["remote", "get-url", "origin"], root)
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
    results = [check_branch(root, b, worktrees, gh_state, main["ref"], remote_url, want_suite)
               for b in branches]
    tips = {r["branch"]: r["tip"] for r in results if r.get("tip")}
    conflicts = []
    for a, b in itertools.combinations([r["branch"] for r in results if r.get("tip")], 2):
        m = merge_check(root, tips[a], tips[b])
        if not m["clean"]:
            conflicts.append({"a": a, "b": b, "files": m["files"], "error": m.get("error")})
    landing = []
    for r in results:
        if not r["ready"]:
            continue
        title, body = pr_text(r, main, commit_subjects(root, main["ref"], r["tip"]), conflicts)
        landing.append({"branch": r["branch"],
                        "push": f"cd {r['worktree']} && git push origin "
                                f"refs/heads/{r['branch']}:refs/heads/{r['branch']}",
                        "compare_url": compare_url(slug, r["branch"], title, body),
                        "pushed": r.get("github_tip") == r["tip"]})
    return {"repo": slug, "main": main, "branches": results, "conflicts": conflicts,
            "merge_order": merge_order(results, conflicts),
            "landing": landing,
            "push_commands": [x["push"] for x in landing],
            "all_ready": bool(results) and all(r["ready"] for r in results)
                         and gh_state is not None}


def render(rep: dict) -> str:
    m = rep["main"]
    out = [f"GitHub main: {m['github_main'] or 'UNREADABLE'}",
           f"local origin/main: {m['local_origin_main']}",
           f"checked against: {m['ref']} ({m['method']})"]
    out += [f"!!! {w}" for w in m["warnings"]]
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
