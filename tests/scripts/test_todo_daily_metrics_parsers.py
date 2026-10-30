"""todo_daily_metrics.py parsers: exact outputs, written from the 2026-10-30 mutation run.

That run left 69 survivors in this file: rows that should be skipped (a header, a
separator, a short row, a non-table line) were never fed to the parsers, and results were
checked for presence rather than compared whole. Each test below pins one of those.
"""
import sys
from datetime import date

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import todo_daily_metrics as tdm  # noqa: E402

D = date(2026, 1, 7)
PIPE_HEAD = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
             "| --- | --- | --- | --- | --- | --- | --- | --- |\n")


# --- parse_daily_log -------------------------------------------------------------------

def test_daily_log_counts_only_dated_entries():
    text = "# Daily Log\npreamble line\n### 2026-01-07\n**Completed today: 2**\n"
    out = tdm.parse_daily_log(text, D)
    assert out["entry_count"] == 1 and out["all_time"] == 2 and out["streak"] == 1


def test_daily_log_with_no_entries_has_zero_entries():
    assert tdm.parse_daily_log("# Daily Log\nnothing dated\n", D)["entry_count"] == 0


def _log_with_overdue(today_n, prior_n):
    days = [f"### 2026-01-{7 - i:02d}\nOverdue: {prior_n}\n" for i in range(1, 7)]
    return f"### 2026-01-07\nOverdue: {today_n}\n" + "".join(days) + f"### 2025-12-31\nOverdue: {prior_n}\n"


@pytest.mark.parametrize("today_n,prior_n,want", [
    (5, 0, "↑"), (0, 5, "↓"), (2, 2, "→"), (3, 2, "↑"), (1, 2, "↓"),
])
def test_overdue_trend_compares_today_with_the_prior_seven_days(today_n, prior_n, want):
    assert tdm.parse_daily_log(_log_with_overdue(today_n, prior_n), D)["overdue_trend"] == want


# --- parse_outreach_today --------------------------------------------------------------

OUTREACH = ("| Date | Type | Channel | Recipient | Company | Subject | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 2026-01-07 | cold-outreach | email | Sam Example | Acme Corp | Hello | Sent |\n"
            "| 2026-01-07 | follow-up | linkedin | Lee Sample | Beta Inc |\n"
            "| 2026-01-06 | cold-outreach | email | Old Row | Gamma | Hi | Sent |\n"
            "2026-01-07 | note | text | Not A Row | Delta | prose |\n")


def test_outreach_today_returns_whole_rows_for_today_only():
    assert tdm.parse_outreach_today(OUTREACH, D) == [
        {"date": "2026-01-07", "type": "cold-outreach", "channel": "email",
         "name": "Sam Example", "company": "Acme Corp", "subject": "Hello"},
        {"date": "2026-01-07", "type": "follow-up", "channel": "linkedin",
         "name": "Lee Sample", "company": "Beta Inc", "subject": ""},
    ]


# --- find_research_today ---------------------------------------------------------------

def _dossier(root, slug, name, header_line_index, stamp="Last updated: 2026-01-07"):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    lines = ["filler\n"] * 20
    lines[header_line_index] = stamp + "\n"
    (d / f"{name}.md").write_text("".join(lines), encoding="utf-8")


def test_research_today_finds_only_dossiers_stamped_today_in_the_first_15_lines(tmp_path):
    out = tmp_path / "output"
    _dossier(out, "acme", "acme", 14)              # last line read: found
    _dossier(out, "beta", "beta", 15)              # 16th line: not read
    _dossier(out, "gamma", "notes", 0)             # not a dossier (stem != folder)
    _dossier(out, "delta", "delta", 2, "Last updated: 2026-01-06")
    assert tdm.find_research_today(out, D) == [{"name": "acme", "path": "output/acme/acme.md"}]


def test_research_today_on_an_empty_tree_is_an_empty_list(tmp_path):
    (tmp_path / "output").mkdir()
    assert tdm.find_research_today(tmp_path / "output", D) == []


# --- parse_changelog_today -------------------------------------------------------------

def test_changelog_today_lists_only_todays_headings():
    text = "## 2026-01-07 — First\n## 2026-01-06 — Old\n## 2026-01-07 - Second\n"
    assert tdm.parse_changelog_today(text, D) == [
        {"title": "First", "date": "2026-01-07"}, {"title": "Second", "date": "2026-01-07"}]
    assert tdm.parse_changelog_today("## 2026-01-06 — Old\n", D) == []


# --- _bucket_for_stage / parse_pipeline ------------------------------------------------

def test_terminal_stages_bucket_as_terminal():
    assert tdm._bucket_for_stage("Closed") == "terminal"
    assert tdm._bucket_for_stage(" rejected ") == "terminal"


PIPE = ("# Pipeline\n\n## Active\n" + PIPE_HEAD +
        "| Acme | PM | Applied | 2026-01-01 | — | — | — | — |\n"
        "| Short | PM |\n"
        "| --- | x | Applied | 2026-01-01 | — | — | — | — |\n"
        "| — | x | Applied | 2026-01-01 | — | — | — | — |\n"
        "| Beta | Ops | To Evaluate | 2026-01-01 | — | — | — | — |\n"
        "| Gamma | CoS | Rejected | 2026-01-01 | — | — | — | — |\n"
        "\n## Archived\n" + PIPE_HEAD +
        "| Old Co | PM | Applied | 2025-01-01 | — | — | — | — |\n")


def test_parse_pipeline_buckets_only_real_active_rows():
    assert tdm.parse_pipeline(PIPE) == {
        "active_process": [{"company": "Acme", "role": "PM", "stage": "Applied"}],
        "evaluation_backlog": [{"company": "Beta", "role": "Ops", "stage": "To Evaluate"}],
        "terminal": [{"company": "Gamma", "role": "CoS", "stage": "Rejected"}],
    }


def test_parse_pipeline_without_an_active_heading_reads_the_whole_table():
    text = "# Pipeline\n" + PIPE_HEAD + "| Acme | PM | Applied | 2026-01-01 | — | — | — | — |\n"
    assert tdm.parse_pipeline(text)["active_process"] == [
        {"company": "Acme", "role": "PM", "stage": "Applied"}]


def test_is_separator_row_is_strictly_boolean():
    assert tdm._is_separator_row("---") is True and tdm._is_separator_row("Acme") is False


# --- _extract_dates --------------------------------------------------------------------

def test_extract_dates_skips_an_impossible_month_day_without_reusing_the_last_one():
    assert tdm._extract_dates("2/30", D) == []
    assert tdm._extract_dates("1/8 then 2/30", D) == [date(2026, 1, 8)]


# --- parse_upcoming_scheduled ----------------------------------------------------------

UP = ("# Pipeline\n\n## Active\n" + PIPE_HEAD +
      "| Acme | PM | Applied | 2026-01-01 | Interview Thu 1/8 2pm | — | — | — |\n"
      "| Short | PM | Applied | 2026-01-01 |\n"
      "| --- | x | Applied | 2026-01-01 | call 1/8 | — | — | — |\n"
      "Prose | with | pipes | here | call 1/8 | x |\n"
      "\n## Archived\n" + PIPE_HEAD +
      "| Old Co | PM | Applied | 2025-01-01 | Interview 1/8 | — | — | — |\n")


def test_upcoming_scheduled_reads_only_real_active_rows():
    out = tdm.parse_upcoming_scheduled(UP, D)
    assert [(e["company"], e["date"], e["is_event"]) for e in out] == [("Acme", "2026-01-08", True)]


def test_upcoming_scheduled_without_an_active_heading_reads_the_table():
    text = "# Pipeline\n" + PIPE_HEAD + "| Acme | PM | Applied | 2026-01-01 | call 1/8 | — | — | — |\n"
    assert [e["company"] for e in tdm.parse_upcoming_scheduled(text, D)] == ["Acme"]


# --- parse_networking_activity ---------------------------------------------------------

NET = ("| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
       "|---|---|---|---|---|---|---|\n"
       "| Sam Example | Acme Corp | PM | peer | 2026-01-01 | 2026-01-05 | — |\n"
       "| Lee Sample | Beta Inc | Eng |\n"
       "| Short Row | Gamma |\n"
       "| — | Delta | x | y |\n"
       "| Pat Nobody | Echo | x | y | z |\n"
       "Name Prose | Foxtrot | x | y |\n")
TODOS = "| Follow up: Sam Example | Med |\n| Ping Lee Sample, then Sam Example | Low |\n| Name Prose — | x |\n| Short Row | x |\n| — | x |\n"


def test_networking_activity_lists_real_contacts_named_in_todos_with_counts():
    # PINS CURRENT BEHAVIOR, NOT CORRECT BEHAVIOR: "last_interaction" is read from cols[4],
    # which in today's networking.md schema is the Added column (Last Interaction is
    # cols[5]). Found 2026-10-30 while writing this test; reported for a fix, not fixed here
    # (the run allowed test changes only). Change this expectation with the fix.
    assert tdm.parse_networking_activity(NET, TODOS) == [
        {"name": "Sam Example", "company": "Acme Corp", "last_interaction": "2026-01-01",
         "pending_todos": 2},
        {"name": "Lee Sample", "company": "Beta Inc", "last_interaction": "", "pending_todos": 1},
    ]


def test_networking_activity_skips_the_header_even_when_todos_say_name():
    assert tdm.parse_networking_activity(NET, "Name and Company") == []
