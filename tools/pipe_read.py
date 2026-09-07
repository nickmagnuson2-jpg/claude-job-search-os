#!/usr/bin/env python3
"""
pipe_read.py — Pre-process job pipeline for /pipe show command.

Reads: data/job-pipeline.md

Per-entry output:
  days_since_update, stale (per-stage thresholds), missing_action (next_action is
  blank or "—"), needs_attention (stale OR missing_action), stale_label (pre-formatted)

Pipeline column layout (8 columns):
  Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL

Output JSON (stdout):
  {
    "active_entries": [...],   — all non-terminal entries
    "needs_attention": [...],  — entries where stale or missing_action
    "stage_distribution": {},
    "metrics": { "total_active", "total_stalled", "archived_count" },
    "company_index": { "company (lowercase)": ["Role 1", "Role 2"] },
    "target_date": "YYYY-MM-DD"
  }

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/pipe_read.py [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Shared freeform-stage classifier (single source of truth). See stage_vocab.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_vocab import is_terminal_stage, is_active_pursuit  # noqa: E402

# Per-stage staleness thresholds (calendar days)
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


def parse_args():
    p = argparse.ArgumentParser(
        description="Read job pipeline with staleness and attention flags. Read-only."
    )
    p.add_argument("--target-date", default=None,
                   help="Date to treat as today (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root. Defaults to cwd.")
    return p.parse_args()


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _is_missing_action(value: str) -> bool:
    """True when next_action is empty, dash, or em-dash."""
    stripped = value.strip()
    return stripped in ("", "—", "-", "–", "—")


def parse_all_rows(content: str, today: date) -> list[dict]:
    """Parse EVERY pipeline row, terminal ones included, with full 8-column data.

    parse_pipeline() intentionally drops terminal rows (it only counts them) because
    /pipe show is about live work. Consumers that need the whole table — the dashboard
    TUI and db_sync.py — used to each keep their own reduced parser, which is how the
    dashboard ended up carrying only 5 of the 8 columns. This is the single source of
    truth for "all rows"; classification still defers to stage_vocab.

    Adds `archived` (terminal stage OR sitting under an Archived/Withdrawn/Rejected
    heading) on top of the fields parse_pipeline emits.
    """
    rows: list[dict] = []
    in_archived = False

    for line in content.splitlines():
        if re.match(r"^##\s+", line):
            section = line.strip("# ").strip().lower()
            in_archived = any(t in section for t in ("archived", "withdrawn", "rejected"))
            continue

        if not line.startswith("|"):
            continue
        if re.match(r"\|\s*(Company|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0] or cols[0] == "---":
            continue

        def col(i: int) -> str:
            return cols[i] if len(cols) > i else ""

        company, role, stage = cols[0], col(1), col(2)
        date_updated, next_action = col(3), col(4)
        cv_used, notes, url = col(5), col(6), col(7)

        archived = is_terminal_stage(stage) or in_archived

        days_since_update = None
        if date_updated and date_updated != "\u2014":
            try:
                dt = datetime.strptime(date_updated, "%Y-%m-%d").date()
                days_since_update = (today - dt).days
            except ValueError:
                pass

        threshold = STAGE_THRESHOLDS.get(stage, DEFAULT_THRESHOLD)
        stale = (not archived
                 and is_active_pursuit(stage)
                 and days_since_update is not None
                 and days_since_update >= threshold)
        missing_action = (not archived) and _is_missing_action(next_action)

        rows.append({
            "company": company,
            "role": role,
            "stage": stage,
            "date_updated": date_updated,
            "next_action": next_action,
            "cv_used": cv_used,
            "notes": notes,
            "url": url,
            "days_since_update": days_since_update,
            "archived": archived,
            "stale": stale,
            "missing_action": missing_action,
            "needs_attention": stale or missing_action,
        })

    return rows


def parse_pipeline(content: str, today: date) -> dict:
    active_entries: list[dict] = []
    archived_count = 0
    in_archived = False

    for line in content.splitlines():
        # Detect section headers to know if we're in Archived
        if re.match(r"^##\s+", line):
            section = line.strip("# ").strip().lower()
            in_archived = any(t in section for t in ("archived", "withdrawn", "rejected"))
            continue

        if not line.startswith("|"):
            continue
        # Skip header and separator rows
        if re.match(r"\|\s*(Company|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0]:
            continue

        # Column mapping (8-column format):
        # 0=Company, 1=Role, 2=Stage, 3=Date Updated, 4=Next Action,
        # 5=CV Used, 6=Notes, 7=URL
        company      = cols[0]
        role         = cols[1] if len(cols) > 1 else ""
        stage        = cols[2] if len(cols) > 2 else ""
        date_updated = cols[3] if len(cols) > 3 else ""
        next_action  = cols[4] if len(cols) > 4 else ""
        cv_used      = cols[5] if len(cols) > 5 else ""
        notes        = cols[6] if len(cols) > 6 else ""
        url          = cols[7] if len(cols) > 7 else ""

        # Skip separator rows (e.g. "---")
        if company == "---":
            continue

        # Count archived/closed entries separately. Uses the shared freeform-stage
        # classifier, not a 4-value exact set — otherwise descriptive closed stages
        # ("Closed - they passed", "Declined", "Considered - passed") fall through
        # into active_entries and inflate total_active (fable-audit Theme 2: 29 such
        # rows were mis-counted as active on the live pipeline).
        if is_terminal_stage(stage) or in_archived:
            archived_count += 1
            continue

        # Compute staleness
        days_since_update = None
        if date_updated and date_updated != "—":
            try:
                dt = datetime.strptime(date_updated, "%Y-%m-%d").date()
                days_since_update = (today - dt).days
            except ValueError:
                pass

        threshold = STAGE_THRESHOLDS.get(stage, DEFAULT_THRESHOLD)
        stale = (is_active_pursuit(stage)
                 and days_since_update is not None
                 and days_since_update >= threshold)
        missing_action = _is_missing_action(next_action)
        needs_attention = stale or missing_action

        stale_label = ""
        if stale and days_since_update is not None:
            stale_label = f"⚠️ [{days_since_update} days stale]"

        entry = {
            "company":            company,
            "role":               role,
            "stage":              stage,
            "date_updated":       date_updated,
            "next_action":        next_action,
            "cv_used":            cv_used,
            "notes":              notes,
            "url":                url,
            "days_since_update":  days_since_update,
            "stale":              stale,
            "missing_action":     missing_action,
            "needs_attention":    needs_attention,
            "stale_label":        stale_label,
        }
        active_entries.append(entry)

    # Sort active entries by date_updated descending (most recent first)
    def _sort_key(e: dict):
        d = e["date_updated"]
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return date.min

    active_entries.sort(key=_sort_key, reverse=True)

    needs_attention = [e for e in active_entries if e["needs_attention"]]
    stage_distribution: dict[str, int] = {}
    for e in active_entries:
        stage_distribution[e["stage"]] = stage_distribution.get(e["stage"], 0) + 1

    # Company index (lowercase key → list of roles, for duplicate detection)
    company_index: dict[str, list[str]] = {}
    for e in active_entries:
        key = e["company"].lower()
        if key not in company_index:
            company_index[key] = []
        if e["role"] and e["role"] not in company_index[key]:
            company_index[key].append(e["role"])

    return {
        "active_entries":    active_entries,
        "needs_attention":   needs_attention,
        "stage_distribution": stage_distribution,
        "metrics": {
            "total_active":  len(active_entries),
            "total_stalled": sum(1 for e in active_entries if e["stale"]),
            "archived_count": archived_count,
        },
        "company_index": company_index,
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    content = read_file(repo_root / "data" / "job-pipeline.md")

    result = parse_pipeline(content, today)
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
