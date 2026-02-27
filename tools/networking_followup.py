#!/usr/bin/env python3
"""
networking_followup.py — Pre-process networking follow-up due dates for /standup.

Reads: data/networking.md

Outputs JSON to stdout with keys:
  followup_due[]      — contacts where follow-up is due today or in the future (within 7 days)
  followup_overdue[]  — contacts where follow-up is overdue
  summary{}           — total_contacts, due_today, overdue

Due-date inference rules (applied to follow-up notes):
  - "next week" → last_date + 7d
  - "3–5 business days" / "3-5 business days" → last_date + 5d
  - "~YYYY-MM-DD" → extract date
  - Explicit date YYYY-MM-DD in notes → use that date
  - Default → last_date + 14d

Usage:
  python tools/networking_followup.py [--days-overdue N] [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days-overdue", type=int, default=0,
                   help="Minimum days past due before flagging as overdue (default: 0 = today).")
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


def infer_followup_date(last_date: date, notes: str) -> date | None:
    """Infer the follow-up due date from notes and last interaction date."""
    if not last_date:
        return None

    notes_lower = notes.lower().strip()

    # Explicit date with tilde: ~YYYY-MM-DD
    m = re.search(r"~(\d{4}-\d{2}-\d{2})", notes)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    # Explicit date YYYY-MM-DD anywhere in notes
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", notes)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    # "next week"
    if "next week" in notes_lower:
        return last_date + timedelta(days=7)

    # "3-5 business days" or "3–5 business days"
    if re.search(r"3[–-]5\s+business\s+days?", notes_lower):
        return last_date + timedelta(days=5)

    # "N days" or "N business days"
    m = re.search(r"(\d+)\s+(?:business\s+)?days?", notes_lower)
    if m:
        return last_date + timedelta(days=int(m.group(1)))

    # "in N weeks"
    m = re.search(r"in\s+(\d+)\s+weeks?", notes_lower)
    if m:
        return last_date + timedelta(weeks=int(m.group(1)))

    # Default: 14 days
    return last_date + timedelta(days=14)


def parse_networking(content: str, today: date, days_overdue_threshold: int) -> dict:
    """Parse networking.md contacts table and interaction notes."""
    followup_due = []
    followup_overdue = []

    # Find the main contacts table
    contacts_match = re.search(
        r"\|\s*Name\s*\|.*?\n\|[-\s|]+\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL
    )
    if not contacts_match:
        return {
            "followup_due": [],
            "followup_overdue": [],
            "summary": {"total_contacts": 0, "due_today": 0, "overdue": 0},
        }

    table_text = contacts_match.group(1)
    total_contacts = 0

    for line in table_text.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4 or not cols[0]:
            continue

        name = cols[0]
        company = cols[1] if len(cols) > 1 else ""
        role = cols[2] if len(cols) > 2 else ""
        relationship = cols[3] if len(cols) > 3 else ""
        last_interaction_str = cols[4] if len(cols) > 4 else ""
        follow_up_action = cols[5] if len(cols) > 5 else ""

        total_contacts += 1

        if not last_interaction_str or last_interaction_str == "—":
            continue

        try:
            last_date = datetime.strptime(last_interaction_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Skip contacts with "—" follow-up
        if follow_up_action == "—" or not follow_up_action:
            continue

        followup_date = infer_followup_date(last_date, follow_up_action)
        if not followup_date:
            continue

        overdue_cutoff = today - timedelta(days=days_overdue_threshold)
        days_until = (followup_date - today).days

        entry = {
            "name": name,
            "company": company,
            "role": role,
            "relationship": relationship,
            "last_interaction": last_interaction_str,
            "follow_up_action": follow_up_action,
            "followup_date": followup_date.strftime("%Y-%m-%d"),
            "days_until": days_until,
        }

        if followup_date <= today:
            if followup_date <= overdue_cutoff:
                followup_overdue.append(entry)
            else:
                followup_due.append(entry)
        elif days_until <= 7:
            # Due within 7 days — surface as upcoming
            followup_due.append(entry)

    # Sort overdue by most overdue first
    followup_overdue.sort(key=lambda e: e["days_until"])
    followup_due.sort(key=lambda e: e["days_until"])

    return {
        "followup_due": followup_due,
        "followup_overdue": followup_overdue,
        "summary": {
            "total_contacts": total_contacts,
            "due_today": len([e for e in followup_due if e["days_until"] == 0]),
            "overdue": len(followup_overdue),
        },
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    content = read_file(repo_root / "data" / "networking.md")

    result = parse_networking(content, today, args.days_overdue)
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
