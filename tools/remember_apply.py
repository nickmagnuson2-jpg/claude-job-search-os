#!/usr/bin/env python3
"""
remember_apply.py — Apply classified routing destinations from remember_classify.py.

Accepts:
  --note TEXT              Note text to write
  --note-file PATH         File containing note text (Windows shell-escaping safety)
  --destinations '[...]'   JSON array of destination dicts from remember_classify.py
  --destinations-file PATH File containing destinations JSON (Windows shell-escaping safety)
  --repo-root PATH         Repository root. Defaults to cwd.
  --dry-run                Return JSON contract without writing.

Destination types handled:
  contact_note    → data/networking.md — append to contact's section
  outreach_reply  → data/outreach-log.md — update Status to Replied
  pipeline_note   → data/job-pipeline.md — append to Notes cell
  company_note    → data/company-notes/<slug>.md — prepend entry
  profile_update  → data/profile.md — append under ## Session Notes
  decision        → data/decisions.md — prepend dated entry (newest-first log)
  general_note    → data/notes.md — append under ## Notes
  raw_capture     → inbox/ — create new file
  source_article  → data/source-articles/YYYYMMDD-<slug>.md — voice-pure commonplace-book entry
  deferred_idea   → data/inbox.md — prepend dated ## YYYY-MM-DD | <title> block

Output: JSON to stdout
  Single:  {"status": "ok", "action": "<type>", "summary": "..."}
  Multi:   {"status": "ok", "action": "multi_write", "results": [...]}
  Error:   {"status": "error", "message": "...", "code": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/remember_apply.py \\
    --note "Jane replied" \\
    --destinations '[{"type":"contact_note","entity":"Jane Doe","file":"data/networking.md"}]' \\
    --repo-root .
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

NOTES_FILE      = "data/notes.md"
DECISIONS_FILE  = "data/decisions.md"
ACCOMPLISHMENTS_FILE = "data/accomplishments.md"
NETWORKING_FILE = "data/networking.md"
PIPELINE_FILE   = "data/job-pipeline.md"
PROFILE_FILE    = "data/profile.md"
OUTREACH_FILE   = "data/outreach-log.md"


# ---------------------------------------------------------------------------
# I/O helpers (self-contained)
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def out_ok(action: str, summary: str, **extra) -> None:
    d = {"status": "ok", "action": action, "summary": summary}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    d = {"status": "error", "message": message, "code": code}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Table row helpers
# ---------------------------------------------------------------------------

def is_sep_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|[-: |]+\|\s*$", line))


def is_data_row_generic(line: str) -> bool:
    if not line.startswith("|"):
        return False
    if is_sep_row(line):
        return False
    return True


def parse_cols(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def find_section(lines: list, pattern: str) -> tuple:
    start = -1
    for i, line in enumerate(lines):
        if re.match(pattern, line, re.I):
            start = i
            break
    if start == -1:
        return (-1, -1)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return (start, end)


def ensure_section(lines: list, heading: str) -> int:
    """Ensure a ## heading exists at end of doc. Returns its start index."""
    pattern = r"^##\s+" + re.escape(heading.lstrip("# ").strip())
    start, _ = find_section(lines, pattern)
    if start != -1:
        return start
    lines.append("")
    lines.append(f"## {heading.lstrip('# ').strip()}")
    lines.append("")
    return len(lines) - 3


def section_end(lines: list, start: int) -> int:
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return end


# ---------------------------------------------------------------------------
# Per-destination writers
# ---------------------------------------------------------------------------

def apply_contact_note(note: str, dest: dict, repo_root: Path) -> dict:
    """Append [date] note to matching contact's section in networking.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    entity = dest.get("entity", "")
    path = repo_root / NETWORKING_FILE

    content = read_file(path)
    if not content:
        return {"status": "error", "type": "contact_note",
                "message": f"networking.md not found or empty"}

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]

    # Find contact's log section
    name_lower = entity.lower().strip()
    sec_start = -1
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        m = re.match(r"^###\s+(?:\[ARCHIVED\]\s+)?(.+?)(?:\s+[—–]|$)", line)
        if m and m.group(1).strip().lower() == name_lower:
            sec_start = i
            break

    entry = f"[{today}] {note}"

    if sec_start == -1:
        # Contact section not found — append note to general section or create
        lines.append(f"### {entity}")
        lines.append("")
        lines.append(entry)
        lines.append("")
    else:
        # Insert after heading (+ blank line)
        insert_at = sec_start + 1
        if insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        lines.insert(insert_at, entry)
        lines.insert(insert_at + 1, "")

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": "contact_note", "file": str(path), "entity": entity}


def apply_outreach_reply(note: str, dest: dict, repo_root: Path) -> dict:
    """Update most recent Sent/Drafted row for entity in outreach-log.md to Replied."""
    entity = dest.get("entity", "")
    path = repo_root / OUTREACH_FILE

    content = read_file(path)
    if not content:
        # Fallback to networking.md
        result = apply_contact_note(note, dest, repo_root)
        result["warning"] = "No outreach-log.md found — logged to networking.md"
        return result

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    name_lower = entity.lower().strip()

    # Find most recent Sent/Drafted row matching entity
    # Outreach log columns: Date | Recipient | Company | Type | Subject | Status | Notes
    last_match = -1
    for i, line in enumerate(lines):
        if not is_data_row_generic(line):
            continue
        cols = parse_cols(line)
        if len(cols) < 6:
            continue
        recipient = cols[1].lower()
        status = cols[5]
        if name_lower in recipient and status in ("Sent", "Drafted", "Sent ", "Drafted "):
            last_match = i

    if last_match == -1:
        # Fallback to contact note
        result = apply_contact_note(note, dest, repo_root)
        result["warning"] = f"No Sent/Drafted outreach row found for {entity} — logged to networking.md"
        return result

    cols = parse_cols(lines[last_match])
    while len(cols) < 7:
        cols.append("—")
    cols[5] = "Replied"
    lines[last_match] = "| " + " | ".join(cols) + " |"

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": "outreach_reply", "file": str(path), "entity": entity}


def apply_pipeline_note(note: str, dest: dict, repo_root: Path) -> dict:
    """Append [date]: note to Notes cell for matching company in pipeline."""
    today = datetime.now().strftime("%Y-%m-%d")
    entity = dest.get("entity", "")
    path = repo_root / PIPELINE_FILE

    content = read_file(path)
    if not content:
        return {"status": "error", "type": "pipeline_note",
                "message": "job-pipeline.md not found or empty"}

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    entity_lower = entity.lower().strip()

    updated = False
    for i, line in enumerate(lines):
        if not is_data_row_generic(line):
            continue
        cols = parse_cols(line)
        if not cols or cols[0].lower() != entity_lower:
            continue
        # Notes is column 6 (0-indexed)
        while len(cols) < 8:
            cols.append("—")
        existing = cols[6]
        cols[6] = (
            f"{existing} | [{today}]: {note}"
            if existing not in ("—", "", "–")
            else f"[{today}]: {note}"
        )
        lines[i] = "| " + " | ".join(cols) + " |"
        updated = True
        break

    if not updated:
        return {"status": "error", "type": "pipeline_note",
                "message": f"No pipeline entry found for: {entity}"}

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": "pipeline_note", "file": str(path), "entity": entity}


def apply_company_note(note: str, dest: dict, repo_root: Path) -> dict:
    """Prepend ## YYYY-MM-DD | [context] entry to data/company-notes/<slug>.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    slug = dest.get("slug", "")
    entity = dest.get("entity", slug)
    if not slug:
        return {"status": "error", "type": "company_note", "message": "No slug in destination"}

    path = repo_root / "data" / "company-notes" / f"{slug}.md"
    content = read_file(path)

    entry = f"## {today} | General\n{note}\n"

    if not content:
        new_content = (
            f"# {entity} — Notes\n\n"
            "> Running log of raw notes, call prep, and observations.\n"
            "> Newest entries at the top. One section per date + context.\n\n"
            "---\n\n"
            + entry
        )
    else:
        # Prepend after the header block (before first ## entry if present, else at end)
        # Find first "## " line that is not the title
        lines = content.splitlines()
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if re.match(r"^##\s+\d{4}", line):
                insert_at = i
                break
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, entry.rstrip("\n"))
        new_content = "\n".join(lines) + "\n"

    write_atomic(path, new_content)
    return {"status": "ok", "type": "company_note", "file": str(path), "entity": entity}


def apply_profile_update(note: str, dest: dict, repo_root: Path) -> dict:
    """Append note under ## Session Notes in data/profile.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = repo_root / PROFILE_FILE

    content = read_file(path)
    if not content:
        content = "# Profile\n\n"

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    session_start, session_end = find_section(lines, r"^##\s+Session\s+Notes")
    if session_start == -1:
        lines.append("")
        lines.append("## Session Notes")
        lines.append("")
        session_end = len(lines)

    lines.insert(session_end, f"**{today}:** {note}")
    lines.insert(session_end + 1, "")

    new_content = "\n".join(lines) + "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": "profile_update", "file": str(path)}


def apply_notes_md(note: str, dest_type: str, repo_root: Path) -> dict:
    """Append under ## Decisions or ## Notes in data/notes.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = repo_root / NOTES_FILE

    content = read_file(path)
    if not content:
        content = (
            "# Job Search Notes\n\n"
            "> Captured by `/remember`. Raw notes and decisions from sessions.\n\n"
            "## Decisions\n\n## Notes\n"
        )

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]

    heading = "Decisions" if dest_type == "decision" else "Notes"
    sec_start, sec_end = find_section(lines, r"^##\s+" + heading)
    if sec_start == -1:
        lines.append("")
        lines.append(f"## {heading}")
        lines.append("")
        sec_end = len(lines)

    lines.insert(sec_end, f"**{today}:** {note}")
    lines.insert(sec_end + 1, "")

    new_content = "\n".join(lines) + "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": dest_type, "file": str(path)}


# ---------------------------------------------------------------------------
# Dated chronological logs (decisions.md / accomplishments.md), newest-first
# ---------------------------------------------------------------------------

LOG_MARKER = "<!-- NEW ENTRIES BELOW"

LOG_HEADERS = {
    "decision": (
        DECISIONS_FILE,
        "# Job-Search Decisions Log\n\n"
        "> Chronological record of strategic search decisions, newest first. "
        "Companion to goals.md. Each entry: what I decided, what drove it, what changes if it's wrong. "
        "Captured via `/remember` or appended directly. Gitignored (personal).\n\n"
        "<!-- NEW ENTRIES BELOW (newest first) -->\n",
    ),
    "accomplishment": (
        ACCOMPLISHMENTS_FILE,
        "# Job-Search Accomplishments Log\n\n"
        "> Job-search-process wins, newest first. NOT career-history bullets (those live in data/projects/). "
        "Substrate for /weekly-review and retros. Captured via `/remember` or appended directly. Gitignored (personal).\n\n"
        "<!-- NEW ENTRIES BELOW (newest first) -->\n",
    ),
}


def apply_dated_log(note: str, dest_type: str, repo_root: Path) -> dict:
    """Prepend a dated entry (newest-first) to a chronological log file.

    The note may be multi-line (the /remember skill assembles the structured
    body before calling this); it is written verbatim under a `## YYYY-MM-DD`
    header inserted right after the NEW-ENTRIES marker.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rel_path, header = LOG_HEADERS[dest_type]
    path = repo_root / rel_path

    content = read_file(path)
    if not content:
        content = header

    lines = content.splitlines()
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.startswith(LOG_MARKER):
            insert_at = i + 1
            break

    entry = ["", f"## {today}", "", note]
    if insert_at is None:
        lines.extend(entry)
    else:
        lines[insert_at:insert_at] = entry

    new_content = "\n".join(lines).rstrip("\n") + "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": dest_type, "file": str(path)}


def apply_raw_capture(note: str, dest: dict, repo_root: Path) -> dict:
    """Create inbox/YYYYMMDD-HHMMSS-<slug>.md."""
    now = datetime.now()
    date_str = now.strftime("%Y%m%d-%H%M%S")
    # Slug: first 4 words, lowercase, hyphenated
    words = re.sub(r"[^\w\s]", "", note.lower()).split()[:4]
    slug = "-".join(words) if words else "note"
    filename = f"{date_str}-{slug}.md"
    path = repo_root / "inbox" / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    file_content = (
        f"# {note[:80]}\n\n"
        f"Captured: {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"{note}\n\n---\n*Route with `/act` when ready.*\n"
    )
    write_atomic(path, file_content)
    return {"status": "ok", "type": "raw_capture", "file": str(path)}


def _title_from_note(note: str, max_words: int = 8, max_chars: int = 70) -> str:
    """First N words of the note, cleaned up, for use as a heading/title."""
    stripped = note.strip()
    # Drop a leading "interesting:" / "later:" style label if present.
    stripped = re.sub(
        r"^\s*(save( this)?\s+as\s+interesting|interesting|later|park this)\s*[:\-]\s*",
        "", stripped, flags=re.I,
    )
    words = stripped.split()
    title = " ".join(words[:max_words])
    if len(words) > max_words or len(title) > max_chars:
        title = title[:max_chars].rstrip()
        title += "..."
    return title or "Untitled"


def _slug_from_note(note: str, max_words: int = 6) -> str:
    """Lowercase-hyphenated slug from the first few words of the note."""
    stripped = re.sub(
        r"^\s*(save( this)?\s+as\s+interesting|interesting|later|park this)\s*[:\-]\s*",
        "", note.strip(), flags=re.I,
    )
    stripped = re.sub(r"https?://\S+", "", stripped)  # drop URLs from the slug
    words = re.sub(r"[^\w\s]", "", stripped.lower()).split()[:max_words]
    return "-".join(words) if words else "note"


def apply_source_article(note: str, dest: dict, repo_root: Path) -> dict:
    """Create a voice-pure commonplace-book entry in data/source-articles/.

    Matches the entry structure in data/commonplace-book.md: BLUF, an empty
    'My take (voice-pure)' section (never authored by Claude), a separate
    'Claude's read' section, and a Connections stub.
    """
    today = datetime.now()
    date_str = today.strftime("%Y%m%d")
    slug = _slug_from_note(note)
    title = _title_from_note(note)

    dest_dir = repo_root / "data" / "source-articles"
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{date_str}-{slug}.md"
    path = dest_dir / filename
    # Avoid clobbering same-day duplicate slugs.
    n = 2
    while path.exists():
        path = dest_dir / f"{date_str}-{slug}-{n}.md"
        n += 1

    url_match = re.search(r"https?://\S+", note)
    url = url_match.group(0) if url_match else "TODO"

    file_content = (
        f"# {title}\n\n"
        f"> **Source:** TODO\n"
        f"> **Author:** TODO\n"
        f"> **Published:** TODO\n"
        f"> **URL:** {url}\n"
        f"> **Fetched:** {today.strftime('%Y-%m-%d')} (via `/remember`)\n"
        f"> **Evidence tier:** TODO\n\n"
        f"## BLUF\n\n"
        f"{note}\n\n"
        f"## My take (voice-pure)\n\n"
        f"<!-- voice: pure-voice, my words only, not Claude's -->\n\n"
        f"_(unfilled, backlog signal)_\n\n"
        f"## Claude's read\n\n"
        f"_(pending)_\n\n"
        f"## Connections\n\n"
    )
    write_atomic(path, file_content)
    return {"status": "ok", "type": "source_article", "file": str(path)}


def apply_deferred_idea(note: str, dest: dict, repo_root: Path) -> dict:
    """Prepend a dated ## YYYY-MM-DD | <title> block to data/inbox.md.

    Deferred ideas ("come back to this later") route to the inbox rather
    than the general_note fallback to data/notes.md — see global CLAUDE.md
    "The /remember Skill" and feedback_remember_deferred_goes_to_inbox.md.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = repo_root / "data" / "inbox.md"

    content = read_file(path)
    if not content:
        content = (
            "# Inbox\n\n"
            "<!-- Items captured via /remember. Review and route to appropriate files periodically. -->\n"
        )

    title = _title_from_note(note)
    lines = content.splitlines()
    entry = ["", f"## {today} | {title}", "", note, ""]

    insert_at = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("<!-- Items captured"):
            insert_at = i + 1
            break

    if insert_at is None:
        lines.extend(entry)
    else:
        lines[insert_at:insert_at] = entry

    new_content = "\n".join(lines).rstrip("\n") + "\n"
    write_atomic(path, new_content)
    return {"status": "ok", "type": "deferred_idea", "file": str(path)}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLERS = {
    "contact_note":   apply_contact_note,
    "outreach_reply": apply_outreach_reply,
    "pipeline_note":  apply_pipeline_note,
    "company_note":   apply_company_note,
    "profile_update": apply_profile_update,
    "decision":       lambda n, d, r: apply_dated_log(n, "decision", r),
    "accomplishment": lambda n, d, r: apply_dated_log(n, "accomplishment", r),
    "general_note":   lambda n, d, r: apply_notes_md(n, "general_note", r),
    "raw_capture":    apply_raw_capture,
    "source_article": apply_source_article,
    "deferred_idea":  apply_deferred_idea,
}


def apply_destinations(note: str, destinations: list, repo_root: Path, dry_run: bool) -> None:
    if dry_run:
        out_ok("dry_run", f"Would write to {len(destinations)} destination(s)",
               dry_run=True,
               would_mutate=[d.get("file", d.get("type", "unknown")) for d in destinations])
        return

    if not destinations:
        out_error("No destinations provided", "no_destinations")

    results = []
    for dest in destinations:
        dtype = dest.get("type", "general_note")
        handler = HANDLERS.get(dtype)
        if handler is None:
            results.append({"status": "error", "type": dtype,
                            "message": f"Unknown destination type: {dtype}"})
            continue
        try:
            result = handler(note, dest, repo_root)
            results.append(result)
        except Exception as e:
            results.append({"status": "error", "type": dtype, "message": str(e)})

    if len(results) == 1:
        r = results[0]
        if r["status"] == "error":
            out_error(r["message"], r.get("code", "handler_error"))
        else:
            out_ok(r["type"], f"Written to {r.get('file', r['type'])}", **{
                k: v for k, v in r.items() if k not in ("status", "type")
            })
    else:
        any_error = any(r["status"] == "error" for r in results)
        out_ok("multi_write",
               f"Written to {sum(1 for r in results if r['status'] == 'ok')} of {len(results)} destinations",
               results=results,
               has_errors=any_error)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Apply remember_classify destinations to data files.")
    p.add_argument("--note",               default=None, help="Note text")
    p.add_argument("--note-file",          default=None, help="File containing note text")
    p.add_argument("--destinations",       default=None, help="JSON destinations array")
    p.add_argument("--destinations-file",  default=None, help="File containing destinations JSON")
    p.add_argument("--repo-root",          default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve note text
    if args.note_file:
        note = Path(args.note_file).read_text(encoding="utf-8").strip()
    elif args.note:
        note = args.note
    else:
        out_error("Provide --note or --note-file", "missing_note")

    # Resolve destinations
    if args.destinations_file:
        raw = Path(args.destinations_file).read_text(encoding="utf-8").strip()
    elif args.destinations:
        raw = args.destinations
    else:
        out_error("Provide --destinations or --destinations-file", "missing_destinations")

    try:
        destinations = json.loads(raw)
    except json.JSONDecodeError as e:
        out_error(f"Invalid destinations JSON: {e}", "invalid_json")

    if not isinstance(destinations, list):
        out_error("Destinations must be a JSON array", "invalid_destinations")

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    apply_destinations(note, destinations, repo_root, args.dry_run)


if __name__ == "__main__":
    main()
