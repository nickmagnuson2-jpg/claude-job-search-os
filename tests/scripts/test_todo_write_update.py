"""Tests for the `update` subcommand of tools/todo_write.py.

Built 2026-08-14. Before it existed, changing one field meant `supersede` plus a
re-`add` with the full task AND notes retyped -- on rows carrying 1-2k characters of
notes. That is a transcription-error generator, and it cost real work twice in one day.

Two contracts are load-bearing here and each has a test that fails loudly if someone
"simplifies" them away:

  1. STATUS IS NOT UPDATABLE. done/withdraw/supersede own status transitions and carry
     side effects (section move, date stamp, the Withdrawn-vs-Completed distinction
     todo_daily_metrics.py counts). A second path to the same state change is how two
     code paths start disagreeing.
  2. DESTRUCTIVE EDITS LEAVE A TRAIL, additive ones do not. --task/--due/--notes
     overwrite a value that is then gone and stamp `[rev <date>: ...]`. The
     append/prepend forms lose nothing, so stamping them would grow every row forever.
"""
import json
import os
import subprocess
import sys
from datetime import date

from conftest import run_script, TOOLS_DIR

FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Ship the widget | High | 2026-08-15 | Pending | original note |
| Apply to Acme | Med | — | Pending | — |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Archived thing | High | 2026-06-08 | Completed 2026-06-08 |
"""


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _run_raw(tmp_path, *args):
    """Run without check=True so error JSON can be inspected."""
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


def _row(path, fragment):
    return next(l for l in path.read_text(encoding="utf-8").splitlines()
                if fragment in l and l.startswith("|"))


# ------------------------------------------------------------------ the additive path

def test_notes_append_keeps_the_existing_note(tmp_path):
    """The whole point: change one thing without retyping what was already there."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget", "--notes-append", "ADDED",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    row = _row(p, "Ship the widget")
    assert "original note" in row
    assert "ADDED" in row
    assert row.index("original note") < row.index("ADDED")


def test_notes_prepend_puts_text_first(tmp_path):
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "widget", "--notes-prepend", "FIRST",
               "--repo-root", str(tmp_path))
    row = _row(p, "Ship the widget")
    assert row.index("FIRST") < row.index("original note")


def test_prepend_onto_empty_notes_leaves_no_stray_dash(tmp_path):
    """An em-dash placeholder means "no notes", not a note reading "-"."""
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "acme", "--notes-prepend", "NEW",
               "--repo-root", str(tmp_path))
    row = _row(p, "Apply to Acme")
    assert "NEW" in row
    assert "—" not in row.split("|")[5]


def test_additive_edits_record_no_revision_marker(tmp_path):
    """Nothing was lost, so nothing is stamped. Otherwise rows grow without bound."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget", "--notes-append", "X",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    assert "[rev " not in _row(p, "Ship the widget")


def test_priority_change_is_not_stamped(tmp_path):
    """Priority is trivially recoverable, so it does not earn a marker."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget", "--priority", "Low",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    row = _row(p, "Ship the widget")
    assert "| Low |" in row
    assert "[rev " not in row


# ------------------------------------------------------------------ the destructive path

def test_task_rename_records_the_old_text(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget", "--task", "Ship the gadget",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 1
    row = _row(p, "Ship the gadget")
    assert f'[rev {date.today():%Y-%m-%d}: task was "Ship the widget"]' in row


def test_due_change_records_the_old_date(tmp_path):
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "widget", "--due", "2026-09-01",
               "--repo-root", str(tmp_path))
    row = _row(p, "Ship the widget")
    assert "| 2026-09-01 |" in row
    assert 'due was "2026-08-15"' in row


def test_notes_replace_records_what_it_overwrote(tmp_path):
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "widget", "--notes", "totally new",
               "--repo-root", str(tmp_path))
    row = _row(p, "Ship the widget")
    assert "totally new" in row
    assert 'notes was "original note"' in row


def test_two_destructive_changes_record_two_markers(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "Renamed", "--due", "2026-09-01",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 2
    row = _row(p, "Renamed")
    assert "task was" in row and "due was" in row


def test_long_prior_value_is_truncated_in_the_marker(tmp_path):
    """A 1.8k-character note must not be duplicated in full into its own row."""
    long_note = "x" * 900
    p = _write(tmp_path, FIXTURE.replace("original note", long_note))
    run_script("todo_write.py", "update", "widget", "--notes", "short",
               "--repo-root", str(tmp_path))
    row = _row(p, "Ship the widget")
    assert "..." in row
    assert len(row) < 500


# ------------------------------------------------------------------ the refusals

def test_status_cannot_be_updated(tmp_path):
    """THE load-bearing refusal. done/withdraw/supersede own status transitions;
    a second path to the same state change is how two code paths start disagreeing."""
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "widget", "--status", "Done")
    assert code != 0
    assert res["status"] == "error"


def test_status_column_survives_an_update_untouched(tmp_path):
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "widget", "--priority", "Low",
               "--repo-root", str(tmp_path))
    assert "| Pending |" in _row(p, "Ship the widget")


def test_notes_replace_conflicts_with_append(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "widget", "--notes", "a", "--notes-append", "b")
    assert code != 0
    assert "ambiguous" in res["message"].lower()


def test_no_field_flags_is_an_error_not_a_silent_noop(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "widget")
    assert code != 0
    assert "nothing to change" in res["message"].lower()


def test_ambiguous_fragment_lists_the_candidates(tmp_path):
    """Same contract as done/withdraw/supersede: never guess between two rows."""
    _write(tmp_path, FIXTURE.replace("Apply to Acme", "Ship the gadget"))
    code, res = _run_raw(tmp_path, "update", "ship the", "--priority", "Low")
    assert code != 0
    assert "Multiple matches" in res["message"]
    assert "Ship the widget" in res["message"] and "Ship the gadget" in res["message"]


def test_no_match_is_an_error(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "nonexistent", "--priority", "Low")
    assert code != 0


def test_completed_rows_are_not_reachable(tmp_path):
    """Active only. Editing an archived row is rarer and riskier, and `withdraw`
    already covers the one archived-row correction that actually comes up."""
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "Archived thing", "--priority", "Low")
    assert code != 0
    assert "ACTIVE" in res["message"]


def test_unknown_field_flag_is_rejected(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "widget", "--owner", "nick")
    assert code != 0


def test_flag_without_a_value_is_rejected(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "widget", "--priority")
    assert code != 0


def test_fragment_must_come_before_the_flags(tmp_path):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, "update", "--task", "x")
    assert code != 0
    assert "fragment" in res["message"].lower()


# ------------------------------------------------------------------ table integrity

def test_a_pipe_in_a_value_cannot_split_the_row(tmp_path):
    """Root cause of the 2026-07 column drift; _safe_cell must still apply here."""
    p = _write(tmp_path)
    run_script("todo_write.py", "update", "widget", "--notes", "a | b | c",
               "--repo-root", str(tmp_path))
    row = _row(p, "Ship the widget")
    assert row.count("|") == 6          # 5 columns => 6 delimiters


def test_other_rows_are_untouched(tmp_path):
    p = _write(tmp_path)
    before = _row(p, "Apply to Acme")
    run_script("todo_write.py", "update", "widget", "--priority", "Low",
               "--repo-root", str(tmp_path))
    assert _row(p, "Apply to Acme") == before


# ------------------------------------------------------------------ the substring carve-out
# Added 2026-08-14 immediately after the verb's first live use. Prefixing a tracker
# onto five task strings stamped five markers quoting text still present in the new
# value. A trail that records non-losses trains you to skim past the real entries.

def test_prefixing_the_task_records_nothing(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "[TRACKER] Ship the widget",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    row = _row(p, "Ship the widget")
    assert "[TRACKER]" in row
    assert "[rev " not in row


def test_suffixing_the_task_records_nothing(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "Ship the widget (revised)",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    assert "[rev " not in _row(p, "Ship the widget")


def test_notes_replace_that_contains_the_old_note_records_nothing(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--notes", "prefix original note suffix",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    assert "[rev " not in _row(p, "Ship the widget")


def test_a_genuine_replacement_still_records(tmp_path):
    """The carve-out must not swallow the case the trail exists for."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "Something else entirely",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 1
    assert 'task was "Ship the widget"' in _row(p, "Something else entirely")


def test_a_partial_overlap_still_records(tmp_path):
    """Sharing some words is not surviving verbatim. Only containment is safe."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "Ship the thing",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 1


def test_truncating_a_value_still_records(tmp_path):
    """Deleting the tail is a real loss even though the survivor is a substring of
    the OLD value. The containment test runs one way only, and this pins it."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget", "--task", "Ship",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 1
    assert 'task was "Ship the widget"' in _row(p, "| Ship |")


# ------------------------------------------------------------------ insertion-only detection
# The carve-out's first form used substring containment and MISSED the case that
# motivated it: the tracker was inserted mid-string, so the old value was neither a
# prefix nor a suffix of the new one. Subsequence containment is the correct
# formalization of "text was inserted and nothing removed".

def test_mid_string_insertion_records_nothing(tmp_path):
    """THE motivating case. Substring containment fails this; subsequence passes it."""
    p = _write(tmp_path)
    res = run_script("todo_write.py", "update", "widget",
                     "--task", "Ship [TRACKER: pending] the widget",
                     "--repo-root", str(tmp_path))
    assert res["revisions_recorded"] == 0
    assert "[rev " not in _row(p, "TRACKER")


def test_is_insertion_only_unit():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tw", TOOLS_DIR / "todo_write.py")
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)
    f = tw.is_insertion_only

    # nothing removed -> no marker
    assert f("abc", "abc")
    assert f("abc", "xabc")            # prefix
    assert f("abc", "abcx")            # suffix
    assert f("abc", "axbxc")           # mid-string, the missed case
    assert f("DAILY REP Sat 8/15: build", "DAILY REP Sat 8/15 [REP: pending]: build")
    assert f("", "anything")

    # something removed -> marker
    assert not f("abc", "ab")          # truncation
    assert not f("abc", "xyz")         # replacement
    assert not f("Ship the widget", "Ship the thing")   # partial overlap
    assert not f("abc", "cba")         # reorder is not insertion
