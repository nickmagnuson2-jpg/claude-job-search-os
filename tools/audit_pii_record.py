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
"""
from __future__ import annotations

import argparse
import json
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
    if args.command == "record":
        append_row(root, {"kind": "audit", "verdict": "clean", "tree": tree, "tip": args.tip,
                          "recorded": _now(), "files_scanned": args.files,
                          "accepted": args.accept})
        return _out({"status": "ok", "action": "record", "tree": tree, "tip": args.tip}, 0)
    try:
        ok = has_clean_record(root, tree)
    except LedgerError as e:
        return _out({"status": "error", "code": "ledger_unreadable", "message": str(e)}, 2)
    return _out({"status": "ok", "action": "check", "tree": tree, "recorded": ok}, 0 if ok else 1)


if __name__ == "__main__":
    sys.exit(main())
