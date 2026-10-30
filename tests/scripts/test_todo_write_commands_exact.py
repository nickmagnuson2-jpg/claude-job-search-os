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
