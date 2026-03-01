#!/usr/bin/env python3
"""
remember_classify.py — Deterministic destination classifier for /remember notes.

Reads:
  data/networking.md     — known contact names
  data/job-pipeline.md   — known pipeline company names
  output/                — known dossier slugs

Classification priority order (first-match for primary; all matches returned):
  1. outreach_reply   — known contact + reply keywords
  2. contact_note     — known contact name present
  3. pipeline_note    — known pipeline company + decision/status keywords
  4. company_note     — known company (pipeline or dossier) + observation content
  5. profile_update   — comp/salary/availability keywords
  6. decision         — decided/won't/prioritizing keywords
  7. raw_capture      — URL-only, or explicit "save"/"inbox" language
  8. general_note     — fallback

A note can match multiple destinations (e.g., contact + company both written).

Output JSON (stdout):
  {
    "note": "...",
    "destinations": [
      {"type": "contact_note", "file": "data/networking.md", "entity": "Alex Mullin",
       "matched_name": "Alex Mullin"},
      {"type": "company_note", "file": "data/company-notes/amae-health.md",
       "entity": "Amae Health", "slug": "amae-health"}
    ],
    "ambiguous": false,
    "warnings": []
  }

Usage:
  PYTHONIOENCODING=utf-8 python tools/remember_classify.py --note "text" [--repo-root PATH]
"""
import argparse
import json
import re
from pathlib import Path

# ── Keywords ──────────────────────────────────────────────────────────────────

REPLY_KEYWORDS = [
    "replied", "responded", "heard back from", "got back to me",
    "wrote back", "responded to", "reached back", "got a reply",
    "reply from",
]

DECISION_STATUS_KEYWORDS = [
    "decided", "stage", "withdrawn", "withdraw", "offer", "rejected",
    "won't pursue", "not pursuing", "passing on", "moving forward",
    "application", "accepted", "hired",
]

PROFILE_KEYWORDS = [
    "comp", "compensation", "salary", "availability", "start date",
    "floor", "ceiling", "target comp", "notice", "available", "notice period",
    "base salary", "total comp", "package",
]

DECISION_KEYWORDS = [
    "decided", "won't", "not pursuing", "prioritizing", "deprioritizing",
    "de-prioritizing", "choosing not to", "going with", "going to focus",
]

RAW_CAPTURE_KEYWORDS = [
    "save for later", "just note this", "save this", "add to inbox",
    "inbox", "check this out", "look into this later",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _slug(name: str) -> str:
    """Convert company display name to URL slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _has_url(text: str) -> bool:
    return bool(re.search(r"https?://\S+", text))


def _url_only(text: str) -> bool:
    """True if the text is essentially just a URL with no meaningful prose."""
    stripped = text.strip()
    # Remove any URL present
    without_url = re.sub(r"https?://\S+", "", stripped).strip()
    return _has_url(stripped) and len(without_url) < 10


def _contains_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_contacts(content: str) -> list[str]:
    """Extract contact full names from networking.md Contacts table."""
    names = []
    in_contacts = False

    for line in content.splitlines():
        if re.match(r"^##\s+Contacts", line, re.IGNORECASE):
            in_contacts = True
            continue
        if re.match(r"^##\s+", line) and in_contacts:
            in_contacts = False
            continue
        if not in_contacts or not line.startswith("|"):
            continue
        if re.match(r"\|\s*(Name|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if cols and cols[0] and cols[0] != "---":
            names.append(cols[0])

    return names


def load_pipeline_companies(content: str) -> list[str]:
    """Extract active company names from job-pipeline.md."""
    companies = []
    terminal_stages = {"Withdrawn", "Rejected", "Accepted", "Archived"}
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
        if len(cols) < 3 or cols[0] == "---" or not cols[0]:
            continue

        stage = cols[2] if len(cols) > 2 else ""
        if stage in terminal_stages or in_archived:
            continue
        companies.append(cols[0])

    return companies


def load_dossier_slugs(output_dir: Path) -> list[tuple[str, str]]:
    """
    Find dossier files (stem == parent folder name).
    Returns [(slug, display_name), ...]
    """
    results = []
    if not output_dir.exists():
        return results

    for md_file in output_dir.rglob("*.md"):
        if md_file.stem == md_file.parent.name:
            slug = md_file.parent.name
            display = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
            results.append((slug, display))

    return results


# ── Entity matching ───────────────────────────────────────────────────────────

def find_matching_contacts(note: str, contacts: list[str]) -> list[str]:
    """
    Return list of contact names that appear as a substring in the note.
    Uses full-name match (case-insensitive) — not first name only.
    """
    note_lower = note.lower()
    matches = []
    for name in contacts:
        if name.lower() in note_lower:
            matches.append(name)
    return matches


def find_matching_companies(
    note: str,
    pipeline_companies: list[str],
    dossier_slugs: list[tuple[str, str]],
) -> list[tuple[str, str, bool]]:
    """
    Return list of (company_name, slug, in_pipeline) for companies found in note.
    Searches pipeline names and dossier display names.
    """
    note_lower = note.lower()
    found: dict[str, tuple[str, str, bool]] = {}  # slug → (name, slug, in_pipeline)

    # Check pipeline companies
    for company in pipeline_companies:
        if company.lower() in note_lower:
            s = _slug(company)
            found[s] = (company, s, True)

    # Check dossier slugs (by display name)
    for slug, display in dossier_slugs:
        if display.lower() in note_lower:
            if slug not in found:
                found[slug] = (display, slug, False)

    return list(found.values())


# ── Classifier ────────────────────────────────────────────────────────────────

def classify_note(
    note: str,
    contacts: list[str],
    pipeline_companies: list[str],
    dossier_slugs: list[tuple[str, str]],
) -> dict:
    destinations = []
    warnings = []

    matching_contacts = find_matching_contacts(note, contacts)
    matching_companies = find_matching_companies(note, pipeline_companies, dossier_slugs)

    # ── 1. Outreach reply ─────────────────────────────────────────────────
    if matching_contacts and _contains_any(note, REPLY_KEYWORDS):
        for contact in matching_contacts:
            destinations.append({
                "type":         "outreach_reply",
                "file":         "data/outreach-log.md",
                "entity":       contact,
                "matched_name": contact,
            })
        # An outreach reply often also contains contact-note content — fall through

    # ── 2. Contact note ───────────────────────────────────────────────────
    # Always add contact_note when a known contact is present — even if
    # outreach_reply was also added (the skill writes to both files).
    if matching_contacts:
        for contact in matching_contacts:
            already_contact_note = any(
                d["entity"] == contact and d["type"] == "contact_note"
                for d in destinations
            )
            if not already_contact_note:
                destinations.append({
                    "type":         "contact_note",
                    "file":         "data/networking.md",
                    "entity":       contact,
                    "matched_name": contact,
                })

    # ── 3. Pipeline note ──────────────────────────────────────────────────
    pipeline_cos = [(name, slug) for name, slug, in_pipe in matching_companies if in_pipe]
    if pipeline_cos and _contains_any(note, DECISION_STATUS_KEYWORDS):
        for name, slug in pipeline_cos:
            destinations.append({
                "type":   "pipeline_note",
                "file":   "data/job-pipeline.md",
                "entity": name,
                "slug":   slug,
            })

    # ── 4. Company note ───────────────────────────────────────────────────
    if matching_companies:
        for name, slug, in_pipe in matching_companies:
            # Add company_note unless pipeline_note already covers it
            already_pipeline = any(
                d.get("slug") == slug and d["type"] == "pipeline_note"
                for d in destinations
            )
            # Add company_note if not already covered by a pipeline_note
            # (pipeline_note → job-pipeline.md; company_note → company-notes/<slug>.md — different files)
            destinations.append({
                "type":   "company_note",
                "file":   f"data/company-notes/{slug}.md",
                "entity": name,
                "slug":   slug,
            })

    # If we already have specific destinations, we're done (no need for fallbacks)
    if destinations:
        return {
            "note":         note,
            "destinations": destinations,
            "ambiguous":    False,
            "warnings":     warnings,
        }

    # ── 5. Profile update ─────────────────────────────────────────────────
    if _contains_any(note, PROFILE_KEYWORDS):
        return {
            "note": note,
            "destinations": [{
                "type": "profile_update",
                "file": "data/profile.md",
                "entity": None,
            }],
            "ambiguous": False,
            "warnings":  warnings,
        }

    # ── 6. Decision ───────────────────────────────────────────────────────
    if _contains_any(note, DECISION_KEYWORDS):
        return {
            "note": note,
            "destinations": [{
                "type": "decision",
                "file": "data/notes.md",
                "entity": None,
            }],
            "ambiguous": False,
            "warnings":  warnings,
        }

    # ── 7. Raw capture ────────────────────────────────────────────────────
    if _url_only(note) or _contains_any(note, RAW_CAPTURE_KEYWORDS):
        return {
            "note": note,
            "destinations": [{
                "type": "raw_capture",
                "file": "inbox/",
                "entity": None,
            }],
            "ambiguous": False,
            "warnings":  warnings,
        }

    # ── 8. General note (fallback) ────────────────────────────────────────
    return {
        "note": note,
        "destinations": [{
            "type": "general_note",
            "file": "data/notes.md",
            "entity": None,
        }],
        "ambiguous": True,
        "warnings":  ["Could not classify — routed to data/notes.md"],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Classify a /remember note to its destination files. Read-only."
    )
    p.add_argument("--note", required=True,
                   help="The note text to classify.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root. Defaults to cwd.")
    return p.parse_args()


def main():
    args = parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    networking_content = read_file(repo_root / "data" / "networking.md")
    pipeline_content   = read_file(repo_root / "data" / "job-pipeline.md")

    contacts           = load_contacts(networking_content)
    pipeline_companies = load_pipeline_companies(pipeline_content)
    dossier_slugs      = load_dossier_slugs(repo_root / "output")

    result = classify_note(args.note, contacts, pipeline_companies, dossier_slugs)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
