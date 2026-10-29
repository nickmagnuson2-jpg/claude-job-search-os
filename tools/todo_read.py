"""todo_read.py - the one reader for data/job-todos.md, and the one definition of overdue.

Why it exists (S5, ruled 2026-10-28: "Yes, one parser"). Three readers of the same file each
had their own parser and disagreed on the same day: overdue 206 vs 197 (`<=` vs `<`),
pending 480 vs 485 (one counted Withdrawn and Done rows left in the Active section as still
active, one did not). attention.py, todos_summary.py and todo_daily_metrics.py now import
this module, and tests/scripts/test_todo_parser_consistency.py fails if a fourth copy of the
rule appears in any of them or if their numbers drift apart on the same file.

The rules, each stated once:

  * Sections are found by an ANCHORED header (`^## Active`, `^## Completed`). An unanchored
    search matched a Notes cell containing the text "## Completed" and truncated the section
    (fired 2026-08-17).
  * A row is a table line that is not the header and not a separator. A literal "---" task
    is the file's known spacer row and is skipped.
  * OPEN: a row in the Active section whose Status is not closed (stage_vocab's
    is_closed_todo_status: "Done", "Withdrawn <date> (...)"). Pending and In Progress are open.
  * OVERDUE: an open row whose Due cell holds a date strictly BEFORE today. A row due today
    is due today, not overdue.
  * COMPLETED ON a date: a Completed-section row whose Completed or Notes cell holds that
    date, unless the row was withdrawn (a withdrawal is not a completion).

  * LOGICAL DAY: before 04:00 local, a run with no explicit date is about the previous day
    (logical_today).

Schema drift (the Active header no longer reads Task | Priority | Due | Status | Notes)
raises SchemaDriftError rather than reading the wrong columns.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_guard import SchemaDriftError, assert_schema, find_header_line  # noqa: E402,F401
from stage_vocab import is_closed_todo_status  # noqa: E402

ACTIVE_COLUMNS = ["Task", "Priority", "Due", "Status", "Notes"]
# Before this hour (local), "today" is still the previous day (todo #31, 2026-10-29): a
# /checkout or /standup run at 00:30 after a late session is about the day that just ended.
ROLLOVER_HOUR = 4
COMPLETED_COLUMNS = ["Task", "Priority", "Completed", "Notes"]

SECTION_RE = r"^## {header}.*?\n(.*?)(?=^## |\Z)"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def section_text(content: str, header: str) -> str | None:
    """One section's body, or None when the header is missing."""
    m = re.search(SECTION_RE.format(header=header), content, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else None


def is_row(line: str) -> bool:
    """A table data row: starts with "|", is not a separator, is not the header."""
    if not line.startswith("|"):
        return False
    if re.match(r"^\|\s*:?-+:?\s*\|", line):
        return False
    return not line.startswith("| Task")


def section_row_counts(content: str) -> tuple[int, int]:
    """(active_rows, completed_rows) as the reader sees them; -1 for a missing section."""
    out = []
    for header in ("Active", "Completed"):
        body = section_text(content, header)
        out.append(-1 if body is None else sum(1 for line in body.splitlines() if is_row(line)))
    return out[0], out[1]


def parse_date(cell: str) -> date | None:
    """The first YYYY-MM-DD in a cell, or None ("—", "", or an impossible date)."""
    m = _DATE_RE.search(cell or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def active_rows(content: str) -> list[dict]:
    """Every Active-section row, open or not: {task, priority, due, due_date, status, notes}."""
    assert_schema(find_header_line(content, "| Task |"), ACTIVE_COLUMNS)
    body = section_text(content, "Active") or ""
    rows = []
    for line in body.splitlines():
        if not is_row(line):
            continue
        cols = _cells(line)
        if len(cols) < 4 or cols[0] in ("---", "—", ""):
            continue
        rows.append({"task": cols[0], "priority": cols[1], "due": cols[2],
                     "due_date": parse_date(cols[2]), "status": cols[3],
                     "notes": cols[4] if len(cols) > 4 else ""})
    return rows


def completed_rows(content: str) -> list[dict]:
    """Every Completed-section row: {task, priority, completed, notes, withdrawn}."""
    body = section_text(content, "Completed")
    if body is None:
        return []
    assert_schema(find_header_line(body, "| Task |"), COMPLETED_COLUMNS)
    rows = []
    for line in body.splitlines():
        if not is_row(line):
            continue
        cols = _cells(line)
        if len(cols) < 3:
            continue
        completed, notes = cols[2], (cols[3] if len(cols) > 3 else "")
        rows.append({"task": cols[0], "priority": cols[1], "completed": completed,
                     "notes": notes,
                     "withdrawn": "withdrawn" in completed.lower() or "withdrawn" in notes.lower()})
    return rows


def is_open(row: dict) -> bool:
    return not is_closed_todo_status(row["status"])


def is_overdue(row: dict, today: date) -> bool:
    d = row["due_date"]
    return is_open(row) and d is not None and d < today


def open_rows(content: str) -> list[dict]:
    return [r for r in active_rows(content) if is_open(r)]


def overdue_rows(content: str, today: date) -> list[dict]:
    return [r for r in active_rows(content) if is_overdue(r, today)]


def completed_on(content: str, day: date) -> list[dict]:
    d = day.isoformat()
    return [r for r in completed_rows(content)
            if not r["withdrawn"] and (d in r["completed"] or d in r["notes"])]


def logical_today(now: datetime | None = None, rollover_hour: int = ROLLOVER_HOUR) -> date:
    """The day a run is about: the calendar date, or the previous one before rollover_hour.

    Used by every daily tool when no --target-date is given (todo_daily_metrics,
    standup_collect, day_check), and printed by `todo_read.py --logical-date` for the
    skills' shell lines, so one rule decides which day a late-night run belongs to."""
    now = now or datetime.now()
    return (now - timedelta(days=1)).date() if now.hour < rollover_hour else now.date()


if __name__ == "__main__":
    if sys.argv[1:] == ["--logical-date"]:
        print(logical_today().isoformat())
        sys.exit(0)
    print("usage: todo_read.py --logical-date   (prints the day a run now is about)",
          file=sys.stderr)
    sys.exit(2)
