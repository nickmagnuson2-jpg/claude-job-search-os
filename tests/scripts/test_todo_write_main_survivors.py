"""CLI-level pins for todo_write.py dispatch, `update` flag parsing, `audit`, and the
missing-section paths of `withdraw`.

Every test runs the real script through subprocess against a throwaway repo root and
asserts the exact JSON it prints plus the resulting file, so each one fails if the
behavior changes, not only if a particular line changes. Placeholder data only (this
repo is public).
"""
import json
import os
import subprocess
import sys

from conftest import TOOLS_DIR

FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Live active task | High | 2026-11-02 | Pending | still real |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Finished widget review | Med | 2026-06-08 | Completed 2026-06-08 |
| Stranded prep call | High | 2026-08-06 | Pending | left behind |
"""

USAGE = ("Usage: todo_write.py <add|update|done|rotate|withdraw|supersede|clear|sync|list> "
         "[args...]")


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _run(tmp_path, *args):
    """(exit code, parsed stdout JSON). Never raises on a non-zero exit."""
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


# --- main() dispatch ---------------------------------------------------------------

def test_no_subcommand_prints_the_usage_error(tmp_path):
    p = _write(tmp_path)
    rc, out = _run(tmp_path)
    assert rc == 1
    assert out == {"status": "error", "message": USAGE}
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_unknown_subcommand_is_refused_by_name_and_writes_nothing(tmp_path):
    p = _write(tmp_path)
    rc, out = _run(tmp_path, "frobnicate")
    assert rc == 1
    assert out == {
        "status": "error",
        "message": "Unknown command: frobnicate. Use: add, update, done, rotate, "
                   "withdraw, supersede, clear, sync, list, audit",
    }
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_list_reports_every_row_of_both_sections(tmp_path):
    p = _write(tmp_path)
    rc, out = _run(tmp_path, "list")
    assert rc == 0
    assert out == {
        "status": "ok", "action": "list", "summary": "3 todo(s)", "count": 3,
        "entries": [
            {"section": "Active", "task": "Live active task", "priority": "High",
             "due": "2026-11-02", "status": "Pending", "notes": "still real"},
            {"section": "Completed", "task": "Finished widget review", "priority": "Med",
             "due": "2026-06-08", "status": "Done", "notes": "Completed 2026-06-08"},
            {"section": "Completed", "task": "Stranded prep call", "priority": "High",
             "due": "2026-08-06", "status": "Done", "notes": "Pending | left behind"},
        ],
    }
    assert p.read_text(encoding="utf-8") == FIXTURE


# --- audit ---------------------------------------------------------------------------

def test_audit_reports_each_misfiled_row_with_its_columns(tmp_path):
    p = _write(tmp_path)
    rc, out = _run(tmp_path, "audit")
    assert rc == 0
    assert out == {
        "status": "ok", "action": "audit",
        "summary": "1 misfiled row(s): under ## Completed but still un-finished",
        "misfiled_count": 1,
        "misfiled": [{"line": 14, "task": "Stranded prep call", "priority": "High",
                      "due": "2026-08-06", "status": "Pending"}],
    }
    assert p.read_text(encoding="utf-8") == FIXTURE


# --- update --------------------------------------------------------------------------

def test_update_refuses_a_bare_word_in_a_field_flag_position(tmp_path):
    """main() only screens `--word` tokens, so a bare word reaches _parse_update_flags,
    which must refuse it rather than accept it as a field and write a no-op "update"."""
    p = _write(tmp_path)
    rc, out = _run(tmp_path, "update", "Live active", "priority", "Low")
    assert rc == 1
    assert out == {
        "status": "error",
        "message": "update: unknown field flag priority. Allowed: --due, --notes, "
                   "--notes-append, --notes-prepend, --priority, --task",
    }
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_update_with_no_match_does_not_blame_an_unrelated_misfiled_row(tmp_path):
    """The misfiled-row hint names only stranded rows that MATCH the fragment."""
    p = _write(tmp_path)
    rc, out = _run(tmp_path, "update", "Quarterly budget", "--priority", "Low")
    assert rc == 1
    assert out == {"status": "error",
                   "message": "No ACTIVE task found matching: Quarterly budget"}
    assert p.read_text(encoding="utf-8") == FIXTURE


# --- withdraw on a file missing a section ---------------------------------------------

NO_ACTIVE = """\
# Job Search To-Dos

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Finished widget review | Med | 2026-06-08 | Completed 2026-06-08 |
"""


def test_withdraw_without_an_active_section_reaches_the_completed_row_then_rolls_back(tmp_path):
    """With no ## Active the search falls through to ## Completed and FINDS the row (a
    miss would say "No task found"); the save's round-trip check then refuses a file
    without both sections and rolls the write back."""
    p = _write(tmp_path, NO_ACTIVE)
    rc, out = _run(tmp_path, "withdraw", "widget review")
    assert rc == 1
    assert out["reason"] == "roundtrip_missing_section"
    assert out["writer_counts"] == [-1, 1]
    assert out["reader_counts"] == [-1, 1]
    assert "The mutation was ROLLED BACK" in out["message"]
    assert p.read_text(encoding="utf-8") == NO_ACTIVE


NO_COMPLETED = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Live active task | High | 2026-11-02 | Pending | still real |
"""


def test_withdraw_without_a_completed_section_reports_no_match_and_writes_nothing(tmp_path):
    p = _write(tmp_path, NO_COMPLETED)
    rc, out = _run(tmp_path, "withdraw", "Quarterly budget")
    assert rc == 1
    assert out == {"status": "error", "message": "No task found matching: Quarterly budget"}
    assert p.read_text(encoding="utf-8") == NO_COMPLETED


def test_withdraw_on_a_file_with_neither_section_reports_no_match(tmp_path):
    body = "# Job Search To-Dos\n\nNothing tabled yet.\n"
    p = _write(tmp_path, body)
    rc, out = _run(tmp_path, "withdraw", "Live active")
    assert rc == 1
    assert out == {"status": "error", "message": "No task found matching: Live active"}
    assert p.read_text(encoding="utf-8") == body
