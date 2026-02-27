#!/usr/bin/env python3
"""
todo_daily_metrics.py — Pre-process daily job search metrics for /checkout and /weekly-review.

Reads: data/job-todos.md, data/job-todos-daily-log.md, data/job-pipeline.md,
       data/networking.md, data/outreach-log.md, docs/CHANGELOG.md, output/**/*.md

Outputs JSON to stdout with keys:
  completed_today[]      — tasks completed today
  active_remaining[]     — tasks still active
  overdue[]              — tasks past due date
  outreach_sent_today[]  — outreach sent today
  research_completed_today[] — dossiers updated today
  changelog_today[]      — changelog entries for today
  metrics{}              — streak, this_week, last_7, last_30, all_time, avg_per_day, overdue_trend
  pipeline_snapshot[]    — active pipeline entries
  networking_activity[]  — contacts with pending follow-up

Usage:
  python tools/todo_daily_metrics.py [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-date", default=None,
                   help="Date to evaluate as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    return p.parse_args()


def read_file(path: Path) -> str:
    """Read file contents or return empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def parse_todos(content: str, today: date) -> tuple[list, list, list]:
    """Parse job-todos.md into completed_today, active, overdue lists."""
    completed_today = []
    active = []
    overdue = []

    today_str = today.strftime("%Y-%m-%d")

    # Find Active section
    active_match = re.search(r"## Active.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if active_match:
        active_text = active_match.group(1)
        # Parse markdown table rows (skip header/separator)
        for line in active_text.splitlines():
            if not line.startswith("|") or line.startswith("| Task") or line.startswith("|---"):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) < 4:
                continue
            task, priority, due, status = cols[0], cols[1], cols[2], cols[3]
            notes = cols[4] if len(cols) > 4 else ""

            item = {"task": task, "priority": priority, "due": due, "status": status, "notes": notes}
            active.append(item)

            # Check if overdue
            if due and due != "—" and due != "-":
                try:
                    due_date = datetime.strptime(due, "%Y-%m-%d").date()
                    if due_date < today and status not in ("Done", "Withdrawn"):
                        overdue.append(item)
                except ValueError:
                    pass

    # Find Completed section — look for today's date in Notes or Completed column
    completed_match = re.search(r"## Completed.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if completed_match:
        completed_text = completed_match.group(1)
        for line in completed_text.splitlines():
            if not line.startswith("|") or line.startswith("| Task") or line.startswith("|---"):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) < 3:
                continue
            task, priority = cols[0], cols[1]
            completed_date = cols[2] if len(cols) > 2 else ""
            notes = cols[3] if len(cols) > 3 else ""

            # Check if completed today
            if today_str in completed_date or today_str in notes:
                completed_today.append({"task": task, "priority": priority, "notes": notes})

    return completed_today, active, overdue


def parse_daily_log(content: str, today: date) -> dict:
    """Parse daily log and compute streak + velocity metrics."""
    today_str = today.strftime("%Y-%m-%d")

    # Extract all dated entries: ### YYYY-MM-DD
    entries = {}
    current_date = None
    current_lines = []

    for line in content.splitlines():
        m = re.match(r"^### (\d{4}-\d{2}-\d{2})", line)
        if m:
            if current_date:
                entries[current_date] = "\n".join(current_lines)
            current_date = m.group(1)
            current_lines = [line]
        elif current_date:
            current_lines.append(line)

    if current_date:
        entries[current_date] = "\n".join(current_lines)

    def completions_for(date_str: str) -> int:
        text = entries.get(date_str, "")
        m = re.search(r"\*\*Completed today:\s*(\d+)\*\*", text)
        return int(m.group(1)) if m else 0

    # This week (last 7 days including today)
    this_week = sum(completions_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                    for i in range(7))

    # Last 30 days
    last_30 = sum(completions_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                  for i in range(30))

    # All-time
    all_time = sum(
        int(re.search(r"\*\*Completed today:\s*(\d+)\*\*", text).group(1))
        for text in entries.values()
        if re.search(r"\*\*Completed today:\s*(\d+)\*\*", text)
    )

    # Streak — consecutive days ending today with >=1 completion
    streak = 0
    check_date = today
    while True:
        ds = check_date.strftime("%Y-%m-%d")
        if completions_for(ds) >= 1:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # Avg per active day
    active_days = sum(1 for text in entries.values()
                      if re.search(r"\*\*Completed today:\s*[1-9]", text))
    avg_per_day = round(all_time / active_days, 1) if active_days > 0 else 0.0

    # Overdue trend — compare today vs 7-day rolling average
    def overdue_for(date_str: str) -> int:
        text = entries.get(date_str, "")
        m = re.search(r"Overdue:\s*(\d+)", text)
        return int(m.group(1)) if m else 0

    today_overdue = overdue_for(today_str)
    avg_overdue = sum(overdue_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                      for i in range(1, 8)) / 7
    if today_overdue > avg_overdue + 0.5:
        overdue_trend = "↑"
    elif today_overdue < avg_overdue - 0.5:
        overdue_trend = "↓"
    else:
        overdue_trend = "→"

    return {
        "streak": streak,
        "this_week": this_week,
        "last_7": this_week,
        "last_30": last_30,
        "all_time": all_time,
        "avg_per_day": avg_per_day,
        "overdue_trend": overdue_trend,
        "entry_count": len(entries),
    }


def parse_outreach_today(content: str, today: date) -> list:
    """Extract outreach entries sent today from outreach-log.md."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("| Date") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 5 and cols[0] == today_str:
            results.append({
                "date": cols[0],
                "type": cols[1] if len(cols) > 1 else "",
                "channel": cols[2] if len(cols) > 2 else "",
                "name": cols[3] if len(cols) > 3 else "",
                "company": cols[4] if len(cols) > 4 else "",
                "subject": cols[5] if len(cols) > 5 else "",
            })

    return results


def find_research_today(output_dir: Path, today: date) -> list:
    """Find dossier files updated today (output/<slug>/<slug>.md pattern)."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for md_file in output_dir.rglob("*.md"):
        # Dossier = file where stem matches parent directory name
        if md_file.stem == md_file.parent.name:
            try:
                # Read only first 15 lines for efficiency
                lines = []
                with open(md_file, encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= 15:
                            break
                        lines.append(line)
                header = "".join(lines)
                if f"Last updated: {today_str}" in header:
                    results.append({
                        "name": md_file.parent.name,
                        "path": str(md_file.relative_to(output_dir.parent)),
                    })
            except (IOError, UnicodeDecodeError):
                pass

    return results


def parse_changelog_today(content: str, today: date) -> list:
    """Extract changelog entries for today."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for line in content.splitlines():
        m = re.match(rf"^## {re.escape(today_str)}\s*[—-]?\s*(.*)", line)
        if m:
            results.append({"title": m.group(1).strip(), "date": today_str})

    return results


def parse_pipeline(content: str) -> list:
    """Parse active pipeline entries from job-pipeline.md."""
    results = []

    # Find the Active section
    active_match = re.search(r"## Active.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if not active_match:
        # Try parsing without section headers
        active_match = re.search(r"\n(.*)", content, re.DOTALL)
    if not active_match:
        return results

    for line in active_match.group(1).splitlines():
        if not line.startswith("|") or line.startswith("| Company") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 3 and cols[0]:
            results.append({
                "company": cols[0],
                "role": cols[1] if len(cols) > 1 else "",
                "stage": cols[2] if len(cols) > 2 else "",
            })

    return results


def parse_networking_activity(networking_content: str, todos_content: str) -> list:
    """Find contacts with pending follow-up to-dos."""
    results = []

    # Extract contact names from networking.md
    contacts = []
    for line in networking_content.splitlines():
        if not line.startswith("|") or line.startswith("| Name") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 3 and cols[0]:
            contacts.append({
                "name": cols[0],
                "company": cols[1] if len(cols) > 1 else "",
                "last_interaction": cols[4] if len(cols) > 4 else "",
            })

    # Cross-reference with active todos (full name matching)
    for contact in contacts:
        name = contact["name"]
        if name and name in todos_content:
            # Count pending todos mentioning this contact
            count = todos_content.count(name)
            if count > 0:
                results.append({
                    "name": name,
                    "company": contact["company"],
                    "last_interaction": contact["last_interaction"],
                    "pending_todos": count,
                })

    return results


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    # Read all source files
    todos_content = read_file(repo_root / "data" / "job-todos.md")
    daily_log_content = read_file(repo_root / "data" / "job-todos-daily-log.md")
    pipeline_content = read_file(repo_root / "data" / "job-pipeline.md")
    networking_content = read_file(repo_root / "data" / "networking.md")
    outreach_content = read_file(repo_root / "data" / "outreach-log.md")
    changelog_content = read_file(repo_root / "docs" / "CHANGELOG.md")

    output_dir = repo_root / "output"

    # Process each source
    completed_today, active, overdue = parse_todos(todos_content, today)
    metrics = parse_daily_log(daily_log_content, today)
    outreach_today = parse_outreach_today(outreach_content, today)
    research_today = find_research_today(output_dir, today) if output_dir.exists() else []
    changelog_today = parse_changelog_today(changelog_content, today)
    pipeline = parse_pipeline(pipeline_content)
    networking = parse_networking_activity(networking_content, todos_content)

    result = {
        "target_date": today.strftime("%Y-%m-%d"),
        "completed_today": completed_today,
        "active_remaining": active,
        "overdue": overdue,
        "outreach_sent_today": outreach_today,
        "research_completed_today": research_today,
        "changelog_today": changelog_today,
        "metrics": metrics,
        "pipeline_snapshot": pipeline,
        "networking_activity": networking,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
