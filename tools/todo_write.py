#!/usr/bin/env python3
"""
todo_write.py — Atomic mutations for data/job-todos.md.

Handles add/done/clear/sync without loading the file into Claude's context window.
Each command reads the file, performs a targeted line-level mutation, and writes back.

Commands:
  add <task> <priority> <due> <notes>  — append row to Active section
  done <task_fragment>                 — move matching row to Completed (a completion)
  withdraw <task_fragment>             — mark matching row Withdrawn, not Done (NOT a completion;
                                         also corrects a row already mis-marked Completed)
  clear                                — move Done/Withdrawn rows to Completed
  sync                                 — auto-withdraw based on pipeline Archived section
  list [--status <Status>] [--grep <substring>]
                                        — print todos (Active + Completed) as structured JSON;
                                         verify a mutation landed without grepping the raw file.
                                         --status filters on exact status (Pending/In Progress/
                                         Done/Withdrawn); --grep filters task+notes substring
                                         (case-insensitive). Filters compose (AND).

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "...", ...extra}
  Failure: {"status": "error", "message": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "Task name" Med 2026-03-01 "Notes"
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py done "task fragment"
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py clear
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py sync
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py list --status Pending
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py list --grep "acme corp"
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

TODOS_FILE = "data/job-todos.md"
PIPELINE_FILE = "data/job-pipeline.md"
COMPLETED_HEADER = "| Task | Priority | Completed | Notes |"
COMPLETED_SEP = "| --- | --- | --- | --- |"
TERMINAL_STAGES = {"Withdrawn", "Rejected", "Accepted"}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    """Read file contents or return empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def write_atomic(path: Path, content: str) -> None:
    """Write file atomically via a sibling temp file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def out_ok(action: str, summary: str, **extra) -> None:
    d = {"status": "ok", "action": action, "summary": summary}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str) -> None:
    print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Table row helpers
# ---------------------------------------------------------------------------

def is_data_row(line: str) -> bool:
    """Return True for markdown table data rows (not header or separator)."""
    if not line.startswith("|"):
        return False
    # Separator rows: | --- | --- | ...
    if re.match(r"^\|\s*:?-+:?\s*\|", line):
        return False
    # Known header rows
    if line.startswith("| Task") or line.startswith("| Company"):
        return False
    return True


def parse_cols(line: str) -> list[str]:
    """Split a table row into column values."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _safe_cell(s: str) -> str:
    """Neutralize literal pipe chars so a field value can't split a table row into
    extra columns on write (root cause of the 2026-07 job-todos.md column drift)."""
    return s.replace("|", "/") if s else s


def fmt_active(task: str, priority: str, due: str, status: str, notes: str) -> str:
    task, priority, due, status, notes = (
        _safe_cell(task), _safe_cell(priority), _safe_cell(due), _safe_cell(status), _safe_cell(notes))
    return f"| {task} | {priority} | {due} | {status} | {notes} |"


def fmt_completed(task: str, priority: str, completed: str, notes: str) -> str:
    task, priority, completed, notes = (
        _safe_cell(task), _safe_cell(priority), _safe_cell(completed), _safe_cell(notes))
    return f"| {task} | {priority} | {completed} | {notes} |"


# ---------------------------------------------------------------------------
# Section navigation
# ---------------------------------------------------------------------------

def find_section(lines: list[str], header: str) -> tuple[int, int]:
    """
    Find a section by its ## header line.
    Returns (start_idx, end_idx) where end_idx is one past the last line.
    Returns (-1, -1) if not found.
    """
    start = -1
    for i, line in enumerate(lines):
        if line.strip() == header:
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


def table_insert_pos(lines: list[str], sec_start: int, sec_end: int) -> int:
    """Return the line index to insert a new row (after last data row, or after separator)."""
    # Find last data row
    last = -1
    for i in range(sec_start, sec_end):
        if is_data_row(lines[i]):
            last = i
    if last != -1:
        return last + 1
    # No data rows yet — insert after separator
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|") and "---" in lines[i]:
            return i + 1
    # Fallback: after first pipe row
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|"):
            return i + 1
    return sec_end


def insert_into_completed(lines: list[str], row: str) -> None:
    """
    Append a row to the Completed section, creating the section if absent.
    Re-finds the section each call so indices stay correct after prior mutations.
    """
    comp_start, comp_end = find_section(lines, "## Completed")
    if comp_start == -1:
        lines.append("")
        lines.append("## Completed")
        lines.append("")
        lines.append(COMPLETED_HEADER)
        lines.append(COMPLETED_SEP)
        lines.append(row)
    else:
        pos = table_insert_pos(lines, comp_start, comp_end)
        lines.insert(pos, row)


# ---------------------------------------------------------------------------
# File load / save
# ---------------------------------------------------------------------------

def load_todos(todos_path: Path) -> tuple[str, list[str]]:
    """Return (raw_content, stripped_lines). Exits on missing file."""
    content = read_file(todos_path)
    if not content:
        out_error(f"File not found or empty: {todos_path}")
    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    return content, lines


def save_lines(path: Path, lines: list[str], original_content: str) -> None:
    """Rejoin lines and write atomically, preserving trailing newline."""
    content = "\n".join(lines)
    if original_content.endswith("\n"):
        content += "\n"
    write_atomic(path, content)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(args: list[str], todos_path: Path) -> None:
    """Append a new Pending row to the Active section."""
    if not args:
        out_error("Usage: add <task> [priority] [due] [notes]")

    task = args[0]
    priority = args[1] if len(args) > 1 and args[1] not in ("--", "-", "") else "Med"
    due = args[2] if len(args) > 2 and args[2] not in ("--", "-", "") else "—"
    notes = args[3] if len(args) > 3 and args[3] not in ("--", "-", "") else "—"

    if priority not in ("High", "Med", "Low"):
        priority = "Med"

    content, lines = load_todos(todos_path)

    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-todos.md")

    # Duplicate check (fuzzy — warn but still add)
    warning = None
    task_lower = task.lower()
    for i in range(act_start, act_end):
        if is_data_row(lines[i]):
            cols = parse_cols(lines[i])
            if cols and cols[0] and task_lower in cols[0].lower():
                warning = f"Similar task already exists: {cols[0]}"
                break

    pos = table_insert_pos(lines, act_start, act_end)
    lines.insert(pos, fmt_active(task, priority, due, "Pending", notes))
    save_lines(todos_path, lines, content)

    result: dict = {
        "status": "ok", "action": "added",
        "summary": f"Added: {task} [{priority}]",
        "task": task, "priority": priority, "due": due,
    }
    if warning:
        result["warning"] = warning
    print(json.dumps(result, ensure_ascii=False))


def cmd_done(args: list[str], todos_path: Path) -> None:
    """Move a matching Active row to Completed."""
    if not args:
        out_error("Usage: done <task_fragment>")

    fragment = args[0].lower()
    today_str = date.today().strftime("%Y-%m-%d")

    content, lines = load_todos(todos_path)

    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-todos.md")

    matches = []
    for i in range(act_start, act_end):
        if not is_data_row(lines[i]):
            continue
        cols = parse_cols(lines[i])
        if cols and cols[0] and fragment in cols[0].lower():
            matches.append((i, cols))

    if not matches:
        out_error(f"No task found matching: {args[0]}")
    if len(matches) > 1:
        options = "\n".join(f"  - {c[0]}" for _, c in matches)
        out_error(f"Multiple matches — be more specific:\n{options}")

    row_idx, cols = matches[0]
    task = cols[0]
    priority = cols[1] if len(cols) > 1 else "Med"
    # Join extra cols back in case notes contained a pipe character
    notes = " | ".join(cols[4:]) if len(cols) > 4 else "—"

    suffix = f"Completed {today_str}"
    completed_notes = suffix if notes in ("—", "") else f"{suffix} | {notes}"

    lines.pop(row_idx)
    insert_into_completed(lines, fmt_completed(task, priority, today_str, completed_notes))
    save_lines(todos_path, lines, content)

    act_s, act_e = find_section(lines, "## Active")
    remaining = sum(1 for i in range(act_s, act_e) if is_data_row(lines[i])) if act_s != -1 else 0

    out_ok("done", f"Completed: {task}", task=task, remaining_active=remaining)


def _find_in_section(lines: list[str], start: int, end: int, fragment: str) -> list[tuple[int, list[str]]]:
    """Return [(line_index, cols)] for data rows in [start, end) whose task contains fragment."""
    matches = []
    for i in range(start, end):
        if not is_data_row(lines[i]):
            continue
        cols = parse_cols(lines[i])
        if cols and cols[0] and fragment in cols[0].lower():
            matches.append((i, cols))
    return matches


def cmd_withdraw(args: list[str], todos_path: Path) -> None:
    """Mark a matching todo Withdrawn (NOT Done) so it never counts as a completion.

    - If the task is in ## Active: move it to ## Completed marked "Withdrawn <today>".
    - If it is already in ## Completed (e.g. mis-marked "Completed <date>" when it was
      actually superseded): rewrite it in place to "Withdrawn <date>", preserving the date.
    Withdrawn rows are excluded from completion counts by tools/todo_daily_metrics.py.
    """
    if not args:
        out_error("Usage: withdraw <task_fragment>")

    fragment = args[0].lower()
    today_str = date.today().strftime("%Y-%m-%d")
    content, lines = load_todos(todos_path)

    # 1) Active section: move the row out, marked Withdrawn.
    act_start, act_end = find_section(lines, "## Active")
    if act_start != -1:
        matches = _find_in_section(lines, act_start, act_end, fragment)
        if len(matches) > 1:
            opts = "\n".join(f"  - {c[0]}" for _, c in matches)
            out_error(f"Multiple matches — be more specific:\n{opts}")
        if len(matches) == 1:
            row_idx, cols = matches[0]
            task = cols[0]
            priority = cols[1] if len(cols) > 1 else "Med"
            notes = " | ".join(cols[4:]) if len(cols) > 4 else "—"
            lines.pop(row_idx)
            insert_into_completed(lines, fmt_completed(task, priority, f"Withdrawn {today_str}", notes))
            save_lines(todos_path, lines, content)
            out_ok("withdrawn", f"Withdrawn: {task}", task=task)
            return

    # 2) Completed section: correct an already-archived row to Withdrawn.
    comp_start, comp_end = find_section(lines, "## Completed")
    if comp_start != -1:
        matches = _find_in_section(lines, comp_start, comp_end, fragment)
        if len(matches) > 1:
            opts = "\n".join(f"  - {c[0]}" for _, c in matches)
            out_error(f"Multiple matches — be more specific:\n{opts}")
        if len(matches) == 1:
            row_idx, cols = matches[0]
            task = cols[0]
            priority = cols[1] if len(cols) > 1 else "Med"
            completed_cell = cols[2] if len(cols) > 2 else ""
            notes = cols[3] if len(cols) > 3 else "—"
            if "withdrawn" in completed_cell.lower() or "withdrawn" in notes.lower():
                out_ok("withdrawn", f"Already withdrawn: {task}", task=task, changed=False)
                return
            # Preserve the original date if present, else use today.
            m = re.search(r"\d{4}-\d{2}-\d{2}", f"{completed_cell} {notes}")
            date_token = m.group(0) if m else today_str
            # Strip a leading "Completed <date>" marker from notes so no completion marker remains.
            clean_notes = re.sub(r"^Completed\s+\d{4}-\d{2}-\d{2}\s*\|?\s*", "", notes).strip() or "—"
            lines[row_idx] = fmt_completed(task, priority, f"Withdrawn {date_token}", clean_notes)
            save_lines(todos_path, lines, content)
            out_ok("withdrawn", f"Corrected to Withdrawn: {task}", task=task, changed=True)
            return

    out_error(f"No task found matching: {args[0]}")


def cmd_supersede(args: list[str], todos_path: Path) -> None:
    """Withdraw EVERY Active row whose task starts with the given prefix.

    Keeps a single live item per logical group. Callers (e.g. networking_write.py)
    supersede a contact's prior auto-generated follow-up before adding a fresh one,
    so repeated logging doesn't stack duplicate "Follow up: <name> — ..." rows.
    Superseded rows move to Completed marked "Withdrawn <today>" (not a completion).
    """
    if not args:
        out_error("Usage: supersede <task-prefix>")

    prefix = args[0].lower()
    content, lines = load_todos(todos_path)
    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-todos.md")

    today_str = date.today().strftime("%Y-%m-%d")
    matches = []
    for i in range(act_start, act_end):
        if is_data_row(lines[i]):
            cols = parse_cols(lines[i])
            status = cols[3] if len(cols) > 3 else ""
            if (cols and cols[0] and cols[0].lower().startswith(prefix)
                    and status not in TERMINAL_STAGES and status != "Done"):
                matches.append((i, cols))

    if not matches:
        out_ok("supersede", f"No active todos matching prefix: {args[0]}", superseded=0)
        return

    # Delete from Active (reverse order to keep indices valid), then archive as Withdrawn.
    for i, _ in reversed(matches):
        del lines[i]
    for _, cols in matches:
        task = cols[0]
        priority = cols[1] if len(cols) > 1 and cols[1] else "Med"
        notes = cols[4] if len(cols) > 4 else "—"
        insert_into_completed(lines, fmt_completed(task, priority, f"Withdrawn {today_str}", notes))

    save_lines(todos_path, lines, content)
    out_ok("supersede", f"Superseded {len(matches)} todo(s) matching: {args[0]}",
           superseded=len(matches), tasks=[c[0] for _, c in matches])


def cmd_clear(todos_path: Path) -> None:
    """Move all Done/Withdrawn rows from Active to Completed."""
    today_str = date.today().strftime("%Y-%m-%d")

    content, lines = load_todos(todos_path)

    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-todos.md")

    to_move = []
    for i in range(act_start, act_end):
        if not is_data_row(lines[i]):
            continue
        cols = parse_cols(lines[i])
        if len(cols) >= 4 and cols[3] in ("Done", "Withdrawn"):
            to_move.append((i, cols))

    if not to_move:
        out_ok("clear", "No Done or Withdrawn items to archive", archived=0, done=0, withdrawn=0)
        return

    done_count = sum(1 for _, c in to_move if c[3] == "Done")
    withdrawn_count = sum(1 for _, c in to_move if c[3] == "Withdrawn")

    # Build completed rows before mutating lines
    completed_rows = []
    for _, cols in to_move:
        task = cols[0]
        priority = cols[1] if len(cols) > 1 else "Med"
        status = cols[3] if len(cols) > 3 else "Done"
        notes = " | ".join(cols[4:]) if len(cols) > 4 else "—"
        completed_date = today_str if status == "Done" else f"Withdrawn {today_str}"
        completed_rows.append(fmt_completed(task, priority, completed_date, notes))

    # Remove from Active in reverse order so earlier indices stay valid
    for idx, _ in reversed(to_move):
        lines.pop(idx)

    # Append to Completed one at a time (insert_into_completed re-finds section each call)
    for row in completed_rows:
        insert_into_completed(lines, row)

    save_lines(todos_path, lines, content)

    parts = []
    if done_count:
        parts.append(f"{done_count} Done")
    if withdrawn_count:
        parts.append(f"{withdrawn_count} Withdrawn")
    out_ok("clear", f"Archived: {' + '.join(parts)}",
           done=done_count, withdrawn=withdrawn_count, archived=done_count + withdrawn_count)


def cmd_sync(todos_path: Path, pipeline_path: Path) -> None:
    """Auto-withdraw Active todos for terminal pipeline companies."""
    today_str = date.today().strftime("%Y-%m-%d")

    pipeline_content = read_file(pipeline_path)
    if not pipeline_content:
        out_ok("sync", "Pipeline file not found — skipping sync", withdrawn=0)
        return

    # Fast path: check if Archived section has any data rows at all
    archived_match = re.search(r"## Archived.*?\n(.*?)(?=\n## |\Z)", pipeline_content, re.DOTALL)
    if not archived_match:
        out_ok("sync", "No Archived section in pipeline — nothing to sync", withdrawn=0)
        return

    has_data = any(
        line.startswith("|")
        and not line.startswith("| Company")
        and not re.match(r"^\|\s*:?-+", line)
        for line in archived_match.group(1).splitlines()
    )
    if not has_data:
        out_ok("sync", "Archived pipeline is empty — nothing to sync", withdrawn=0)
        return

    # Companies with a LIVE (non-terminal) row in Active Pipeline are never terminal,
    # even if a stale Archived row says otherwise. A company can legitimately appear
    # in both sections (an old withdrawn application plus a current live loop); the
    # live row wins. Origin: 2026-08-08, a stale duplicate row mass-withdrew 18 todos.
    live: set[str] = set()
    active_match = re.search(r"## Active Pipeline.*?\n(.*?)(?=\n## |\Z)",
                             pipeline_content, re.DOTALL)
    if active_match:
        for line in active_match.group(1).splitlines():
            if (not line.startswith("|")
                    or line.startswith("| Company")
                    or re.match(r"^\|\s*:?-+", line)):
                continue
            cols = parse_cols(line)
            if len(cols) >= 3 and cols[0] and cols[2] not in TERMINAL_STAGES:
                live.add(cols[0].lower())

    # Extract terminal companies from Archived section
    terminal: list[tuple[str, str]] = []
    for line in archived_match.group(1).splitlines():
        if (not line.startswith("|")
                or line.startswith("| Company")
                or re.match(r"^\|\s*:?-+", line)):
            continue
        cols = parse_cols(line)
        if len(cols) >= 3 and cols[0] and cols[2] in TERMINAL_STAGES:
            if cols[0].lower() in live:
                continue
            terminal.append((cols[0], cols[2]))

    if not terminal:
        out_ok("sync", "No terminal-stage companies in Archived section", withdrawn=0)
        return

    content, lines = load_todos(todos_path)

    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("Could not find ## Active section in job-todos.md")

    to_withdraw: list[tuple[int, list[str], str, str]] = []
    for i in range(act_start, act_end):
        if not is_data_row(lines[i]):
            continue
        cols = parse_cols(lines[i])
        if not cols:
            continue
        # Skip rows already marked terminal
        if len(cols) >= 4 and cols[3] in ("Done", "Withdrawn"):
            continue
        # Match the TASK column only, on a word boundary. Matching the joined row
        # meant a todo that merely name-dropped the company in its Notes ("reuse
        # the Acme case format") was withdrawn along with it.
        task_text = cols[0]
        for company, stage in terminal:
            if re.search(rf"\b{re.escape(company)}\b", task_text, re.IGNORECASE):
                to_withdraw.append((i, cols, company, stage))
                break

    if not to_withdraw:
        out_ok("sync", "No active todos matched terminal pipeline companies", withdrawn=0)
        return

    # Build completed rows
    completed_rows = []
    notices = []
    for _, cols, company, stage in to_withdraw:
        task = cols[0]
        priority = cols[1] if len(cols) > 1 else "Med"
        notes = " | ".join(cols[4:]) if len(cols) > 4 else "—"
        auto_note = f"Auto-withdrawn {today_str} — {company} pipeline entry marked {stage}"
        full_notes = auto_note if notes in ("—", "") else f"{notes} | {auto_note}"
        completed_rows.append(fmt_completed(task, priority, f"Withdrawn {today_str}", full_notes))
        notices.append(f"{company} ({stage})")

    # Remove from Active in reverse order
    for idx, _, _, _ in reversed(to_withdraw):
        lines.pop(idx)

    for row in completed_rows:
        insert_into_completed(lines, row)

    save_lines(todos_path, lines, content)

    n = len(to_withdraw)
    companies = list(dict.fromkeys(c for _, _, c, _ in to_withdraw))  # deduped, ordered
    out_ok("sync", f"{n} to-do(s) auto-withdrawn — {'; '.join(set(notices))}",
           withdrawn=n, companies=companies)


def cmd_list(args: list[str], todos_path: Path) -> None:
    """Print Active + Completed todos as structured JSON, with optional filters.

    Exists to verify a mutation landed without grepping the raw markdown file
    (an errored/mistyped subcommand's empty grep output is UNKNOWN, not ABSENT —
    see memory/feedback_verify_mutation_via_raw_file_not_tool_subcommand.md).
    """
    status_filter = None
    grep_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status_filter = args[i + 1]
            i += 2
        elif args[i] == "--grep" and i + 1 < len(args):
            grep_filter = args[i + 1].lower()
            i += 2
        else:
            i += 1

    content, lines = load_todos(todos_path)
    entries: list[dict] = []

    act_start, act_end = find_section(lines, "## Active")
    if act_start != -1:
        for i in range(act_start, act_end):
            if not is_data_row(lines[i]):
                continue
            cols = parse_cols(lines[i])
            # Filter separator-row noise (project convention: e.get("task") != "---").
            if not cols or not cols[0] or cols[0] == "---":
                continue
            entries.append({
                "section": "Active",
                "task": cols[0],
                "priority": cols[1] if len(cols) > 1 else "",
                "due": cols[2] if len(cols) > 2 else "",
                "status": cols[3] if len(cols) > 3 else "",
                "notes": " | ".join(cols[4:]) if len(cols) > 4 else "—",
            })

    comp_start, comp_end = find_section(lines, "## Completed")
    if comp_start != -1:
        for i in range(comp_start, comp_end):
            if not is_data_row(lines[i]):
                continue
            cols = parse_cols(lines[i])
            if not cols or not cols[0] or cols[0] == "---":
                continue
            completed = cols[2] if len(cols) > 2 else ""
            status = "Withdrawn" if completed.lower().startswith("withdrawn") else "Done"
            # Some Completed rows have drifted to 5 columns (a stray Active-shaped row
            # migrated without reflow) — join everything past col 3 so trailing notes
            # text a --grep filter relies on isn't silently dropped.
            notes = " | ".join(cols[3:]) if len(cols) > 3 else "—"
            entries.append({
                "section": "Completed",
                "task": cols[0],
                "priority": cols[1] if len(cols) > 1 else "",
                "due": completed,
                "status": status,
                "notes": notes,
            })

    if status_filter:
        entries = [e for e in entries if e["status"].lower() == status_filter.lower()]
    if grep_filter:
        entries = [
            e for e in entries
            if grep_filter in e["task"].lower() or grep_filter in e["notes"].lower()
        ]

    out_ok("list", f"{len(entries)} todo(s)", count=len(entries), entries=entries)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Extract --repo-root from anywhere in argv so it works before or after the command
    argv = sys.argv[1:]
    repo_root = Path.cwd()
    filtered = []
    i = 0
    while i < len(argv):
        if argv[i] == "--repo-root" and i + 1 < len(argv):
            repo_root = Path(argv[i + 1])
            i += 2
        else:
            filtered.append(argv[i])
            i += 1

    if not filtered:
        out_error("Usage: todo_write.py <add|done|withdraw|supersede|clear|sync|list> [args...]")

    cmd = filtered[0].lower()
    extra_args = filtered[1:]

    # Reject unknown --flags before they silently slot into wrong columns.
    # todo_write.py is positional-only; the only flag is --repo-root (handled above).
    # A BARE "--" (or "-") is allowed: cmd_add treats it as a "use default"
    # placeholder for an omitted priority/due/notes. Reject only "--word" kwarg-style
    # flags (len > 2), not the bare placeholder — otherwise cmd_add's documented "--"
    # sentinel is unreachable (the contradiction that made /todo's "--" instruction
    # fail, 2026-06-06).
    # Per memory/feedback_atomic_script_positional_args.md (script-tier promotion 2026-05-21).
    # `list` is the one query subcommand and takes real --flags (--status, --grep).
    allowed_flags = {"--status", "--grep"} if cmd == "list" else set()
    unknown_flags = [
        a for a in filtered if a.startswith("--") and len(a) > 2 and a not in allowed_flags
    ]
    if unknown_flags:
        out_error(
            f"Unknown flag(s): {', '.join(unknown_flags)}. "
            "todo_write.py is positional-only — pass bare values, not --flag names "
            "(except `list`, which takes --status/--grep). "
            "Usage: add <task> [priority] [due] [notes]  |  "
            "done <fragment>  |  withdraw <fragment>  |  supersede <prefix>  |  clear  |  sync  |  "
            "list [--status <Status>] [--grep <substring>]  |  --repo-root <path> (anywhere)"
        )

    todos_path = repo_root / TODOS_FILE
    pipeline_path = repo_root / PIPELINE_FILE

    if cmd == "add":
        cmd_add(extra_args, todos_path)
    elif cmd == "done":
        cmd_done(extra_args, todos_path)
    elif cmd == "withdraw":
        cmd_withdraw(extra_args, todos_path)
    elif cmd == "supersede":
        cmd_supersede(extra_args, todos_path)
    elif cmd == "clear":
        cmd_clear(todos_path)
    elif cmd == "sync":
        cmd_sync(todos_path, pipeline_path)
    elif cmd == "list":
        cmd_list(extra_args, todos_path)
    else:
        out_error(f"Unknown command: {cmd}. Use: add, done, withdraw, supersede, clear, sync, list")


if __name__ == "__main__":
    main()
