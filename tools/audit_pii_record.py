#!/usr/bin/env python3
"""audit_pii_record.py -- the record /audit-pii writes on a CLEAN verdict, and the check
the pre-push guard runs against it.

    record --tip SHA [--files N] [--accept FILE:TOKEN ...]   append a clean record
    check  --tip SHA                                        exit 0 if the tip's tree has one

A record is keyed to the TREE of the pushed commit (`git rev-parse SHA^{tree}`), not the
commit, so rewording a commit message keeps its record while any content change needs a
new pass. Rows go to the gitignored tools/.audit-pii-ledger.jsonl, one JSON object per line:

    {"kind": "audit", "verdict": "clean", "tree", "tip", "recorded", "files_scanned", "accepted"}
    {"kind": "waiver", "tree", "tip", "reason", "recorded"}     (written by prepush_pii_guard)

Only a "kind": "audit" row with "verdict": "clean" clears a tree. A waiver clears only the
push it was typed for.

LIMIT: Claude writes the record, so it proves the semantic pass RAN to a clean verdict on
that tree. It does not prove the pass was any good. (Nick, 2026-11-05.)

FROM A LINKED WORKTREE (2026-11-22, 3 fires in one day): the CLI uses the MAIN checkout's
ledger, the one merge-push's gate reads. `record` writes there and also to the worktree's
copy, which a push from the worktree and land_branch step 5 read; `check` passes only on
main's rows, and a record held only by the worktree's copy fails with code
recorded_only_in_worktree. A linked worktree whose main cannot be found exits 2
(main_checkout_unresolved) and writes nothing. The library functions (read_rows, has_clean_record,
append_waiver) still use the root they are given: land_branch's merge-push preview runs the
guard inside a throwaway worktree whose copy holds rows main does not have yet.

FROM THE MAIN CHECKOUT (2026-11-24, S4b): `record` also appends the row to the copy of every
linked worktree that has a BRANCH checked out at a commit with the recorded tree, because a
push from that worktree reads its own copy. Before this, a record made from main reached only
main, and land_branch's dry run from the worktree refused a branch that had a clean record
(L1, overnight 2026-11-24). Detached worktrees (mutation and suite_record temp trees) are
skipped: rows there outlive the tree that used them. The result names the copies reached.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


LEDGER = Path("tools") / ".audit-pii-ledger.jsonl"


class LedgerError(Exception):
    """The ledger exists but cannot be read. Never read as "no record"."""


def tree_of(root: Path, sha: str) -> str | None:
    """The tree id of commit SHA, or None when SHA is not a commit here."""
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet",
                        f"{sha}^{{commit}}^{{tree}}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def commit_of(root: Path, sha: str) -> str | None:
    """The full commit id SHA names in `root` (HEAD differs between checkouts)."""
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet",
                        f"{sha}^{{commit}}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


class MainUnresolved(Exception):
    """`root` is a linked worktree whose main checkout cannot be found."""


def resolve_main(root: Path) -> Path:
    """The main checkout's root, or `root` itself when it is one.

    codex_verify._main_checkout is the resolver the review ledger already uses
    (mirror_to_main, finding_write, the open-P0 line), so both ledgers agree on
    "main". It rejects a --separate-git-dir holder (Grok r1 F2) and raises for a linked
    worktree it cannot place (Codex r1 F1). Imported here, not at module top, so the
    push guard that imports this module's library functions does not load it."""
    import codex_verify as cv
    try:
        main = cv._main_checkout(Path(root).resolve())
    except RuntimeError as e:
        raise MainUnresolved(str(e)) from None
    return Path(main) if main else Path(root)


def branch_worktrees_at(main: Path, tree: str) -> list[Path]:
    """Linked worktrees with a branch checked out at a commit whose tree is TREE.

    From `git worktree list --porcelain`: the main checkout (listed first) is skipped, a
    detached HEAD is skipped, and a worktree whose directory is gone is skipped. Raises
    RuntimeError when git cannot list them, so the caller reports it instead of acting as
    if there were none."""
    r = subprocess.run(["git", "-C", str(main), "worktree", "list", "--porcelain"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree list failed: {r.stderr.strip()}")
    out: list[Path] = []
    for block in r.stdout.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, _, val = line.partition(" ")
            fields[key] = val
        path = fields.get("worktree")
        if not path or "branch" not in fields or "HEAD" not in fields:
            continue
        wt = Path(path)
        if wt.resolve() == Path(main).resolve() or not wt.is_dir():
            continue
        if tree_of(main, fields["HEAD"]) == tree:
            out.append(wt)
    return out


def read_rows(root: Path) -> list[dict]:
    path = root / LEDGER
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise LedgerError(f"{LEDGER} line {n} is not JSON ({e.msg})") from None
        if not isinstance(row, dict):
            raise LedgerError(f"{LEDGER} line {n} is not an object")
        rows.append(row)
    return rows


def has_clean_record(root: Path, tree: str) -> bool:
    return any(r.get("kind") == "audit" and r.get("verdict") == "clean" and r.get("tree") == tree
               for r in read_rows(root))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_row(root: Path, row: dict) -> None:
    path = root / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_waiver(root: Path, tree: str, tip: str, reason: str) -> None:
    append_row(root, {"kind": "waiver", "tree": tree, "tip": tip, "reason": reason,
                      "recorded": _now()})


def _out(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record or check a clean /audit-pii pass.")
    ap.add_argument("--repo-root", default=".")
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--tip", required=True)
    rec.add_argument("--files", type=int, default=None, help="files the pass scanned")
    rec.add_argument("--accept", action="append", default=[],
                     help="an accepted ambiguous entry, FILE:TOKEN (repeatable)")
    chk = sub.add_parser("check")
    chk.add_argument("--tip", required=True)
    args = ap.parse_args(argv)

    root = Path(args.repo_root)
    tree = tree_of(root, args.tip)
    if tree is None:
        return _out({"status": "error", "code": "not_a_commit",
                     "message": f"{args.tip} is not a commit in {root}"}, 1)
    try:
        main = resolve_main(root)
    except MainUnresolved as e:
        return _out({"status": "error", "code": "main_checkout_unresolved",
                     "message": f"{e}; the push gate reads main's ledger, so run this from "
                                "the main checkout"}, 2)
    in_worktree = main.resolve() != root.resolve()
    ledger = str(main / LEDGER)
    if args.command == "record":
        row = {"kind": "audit", "verdict": "clean", "tree": tree, "tip": args.tip,
               "recorded": _now(), "files_scanned": args.files, "accepted": args.accept}
        append_row(main, row)
        if in_worktree:
            # The worktree's copy too: a push run from the worktree, and land_branch
            # step 5's dry run, read it (the hook's root is --show-toplevel) (Grok r1 F1).
            append_row(root, row)
            return _out({"status": "ok", "action": "record", "tree": tree, "tip": args.tip,
                         "ledger": ledger}, 0)
        # From main: every branch worktree at this tree pushes from its own copy (S4b).
        try:
            reached = branch_worktrees_at(main, tree)
        except RuntimeError as e:
            return _out({"status": "error", "code": "worktrees_unlisted", "tree": tree,
                         "ledger": ledger,
                         "message": f"{e}; main's record is written, but no worktree copy "
                                    "was; re-record from the branch's worktree"}, 2)
        for wt in reached:
            append_row(wt, row)
        return _out({"status": "ok", "action": "record", "tree": tree, "tip": args.tip,
                     "ledger": ledger, "worktrees": [str(w) for w in reached]}, 0)
    try:
        ok = has_clean_record(main, tree)
        stranded = not ok and in_worktree and has_clean_record(root, tree)
    except LedgerError as e:
        return _out({"status": "error", "code": "ledger_unreadable", "message": str(e)}, 2)
    if stranded:
        fix = " ".join(shlex.quote(str(a)) for a in (
            "python3", main / "tools" / "audit_pii_record.py", "--repo-root", main,
            "record", "--tip", commit_of(root, args.tip)))
        fix = f"PYTHONIOENCODING=utf-8 {fix}"
        return _out({"status": "ok", "action": "check", "tree": tree, "recorded": False,
                     "code": "recorded_only_in_worktree", "ledger": ledger,
                     "message": "recorded only in this worktree's copy, which merge-push's gate "
                                f"does not read; re-record from the main checkout: {fix}"}, 1)
    return _out({"status": "ok", "action": "check", "tree": tree, "recorded": ok,
                 "ledger": ledger}, 0 if ok else 1)


if __name__ == "__main__":
    sys.exit(main())
