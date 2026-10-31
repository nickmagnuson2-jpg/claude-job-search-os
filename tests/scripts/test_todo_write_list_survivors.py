"""Tests for the `list` subcommand of tools/todo_write.py.

`list` is the read-back a caller uses to confirm a write landed, so its output has to
be exact: which rows count as entries, how each column maps to a field, how Completed
rows get a Done/Withdrawn status, and how --status and --grep narrow the result.
Every test runs the real CLI against a crafted file and compares the whole JSON reply.
"""
import json
import os
import subprocess
import sys

from conftest import TOOLS_DIR

DASH = "—"  # the placeholder `list` reports for an empty Notes cell

FIXTURE = """\
# Job Search To-Dos

## Active

Prose under the heading is not a table row.

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Call Sam about the Acme role | High | 2026-11-02 | Pending | warm intro |
| Draft follow-up note | Med | 2026-11-03 | In Progress | mentions ACME board |
|  | Low | 2026-11-04 | Pending | blank task cell |
| Read the Example Corp report | Low | 2026-11-05 | Pending |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Send Acme thank-you | High | 2026-10-20 | sent by email |
| Old Example Corp lead | Med | Withdrawn 2026-10-21 | role closed |
|  | Low | 2026-10-22 | blank task cell |
| Drifted row | Low | 2026-10-23 | Pending | stray notes |
| Short completed row | Low | 2026-10-24 |

## Parking Lot

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Not part of the list | Low | 2026-12-01 | Pending | other section |
"""

CALL_SAM = {"section": "Active", "task": "Call Sam about the Acme role", "priority": "High",
            "due": "2026-11-02", "status": "Pending", "notes": "warm intro"}
DRAFT = {"section": "Active", "task": "Draft follow-up note", "priority": "Med",
         "due": "2026-11-03", "status": "In Progress", "notes": "mentions ACME board"}
READ = {"section": "Active", "task": "Read the Example Corp report", "priority": "Low",
        "due": "2026-11-05", "status": "Pending", "notes": DASH}
THANKS = {"section": "Completed", "task": "Send Acme thank-you", "priority": "High",
          "due": "2026-10-20", "status": "Done", "notes": "sent by email"}
OLD_LEAD = {"section": "Completed", "task": "Old Example Corp lead", "priority": "Med",
            "due": "Withdrawn 2026-10-21", "status": "Withdrawn", "notes": "role closed"}
DRIFTED = {"section": "Completed", "task": "Drifted row", "priority": "Low",
           "due": "2026-10-23", "status": "Done", "notes": "Pending | stray notes"}
SHORT = {"section": "Completed", "task": "Short completed row", "priority": "Low",
         "due": "2026-10-24", "status": "Done", "notes": DASH}

ALL = [CALL_SAM, DRAFT, READ, THANKS, OLD_LEAD, DRIFTED, SHORT]


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _list(tmp_path, *args):
    """Run `todo_write.py list <args>` and return (exit code, parsed JSON)."""
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), "list", *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


def _expect(entries):
    n = len(entries)
    return {"status": "ok", "action": "list", "summary": f"{n} todo(s)",
            "count": n, "entries": entries}


def test_list_reports_every_table_row_of_both_sections_and_nothing_else(tmp_path):
    # Prose, header rows, separator rows, blank-task rows and other sections are not
    # entries. Active columns map 1:1; a 4-column Active row gets the dash for notes.
    # Completed rows report their Completed cell as `due`, are Done unless that cell
    # starts with "Withdrawn", keep every cell past the third as notes (a drifted
    # 5-column row), and a 3-column row gets the dash.
    _write(tmp_path)
    code, out = _list(tmp_path)
    assert code == 0
    assert out == _expect(ALL)


def test_status_filter_is_case_insensitive_and_exact(tmp_path):
    _write(tmp_path)
    assert _list(tmp_path, "--status", "pending") == (0, _expect([CALL_SAM, READ]))
    # "In Progress" is a different status, not a superset match on "Pending".
    assert _list(tmp_path, "--status", "In Progress") == (0, _expect([DRAFT]))
    # Completed rows are filtered on their derived status.
    assert _list(tmp_path, "--status", "WITHDRAWN") == (0, _expect([OLD_LEAD]))
    assert _list(tmp_path, "--status", "Done") == (0, _expect([THANKS, DRIFTED, SHORT]))


def test_grep_matches_task_or_notes_case_insensitively(tmp_path):
    _write(tmp_path)
    # CALL_SAM and THANKS match on the task only, DRAFT on the notes only;
    # every other row matches neither and must be left out.
    assert _list(tmp_path, "--grep", "aCmE") == (0, _expect([CALL_SAM, DRAFT, THANKS]))
    # Notes-only match in a Completed row, including a drifted extra column.
    assert _list(tmp_path, "--grep", "stray") == (0, _expect([DRIFTED]))


def test_grep_and_status_combine(tmp_path):
    _write(tmp_path)
    assert _list(tmp_path, "--status", "Pending", "--grep", "acme") == (0, _expect([CALL_SAM]))
    assert _list(tmp_path, "--grep", "acme", "--status", "Done") == (0, _expect([THANKS]))


def test_no_match_reports_zero(tmp_path):
    _write(tmp_path)
    assert _list(tmp_path, "--grep", "no such task zzz") == (0, _expect([]))


def test_flag_without_a_value_is_ignored(tmp_path):
    # A trailing --grep or --status with nothing after it does not filter and does
    # not crash: the full list comes back.
    _write(tmp_path)
    assert _list(tmp_path, "--grep") == (0, _expect(ALL))
    assert _list(tmp_path, "--status") == (0, _expect(ALL))


def test_a_missing_section_contributes_no_entries(tmp_path):
    active_only, completed_part = FIXTURE.split("## Completed")
    _write(tmp_path, active_only)
    assert _list(tmp_path) == (0, _expect([CALL_SAM, DRAFT, READ]))

    _write(tmp_path, "# Job Search To-Dos\n\n## Completed" + completed_part)
    assert _list(tmp_path) == (0, _expect([THANKS, OLD_LEAD, DRIFTED, SHORT]))
