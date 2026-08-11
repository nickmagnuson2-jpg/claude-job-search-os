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

Usage (--repo-root/--dry-run are top-level flags — they MUST come BEFORE the
subcommand; argparse rejects them after it with "unrecognized arguments"):
  PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . add "Acme" "PM"
  PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . update "Acme" "Applied"
  PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . remove "Acme"
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# Stages a row may be archived under. Must stay in sync with
# todo_write.py TERMINAL_STAGES, which exact-matches these in ## Archived —
# a row archived under anything else is invisible to every terminal consumer.
ARCHIVABLE_STAGES = {"Withdrawn", "Rejected", "Accepted"}

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


FIT_VERDICTS = ("fit", "not-fit", "neutral", "unknown")


def sanitize_cell(text: str) -> str:
    """Collapse whitespace and strip any '|' that would break the 8-column row."""
    return re.sub(r"\s+", " ", str(text).replace("|", "/")).strip()


def compose_fit_note(existing_notes: str, fit_reason: str, fit_verdict, today: str) -> str:
    """Append a structured, greppable [fit-reason ...] tag to the Notes cell.

    Captures Nick's one-line fit rationale at a stage change — the input the
    calibration scorer was starving for. The blind machine re-run abstained on 9
    of 52 of Nick's fit calls purely because his reasoning lived in his head, not
    in a quotable source (output/analysis/071526-machine-vs-human-agreement.md).
    Storing it in Notes needs no schema change: the extract-verify manifest and
    scorer_eval already read the Notes cell, so a future run can quote it. Format:
    `[fit-reason YYYY-MM-DD <verdict>: <reason>]` (verdict omitted if not given).
    """
    verdict = f" {fit_verdict}" if fit_verdict in FIT_VERDICTS else ""
    tag = f"[fit-reason {today}{verdict}: {sanitize_cell(fit_reason)}]"
    base = (existing_notes or "").strip()
    if base in ("", "—", "–"):
        return tag
    return f"{base} {tag}"


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
    notes = "—"
    if getattr(args, "fit_reason", None):
        notes = compose_fit_note("—", args.fit_reason, getattr(args, "fit_verdict", None), today)

    if dry_run:
        row = fmt_row(args.company, args.role, stage, today, "—", "—", notes, url)
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

    row = fmt_row(args.company, args.role, stage, today, "—", "—", notes, url)
    pos = table_insert_pos(lines, act_start, act_end)
    lines.insert(pos, row)
    save_lines(pipeline_path, lines, content)

    out_ok("add", f"Added: {args.company} | {args.role} | {stage}",
           company=args.company, role=args.role, stage=stage,
           fit_reason_logged=bool(getattr(args, "fit_reason", None)))


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

    if len(cols) != 8:
        out_error(
            f"Row for {args.company} has {len(cols)} columns, expected 8 — "
            f"a Notes or Next Action cell likely contains an unescaped '|'. "
            f"Fix the row by hand in {PIPELINE_FILE} (replace the stray '|' "
            f"with ' - ' or similar) before retrying, or the rewrite will "
            f"silently truncate Notes and/or destroy the URL.",
            "malformed_row",
            row=row_idx + 1,
            column_count=len(cols),
        )

    new_next_action = args.next_action if args.next_action else (cols[4] if len(cols) > 4 else "—")
    new_cv_used     = args.cv_used     if args.cv_used     else (cols[5] if len(cols) > 5 else "—")
    new_notes       = args.notes       if args.notes       else (cols[6] if len(cols) > 6 else "—")
    if getattr(args, "fit_reason", None):
        new_notes = compose_fit_note(new_notes, args.fit_reason, getattr(args, "fit_verdict", None), today)
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
           company=args.company, stage=args.new_stage,
           fit_reason_logged=bool(getattr(args, "fit_reason", None)))


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

    if len(cols) != 8:
        out_error(
            f"Row for {args.company} has {len(cols)} columns, expected 8 — "
            f"a Notes or Next Action cell likely contains an unescaped '|'. "
            f"Fix the row by hand in {PIPELINE_FILE} (replace the stray '|' "
            f"with ' - ' or similar) before retrying, or the rewrite will "
            f"silently truncate Notes and/or destroy the URL.",
            "malformed_row",
            row=row_idx + 1,
            column_count=len(cols),
        )

    stage = getattr(args, "stage", "Withdrawn") or "Withdrawn"
    if stage not in ARCHIVABLE_STAGES:
        out_error(
            f"--stage must be one of {sorted(ARCHIVABLE_STAGES)}, got {stage!r}. "
            f"todo_write.py sync exact-matches these in ## Archived; a row "
            f"archived under any other stage is invisible to it.",
            "invalid_stage",
            stage=stage,
        )

    existing_notes = cols[6] if len(cols) > 6 else "—"
    new_notes = (
        f"{existing_notes} - {stage} {today}"
        if existing_notes not in ("—", "", "–")
        else f"{stage} {today}"
    )

    archived_row = fmt_row(
        cols[0],
        cols[1] if len(cols) > 1 else "—",
        stage,
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

    out_ok("soft_delete", f"Archived: {args.company} → {stage}",
           company=args.company, stage=stage)


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
    add_p.add_argument("--fit-reason", dest="fit_reason", default=None,
                       help="One-line fit rationale; appended to Notes as a [fit-reason ...] tag.")
    add_p.add_argument("--fit-verdict", dest="fit_verdict", default=None, choices=list(FIT_VERDICTS),
                       help="Optional fit_verdict class for the fit-reason tag (feeds the scorer target).")

    upd_p = sub.add_parser("update")
    upd_p.add_argument("company")
    upd_p.add_argument("new_stage")
    upd_p.add_argument("--role", default=None)
    upd_p.add_argument("--next-action", dest="next_action", default=None)
    upd_p.add_argument("--cv-used",     dest="cv_used",     default=None)
    upd_p.add_argument("--notes",                           default=None)
    upd_p.add_argument("--fit-reason", dest="fit_reason", default=None,
                       help="One-line fit rationale for this stage change; appended to Notes as a [fit-reason ...] tag.")
    upd_p.add_argument("--fit-verdict", dest="fit_verdict", default=None, choices=list(FIT_VERDICTS),
                       help="Optional fit_verdict class for the fit-reason tag (feeds the scorer target).")

    rem_p = sub.add_parser("remove")
    rem_p.add_argument("company")
    rem_p.add_argument("--role", default=None)
    rem_p.add_argument(
        # Validated in cmd_remove, not by argparse `choices`, so an invalid
        # stage returns the tool's JSON error contract instead of argparse's
        # exit-2 plain text.
        "--stage", default="Withdrawn",
        help="Terminal stage to archive under. Default Withdrawn. Use Rejected "
             "when the company passed, Accepted when an offer was taken — "
             "archiving a rejection as a withdrawal inverts the fact.",
    )

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
