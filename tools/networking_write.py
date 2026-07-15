#!/usr/bin/env python3
"""
networking_write.py — Atomic mutations for data/networking.md.

Subcommands:
  add <name> [--company CO] [--role ROLE] [--relationship TYPE] [--force]
  log <name> --date DATE --type TYPE --summary SUMMARY
             [--followup TEXT] [--content TEXT]
  remove <name> [--role ROLE]

`add` blocks on a fuzzy name match to an existing contact (spelling variants like
"Jordan Sample"/"Jordan Samples", nickname vs full name, accents) with code
"possible_duplicate". Re-run with --force only if it is genuinely a different
person; otherwise `log` to the existing contact. Origin 2026-06-12.
Note: `log --followup` already auto-creates the follow-up todo — do not also add
one manually, or you double-write.

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd.
  --dry-run          Return JSON contract without writing.

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "..."}
  Failure: {"status": "error", "message": "...", "code": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/networking_write.py add "Jane Doe" --company Acme --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/networking_write.py log "Jane Doe" --date 2026-02-28 --type email --summary "Replied" --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/networking_write.py remove "Jane Doe" --repo-root .
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# Sibling import: fuzzy name-duplicate guard (stdlib-only module in tools/).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_dedup import find_near_duplicates  # noqa: E402

NETWORKING_FILE  = "data/networking.md"
TODOS_FILE       = "data/job-todos.md"
OUTREACH_FILE    = "data/outreach-log.md"

# Keywords in interaction summary that signal a reply was received
REPLY_SIGNALS = (
    "replied", "responded", "heard back", "call scheduled", "meeting set",
    "coffee scheduled", "phone screen", "interview scheduled", "got back",
    "reply", "called back", "texted back",
)

# Phrases where NICK is the one replying/sending (outbound). These must NOT flip
# the outreach-log to "Replied" — that status means the RECIPIENT replied, not
# that Nick replied to them. Bug origin 2026-06-18: logging "Replied to her
# intro" (Nick's own send) falsely flipped a Sent row to Replied.
OUTBOUND_REPLY_MARKERS = (
    "replied to", "reply to", "replying to", "responded to", "responding to",
    "my reply", "sent a reply", "sent my reply", "i replied", "nick replied",
)

# Explicit inbound-subject phrases — the RECIPIENT acted. These win over an
# outbound marker so "She replied to my email" still counts as a received reply.
INBOUND_SUBJECT_MARKERS = (
    "they replied", "she replied", "he replied", "they responded",
    "she responded", "he responded", "heard back", "got back", "wrote back",
    "called back", "texted back", "reply from", "response from",
    "got a reply", "got a response",
)


def reply_received(summary: str) -> bool:
    """Heuristic: did the RECIPIENT reply (→ flip outreach-log to Replied)?

    Order matters: an explicit inbound subject ("she replied") wins; an outbound
    marker ("replied to her") suppresses; otherwise fall back to the bare signals.
    """
    s = summary.lower()
    if any(m in s for m in INBOUND_SUBJECT_MARKERS):
        return True
    if any(m in s for m in OUTBOUND_REPLY_MARKERS):
        return False
    return any(sig in s for sig in REPLY_SIGNALS)

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
# Outreach-log cross-update
# ---------------------------------------------------------------------------

def update_outreach_status(repo_root: Path, contact_name: str, log_date: str) -> bool:
    """Update the most recent 'Sent' entry in outreach-log.md for contact_name to 'Replied'.

    Returns True if an entry was updated.
    """
    outreach_path = repo_root / OUTREACH_FILE
    content = read_file(outreach_path)
    if not content:
        return False

    name_lower = contact_name.lower().strip()
    lines = content.splitlines()
    best_idx = -1
    best_date = None

    for i, line in enumerate(lines):
        if not line.startswith("|") or line.startswith("| Date") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 7:
            continue
        row_name = cols[3].lower().strip()
        row_status = cols[6].strip().lower()
        if row_name != name_lower:
            continue
        if row_status not in ("sent", "drafted", "pending"):
            continue
        # Track the most recent matching row
        try:
            row_date = datetime.strptime(cols[0].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        if best_date is None or row_date > best_date:
            best_date = row_date
            best_idx = i

    if best_idx == -1:
        return False

    # Replace status in the matched row
    cols = [c.strip() for c in lines[best_idx].strip("|").split("|")]
    cols[6] = "Replied"
    lines[best_idx] = "| " + " | ".join(cols) + " |"

    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(outreach_path, new_content)
    return True


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

    # Load the file up front so the duplicate checks run in BOTH real and dry-run
    # mode — a --dry-run must report the SAME possible_duplicate / duplicate_warning
    # the real run would, not a blind "Would add" (fable-audit Theme 2). On a
    # missing/empty file there are no existing contacts, so dry-run still previews an
    # add; only the real write requires the file to exist.
    content = read_file(networking_path)
    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)] if content else []

    # Collect existing contact names once (for exact + fuzzy dup checks).
    contacts_start, contacts_end = find_section(lines, r"^##\s+Contacts")
    existing_names = []
    if contacts_start != -1:
        for i in range(contacts_start, contacts_end):
            if is_data_row(lines[i]):
                cols = parse_cols(lines[i])
                if cols and cols[0]:
                    existing_names.append((cols[0], cols[1] if len(cols) > 1 else ""))

    # Exact duplicate check (case-insensitive name).
    for nm, co in existing_names:
        if nm.lower() == args.name.lower():
            out_ok("duplicate_warning",
                   f"{args.name} already exists in contacts",
                   existing_company=co)
            return

    # Fuzzy duplicate guard: catch spelling variants of the SAME person
    # (e.g. dropped/transposed letters, nickname vs full name, accents) that the
    # exact check above misses. Blocks unless --force. Origin 2026-06-12.
    if not getattr(args, "force", False):
        near = find_near_duplicates(args.name, [nm for nm, _ in existing_names])
        if near:
            co_by_name = {nm: co for nm, co in existing_names}
            candidates = [
                {"name": nm, "company": co_by_name.get(nm, ""), "similarity": ratio}
                for nm, ratio in near
            ]
            out_error(
                f"Possible duplicate of existing contact(s): "
                + ", ".join(f"{c['name']} ({c['similarity']})" for c in candidates)
                + f". If {args.name} is genuinely a different person, re-run with --force. "
                  f"Otherwise log to the existing contact instead of adding a new one.",
                code="possible_duplicate",
                candidates=candidates,
            )

    if dry_run:
        row = fmt_contact_row(args.name, company, role, rel, today, "—")
        out_ok("add", f"Would add contact: {args.name}",
               dry_run=True,
               would_mutate=[{"file": str(networking_path), "row": row}])
        return

    if not content:
        out_error(f"File not found or empty: {networking_path}", "file_not_found")

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

    # Follow-up todo via subprocess (non-fatal if todos file missing).
    # Supersede this contact's prior AUTO-generated follow-up first so repeated
    # logging keeps a single live "Follow up: <name> — ..." row instead of stacking
    # duplicates (bug origin 2026-06-18). The "— " prefix matches only the auto
    # format, so a manually-curated variant (e.g. "Follow up: <name> (Acme) — ...")
    # is left untouched.
    if args.followup:
        todos_path = repo_root / TODOS_FILE
        if todos_path.exists():
            todo_write = str(Path(__file__).parent / "todo_write.py")
            todo_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            try:
                subprocess.run(
                    [sys.executable, todo_write, "--repo-root", str(repo_root),
                     "supersede", f"Follow up: {args.name} —"],
                    capture_output=True, text=True, encoding="utf-8", env=todo_env,
                    cwd=str(repo_root),
                )
                todo_task = f"Follow up: {args.name} — {args.followup}"
                from datetime import timedelta
                due = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
                subprocess.run(
                    [sys.executable, todo_write,
                     "add", todo_task, "Med", due, f"From networking log on {log_date}"],
                    capture_output=True, text=True, encoding="utf-8", env=todo_env,
                    cwd=str(repo_root),
                )
            except Exception:
                pass  # todo creation is best-effort

    # Auto-update outreach-log.md only if the RECIPIENT replied.
    # Explicit flags override the heuristic; otherwise reply_received() guards
    # against flipping on Nick's own outbound replies ("Replied to her intro").
    outreach_updated = False
    if getattr(args, "reply_received", False):
        should_flip = True
    elif getattr(args, "no_reply_flip", False):
        should_flip = False
    else:
        should_flip = reply_received(args.summary)
    if should_flip:
        outreach_updated = update_outreach_status(repo_root, args.name, log_date)

    out_ok("log", f"Logged interaction for {args.name}: {args.summary}",
           name=args.name, date=log_date,
           outreach_log_updated=outreach_updated)


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
    add_p.add_argument("--force", action="store_true",
                       help="Add even if a fuzzy name match to an existing contact is found.")

    log_p = sub.add_parser("log")
    log_p.add_argument("name")
    log_p.add_argument("--date",     default=None)
    log_p.add_argument("--type",     default="other")
    log_p.add_argument("--summary",  required=True)
    log_p.add_argument("--followup", default=None)
    log_p.add_argument("--content",  default=None)
    log_p.add_argument("--reply-received", action="store_true",
                       help="Force-flip the matching outreach-log row to 'Replied' "
                            "(the RECIPIENT replied). Overrides the summary heuristic.")
    log_p.add_argument("--no-reply-flip", action="store_true",
                       help="Never flip the outreach-log, even if the summary looks "
                            "like a reply. Use when logging Nick's own outbound reply.")

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
