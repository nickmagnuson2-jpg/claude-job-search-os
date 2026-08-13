#!/usr/bin/env python3
"""merge_mining_verdicts.py — turn per-agent mining records into one verdict file.

WHY THIS EXISTS. The 2026-08-13 mining run produced 230 verified records across 20
agents. Hand-transcribing those into frontmatter edits is the step that already
produced one miscount in this project (flaw #7 of the Phase 2 post-mortem: "free-text
output meant hand transcription"). This script closes that gap: records in, verdict
file out, no retyping.

INPUT — the per-agent TSVs written as each agent returned. One record per line:

    <filename>\t<corrected_occurrences>\t<disposition;disposition>\t<enforcement note>

Dispositions are NOT mutually exclusive (that was flaw #5): a file can be both
`count-correction` and `already-landed`.

OUTPUT — a verdict file for `tools/apply_memory_verdicts.py`, which is the only thing
permitted to touch the corpus. This script never writes to the memory dir.

MAPPING RULES (each one exists because a wrong default here would corrupt the corpus):

  occurrences   Written whenever the record's corrected value differs from what the
                file currently carries. A value of 0 is written as 1, not 0: `0` means
                "the origin was a success, not a fire", but the schema treats
                occurrences as a count with a floor of 1, and 0 would read as "never
                happened" rather than "never failed". The distinction is preserved in
                needs_review instead.

  promoted      `already-landed` -> "yes -- <enforcement note>". If the record ALSO
                carries `genuinely-open`, the rule is only half-enforced, so it is
                written as "partial -- ..." instead. Marking a half-landed rule `yes`
                would hide a real gap -- the ghost failure mode in reverse.

  needs_review  Cleared to false for every verified record: a human-directed agent
                read the file and checked the repo, so the value is no longer unknown.
                EXCEPT where the record's occurrences is 0 (an origin that was a
                success) -- those stay flagged, because the right long-term fix is
                re-typing the file out of the feedback corpus, not a count edit.

  reopen_gate   NEVER written. Gates are prose judgments about what would trip a
                promotion; a script cannot compose one, and overwriting a real gate
                with a generated string would destroy the most valuable field in the
                schema. Files still carrying the backfill placeholder keep it.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/merge_mining_verdicts.py --records-dir <dir>
  ... --out verdicts.tsv        write the verdict file
  ... --memory-dir <dir>        compare against current frontmatter, skip no-ops
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
VALID_DISPOSITIONS = {
    "count-correction", "already-landed", "terminal-behavioral", "genuinely-open",
}


def read_records(records_dir: Path) -> list[dict]:
    records, errors = [], []
    files = sorted(records_dir.glob("work*.tsv"))
    if not files:
        raise SystemExit(json.dumps({
            "status": "error",
            "reason": f"ZERO work*.tsv files in {records_dir} -- an empty merge is an "
                      "error, not an empty verdict set",
        }, indent=2))
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                errors.append(f"{path.name}:{lineno}: fewer than 3 fields")
                continue
            fname, occ, disp = parts[0].strip(), parts[1].strip(), parts[2].strip()
            note = parts[3].strip() if len(parts) > 3 else "-"
            dispositions = [d.strip() for d in disp.split(";") if d.strip()]
            unknown = set(dispositions) - VALID_DISPOSITIONS
            if unknown:
                errors.append(f"{path.name}:{lineno}: unknown disposition(s) {sorted(unknown)}")
                continue
            if not occ.isdigit():
                errors.append(f"{path.name}:{lineno}: occurrences {occ!r} not an integer")
                continue
            records.append({
                "file": fname, "occurrences": int(occ),
                "dispositions": dispositions, "note": note, "source": path.name,
            })
    if errors:
        raise SystemExit(json.dumps({"status": "error", "parse_errors": errors}, indent=2))
    return records


def current_frontmatter(memory_dir: Path, fname: str) -> dict:
    path = memory_dir / fname
    if not path.is_file():
        return {}
    m = FRONTMATTER_RE.match(path.read_text(encoding="utf-8", errors="ignore"))
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if kv:
            out[kv.group(1)] = kv.group(2).strip()
    return out


def quote(value: str) -> str:
    """Frontmatter-safe double-quoted scalar."""
    return '"' + value.replace('"', "'") + '"'


def build_verdicts(records: list[dict], memory_dir: Path | None) -> tuple[list[str], dict]:
    lines, stats = [], {
        "records": len(records), "emitted": 0, "no_op": 0, "missing_file": 0,
        "occurrences_changed": 0, "promoted_yes": 0, "promoted_partial": 0,
        "needs_review_cleared": 0, "zero_fire_flagged": 0,
    }
    for r in sorted(records, key=lambda x: x["file"]):
        fm = current_frontmatter(memory_dir, r["file"]) if memory_dir else {}
        if memory_dir and not fm:
            stats["missing_file"] += 1
            lines.append(f"# SKIPPED (no frontmatter / file absent): {r['file']}")
            continue

        pairs = []
        # occurrences: floor of 1 -- see module docstring on why 0 is not written
        target_occ = max(1, r["occurrences"])
        if str(fm.get("occurrences", "")).strip() != str(target_occ):
            pairs.append(f"occurrences={target_occ}")
            stats["occurrences_changed"] += 1

        if "already-landed" in r["dispositions"]:
            half = "genuinely-open" in r["dispositions"]
            note = r["note"] if r["note"] not in ("-", "") else "verified in repo"
            value = ("partial -- " if half else "yes -- ") + note
            if fm.get("promoted", "").strip('"') != value:
                pairs.append(f"promoted={quote(value)}")
                stats["promoted_partial" if half else "promoted_yes"] += 1

        # A zero-fire record means the origin was a success. Keep it flagged: the fix
        # is re-typing the file out of the feedback corpus, not a count edit.
        if r["occurrences"] == 0:
            stats["zero_fire_flagged"] += 1
        elif "needs_review" not in fm:
            # The 20 files that already carried the schema before the Phase 1 backfill
            # never got a needs_review key -- they were never marked unknown, so there
            # is nothing to clear. Emitting it anyway fails with key-absent (the applier
            # refuses to invent keys, correctly).
            stats["no_needs_review_key"] = stats.get("no_needs_review_key", 0) + 1
        elif fm.get("needs_review", "").lower() != "false":
            pairs.append("needs_review=false")
            stats["needs_review_cleared"] += 1

        if pairs:
            lines.append(r["file"] + "\t" + "\t".join(pairs))
            stats["emitted"] += 1
        else:
            stats["no_op"] += 1
    return lines, stats


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--records-dir", required=True)
    ap.add_argument("--memory-dir", default=None,
                    help="compare against live frontmatter and skip no-ops")
    ap.add_argument("--out", default=None, help="write the verdict file here")
    args = ap.parse_args(argv)

    records = read_records(Path(args.records_dir).expanduser())
    memory_dir = Path(args.memory_dir).expanduser().resolve() if args.memory_dir else None
    if memory_dir and not memory_dir.is_dir():
        raise SystemExit(json.dumps({"status": "error", "reason": f"no memory dir: {memory_dir}"}))

    lines, stats = build_verdicts(records, memory_dir)
    header = [
        "# Generated by tools/merge_mining_verdicts.py -- do not hand-edit.",
        "# Every line is backed by an agent record that verified the file against the repo.",
        "# reopen_gate is deliberately never written: a script cannot compose a promotion gate.",
        "",
    ]
    body = "\n".join(header + lines) + "\n"
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        stats["written_to"] = args.out
    else:
        sys.stdout.write(body)

    sys.stderr.write(json.dumps({"status": "ok", **stats}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
