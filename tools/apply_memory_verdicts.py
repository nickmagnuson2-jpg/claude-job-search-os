#!/usr/bin/env python3
"""apply_memory_verdicts.py — apply VERIFIED mining verdicts to memory frontmatter.

Companion to `backfill_memory_schema.py`. The backfill stamped placeholders
(`occurrences: 1`, `promoted: no`, `needs_review: true`) that mean "unknown". This
script replaces a placeholder with a value that was actually established, and clears
`needs_review` only for the files where it was.

WHY A SCRIPT. The Phase 2 mining pass produced ~20 frontmatter corrections across a
corpus of 400+ files. Hand-editing them is the shape that produced a miscount during
the merge (free text retyped by hand). Verdicts arrive as data; the mutation is
mechanical and atomic.

VERDICT FILE FORMAT — one record per line, tab- or multi-space separated:

    <filename>\t<key>=<value>[\t<key>=<value>...]

    feedback_example.md    occurrences=3   promoted="yes -- hook, 2026-05-14"
    feedback_other.md      needs_review=false

Only these keys may be set: occurrences, promoted, needs_review, reopen_gate.
Anything else is rejected -- this script edits the schema block, never prose.

SAFETY (same contract as the backfill):
  * Dry run by default; `--apply` required to write.
  * Refuses an empty verdict file (exit 2). An empty change-set is an error, not a no-op.
  * Refuses a filename that does not exist, or a key already holding the target value
    (reported as `unchanged`, never silently counted as applied).
  * Conservation: the ONLY permitted diff is the value of an existing key on its own
    line. Line count must not change. Verified per file before the write.
  * Atomic per-file write (tmp + os.replace).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/apply_memory_verdicts.py --memory-dir <path> --verdicts <file>
  PYTHONIOENCODING=utf-8 python3 tools/apply_memory_verdicts.py --memory-dir <path> --verdicts <file> --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ALLOWED_KEYS = {"occurrences", "promoted", "needs_review", "reopen_gate"}
FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
KV_LINE = "  {key}: {value}\n"


def parse_verdicts(text: str) -> tuple[list[tuple[str, dict]], list[str]]:
    records, errors = [], []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+|\s{2,}", line)
        parts = [p for p in parts if p]
        if len(parts) < 2:
            errors.append(f"line {lineno}: no key=value pairs: {raw!r}")
            continue
        fname, pairs = parts[0], parts[1:]
        kv = {}
        for p in pairs:
            if "=" not in p:
                errors.append(f"line {lineno}: not a key=value: {p!r}")
                continue
            k, v = p.split("=", 1)
            k = k.strip()
            if k not in ALLOWED_KEYS:
                errors.append(f"line {lineno}: key {k!r} not permitted (allowed: {sorted(ALLOWED_KEYS)})")
                continue
            kv[k] = v.strip()
        if kv:
            records.append((fname, kv))
    return records, errors


def set_key(text: str, key: str, value: str) -> tuple[str, str]:
    """Replace the value of an existing frontmatter key. Returns (new_text, status)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text, "no-frontmatter"
    fm = m.group(1)
    pat = re.compile(rf"^(\s*){re.escape(key)}:[ \t]*(.*)$", re.M)
    found = pat.search(fm)
    if not found:
        return text, "key-absent"
    if found.group(2).strip() == value:
        return text, "unchanged"
    new_fm = fm[:found.start()] + f"{found.group(1)}{key}: {value}" + fm[found.end():]
    return text[:4] + new_fm + text[4 + len(fm):], "set"


def conservation_ok(original: str, new: str) -> bool:
    """Only values changed: same line count, and every differing line is a schema key."""
    o, n = original.splitlines(), new.splitlines()
    if len(o) != len(n):
        return False
    for a, b in zip(o, n):
        if a == b:
            continue
        key = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", b)
        if not key or key.group(1) not in ALLOWED_KEYS:
            return False
    return True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    memory_dir = Path(args.memory_dir).expanduser().resolve()
    vpath = Path(args.verdicts).expanduser()
    if not memory_dir.is_dir():
        print(json.dumps({"status": "error", "reason": f"not a directory: {memory_dir}"}, indent=2))
        return 2
    if not vpath.is_file():
        print(json.dumps({"status": "error", "reason": f"no verdict file: {vpath}"}, indent=2))
        return 2

    records, errors = parse_verdicts(vpath.read_text(encoding="utf-8"))
    if not records:
        print(json.dumps({
            "status": "error",
            "reason": "ZERO verdict records -- an empty change-set is an error, not a no-op",
            "parse_errors": errors,
        }, indent=2))
        return 2

    applied, unchanged, failed = [], [], list(errors)
    for fname, kv in records:
        path = memory_dir / fname
        if not path.is_file():
            failed.append(f"{fname}: no such file")
            continue
        original = path.read_text(encoding="utf-8")
        text = original
        changes = {}
        for key, value in kv.items():
            text, status = set_key(text, key, value)
            if status == "set":
                changes[key] = value
            elif status != "unchanged":
                failed.append(f"{fname}: {key}: {status}")
        if not changes:
            unchanged.append(fname)
            continue
        if not conservation_ok(original, text):
            failed.append(f"{fname}: conservation check failed -- not written")
            continue
        applied.append({"file": fname, "changes": changes})
        if args.apply:
            fd, tmp = tempfile.mkstemp(dir=str(memory_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    print(json.dumps({
        "status": "ok" if not failed else "partial",
        "applied_mode": bool(args.apply),
        "records": len(records),
        "changed": len(applied),
        "unchanged": len(unchanged),
        "failed": failed,
        "detail": applied,
    }, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
