"""A backdated `log` (--date older than the newest entry) must not rewind the contact.

Friction ledger 2026-11-06: logging an older interaction overwrote Last Interaction with the
older date and prepended the entry above newer ones. Cross-model review the same day (Codex
F1-F2, Grok F1-F5) widened it: a backdated log also must not replace the live follow-up to-do
or mark a newer outreach row Replied, and placement must hold in a section that is already
out of order. Last Interaction is the newest interaction date.
"""
import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import networking_write as nw  # noqa: E402

DOC = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | {last} | — |

## Interaction Log

### Jane Doe — Acme Corp

{entries}
### Someone Else

#### 2026-01-01 | email | Unrelated

**Follow-up:** —
"""


def _entry(d, summary):
    return f"#### {d} | email | {summary}\n\n**Follow-up:** —\n"


def _text(last, *entries):
    return DOC.format(last=last, entries="\n".join(_entry(d, s) for d, s in entries))


def _doc(last, *entries):
    return _text(last, *entries).split("\n")


def _log(lines, d, summary="new"):
    entry = [f"#### {d} | email | {summary}", "", "**Follow-up:** —", ""]
    assert nw.apply_log(lines, "Jane Doe", d, entry, "none", False) is None
    return lines


def _last_interaction(lines):
    row = next(l for l in lines if l.startswith("| Jane Doe "))
    return nw.parse_cols(row)[5]


def _jane_heads(lines):
    start, end = nw.find_contact_log_section(lines, "Jane Doe")
    return [l for l in lines[start:end] if l.startswith("#### ")]


def _jane_dates(lines):
    return [h.split(" ")[1] for h in _jane_heads(lines)]


# --- Last Interaction ------------------------------------------------------------------------

def test_backdated_log_keeps_newer_last_interaction():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a")), "2026-10-20")
    assert _last_interaction(lines) == "2026-11-05"


def test_newer_log_advances_last_interaction():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a")), "2026-11-06")
    assert _last_interaction(lines) == "2026-11-06"


def test_stale_cell_advances_to_the_logged_date():
    lines = _log(_doc("2026-01-01", ("2026-01-01", "a")), "2026-03-01")
    assert _last_interaction(lines) == "2026-03-01"


def test_placeholder_cell_takes_the_newest_entry_not_the_backdated_log():
    lines = _log(_doc("—", ("2026-11-05", "a")), "2026-10-20")
    assert _last_interaction(lines) == "2026-11-05"


def test_placeholder_cell_with_no_entries_takes_the_logged_date():
    lines = _log(_doc("—"), "2026-10-20")
    assert _last_interaction(lines) == "2026-10-20"


def test_stale_cell_is_corrected_to_the_newest_entry():
    """The cell said 01-01 though an 11-05 entry exists: the newest interaction wins."""
    lines = _log(_doc("2026-01-01", ("2026-11-05", "a")), "2026-10-20")
    assert _last_interaction(lines) == "2026-11-05"


def test_a_cell_newer_than_every_entry_is_kept():
    lines = _log(_doc("2026-12-01", ("2026-11-05", "a")), "2026-11-06")
    assert _last_interaction(lines) == "2026-12-01"


def test_another_contacts_entries_do_not_count():
    """Someone Else's section holds only 01-01; Jane's newest is what counts."""
    lines = _log(_doc("—", ("2026-02-01", "a")), "2026-01-15")
    assert _last_interaction(lines) == "2026-02-01"


def test_short_contact_row_is_padded_to_seven_cells():
    lines = _doc("—", ("2026-11-05", "a"))
    i = next(i for i, l in enumerate(lines) if l.startswith("| Jane Doe "))
    lines[i] = "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 |"
    lines = _log(lines, "2026-11-06")
    assert nw.parse_cols(lines[i]) == ["Jane Doe", "Acme Corp", "PM", "peer", "2026-01-01",
                                       "2026-11-06", "—"]


# --- Placement -------------------------------------------------------------------------------

def test_backdated_entry_lands_between_newer_and_older():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a"), ("2026-10-01", "b")), "2026-10-20")
    assert _jane_dates(lines) == ["2026-11-05", "2026-10-20", "2026-10-01"]


def test_entry_older_than_all_lands_at_section_end_not_in_next_section():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a"), ("2026-10-01", "b")), "2026-09-01")
    assert _jane_dates(lines) == ["2026-11-05", "2026-10-01", "2026-09-01"]
    i_new = next(i for i, l in enumerate(lines) if l.startswith("#### 2026-09-01"))
    start, _ = nw.find_contact_log_section(lines, "Someone Else")
    assert i_new < start
    assert lines[start + 2].startswith("#### 2026-01-01 | email | Unrelated")


def test_out_of_order_section_still_puts_the_entry_below_every_newer_one():
    """Grok F1: [10-20, 11-05, 10-01] is the shape the old prepend produced."""
    lines = _log(_doc("2026-11-05", ("2026-10-20", "x"), ("2026-11-05", "a"),
                      ("2026-10-01", "b")), "2026-10-25")
    assert _jane_dates(lines) == ["2026-10-20", "2026-11-05", "2026-10-25", "2026-10-01"]
    assert _last_interaction(lines) == "2026-11-05"


def test_same_day_entry_goes_on_top_of_that_day():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a")), "2026-11-05", summary="second")
    assert _jane_heads(lines)[0].endswith("| second")
    assert _last_interaction(lines) == "2026-11-05"


def test_newest_entry_still_goes_first():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a")), "2026-11-06")
    assert _jane_dates(lines) == ["2026-11-06", "2026-11-05"]


def test_empty_section_takes_entry_after_heading():
    lines = _log(_doc("—"), "2026-11-06")
    start, _ = nw.find_contact_log_section(lines, "Jane Doe")
    assert lines[start + 1] == "" and lines[start + 2].startswith("#### 2026-11-06")


def test_middle_insert_keeps_a_blank_line_on_both_sides():
    lines = _log(_doc("2026-11-05", ("2026-11-05", "a"), ("2026-10-01", "b")), "2026-10-20")
    i = next(i for i, l in enumerate(lines) if l.startswith("#### 2026-10-20"))
    assert lines[i - 1] == ""
    j = next(j for j, l in enumerate(lines) if l.startswith("#### 2026-10-01"))
    assert lines[j - 1] == ""


def test_newest_entry_date_reads_only_that_contact():
    lines = _doc("—", ("2026-02-01", "a"), ("2026-03-01", "b"))
    assert nw.newest_entry_date(lines, "Jane Doe") == "2026-03-01"
    assert nw.newest_entry_date(lines, "Someone Else") == "2026-01-01"
    assert nw.newest_entry_date(lines, "Nobody") is None


# --- Side effects on other files (CLI) -------------------------------------------------------

TODOS = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Follow up: Jane Doe — Current next step | Med | 2026-11-12 | Pending | From networking log on 2026-11-05 |

## Completed

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
"""

OUTREACH = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-05 | follow-up | email | Jane Doe | Acme Corp | Re: intro | Sent |
"""


def _cli(tmp_path, log_date, *extra):
    write_fixture(tmp_path, "data/networking.md", _text("2026-11-05", ("2026-11-05", "a")))
    write_fixture(tmp_path, "data/job-todos.md", TODOS)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH)
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
         "log", "Jane Doe", "--date", log_date, "--type", "email", "--summary", "s", *extra],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout), r.returncode


def test_backdated_followup_leaves_the_live_todo_alone(tmp_path):
    out, code = _cli(tmp_path, "2026-10-20", "--direction", "none",
                     "--followup", "Historical next step")
    assert code == 0
    assert (tmp_path / "data/job-todos.md").read_text(encoding="utf-8") == TODOS
    assert out["todo_added"] is False
    assert out["backdated"] is True and out["newest_entry"] == "2026-11-05"
    assert out["skipped"] == ["follow-up to-do"]


def test_backdated_inbound_does_not_mark_the_newer_send_replied(tmp_path):
    out, code = _cli(tmp_path, "2026-10-20", "--direction", "in", "--no-todo")
    assert code == 0
    assert (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8") == OUTREACH
    assert out["outreach_log_updated"] is False
    assert out["skipped"] == ["outreach status check"]


def test_backdated_with_both_effects_reports_both_skipped(tmp_path):
    out, _ = _cli(tmp_path, "2026-10-20", "--direction", "in", "--followup", "x")
    assert out["skipped"] == ["follow-up to-do", "outreach status check"]


def test_backdated_with_no_effects_reports_nothing_skipped(tmp_path):
    out, _ = _cli(tmp_path, "2026-10-20", "--direction", "none")
    assert out["backdated"] is True and out["skipped"] == []


def test_same_day_inbound_still_flips_outreach_and_is_not_backdated(tmp_path):
    out, code = _cli(tmp_path, "2026-11-05", "--direction", "in", "--no-todo")
    assert code == 0 and out["outreach_log_updated"] is True
    assert "backdated" not in out
    assert "| Replied |" in (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")


def test_newer_followup_still_supersedes_the_live_todo(tmp_path):
    out, code = _cli(tmp_path, "2026-11-06", "--direction", "none",
                     "--followup", "Newer next step")
    todos = (tmp_path / "data/job-todos.md").read_text(encoding="utf-8")
    assert code == 0 and out["todo_added"] is True
    assert "Follow up: Jane Doe — Newer next step" in todos


# --- Impossible dates (cross-model review round 2) -------------------------------------------

def test_is_date_rejects_shape_only_dates():
    assert nw.is_date("2026-11-06") is True
    assert nw.is_date("2026-13-40") is False
    assert nw.is_date("2026-02-30") is False
    assert nw.is_date("—") is False
    assert nw.is_date("2026-11-06x") is False
    # date.fromisoformat accepts the compact form; the YYYY-MM-DD shape check must not.
    assert nw.is_date("20261106") is False


def test_impossible_cell_date_is_replaced_not_kept():
    lines = _log(_doc("2026-13-40", ("2026-11-05", "a")), "2026-11-06")
    assert _last_interaction(lines) == "2026-11-06"


def test_impossible_entry_date_is_ignored_for_placement_and_last_interaction():
    lines = _log(_doc("—", ("2026-13-40", "bad"), ("2026-11-05", "a")), "2026-11-06")
    assert _last_interaction(lines) == "2026-11-06"
    assert nw.newest_entry_date(lines, "Jane Doe") == "2026-11-06"


def test_impossible_log_date_is_refused_and_writes_nothing(tmp_path):
    out, code = _cli(tmp_path, "2026-13-40", "--direction", "none", "--no-todo")
    assert code != 0 and out["code"] == "invalid_date"
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == _text(
        "2026-11-05", ("2026-11-05", "a"))


def test_no_todo_backdated_log_does_not_report_a_skipped_todo(tmp_path):
    out, _ = _cli(tmp_path, "2026-10-20", "--direction", "none", "--followup", "x", "--no-todo")
    assert out["backdated"] is True and out["skipped"] == []


def test_backdated_log_with_no_todos_file_reports_no_skipped_todo(tmp_path):
    """r3 F1: with no to-do file the step would not have run, so it is not a skip."""
    write_fixture(tmp_path, "data/networking.md", _text("2026-11-05", ("2026-11-05", "a")))
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
         "log", "Jane Doe", "--date", "2026-10-20", "--type", "email", "--summary", "s",
         "--direction", "none", "--followup", "x"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    out = json.loads(r.stdout)
    assert out["backdated"] is True and out["skipped"] == []
