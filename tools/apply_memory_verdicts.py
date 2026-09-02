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

Only these keys may be set: occurrences, promoted, needs_review, reopen_gate,
terminal, terminal_reason. Anything else is rejected -- this script edits the schema
block, never prose.

Of those, only `terminal` and `terminal_reason` may be CREATED when absent (see
INSERTABLE_KEYS). Every other key must already exist; a missing one is reported as
`key-absent` rather than filled in, because a file lacking `occurrences` is a schema
defect to surface, not a hole to quietly patch.

SAFETY (same contract as the backfill):
  * Dry run by default; `--apply` required to write.
  * Refuses an empty verdict file (exit 2). An empty change-set is an error, not a no-op.
  * Refuses a filename that does not exist, or a key already holding the target value
    (reported as `unchanged`, never silently counted as applied).
  * Conservation: the ONLY permitted diff is a changed value on an existing schema key
    line, or a newly inserted insertable-key line. Nothing is ever deleted and no prose
    line may change -- checked structurally with a line diff, not a line count, so an
    insertion cannot mask a deletion elsewhere. Verified per file before the write.
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

import yaml
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ALLOWED_KEYS = {"occurrences", "promoted", "needs_review", "reopen_gate",
                "terminal", "terminal_reason", "description",
                # The exit record (2026-08-25). A promotion that leaves no dated record is
                # not an observable event, so the drain rate cannot be measured: only 37 of
                # 149 promoted files carried a date anywhere. Enforced by
                # tools/promotion_schema.py, which BLOCKS a promoted rule missing them.
                "promoted_date", "exit_path", "detector_signature", "detector_control"}

# Keys whose value is free prose and must therefore be emitted as an explicitly
# quoted YAML scalar, then re-parsed with a STRICT reader before the write is
# allowed. `description` is top-level (not under `metadata:`) and routinely contains
# a colon, a `#`, or a quote -- all of which the in-house regex parser reads happily
# and yaml.safe_load rejects. Verifying with the convenient reader instead of the
# strictest one is the 2026-08-20 failure; this is the guard against repeating it.
QUOTED_KEYS = {"description", "detector_signature", "detector_control"}

# Keys that may be CREATED when absent, rather than only overwritten. Deliberately
# narrow: `occurrences` missing from a file is a schema defect worth surfacing as
# `key-absent`, but the 41 self-declared terminal-behavioral files legitimately have
# no `terminal:` line yet -- the key was introduced after they were written. Anything
# not listed here still fails loudly, so a typo'd key name can never append a new line.
INSERTABLE_KEYS = {"terminal", "terminal_reason",
                   # These three are new as of 2026-08-25, so EVERY pre-existing file
                   # legitimately lacks them -- the same situation `terminal` was in.
                   "promoted_date", "exit_path", "detector_signature",
                   "detector_control"}

# Where a newly created key is placed, in preference order per key. This yields
# promoted -> terminal -> terminal_reason, matching the 17 files that already carry
# the convention. terminal_reason must prefer `terminal` or the two land inverted.
INSERT_AFTER = {
    "terminal": ("promoted", "occurrences", "type"),
    "terminal_reason": ("terminal", "promoted", "occurrences", "type"),
    "promoted_date": ("promoted", "occurrences", "type"),
    "exit_path": ("promoted_date", "promoted", "occurrences", "type"),
    "detector_signature": ("exit_path", "promoted_date", "promoted", "occurrences", "type"),
    "detector_control": ("detector_signature", "exit_path", "promoted_date", "promoted", "occurrences", "type"),
}
FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)


def _yaml_scalar(value: str) -> str:
    """Emit `value` as a single-line double-quoted YAML scalar."""
    return yaml.safe_dump(value, default_style='"', width=10**9,
                          allow_unicode=True).rstrip("\n")


def strict_roundtrip_ok(text: str, key: str, value: str) -> bool:
    """Re-read the whole frontmatter with the strictest reader and confirm the value.

    Not "does it parse" -- does it parse AND give back exactly what was intended.
    A value that parses into something else is a silent corruption, which is worse
    than a crash.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return False
    try:
        loaded = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return False
    if not isinstance(loaded, dict):
        return False
    # The key may be top-level (`description`) or nested under `metadata:` (everything in
    # the schema block). A top-level-only lookup silently returns None for every nested
    # key, so the check refused EVERY write of detector_signature / detector_control while
    # reporting a round-trip failure -- failing safe, but blocking legitimate writes and
    # blaming the value rather than the lookup. Found 2026-08-25 writing 50 detectors.
    if key in loaded:
        return loaded[key] == value
    meta = loaded.get("metadata")
    if isinstance(meta, dict) and key in meta:
        return meta[key] == value
    return False
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
    if key in QUOTED_KEYS:
        if "\n" in value or "\r" in value:
            return text, "multiline-refused"
        value = _yaml_scalar(value)
    pat = re.compile(rf"^(\s*){re.escape(key)}:[ \t]*(.*)$", re.M)
    found = pat.search(fm)
    if not found:
        if key not in INSERTABLE_KEYS:
            return text, "key-absent"
        anchor = None
        for anchor_key in INSERT_AFTER[key]:
            a = re.compile(rf"^(\s*){re.escape(anchor_key)}:[ \t]*.*$", re.M).search(fm)
            if a:
                anchor = a
                break
        if anchor is None:
            return text, "no-anchor"
        indent = anchor.group(1)
        new_fm = fm[:anchor.end()] + f"\n{indent}{key}: {value}" + fm[anchor.end():]
        return text[:4] + new_fm + text[4 + len(fm):], "set"
    if found.group(2).strip() == value:
        return text, "unchanged"
    new_fm = fm[:found.start()] + f"{found.group(1)}{key}: {value}" + fm[found.end():]
    return text[:4] + new_fm + text[4 + len(fm):], "set"


def _schema_key(line: str) -> str | None:
    m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", line)
    return m.group(1) if m else None


def conservation_ok(original: str, new: str) -> bool:
    """The only permitted diff is a changed schema VALUE or an inserted insertable key.

    Nothing may ever be deleted, and no prose line may change. Line count may grow
    only by the inserted keys -- checked structurally via a diff rather than by a
    count, so an insertion cannot mask a simultaneous prose deletion elsewhere.
    """
    o, n = original.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, o, n, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            return False
        if tag == "insert":
            if not all(_schema_key(b) in INSERTABLE_KEYS for b in n[j1:j2]):
                return False
            continue
        # `replace`: a value edit in place, possibly with an insertion merged in by
        # SequenceMatcher when the new line abuts a changed one. Spans may therefore
        # differ. Verify by KEY rather than by count: every line on both sides must be
        # an allowed schema key, nothing may disappear, and any surplus must be
        # insertable. This is what stops a prose line being consumed by the span.
        old_keys = [_schema_key(a) for a in o[i1:i2]]
        new_keys = [_schema_key(b) for b in n[j1:j2]]
        if not all(k in ALLOWED_KEYS for k in old_keys + new_keys):
            return False
        if Counter(old_keys) - Counter(new_keys):
            return False  # a schema line vanished
        if any(k not in INSERTABLE_KEYS
               for k in (Counter(new_keys) - Counter(old_keys)).elements()):
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
        bad_roundtrip = [k for k in changes if k in QUOTED_KEYS
                         and not strict_roundtrip_ok(text, k, kv[k])]
        if bad_roundtrip:
            failed.append(f"{fname}: strict YAML round-trip failed for "
                          f"{','.join(sorted(bad_roundtrip))} -- not written")
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
