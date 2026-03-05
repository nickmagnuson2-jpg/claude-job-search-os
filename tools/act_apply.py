#!/usr/bin/env python3
"""
act_apply.py — Atomic writes for /act Immediate Route buckets.

Subcommands:
  pipeline-add <company>    [--role ROLE] [--url URL] [--notes NOTES]
                            [--source-file FILENAME]
  contact-add <name>        [--company CO] [--role ROLE] [--content TEXT]
                            [--source-file FILENAME]
  notes-add --content TEXT  [--company-slug SLUG] [--source-file FILENAME]
  company-note-add <slug> --content TEXT [--context LABEL]
                            [--source-file FILENAME]

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd.
  --dry-run          Return JSON contract without writing.

Note: Todo mutations (done/add) are NOT in this script — the /act skill
delegates those to todo_write.py directly. Inbox deletion stays in the
skill body, gated on this script returning status:ok.

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "..."}
  Failure: {"status": "error", "message": "...", "code": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python tools/act_apply.py pipeline-add "OpenAI" --url https://... --repo-root .
  PYTHONIOENCODING=utf-8 python tools/act_apply.py contact-add "Sarah Chen" --company Headway --repo-root .
  PYTHONIOENCODING=utf-8 python tools/act_apply.py notes-add --content "Random note" --repo-root .
  PYTHONIOENCODING=utf-8 python tools/act_apply.py company-note-add "amae-health" --content "..." --context "inbound email" --repo-root .
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

PIPELINE_FILE      = "data/job-pipeline.md"
NETWORKING_FILE    = "data/networking.md"
NOTES_FILE         = "data/notes.md"
COMPANY_NOTES_DIR  = "data/company-notes"

PIPELINE_HEADER = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
PIPELINE_SEP    = "| --- | --- | --- | --- | --- | --- | --- | --- |"

CONTACTS_HEADER = "| Name | Company | Role | Relationship | Added | Last Interaction | Email |"
CONTACTS_SEP    = "| --- | --- | --- | --- | --- | --- | --- |"


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


def is_data_row(line: str) -> bool:
    if not line.startswith("|"):
        return False
    if is_sep_row(line):
        return False
    # Skip known header rows
    if re.match(r"^\|\s*(Company|Name|---)\s*\|", line):
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


def table_insert_pos(lines: list, sec_start: int, sec_end: int) -> int:
    last = -1
    for i in range(sec_start, sec_end):
        if is_data_row(lines[i]):
            last = i
    if last != -1:
        return last + 1
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|") and "---" in lines[i]:
            return i + 1
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|"):
            return i + 1
    return sec_end


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_pipeline_add(args, pipeline_path: Path, dry_run: bool) -> None:
    today   = date.today().strftime("%Y-%m-%d")
    role    = args.role        if args.role    else "—"
    url     = args.url         if args.url     else "—"
    source  = args.source_file if args.source_file else ""
    base_notes = args.notes if args.notes else "—"
    notes = f"{base_notes} | Added from inbox/{source}" if source else base_notes

    next_action = "Run /research-company, then /generate-cv"
    row = f"| {args.company} | {role} | Researching | {today} | {next_action} | — | {notes} | {url} |"

    if dry_run:
        out_ok("pipeline_add", f"Would add pipeline entry: {args.company}",
               dry_run=True, would_mutate=[{"file": str(pipeline_path), "row": row}])
        return

    content = read_file(pipeline_path)
    if not content:
        out_error(f"File not found or empty: {pipeline_path}", "file_not_found")

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    act_start, act_end = find_section(lines, r"^##\s+Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-pipeline.md", "missing_section")

    pos = table_insert_pos(lines, act_start, act_end)
    lines.insert(pos, row)

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(pipeline_path, new_content)

    out_ok("pipeline_add", f"Added pipeline entry: {args.company} | {role}",
           company=args.company, role=role)


def cmd_contact_add(args, networking_path: Path, dry_run: bool) -> None:
    today   = date.today().strftime("%Y-%m-%d")
    company = args.company     if args.company     else "—"
    role    = args.role        if args.role        else "—"
    source  = args.source_file if args.source_file else ""
    content_text = args.content if args.content else ""

    contact_row = (
        f"| {args.name} | {company} | {role} | other | {today} | — | — |"
    )
    log_source = f"inbox/{source}" if source else "inbox"
    interaction_summary = f"Captured from {log_source}"

    if dry_run:
        out_ok("contact_add", f"Would add contact: {args.name}",
               dry_run=True, would_mutate=[{"file": str(networking_path)}])
        return

    content = read_file(networking_path)
    if not content:
        out_error(f"File not found or empty: {networking_path}", "file_not_found")

    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]

    # Add row to Contacts table
    contacts_start, contacts_end = find_section(lines, r"^##\s+Contacts")
    if contacts_start == -1:
        out_error("Could not find ## Contacts section in networking.md", "missing_section")

    pos = table_insert_pos(lines, contacts_start, contacts_end)
    lines.insert(pos, contact_row)

    # Add interaction log entry
    log_start, log_end = find_section(lines, r"^##\s+Interaction\s+Log")
    company_part = company if company != "—" else None
    heading = f"### {args.name} — {company_part}" if company_part else f"### {args.name}"
    entry_lines = [
        heading,
        "",
        f"#### {today} | other | {interaction_summary}",
        "",
    ]
    if content_text:
        for cl in content_text.splitlines():
            entry_lines.append(f"> {cl}" if cl else ">")
        entry_lines.append("")
    entry_lines.append("**Follow-up:** Review and update relationship type, add outreach if appropriate.")
    entry_lines.append("")

    if log_start == -1:
        lines.append("")
        lines.append("## Interaction Log")
        lines.append("")
        for el in entry_lines:
            lines.append(el)
    else:
        insert_at = log_end
        for j, el in enumerate(entry_lines):
            lines.insert(insert_at + j, el)

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(networking_path, new_content)

    out_ok("contact_add", f"Added contact: {args.name} | {company}",
           name=args.name, company=company)


def cmd_notes_add(args, repo_root: Path, dry_run: bool) -> None:
    today = date.today().strftime("%Y-%m-%d")
    source = args.source_file if args.source_file else ""

    if dry_run:
        target = (
            f"data/company-notes/{args.company_slug}.md"
            if args.company_slug else NOTES_FILE
        )
        out_ok("notes_add", "Would write note",
               dry_run=True, would_mutate=[{"file": target}])
        return

    if args.company_slug:
        # Write to data/company-notes/<slug>.md
        slug = args.company_slug
        path = repo_root / "data" / "company-notes" / f"{slug}.md"
        content = read_file(path)
        source_tag = f"From inbox/{source}" if source else "From inbox"
        entry = f"## {today} | {source_tag}\n{args.content}\n"

        if not content:
            new_content = (
                f"# {slug} — Notes\n\n"
                "> Running log of raw notes, call prep, and observations.\n"
                "> Newest entries at the top.\n\n"
                "---\n\n"
                + entry
            )
        else:
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
        out_ok("notes_add", f"Written to company-notes/{slug}.md",
               file=str(path))
    else:
        # Write to data/notes.md under ## From Inbox
        path = repo_root / NOTES_FILE
        content = read_file(path)
        if not content:
            content = (
                "# Notes\n\n"
                "> General notes, decisions, and unroutable captures.\n\n"
                "## Decisions\n\n## Notes\n\n## From Inbox\n"
            )

        lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
        sec_start, sec_end = find_section(lines, r"^##\s+From\s+Inbox")
        if sec_start == -1:
            lines.append("")
            lines.append("## From Inbox")
            lines.append("")
            sec_end = len(lines)

        source_tag = f"from inbox/{source}" if source else "from inbox"
        entry = f"**{today} — {source_tag}:**\n{args.content}"
        lines.insert(sec_end, entry)
        lines.insert(sec_end + 1, "")

        new_content = "\n".join(lines) + "\n"
        write_atomic(path, new_content)
        out_ok("notes_add", f"Written to {NOTES_FILE} (## From Inbox)",
               file=str(path))


def cmd_company_note_add(args, repo_root: Path, dry_run: bool) -> None:
    """Prepend a dated entry to data/company-notes/<slug>.md.

    Used by /act Gmail triage to route inbound emails into company context so
    /follow-up and /draft-email can read them automatically.

    Header format: ## YYYY-MM-DD | <context>
    Entries are prepended (newest first) per the company-notes convention.
    """
    today   = date.today().strftime("%Y-%m-%d")
    slug    = args.slug
    context = args.context if args.context else "inbound email"
    source  = args.source_file if args.source_file else ""

    header_label = f"{context} — {source}" if source else context
    entry = f"## {today} | {header_label}\n\n{args.content}\n"

    path = repo_root / COMPANY_NOTES_DIR / f"{slug}.md"

    if dry_run:
        out_ok("company_note_add", f"Would prepend note to company-notes/{slug}.md",
               dry_run=True, would_mutate=[{"file": str(path)}])
        return

    content = read_file(path)
    if not content:
        new_content = (
            f"# {slug} — Notes\n\n"
            "> Running log of raw notes, call prep, and observations.\n"
            "> Newest entries at the top.\n\n"
            "---\n\n"
            + entry
        )
    else:
        lines = content.splitlines()
        # Find the first ## YYYY-* heading to prepend before it (newest first)
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if re.match(r"^##\s+\d{4}", line):
                insert_at = i
                break
        lines.insert(insert_at, "")
        for j, el in enumerate(entry.rstrip("\n").splitlines()):
            lines.insert(insert_at + 1 + j, el)
        new_content = "\n".join(lines) + "\n"

    write_atomic(path, new_content)
    out_ok("company_note_add", f"Prepended note to company-notes/{slug}.md",
           slug=slug, file=str(path))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Atomic writes for /act Immediate Route buckets.")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--dry-run", action="store_true")

    sub = p.add_subparsers(dest="command")

    pa = sub.add_parser("pipeline-add")
    pa.add_argument("company")
    pa.add_argument("--role",        default=None)
    pa.add_argument("--url",         default=None)
    pa.add_argument("--notes",       default=None)
    pa.add_argument("--source-file", dest="source_file", default=None)

    ca = sub.add_parser("contact-add")
    ca.add_argument("name")
    ca.add_argument("--company",     default=None)
    ca.add_argument("--role",        default=None)
    ca.add_argument("--content",     default=None)
    ca.add_argument("--source-file", dest="source_file", default=None)

    na = sub.add_parser("notes-add")
    na.add_argument("--content",      required=True)
    na.add_argument("--company-slug", dest="company_slug", default=None)
    na.add_argument("--source-file",  dest="source_file",  default=None)

    cna = sub.add_parser("company-note-add")
    cna.add_argument("slug")
    cna.add_argument("--content",     required=True)
    cna.add_argument("--context",     default=None)
    cna.add_argument("--source-file", dest="source_file", default=None)

    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.command:
        out_error("Usage: act_apply.py <pipeline-add|contact-add|notes-add|company-note-add> [args...]")

    repo_root       = Path(args.repo_root) if args.repo_root else Path.cwd()
    pipeline_path   = repo_root / PIPELINE_FILE
    networking_path = repo_root / NETWORKING_FILE

    if args.command == "pipeline-add":
        cmd_pipeline_add(args, pipeline_path, args.dry_run)
    elif args.command == "contact-add":
        cmd_contact_add(args, networking_path, args.dry_run)
    elif args.command == "notes-add":
        cmd_notes_add(args, repo_root, args.dry_run)
    elif args.command == "company-note-add":
        cmd_company_note_add(args, repo_root, args.dry_run)
    else:
        out_error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
