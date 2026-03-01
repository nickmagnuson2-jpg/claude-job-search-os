#!/usr/bin/env python3
"""
networking_read.py — Pre-process networking contacts for /networking show command.

Reads: data/networking.md, data/job-pipeline.md

Per-contact output:
  days_since_last_interaction, stale (>14 days or "—"),
  interaction_count (count "#### " date headers in that contact's section),
  pipeline_link (company field matches active pipeline company)

Contact table column layout (7 columns):
  Name | Company | Role | Relationship | Added | Last Interaction | Email

Output JSON (stdout):
  {
    "contacts": [...],
    "stale_contacts": [...],
    "pipeline_connections": [...],
    "metrics": { "total_contacts", "active_count", "stale_count" },
    "target_date": "YYYY-MM-DD"
  }

Usage:
  PYTHONIOENCODING=utf-8 python tools/networking_read.py [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

STALE_THRESHOLD_DAYS = 14
TERMINAL_STAGES = {"Withdrawn", "Rejected", "Accepted", "Archived"}


def parse_args():
    p = argparse.ArgumentParser(
        description="Read networking contacts with staleness and pipeline links. Read-only."
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


# ─────────────────────────────────────────────────────────────────────────────
# Contact table parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_contacts(content: str) -> list[dict]:
    """
    Parse the Contacts table from networking.md.
    Columns: Name | Company | Role | Relationship | Added | Last Interaction | Email
    """
    contacts = []
    in_contacts = False

    for line in content.splitlines():
        if re.match(r"^##\s+Contacts", line, re.IGNORECASE):
            in_contacts = True
            continue
        if re.match(r"^##\s+", line) and in_contacts:
            in_contacts = False
            continue

        if not in_contacts:
            continue
        if not line.startswith("|"):
            continue
        if re.match(r"\|\s*(Name|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or cols[0] == "---" or not cols[0]:
            continue

        contacts.append({
            "name":             cols[0],
            "company":          cols[1] if len(cols) > 1 else "",
            "role":             cols[2] if len(cols) > 2 else "",
            "relationship":     cols[3] if len(cols) > 3 else "",
            "added":            cols[4] if len(cols) > 4 else "",
            "last_interaction": cols[5] if len(cols) > 5 else "",
            "email":            cols[6] if len(cols) > 6 else "",
        })

    return contacts


# ─────────────────────────────────────────────────────────────────────────────
# Interaction count from log
# ─────────────────────────────────────────────────────────────────────────────

def build_interaction_counts(content: str) -> dict[str, int]:
    """
    Count '#### ' date headers per contact section in the Interaction Log.
    Returns { contact_name_lower: count }
    """
    counts: dict[str, int] = {}
    current_contact: str | None = None

    for line in content.splitlines():
        # Contact section header: "### Name — Company" or "### Name"
        m = re.match(r"^###\s+(.+?)(?:\s+—|\s+–|$)", line)
        if m:
            current_contact = m.group(1).strip().lower()
            if current_contact not in counts:
                counts[current_contact] = 0
            continue

        # Interaction date header: "#### YYYY-MM-DD | type | label"
        if line.startswith("#### ") and current_contact is not None:
            counts[current_contact] = counts.get(current_contact, 0) + 1

    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline cross-reference
# ─────────────────────────────────────────────────────────────────────────────

def build_pipeline_index(pipeline_content: str) -> dict[str, dict]:
    """
    Parse active pipeline entries. Return { company_name_lower: {company, stage} }
    """
    index: dict[str, dict] = {}
    in_archived = False

    for line in pipeline_content.splitlines():
        if re.match(r"^##\s+", line):
            section = line.strip("# ").strip().lower()
            in_archived = any(t in section for t in ("archived", "withdrawn", "rejected"))
            continue

        if not line.startswith("|"):
            continue
        if re.match(r"\|\s*(Company|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or cols[0] == "---" or not cols[0]:
            continue

        company = cols[0]
        stage   = cols[2] if len(cols) > 2 else ""

        if stage in TERMINAL_STAGES or in_archived:
            continue

        index[company.lower()] = {"company": company, "stage": stage}

    return index


def _company_key(company: str) -> str:
    """Normalize company name for matching — lowercase, strip punctuation."""
    return re.sub(r"[^a-z0-9\s]", "", company.lower()).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Main processing
# ─────────────────────────────────────────────────────────────────────────────

def process_contacts(
    contacts: list[dict],
    interaction_counts: dict[str, int],
    pipeline_index: dict[str, dict],
    today: date,
) -> list[dict]:
    enriched = []

    for c in contacts:
        # Staleness
        last = c["last_interaction"]
        days_since = None
        stale = True  # default stale if no date

        if last and last not in ("—", "-", "–", ""):
            try:
                dt = datetime.strptime(last, "%Y-%m-%d").date()
                days_since = (today - dt).days
                stale = days_since > STALE_THRESHOLD_DAYS
            except ValueError:
                pass  # malformed date → treat as stale

        # Interaction count
        count = interaction_counts.get(c["name"].lower(), 0)

        # Pipeline link — check if contact's company matches any active pipeline entry
        pipeline_link = None
        contact_co = _company_key(c["company"])
        if contact_co:
            for key, entry in pipeline_index.items():
                if contact_co == _company_key(entry["company"]):
                    pipeline_link = {"company": entry["company"], "stage": entry["stage"]}
                    break

        enriched.append({
            **c,
            "days_since_last_interaction": days_since,
            "stale":                       stale,
            "interaction_count":           count,
            "pipeline_link":               pipeline_link,
        })

    return enriched


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    networking_content = read_file(repo_root / "data" / "networking.md")
    pipeline_content   = read_file(repo_root / "data" / "job-pipeline.md")

    contacts          = parse_contacts(networking_content)
    interaction_counts = build_interaction_counts(networking_content)
    pipeline_index    = build_pipeline_index(pipeline_content)

    enriched = process_contacts(contacts, interaction_counts, pipeline_index, today)

    stale_contacts       = [c for c in enriched if c["stale"]]
    pipeline_connections = [c for c in enriched if c["pipeline_link"] is not None]

    result = {
        "contacts":            enriched,
        "stale_contacts":      stale_contacts,
        "pipeline_connections": pipeline_connections,
        "metrics": {
            "total_contacts": len(enriched),
            "active_count":   len([c for c in enriched if not c["stale"]]),
            "stale_count":    len(stale_contacts),
        },
        "target_date": today.strftime("%Y-%m-%d"),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
