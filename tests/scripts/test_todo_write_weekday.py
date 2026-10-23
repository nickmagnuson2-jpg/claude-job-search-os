"""todo_write.py refuses a row whose weekday contradicts its own date.

Origin: memory/feedback_run_date_dont_infer_local_time.md. A week of todos was labelled
"Fri 8/15", "Sat 8/16", ... when 2026-08-15 was a Saturday, and the labels were carried
forward through later edits because they read as content, not as a checkable fact.
todo_write.py is the only write path for data/job-todos.md, so the check lives here and
reuses find_mismatches from tools/check_draft_weekday.py (one parser for drafts and todos).

Every date in these tests carries an explicit year, so the verdict does not depend on
the day the suite runs (a bare "8/15" resolves to the nearest year).
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
| Ship the widget | High | 2026-08-15 | Pending | original note |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Archived thing | High | 2026-06-08 | Completed 2026-06-08 |
"""


def _write(tmp_path):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FIXTURE, encoding="utf-8")
    return p


def _run(tmp_path, *args):
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


# ------------------------------------------------------------------------- add

def test_add_with_wrong_weekday_in_task_is_refused_and_file_unchanged(tmp_path):
    p = _write(tmp_path)
    code, out = _run(tmp_path, "add", "DAILY REP Fri 8/15/2026", "Med", "2026-08-15", "—")
    assert code == 1
    assert out["status"] == "error"
    assert "Fri 8/15/2026" in out["message"]
    assert "Saturday 2026-08-15" in out["message"]
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_add_with_wrong_weekday_in_notes_is_refused(tmp_path):
    p = _write(tmp_path)
    code, out = _run(tmp_path, "add", "Reply to Casey Doe", "Med", "2026-10-19",
                     "offer windows Monday, Oct 20, 2026 and Tue 10/20/2026")
    assert code == 1
    assert "Tuesday 2026-10-20" in out["message"]
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_add_with_correct_weekday_is_written(tmp_path):
    p = _write(tmp_path)
    code, out = _run(tmp_path, "add", "DAILY REP Sat 8/15/2026", "Med", "2026-08-15",
                     "then Sunday, Aug 16, 2026")
    assert code == 0, out
    assert out["status"] == "ok"
    assert "DAILY REP Sat 8/15/2026" in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- update

def test_update_notes_with_wrong_weekday_is_refused_and_file_unchanged(tmp_path):
    p = _write(tmp_path)
    code, out = _run(tmp_path, "update", "widget", "--notes-append", "nudge Fri 9/26/2026")
    assert code == 1
    assert "Saturday 2026-09-26" in out["message"]
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_update_task_with_wrong_weekday_is_refused(tmp_path):
    p = _write(tmp_path)
    code, _ = _run(tmp_path, "update", "widget", "--task", "Ship the widget Tue 9/16/2026")
    assert code == 1
    assert p.read_text(encoding="utf-8") == FIXTURE


def test_update_with_correct_weekday_is_written(tmp_path):
    p = _write(tmp_path)
    code, out = _run(tmp_path, "update", "widget", "--notes", "nudge Sat 9/26/2026")
    assert code == 0, out
    assert "nudge Sat 9/26/2026" in p.read_text(encoding="utf-8")


# ------------------------------------------- networking_write.py log --followup

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Casey Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Casey Doe — Acme Corp

"""

TODOS_WITH_FOLLOWUP = FIXTURE.replace(
    "| Ship the widget |",
    "| Follow up: Casey Doe — old nudge | Med | 2026-09-20 | Pending | — |\n| Ship the widget |",
)


def test_networking_followup_with_wrong_weekday_writes_nothing(tmp_path):
    """networking_write.py supersedes the contact's old follow-up, then adds the new one
    and ignores the add's exit code. Refusing only inside todo_write.py would withdraw
    the old follow-up and silently add nothing, so the log is refused before any write."""
    net = tmp_path / "data" / "networking.md"
    todos = tmp_path / "data" / "job-todos.md"
    net.parent.mkdir(parents=True, exist_ok=True)
    net.write_text(NETWORKING, encoding="utf-8")
    todos.write_text(TODOS_WITH_FOLLOWUP, encoding="utf-8")
    cmd = [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
           "log", "Casey Doe", "--date", "2026-09-19", "--type", "email",
           "--summary", "Sent the deck", "--followup", "nudge Fri 9/26/2026 if silent"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1
    assert "Saturday 2026-09-26" in json.loads(r.stdout)["message"]
    assert net.read_text(encoding="utf-8") == NETWORKING
    assert todos.read_text(encoding="utf-8") == TODOS_WITH_FOLLOWUP


def test_networking_followup_with_correct_weekday_still_adds_the_todo(tmp_path):
    net = tmp_path / "data" / "networking.md"
    todos = tmp_path / "data" / "job-todos.md"
    net.parent.mkdir(parents=True, exist_ok=True)
    net.write_text(NETWORKING, encoding="utf-8")
    todos.write_text(TODOS_WITH_FOLLOWUP, encoding="utf-8")
    cmd = [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
           "log", "Casey Doe", "--date", "2026-09-19", "--type", "email",
           "--summary", "Sent the deck", "--followup", "nudge Sat 9/26/2026 if silent"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout
    assert "Follow up: Casey Doe — nudge Sat 9/26/2026 if silent" in todos.read_text(encoding="utf-8")
