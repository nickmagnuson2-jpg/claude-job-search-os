#!/usr/bin/env python3
"""
todo_write.py — Atomic mutations for data/job-todos.md.

Handles add/done/clear/sync without loading the file into Claude's context window.
Each command reads the file, performs a targeted line-level mutation, and writes back.

Commands:
  add <task> <priority> <due> <notes>  — append row to Active section
  update <task_fragment> --field val   — edit an ACTIVE row's fields in place without
                                         retyping the rest. Fields: --task, --priority,
                                         --due, --notes (replaces), --notes-append,
                                         --notes-prepend. STATUS IS NOT UPDATABLE —
                                         done/withdraw/supersede own that transition.
                                         --task/--due/--notes stamp a `[rev <date>: ...]`
                                         marker into notes, since they overwrite; the
                                         append/prepend forms lose nothing and do not.
                                         No marker when the edit only INSERTED text
                                         (subsequence test), since nothing was lost.
  done <task_fragment>                 — move matching row to Completed (a completion)
  withdraw <task_fragment>             — mark matching row Withdrawn, not Done (NOT a completion;
                                         also corrects a row already mis-marked Completed)
  clear                                — move Done/Withdrawn rows to Completed
  sync                                 — auto-withdraw Active todos whose company has a
                                         terminal stage anywhere in the pipeline (either
                                         section); a live row for that company always wins
  audit                                — READ-ONLY. Report rows whose physical section
                                         disagrees with their status column (a row under
                                         ## Completed still marked Pending). Such rows are
                                         invisible to done/update and to the Active list,
                                         yet todos_summary.py still counts them, so they
                                         inflate the overdue count indefinitely. 18 existed
                                         in the live file on 2026-08-17, produced by the
                                         2026-08-05 bad sync + restore-from-backup.
                                         Reports line numbers; never writes — disposition
                                         (done vs withdraw vs back-to-Active) is judgment.
  list [--status <Status>] [--grep <substring>]
                                        — print todos (Active + Completed) as structured JSON;
                                         verify a mutation landed without grepping the raw file.
                                         --status filters on exact status (Pending/In Progress/
                                         Done/Withdrawn); --grep filters task+notes substring
                                         (case-insensitive). Filters compose (AND).

Every mutation is ROUND-TRIP VERIFIED: save_lines re-reads the written file with
todo_daily_metrics.section_row_counts (the downstream consumer) and rolls the file back
if the reader sees different per-section row counts, or a missing section. Verifying a
write with the tool that made it proves nothing about the tool that reads it -- see
tests/scripts/test_todo_write_roundtrip.py for the 2026-08-17 failure this closes.

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
  PYTHONIOENCODING=utf-8 python3 tools/todo_write.py audit
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Shared freeform-stage classifier (single source of truth). See stage_vocab.py.
# Enrolled 2026-08-14: this module previously carried its own exact-match
# TERMINAL_STAGES = {"Withdrawn","Rejected","Accepted"}, which made `sync` blind to
# every descriptive close ("Closed - they passed", "Declined", "Considered - passed").
from stage_vocab import is_terminal_stage  # noqa: E402

TODOS_FILE = "data/job-todos.md"
PIPELINE_FILE = "data/job-pipeline.md"
COMPLETED_HEADER = "| Task | Priority | Completed | Notes |"
COMPLETED_SEP = "| --- | --- | --- | --- |"

# Statuses in the TODO table's Status column that mean the row is finished. This is a
# different vocabulary from pipeline stages and must not be conflated with them: the
# writer only ever emits Pending / In Progress / Done / Withdrawn for a todo.
TERMINAL_TODO_STATUSES = {"Done", "Withdrawn"}


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


def out_error(message: str, **extra) -> None:
    d = {"status": "error", "message": message}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Misfiled rows
# ---------------------------------------------------------------------------
# A "misfiled row" sits physically under ## Completed while its status column still
# reads Pending / In Progress. It is produced by a bad `sync` + restore-from-backup:
# the 2026-08-05 sync withdrew 18 todos, the file was restored, and the restore left a
# contiguous block relocated into Completed with their status untouched.
#
# Such a row is invisible to `done` and `update` (both search ## Active only) and
# invisible to anyone reading the Active list -- yet todos_summary.py still counts it
# Pending, so it inflates the overdue count on every /standup indefinitely. An audit on
# 2026-08-17 found 18 of them in the owner's live file.
#
# The column-shift trap: a misfiled row is in ACTIVE format
#   Task | Priority | Due | Status | Notes
# inside a section whose schema is COMPLETED format
#   Task | Priority | Completed | Notes
# so the STATUS value lands in the position the Completed schema reads as `notes`.
# Any repair that ignores this leaves the literal string "Pending" in a completed row's
# notes column. That is what MISFILED_STATUSES + the notes-scrub in cmd_done handle.
#
# Origin: memory/feedback_todo_write_done_misses_misfiled_rows.md (2 fires: 8/06, 8/17).

MISFILED_STATUSES = {"pending", "in progress"}


def is_misfiled_row(cols: list[str]) -> bool:
    """True if a row inside ## Completed still carries an un-finished status value.

    Discriminates on the STATUS VALUE, not on mere presence in the section -- a
    genuinely completed row lives there legitimately and must not be flagged.
    """
    if len(cols) < 4:
        return False
    return cols[3].strip().lower() in MISFILED_STATUSES


def find_misfiled(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Return [(line_index, cols)] for every misfiled row under ## Completed."""
    comp_start, comp_end = find_section(lines, "## Completed")
    if comp_start == -1:
        return []
    return [
        (i, parse_cols(lines[i]))
        for i in range(comp_start, comp_end)
        if is_data_row(lines[i]) and is_misfiled_row(parse_cols(lines[i]))
    ]


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


def _count_rows(lines: list[str], header: str) -> int:
    """Data rows under `header`, counted the WRITER's way (line-by-line walk)."""
    start, end = find_section(lines, header)
    if start == -1:
        return -1
    return sum(1 for i in range(start, end) if is_data_row(lines[i]))


def writer_section_counts(lines: list[str]) -> tuple[int, int]:
    """(active_rows, completed_rows) as this module sees them."""
    return _count_rows(lines, "## Active"), _count_rows(lines, "## Completed")


def reader_section_counts(path: Path) -> tuple[int, int]:
    """(active_rows, completed_rows) as the DOWNSTREAM CONSUMER sees them.

    Deliberately imported from todo_daily_metrics rather than reimplemented: the
    entire point is to cross the writer/reader boundary. A local reimplementation
    would share this module's blind spots and prove nothing.
    """
    import todo_daily_metrics as tdm

    return tdm.section_row_counts(path.read_text(encoding="utf-8"))


def save_lines(path: Path, lines: list[str], original_content: str) -> None:
    """Rejoin lines, write atomically, then VERIFY WITH THE CONSUMER.

    Round-trip invariant: after the write, the downstream parser must see the same
    number of rows in each section that this module just wrote. On divergence the
    file is rolled back to `original_content` and the command errors, so a mutation
    that would corrupt the reader's view never lands.

    Why this exists (2026-08-17): a Notes cell containing the literal "## Completed"
    made todo_daily_metrics' unanchored section regex match inside a table row. Its
    Completed section collapsed from 744 rows to 2 and `completed_today` reported 0
    while five rows read "Completed 2026-08-17". Every check this module had passed,
    because they all walk lines and were never vulnerable — the writer's checks and
    the writer's bug were on the same side of the boundary. The regex is now anchored,
    but the class is not regex-specific: any future divergence between how these two
    modules read the file is caught here, at the write, instead of surfacing as a
    plausible-looking wrong number in the daily log.

    Cost is one extra parse per mutation on a file that is written a handful of times
    a day. Cheap enough not to think about.
    """
    content = "\n".join(lines)
    if original_content.endswith("\n"):
        content += "\n"
    write_atomic(path, content)

    expected = writer_section_counts(lines)
    try:
        actual = reader_section_counts(path)
    except Exception as exc:  # parser raised (schema drift, etc.) -> still a divergence
        write_atomic(path, original_content)
        out_error(
            f"Round-trip check could not parse the file after the write, so the "
            f"mutation was rolled back: {type(exc).__name__}: {exc}",
            reason="roundtrip_unreadable",
        )
        return

    # A missing section (-1) must fail even when both sides agree it is missing:
    # count-equality alone would happily pass a file whose header was deleted, since
    # writer and reader would concur on "not found". Structure is an invariant, not a
    # quantity. Caught by test_guard_catches_a_real_section_destroying_write.
    if -1 in expected or -1 in actual:
        write_atomic(path, original_content)
        out_error(
            f"Round-trip check found a MISSING SECTION after the write — "
            f"writer (active={expected[0]}, completed={expected[1]}), "
            f"reader (active={actual[0]}, completed={actual[1]}); -1 means the "
            f"section header was not found. The mutation was ROLLED BACK. "
            f"job-todos.md must always carry both '## Active' and '## Completed'.",
            reason="roundtrip_missing_section",
            writer_counts=list(expected),
            reader_counts=list(actual),
        )
        return

    if expected != actual:
        write_atomic(path, original_content)
        out_error(
            f"Round-trip mismatch — the write landed but the downstream parser sees a "
            f"different file. Writer counted (active={expected[0]}, "
            f"completed={expected[1]}); reader sees (active={actual[0]}, "
            f"completed={actual[1]}). The mutation was ROLLED BACK and the file is "
            f"unchanged. Most likely a cell contains text the reader mistakes for "
            f"structure (a literal '## Active'/'## Completed' at the start of a line).",
            reason="roundtrip_mismatch",
            writer_counts=list(expected),
            reader_counts=list(actual),
        )


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
        # Fallback: the row may be MISFILED -- physically in ## Completed but still
        # Pending. Repair it in place (it is already in the right section; only the
        # status cell is wrong) and say so, rather than reporting a false absence.
        # Deliberately narrow: only rows whose status is un-finished qualify, so a
        # genuine completion is never matched and never silently re-dated.
        misfiled = [
            (i, cols) for i, cols in find_misfiled(lines)
            if cols and cols[0] and fragment in cols[0].lower()
        ]
        if len(misfiled) > 1:
            opts = "\n".join(f"  - {c[0]}" for _, c in misfiled)
            out_error(f"Multiple misfiled matches — be more specific:\n{opts}")
        if len(misfiled) == 1:
            row_idx, cols = misfiled[0]
            task = cols[0]
            priority = cols[1] if len(cols) > 1 else "Med"
            # cols[2] is the Due date under the ACTIVE schema; cols[3] is the status
            # word, which must NOT survive into the notes column. Anything past cols[3]
            # is the real notes payload.
            orig_due = cols[2].strip() if len(cols) > 2 else ""
            tail = " | ".join(cols[4:]).strip() if len(cols) > 4 else ""
            bits = [f"Completed {today_str}", "repaired misfiled row 2026-08-17"]
            if orig_due and orig_due != "—":
                bits.append(f"was due {orig_due}")
            if tail:
                bits.append(tail)
            lines[row_idx] = fmt_completed(task, priority, today_str, " | ".join(bits))
            save_lines(todos_path, lines, content)
            out_ok("done", f"Completed (repaired misfiled row): {task}",
                   task=task, repaired_misfiled=True, found_in_section="## Completed")
            return
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
            # Among Completed matches these are NOT equally plausible targets: a
            # MISFILED row (status still Pending) is the only un-finished one, so it
            # is uniquely identifiable and is what the caller meant. Narrow to it.
            # Deliberately narrow -- two misfiled rows remain genuinely ambiguous.
            # Origin 2026-08-17: repairing the live file created exactly this state (a
            # recovered duplicate beside the stranded original) and withdraw deadlocked.
            misfiled_matches = [(i, c) for i, c in matches if is_misfiled_row(c)]
            if len(misfiled_matches) == 1:
                matches = misfiled_matches
            else:
                opts = "\n".join(f"  - {c[0]}" for _, c in matches)
                out_error(f"Multiple matches — be more specific:\n{opts}")
        if len(matches) == 1:
            row_idx, cols = matches[0]
            task = cols[0]
            priority = cols[1] if len(cols) > 1 else "Med"
            completed_cell = cols[2] if len(cols) > 2 else ""
            notes = cols[3] if len(cols) > 3 else "—"
            # A MISFILED row's status word sits in the position this schema reads as
            # `notes`, so scrub it or the repaired row ends up saying "Pending" forever.
            # Caught on REAL data 2026-08-17 after the fixture suite passed: withdraw
            # only ever stripped a leading "Completed <date>" marker.
            stranded_status = notes.strip().lower() in MISFILED_STATUSES
            if "withdrawn" in completed_cell.lower() or "withdrawn" in notes.lower():
                if not stranded_status:
                    out_ok("withdrawn", f"Already withdrawn: {task}", task=task, changed=False)
                    return
                # Already withdrawn but carrying the residue of a pre-fix repair: scrub
                # it in place rather than leaving a permanently confusing row.
                lines[row_idx] = fmt_completed(task, priority, completed_cell,
                                               "— | was misfiled, status residue scrubbed 2026-08-17")
                save_lines(todos_path, lines, content)
                out_ok("withdrawn", f"Scrubbed stranded status on: {task}",
                       task=task, changed=True, scrubbed_status_residue=True)
                return
            # Preserve the original date if present, else use today.
            m = re.search(r"\d{4}-\d{2}-\d{2}", f"{completed_cell} {notes}")
            date_token = m.group(0) if m else today_str
            # Strip a leading "Completed <date>" marker from notes so no completion marker remains.
            clean_notes = re.sub(r"^Completed\s+\d{4}-\d{2}-\d{2}\s*\|?\s*", "", notes).strip() or "—"
            if stranded_status:
                clean_notes = "— | was misfiled under ## Completed, repaired 2026-08-17"
            lines[row_idx] = fmt_completed(task, priority, f"Withdrawn {date_token}", clean_notes)
            save_lines(todos_path, lines, content)
            out_ok("withdrawn", f"Corrected to Withdrawn: {task}", task=task, changed=True)
            return

    out_error(f"No task found matching: {args[0]}")


UPDATE_FIELD_FLAGS = {"--task", "--priority", "--due",
                      "--notes", "--notes-append", "--notes-prepend"}

# Changing one of these DESTROYS the prior value, so it earns a revision marker.
# --notes-append/--notes-prepend destroy nothing by construction, and priority is
# trivially recoverable, so neither is stamped. Record what a change LOSES, not
# every change -- otherwise rows already carrying 1-2k of notes grow without bound.
UPDATE_DESTRUCTIVE = {"--task", "--due", "--notes"}
REV_VALUE_CAP = 120


def is_insertion_only(old: str, new: str) -> bool:
    """True when `new` is `old` with text inserted and nothing removed.

    The test is SUBSEQUENCE containment, not substring. Substring only recognises a
    pure prefix or suffix edit; it misses an insertion into the MIDDLE, which is the
    common shape -- and was the exact shape of this verb's first live use, where a
    tracker went in after the date and before the colon. Every character of `old`
    still present, in order, is precisely the statement "nothing was deleted".

    Deletions, truncations and replacements all break the ordering and correctly
    still record. "Ship the widget" -> "Ship the thing" is NOT insertion-only: after
    "Ship the " there is no `w` left to match.
    """
    if not old:
        return True
    it = iter(new)
    return all(ch in it for ch in old)


def _parse_update_flags(args: list[str]) -> tuple[str, dict[str, str]]:
    """`update <fragment> --flag value ...` in any order. Returns (fragment, fields)."""
    usage = ("Usage: update <task_fragment> [--task X] [--priority X] [--due X] "
             "[--notes X] [--notes-append X] [--notes-prepend X]  (one or more)")
    if not args:
        out_error(usage)
    fragment = args[0]
    if fragment.startswith("--"):
        out_error(f"update needs a task fragment FIRST, before any flag. {usage}")

    fields: dict[str, str] = {}
    i = 1
    while i < len(args):
        flag = args[i]
        if flag not in UPDATE_FIELD_FLAGS:
            out_error(f"update: unknown field flag {flag}. "
                      f"Allowed: {', '.join(sorted(UPDATE_FIELD_FLAGS))}")
        if i + 1 >= len(args):
            out_error(f"update: {flag} needs a value")
        if flag in fields:
            out_error(f"update: {flag} given twice")
        fields[flag] = args[i + 1]
        i += 2

    if not fields:
        out_error(f"update: nothing to change — pass at least one field flag. {usage}")
    if "--notes" in fields and ({"--notes-append", "--notes-prepend"} & set(fields)):
        out_error("update: --notes REPLACES the whole cell, so combining it with "
                  "--notes-append/--notes-prepend is ambiguous. Pick one.")
    return fragment, fields


def cmd_update(args: list[str], todos_path: Path) -> None:
    """Edit an ACTIVE row's fields in place, without retyping the ones you are not changing.

    Built 2026-08-14 after the missing verb cost real work twice in one day: every
    edit meant `supersede` plus a re-`add` with the full task AND notes retyped, on
    rows carrying 1-2k characters of notes. That is a transcription-error generator.

    STATUS IS NOT UPDATABLE, deliberately. done/withdraw/supersede own the status
    transitions and carry side effects (section move, date stamp, and the
    Withdrawn-vs-Completed distinction tools/todo_daily_metrics.py counts on). A
    second path to the same state change is how two code paths start disagreeing.

    ACTIVE ROWS ONLY. Editing an archived row is rarer and riskier, and `withdraw`
    already handles the one archived-row correction that actually comes up.

    DESTRUCTIVE CHANGES LEAVE A TRAIL. --task/--due/--notes overwrite a value that is
    then gone, so each stamps `[rev <date>: <field> was "<old>"]` into notes -- unless
    the edit only INSERTED text, which loses nothing. See is_insertion_only.
    """
    fragment_raw, fields = _parse_update_flags(args)
    fragment = fragment_raw.lower()
    content, lines = load_todos(todos_path)

    act_start, act_end = find_section(lines, "## Active")
    if act_start == -1:
        out_error("No ## Active section found")

    matches = _find_in_section(lines, act_start, act_end, fragment)
    if not matches:
        # Refuse accurately. Mutating a stranded row's fields would cement the
        # strandedness, so update does NOT repair -- but a bare "No ACTIVE task found"
        # is what sent the 2026-08-06 investigation chasing match-string variants for
        # four turns against a row that was visibly alive in the file. Name the section.
        misfiled = [
            (i, cols) for i, cols in find_misfiled(lines)
            if cols and cols[0] and fragment in cols[0].lower()
        ]
        if misfiled:
            names = ", ".join(repr(c[0]) for _, c in misfiled)
            out_error(
                f"Found {names} in ## Completed with an un-finished status — this is a "
                f"MISFILED ROW, not an absent one. `update` will not mutate a stranded "
                f"row. Run `todo_write.py audit` to see all of them, then `done` or "
                f"`withdraw` to resolve this one.",
                reason="misfiled_row",
                found_in_section="## Completed",
            )
        out_error(f"No ACTIVE task found matching: {fragment_raw}")
    if len(matches) > 1:
        opts = "\n".join(f"  - {c[0]}" for _, c in matches)
        out_error(f"Multiple matches — be more specific:\n{opts}")

    row_idx, cols = matches[0]
    task = cols[0]
    priority = cols[1] if len(cols) > 1 else "Med"
    due = cols[2] if len(cols) > 2 else "—"
    status = cols[3] if len(cols) > 3 else "Pending"
    # Legacy rows may carry literal pipes in notes; rejoin exactly as cmd_withdraw does.
    notes = " | ".join(cols[4:]) if len(cols) > 4 else "—"

    today_str = date.today().strftime("%Y-%m-%d")
    revs: list[str] = []

    def _stamp(field_name: str, old: str, new: str) -> None:
        """Record the prior value ONLY when the change actually destroys it.

        Three ways nothing is lost, and none earns a marker:
          - there was no prior value
          - the cell held the "—" placeholder, which means "empty"
          - the edit was INSERTION-ONLY (see is_insertion_only)

        Not hypothetical. The first live use of this verb inserted a tracker into five
        task strings and stamped five markers quoting text still fully present in the
        new value. A trail that records non-losses is noise, and noise in an audit
        trail is worse than none: it trains you to skim past the real entries.
        """
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or old == "—" or is_insertion_only(old, new):
            return
        shown = old if len(old) <= REV_VALUE_CAP else old[:REV_VALUE_CAP].rstrip() + "..."
        revs.append(f'[rev {today_str}: {field_name} was "{shown}"]')

    changed: list[str] = []

    if "--task" in fields:
        _stamp("task", task, fields["--task"])
        task = fields["--task"]
        changed.append("task")
    if "--priority" in fields:
        priority = fields["--priority"]
        changed.append("priority")
    if "--due" in fields:
        _stamp("due", due, fields["--due"])
        due = fields["--due"]
        changed.append("due")

    if "--notes" in fields:
        _stamp("notes", notes, fields["--notes"])
        notes = fields["--notes"]
        changed.append("notes")
    else:
        base = "" if notes.strip() in ("", "—") else notes
        if "--notes-prepend" in fields:
            notes = f"{fields['--notes-prepend']} {base}".strip()
            changed.append("notes-prepend")
        if "--notes-append" in fields:
            notes = f"{base} {fields['--notes-append']}".strip()
            changed.append("notes-append")

    if revs:
        notes = f"{notes} {' '.join(revs)}".strip()

    lines[row_idx] = fmt_active(task, priority, due, status, notes)
    save_lines(todos_path, lines, content)
    out_ok("updated", f"Updated ({', '.join(changed)}): {task[:70]}",
           task=task, changed=changed, revisions_recorded=len(revs))


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
                    and status not in TERMINAL_TODO_STATUSES):
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


def cmd_sync(todos_path: Path, pipeline_path: Path, apply: bool = False) -> None:
    """Report (default) or withdraw (--apply) Active todos for terminal companies.

    PREVIEW BY DEFAULT, deliberately. Company-name matching against the task column
    cannot tell an opportunity todo from a todo that merely names the company, and on
    real data the difference is most of the result. Measured 2026-08-14 against the
    owner's live files: 31 candidates, of which roughly 4 were genuine. The rest were

      * name collisions with ordinary English (a company literally named after a common
        verb matched every todo using that verb),
      * live work matched through a shared channel or marketplace name,
      * relationship follow-ups ("Follow up: Casey Doe"), which outlive the opportunity
        by design and are the category the owner most wants preserved,
      * infra and learning todos that name-drop a company in the task text.

    Deciding which is which is judgment, so it belongs to the caller (a skill pass or
    the owner), not to a regex here. This command's job is to find candidates and report
    them honestly. `--apply` writes; bare `sync` never touches the file.
    """
    today_str = date.today().strftime("%Y-%m-%d")

    pipeline_content = read_file(pipeline_path)
    if not pipeline_content:
        out_ok("sync", "Pipeline file not found — skipping sync", withdrawn=0)
        return

    def _stage_rows(section: str | None):
        """Yield (company, stage) for every data row in a pipeline section."""
        if not section:
            return
        for line in section.splitlines():
            if (not line.startswith("|")
                    or line.startswith("| Company")
                    or re.match(r"^\|\s*:?-+", line)):
                continue
            cols = parse_cols(line)
            if len(cols) >= 3 and cols[0]:
                yield cols[0], cols[2]

    archived_match = re.search(r"^## Archived.*?\n(.*?)(?=^## |\Z)", pipeline_content,
                              re.DOTALL | re.MULTILINE)
    active_match = re.search(r"^## Active Pipeline.*?\n(.*?)(?=^## |\Z)",
                             pipeline_content, re.DOTALL | re.MULTILINE)
    archived_section = archived_match.group(1) if archived_match else None
    active_section = active_match.group(1) if active_match else None

    # Companies with a LIVE (non-terminal) row anywhere are never terminal, even if a
    # stale row elsewhere says otherwise. A company can legitimately appear twice (an
    # old withdrawn application plus a current live loop); the live row wins.
    # Origin: 2026-08-08, a stale duplicate row mass-withdrew 18 todos.
    live: set[str] = {
        company.lower()
        for company, stage in _stage_rows(active_section)
        if not is_terminal_stage(stage)
    }

    # Terminal companies come from BOTH sections. Archiving is the tidy path, but a row
    # closed in place with a descriptive stage ("Closed - they passed") is just as
    # terminal, and on 2026-08-14 that was 30 of them on the live pipeline — every one
    # invisible to this command while it used an exact-match stage set.
    seen: set[str] = set()
    terminal: list[tuple[str, str]] = []
    for company, stage in (*_stage_rows(archived_section), *_stage_rows(active_section)):
        key = company.lower()
        if key in live or key in seen or not is_terminal_stage(stage):
            continue
        seen.add(key)
        terminal.append((company, stage))

    if not terminal:
        out_ok("sync", "No terminal-stage companies in the pipeline", withdrawn=0)
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
        out_ok("sync", "No active todos matched terminal pipeline companies",
               withdrawn=0, candidates=[])
        return

    candidates = [
        {"task": cols[0], "priority": cols[1] if len(cols) > 1 else "Med",
         "company": company, "stage": stage}
        for _, cols, company, stage in to_withdraw
    ]

    if not apply:
        out_ok(
            "sync",
            f"{len(candidates)} candidate(s) found — NOTHING WRITTEN. "
            f"Review, then re-run with --apply to withdraw.",
            withdrawn=0, candidates=candidates, applied=False,
        )
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

def cmd_audit(todos_path: Path) -> None:
    """Report rows whose physical section disagrees with their status column. READ-ONLY.

    Exists because this class of damage is otherwise found by accident: the rows are
    invisible to `done`/`update` and to a human reading the Active list, while
    todos_summary.py still counts them. Reports line numbers so a hand-repair is
    possible, and never writes -- disposition (done vs withdraw vs move back to Active)
    is a judgment call that belongs to the caller.
    """
    _, lines = load_todos(todos_path)
    misfiled = find_misfiled(lines)
    rows = [
        {
            "line": i + 1,
            "task": cols[0],
            "priority": cols[1] if len(cols) > 1 else "",
            "due": cols[2] if len(cols) > 2 else "",
            "status": cols[3] if len(cols) > 3 else "",
        }
        for i, cols in misfiled
    ]
    summary = (
        f"{len(rows)} misfiled row(s): under ## Completed but still un-finished"
        if rows else "No misfiled rows — every row's section agrees with its status"
    )
    out_ok("audit", summary, misfiled_count=len(rows), misfiled=rows)


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
        out_error("Usage: todo_write.py <add|update|done|withdraw|supersede|clear|sync|list> [args...]")

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
    # `sync` takes --apply: it previews by default and only writes when asked.
    # `update` is partial-field by design: naming the field is the whole point, and a
    # positional form would need sentinel placeholders for every column you are NOT
    # changing, which reintroduces exactly the retyping this verb exists to delete.
    if cmd == "list":
        allowed_flags = {"--status", "--grep"}
    elif cmd == "sync":
        allowed_flags = {"--apply"}
    elif cmd == "update":
        allowed_flags = set(UPDATE_FIELD_FLAGS)
    else:
        allowed_flags = set()
    unknown_flags = [
        a for a in filtered if a.startswith("--") and len(a) > 2 and a not in allowed_flags
    ]
    if unknown_flags:
        out_error(
            f"Unknown flag(s): {', '.join(unknown_flags)}. "
            "todo_write.py is positional-only — pass bare values, not --flag names "
            "(except `list`, which takes --status/--grep). "
            "Usage: add <task> [priority] [due] [notes]  |  "
            "update <fragment> [--task|--priority|--due|--notes|--notes-append|--notes-prepend] X  |  "
            "done <fragment>  |  withdraw <fragment>  |  supersede <prefix>  |  clear  |  sync  |  "
            "list [--status <Status>] [--grep <substring>]  |  audit  |  --repo-root <path> (anywhere)"
        )

    todos_path = repo_root / TODOS_FILE
    pipeline_path = repo_root / PIPELINE_FILE

    if cmd == "add":
        cmd_add(extra_args, todos_path)
    elif cmd == "done":
        cmd_done(extra_args, todos_path)
    elif cmd == "withdraw":
        cmd_withdraw(extra_args, todos_path)
    elif cmd == "update":
        cmd_update(extra_args, todos_path)
    elif cmd == "supersede":
        cmd_supersede(extra_args, todos_path)
    elif cmd == "clear":
        cmd_clear(todos_path)
    elif cmd == "sync":
        cmd_sync(todos_path, pipeline_path, apply="--apply" in extra_args)
    elif cmd == "list":
        cmd_list(extra_args, todos_path)
    elif cmd == "audit":
        cmd_audit(todos_path)
    else:
        out_error(f"Unknown command: {cmd}. Use: add, update, done, withdraw, supersede, clear, sync, list, audit")


if __name__ == "__main__":
    main()
