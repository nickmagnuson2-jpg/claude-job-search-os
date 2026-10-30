"""todo_write.py add / withdraw / update / supersede / clear / sync: whole-file results and
error paths, from the 2026-10-30 mutation run (survivors where only "ok" was checked)."""
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

TODAY = date.today().isoformat()
AH = "| Task | Priority | Due | Status | Notes |\n|---|---|---|---|---|\n"
CH = "| Task | Priority | Completed | Notes |\n|---|---|---|---|\n"
FILE = ("# To-Dos\n\n## Active\n\n" + AH +
        "| Follow up: Sam — call | High | 2026-01-05 | Pending | n1 |\n"
        "| Follow up: Sam — email | High | — | Pending | n2 |\n"
        "| Ship deck | High | — | Done | d1 |\n"
        "| Drop plan | High | — | Withdrawn 2026-01-02 (moot) | w1 |\n"
        "\n## Completed\n\n" + CH + "| Old item | Low | 2026-01-01 | Completed 2026-01-01 |\n")
NO_ACTIVE = "# To-Dos\n\n## Completed\n\n" + CH


def run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "todo_write.py"), "--repo-root", str(tmp_path), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode


def todos(tmp_path, text=FILE):
    return write_fixture(tmp_path, "data/job-todos.md", text)


@pytest.mark.parametrize("cmd", ["add", "withdraw", "supersede", "update"])
def test_each_command_without_arguments_is_a_usage_error(tmp_path, cmd):
    todos(tmp_path)
    out, code = run(tmp_path, cmd)
    assert code == 1 and ("Usage" in out["message"] or "usage" in out["message"]), out


@pytest.mark.parametrize("args", [("add", "x"), ("update", "x", "--due", "2026-01-09"),
                                  ("supersede", "x"), ("clear",)])
def test_each_command_without_an_active_section_refuses(tmp_path, args):
    p = todos(tmp_path, NO_ACTIVE)
    write_fixture(tmp_path, "data/job-pipeline.md",
                  "## Active\n| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
                  "|---|---|---|---|---|---|---|---|\n| Acme Corp | PM | Rejected | 2026-01-01 | — | — | — | — |\n")
    out, code = run(tmp_path, *args)
    assert code == 1 and "Active section" in out["message"], (args, out)
    assert p.read_text(encoding="utf-8") == NO_ACTIVE


def test_add_writes_the_row_and_an_unknown_priority_becomes_med(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "add", "Brand new item", "Urgent", "2026-01-09", "nn")
    assert code == 0 and "warning" not in out, out
    assert "| Drop plan | High | — | Withdrawn 2026-01-02 (moot) | w1 |\n| Brand new item | Med | 2026-01-09 | Pending | nn |\n" \
        in p.read_text(encoding="utf-8")


def test_add_warns_on_a_similar_task_but_not_on_the_header(tmp_path):
    todos(tmp_path)
    out, _ = run(tmp_path, "add", "Ship")
    assert out["warning"] == "Similar task already exists: Ship deck"
    out, _ = run(tmp_path, "add", "Task")
    assert "warning" not in out


def test_withdraw_moves_an_active_row_with_its_priority_and_notes(tmp_path):
    p = todos(tmp_path)
    assert run(tmp_path, "withdraw", "Sam — call")[1] == 0
    assert f"| Follow up: Sam — call | High | Withdrawn {TODAY} | n1 |" in p.read_text(encoding="utf-8")


def test_withdraw_with_two_active_matches_is_refused(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "withdraw", "Follow up: Sam")
    assert code == 1 and "Multiple matches" in out["message"]
    assert p.read_text(encoding="utf-8") == FILE


@pytest.mark.parametrize("args,needle", [
    (("Ship", "--bogus", "x"), "Unknown flag"),
    (("Ship", "--due", "2026-01-09", "--due", "2026-01-10"), "given twice"),
])
def test_update_flag_errors(tmp_path, args, needle):
    todos(tmp_path)
    out, code = run(tmp_path, "update", *args)
    assert code == 1 and needle in out["message"], out


def test_update_reports_every_changed_field_and_keeps_priority_and_status(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "update", "Sam — call", "--task", "Follow up: Sam — call back",
                    "--priority", "Low", "--due", "2026-01-09", "--notes", "new notes")
    assert code == 0 and out["changed"] == ["task", "priority", "due", "notes"], out
    out, code = run(tmp_path, "update", "call back", "--notes-append", "more")
    assert out["changed"] == ["notes-append"] and out["revisions_recorded"] == 0
    row = [l for l in p.read_text(encoding="utf-8").splitlines() if "call back" in l][0]
    assert row.startswith("| Follow up: Sam — call back | Low | 2026-01-09 | Pending | new notes")


def test_supersede_withdraws_every_live_prefix_match_with_priority_and_notes(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "supersede", "Follow up: Sam")
    assert code == 0 and out["superseded"] == 2, out
    text = p.read_text(encoding="utf-8")
    assert f"| Follow up: Sam — call | High | Withdrawn {TODAY} | n1 |" in text
    assert f"| Follow up: Sam — email | High | Withdrawn {TODAY} | n2 |" in text
    out, _ = run(tmp_path, "supersede", "Task")
    assert out["superseded"] == 0


def test_clear_archives_done_and_withdrawn_rows_with_their_notes(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "clear")
    assert code == 0 and (out["done"], out["withdrawn"]) == (1, 1), out
    assert "1 Done" in out["summary"] and "1 Withdrawn" in out["summary"]
    text = p.read_text(encoding="utf-8")
    assert f"| Ship deck | High | {TODAY} | d1 |" in text
    assert f"| Drop plan | High | Withdrawn {TODAY} | w1 |" in text
    out, _ = run(tmp_path, "clear")
    assert out["archived"] == 0


def test_sync_without_a_pipeline_file_skips(tmp_path):
    p = todos(tmp_path)
    out, code = run(tmp_path, "sync")
    assert code == 0 and out["withdrawn"] == 0 and "not found" in out["summary"], out
    assert p.read_text(encoding="utf-8") == FILE


# --- second pass over the todo_write survivors (2026-10-30, after the full-file check) ----

def test_withdraw_of_a_completed_row_keeps_its_priority_and_notes(tmp_path):
    text = FILE.replace("| Old item | Low | 2026-01-01 | Completed 2026-01-01 |",
                        "| Old item | High | 2026-01-01 | kept note |")
    p = todos(tmp_path, text)
    out, code = run(tmp_path, "withdraw", "Old item")
    assert code == 0, out
    row = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("| Old item")][0]
    assert row.startswith("| Old item | High |") and "kept note" in row and "Withdrawn" in row


def test_update_keeps_an_in_progress_status(tmp_path):
    p = todos(tmp_path, FILE.replace("| — | Pending | n2 |", "| — | In Progress | n2 |"))
    run(tmp_path, "update", "Sam — email", "--due", "2026-01-09")
    assert "| Follow up: Sam — email | High | 2026-01-09 | In Progress |" in p.read_text(encoding="utf-8")


def test_supersede_skips_closed_rows_and_reports_no_match_plainly(tmp_path):
    p = todos(tmp_path, FILE.replace("| Ship deck | High | — | Done | d1 |",
                                     "| Follow up: Sam — old | High | — | Done | d1 |"))
    out, _ = run(tmp_path, "supersede", "Follow up: Sam")
    assert out["superseded"] == 2 and "Follow up: Sam — old" not in out["tasks"]
    assert "| Follow up: Sam — old | High | — | Done | d1 |" in p.read_text(encoding="utf-8")
    out, _ = run(tmp_path, "supersede", "Nothing matches")
    assert out["summary"] == "No active todos matching prefix: Nothing matches"


def test_clear_summaries_name_only_what_moved(tmp_path):
    todos(tmp_path, FILE.replace("| Ship deck | High | — | Done | d1 |\n", ""))
    out, _ = run(tmp_path, "clear")
    assert "Withdrawn" in out["summary"] and "Done" not in out["summary"]
    todos(tmp_path, FILE.replace("| Drop plan | High | — | Withdrawn 2026-01-02 (moot) | w1 |\n", ""))
    out, _ = run(tmp_path, "clear")
    assert "Done" in out["summary"] and "Withdrawn" not in out["summary"]
    out, _ = run(tmp_path, "clear")
    assert out["summary"] == "No Done or Withdrawn items to archive"


def test_todo_subject_and_distinctive_company_helpers():
    sys.path.insert(0, str(TOOLS_DIR))
    import todo_write as tw
    assert tw.todo_subject("PARKED") == "" and tw.todo_subject("Apply to Acme") == "Apply to Acme"
    assert tw.is_distinctive_company("", set()) is False
    assert tw.is_distinctive_company("...", set()) is False


PIPE_HEAD = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
             "|---|---|---|---|---|---|---|---|\n")


def _pipeline(tmp_path, active_rows, archived_rows=""):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  "# P\n\n## Active Pipeline\n\n" + PIPE_HEAD + active_rows +
                  "\n## Archived\n\n" + PIPE_HEAD + archived_rows)


SYNC_TODOS = ("# T\n\n## Active\n\n" + AH +
              "| Prep for Zephyrix Labs call | High | — | Pending | n |\n"
              "| Prep for Northwind Robotics call | High | — | Pending | n |\n"
              "\n## Completed\n\n" + CH)


def test_sync_previews_each_terminal_company_once_and_spares_live_ones(tmp_path):
    p = todos(tmp_path, SYNC_TODOS)
    row = "| {} | PM | {} | 2026-01-01 | x | x | x | x |\n"
    _pipeline(tmp_path, row.format("Northwind Robotics", "Applied") + row.format("Zephyrix Labs", "Rejected")
              + "| Short | PM |\n", row.format("Zephyrix Labs", "Rejected"))
    out, code = run(tmp_path, "sync")
    assert code == 0 and out["candidates"] == [
        {"task": "Prep for Zephyrix Labs call", "priority": "High", "company": "Zephyrix Labs",
         "stage": "Rejected"}], out
    assert p.read_text(encoding="utf-8") == SYNC_TODOS


def test_sync_with_no_terminal_company_says_so(tmp_path):
    todos(tmp_path, SYNC_TODOS)
    _pipeline(tmp_path, "| Northwind Robotics | PM | Applied | 2026-01-01 | x | x | x | x |\n")
    out, _ = run(tmp_path, "sync")
    assert out["summary"] == "No terminal-stage companies in the pipeline" and out["withdrawn"] == 0


def test_sync_without_an_active_todo_section_refuses(tmp_path):
    p = todos(tmp_path, NO_ACTIVE)
    _pipeline(tmp_path, "| Zephyrix Labs | PM | Rejected | 2026-01-01 | x | x | x | x |\n")
    out, code = run(tmp_path, "sync")
    assert code == 1 and "Active section" in out["message"]
    assert p.read_text(encoding="utf-8") == NO_ACTIVE


def test_sync_never_reads_the_todo_header_as_a_task(tmp_path):
    todos(tmp_path, SYNC_TODOS)
    _pipeline(tmp_path, "| Task | PM | Rejected | 2026-01-01 | x | x | x | x |\n")
    out, _ = run(tmp_path, "sync")
    assert out.get("candidates", []) == []


def test_sync_reports_terminal_companies_too_ordinary_to_match_on(tmp_path):
    todos(tmp_path, SYNC_TODOS)
    _pipeline(tmp_path, "| Summit | PM | Rejected | 2026-01-01 | x | x | x | x |\n")
    out, _ = run(tmp_path, "sync")
    assert out["summary"] == "No terminal-stage companies with a distinctive name"
    assert out["skipped_companies"] == ["Summit"]
