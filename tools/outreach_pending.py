#!/usr/bin/env python3
"""
outreach_pending.py — Pre-process outreach follow-up status for /standup and /weekly-review.

Reads: data/outreach-log.md, data/networking.md

Cross-references networking.md Interaction Log to detect replies that were logged
via networking_write.py but not reflected in outreach-log.md status.

Outputs JSON to stdout with keys:
  awaiting_response[]           — outreach with no reply, not yet overdue
  awaiting_response_overdue[]   — outreach with no reply, overdue (>=threshold days)
  recent_outreach{}             — {sent, replied, no_reply, response_rate_percent}

Usage:
  python tools/outreach_pending.py [--days-threshold-overdue N] [--lookback-days N]
                                   [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days-threshold-overdue", type=int, default=5,
                   help="Days without reply before marking overdue (default: 5).")
    p.add_argument("--lookback-days", type=int, default=30,
                   help="Days to look back in outreach log (default: 30).")
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


def parse_networking_interactions(networking_content: str) -> dict[str, date | None]:
    """Parse networking.md Interaction Log to find most recent interaction date per contact.

    Returns {lowercase_name: latest_interaction_date}.
    """
    interactions: dict[str, date | None] = {}
    lines = networking_content.splitlines()
    current_name = None

    for line in lines:
        # Match ### Name or ### Name — Company
        m = re.match(r"^###\s+(.+?)(?:\s+[—–]\s+.+)?$", line)
        if m:
            current_name = m.group(1).strip().lower()
            continue

        if current_name is None:
            continue

        # Match #### YYYY-MM-DD | type | summary
        entry_match = re.match(r"^####\s+(\d{4}-\d{2}-\d{2})\s*\|", line)
        if entry_match:
            try:
                entry_date = datetime.strptime(entry_match.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            existing = interactions.get(current_name)
            if existing is None or entry_date > existing:
                interactions[current_name] = entry_date

    return interactions


def parse_outreach_log(content: str, today: date, lookback_days: int,
                       overdue_threshold: int,
                       networking_interactions: dict[str, date | None] | None = None) -> dict:
    """Parse outreach-log.md table rows, cross-referencing networking.md for replies."""
    cutoff = today - timedelta(days=lookback_days)
    if networking_interactions is None:
        networking_interactions = {}

    sent_count = 0
    replied_count = 0
    no_reply_count = 0
    awaiting = []
    awaiting_overdue = []

    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("| Date") or line.startswith("|---"):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 6:
            continue

        date_str = cols[0]
        outreach_type = cols[1]
        channel = cols[2]
        name = cols[3]
        company = cols[4]
        subject = cols[5]
        status = cols[6] if len(cols) > 6 else "Drafted"

        try:
            sent_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if sent_date < cutoff:
            continue

        sent_count += 1

        # Cross-reference: if networking.md has an interaction AFTER the outreach date,
        # treat this as replied regardless of outreach-log status
        effective_status = status
        name_lower = name.lower().strip()
        latest_interaction = networking_interactions.get(name_lower)
        if (latest_interaction is not None
                and latest_interaction > sent_date
                and effective_status.lower() in ("sent", "drafted", "pending")):
            effective_status = "Replied"

        if effective_status.lower() in ("replied", "reply"):
            replied_count += 1
        elif effective_status.lower() in ("no reply", "no_reply", "noreply"):
            no_reply_count += 1
            days_since = (today - sent_date).days
            entry = {
                "date": date_str,
                "name": name,
                "company": company,
                "channel": channel,
                "subject": subject,
                "days_since_sent": days_since,
            }
            if days_since >= overdue_threshold:
                awaiting_overdue.append(entry)
            else:
                awaiting.append(entry)
        elif effective_status.lower() in ("drafted", "sent", "pending"):
            days_since = (today - sent_date).days
            if days_since >= overdue_threshold:
                entry = {
                    "date": date_str,
                    "name": name,
                    "company": company,
                    "channel": channel,
                    "subject": subject,
                    "days_since_sent": days_since,
                    "status": status,
                }
                awaiting_overdue.append(entry)

    # Sort by most overdue first
    awaiting_overdue.sort(key=lambda e: -e["days_since_sent"])
    awaiting.sort(key=lambda e: -e["days_since_sent"])

    denominator = sent_count - replied_count
    response_rate = round((replied_count / denominator) * 100) if denominator > 0 else None

    return {
        "awaiting_response": awaiting,
        "awaiting_response_overdue": awaiting_overdue,
        "recent_outreach": {
            "sent": sent_count,
            "replied": replied_count,
            "no_reply": no_reply_count,
            "response_rate_percent": response_rate,
        },
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    outreach_content = read_file(repo_root / "data" / "outreach-log.md")
    networking_content = read_file(repo_root / "data" / "networking.md")

    # Build interaction map from networking.md
    networking_interactions = parse_networking_interactions(networking_content)

    result = parse_outreach_log(
        outreach_content,
        today,
        lookback_days=args.lookback_days,
        overdue_threshold=args.days_threshold_overdue,
        networking_interactions=networking_interactions,
    )
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
