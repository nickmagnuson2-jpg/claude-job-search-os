"""todo_write.py `done` and its helpers: whole-file results.

The 2026-10-30 mutation run left survivors across `done`, among them dropping the call
that puts the closed row into ## Completed: no test checked that the row arrived there.
These tests compare whole files and whole error payloads.
"""
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import todo_write as tw  # noqa: E402

TODAY = date.today().isoformat()
ACTIVE_HEAD = "| Task | Priority | Due | Status | Notes |\n|---|---|---|---|---|\n"
DONE_HEAD = "| Task | Priority | Completed | Notes |\n|---|---|---|---|\n"
FILE = ("# To-Dos\n\n## Active\n\n" + ACTIVE_HEAD +
        "| Email Sam about the deck | High | 2026-01-05 | Pending | sent draft |\n"
        "| Prep notes | Low | — | Pending | — |\n"
        "| Prep slides | Med | — | Pending | — |\n"
        "\n## Completed\n\n" + DONE_HEAD +
        "| Old task | Low | 2026-01-01 | Completed 2026-01-01 |\n")


def run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "todo_write.py"), "--repo-root", str(tmp_path), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode


def todos(tmp_path, text=FILE):
    return write_fixture(tmp_path, "data/job-todos.md", text)


# --- done -------------------------------------------------------------------------------

def test_done_moves_the_row_into_completed_with_its_notes(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "done", "Email Sam")
    assert code == 0 and out["remaining_active"] == 2, out
    assert p.read_text(encoding="utf-8") == FILE.replace(
        "| Email Sam about the deck | High | 2026-01-05 | Pending | sent draft |\n", "").replace(
        "| Old task | Low | 2026-01-01 | Completed 2026-01-01 |\n",
        "| Old task | Low | 2026-01-01 | Completed 2026-01-01 |\n"
        f"| Email Sam about the deck | High | {TODAY} | Completed {TODAY} / sent draft |\n")


def test_done_on_a_row_without_notes_writes_only_the_completion(tmp_path):
    p = todos(tmp_path)
    run(tmp_path, "done", "Prep notes")
    assert f"| Prep notes | Low | {TODAY} | Completed {TODAY} |\n" in p.read_text(encoding="utf-8")


def test_done_creates_the_completed_section_when_missing(tmp_path):
    text = FILE.split("\n## Completed")[0] + "\n"
    p = todos(tmp_path, text)
    out, code = run(tmp_path, "done", "Prep notes")
    assert code == 0, out
    assert p.read_text(encoding="utf-8").endswith(
        "\n## Completed\n\n| Task | Priority | Completed | Notes |\n| --- | --- | --- | --- |\n"
        f"| Prep notes | Low | {TODAY} | Completed {TODAY} |\n")


@pytest.mark.parametrize("args,needle", [
    ((), "Usage: done"),
    (("Prep",), "Multiple matches"),
    (("Task",), "No task found"),   # the table header row is never a task
])
def test_done_errors_write_nothing(tmp_path, args, needle):
    p = todos(tmp_path)
    out, code = run(tmp_path, "done", *args)
    assert code == 1 and needle in out["message"], out
    assert p.read_text(encoding="utf-8") == FILE


def test_done_without_an_active_section_says_so(tmp_path):
    text = "# To-Dos\n\n## Completed\n\n" + DONE_HEAD
    p = todos(tmp_path, text)
    out, code = run(tmp_path, "done", "x")
    assert code == 1 and out["message"] == "Could not find ## Active section in job-todos.md"
    assert p.read_text(encoding="utf-8") == text


def test_done_on_a_missing_file_is_an_error(tmp_path):
    out, code = run(tmp_path, "done", "x")
    assert code == 1 and out["message"].startswith("File not found or empty")


# --- the misfiled-row repair ------------------------------------------------------------

def _misfiled(*rows):
    return ("# To-Dos\n\n## Active\n\n" + ACTIVE_HEAD + "| Other | Low | — | Pending | — |\n"
            "\n## Completed\n\n" + DONE_HEAD + "".join(r + "\n" for r in rows))


def test_a_misfiled_row_is_repaired_in_place_with_its_due_and_notes(tmp_path):
    p = todos(tmp_path, _misfiled("| Stranded task | High | 2026-01-03 | Pending | ping again |"))
    out, code = run(tmp_path, "done", "Stranded")
    assert code == 0 and out["repaired_misfiled"] is True, out
    assert p.read_text(encoding="utf-8") == _misfiled(
        f"| Stranded task | High | {TODAY} | Completed {TODAY} / repaired misfiled row 2026-08-17 "
        "/ was due 2026-01-03 / ping again |")


def test_a_misfiled_row_without_due_or_notes_gets_only_the_repair_note(tmp_path):
    p = todos(tmp_path, _misfiled("| Stranded task | High | — | Pending |"))
    run(tmp_path, "done", "Stranded")
    assert p.read_text(encoding="utf-8") == _misfiled(
        f"| Stranded task | High | {TODAY} | Completed {TODAY} / repaired misfiled row 2026-08-17 |")


def test_two_misfiled_matches_are_refused(tmp_path):
    text = _misfiled("| Stranded A | High | — | Pending | x |", "| Stranded B | High | — | Pending | y |")
    p = todos(tmp_path, text)
    out, code = run(tmp_path, "done", "Stranded")
    assert code == 1 and "Multiple misfiled matches" in out["message"]
    assert p.read_text(encoding="utf-8") == text


# --- helpers ----------------------------------------------------------------------------

def test_helpers_are_strictly_boolean_and_bounded():
    assert tw.is_misfiled_row(["a", "b", "c"]) is False
    assert tw.is_misfiled_row(["a", "b", "c", "Pending"]) is True
    assert tw.find_misfiled(["## Active", "| x | y | z | Pending | n |"]) == []
    assert tw.is_data_row("not a row") is False
    assert tw.is_data_row("|---|---|") is False
    assert tw.is_data_row("| Task | Priority |") is False
    assert tw.is_data_row("| a | b |") is True
    assert tw.find_section(["# x", "## Other"], "## Active") == (-1, -1)
    assert tw._count_rows(["# x"], "## Active") == -1
    assert tw.read_file(tw.Path("/nonexistent/file.md")) == ""


def test_a_file_without_a_trailing_newline_keeps_none(tmp_path):
    p = todos(tmp_path, FILE.rstrip("\n"))
    run(tmp_path, "done", "Prep notes")
    assert not p.read_text(encoding="utf-8").endswith("\n")


@pytest.mark.parametrize("reader", [lambda p: (99, 99), lambda p: (_ for _ in ()).throw(ValueError("x"))])
def test_a_round_trip_failure_restores_the_original_file(tmp_path, monkeypatch, capsys, reader):
    p = todos(tmp_path)
    monkeypatch.setattr(tw, "reader_section_counts", reader)
    with pytest.raises(SystemExit):
        tw.cmd_done(["Prep notes"], p)
    assert p.read_text(encoding="utf-8") == FILE
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_withdraw_never_matches_the_table_header(tmp_path):
    """_find_in_section skips non-data rows; without that, 'Task' matched the header."""
    text = FILE.replace("Old task", "Old item")      # so only the header says "Task"
    p = todos(tmp_path, text)
    out, code = run(tmp_path, "withdraw", "Task")
    assert code == 1, out
    assert p.read_text(encoding="utf-8") == text
