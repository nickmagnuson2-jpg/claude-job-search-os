#!/usr/bin/env python3
"""
networking_followup.py — Pre-process networking follow-up due dates for /standup.

Reads: data/networking.md

Outputs JSON to stdout with keys:
  followup_due[]      — contacts where follow-up is due today or in the future (within 7 days)
  followup_overdue[]  — contacts where follow-up is overdue
  summary{}           — total_contacts, due_today, overdue

Follow-up source: The Interaction Log section of networking.md, NOT the Contacts table.
Each contact's subsection (### Name — Company) contains entries with **Follow-up:** lines.
The most recent non-dash follow-up line is used for date inference.

Due-date inference rules (applied to follow-up notes):
  - "next week" → last_date + 7d
  - "3–5 business days" / "3-5 business days" → last_date + 5d
  - "~YYYY-MM-DD" → extract date
  - Explicit date YYYY-MM-DD in notes → use that date
  - Default → last_date + 14d

Usage:
  python3 tools/networking_followup.py [--days-overdue N] [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Shared freeform-stage classifier (single source of truth). See stage_vocab.py.
from stage_vocab import is_terminal_stage  # noqa: E402


def build_closed_company_index(pipeline_content: str) -> dict[str, str]:
    """Map company (lowercased) -> ISO date of its most recent terminal pipeline row.

    Two rules, both learned the hard way elsewhere in this codebase:

    * A company with ANY non-terminal row is not closed, regardless of what a stale
      row says. Same rule todo_write.sync uses; origin 2026-08-08.
    * Use the LATEST terminal row. A company can be closed twice (an old withdrawn
      application, then a real rejection months later), and comparing against the
      older date makes a pre-close touch look post-close.
    """
    latest: dict[str, str] = {}
    live: set[str] = set()

    for line in pipeline_content.splitlines():
        if (not line.startswith("|")
                or line.startswith("| Company")
                or re.match(r"^\|\s*:?-+", line)):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 4 or not cols[0]:
            continue
        company, stage, updated = cols[0].lower(), cols[2], cols[3]
        if not is_terminal_stage(stage):
            live.add(company)
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
            continue
        if company not in latest or updated > latest[company]:
            latest[company] = updated

    return {c: d for c, d in latest.items() if c not in live}


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


def parse_contacts_table(content: str) -> list[dict]:
    """Parse the Contacts table. Returns list of {name, company, role, relationship, last_interaction}.

    Real table format (7 cols):
      | Name | Company | Role | Relationship | Added | Last Interaction | Email |
    """
    contacts = []
    contacts_match = re.search(
        r"\|\s*Name\s*\|.*?\n\|[-\s|]+\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL,
    )
    if not contacts_match:
        return contacts

    for line in contacts_match.group(1).splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4 or not cols[0]:
            continue

        name = cols[0]
        company = cols[1] if len(cols) > 1 else ""
        role = cols[2] if len(cols) > 2 else ""
        relationship = cols[3] if len(cols) > 3 else ""
        # cols[4] = Added (date added to table)
        # cols[5] = Last Interaction (date of most recent interaction)
        last_interaction_str = cols[5] if len(cols) > 5 else ""

        contacts.append({
            "name": name,
            "company": company,
            "role": role,
            "relationship": relationship,
            "last_interaction": last_interaction_str,
        })
    return contacts


def extract_followup_from_interaction_log(content: str, name: str) -> tuple[str, date | None]:
    """Find the most recent **Follow-up:** line in a contact's Interaction Log subsection.

    Returns (followup_text, interaction_date) where interaction_date comes from the
    #### YYYY-MM-DD header above the follow-up line. Returns ("", None) if not found.
    """
    name_lower = name.lower().strip()
    lines = content.splitlines()

    # Find the ### section for this contact
    section_start = -1
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        m = re.match(r"^###\s+(.+?)(?:\s+[—–]|$)", line)
        if m and m.group(1).strip().lower() == name_lower:
            section_start = i
            break

    if section_start == -1:
        return ("", None)

    # Find section end
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if lines[i].startswith("## ") or lines[i].startswith("### "):
            section_end = i
            break

    # Scan entries in section (newest first — entries are prepended).
    # Each entry starts with #### YYYY-MM-DD | type | summary
    # and has a **Follow-up:** line below it.
    # We want the MOST RECENT non-dash follow-up — BUT if the most recent entry
    # explicitly sets Follow-up: — (or "None"), the thread is closed; do not pull
    # a stale follow-up from older entries.
    current_entry_date = None
    entries_seen = 0
    saw_fu_in_current_entry = False
    for i in range(section_start + 1, section_end):
        line = lines[i]

        entry_match = re.match(r"^####\s+(\d{4}-\d{2}-\d{2})\s*\|", line)
        if entry_match:
            entries_seen += 1
            saw_fu_in_current_entry = False
            try:
                current_entry_date = datetime.strptime(entry_match.group(1), "%Y-%m-%d").date()
            except ValueError:
                current_entry_date = None
            continue

        fu_match = re.match(r"^\*\*Follow-up[^*:]*:\*\*\s*(.*)", line)
        if fu_match:
            fu_text = fu_match.group(1).strip()
            fu_lower = fu_text.lower()
            is_closed = (
                fu_text in ("—", "-", "")
                or fu_lower.startswith("none")
                or re.match(r"^(closed|resolved)\b", fu_lower) is not None
            )
            # If the MOST RECENT entry's first Follow-up line is an explicit close,
            # treat the contact as closed and stop scanning older entries.
            if is_closed and entries_seen == 1 and not saw_fu_in_current_entry:
                return ("", None)
            saw_fu_in_current_entry = True
            if is_closed:
                continue
            return (fu_text, current_entry_date)

    return ("", None)


def parse_networking(content: str, today: date, days_overdue_threshold: int,
                     closed_index: dict[str, str] | None = None) -> dict:
    """Parse networking.md contacts table and interaction log follow-ups."""
    followup_due = []
    followup_overdue = []
    suppressed_closed = []
    closed_index = closed_index or {}

    contacts = parse_contacts_table(content)
    if not contacts:
        return {
            "followup_due": [],
            "followup_overdue": [],
            "suppressed_closed": [],
            "summary": {"total_contacts": 0, "due_today": 0, "overdue": 0,
                        "suppressed_closed": 0},
        }

    total_contacts = len(contacts)

    for contact in contacts:
        name = contact["name"]
        last_interaction_str = contact["last_interaction"]

        if not last_interaction_str or last_interaction_str == "—":
            continue

        try:
            last_date = datetime.strptime(last_interaction_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Get follow-up from Interaction Log section (not table column)
        followup_text, entry_date = extract_followup_from_interaction_log(content, name)
        if not followup_text:
            # No actionable follow-up in interaction log — skip this contact
            continue

        # Use the entry date for relative inference (e.g., "next week" = entry_date + 7d)
        reference_date = entry_date if entry_date else last_date
        followup_date = infer_followup_date(reference_date, followup_text)
        if not followup_date:
            continue

        overdue_cutoff = today - timedelta(days=days_overdue_threshold)
        days_until = (followup_date - today).days

        # Staleness detection: action text contains time-bound tokens
        # ("today", "tomorrow", "EOD", etc.) but the entry was written in the past.
        # These are prep notes frozen at write-time, not live actions.
        entry_age_days = (today - entry_date).days if entry_date else None
        is_stale = False
        if entry_date and entry_date < today:
            stale_pattern = re.compile(
                r"\b(today|tomorrow|tonight|this morning|this afternoon|this evening|EOD|COB)\b",
                re.IGNORECASE,
            )
            if stale_pattern.search(followup_text):
                is_stale = True
                followup_text = f"[STALE prep note from {entry_date.isoformat()}] {followup_text}"

        entry = {
            "name": name,
            "company": contact["company"],
            "role": contact["role"],
            "relationship": contact["relationship"],
            "last_interaction": last_interaction_str,
            "follow_up_action": followup_text,
            "followup_date": followup_date.strftime("%Y-%m-%d"),
            "days_until": days_until,
            "entry_age_days": entry_age_days,
            "is_stale": is_stale,
        }

        # Silence children of a closed pipeline row — but ONLY when the contact has not
        # been touched since the close. A touch after the close is deliberate
        # relationship work and outlives the opportunity by design.
        close_date = closed_index.get((contact["company"] or "").strip().lower())
        if close_date and last_interaction_str < close_date:
            entry["pipeline_closed"] = True
            entry["close_date"] = close_date
            entry["suppression_reason"] = (
                f"{contact['company']} closed {close_date}; last touch "
                f"{last_interaction_str} predates it, so this nudge is a ghost."
            )
            suppressed_closed.append(entry)
            continue

        if followup_date <= today:
            # Strictly-before the cutoff is overdue; a follow-up due exactly today
            # (or within the grace window) is "due", not "overdue". With the default
            # threshold (days_overdue=0 → cutoff==today), a `<=` here sent every
            # due-today item to `overdue`, so summary.due_today was structurally
            # always 0 (fable-audit Theme 2).
            if followup_date < overdue_cutoff:
                followup_overdue.append(entry)
            else:
                followup_due.append(entry)
        elif days_until <= 7:
            followup_due.append(entry)

    followup_overdue.sort(key=lambda e: e["days_until"])
    followup_due.sort(key=lambda e: e["days_until"])

    return {
        "followup_due": followup_due,
        "followup_overdue": followup_overdue,
        "suppressed_closed": suppressed_closed,
        "summary": {
            "total_contacts": total_contacts,
            "due_today": len([e for e in followup_due if e["days_until"] == 0]),
            "overdue": len(followup_overdue),
            "suppressed_closed": len(suppressed_closed),
        },
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    content = read_file(repo_root / "data" / "networking.md")

    # Graceful degradation: no pipeline file means no suppression, never a crash.
    pipeline_content = read_file(repo_root / "data" / "job-pipeline.md")
    closed_index = build_closed_company_index(pipeline_content) if pipeline_content else {}

    result = parse_networking(content, today, args.days_overdue, closed_index)
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
