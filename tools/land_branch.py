#!/usr/bin/env python3
"""land_branch.py: check branches for landing. Read-only. Never rebases, never fetches.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/land_branch.py <branch> [<branch> ...] [--json] [--suite]

Where to run it: the check mode works from any checkout of the repo, a branch's own worktree
included; it finds each branch's worktree itself, and steps 6 to 8 always use the main
checkout's tools/ (below). --merge-push is run from the main checkout. --suite only CHECKS for a
suite record; it never runs the suite, so `tools/suite_record.py run --branch <branch>` comes
first (S4 Grok F4 and S4's lesson, 2026-11-25).

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
  5. The push gate, judged with the BASE's gate (Nick, 2026-11-27), exactly as a one-branch
     merge-push: an unreferenced merge of the tip onto main, then the main checkout's
     tools/hooks/pre-push and tools/cross_model_gate.py on main..merge, run by preview_gate in
     a throwaway worktree, with the main checkout's gate data plus the branch worktree's
     review and audit rows main lacks (merge-push's backfill). The gate's chain path counts
     only rows reviewed at the tip's tree, as it will for the real merge-push. The
     report's `judged_with` names the checkers' path and commit. Nothing from the branch
     executes: `own_gate` only compares the branch's tools/cross_model_gate.py and
     tools/hooks/pre-push blobs with main's, and a note says a per-branch push would run the
     branch's differing copy. Before this, step 5 RAN the branch's own hook, so a branch cut
     before a gate change was refused by its own old gate, a branch that edits the gate
     judged itself, and branch code ran before the trusted steps (Codex 1603.F1).
  6. `tools/audit_pii_record.py check --tip <sha>`, pointed at the worktree. The check reads
     the MAIN checkout's audit ledger (2026-11-22): a record held only by the worktree's copy
     is refused, and the refusal carries the tool's message naming the command to fix it.
  7. `tools/check_public_pii.py --scan --stdin-paths` over the branch's added/modified files.
  8. Optional (--suite): `tools/suite_record.py check --tip <sha>` when that tool exists;
     skipped with a notice when it does not.

Steps 5 to 8 judge with the MAIN CHECKOUT's copies of those tools, never the branch's: a
branch that edits its own gate, audit or PII scanner must not get to judge itself (Codex r1 F1,
P0). No step executes the branch's own copies of anything. Those
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
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_report import format_line  # noqa: E402  (a count states its reach)
import merge_chain as mc  # noqa: E402  (one rule for a valid merge, shared with both gates)
import stack_check  # noqa: E402  (codify #3: one stacking rule, shared with suite_record)

ZERO = "0" * 40
# Env vars that turn a gate dry run into a recorded waiver. A read-only check must never
# carry them through, or running it from a shell that has one set would write a ledger row.
WAIVER_ENV = ("CODEX_VERIFY_WAIVE", "AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# How long a landing suite queues behind a running builder suite. A builder holds the lock for
# setup + pytest (capped at suite_record's DEFAULT_TIMEOUT, 3600 s) + teardown, so the wait is
# that cap plus 30 minutes (Grok 585ac8c5 F4: an equal wait could still expire and refuse).
SUITE_WAIT_S = 3600 + 1800


def suite_supports_wait(tool: Path) -> bool:
    """True if the checkers' suite_record.py accepts --wait (C2). GitHub main's copy before C2
    lands rejects the flag with argparse exit 2, so it is passed only when supported (Grok
    585ac8c5 F1). The parser is asked (`run --help`), not the file text: a comment or a
    string naming "--wait" is not support (1294.F2, 1306.F3, 1322.F2). Unreadable, failing
    or silent means unsupported."""
    if not Path(tool).is_file():
        return False
    try:
        r = subprocess.run([sys.executable, str(tool), "run", "--help"], capture_output=True,
                           text=True, timeout=60, cwd=str(Path(tool).parent))
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and re.search(r"^\s+--wait\b", r.stdout, re.M) is not None


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
def base_gate(root: Path, wt: Path, branch: str, tip: str, main_ref: str | None,
              remote_url: str) -> dict:
    """The verdict step 5 uses: the branch judged exactly as a one-branch merge-push would
    judge it (Nick, 2026-11-27). An unreferenced merge of the tip onto main is built
    (merge_chain.build, as merge-push builds it) and preview_gate runs the MAIN CHECKOUT's
    hook and gate on main..merge with main's refspec, plus the branch worktree's review and
    audit rows main lacks (merge-push's backfill). So the gate takes its chain path, which
    counts only rows reviewed at this tip's tree (Fable 2.F1), and the diff is the branch's
    change against main. The branch's own copies never judge it."""
    tools = trusted_tools(root)
    if tools is None:
        return {"rc": None, "ok": False, "reason": NO_TRUSTED, "judged_with": None}
    main = tools.parent
    rc, head, _ = git(["rev-parse", "HEAD"], main)
    judged = {"path": str(tools), "commit": head.strip() if rc == 0 else None}
    if not main_ref:
        return {"rc": None, "ok": False, "reason": "no main ref to judge against",
                "judged_with": judged}
    try:
        merge = mc.build(root, main_ref, tip, f"Merge-push: {branch} {tip}\n")
    except (mc.ChainError, mc.GitError) as e:
        return {"rc": None, "ok": False, "judged_with": judged,
                "reason": f"cannot build the merge onto main: {e}"}
    cross, audit = collect_backfill(main, [{"worktree": str(wt)}])
    try:
        g = preview_gate(root, main, tools, main_ref, merge, remote_url, cross, audit)
    except OSError as e:          # preview_gate's finally has already removed its worktree
        g = {"rc": None, "ok": False, "reason": f"preview worktree: {e}"}
    return {**g, "judged_with": judged, "merge": merge}


# The files a per-branch `git push` runs from the branch's own tree (the installed hook calls
# $root/tools/cross_model_gate.py). Compared as blobs only: nothing from the branch executes
# (Codex 1603.F1, 2026-11-27: running them let branch code write the main checkout).
OWN_GATE_FILES = ("tools/cross_model_gate.py", "tools/hooks/pre-push")


def own_gate_differs(root: Path, tip: str, main_ref: str | None) -> list[str]:
    """OWN_GATE_FILES whose blob at the tip differs from main's (absent on one side counts)."""
    if not main_ref:
        return []
    out = []
    for path in OWN_GATE_FILES:
        a = git(["rev-parse", "--verify", "--quiet", f"{tip}:{path}"], root)[1].strip()
        b = git(["rev-parse", "--verify", "--quiet", f"{main_ref}:{path}"], root)[1].strip()
        if a != b:
            out.append(path)
    return out


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
    message = None
    for line in reversed(out.strip().splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            message = payload.get("message")
            break
    return {"rc": rc, "ok": rc == 0, "tool": str(tool), "tail": tail(out + err),
            "message": message}


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
        try:
            r["refusals"] += [stack_check.format_refusals(branch, [s])
                              for s in stack_check.check(str(root), branch, main_ref)]
        except stack_check.StackCheckError as e:
            r["refusals"].append(f"stack check could not run: {e}")
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
    g = base_gate(root, wt, branch, tip, main_ref, remote_url)
    r["steps"]["gate"] = g
    if not g["ok"]:
        r["refusals"].append(f"push gate rc {g['rc']}: {g.get('reason')}")
    differs = own_gate_differs(root, tip, main_ref)
    r["steps"]["own_gate"] = {"differs_from_main": differs}
    if g["ok"] and differs:
        r.setdefault("notes", []).append(
            f"this branch's own {', '.join(differs)} differs from main's, and a per-branch "
            "`git push` runs the branch's copy, which can refuse what main's gate passes: "
            "land it with --merge-push, not the per-branch push command")
    tools = trusted_tools(root)
    a = audit_check(tools, wt, tip)
    r["steps"]["audit"] = a
    if not a["ok"]:
        r["refusals"].append(f"audit_pii_record check rc {a['rc']}")
        if a.get("message"):          # e.g. recorded_only_in_worktree names its own fix
            r["refusals"].append(f"audit_pii_record: {a['message']}")
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
             "- Push gate rc 0 with main's gate; /audit-pii record present for this tip; "
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
          allowlist_ok: bool = False, descriptions: bool = False) -> dict:
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
    tools = trusted_tools(root)
    checkers = checker_provenance(tools, main)
    if descriptions:
        # CLI check mode reads the description the merge will use, so a bad title shows
        # before push day (2 of 10 titles over 72 chars were found only at push, 11-25).
        # merge_push checks it itself and leaves this off, so nothing is reported twice.
        for r in results:
            probs = ([f"description not checked: {NO_TRUSTED}"] if tools is None
                     else check_description(tools.parent, r["branch"])[1])
            r["steps"]["description"] = {"ok": not probs, "problems": probs}
            if probs:
                r["refusals"] += probs
                r["ready"] = False
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
WEDGE_HELP = ('diagnose: ps -ax -o pid,etime,command | grep -E "ssh git@github.com|SecretAgent"; '
              "a wedged agent is fixed by Nick: kill the waiting push pids, then "
              "pkill -f SecretAgent; sleep 2; open -a Secretive")


# GitHub's ssh endpoints (ssh.github.com serves ssh over port 443).
GITHUB_SSH_HOSTS = {"github.com", "ssh.github.com"}

# git's global options that take a separate value: skipped to reach the subcommand.
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
                   "--super-prefix"}


def _ssh_host_candidates(args: list[str]) -> list[str]:
    """Every token of an ssh argv that could name the host ssh connects to, lowercased
    (host names are case-insensitive). Covers the destination as [user@]host[:path] or
    ssh://[user@]host[:port]/path, the user from -l or -oUser, and the host from
    `-o HostName=<host>` or `-oHostName=<host>` (Codex 11-26 s5c F1 and narrow F1; dc 14:11
    ruling). -p, -oPort and -oUser values are not hosts. Erring toward busy is the safe
    direction: a false busy only delays a landing, so every bare token is a candidate
    rather than only the parsed destination. That fallback is also what catches the
    ps-joined `-o HostName <host>`: ps splits it into separate tokens, so the option parse
    sees `HostName` with no value and the host arrives as a bare token (Fable 6.F2).

    Known limits: a host that only ssh_config supplies (`ssh gh` where `Host gh` sets
    HostName github.com), a ProxyJump/ProxyCommand hop, an IP address for GitHub, or a
    HostName given inside clustered short flags (`ssh -vo HostName=github.com gh`, Fable
    6.F1) is not recognised. ps output does not carry ssh_config, so the first three cannot
    be read from argv; git does not produce the fourth."""
    out = []
    prev = ""
    for t in args:
        opt = t[2:] if t.startswith("-o") and len(t) > 2 else (t if prev == "-o" else None)
        if opt is not None:
            key, _, val = opt.replace("=", " ", 1).partition(" ")
            if key.lower() == "hostname" and val:
                out.append(val.strip().lower())
        elif not t.startswith("-"):
            out.append(t.removeprefix("ssh://").split("@")[-1].split(":")[0]
                       .split("/")[0].lower())
        prev = t
    return out


def _is_busy_command(command: str) -> bool:
    """True when the process IS a network git command or an ssh session to GitHub, read
    from its argv: argv[0]'s name, then git's subcommand. Text quoted inside another
    program's arguments (a review prompt that mentions "git push") is not a push (11-25).
    ps joins argv with spaces, so a token split is argv up to spaces inside an argument,
    which can only add tokens after argv[0], never change it."""
    toks = command.split()
    if not toks:
        return False
    exe = os.path.basename(toks[0])
    if exe == "ssh":
        return any(h in GITHUB_SSH_HOSTS for h in _ssh_host_candidates(toks[1:]))
    if exe != "git":
        return False
    i = 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in _GIT_VALUE_OPTS else 1
    return i < len(toks) and toks[i] in ("push", "fetch", "pull")


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
        if len(parts) == 3 and parts[0] != str(os.getpid()) and _is_busy_command(parts[2]):
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
    denylists (GATE_DATA[2:]) plus the backfill rows not yet in main; the hook reads main's
    ledgers itself, through git's common dir. Nothing in the main checkout changes."""
    tmp = Path(tempfile.mkdtemp(prefix="merge-push-preview."))
    wt = tmp / "wt"
    try:
        rc, _, err = git(["worktree", "add", "--no-checkout", "--detach", str(wt), base], root)
        if rc != 0:
            return {"rc": None, "ok": False, "reason": f"preview worktree: {err.strip()[:200]}"}
        shutil.copytree(checkers, wt / "tools", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".*", "__pycache__", "logs", "*.log"))
        # The two ledgers are NOT copied: the hook in this linked worktree reads main's
        # through git's common dir and adds this copy's backfill rows (S2d). Only the
        # gitignored denylists, which nothing reads from main, are copied.
        for name in GATE_DATA[2:]:
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


# ---------------------------------------------------------------- live-data preflight
# S3, 2026-11-24: two merge-push suites ran 846 s and 917 s, then failed on
# test_workstream_backlog.py::test_every_live_workstream_has_a_valid_header_or_is_exempt,
# because live workstream headers went stale between the dry run and the real run. That test
# is unmarked and reads the MAIN checkout's data/workstreams even from suite_record's temp
# worktree. So before anything slow, merge-push runs the tests that read live data, in the
# main checkout (the only tree that has data/), and stops on the first failure.
#
# What runs: every @pytest.mark.live_data test (they SKIP in the suite's linked worktree,
# tests/conftest.py, so this is the one place they meet the real files), plus the whole
# test_workstream_backlog.py. Main checkout = main's code: on a real run the checkers-current
# rule makes it GitHub main. LIMIT: a branch that edits a live-data test meets real data only
# after it lands. Exclusions: none. Every file selected was read for writes on 2026-11-24
# (open w/a, write_text/bytes, *_write.py, shutil, unlink, mkdir); each write targets
# tmp_path. Ruling (job-search-b7, 2026-11-24): a failure here BLOCKS, because every branch
# lands onto main and main disagrees with real data.
#
# Writer guard (b7's condition): a test run from the main checkout can write Nick's live data
# (three tests in test_check_workstream_heading_time.py did, 11-24). data/ and memory/ are
# gitignored, so `git status` cannot see a write there; the guard compares (mtime, size) of
# every file under them before and after, and refuses when anything changed. Another session
# appending a log in the same seconds trips it too, so the refusal says to re-run and treat a
# repeat on the same file as a writer.
#
# Backup trees (Grok F1 on s3-preflight-1124, P1 parked; fixed 2026-11-25): tools/backup-data.sh
# rsyncs the live memory tier into memory/canonical-sidecar/ and memory/global-claude-mirror/,
# so a nightly backup overlapping the preflight changed files there and the refusal blamed a
# test. Those two trees are copies written only by the backup, so they are left out.
#
# Dirty tests/ (Grok F2, same run): the checkers-current rule statuses tools/ only, so an
# uncommitted change under the main checkout's tests/ would run here as if it were main and
# could weaken the live check. The preflight refuses it before pytest, naming the paths as
# working-tree dirt; an unreadable git status refuses too (fail closed).
PREFLIGHT_RUNS = (("-m", "live_data", "tests"), ("tests/scripts/test_workstream_backlog.py",))
PREFLIGHT_GUARDED = ("data", "memory")
PREFLIGHT_BACKUP_TREES = ("memory/canonical-sidecar", "memory/global-claude-mirror")
PREFLIGHT_TIMEOUT = 600


def dirty_tests(main: Path) -> tuple[list[str] | None, str | None]:
    """(paths under tests/ with uncommitted changes, None), or (None, why) when git status
    cannot be read. Untracked files count: pytest would collect them."""
    # --no-renames: with -z a rename record carries the old path as a second NUL field,
    # which this parser would read as another record (Codex 11-25 F2 on s5).
    rc, out, err = git(["status", "--porcelain=v1", "-z", "--no-renames",
                        "--untracked-files=all", "--", "tests"], main)
    if rc != 0:
        return None, f"git status in the main checkout failed: {(err or out).strip()[:200]}"
    paths = []
    for entry in out.split("\0"):
        if len(entry) > 3:
            paths.append(entry[3:])
    return sorted(paths), None


def data_snapshot(main: Path) -> dict[str, tuple[int, int]]:
    snap = {}
    skip = {main / t for t in PREFLIGHT_BACKUP_TREES}
    for top in PREFLIGHT_GUARDED:
        for dirpath, dirs, files in os.walk(main / top):
            dirs[:] = [d for d in dirs if Path(dirpath) / d not in skip]
            for name in files:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                snap[str(p.relative_to(main))] = (st.st_mtime_ns, st.st_size)
    return snap


def snapshot_changes(before: dict, after: dict) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def preflight(main: Path, runs=PREFLIGHT_RUNS, timeout: int = PREFLIGHT_TIMEOUT) -> dict:
    """Run the live-data tests in the main checkout. ok only when every run exits 0 with a
    readable report, no failures or errors, more than 0 collected, and no file under
    data/ or memory/ changed."""
    import suite_record as sr   # one JUnit reader and one child env, shared with the suite
    res = {"ok": False, "runs": [], "failing": [], "data_changed": [], "problem": None,
           "dirty_tests": []}
    dirty, why = dirty_tests(main)
    if why or dirty:
        res["dirty_tests"] = dirty or []
        res["git_status_error"] = why
        res["problem"] = why or ("uncommitted changes under tests/ in the main checkout: "
                                 + ", ".join(dirty))
        res["duration_s"] = 0.0
        return res
    env = {**sr.child_env(), "PYTHONDONTWRITEBYTECODE": "1"}
    before = data_snapshot(main)
    start = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="merge-push-preflight."))
    try:
        for i, sel in enumerate(runs):
            junit = tmp / f"run{i}.xml"
            argv = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                    f"--junitxml={junit}", *sel]
            try:
                p = subprocess.run(argv, cwd=main, env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=timeout)
                rc, out = p.returncode, p.stdout + p.stderr
            except subprocess.TimeoutExpired:
                rc, out = None, f"timeout after {timeout} s"
            except OSError as e:
                rc, out = 127, f"{type(e).__name__}: {e}"
            counts = sr.parse_junit(junit)
            detail = sr.junit_cases(junit)
            res["runs"].append({"select": " ".join(sel), "rc": rc, **(counts or {}),
                                "skips_by_reason": detail["skips_by_reason"]})
            res["failing"] += detail["failing"]
            if rc is None:
                res["problem"] = f"{' '.join(sel)}: {out}"
            elif counts is None:
                res["problem"] = f"{' '.join(sel)}: pytest exit {rc} wrote no readable report"
            elif counts["failed"] or counts["errors"] or rc != 0:
                res["problem"] = f"{' '.join(sel)}: pytest exit {rc}"
            elif counts["collected"] <= 0:
                res["problem"] = f"{' '.join(sel)}: collected 0 tests"
            if res["problem"]:
                res["tail"] = tail(out, 15)
                break
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["duration_s"] = round(time.monotonic() - start, 1)
    res["data_changed"] = snapshot_changes(before, data_snapshot(main))
    res["ok"] = res["problem"] is None and not res["data_changed"]
    return res


def preflight_refusal(pf: dict) -> str:
    if pf.get("git_status_error"):
        # Its own text (Grok 11-25 F1 on s5): no test ran, so neither "the failure is on
        # main" nor a pointer at preflight.tail applies.
        return ("live-data preflight refused before running: could not read git status of "
                "the main checkout's tests/ (fail closed): " + pf["git_status_error"])
    if pf.get("dirty_tests"):
        return ("live-data preflight refused before running: the main checkout has "
                "uncommitted changes under tests/ (working-tree dirt, not main): "
                + ", ".join(pf["dirty_tests"][:10])
                + ". Commit or remove them in the main checkout, then re-run")
    if pf["problem"]:
        names = ", ".join(pf["failing"]) or "no test named (see preflight.tail)"
        return (f"live-data preflight failed in the main checkout after {pf['duration_s']} s; "
                f"the failure is on main, not this branch: {pf['problem']}. Failing: {names}")
    return ("live-data preflight changed files under data/ or memory/: "
            + ", ".join(pf["data_changed"][:10])
            + ". Re-run; if the same file changes again, a preflight test writes live data")


def merge_push(root: Path, branches: list[str], *, dry_run: bool, gh=None,
               checkers: Path | None = None, busy=None, want_suite: bool | None = None,
               run_preflight=None) -> dict:
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
    # Seconds, before check(), the chain, the gate and the suite (S3, 2026-11-24).
    pf = rep["preflight"] = (run_preflight or preflight)(main)
    if not pf["ok"]:
        rep["refusals"].append(preflight_refusal(pf))
        return rep
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
        wait = ["--wait", str(SUITE_WAIT_S)] if suite_supports_wait(tool) else []
        rc, out, err = run([sys.executable, str(tool), "run", "--branch", cur, "--base", base,
                            *wait, "--repo-root", str(main)], cwd=main,
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
    if rep.get("preflight"):
        pf = rep["preflight"]
        n = sum(r.get("collected", 0) for r in pf["runs"])
        out.append(f"live-data preflight: {'ok' if pf['ok'] else 'FAIL'} "
                   f"({n} test(s) in {pf['duration_s']} s, main checkout)")
        if not pf["ok"] and pf.get("tail"):
            out += ["  " + ln for ln in pf["tail"].splitlines()]
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
    ff = None     # a single fast-forward push of one branch tip (2026-11-27)
    try:
        chain = mc.walk(root, base, tip)
    except mc.ChainError as e:
        if git(["merge-base", "--is-ancestor", base, tip], root)[0] != 0 or \
                not git(["rev-list", f"{base}..{tip}"], root)[1].strip() or \
                git(["rev-list", "--merges", f"{base}..{tip}"], root)[1].strip():
            res["problems"].append(f"not a merge-push chain: {e}")
            return res
        names = git(["for-each-ref", "--points-at", tip, "--format=%(refname:short)",
                     "refs/heads"], root)[1].split()
        ff, chain = ", ".join(n for n in names if n != "main") or "?", []
    except mc.GitError as e:
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
    what = (f"fast-forward landed {ff}" if ff else
            f"merge-push landed {len(chain)} branch(es)")
    lines = [f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} by `date` | {what} "
             f"(land_branch.py --verify-landed)\n",
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


def install_term_handler() -> None:
    """SIGTERM (a stopped task, `kill`) raises SystemExit(143), so every `finally` runs and
    preview_gate removes its throwaway worktree. Python's default SIGTERM skips them, and a
    stopped run left one registered (2026-11-27 09:43)."""
    import signal
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))


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
    install_term_handler()
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
    rep = check(root, list(dict.fromkeys(args.branches)), args.suite, descriptions=True)
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 0 if rep["all_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
