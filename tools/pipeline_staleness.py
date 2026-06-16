#!/usr/bin/env python3
"""
pipeline_staleness.py — Pre-process pipeline staleness for /standup and /weekly-review.

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
from datetime import date, datetime, timedelta
from pathlib import Path

# Staleness thresholds by stage (days)
STAGE_THRESHOLDS = {
    "Researching": 7,
    "Applied": 5,
    "Phone Screen": 5,
    "Screening": 5,
    "Interview": 7,
    "Interviewing": 7,
    "Offer": 3,
}
DEFAULT_THRESHOLD = 7

TERMINAL_STAGES = {"Withdrawn", "Rejected", "Accepted", "Archived"}

# Stages are freeform in this pipeline ("Strategic Thinking round complete (6/8)",
# "To Apply (warm-intro path)", etc.), so staleness is gated by keyword: only rows
# whose stage names an active *pursuit* are eligible to be flagged stalled. Backlog
# stages (To Evaluate, To Apply, Deprioritized, Watch) and closed variants match none
# of these and are therefore never nagged, though they still count in the distribution.
ACTIVE_PURSUIT_KEYWORDS = (
    "applied", "screen", "interview", "onsite", "offer",
    "researching", "recruiter", "phone", "round", "case", "loop", "final",
)


def _is_active_pursuit(stage: str) -> bool:
    """True if the (freeform) stage names a live pursuit eligible for staleness flagging."""
    s = stage.lower()
    return any(k in s for k in ACTIVE_PURSUIT_KEYWORDS)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days-threshold", type=int, default=None,
                   help="Override staleness threshold for all stages.")
    p.add_argument("--target-date", default=None,
                   help="Date to use as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    return p.parse_args()


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def parse_pipeline(content: str, today: date, global_threshold: int | None) -> dict:
    active_entries = []
    stalled_entries = []
    stage_distribution = {}

    # Look for rows in any section — exclude rows from Archived/Terminal sections
    in_terminal_section = False

    for line in content.splitlines():
        # Track which section we're in
        if re.match(r"^##\s+", line):
            section_name = line.strip("# ").strip()
            in_terminal_section = any(t.lower() in section_name.lower()
                                     for t in ("archived", "terminal", "withdrawn", "rejected"))
            continue

        if not line.startswith("|") or line.startswith("| Company") or line.startswith("|---"):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0]:
            continue

        company = cols[0]
        role = cols[1] if len(cols) > 1 else ""
        stage = cols[2] if len(cols) > 2 else ""
        # Canonical schema (tools/pipe_write.py): Date Updated | Next Action | CV Used | Notes | URL
        date_updated = cols[3] if len(cols) > 3 else ""
        next_action = cols[4] if len(cols) > 4 else ""
        cv_used = cols[5] if len(cols) > 5 else ""
        notes = cols[6] if len(cols) > 6 else ""
        url = cols[7] if len(cols) > 7 else ""
        date_added = date_updated  # no separate Date Added column; mirror for back-compat

        # Skip terminal entries
        if stage in TERMINAL_STAGES or in_terminal_section:
            continue

        # Calculate days since update
        days_since_update = None
        update_date_str = date_updated
        if update_date_str and update_date_str != "—":
            try:
                update_date = datetime.strptime(update_date_str, "%Y-%m-%d").date()
                days_since_update = (today - update_date).days
            except ValueError:
                pass

        # Update stage distribution
        stage_distribution[stage] = stage_distribution.get(stage, 0) + 1

        threshold = global_threshold if global_threshold is not None else STAGE_THRESHOLDS.get(stage, DEFAULT_THRESHOLD)

        entry = {
            "company": company,
            "role": role,
            "stage": stage,
            "date_added": date_added,
            "date_updated": date_updated or date_added,
            "days_since_update": days_since_update,
            "threshold": threshold,
            "stalled": _is_active_pursuit(stage) and days_since_update is not None and days_since_update >= threshold,
            "url": url,
            "notes": notes,
        }

        active_entries.append(entry)
        if entry["stalled"]:
            stalled_entries.append(entry)

    # Sort stalled by most overdue first
    stalled_entries.sort(key=lambda e: -(e["days_since_update"] or 0))

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

    result = parse_pipeline(content, today, args.days_threshold)
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
