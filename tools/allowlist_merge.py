#!/usr/bin/env python3
"""allowlist_merge.py -- key-wise three-way merge for tools/mutation-allow.json.

WHY THIS EXISTS (2026-11-16). Every branch that allowlists a mutation survivor appends
its keys at the end of one JSON object. Two branches with disjoint keys therefore edit
the same last lines, and git's line merge reports a conflict on every landing: on 11-16,
glob-rewrite x friction-capture, then q11-shell-tokens x both. The keys never collided,
only the lines did, and each conflict was resolved by hand. Design:
output/overnight-merge-back/110726-orchestration-design.md, section E (FT:37).

RULES, per key:
  - added on either side: kept;
  - removed on one side and unchanged on the other: removed;
  - changed on one side only: that side's reason;
  - the same change on both sides: kept once;
  - two different results for one key (two reasons, or a rewrite against a removal):
    a conflict. Every conflict is reported, never only the first.
A reason must be a non-blank string on every side, so no empty reason reaches the output.

ORDER. The file is append-ordered, not sorted (20 order breaks at f58251d6), so the
output keeps OURS' order and puts THEIRS' new keys after it in THEIRS' order. Re-sorting
would rewrite hundreds of lines and make the merge commit unreviewable.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_merge.py BASE OURS THEIRS [-o OUT]

With no -o the result is written to OURS, which is the git merge-driver contract
(`%O %A %B`; git reads the merged file back from %A). Exit codes: 0 merged, 1 conflict
(each key and both reasons on stderr; nothing written), 2 unreadable input.

Registering it as a merge driver is a repo-config change and is NOT done here:
  .gitattributes:  tools/mutation-allow.json merge=allowlist
  git config merge.allowlist.driver "PYTHONIOENCODING=utf-8 python3 tools/allowlist_merge.py %O %A %B"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MISSING = object()


class AllowlistConflict(ValueError):
    """One key ended with two different results. `.conflicts` is a list of
    (key, ours_reason_or_None, theirs_reason_or_None)."""

    def __init__(self, conflicts: list[tuple[str, str | None, str | None]]):
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} conflicting key(s): "
                         + ", ".join(c[0] for c in conflicts))


def _check(side: str, data) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{side} is not a JSON object")
    for key, why in data.items():
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f"{side}: {key} has an empty or non-string reason")
    return data


def merge(base: dict, ours: dict, theirs: dict) -> dict:
    """Merge three allowlists by key. Raises AllowlistConflict on any conflict and
    ValueError on malformed input."""
    base = _check("base", base)
    ours = _check("ours", ours)
    theirs = _check("theirs", theirs)

    out: dict[str, str] = {}
    conflicts: list[tuple[str, str | None, str | None]] = []
    order = list(ours) + [k for k in theirs if k not in ours] \
        + [k for k in base if k not in ours and k not in theirs]
    for key in order:
        b = base.get(key, _MISSING)
        o = ours.get(key, _MISSING)
        t = theirs.get(key, _MISSING)
        if o == t:
            result = o
        elif o == b:
            result = t
        elif t == b:
            result = o
        else:
            conflicts.append((key, None if o is _MISSING else o,
                              None if t is _MISSING else t))
            continue
        if result is not _MISSING:
            out[key] = result
    if conflicts:
        raise AllowlistConflict(conflicts)
    return out


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}: json.load would keep only the last "
                             f"reason and drop the first without a word")
        seen[key] = value
    return seen


def load(path: Path, empty_ok: bool = False) -> dict:
    """Read one side. Only BASE may be empty: git passes an empty %O when the two
    sides have no common ancestor. An empty OURS or THEIRS is refused, because reading
    it as {} would remove every key that side still holds."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        if empty_ok:
            return {}
        raise ValueError(f"{path} is empty; only BASE may be empty")
    return json.loads(text, object_pairs_hook=_no_duplicates)


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base")
    ap.add_argument("ours")
    ap.add_argument("theirs")
    ap.add_argument("-o", "--output", default=None,
                    help="write here instead of OURS (the merge-driver default)")
    args = ap.parse_args(argv)

    try:
        sides = [load(Path(args.base), empty_ok=True),
                 load(Path(args.ours)), load(Path(args.theirs))]
        merged = merge(*sides)
    except AllowlistConflict as exc:
        print(f"allowlist_merge: {exc}", file=sys.stderr)
        for key, o, t in exc.conflicts:
            print(f"\nCONFLICT {key}\n  ours:   {o if o is not None else '(removed)'}"
                  f"\n  theirs: {t if t is not None else '(removed)'}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"allowlist_merge: {exc}", file=sys.stderr)
        return 2

    target = Path(args.output) if args.output else Path(args.ours)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(dumps(merged), encoding="utf-8")
    tmp.replace(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
