"""Tests for tools/check_draft_weekday.py — the weekday/date mismatch gate.

Run date fixed at 2027-03-10 (a Wednesday) so year inference is deterministic.
Reference weekdays: 2027-03-11 Thu, 03-15 Mon, 03-16 Tue, 03-17 Wed.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import check_draft_weekday as h  # noqa: E402

TODAY = date(2027, 3, 10)


# --- a list with two wrong weekdays among correct ones ----------------------------
def test_mismatched_weekdays_block():
    body = (
        "Times that work:\n"
        "- Thursday, Mar 11, 2pm PT\n"
        "- Monday, Mar 16, 2pm PT\n"
        "- Tuesday, Mar 17, 2pm PT\n"
    )
    found = h.find_mismatches(body, TODAY)
    assert [p for p, _ in found] == ["Monday, Mar 16", "Tuesday, Mar 17"]
    assert found[0][1] == "Tuesday 2027-03-16"
    assert found[1][1] == "Wednesday 2027-03-17"


def test_matching_weekdays_clean():
    body = "- Thursday, Mar 11\n- Tuesday, Mar 16\n- Wednesday, Mar 17\n"
    assert h.find_mismatches(body, TODAY) == []


# --- every supported shape catches a wrong weekday ----------------------------
@pytest.mark.parametrize("text", [
    "Monday, Mar 16",
    "Mon Mar 16",
    "Mon. Mar. 16",
    "monday march 16th",
    "Monday, March 16, 2027",
    "Mon 3/16",
    "Monday, 3/16/2027",
    "Mon 3/16/27",
    "Monday the Mar 16",
    "Mar 16 (Mon)",
    "3/16 (Monday)",
    "Thurs Mar 16",
])
def test_blocks_each_shape(text):
    assert len(h.find_mismatches(text, TODAY)) == 1


@pytest.mark.parametrize("text", [
    "Tuesday, Mar 16",
    "Tue Mar 16",
    "Tues 3/16",
    "Tuesday, March 16, 2027",
    "Mar 16 (Tue)",
    "3/16 (Tuesday)",
])
def test_allows_each_shape_when_correct(text):
    assert h.find_mismatches(text, TODAY) == []


# --- things that are not weekday/date pairs ------------------------------------
@pytest.mark.parametrize("text", [
    "Talk Thursday at 1pm.",           # weekday, no date
    "Sent on Mar 16.",                 # date, no weekday
    "Monday works, or Mar 18.",        # not adjacent
    "Mon 13/40",                       # impossible numeric date
    "Monday, Feb 30",                  # impossible named date
    "Mon 3/16/2027x",                  # trailing junk is not a date end
    "Wed/Mon 3/16-17",                 # a weekday list over a date range
    "",
])
def test_allows_non_pairs(text):
    assert h.find_mismatches(text, TODAY) == []


# --- year inference -------------------------------------------------------------
def test_explicit_year_is_used_as_written():
    # 2025-06-02 was a Monday; with the year written, Monday is correct.
    assert h.find_mismatches("Monday, Jun 2, 2025", TODAY) == []
    assert len(h.find_mismatches("Tuesday, Jun 2, 2025", TODAY)) == 1


def test_inferred_year_rolls_forward_near_year_end():
    late_dec = date(2026, 12, 28)
    # 2027-01-05 is a Tuesday; 2026-01-05 was a Monday. Nearest is 2027.
    assert h.find_mismatches("Tuesday, Jan 5", late_dec) == []
    assert len(h.find_mismatches("Monday, Jan 5", late_dec)) == 1


def test_two_digit_year():
    assert h.find_mismatches("Mon 3/15/27", TODAY) == []


def test_two_digit_year_00_is_2000():
    # 2000-03-01 was a Wednesday. "00" must read as 2000, not year 0
    # (round-3 cross-model finding: year 0 is invalid and would skip the pair).
    assert len(h.find_mismatches("Monday 3/1/00", TODAY)) == 1
    assert h.find_mismatches("Wednesday 3/1/00", TODAY) == []


# --- subject line is checked too, separately from the body -----------------------
def test_checked_fields_splits_subject_and_body():
    content = "TO: x@example.com\nSUBJECT: Call Monday, Mar 16?\nBODY:\nHi,\nSee you.\n"
    subject, body = h.checked_fields(content)
    assert "Monday, Mar 16" in subject
    assert "See you." in body
    assert len(h.find_mismatches(subject, TODAY)) == 1


# --- no pair is invented across a boundary (cross-model finding F2) --------------
@pytest.mark.parametrize("text", [
    "I can meet Monday. March 16 was discussed.",   # sentence boundary
    "Free Monday\nMar 16 is the deadline.",          # line boundary
    "Monday\n(Mar 16)",                               # line boundary, parenthesised
    "Mar 16\n(Mon)",                                  # date-then-weekday across a line
])
def test_no_pair_across_sentence_or_line(text):
    assert h.find_mismatches(text, TODAY) == []


def test_abbreviation_dot_still_pairs():
    assert len(h.find_mismatches("Mon. Mar 16", TODAY)) == 1


def test_main_does_not_pair_subject_with_body(monkeypatch, tmp_path):
    draft = "TO:\nSUBJECT: Availability Monday\nBODY:\nMar 16 was discussed.\nNick\n"
    assert _run_main(monkeypatch, tmp_path, draft) == 0


# --- explicit years outside 1900-2099 are not read as years (finding F3) ----------
@pytest.mark.parametrize("text", [
    "Monday 3/1/8000", "Monday, Mar 1, 8000",       # wrong as written
    "Wednesday 3/1/8000", "Wednesday, Mar 1, 8000", # correct as written (8000-03-01 is a Wed)
])
def test_out_of_range_year_rejects_the_whole_date(text):
    # The year must not be dropped and the date re-read as this year
    # (round-2 cross-model finding): an unsupported year means no pair at all.
    assert h.find_mismatches(text, TODAY) == []


def test_time_after_comma_still_pairs():
    assert len(h.find_mismatches("Monday, Mar 16, 10am PT", TODAY)) == 1
    # Known limit: a bare four-digit 24-hour time ("Mar 16, 1030") reads as a
    # year and the pair is skipped, never blocked. Fail-safe direction.
    assert h.find_mismatches("Monday, Mar 16, 1030 PT", TODAY) == []


# --- the real entry point ---------------------------------------------------------
def _run_main(monkeypatch, tmp_path, draft, command="python3 tools/open_draft.py", env=None):
    f = tmp_path / ".pending-draft.txt"
    if draft is not None:
        f.write_text(draft, encoding="utf-8")
    monkeypatch.setattr(h, "DRAFT_FILE", f)
    monkeypatch.setattr(h, "read_hook_command", lambda: command)
    monkeypatch.delenv("DRAFT_OVERRIDE", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)

    class _Fixed(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(h, "date", _Fixed)
    with pytest.raises(SystemExit) as e:
        h.main()
    return e.value.code


BAD = "TO:\nSUBJECT: Re: hello\nBODY:\nHi,\n- Monday, Mar 16, 2pm PT\nBest,\nNick\n"
GOOD = "TO:\nSUBJECT: Re: hello\nBODY:\nHi,\n- Tuesday, Mar 16, 2pm PT\nBest,\nNick\n"


def test_main_blocks_bad_draft(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, BAD) == 2
    assert "Monday, Mar 16" in capsys.readouterr().err


def test_main_allows_good_draft(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, GOOD) == 0


def test_main_ignores_other_commands(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, BAD, command="ls tools/") == 0


def test_main_fails_open_without_draft(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, None) == 0


def test_main_override(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, BAD, env={"DRAFT_OVERRIDE": "1"}) == 0


# --- the script as Claude Code runs it (subprocess, JSON payload on stdin) --------
def test_script_blocks_via_subprocess(tmp_path):
    """Copy the hook next to a bad draft and run it as a real PreToolUse hook.

    The explicit year makes the verdict independent of the real run date.
    """
    import json
    import shutil
    import subprocess

    tools = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
    shutil.copy(os.path.join(tools, "check_draft_weekday.py"), tmp_path)
    (tmp_path / ".pending-draft.txt").write_text(
        "TO:\nSUBJECT: Re: hi\nBODY:\nHi,\nMonday, Mar 16, 2027 works.\nBest,\nNick\n",
        encoding="utf-8",
    )
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "python3 tools/open_draft.py"}})
    env = {**os.environ, "PYTHONPATH": os.path.abspath(tools)}
    env.pop("DRAFT_OVERRIDE", None)
    r = subprocess.run([sys.executable, str(tmp_path / "check_draft_weekday.py")],
                       input=payload, capture_output=True, text=True, env=env)
    assert r.returncode == 2, r.stderr
    assert "Monday, Mar 16, 2027" in r.stderr
