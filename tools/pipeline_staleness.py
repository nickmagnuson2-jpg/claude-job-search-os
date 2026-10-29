#!/usr/bin/env python3
"""
pipeline_staleness.py — Pre-process pipeline staleness for /standup.

Reads: data/job-pipeline.md

Outputs JSON to stdout with keys:
  active_entries[]      — all non-terminal pipeline entries with days_since_update
  stalled_entries[]     — entries that exceed their stage staleness threshold
  stage_distribution{}  — count of entries per stage
  metrics{}             — total_active, total_stalled, last_updated

Usage:
  python3 tools/pipeline_staleness.py [--days-threshold N] [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_guard import assert_schema, find_header_line, SchemaDriftError  # noqa: E402
# Shared freeform-stage classifier (single source of truth). See stage_vocab.py.
from stage_vocab import is_active_pursuit  # noqa: E402

# Canonical schema (tools/pipe_write.py PIPELINE_HEADER) — the parser's cols[N]
# indices below assume this exact column order. See schema_guard.py docstring for
# the 2026-06-08 incident this guards against.
PIPELINE_EXPECTED_COLUMNS = [
    "Company", "Role", "Stage", "Date Updated",
    "Next Action", "CV Used", "Notes", "URL",
]

# Staleness thresholds by stage (days): one table shared with /pipe, so /standup
# and /pipe cannot disagree about what is stale.
from pipe_read import STAGE_THRESHOLDS, DEFAULT_THRESHOLD, parse_all_rows  # noqa: E402
from md_table import read_file_narrow as read_file  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days-threshold", type=int, default=None,
                   help="Override staleness threshold for all stages.")
    p.add_argument("--target-date", default=None,
                   help="Date to use as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    return p.parse_args()


def _without_terminal_sections(content: str) -> str:
    """content minus the table rows under a "## ... Terminal ..." heading.

    pipe_read already drops rows under Archived/Withdrawn/Rejected headings; this
    tool has also treated a Terminal heading as closed, so that rule stays here."""
    kept, in_terminal = [], False
    for line in content.splitlines():
        if re.match(r"^##\s+", line):
            in_terminal = "terminal" in line.lower()
        if not (in_terminal and line.startswith("|")):
            kept.append(line)
    return "\n".join(kept)


def parse_pipeline(content: str, today: date, global_threshold: int | None) -> dict:
    # Fail loudly (not silently) if the live file's header has drifted from what
    # the column order assumes — see schema_guard.py.
    assert_schema(find_header_line(content, "| Company |"), PIPELINE_EXPECTED_COLUMNS)

    active_entries = []
    stage_distribution = {}
    # Rows, the closed rule (stage_vocab) and the closed headings come from pipe_read,
    # the same reader /pipe uses, so /standup and /pipe count the same live rows.
    for row in parse_all_rows(_without_terminal_sections(content), today):
        if row["archived"]:
            continue
        stage, days_since_update = row["stage"], row["days_since_update"]
        stage_distribution[stage] = stage_distribution.get(stage, 0) + 1
        threshold = (global_threshold if global_threshold is not None
                     else STAGE_THRESHOLDS.get(stage, DEFAULT_THRESHOLD))
        active_entries.append({
            "company": row["company"],
            "role": row["role"],
            "stage": stage,
            "date_added": row["date_updated"],  # no separate Date Added column; mirrored for back-compat
            "date_updated": row["date_updated"],
            "days_since_update": days_since_update,
            "threshold": threshold,
            "stalled": (is_active_pursuit(stage) and days_since_update is not None
                        and days_since_update >= threshold),
            "url": row["url"],
            "notes": row["notes"],
        })

    # Sort stalled by most overdue first
    stalled_entries = sorted((e for e in active_entries if e["stalled"]),
                             key=lambda e: -(e["days_since_update"] or 0))

    return {
        "active_entries": active_entries,
        "stalled_entries": stalled_entries,
        "stage_distribution": stage_distribution,
        "metrics": {
            "total_active": len(active_entries),
            "total_stalled": len(stalled_entries),
        },
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    content = read_file(repo_root / "data" / "job-pipeline.md")

    try:
        result = parse_pipeline(content, today, args.days_threshold)
    except SchemaDriftError as e:
        print(json.dumps({"status": "error", "code": "schema_drift", "message": str(e)},
                          indent=2, ensure_ascii=False))
        sys.exit(1)

    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
