#!/usr/bin/env python3
"""allowlist_write.py -- add or remove a mutation-allowlist entry, under a lock.

WHY THIS EXISTS (2026-09-07). tools/mutation-allow.json is shared state that several
concurrent sessions write, and every one of them edited it with an ad-hoc
`json.load` / mutate / `json.dump` script. That is a read-modify-write with no lock: two
sessions overlapping lose one set of entries entirely, with both reporting success.

The same shape was already found and fixed one level down, in the cross-model ledger
(cross_model_gate.append_row and finding_write.set_disposition now share an advisory
lock). This file had the identical defect and no owner. On 2026-09-06 two sessions wrote
it within the same hour; it survived by luck, not by design.

A locked writer only helps if it is the path people take, so this is a CLI rather than a
library function: `PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py add ...` is
shorter than the inline python it replaces.

WHY A REASON IS MANDATORY. An allowlist entry without a justification is how a mutation
gate decays back into "green means done" -- the repo's rule is that a surviving mutant is
acceptable only with a written reason. `--why` is required and may not be blank, and the
`add` path refuses to overwrite an existing reason without --force, because silently
replacing someone else's justification erases the argument it recorded.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py add <key> --why "<reason>"
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py remove <key>
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py get <key>
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py list [--prefix tools/foo.py]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import inbox_lock  # noqa: E402

ALLOW_NAME = "mutation-allow.json"

# A key is <path>::<func>::<OP>::<8 hex>. Checked because a typo'd key is not an error
# anywhere else: it simply never matches a mutant, so the entry sits in the file looking
# like coverage while the survivor it was meant to explain keeps surviving.
KEY_PARTS = 4


def allow_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tools" / ALLOW_NAME


def load(repo_root: Path) -> dict:
    path = allow_path(repo_root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _dump(repo_root: Path, data: dict) -> None:
    """Write the whole file, sorted, with a trailing newline.

    Called only inside the lock. Sorted so concurrent additions by different sessions
    produce a minimal, reviewable diff instead of reordering the file.
    """
    path = allow_path(repo_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def validate_key(key: str) -> None:
    parts = key.split("::")
    if len(parts) != KEY_PARTS:
        raise ValueError(
            f"key must be '<path>::<func>::<OP>::<hash>' ({KEY_PARTS} parts), "
            f"got {len(parts)}: {key!r}")
    if not parts[0].endswith(".py"):
        raise ValueError(f"first part must be a .py path, got {parts[0]!r}")


def add(repo_root: Path, key: str, why: str, force: bool = False,
        timeout: float = 30.0) -> dict:
    validate_key(key)
    if not why or not why.strip():
        raise ValueError("--why is required and may not be blank: an allowlist entry "
                         "without a justification is how the gate decays back into "
                         "'green means done'")
    path = allow_path(repo_root)
    with inbox_lock.file_lock(path, timeout=timeout):
        data = load(repo_root)
        existing = data.get(key)
        if existing and not force:
            raise ValueError(
                f"{key} is already allowlisted; pass --force to replace its reason. "
                f"Existing: {existing[:120]}...")
        before = len(data)
        data[key] = why.strip()
        _dump(repo_root, data)
    return {"key": key, "action": "replaced" if existing else "added",
            "entries_before": before, "entries_after": before + (0 if existing else 1)}


def remove(repo_root: Path, key: str, timeout: float = 30.0) -> dict:
    path = allow_path(repo_root)
    with inbox_lock.file_lock(path, timeout=timeout):
        data = load(repo_root)
        if key not in data:
            raise KeyError(f"{key} is not in the allowlist")
        before = len(data)
        data.pop(key)
        _dump(repo_root, data)
    return {"key": key, "action": "removed",
            "entries_before": before, "entries_after": before - 1}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="seconds to wait for the file lock")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="allowlist one mutant, with a reason")
    p_add.add_argument("key")
    p_add.add_argument("--why", required=True)
    p_add.add_argument("--force", action="store_true",
                       help="replace an existing reason")

    p_rm = sub.add_parser("remove", help="drop one entry")
    p_rm.add_argument("key")

    p_get = sub.add_parser("get", help="show one entry's reason")
    p_get.add_argument("key")

    p_ls = sub.add_parser("list", help="keys, optionally filtered")
    p_ls.add_argument("--prefix", default=None)

    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    try:
        if args.cmd == "add":
            out = add(root, args.key, args.why, force=args.force, timeout=args.timeout)
        elif args.cmd == "remove":
            out = remove(root, args.key, timeout=args.timeout)
        elif args.cmd == "get":
            data = load(root)
            if args.key not in data:
                raise KeyError(f"{args.key} is not in the allowlist")
            out = {"key": args.key, "why": data[args.key]}
        else:
            data = load(root)
            keys = sorted(k for k in data
                          if not args.prefix or k.startswith(args.prefix))
            out = {"count": len(keys), "keys": keys}
    except (KeyError, ValueError, inbox_lock.LockTimeout) as exc:
        print(json.dumps({"error": str(exc).strip("'")}), file=sys.stderr)
        return 2

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
