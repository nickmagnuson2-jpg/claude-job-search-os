"""Tests for tools/source_check.py.

Focus is the invariant that motivates the tool: a recurring row must never be
closed without being recreated. The happy path is the least interesting part.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import source_check as sc  # noqa: E402


TODOS = """# Job To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|---|---|---|---|---|
| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |
| Unrelated task | Low | — | Pending | noise |

## Completed

| Task | Priority | Completed | Notes |
|---|---|---|---|
| Check ACME board for new job postings | High | 2026-09-02 | old instance |
"""

REGISTRY = {
    "sources": [
        {
            "id": "acme",
            "label": "ACME board",
            "todo_fragment": "Check ACME board for new job postings",
            "cadence_days": 3,
            "priority": "High",
            "note_label": "ACME job board",
        }
    ]
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "data" / "job-todos.md").write_text(TODOS, encoding="utf-8")
    (tmp_path / "data" / "source-checks.json").write_text(
        json.dumps(REGISTRY), encoding="utf-8"
    )
    (tmp_path / "data" / "notes.md").write_text(
        "# Job Search Notes\n\n## Notes\n\n**2026-01-01:** older entry.\n",
        encoding="utf-8",
    )
    return tmp_path


# --- row matching -----------------------------------------------------------

def test_find_pending_row_ignores_completed_section():
    """A Completed row with identical text must not read as live."""
    row = sc.find_pending_row(TODOS, "Check ACME board for new job postings")
    assert row is not None
    assert row[3] == "Pending"
    assert row[2] == "2026-09-05"


def test_find_pending_row_returns_none_when_only_completed():
    only_completed = TODOS.replace("| Pending | Recurring. |", "| Completed | x |")
    assert sc.find_pending_row(only_completed, "Check ACME board") is None


# --- due computation --------------------------------------------------------

def test_missing_pending_row_reports_retired_and_due():
    """The failure the tool exists to catch: source silently dropped."""
    stripped = TODOS.replace(
        "| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |\n",
        "",
    )
    rows = sc.compute_due(REGISTRY["sources"], stripped, dt.date(2026, 9, 8))
    assert rows[0]["retired"] is True
    assert rows[0]["is_due"] is True


def test_overdue_days_are_counted():
    rows = sc.compute_due(REGISTRY["sources"], TODOS, dt.date(2026, 9, 8))
    assert rows[0]["is_due"] is True
    assert rows[0]["days_overdue"] == 3
    assert rows[0]["retired"] is False


def test_not_yet_due_is_not_flagged():
    rows = sc.compute_due(REGISTRY["sources"], TODOS, dt.date(2026, 9, 4))
    assert rows[0]["is_due"] is False
    assert rows[0]["days_overdue"] == -1


def test_due_on_the_day_counts_as_due():
    rows = sc.compute_due(REGISTRY["sources"], TODOS, dt.date(2026, 9, 5))
    assert rows[0]["is_due"] is True


def test_blank_due_date_is_treated_as_due():
    """A recurring row with no due date must not hide from the report."""
    blank = TODOS.replace("| High | 2026-09-05 | Pending |", "| High | — | Pending |")
    rows = sc.compute_due(REGISTRY["sources"], blank, dt.date(2026, 9, 8))
    assert rows[0]["is_due"] is True
    assert rows[0]["due"] is None


# --- note building ----------------------------------------------------------

def test_note_for_null_check_says_no_roles():
    note = sc.build_note(REGISTRY["sources"][0], dt.date(2026, 9, 8), None)
    assert note.startswith("**2026-09-08:**")
    assert "no available roles" in note
    assert "ACME job board" in note


def test_note_for_positive_check_carries_the_finding():
    note = sc.build_note(REGISTRY["sources"][0], dt.date(2026, 9, 8), "two BizOps roles")
    assert "two BizOps roles" in note
    assert "no available roles" not in note


def test_prepend_note_puts_newest_first(repo: Path):
    notes = repo / "data" / "notes.md"
    sc.prepend_note(notes, "**2026-09-08:** newest.")
    text = notes.read_text(encoding="utf-8")
    assert text.index("2026-09-08") < text.index("2026-01-01")
    assert "## Notes" in text


def test_prepend_note_refuses_file_without_anchor(tmp_path: Path):
    bad = tmp_path / "notes.md"
    bad.write_text("# No anchor here\n", encoding="utf-8")
    with pytest.raises(sc.SourceCheckError, match="no '## Notes' header"):
        sc.prepend_note(bad, "**x:** y")


# --- registry validation ----------------------------------------------------

def test_unknown_source_id_lists_known_ids():
    with pytest.raises(sc.SourceCheckError, match="acme"):
        sc.get_source(REGISTRY["sources"], "nope")


def test_registry_missing_required_key_is_fatal(repo: Path):
    (repo / "data" / "source-checks.json").write_text(
        json.dumps({"sources": [{"id": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(sc.SourceCheckError, match="missing"):
        sc.load_registry(repo)


def test_registry_absent_is_fatal(tmp_path: Path):
    with pytest.raises(sc.SourceCheckError, match="registry not found"):
        sc.load_registry(tmp_path)


def test_registry_empty_sources_is_fatal(repo: Path):
    (repo / "data" / "source-checks.json").write_text(
        json.dumps({"sources": []}), encoding="utf-8"
    )
    with pytest.raises(sc.SourceCheckError, match="no 'sources' list"):
        sc.load_registry(repo)


# --- the load-bearing invariant --------------------------------------------

def test_rotate_restores_table_when_recreate_fails(repo: Path, monkeypatch):
    """If the recreate half fails, the close must be rolled back.

    Leaving the row closed is exactly how a lead source gets silently retired.
    """
    todos = repo / "data" / "job-todos.md"
    before = todos.read_text(encoding="utf-8")

    def fake_run(repo_root, args):
        if args[0] == "done":
            # Simulate a real close: drop the Pending row.
            text = todos.read_text(encoding="utf-8")
            todos.write_text(
                text.replace(
                    "| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |\n",
                    "",
                ),
                encoding="utf-8",
            )
            return {"status": "ok"}
        return {"status": "error", "message": "disk full"}

    monkeypatch.setattr(sc, "_run_todo", fake_run)
    with pytest.raises(sc.SourceCheckError, match="recreate FAILED"):
        sc.rotate_todo(repo, REGISTRY["sources"][0], dt.date(2026, 9, 8), "note")

    assert todos.read_text(encoding="utf-8") == before
    assert sc.find_pending_row(todos.read_text(encoding="utf-8"), "Check ACME") is not None


def test_rotate_refuses_when_no_pending_row_exists(repo: Path):
    todos = repo / "data" / "job-todos.md"
    todos.write_text(
        TODOS.replace(
            "| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |\n",
            "",
        ),
        encoding="utf-8",
    )
    with pytest.raises(sc.SourceCheckError, match="no Pending todo row"):
        sc.rotate_todo(repo, REGISTRY["sources"][0], dt.date(2026, 9, 8), "note")


def test_rotate_sets_next_due_from_cadence(repo: Path, monkeypatch):
    captured = {}

    def fake_run(repo_root, args):
        if args[0] == "add":
            captured["due"] = args[3]
        return {"status": "ok"}

    monkeypatch.setattr(sc, "_run_todo", fake_run)
    res = sc.rotate_todo(repo, REGISTRY["sources"][0], dt.date(2026, 9, 8), "note")
    assert res["next_due"] == "2026-09-11"
    assert captured["due"] == "2026-09-11"


# --- CLI --------------------------------------------------------------------

def test_mark_requires_an_outcome_flag(repo: Path, capsys):
    rc = sc.main(["--repo-root", str(repo), "mark", "acme"])
    assert rc == 2
    assert "--none" in capsys.readouterr().err


def test_dry_run_writes_nothing(repo: Path):
    notes_before = (repo / "data" / "notes.md").read_text(encoding="utf-8")
    todos_before = (repo / "data" / "job-todos.md").read_text(encoding="utf-8")
    rc = sc.main(["--repo-root", str(repo), "mark", "acme", "--none",
                  "--date", "2026-09-08", "--dry-run"])
    assert rc == 0
    assert (repo / "data" / "notes.md").read_text(encoding="utf-8") == notes_before
    assert (repo / "data" / "job-todos.md").read_text(encoding="utf-8") == todos_before


def test_unknown_source_exits_nonzero(repo: Path):
    assert sc.main(["--repo-root", str(repo), "mark", "ghost", "--none"]) == 2


# --- gaps found by mutation survival, not by reading the tests --------------

def test_rotate_raises_when_close_itself_fails(repo: Path, monkeypatch):
    """If the close fails we must abort, not proceed to recreate a duplicate."""
    todos = repo / "data" / "job-todos.md"
    before = todos.read_text(encoding="utf-8")
    monkeypatch.setattr(
        sc, "_run_todo",
        lambda r, a: {"status": "error", "message": "locked"} if a[0] == "done"
        else {"status": "ok"},
    )
    with pytest.raises(sc.SourceCheckError, match="could not close"):
        sc.rotate_todo(repo, REGISTRY["sources"][0], dt.date(2026, 9, 8), "n")
    assert todos.read_text(encoding="utf-8") == before


def test_active_rows_skips_separators_headers_and_short_rows():
    text = (
        "| Task | Priority | Due | Status | Notes |\n"
        "|---|---|---|---|---|\n"
        "| Real row | High | 2026-09-05 | Pending | ok |\n"
        "| too | few |\n"
        "not a table line\n"
    )
    rows = sc._active_rows(text)
    assert len(rows) == 1
    assert rows[0][0] == "Real row"


def test_active_rows_excludes_the_header_row_specifically():
    rows = sc._active_rows("| Task | Priority | Due | Status | Notes |\n")
    assert rows == []


def test_active_rows_excludes_separator_specifically():
    rows = sc._active_rows("|---|---|---|---|---|\n")
    assert rows == []


def test_present_flag_is_true_when_row_exists():
    rows = sc.compute_due(REGISTRY["sources"], TODOS, dt.date(2026, 9, 8))
    assert rows[0]["present"] is True


def test_present_flag_is_false_when_row_missing():
    stripped = TODOS.replace(
        "| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |\n",
        "",
    )
    rows = sc.compute_due(REGISTRY["sources"], stripped, dt.date(2026, 9, 8))
    assert rows[0]["present"] is False


def test_today_uses_target_date_when_given():
    # Deliberately NOT today's date: an earlier version of this test used
    # 2026-09-08 and passed even when the target_date branch was mutated away,
    # because that happened to be the day it was written.
    assert sc._today("2001-03-14") == dt.date(2001, 3, 14)
    assert sc._today("2001-03-14") != dt.date.today()


def test_today_falls_back_to_real_today():
    assert sc._today(None) == dt.date.today()


def test_run_todo_parses_json_from_last_stdout_line(repo: Path, monkeypatch):
    class P:
        stdout = 'noise\n{"status": "ok", "action": "add"}'
        stderr = ""
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: P())
    assert sc._run_todo(repo, ["add"])["status"] == "ok"


def test_run_todo_reports_error_on_unparseable_output(repo: Path, monkeypatch):
    class P:
        stdout = "Traceback: boom"
        stderr = ""
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: P())
    res = sc._run_todo(repo, ["add"])
    assert res["status"] == "error"
    assert "boom" in res["message"]


def test_run_todo_reports_stderr_when_stdout_empty(repo: Path, monkeypatch):
    class P:
        stdout = ""
        stderr = "exploded"
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: P())
    assert sc._run_todo(repo, ["add"])["message"] == "exploded"


def test_due_json_mode_emits_parseable_payload(repo: Path, capsys):
    rc = sc.cmd_due(repo, dt.date(2026, 9, 8), as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target_date"] == "2026-09-08"
    assert payload["sources"][0]["id"] == "acme"


def test_due_text_mode_marks_overdue_with_day_count(repo: Path, capsys):
    sc.cmd_due(repo, dt.date(2026, 9, 8), as_json=False)
    out = capsys.readouterr().out
    assert out.startswith("DUE")
    assert "3d overdue" in out


def test_due_text_mode_omits_day_count_when_due_today(repo: Path, capsys):
    sc.cmd_due(repo, dt.date(2026, 9, 5), as_json=False)
    out = capsys.readouterr().out
    assert out.startswith("DUE")
    assert "overdue" not in out


def test_due_text_mode_shows_ok_when_not_due(repo: Path, capsys):
    sc.cmd_due(repo, dt.date(2026, 9, 4), as_json=False)
    assert capsys.readouterr().out.startswith("ok")


def test_due_text_mode_shouts_when_source_retired(repo: Path, capsys):
    todos = repo / "data" / "job-todos.md"
    todos.write_text(
        TODOS.replace(
            "| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |\n",
            "",
        ),
        encoding="utf-8",
    )
    sc.cmd_due(repo, dt.date(2026, 9, 8), as_json=False)
    out = capsys.readouterr().out
    assert out.startswith("RETIRED")
    assert "lead source was dropped" in out


# --- end-to-end: all three writes land, against the REAL todo_write.py ------

@pytest.fixture()
def repo_with_writer(repo: Path) -> Path:
    """The tmp repo, plus the real tools/todo_write.py it shells out to."""
    # Symlink the REAL tools/ rather than copying todo_write.py and chasing its
    # transitive imports. This exercises the actual writer, including its
    # round-trip conservation check, against a throwaway data/ tree.
    (repo / "tools").rmdir()
    (repo / "tools").symlink_to(REPO / "tools")
    return repo


def test_mark_end_to_end_writes_note_and_rotates_row(repo_with_writer: Path):
    repo = repo_with_writer
    rc = sc.main(["--repo-root", str(repo), "mark", "acme", "--none",
                  "--date", "2026-09-08"])
    assert rc == 0

    notes = (repo / "data" / "notes.md").read_text(encoding="utf-8")
    assert "**2026-09-08:**" in notes
    assert "no available roles" in notes
    assert notes.index("2026-09-08") < notes.index("2026-01-01")

    todos = (repo / "data" / "job-todos.md").read_text(encoding="utf-8")
    row = sc.find_pending_row(todos, "Check ACME board for new job postings")
    assert row is not None, "recurring row was not recreated -- source retired"
    assert row[2] == "2026-09-11"
    assert "RECREATED 2026-09-08" in row[4]


def test_mark_end_to_end_records_a_positive_finding(repo_with_writer: Path):
    repo = repo_with_writer
    rc = sc.main(["--repo-root", str(repo), "mark", "acme",
                  "--found", "two BizOps roles worth a look",
                  "--date", "2026-09-08"])
    assert rc == 0
    notes = (repo / "data" / "notes.md").read_text(encoding="utf-8")
    assert "two BizOps roles worth a look" in notes
    assert "no available roles" not in notes


def test_mark_twice_does_not_accumulate_duplicate_pending_rows(repo_with_writer: Path):
    repo = repo_with_writer
    sc.main(["--repo-root", str(repo), "mark", "acme", "--none", "--date", "2026-09-08"])
    sc.main(["--repo-root", str(repo), "mark", "acme", "--none", "--date", "2026-09-11"])
    todos = (repo / "data" / "job-todos.md").read_text(encoding="utf-8")
    pending = [
        c for c in sc._active_rows(todos)
        if "Check ACME board" in c[0] and c[3] == "Pending"
    ]
    assert len(pending) == 1, f"expected exactly one live row, got {len(pending)}"
    assert pending[0][2] == "2026-09-14"


def test_mark_emits_ok_json_with_next_due(repo_with_writer: Path, capsys):
    sc.main(["--repo-root", str(repo_with_writer), "mark", "acme", "--none",
             "--date", "2026-09-08"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["next_due"] == "2026-09-11"
    assert payload["source"] == "acme"


def test_main_dispatches_due_subcommand(repo: Path, capsys):
    rc = sc.main(["--repo-root", str(repo), "due", "--target-date", "2026-09-08", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["target_date"] == "2026-09-08"


def test_main_due_respects_target_date_flag(repo: Path, capsys):
    sc.main(["--repo-root", str(repo), "due", "--target-date", "2026-09-04"])
    assert capsys.readouterr().out.startswith("ok")


def test_dry_run_reports_what_it_would_do(repo: Path, capsys):
    """Dry-run must still SHOW the plan; silence would make it useless."""
    sc.main(["--repo-root", str(repo), "mark", "acme", "--none",
             "--date", "2026-09-08", "--dry-run"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry-run"
    assert payload["next_due"] == "2026-09-11"
    assert "no available roles" in payload["note"]


@pytest.mark.parametrize("argv_builder", [
    lambda r: ["--repo-root", r, "due", "--target-date", "2026-09-08", "--json"],
    lambda r: ["due", "--repo-root", r, "--target-date", "2026-09-08", "--json"],
])
def test_repo_root_accepted_on_either_side_of_subcommand(repo: Path, capsys, argv_builder):
    """Regression: `due --repo-root .` died in argparse until 2026-09-08.

    A live smoke test caught it; the unit tests did not, because they all
    happened to pass the flag before the subcommand. The /standup wiring uses
    the after-subcommand form.
    """
    assert sc.main(argv_builder(str(repo))) == 0
    assert json.loads(capsys.readouterr().out)["sources"][0]["id"] == "acme"


def test_repo_root_after_subcommand_works_for_mark(repo: Path, capsys):
    assert sc.main(["mark", "--repo-root", str(repo), "acme", "--none",
                    "--date", "2026-09-08", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"
