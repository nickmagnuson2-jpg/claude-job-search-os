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
      (a reason that says EQUIVALENT must contain "EXECUTED: <command run> -> <observed
      result>"; see the executed-evidence check below)
      [--differs-from "<NOT EQUIVALENT precedent key>: <why this site differs>"]
      (required when the reason says EQUIVALENT and an entry with the same operator and
      line hash says NOT EQUIVALENT; see the precedent check below)
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py remove <key>
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py get <key>
  PYTHONIOENCODING=utf-8 python3 tools/allowlist_write.py list [--prefix tools/foo.py]
"""
from __future__ import annotations

import argparse
import json
import re
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


def serialize(data: dict) -> str:
    """The one byte form of the allowlist: sorted keys, 2-space indent, trailing newline.

    tools/merge_chain.py resolves a mutation-allow.json merge conflict to exactly these
    bytes and the push gate recomputes them, so this function is the single definition
    both the writer and the gate import (Nick, 2026-11-18: allowlist conflicts are
    resolved by allowlist_merge + _dump)."""
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _dump(repo_root: Path, data: dict) -> None:
    """Write the whole file, sorted, with a trailing newline.

    Called only inside the lock. Sorted so concurrent additions by different sessions
    produce a minimal, reviewable diff instead of reordering the file.
    """
    path = allow_path(repo_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialize(data), encoding="utf-8")
    tmp.replace(path)


def validate_key(key: str) -> None:
    parts = key.split("::")
    if len(parts) != KEY_PARTS:
        raise ValueError(
            f"key must be '<path>::<func>::<OP>::<hash>' ({KEY_PARTS} parts), "
            f"got {len(parts)}: {key!r}")
    if not parts[0].endswith(".py"):
        raise ValueError(f"first part must be a .py path, got {parts[0]!r}")


# --- precedent check (codify #3, 2026-11-23) -----------------------------------------
# A reason that claims a mutant is EQUIVALENT must engage any entry of the same shape whose
# reason is labelled NOT EQUIVALENT. Shape = same operator + same line hash, any file: the
# hash covers the line text only, so this is where copied code (a sibling hook) shows up.
#
# Two predicates, deliberately asymmetric (Grok r1 F1-F3, 2026-11-23):
#   - a PRECEDENT is a reason that STARTS with a NOT EQUIVALENT label, the file's own
#     convention (4 entries at b2495952). Mentioning "not equivalent" later in a reason
#     (e.g. an EQUIVALENT reason explaining that a sibling was not) does not make it one.
#   - a CLAIM is any reason that mentions EQUIVALENT and does not start with a NOT label,
#     so mixed phrasing ("Equivalent: ...; X is NOT EQUIVALENT") cannot skip the check.
# Measured at b2495952: refusing outright would refuse 7 of 280 existing claims, all the
# bare `sys.exit(0)` collision (a5b711fb) and genuine on reading. So the writer does not
# refuse; it demands --differs-from "<key>: <why>" and records it in the reason. It
# cannot catch novel code with no sibling, nor a NOT EQUIVALENT entry added after the
# EQUIVALENT one (it checks the new entry only).
_NOT_LABEL = re.compile(r"\s*(?:NOT\s+(?:AN?\s+)?EQUIVALENT|NON-EQUIVALENT)\b", re.IGNORECASE)
_EQ = re.compile(r"\bEQUIVALENT\b", re.IGNORECASE)


def is_not_equivalent_label(reason: str) -> bool:
    """True when the reason starts with a NOT EQUIVALENT label."""
    return bool(_NOT_LABEL.match(reason))


def claims_equivalent(reason: str) -> bool:
    """True when the reason mentions EQUIVALENT and is not labelled NOT EQUIVALENT."""
    return bool(_EQ.search(reason)) and not is_not_equivalent_label(reason)


# Occurrence 2+ of the same line, op and function carries "<hash>.<N>" (S5's per-site
# keys, 2026-11-25); occurrence 1 keeps the bare hash. The line shape is the bare hash.
_OCCURRENCE = re.compile(r"\.\d+$")


def _shape(key: str) -> tuple[str, str]:
    """(operator, line hash without any occurrence suffix)."""
    parts = key.split("::")
    return parts[2], _OCCURRENCE.sub("", parts[3])


def not_equivalent_precedents(data: dict, key: str) -> list[str]:
    """Other keys with the same operator and hash whose reason is labelled NOT EQUIVALENT."""
    shape = _shape(key)
    return sorted(k for k, v in data.items()
                  if k != key and len(k.split("::")) == KEY_PARTS and _shape(k) == shape
                  and is_not_equivalent_label(v))


# --- executed evidence (overnight-112526 S2, 2026-11-25) ------------------------------
# memory/feedback_verify_equivalent_mutant_by_execution.md: a mutant may be called
# EQUIVALENT only after the mutated behaviour was RUN and observed (5 fires; reasons written
# from reading the source were refuted by review on 2026-11-17, -18 and -23). The claim must
# carry what was run and what it returned, so a later reader can audit it rather than trust
# it: `EXECUTED: <command run> -> <observed result>`. Checked at add time only (org ruling,
# 2026-11-25); the 292 existing EQUIVALENT reasons are not rewritten or validated.
# The command is the text up to the FIRST arrow, the result is what follows it. Each field
# must hold one character that is not whitespace and not part of an arrow, so an arrow
# cannot stand in for either (`EXECUTED: -> -> x`, Codex S2 pre-commit F1), while an empty
# container is a real result (`-> []`, Grok S2 tip F1: a letter-or-digit rule refused it).
_EXECUTED = re.compile(r"\bEXECUTED:(.*?)->(.*)", re.S)
_CONTENT = re.compile(r"[^\s>-]")


def has_executed_evidence(reason: str) -> bool:
    """True when the reason carries `EXECUTED: <command> -> <observed result>`."""
    m = _EXECUTED.search(reason)
    return bool(m and _CONTENT.search(m.group(1)) and _CONTENT.search(m.group(2)))


def _checked_reason(data: dict, key: str, why: str, differs_from: str | None) -> str:
    why = why.strip()
    if claims_equivalent(why) and not has_executed_evidence(why):
        raise ValueError(
            f"{key}: no executed evidence for an EQUIVALENT claim. Run the mutated "
            f"behaviour, then put what you ran and what it returned in the reason: "
            f"'EXECUTED: <command run> -> <observed result>'. If you cannot produce that "
            f"in a minute, write a test instead of an entry "
            f"(memory/feedback_verify_equivalent_mutant_by_execution.md).")
    precedents = not_equivalent_precedents(data, key) if claims_equivalent(why) else []
    if differs_from is None:
        if precedents:
            shown = "; ".join(f"{k}: {data[k][:160]}" for k in precedents)
            raise ValueError(
                f"{key} is called EQUIVALENT, but the same operator and line hash is "
                f"recorded NOT EQUIVALENT at {shown}. Read that reason, then either fix the "
                f"verdict (start the reason with 'NOT EQUIVALENT:' if that is what you mean) "
                f"or pass --differs-from \"<precedent key>: <why this site differs>\".")
        return why
    if not precedents:
        raise ValueError("--differs-from given, but this EQUIVALENT reason has no NOT "
                         "EQUIVALENT precedent of the same shape; drop it")
    # A key contains '::', so match a known precedent key as the prefix.
    cited = next((k for k in precedents if differs_from.startswith(k + ":")), None)
    if cited is None:
        raise ValueError(f"--differs-from must start with one of {precedents} followed by "
                         f"': <why this site differs>'")
    because = differs_from[len(cited) + 1:].strip()
    if not because:
        raise ValueError("--differs-from needs a reason after the precedent key")
    return f"{why} DIFFERS FROM {cited}: {because}"


def add(repo_root: Path, key: str, why: str, force: bool = False,
        timeout: float = 30.0, differs_from: str | None = None) -> dict:
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
        reason = _checked_reason(data, key, why, differs_from)
        before = len(data)
        data[key] = reason
        _dump(repo_root, data)
    return {"key": key, "action": "replaced" if existing else "added",
            "entries_before": before, "entries_after": before + (0 if existing else 1)}


def remove(repo_root: Path, key: str, timeout: float = 30.0) -> dict:
    path = allow_path(repo_root)
    with inbox_lock.file_lock(path, timeout=timeout):
        data = load(repo_root)
        before = len(data)
        try:
            data.pop(key)
        except KeyError:
            raise KeyError(f"{key} is not in the allowlist") from None
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
    p_add.add_argument("--differs-from", default=None,
                       help="'<NOT EQUIVALENT precedent key>: <why this site differs>'; "
                            "required when an EQUIVALENT reason has such a precedent")

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
            out = add(root, args.key, args.why, force=args.force, timeout=args.timeout,
                      differs_from=args.differs_from)
        elif args.cmd == "remove":
            out = remove(root, args.key, timeout=args.timeout)
        elif args.cmd == "get":
            data = load(root)
            try:
                out = {"key": args.key, "why": data[args.key]}
            except KeyError:
                raise KeyError(f"{args.key} is not in the allowlist") from None
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
