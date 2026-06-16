#!/usr/bin/env python3
"""
todos_summary.py — Pre-process job-todos.md for /standup and /weekly-review.

Reads: data/job-todos.md, data/workbooks/_morning-starter-YYYY-MM-DD.md

Sorts pending todos by (overdue → priority → due-date-asc) and returns the top-N.
Also surfaces a morning-starter file for the target date if present.

Built 2026-05-13 after /standup confidently truncated job-todos.md at line 120
and missed three High-priority same-day todos (lines 123-125) that defined the
day's actual chain.

Outputs JSON to stdout with keys:
  top_n[]               — ranked top todos (default N=5)
  upcoming_scheduled[]  — interviews/screens/calls parsed from the pipeline
                          Next-Action column, within the next 3 days; pin atop
                          Today's Top 3 (they live in free text, not dated todos)
  overdue_count         — pending todos past their due date
  high_priority_count   — pending todos with priority High
  total_pending         — total pending todos
  morning_starter       — { path, exists, content_preview } for target date
  target_date

Usage:
  python3 tools/todos_summary.py [--top-n N] [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Reuse the scheduled-event parser from the sibling metrics script so /standup
# and /checkout surface imminent interviews identically (single source of truth
# for the date-parsing logic — avoids the schema-drift class of bug). Both
# scripts live in tools/; ensure that dir is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from todo_daily_metrics import parse_upcoming_scheduled  # noqa: E402


PRIORITY_RANK = {"high": 0, "med": 1, "medium": 1, "low": 2}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--target-date", default=None)
    p.add_argument("--repo-root", default=None)
    return p.parse_args()


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_todos(content: str) -> list[dict]:
    """Parse the | Task | Priority | Due | Status | Notes | table rows."""
    todos = []
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        # Skip header + separator rows
        if re.match(r"^\|\s*Task\s*\|", line) or re.match(r"^\|\s*-+\s*\|", line):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 4:
            continue
        task, priority, due, status = cols[0], cols[1], cols[2], cols[3]
        notes = cols[4] if len(cols) > 4 else ""
        if not task or task == "---":
            continue
        todos.append({
            "task": task,
            "priority": priority,
            "priority_rank": PRIORITY_RANK.get(priority.lower().strip(), 3),
            "due_raw": due,
            "due_date": parse_date(due),
            "status": status,
            "notes": notes,
        })
    return todos


def rank_todos(todos: list[dict], today: date) -> list[dict]:
    """Sort by: overdue first, then priority, then due-date asc, then task order."""
    pending = [t for t in todos if t["status"].lower() == "pending"]
    for t in pending:
        d = t["due_date"]
        t["is_overdue"] = bool(d and d < today)
        t["is_due_today"] = bool(d and d == today)
        t["days_until_due"] = (d - today).days if d else None

    def sort_key(t):
        # Primary: priority (High wins).
        # Secondary: time-relevance — due-today is 0, tomorrow is 1, yesterday is 1,
        # so today wins over both adjacent days; older overdue ranks below newer.
        # Tertiary: prefer overdue over future at same distance (sign tiebreak).
        # No-due-date items sink to the bottom of their priority bucket.
        d = t["days_until_due"]
        if d is None:
            time_dist = 10_000
            sign_tiebreak = 1
        else:
            time_dist = abs(d)
            sign_tiebreak = 0 if d <= 0 else 1
        return (t["priority_rank"], time_dist, sign_tiebreak)

    return sorted(pending, key=sort_key)


def main():
    args = parse_args()
    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    todos_path = root / "data" / "job-todos.md"
    content = read_file(todos_path)
    todos = parse_todos(content)
    ranked = rank_todos(todos, today)

    # Imminent scheduled events (interviews/screens/calls) live in the pipeline
    # Next-Action free text, not in dated todos — parse them so standup can pin
    # them atop Today's Top 3. Origin 2026-06-11 scheduled-interview miss.
    pipeline_content = read_file(root / "data" / "job-pipeline.md")
    upcoming_scheduled = parse_upcoming_scheduled(pipeline_content, today)

    overdue = [t for t in ranked if t["is_overdue"]]
    high = [t for t in ranked if t["priority"].lower() == "high"]

    top_n = []
    for t in ranked[: args.top_n]:
        top_n.append({
            "task": t["task"][:300],
            "priority": t["priority"],
            "due": t["due_raw"],
            "is_overdue": t["is_overdue"],
            "is_due_today": t["is_due_today"],
            "days_until_due": t["days_until_due"],
            "notes": t["notes"][:200],
        })

    starter_path = (root / "data" / "workbooks"
                    / f"_morning-starter-{today.isoformat()}.md")
    starter_exists = starter_path.exists()
    starter_preview = ""
    if starter_exists:
        try:
            starter_preview = starter_path.read_text(encoding="utf-8")[:2000]
        except (FileNotFoundError, PermissionError):
            pass

    out = {
        "top_n": top_n,
        "upcoming_scheduled": upcoming_scheduled,
        "overdue_count": len(overdue),
        "high_priority_count": len(high),
        "total_pending": len(ranked),
        "morning_starter": {
            "path": str(starter_path.relative_to(root)) if starter_path.is_relative_to(root) else str(starter_path),
            "exists": starter_exists,
            "content_preview": starter_preview,
        },
        "target_date": today.isoformat(),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
