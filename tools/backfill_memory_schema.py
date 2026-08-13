#!/usr/bin/env python3
"""backfill_memory_schema.py -- make the legacy memory corpus VISIBLE to the
promotion detector, without fabricating anything about it.

Context. `tools/scan_promotion_candidates.py` reads four frontmatter keys:
`occurrences`, `promoted`, `reopen_gate`, `last_cited`. On 2026-08-13 only 19 of
403 `feedback_*.md` files carried them, so the detector returned 1 candidate
corpus-wide and that read as an all-clear. Root cause (a CLAUDE.md/skill doc
contradiction) was fixed the same day; this script sweeps the files written
before the fix.

What it stamps, and what it deliberately does NOT:

  occurrences: 1        -- a floor, NOT a measurement. The true count is unknown.
  promoted: no          -- likewise unknown; `no` is the conservative default,
                           since a false `no` surfaces a candidate for review
                           while a false `yes` hides a live gate forever.
  needs_review: true    -- the key that keeps the two above honest. It marks
                           "unknown", distinguishing a backfilled 1 from a
                           counted 1. Never stamp a claimed-accurate count.
  reopen_gate: "<backfill marker>"  -- states its own provenance, no invented gate.

  last_cited            -- OMITTED ON PURPOSE. Seeding today would make all 384
                           files look freshly cited (zero demotion candidates for
                           60 days); seeding mtime would report "last modified" as
                           "last cited" and flood demotion with false staleness.
                           The `memory-last-cited-stamp` PostToolUse hook writes it
                           on the first genuine Read -- and that hook is itself gated
                           on `occurrences:` existing, so this backfill is what
                           switches the demotion signal on. Every value in the corpus
                           then means what it says.

Safety properties (all asserted, not asserted-about):
  * Dry run by default; `--apply` is required to write.
  * Refuses a zero-file scope (exit 2). An empty sweep is an error, never a clean pass.
  * Idempotent: a file already carrying `occurrences:` is skipped, never re-stamped.
  * Conservation anchor: every byte of the original file must survive verbatim in
    the result, with ONLY the new key lines added. Verified per file before the
    write; any file that fails is skipped and reported, not written.
  * Atomic per-file write (tmp + os.replace) in the same directory.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/backfill_memory_schema.py --memory-dir <path>
  PYTHONIOENCODING=utf-8 python3 tools/backfill_memory_schema.py --memory-dir <path> --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
METADATA_HEADER_RE = re.compile(r"^metadata:[ \t]*$", re.MULTILINE)
INDENTED_RE = re.compile(r"^[ \t]+\S")

BACKFILL_GATE = (
    "UNREVIEWED -- schema backfill {date}; true fire count and promotion target "
    "not yet derived (see output/analysis/081326-memory-hygiene-project.md Phase 2)"
)

SKIP_PREFIXES = (
    "MEMORY", "archive-", "archived-", "audit-", "promotion-backlog", "index-",
)


def is_rule_file(name: str) -> bool:
    return name.endswith(".md") and not any(name.startswith(p) for p in SKIP_PREFIXES)


def frontmatter_of(text: str) -> str | None:
    m = FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


def is_feedback(name: str, fm: str) -> bool:
    return name.startswith("feedback_") or re.search(r"^\s*type:\s*feedback\s*$", fm, re.M) is not None


def plan_lines(date_str: str) -> list[str]:
    return [
        "  occurrences: 1",
        "  promoted: no",
        f'  reopen_gate: "{BACKFILL_GATE.format(date=date_str)}"',
        "  needs_review: true",
    ]


def stamp(text: str, date_str: str) -> tuple[str, str, list[str]]:
    """Return (new_text, mode, added_lines). Raises ValueError if there is no frontmatter."""
    fm = frontmatter_of(text)
    if fm is None:
        raise ValueError("no frontmatter")

    fm_start = 4  # len("---\n")
    fm_end = fm_start + len(fm)  # index of the closing "---\n"
    new_keys = plan_lines(date_str)

    m = METADATA_HEADER_RE.search(fm)
    if m:
        # Insert after the last indented child of the metadata block, so the keys
        # land INSIDE it (a wrong indent writes them where the parser cannot see them).
        lines = fm.splitlines(keepends=True)
        # locate the metadata header line index
        offset = 0
        header_idx = None
        for i, line in enumerate(lines):
            if offset == m.start():
                header_idx = i
                break
            offset += len(line)
        if header_idx is None:
            raise ValueError("could not locate metadata header line")
        insert_at = header_idx + 1
        while insert_at < len(lines) and INDENTED_RE.match(lines[insert_at]):
            insert_at += 1
        added = [k + "\n" for k in new_keys]
        new_fm = "".join(lines[:insert_at]) + "".join(added) + "".join(lines[insert_at:])
        mode = "into-metadata"
    else:
        # No metadata block at all (51 legacy files): create one at the end of the
        # frontmatter. `node_type`/`type` are only added when the file states neither.
        head = ["metadata:"]
        if not re.search(r"^\s*node_type:", fm, re.M):
            head.append("  node_type: memory")
        if not re.search(r"^\s*type:", fm, re.M):
            head.append("  type: feedback")
        added = [k + "\n" for k in head + new_keys]
        new_fm = fm + "".join(added)
        mode = "new-metadata-block"

    return text[:fm_start] + new_fm + text[fm_end:], mode, added


def conservation_ok(original: str, new: str, added: list[str]) -> bool:
    """Every original byte survives, and the ONLY additions are the lines we planned.

    Deleting exactly the added lines from the result must reproduce the source byte
    for byte. This is the anchor that makes "we only inserted keys" a checked claim
    rather than a described one.
    """
    stripped = new
    for line in added:
        idx = stripped.find(line)
        if idx == -1:
            return False
        stripped = stripped[:idx] + stripped[idx + len(line):]
    return stripped == original and len(new) == len(original) + sum(len(a) for a in added)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--apply", action="store_true", help="write; default is dry run")
    ap.add_argument("--date", default=None, help="provenance date stamped into reopen_gate (default: today)")
    ap.add_argument("--limit", type=int, default=0, help="stamp at most N files (0 = no limit)")
    args = ap.parse_args(argv)

    if args.date:
        date_str = args.date
    else:
        from datetime import date as _date
        date_str = _date.today().isoformat()

    memory_dir = Path(args.memory_dir).expanduser().resolve()
    if not memory_dir.is_dir():
        sys.stdout.write(json.dumps({"status": "error", "reason": f"not a directory: {memory_dir}"}) + "\n")
        return 2

    paths = [p for p in sorted(memory_dir.glob("*.md")) if is_rule_file(p.name)]
    if not paths:
        sys.stdout.write(json.dumps({
            "status": "error",
            "reason": f"ZERO rule files under {memory_dir} -- an empty sweep is an error, not a clean pass",
        }) + "\n")
        return 2

    scanned = 0
    already = 0
    not_feedback = 0
    no_frontmatter: list[str] = []
    failed: list[str] = []
    changed: list[dict] = []

    for path in paths:
        scanned += 1
        text = path.read_text(encoding="utf-8")
        fm = frontmatter_of(text)
        if fm is None:
            no_frontmatter.append(path.name)
            continue
        if re.search(r"^\s*occurrences:", fm, re.M):
            already += 1
            continue
        if not is_feedback(path.name, fm):
            not_feedback += 1
            continue
        if args.limit and len(changed) >= args.limit:
            continue
        try:
            new_text, mode, added = stamp(text, date_str)
        except ValueError as exc:
            failed.append(f"{path.name}: {exc}")
            continue
        if not conservation_ok(text, new_text, added):
            failed.append(f"{path.name}: conservation check failed -- not written")
            continue
        changed.append({"file": path.name, "mode": mode})
        if args.apply:
            fd, tmp = tempfile.mkstemp(dir=str(memory_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    report = {
        "status": "ok" if not failed else "partial",
        "applied": bool(args.apply),
        "memory_dir": str(memory_dir),
        "scanned": scanned,
        "stamped": len(changed),
        "already_visible": already,
        "skipped_non_feedback": not_feedback,
        "skipped_no_frontmatter": no_frontmatter,
        "failed": failed,
        "modes": {
            "into-metadata": sum(1 for c in changed if c["mode"] == "into-metadata"),
            "new-metadata-block": sum(1 for c in changed if c["mode"] == "new-metadata-block"),
        },
        "note": ("occurrences=1 is a floor, not a count; needs_review=true marks it unknown. "
                 "last_cited is deliberately omitted -- the PostToolUse hook stamps it on first "
                 "genuine Read, and this backfill is what un-gates that hook."),
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
