#!/usr/bin/env python3
"""
pipe_write.py — Atomic mutations for data/job-pipeline.md.

Subcommands:
  add <company> <role> [--url URL] [--stage STAGE]
  update <company> <new-stage> [--role ROLE] [--next-action TEXT]
                               [--cv-used TEXT] [--notes TEXT]
  remove <company> [--role ROLE]

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd.
  --dry-run          Return JSON contract without writing.

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "..."}
  Failure: {"status": "error", "message": "...", "code": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python tools/pipe_write.py add "Acme" "PM" --repo-root .
  PYTHONIOENCODING=utf-8 python tools/pipe_write.py update "Acme" "Applied" --repo-root .
  PYTHONIOENCODING=utf-8 python tools/pipe_write.py remove "Acme" --repo-root .
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

PIPELINE_FILE   = "data/job-pipeline.md"
PIPELINE_HEADER = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
PIPELINE_SEP    = "| --- | --- | --- | --- | --- | --- | --- | --- |"


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
    if re.match(r"^\|\s*Company\s*\|", line):
        return False
    return True


def parse_cols(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def fmt_row(company, role, stage, date_updated, next_action, cv_used, notes, url):
    return f"| {company} | {role} | {stage} | {date_updated} | {next_action} | {cv_used} | {notes} | {url} |"


# ---------------------------------------------------------------------------
# Section navigation
# ---------------------------------------------------------------------------

def find_section(lines: list, pattern: str) -> tuple:
    """Find section matching regex pattern. Returns (start_idx, end_idx)."""
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
# File load / save
# ---------------------------------------------------------------------------

def load_pipeline(path: Path) -> tuple:
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

def cmd_add(args, pipeline_path: Path, dry_run: bool) -> None:
    today = date.today().strftime("%Y-%m-%d")
    stage = args.stage if args.stage else "Researching"
    url   = args.url   if args.url   else "—"

    if dry_run:
        row = fmt_row(args.company, args.role, stage, today, "—", "—", "—", url)
        out_ok("add", f"Would add: {args.company} | {args.role}",
               dry_run=True, would_mutate=[{"file": str(pipeline_path), "row": row}])
        return

    content, lines = load_pipeline(pipeline_path)

    act_start, act_end = find_section(lines, r"^##\s+Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-pipeline.md", "missing_section")

    # Duplicate check (case-insensitive company match)
    existing_roles = []
    for i in range(act_start, act_end):
        if is_data_row(lines[i]):
            cols = parse_cols(lines[i])
            if cols and cols[0].lower() == args.company.lower():
                existing_roles.append(cols[1] if len(cols) > 1 else "")

    if existing_roles:
        out_ok("duplicate_warning",
               f"{args.company} already exists in active pipeline",
               existing_roles=existing_roles)
        return

    row = fmt_row(args.company, args.role, stage, today, "—", "—", "—", url)
    pos = table_insert_pos(lines, act_start, act_end)
    lines.insert(pos, row)
    save_lines(pipeline_path, lines, content)

    out_ok("add", f"Added: {args.company} | {args.role} | {stage}",
           company=args.company, role=args.role, stage=stage)


def cmd_update(args, pipeline_path: Path, dry_run: bool) -> None:
    today = date.today().strftime("%Y-%m-%d")

    if dry_run:
        out_ok("update", f"Would update: {args.company} → {args.new_stage}",
               dry_run=True, would_mutate=[{"file": str(pipeline_path)}])
        return

    content, lines = load_pipeline(pipeline_path)

    act_start, act_end = find_section(lines, r"^##\s+Active")
    if act_start == -1:
        out_error("Could not find ## Active section", "missing_section")

    # Find matching rows
    matches = []
    for i in range(act_start, act_end):
        if is_data_row(lines[i]):
            cols = parse_cols(lines[i])
            if cols and cols[0].lower() == args.company.lower():
                matches.append((i, cols))

    if not matches:
        out_error(f"No active entry found for: {args.company}", "not_found")

    # Ambiguous multi-role case
    if len(matches) > 1 and not args.role:
        match_list = [
            {"role": c[1] if len(c) > 1 else "", "stage": c[2] if len(c) > 2 else ""}
            for _, c in matches
        ]
        out_error(
            f"Multiple roles found for {args.company} — use --role to specify",
            "ambiguous_match",
            matches=match_list,
        )

    # Filter by role if specified
    if args.role:
        role_matches = [
            (i, c) for i, c in matches
            if len(c) > 1 and c[1].lower() == args.role.lower()
        ]
        if not role_matches:
            out_error(f"No entry found for {args.company} / {args.role}", "not_found")
        matches = role_matches

    row_idx, cols = matches[0]

    new_next_action = args.next_action if args.next_action else (cols[4] if len(cols) > 4 else "—")
    new_cv_used     = args.cv_used     if args.cv_used     else (cols[5] if len(cols) > 5 else "—")
    new_notes       = args.notes       if args.notes       else (cols[6] if len(cols) > 6 else "—")
    url             = cols[7] if len(cols) > 7 else "—"

    updated_row = fmt_row(
        cols[0],
        cols[1] if len(cols) > 1 else "—",
        args.new_stage,
        today,
        new_next_action,
        new_cv_used,
        new_notes,
        url,
    )
    lines[row_idx] = updated_row
    save_lines(pipeline_path, lines, content)

    out_ok("update", f"Updated: {args.company} → {args.new_stage}",
           company=args.company, stage=args.new_stage)


def cmd_remove(args, pipeline_path: Path, dry_run: bool) -> None:
    today = date.today().strftime("%Y-%m-%d")

    if dry_run:
        out_ok("remove", f"Would soft-delete: {args.company}",
               dry_run=True, would_mutate=[{"file": str(pipeline_path)}])
        return

    content, lines = load_pipeline(pipeline_path)

    act_start, act_end = find_section(lines, r"^##\s+Active")
    if act_start == -1:
        out_error("Could not find ## Active section", "missing_section")

    matches = []
    for i in range(act_start, act_end):
        if is_data_row(lines[i]):
            cols = parse_cols(lines[i])
            if cols and cols[0].lower() == args.company.lower():
                matches.append((i, cols))

    if not matches:
        out_error(f"No active entry found for: {args.company}", "not_found")

    if len(matches) > 1 and not args.role:
        match_list = [{"role": c[1] if len(c) > 1 else ""} for _, c in matches]
        out_error(
            f"Multiple roles for {args.company} — use --role to specify",
            "ambiguous_match",
            matches=match_list,
        )

    if args.role:
        role_matches = [
            (i, c) for i, c in matches
            if len(c) > 1 and c[1].lower() == args.role.lower()
        ]
        if not role_matches:
            out_error(f"No entry found for {args.company} / {args.role}", "not_found")
        matches = role_matches

    row_idx, cols = matches[0]

    existing_notes = cols[6] if len(cols) > 6 else "—"
    new_notes = (
        f"{existing_notes} | Withdrawn {today}"
        if existing_notes not in ("—", "", "–")
        else f"Withdrawn {today}"
    )

    archived_row = fmt_row(
        cols[0],
        cols[1] if len(cols) > 1 else "—",
        "Withdrawn",
        cols[3] if len(cols) > 3 else today,
        cols[4] if len(cols) > 4 else "—",
        cols[5] if len(cols) > 5 else "—",
        new_notes,
        cols[7] if len(cols) > 7 else "—",
    )

    lines.pop(row_idx)

    # Find or create ## Archived section
    arch_start, arch_end = find_section(lines, r"^##\s+Archived")
    if arch_start == -1:
        lines.append("")
        lines.append("## Archived")
        lines.append("")
        lines.append(PIPELINE_HEADER)
        lines.append(PIPELINE_SEP)
        lines.append(archived_row)
    else:
        pos = table_insert_pos(lines, arch_start, arch_end)
        lines.insert(pos, archived_row)

    save_lines(pipeline_path, lines, content)

    out_ok("soft_delete", f"Archived: {args.company}",
           company=args.company)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Atomic mutations for data/job-pipeline.md.")
    p.add_argument("--repo-root", default=None, help="Repository root. Defaults to cwd.")
    p.add_argument("--dry-run", action="store_true", help="Return JSON without writing.")

    sub = p.add_subparsers(dest="command")

    add_p = sub.add_parser("add")
    add_p.add_argument("company")
    add_p.add_argument("role")
    add_p.add_argument("--url", default=None)
    add_p.add_argument("--stage", default=None)

    upd_p = sub.add_parser("update")
    upd_p.add_argument("company")
    upd_p.add_argument("new_stage")
    upd_p.add_argument("--role", default=None)
    upd_p.add_argument("--next-action", dest="next_action", default=None)
    upd_p.add_argument("--cv-used",     dest="cv_used",     default=None)
    upd_p.add_argument("--notes",                           default=None)

    rem_p = sub.add_parser("remove")
    rem_p.add_argument("company")
    rem_p.add_argument("--role", default=None)

    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.command:
        out_error("Usage: pipe_write.py <add|update|remove> [args...]")

    repo_root     = Path(args.repo_root) if args.repo_root else Path.cwd()
    pipeline_path = repo_root / PIPELINE_FILE

    if args.command == "add":
        cmd_add(args, pipeline_path, args.dry_run)
    elif args.command == "update":
        cmd_update(args, pipeline_path, args.dry_run)
    elif args.command == "remove":
        cmd_remove(args, pipeline_path, args.dry_run)
    else:
        out_error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
