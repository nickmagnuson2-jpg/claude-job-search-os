#!/usr/bin/env python3
"""stack_check.py: refuse a branch stacked on another unlanded branch whose current tip it lacks.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/stack_check.py <branch-or-sha> [--main main] [--repo .] [--json]

Exit 0 clean | 1 refused (stale stack) | 2 the tip or main does not resolve.

WHY. On 2026-11-22 two builds (B2, B7) were stacked on commits of a third (B1) that B1 then
moved past. B7's full suite ran on the stale base and failed, costing the one shared suite slot
and a diagnose cycle; the org's manual merge-base check caught it only afterwards (codify #3).
A replay of this rule over that night's reflogs refuses B7's tip 856a7442 at suite start,
naming its parent with 5 commits missing, and finds no refusal anywhere today.

THE RULE. Tip T is stacked on branch P when T contains a commit that P itself produced and that
is not yet in main, while P's current tip is not in T. "P itself produced" = a commit recorded in
P's reflog by commit, merge, rebase, cherry-pick, pull or am, so a branch that was only created
at someone else's commit (a backup, a fresh branch) is never anyone's parent. The refusal names
P, the newest of P's commits that T has (built_on), P's tip, and the commits T is missing.

MECHANISM ONLY. land_branch.py and suite_record.py call check() and decide what a refusal means
for them; neither restates the rule (tests/scripts/test_stack_check.py checks that).

LIMITS (by construction, not bugs):
  - Reflogs are local to this clone and expire (git's default: 90 days for reachable entries).
    A parent whose reflog is gone, or a deleted parent branch, is invisible.
  - "Landed" means P's tip is an ancestor of main. A parent landed by squash or cherry-pick
    still counts as unlanded and can refuse a child that already has its content.
  - Read-only. Never rebases, never fetches.
"""
import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field

OWN_ACTIONS = ("commit", "merge", "rebase", "cherry-pick", "pull", "am")


@dataclass
class Refusal:
    parent: str
    built_on: str
    parent_tip: str
    missing: list = field(default_factory=list)


class StackCheckError(RuntimeError):
    """The tip or main does not resolve."""


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def _resolve(repo: str, ref: str) -> str:
    r = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if r.returncode != 0 or not r.stdout.strip():
        raise StackCheckError(f"cannot resolve {ref!r} in {repo}")
    return r.stdout.strip()


def _is_ancestor(repo: str, a: str, b: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def _branches(repo: str) -> dict:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads").stdout
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def own_commits(repo: str, branch: str) -> list:
    """Commits the branch itself produced, newest first, from its reflog."""
    out = _git(repo, "reflog", "show", "--format=%H\t%gs", f"refs/heads/{branch}").stdout
    seen, own = set(), []
    for line in out.splitlines():
        sha, _, msg = line.partition("\t")
        action = msg.split(":", 1)[0].split(" ", 1)[0]
        if action in OWN_ACTIONS and sha not in seen:
            seen.add(sha)
            own.append(sha)
    return own


def check(repo: str, tip: str, main: str = "main") -> list:
    """Refusals for `tip` (a branch name or sha); empty when it is not a stale stack."""
    tip_sha = _resolve(repo, tip)
    main_sha = _resolve(repo, main)
    refusals = []
    for name, parent_tip in sorted(_branches(repo).items()):
        if name in (tip, main) or parent_tip == tip_sha:
            continue
        if _is_ancestor(repo, parent_tip, main_sha) or _is_ancestor(repo, parent_tip, tip_sha):
            continue  # landed, or already contained
        built_on = next((c for c in own_commits(repo, name)
                         if not _is_ancestor(repo, c, main_sha) and _is_ancestor(repo, c, tip_sha)),
                        None)
        if built_on is None:
            continue
        missing = _git(repo, "rev-list", "--reverse", f"{tip_sha}..{parent_tip}").stdout.split()
        refusals.append(Refusal(name, built_on, parent_tip, missing))
    return refusals


def format_refusals(tip: str, refusals: list) -> str:
    lines = []
    for r in refusals:
        n = len(r.missing)
        shown = ", ".join(c[:8] for c in r.missing[:5]) + (" ..." if n > 5 else "")
        lines.append(f"REFUSED: {tip} is stacked on unlanded branch {r.parent} (built on "
                     f"{r.built_on[:8]}), whose tip {r.parent_tip[:8]} it does not contain: "
                     f"{n} commit{'s' if n != 1 else ''} missing ({shown}). Rebase onto "
                     f"{r.parent} or wait for it to land.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tip")
    ap.add_argument("--main", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        refusals = check(a.repo, a.tip, a.main)
    except StackCheckError as e:
        print(f"stack_check: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"tip": a.tip, "refused": bool(refusals),
                          "refusals": [asdict(r) for r in refusals]}, indent=1))
    elif refusals:
        print(format_refusals(a.tip, refusals), file=sys.stderr)
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
