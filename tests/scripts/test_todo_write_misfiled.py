"""Tests for misfiled-row handling in tools/todo_write.py.

A "misfiled row" is a todo that sits physically under ## Completed while its status
column still reads Pending (or In Progress). It is produced by a bad `sync` + restore:
the 2026-08-05 sync withdrew 18 todos, the file was restored from backup, and the
restore left a contiguous block of rows relocated into Completed with their status
untouched.

Such a row is invisible to `done` and `update` (both search ## Active only), invisible
to a human reading the Active list, and yet still counted as Pending by
todos_summary.py -- so it inflates the overdue count on every /standup indefinitely.

Structural note that drove the shape of these tests: a misfiled row is in ACTIVE column
format (Task | Priority | Due | Status | Notes) sitting in a section whose header is
COMPLETED format (Task | Priority | Completed | Notes). So the status value lands in the
position the Completed schema reads as `notes`. Any repair has to handle that, or it
leaves the literal string "Pending" stranded in the notes column of a completed row.

Origin: 2026-08-06 (1st fire), 2026-08-17 (2nd fire + audit found 18 rows).
See memory/feedback_todo_write_done_misses_misfiled_rows.md.
"""
import json
import os
import subprocess
import sys

from conftest import run_script, TOOLS_DIR

# Two misfiled rows (status Pending, physically under ## Completed) plus one
# legitimately-completed row, so tests prove the audit discriminates rather than
# flagging everything in the section.
FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Live active task | High | — | Pending | still real |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Genuinely finished thing | High | 2026-06-08 | Completed 2026-06-08 |
| Stranded prep call | High | 2026-08-06 | Pending |
| Stranded parked hook | Low | — | Pending |
"""


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _run_raw(tmp_path, *args):
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def test_audit_finds_misfiled_rows(tmp_path):
    _write(tmp_path)
    res = run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert res["misfiled_count"] == 2
    tasks = [r["task"] for r in res["misfiled"]]
    assert "Stranded prep call" in tasks
    assert "Stranded parked hook" in tasks


def test_audit_does_not_flag_a_genuine_completion(tmp_path):
    """The discriminator has to be the status value, not merely living in Completed."""
    _write(tmp_path)
    res = run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    tasks = [r["task"] for r in res["misfiled"]]
    assert "Genuinely finished thing" not in tasks


def test_audit_does_not_flag_active_rows(tmp_path):
    _write(tmp_path)
    res = run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    tasks = [r["task"] for r in res["misfiled"]]
    assert "Live active task" not in tasks


def test_audit_reports_line_numbers(tmp_path):
    """Line numbers are what make a hand-repair possible; without them the report is a tease."""
    _write(tmp_path)
    res = run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    for row in res["misfiled"]:
        assert isinstance(row["line"], int) and row["line"] > 0


def test_audit_is_read_only(tmp_path):
    p = _write(tmp_path)
    before = p.read_text(encoding="utf-8")
    run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    assert p.read_text(encoding="utf-8") == before


def test_audit_clean_file_reports_zero(tmp_path):
    clean = FIXTURE.replace("| Stranded prep call | High | 2026-08-06 | Pending |\n", "")
    clean = clean.replace("| Stranded parked hook | Low | — | Pending |\n", "")
    _write(tmp_path, clean)
    res = run_script("todo_write.py", "audit", "--repo-root", str(tmp_path))
    assert res["misfiled_count"] == 0
    assert res["misfiled"] == []


# --------------------------------------------------------------------------
# done -- repairs in place
# --------------------------------------------------------------------------

def test_done_reaches_a_misfiled_row(tmp_path):
    """The regression that started this: `done` returned No-task-found for a live row."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "done", "Stranded prep call",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    row = next(l for l in p.read_text(encoding="utf-8").splitlines()
               if "Stranded prep call" in l)
    assert "Completed" in row
    assert "Pending" not in row


def test_done_on_misfiled_row_reports_the_repair_distinctly(tmp_path):
    """Silent repair would hide a data-integrity problem the owner needs to see."""
    _write(tmp_path)
    res = run_script("todo_write.py", "done", "Stranded prep call",
                     "--repo-root", str(tmp_path))
    assert res.get("repaired_misfiled") is True
    assert res.get("found_in_section") == "## Completed"


def test_done_does_not_strand_the_status_word_in_notes(tmp_path):
    """The column-shift trap: status sits where the Completed schema reads notes."""
    p = _write(tmp_path)
    run_script("todo_write.py", "done", "Stranded prep call", "--repo-root", str(tmp_path))
    row = next(l for l in p.read_text(encoding="utf-8").splitlines()
               if "Stranded prep call" in l)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert "Pending" not in cells


def test_done_still_prefers_an_active_row(tmp_path):
    """The fallback must never pre-empt a normal Active match."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "done", "Live active task",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert res.get("repaired_misfiled") is not True
    active = p.read_text(encoding="utf-8").split("## Completed")[0]
    assert "Live active task" not in active


def test_done_on_a_genuinely_absent_task_still_errors(tmp_path):
    """The fallback must not turn a real miss into a false success."""
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "done", "no such task anywhere")
    assert res["status"] == "error"
    assert "No task found" in res["message"]


def test_done_does_not_touch_a_genuine_completion(tmp_path):
    """A row already correctly completed is not a misfiled row; leave it alone."""
    p = _write(tmp_path)
    before = p.read_text(encoding="utf-8")
    code, res = _run_raw(tmp_path, "done", "Genuinely finished thing")
    assert res["status"] == "error"
    assert p.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# update -- refuses, but names the section instead of lying
# --------------------------------------------------------------------------

def test_update_names_the_section_rather_than_claiming_absence(tmp_path):
    """`No ACTIVE task found` sent the last investigation chasing match-string variants.

    Mutating a stranded row's fields would cement the strandedness, so update refuses --
    but it must refuse accurately, naming where the row actually is.
    """
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "Stranded prep call",
                         "--notes-append", " x")
    assert res["status"] == "error"
    assert res.get("reason") == "misfiled_row"
    assert res.get("found_in_section") == "## Completed"
    assert "audit" in res["message"]


def test_update_still_works_on_an_active_row(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "Live active task",
                     "--notes-append", " | appended", "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert "appended" in p.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# withdraw -- same column-shift trap as done
# --------------------------------------------------------------------------
# Caught on REAL data 2026-08-17, not by the fixtures above: withdraw's
# already-in-Completed path correctly set "Withdrawn <date>" but left the literal
# status word in the notes column, because it only strips a leading "Completed <date>"
# marker. Fixture suites had never exercised a misfiled row through withdraw.
# Per CLAUDE.md: data tools are verified on REAL data, not fixtures.

def test_withdraw_does_not_strand_the_status_word_in_notes(tmp_path):
    p = _write(tmp_path)
    run_script("todo_write.py", "withdraw", "Stranded prep call", "--repo-root", str(tmp_path))
    row = next(l for l in p.read_text(encoding="utf-8").splitlines()
               if "Stranded prep call" in l)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert "Withdrawn" in cells[2]
    assert "Pending" not in cells


def test_withdraw_scrubs_a_stranded_status_on_an_already_withdrawn_row(tmp_path):
    """Repair path for rows withdrawn BEFORE this fix landed, which carry the residue."""
    residue = FIXTURE.replace(
        "| Stranded prep call | High | 2026-08-06 | Pending |",
        "| Stranded prep call | High | Withdrawn 2026-08-06 | Pending |")
    p = _write(tmp_path, residue)
    res = run_script("todo_write.py", "withdraw", "Stranded prep call",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert res.get("changed") is True
    row = next(l for l in p.read_text(encoding="utf-8").splitlines()
               if "Stranded prep call" in l)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert "Pending" not in cells


def test_withdraw_still_reports_already_withdrawn_when_clean(tmp_path):
    """A clean withdrawn row must stay a no-op, or the scrub becomes a rewrite loop."""
    clean = FIXTURE.replace(
        "| Stranded prep call | High | 2026-08-06 | Pending |",
        "| Stranded prep call | High | Withdrawn 2026-08-06 | real note |")
    _write(tmp_path, clean)
    res = run_script("todo_write.py", "withdraw", "Stranded prep call",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert res.get("changed") is False


def test_withdraw_disambiguates_to_the_misfiled_row(tmp_path):
    """Two Completed rows with the same task text: one misfiled, one already resolved.

    Withdraw used to bail with "Multiple matches" here, which is unhelpful because the
    two rows are NOT equally plausible targets -- only one is still un-finished. Among
    Completed matches, a misfiled row is uniquely identifiable, so prefer it.

    Origin 2026-08-17: repairing the live file produced exactly this state (a recovered
    duplicate alongside the stranded original) and the command deadlocked on it.
    """
    dup = FIXTURE.replace(
        "| Stranded prep call | High | 2026-08-06 | Pending |",
        "| Stranded prep call | High | 2026-08-06 | Pending |\n"
        "| Stranded prep call | High | Withdrawn 2026-08-17 | superseded copy |")
    p = _write(tmp_path, dup)
    res = run_script("todo_write.py", "withdraw", "Stranded prep call",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    rows = [l for l in p.read_text(encoding="utf-8").splitlines()
            if "Stranded prep call" in l]
    assert len(rows) == 2                                   # nothing duplicated or lost
    assert not any("| Pending |" in r for r in rows)         # the misfiled one was resolved
    assert any("superseded copy" in r for r in rows)         # the other was left alone


def test_withdraw_still_errors_on_two_equally_valid_matches(tmp_path):
    """Disambiguation must be narrow: two misfiled rows are genuinely ambiguous."""
    dup = FIXTURE.replace(
        "| Stranded prep call | High | 2026-08-06 | Pending |",
        "| Stranded prep call | High | 2026-08-06 | Pending |\n"
        "| Stranded prep call | Low | 2026-08-07 | Pending |")
    _write(tmp_path, dup)
    code, res = _run_raw(tmp_path, "withdraw", "Stranded prep call")
    assert res["status"] == "error"
    assert "Multiple" in res["message"]
