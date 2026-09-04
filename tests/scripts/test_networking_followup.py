"""Tests for tools/networking_followup.py"""
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def make_fixture(contacts_rows: str, interaction_log: str = "") -> str:
    """Build a full networking.md fixture with contacts table + interaction log."""
    base = (
        "## Contacts\n\n"
        "| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    )
    base += contacts_rows
    if interaction_log:
        base += "\n## Interaction Log\n\n" + interaction_log
    return base


def test_empty_networking(tmp_path):
    """Script handles missing networking.md gracefully."""
    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["followup_due"] == []
    assert result["followup_overdue"] == []
    assert result["summary"]["total_contacts"] == 0


def test_overdue_contact_from_interaction_log(tmp_path):
    """Contact with 'next week' follow-up from 14 days ago is overdue."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jordan Lee | Acme AI | PM | peer | 2026-02-10 | 2026-02-12 | jordan@acme-ai.com |\n",
        "### Jordan Lee — Acme AI\n\n"
        "#### 2026-02-12 | email | Coffee chat follow-up\n\n"
        "**Follow-up:** Follow up next week\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Follow-up was due 2026-02-19 (2026-02-12 + 7 days), today is 2026-02-26 → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Jordan Lee" in overdue_names


def test_followup_due_exactly_today_counts_as_due(tmp_path):
    """A follow-up whose inferred date is exactly today lands in followup_due and is
    counted in summary.due_today — not dumped into overdue. Regression for fable-audit
    Theme 2: under the default threshold, a `<=` boundary sent due-today to overdue,
    so summary.due_today was structurally always 0."""
    # Entry 2026-02-19 + "next week" (7d) → due 2026-02-26 == target date.
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jordan Lee | Acme AI | PM | peer | 2026-02-10 | 2026-02-19 | jordan@acme-ai.com |\n",
        "### Jordan Lee — Acme AI\n\n"
        "#### 2026-02-19 | email | Coffee chat follow-up\n\n"
        "**Follow-up:** Follow up next week\n\n",
    ))
    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    due_names = [e["name"] for e in result["followup_due"]]
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Jordan Lee" in due_names
    assert "Jordan Lee" not in overdue_names
    assert result["summary"]["due_today"] == 1


def test_explicit_date_in_interaction_log(tmp_path):
    """Explicit date in follow-up line is parsed correctly."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Priya Anand | Northwind | CoS | peer | 2026-02-20 | 2026-02-24 | — |\n",
        "### Priya Anand — Northwind\n\n"
        "#### 2026-02-24 | email | Sent intro request\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-01\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due 2026-03-01 — 3 days from now — should appear in followup_due
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Priya Anand" in due_names


def test_no_interaction_log_skipped(tmp_path):
    """Contacts with no Interaction Log section are not surfaced."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jane Doe | Acme | VP | peer | 2026-02-15 | 2026-02-20 | jane@acme.com |\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Jane Doe" not in all_names


def test_dash_followup_skipped(tmp_path):
    """Contacts with dash follow-up in interaction log are not surfaced."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jane Doe | Acme | VP | peer | 2026-02-15 | 2026-02-20 | jane@acme.com |\n",
        "### Jane Doe — Acme\n\n"
        "#### 2026-02-20 | email | Sent intro request\n\n"
        "**Follow-up:** —\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Jane Doe" not in all_names


def test_3_5_business_days_inference(tmp_path):
    """'3-5 business days' maps to entry_date + 5d."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Bob Smith | TechCo | Eng | peer | 2026-02-15 | 2026-02-20 | — |\n",
        "### Bob Smith — TechCo\n\n"
        "#### 2026-02-20 | email | Sent cold email\n\n"
        "**Follow-up:** Follow up in 3-5 business days\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due: 2026-02-20 + 5d = 2026-02-25, which is 1 day ago → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Bob Smith" in overdue_names


def test_most_recent_followup_used(tmp_path):
    """When multiple interaction entries exist, the most recent (topmost) follow-up is used."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Sam Jones | StartupX | PM | peer | 2026-02-10 | 2026-02-24 | — |\n",
        "### Sam Jones — StartupX\n\n"
        "#### 2026-02-24 | email | Follow-up #2 sent\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-05\n\n"
        "#### 2026-02-15 | email | Initial cold outreach\n\n"
        "**Follow-up:** Follow up next week\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Should use the most recent follow-up (~2026-03-05), not the older "next week"
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Sam Jones" in due_names
    entry = [e for e in result["followup_due"] if e["name"] == "Sam Jones"][0]
    assert entry["followup_date"] == "2026-03-05"


def test_none_required_followup_skipped(tmp_path):
    """Follow-up starting with 'None' is skipped."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Lisa Park | YogaCo | Instructor | other | 2026-02-10 | 2026-02-20 | — |\n",
        "### Lisa Park — YogaCo\n\n"
        "#### 2026-02-20 | call | Decided to step back from yoga biz\n\n"
        "**Follow-up:** None required. Lisa intro pending.\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Lisa Park" not in all_names


def test_recruiter_followup_bug_regression(tmp_path):
    """Regression test: Robin emailed 2026-03-09 with follow-up ~2026-03-16 should NOT be overdue on 2026-03-10."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Robin Diaz | Northwind | — | cold-outreach | 2026-03-09 | 2026-03-09 | — |\n",
        "### Robin Diaz — Northwind\n\n"
        "#### 2026-03-09 | email | Re: Manager, Business Operations application\n\n"
        "> Hi Robin, ...\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-16 if no reply\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-03-10",
                        "--repo-root", str(tmp_path))

    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Robin Diaz" not in overdue_names

    # Should appear in followup_due (6 days until due)
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Robin Diaz" in due_names
    entry = [e for e in result["followup_due"] if e["name"] == "Robin Diaz"][0]
    assert entry["followup_date"] == "2026-03-16"
    assert entry["days_until"] == 6


# ---------------------------------------------------------------------------
# Closed-company suppression (2026-08-14)
#
# A closed pipeline row did not silence its networking children, so /standup kept
# nudging dead threads. First reported 2026-05-13 (4 ghost rows in one morning) and
# parked for three months; on 2026-08-14 it surfaced a recruiting coordinator as "due
# today" with prep instructions for a loop whose company had closed four days earlier.
#
# The discriminator is deliberately NOT judgment: suppress only when the contact's last
# interaction PREDATES the close. A touch after the close is intentional relationship
# work (the "close the loop, buy you a beer" text) and must survive.
# ---------------------------------------------------------------------------

PIPELINE_CLOSED = """\
# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LiveCo | Ops Lead | Onsite scheduled | 2026-08-12 | Prep | — | — | — |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ClosedCo | GTM Ops | Withdrawn | 2026-05-08 | — | — | — | — |
| ClosedCo | Strategist | Rejected | 2026-08-10 | — | — | — | — |
"""


def _closed_setup(tmp_path, contacts, log):
    write_fixture(tmp_path, "data/networking.md", make_fixture(contacts, log))
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_CLOSED)
    return run_script("networking_followup.py",
                      "--target-date", "2026-08-14",
                      "--repo-root", str(tmp_path))


def test_followup_predating_the_close_is_suppressed(tmp_path):
    """The incident shape: last touch 7/31, company closed 8/10, nudge fires 8/14."""
    result = _closed_setup(
        tmp_path,
        "| Casey Doe | ClosedCo | Recruiting coordinator | recruiter | 2026-07-01 | 2026-07-31 | c@x.com |\n",
        "### Casey Doe — ClosedCo\n\n"
        "#### 2026-07-31 | email | Onsite logistics\n\n"
        "**Follow-up:** Accept the calendar invite today. Research the panel before 8/5.\n\n",
    )
    live = [e["name"] for e in result["followup_due"] + result["followup_overdue"]]
    assert "Casey Doe" not in live
    assert "Casey Doe" in [e["name"] for e in result["suppressed_closed"]]


def test_followup_after_the_close_survives(tmp_path):
    """Deliberate post-close relationship work. Suppressing this is the costly error."""
    result = _closed_setup(
        tmp_path,
        "| Jordan Sample | ClosedCo | Building agents | warm | 2026-06-10 | 2026-08-13 | j@x.com |\n",
        "### Jordan Sample — ClosedCo\n\n"
        "#### 2026-08-13 | text | Closed the loop after the no\n\n"
        "**Follow-up:** Nudge 2026-08-18 about the beer she was offered\n\n",
    )
    live = [e["name"] for e in result["followup_due"] + result["followup_overdue"]]
    assert "Jordan Sample" in live
    assert "Jordan Sample" not in [e["name"] for e in result["suppressed_closed"]]


def test_close_date_uses_the_most_recent_terminal_row(tmp_path):
    """ClosedCo has two terminal rows (5/08 and 8/10). Against the stale 5/08 row a
    7/31 touch looks post-close and would wrongly survive."""
    result = _closed_setup(
        tmp_path,
        "| Casey Doe | ClosedCo | Recruiting coordinator | recruiter | 2026-07-01 | 2026-07-31 | c@x.com |\n",
        "### Casey Doe — ClosedCo\n\n"
        "#### 2026-07-31 | email | Onsite logistics\n\n"
        "**Follow-up:** Accept the calendar invite today\n\n",
    )
    entry = [e for e in result["suppressed_closed"] if e["name"] == "Casey Doe"][0]
    assert entry["close_date"] == "2026-08-10"


def test_same_day_touch_is_not_suppressed(tmp_path):
    """Passed on the day of the close, then a same-day note. Not 'before' the close."""
    pipeline = PIPELINE_CLOSED.replace(
        "| ClosedCo | Strategist | Rejected | 2026-08-10 |",
        "| ClosedCo | Strategist | Rejected | 2026-08-06 |")
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Alex Example | ClosedCo | Founder | founder | 2026-07-20 | 2026-08-06 | a@x.com |\n",
        "### Alex Example — ClosedCo\n\n"
        "#### 2026-08-06 | email | Post-pass note, no ask\n\n"
        "**Follow-up:** Reconnect on a trigger, not a calendar. Fallback window ~Nov.\n\n",
    ))
    write_fixture(tmp_path, "data/job-pipeline.md", pipeline)
    result = run_script("networking_followup.py", "--target-date", "2026-08-14",
                        "--repo-root", str(tmp_path))
    live = [e["name"] for e in result["followup_due"] + result["followup_overdue"]]
    assert "Alex Example" in live


def test_live_row_beats_a_terminal_row_for_the_same_company(tmp_path):
    """Same rule sync uses: a company with any non-terminal row is not closed."""
    pipeline = PIPELINE_CLOSED.replace(
        "| LiveCo | Ops Lead | Onsite scheduled | 2026-08-12 | Prep | — | — | — |",
        "| ClosedCo | Ops Lead | Onsite scheduled | 2026-08-12 | Prep | — | — | — |")
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Casey Doe | ClosedCo | Recruiting coordinator | recruiter | 2026-07-01 | 2026-07-31 | c@x.com |\n",
        "### Casey Doe — ClosedCo\n\n"
        "#### 2026-07-31 | email | Onsite logistics\n\n"
        "**Follow-up:** Accept the calendar invite today\n\n",
    ))
    write_fixture(tmp_path, "data/job-pipeline.md", pipeline)
    result = run_script("networking_followup.py", "--target-date", "2026-08-14",
                        "--repo-root", str(tmp_path))
    live = [e["name"] for e in result["followup_due"] + result["followup_overdue"]]
    assert "Casey Doe" in live


def test_missing_pipeline_degrades_gracefully(tmp_path):
    """No pipeline file: suppress nothing, never crash. Per the graceful-degradation rule."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Casey Doe | ClosedCo | Recruiting coordinator | recruiter | 2026-07-01 | 2026-07-31 | c@x.com |\n",
        "### Casey Doe — ClosedCo\n\n"
        "#### 2026-07-31 | email | Onsite logistics\n\n"
        "**Follow-up:** Accept the calendar invite today\n\n",
    ))
    result = run_script("networking_followup.py", "--target-date", "2026-08-14",
                        "--repo-root", str(tmp_path))
    assert result["suppressed_closed"] == []
    live = [e["name"] for e in result["followup_due"] + result["followup_overdue"]]
    assert "Casey Doe" in live


# =============================================================================
# 2026-09-03: a date CITED as history is not a date SET as a deadline.
#
# Origin: a contact was reported months overdue in a morning brief. The note's real
# trigger was written as a month name, which the tilde branch cannot parse because
# it only accepts ~YYYY-MM-DD. The parser fell through to "first ISO date anywhere
# in the note" and matched an earlier date that the sentence was CITING as history,
# then used it as the deadline -- one that fell before the most recent conversation.
#
# The fixtures below are synthetic and were written from scratch to exercise that
# parse shape. They are deliberately NOT the real note with the name removed:
# stripping a name from a verbatim private sentence does not genericize it, and the
# distinctive phrasing plus the real dates still identify the record.
#
# The mechanical invariant: a follow-up cannot be due before the last time you
# spoke. If it were, the conversation already happened.
# =============================================================================

import importlib.util as _ilu
from datetime import date as _date, timedelta as _timedelta

_SPEC = _ilu.spec_from_file_location(
    "networking_followup_under_test",
    Path(__file__).resolve().parents[2] / "tools" / "networking_followup.py")
_nf = _ilu.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_nf)

# Synthetic fixture. Shape only: an ISO date cited as HISTORY, and the real trigger
# written as a month name. Written from scratch, not derived from any real note.
CITATION_LAST = _date(2026, 3, 2)      # last interaction
CITATION_CITED = _date(2026, 1, 15)    # the date the note merely mentions
CITATION_DUE = _date(2026, 9, 15)      # what "mid-Sep 2026" must resolve to
CITATION_NOTE = (
    "They own the next step. If nothing changes by mid-Sep 2026, reach out again. "
    "For background, this thread was opened 2026-01-15 and has been quiet since.")


def test_historical_date_citation_is_not_treated_as_a_due_date():
    """The note shape that produced the false months-overdue report."""
    got = _nf.infer_followup_date(CITATION_LAST, CITATION_NOTE)
    assert got != CITATION_CITED, (
        "a date cited as history was used as the deadline -- the months-overdue bug")
    assert got >= CITATION_LAST, (
        f"inferred a due date of {got}, which precedes the last interaction")
    assert got == CITATION_DUE, (
        f"the month-name trigger should win once the citation is rejected; got {got}")


def test_a_due_date_never_precedes_the_last_interaction():
    """The general invariant, stated as a property rather than an example."""
    last = _date(2026, 8, 18)
    for note in ("follow up by 2026-01-01",
                 "~2026-02-14 is the date",
                 "discussed on 2026-03-05, revisit sometime",
                 "see the 2020-01-01 thread"):
        got = _nf.infer_followup_date(last, note)
        assert got is None or got >= last, f"{note!r} -> {got}, which is before {last}"


def test_a_future_iso_date_is_still_honoured():
    """Guards against the fix degrading into 'ignore all explicit dates'.

    Without this, deleting the explicit-date branches entirely would pass the
    two tests above.
    """
    assert _nf.infer_followup_date(_date(2026, 8, 18), "nudge on 2026-09-20") == _date(2026, 9, 20)
    assert _nf.infer_followup_date(_date(2026, 8, 18), "~2026-10-01 re-engage") == _date(2026, 10, 1)


def test_month_name_trigger_is_parsed_rather_than_dropped():
    """"~mid-Oct 2026" is a real deadline shape in the corpus and must not vanish.

    Rejecting the bad citation is only half the fix: if nothing else parses, the
    contact drops out of the nudge list entirely and the real mid-October trigger
    never fires. A false alarm replaced by silence is not an improvement.
    """
    assert _nf.infer_followup_date(_date(2026, 8, 18), "re-engage ~mid-Oct 2026") == _date(2026, 10, 15)
    assert _nf.infer_followup_date(_date(2026, 8, 18), "check back early Nov 2026") == _date(2026, 11, 5)
    assert _nf.infer_followup_date(_date(2026, 8, 18), "late April 2027 at the earliest") == _date(2027, 4, 25)


def test_month_name_without_a_year_resolves_forward_not_backward():
    """A bare month name means the NEXT occurrence, never one already past."""
    got = _nf.infer_followup_date(_date(2026, 8, 18), "circle back mid-July")
    assert got is None or got >= _date(2026, 8, 18), f"resolved backwards to {got}"


def test_citation_note_contact_is_not_reported_overdue(tmp_path):
    """End-to-end through the shipped CLI, not just the helper.

    Pins the reported symptom: a contact must not appear in followup_overdue on a
    date when their real trigger has not yet arrived.
    """
    import json
    contacts = "| Casey Doe | Acme | VP of Growth | warm | 2026-03-01 | 2026-03-02 | c@example.com |\n"
    log = ("### Casey Doe — Acme\n\n"
           "#### 2026-03-02 | linkedin | INBOUND reply, no ask, no date.\n\n"
           "**Follow-up:** " + CITATION_NOTE + "\n")
    write_fixture(tmp_path, "data/networking.md", make_fixture(contacts, log))
    data = run_script("networking_followup.py",
                      "--repo-root", str(tmp_path),
                      "--target-date", "2026-09-03")
    overdue = [e["name"] for e in data.get("followup_overdue", [])]
    assert "Casey Doe" not in overdue, (
        f"still reported overdue: {json.dumps(data.get('followup_overdue'), indent=1)}")


def test_tilde_date_wins_over_an_earlier_plain_date():
    """The tilde is a PRIORITY signal, not a synonym for the general date match.

    Without this, the tilde branch is indistinguishable from the scan below it
    (both find the same date when there is only one), and deleting it changes
    nothing that any test can see.
    """
    note = "we discussed 2026-09-20 informally, but the real deadline is ~2026-12-01"
    assert _nf.infer_followup_date(_date(2026, 8, 18), note) == _date(2026, 12, 1)


def test_a_past_tilde_date_falls_through_to_a_later_valid_date():
    """The past-date guard must SKIP a bad hit, not abort the whole inference.

    If the guard returned its rejection instead of continuing, every note whose
    first date is historical would infer nothing at all -- trading a false alarm
    for silence, which is the failure this fix exists to avoid.
    """
    note = "originally flagged ~2026-02-11, now scheduled for 2026-09-20"
    assert _nf.infer_followup_date(_date(2026, 8, 18), note) == _date(2026, 9, 20)


def test_a_malformed_date_does_not_stop_the_scan():
    """2026-13-45 is date-SHAPED but not a date. The scan must keep going."""
    note = "bad ticket ref 2026-13-45, follow up 2026-09-20"
    assert _nf.infer_followup_date(_date(2026, 8, 18), note) == _date(2026, 9, 20)


def test_bare_month_name_rolls_to_next_year_when_this_year_is_past():
    """"mid-July" said in November means next July, not four months ago."""
    assert _nf.infer_followup_date(_date(2026, 11, 1), "circle back mid-July") == _date(2027, 7, 15)


def test_bare_month_name_stays_in_this_year_when_still_ahead():
    """Guards the roll-forward from becoming an unconditional +1 year."""
    assert _nf.infer_followup_date(_date(2026, 8, 18), "circle back mid-Oct") == _date(2026, 10, 15)


def test_month_name_with_a_past_year_is_rejected_not_used():
    """An explicit past year is a citation too, and gets the same guard."""
    last = _date(2026, 8, 18)
    got = _nf.infer_followup_date(last, "we met late April 2024")
    # NOT `is None`: this function has a 14-day default at the end, so it never
    # returns None when last_date is set. Asserting None here (as an earlier
    # revision of this test did) asserts a contract the function does not have,
    # and it passed only because the pre-fix code returned None by short-circuit,
    # wrongly skipping that default. The real property is the invariant.
    assert got >= last, f"used a past month-name date as the deadline: {got}"
    assert got == last + _timedelta(days=14), (
        f"expected the 14-day default after rejecting the citation, got {got}")


def test_an_explicit_year_is_used_rather_than_the_next_occurrence():
    """Pins that the year branch and the no-year branch are actually different.

    Both produce the same answer for every near-term date, so without a year far
    enough out to diverge, the `if year_txt:` test can be inverted with no test
    noticing.
    """
    assert _nf.infer_followup_date(_date(2026, 8, 18), "revisit mid-Oct 2028") == _date(2028, 10, 15)


def test_a_historical_month_name_does_not_kill_a_later_real_trigger():
    """The ISO fix, applied one layer down, to the branch the ISO fix introduced.

    Found 2026-09-03 by asking whether the fix reproduced the defect it fixed. It
    did: the month-name branch used re.search (first match wins) and returned from
    inside the match block, so "we met late April 2024; next touch is mid-Oct 2026"
    inferred NOTHING -- the citation was rejected and the real trigger never read.
    Same shape as the bug this whole change exists to fix, one layer down.
    """
    last = _date(2026, 8, 18)
    assert _nf.infer_followup_date(
        last, "we met late April 2024; next touch is mid-Oct 2026") == _date(2026, 10, 15)
    assert _nf.infer_followup_date(
        last, "intro'd early Jan 2023, revisit mid-Nov 2026") == _date(2026, 11, 15)


def test_both_citation_shapes_can_precede_the_real_trigger():
    """An ISO citation AND a month-name citation, then the actual deadline."""
    note = "flagged 2026-02-11 historically, met late April 2024, real trigger mid-Oct 2026"
    assert _nf.infer_followup_date(_date(2026, 8, 18), note) == _date(2026, 10, 15)


def test_a_bad_year_in_a_month_name_does_not_stop_the_scan():
    """Kills the month-name `except ValueError: continue`, which is NOT equivalent.

    Dropping that `continue` reaches `if hit` with `hit` unbound. It was previously
    covered by an allowlist reason written about a DIFFERENT statement -- the two
    `continue`s in this function shared one mutant key until 2026-09-03. A four-digit
    year of 0000 matches the regex and makes date() raise, so the handler is reachable
    and the scan must go on to the later valid trigger.
    """
    note = "opened mid-Oct 0000 per the old record; real trigger mid-Nov 2026"
    assert _nf.infer_followup_date(_date(2026, 8, 18), note) == _date(2026, 11, 15)
