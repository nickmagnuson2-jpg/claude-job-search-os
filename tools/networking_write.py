#!/usr/bin/env python3
"""
networking_write.py — Atomic mutations for data/networking.md.

Subcommands:
  add <name> [--company CO] [--role ROLE] [--relationship TYPE]
  log <name> --date DATE --type TYPE --summary SUMMARY
             [--followup TEXT] [--content TEXT]
  remove <name> [--role ROLE]

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd.
  --dry-run          Return JSON contract without writing.

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "..."}
  Failure: {"status": "error", "message": "...", "code": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python tools/networking_write.py add "Jane Doe" --company Acme --repo-root .
  PYTHONIOENCODING=utf-8 python tools/networking_write.py log "Jane Doe" --date 2026-02-28 --type email --summary "Replied" --repo-root .
  PYTHONIOENCODING=utf-8 python tools/networking_write.py remove "Jane Doe" --repo-root .
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

NETWORKING_FILE = "data/networking.md"
TODOS_FILE      = "data/job-todos.md"

CONTACTS_HEADER = "| Name | Company | Role | Relationship | Added | Last Interaction | Email |"
CONTACTS_SEP    = "| --- | --- | --- | --- | --- | --- | --- |"


# ---------------------------------------------------------------------------
# I/O helpers (self-contained — no cross-script imports)
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
    if re.match(r"^\|\s*(Name|---)\s*\|", line):
        return False
    return True


def parse_cols(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def fmt_contact_row(name, company, role, relationship, added, last_interaction, email="—"):
    return (f"| {name} | {company} | {role} | {relationship} | "
            f"{added} | {last_interaction} | {email} |")


# ---------------------------------------------------------------------------
# Section navigation
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


def find_contact_log_section(lines: list, name: str) -> tuple:
    """Find `### Name` or `### Name — Company` section in Interaction Log.
    Returns (start_idx, end_idx) or (-1, -1).
    """
    name_lower = name.lower().strip()
    start = -1
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        # Match "### Name" or "### Name — Company"
        m = re.match(r"^###\s+(.+?)(?:\s+[—–]|$)", line)
        if m and m.group(1).strip().lower() == name_lower:
            start = i
            break
    if start == -1:
        return (-1, -1)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        # End on next ### or ## or EOF
        if lines[i].startswith("## ") or lines[i].startswith("### "):
            end = i
            break
    return (start, end)


# ---------------------------------------------------------------------------
# File load / save
# ---------------------------------------------------------------------------

def load_networking(path: Path) -> tuple:
    content = read_file(path)
    if not content:
        out_error(f"File not found or empty: {path}", "file_not_found")
    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    return content, lines


def save_lines(path: Path, lines: list, original_content: str) -> None:
    content = "\n".join(lines)
    if original_content.endswith("\n"):
        content += "\n"
    write_atomic(path, content)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(args, networking_path: Path, dry_run: bool) -> None:
    today     = date.today().strftime("%Y-%m-%d")
    company   = args.company      if args.company      else "—"
    role      = args.role         if args.role         else "—"
    rel       = args.relationship if args.relationship else "peer"

    stub_heading = (
        f"### {args.name} — {company}"
        if company != "—"
        else f"### {args.name}"
    )

    if dry_run:
        row = fmt_contact_row(args.name, company, role, rel, today, "—")
        out_ok("add", f"Would add contact: {args.name}",
               dry_run=True,
               would_mutate=[{"file": str(networking_path), "row": row}])
        return

    content, lines = load_networking(networking_path)

    # Duplicate check (case-insensitive name)
    contacts_start, contacts_end = find_section(lines, r"^##\s+Contacts")
    if contacts_start != -1:
        for i in range(contacts_start, contacts_end):
            if is_data_row(lines[i]):
                cols = parse_cols(lines[i])
                if cols and cols[0].lower() == args.name.lower():
                    out_ok("duplicate_warning",
                           f"{args.name} already exists in contacts",
                           existing_company=cols[1] if len(cols) > 1 else "")
                    return

    # Append to Contacts table
    if contacts_start == -1:
        # Create Contacts section
        lines.append("")
        lines.append("## Contacts")
        lines.append("")
        lines.append(CONTACTS_HEADER)
        lines.append(CONTACTS_SEP)
        contacts_start = len(lines) - 3
        contacts_end   = len(lines)

    row = fmt_contact_row(args.name, company, role, rel, today, "—")
    pos = table_insert_pos(lines, contacts_start, contacts_end)
    lines.insert(pos, row)

    # Create Interaction Log stub
    log_start, log_end = find_section(lines, r"^##\s+Interaction\s+Log")
    if log_start == -1:
        lines.append("")
        lines.append("## Interaction Log")
        lines.append("")
        lines.append(stub_heading)
        lines.append("")
    else:
        # Insert stub at end of Interaction Log section
        insert_at = log_end
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, stub_heading)
        lines.insert(insert_at + 2, "")

    save_lines(networking_path, lines, content)

    out_ok("add", f"Added contact: {args.name}",
           name=args.name, company=company)


def cmd_log(args, networking_path: Path, repo_root: Path, dry_run: bool) -> None:
    today = date.today().strftime("%Y-%m-%d")
    log_date = args.date if args.date else today

    # Build the entry block
    header = f"#### {log_date} | {args.type} | {args.summary}"
    followup = args.followup if args.followup else "—"
    entry_lines = [header, ""]
    if args.content:
        for content_line in args.content.splitlines():
            entry_lines.append(f"> {content_line}" if content_line else ">")
        entry_lines.append("")
    entry_lines.append(f"**Follow-up:** {followup}")
    entry_lines.append("")

    if dry_run:
        out_ok("log", f"Would log interaction for {args.name}",
               dry_run=True,
               would_mutate=[{"file": str(networking_path), "entry": header}])
        return

    content, lines = load_networking(networking_path)

    # Verify contact exists in Contacts table
    contacts_start, contacts_end = find_section(lines, r"^##\s+Contacts")
    contact_row_idx = -1
    if contacts_start != -1:
        for i in range(contacts_start, contacts_end):
            if is_data_row(lines[i]):
                cols = parse_cols(lines[i])
                if cols and cols[0].lower() == args.name.lower():
                    contact_row_idx = i
                    break

    if contact_row_idx == -1:
        out_error(f"Contact not found: {args.name}", "not_found")

    # Update Last Interaction date in Contacts table
    cols = parse_cols(lines[contact_row_idx])
    while len(cols) < 7:
        cols.append("—")
    cols[5] = log_date
    lines[contact_row_idx] = fmt_contact_row(*cols[:7])

    # Find the contact's section in Interaction Log
    log_sec_start, log_sec_end = find_contact_log_section(lines, args.name)
    if log_sec_start == -1:
        # Section missing — create it
        log_section_start, log_section_end = find_section(lines, r"^##\s+Interaction\s+Log")
        if log_section_start == -1:
            lines.append("")
            lines.append("## Interaction Log")
            lines.append("")
            company_part = cols[1] if len(cols) > 1 and cols[1] not in ("—", "") else None
            heading = f"### {cols[0]} — {company_part}" if company_part else f"### {cols[0]}"
            lines.append(heading)
            lines.append("")
            insert_at = len(lines)
        else:
            insert_at = log_section_end
            company_part = cols[1] if len(cols) > 1 and cols[1] not in ("—", "") else None
            heading = f"### {cols[0]} — {company_part}" if company_part else f"### {cols[0]}"
            lines.insert(insert_at, "")
            lines.insert(insert_at + 1, heading)
            lines.insert(insert_at + 2, "")
            insert_at = insert_at + 3
    else:
        # Insert after section heading (+ blank line), i.e., newest first
        insert_at = log_sec_start + 1
        # Skip a blank line if present right after heading
        if insert_at < log_sec_end and lines[insert_at].strip() == "":
            insert_at += 1

    # Insert entry lines in order
    for j, eline in enumerate(entry_lines):
        lines.insert(insert_at + j, eline)

    save_lines(networking_path, lines, content)

    # Follow-up todo via subprocess (non-fatal if todos file missing)
    if args.followup:
        todos_path = repo_root / TODOS_FILE
        if todos_path.exists():
            try:
                todo_task = f"Follow up: {args.name} — {args.followup}"
                from datetime import timedelta
                due = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
                subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "todo_write.py"),
                     "add", todo_task, "Med", due, f"From networking log on {log_date}"],
                    capture_output=True, text=True, encoding="utf-8",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    cwd=str(repo_root),
                )
            except Exception:
                pass  # todo creation is best-effort

    out_ok("log", f"Logged interaction for {args.name}: {args.summary}",
           name=args.name, date=log_date)


def cmd_remove(args, networking_path: Path, dry_run: bool) -> None:
    if dry_run:
        out_ok("remove", f"Would archive contact: {args.name}",
               dry_run=True,
               would_mutate=[{"file": str(networking_path)}])
        return

    content, lines = load_networking(networking_path)

    # Remove from Contacts table
    contacts_start, contacts_end = find_section(lines, r"^##\s+Contacts")
    removed = False
    if contacts_start != -1:
        for i in range(contacts_start, contacts_end):
            if is_data_row(lines[i]):
                cols = parse_cols(lines[i])
                if cols and cols[0].lower() == args.name.lower():
                    lines.pop(i)
                    removed = True
                    break

    if not removed:
        out_error(f"Contact not found: {args.name}", "not_found")

    # Prepend [ARCHIVED] to interaction log section heading
    log_sec_start, _ = find_contact_log_section(lines, args.name)
    if log_sec_start != -1:
        lines[log_sec_start] = lines[log_sec_start].replace("### ", "### [ARCHIVED] ", 1)

    save_lines(networking_path, lines, content)

    out_ok("remove", f"Archived contact: {args.name}", name=args.name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Atomic mutations for data/networking.md.")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--dry-run", action="store_true")

    sub = p.add_subparsers(dest="command")

    add_p = sub.add_parser("add")
    add_p.add_argument("name")
    add_p.add_argument("--company",      default=None)
    add_p.add_argument("--role",         default=None)
    add_p.add_argument("--relationship", default=None)

    log_p = sub.add_parser("log")
    log_p.add_argument("name")
    log_p.add_argument("--date",     default=None)
    log_p.add_argument("--type",     default="other")
    log_p.add_argument("--summary",  required=True)
    log_p.add_argument("--followup", default=None)
    log_p.add_argument("--content",  default=None)

    rem_p = sub.add_parser("remove")
    rem_p.add_argument("name")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.command:
        out_error("Usage: networking_write.py <add|log|remove> [args...]")

    repo_root       = Path(args.repo_root) if args.repo_root else Path.cwd()
    networking_path = repo_root / NETWORKING_FILE

    if args.command == "add":
        cmd_add(args, networking_path, args.dry_run)
    elif args.command == "log":
        cmd_log(args, networking_path, repo_root, args.dry_run)
    elif args.command == "remove":
        cmd_remove(args, networking_path, args.dry_run)
    else:
        out_error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
