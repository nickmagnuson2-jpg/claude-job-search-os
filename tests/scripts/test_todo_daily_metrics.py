"""Tests for tools/todo_daily_metrics.py"""
import json
import textwrap
from pathlib import Path

import pytest

from conftest import run_script, FIXTURES_DIR, REPO_ROOT


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    """Write a fixture file into a temp directory tree."""
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_todos(tmp_path):
    """Script handles missing todos file gracefully."""
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["completed_today"] == []
    assert result["active_remaining"] == []
    assert result["overdue"] == []


def test_completed_today_detected(tmp_path):
    """Completed tasks with today's date in Notes are surfaced."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Search To-Dos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|

        ## Completed
        | Task | Priority | Completed | Notes |
        |------|----------|-----------|-------|
        | Update LinkedIn | High | 2026-02-26 | Completed 2026-02-26 |
        | Old task | Med | 2026-01-15 | Completed 2026-01-15 |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert len(result["completed_today"]) == 1
    assert result["completed_today"][0]["task"] == "Update LinkedIn"


def test_withdrawn_today_not_counted_as_completion(tmp_path):
    """A row withdrawn today must NOT count as a completion (no streak/velocity inflation).

    The Completed section holds both Done and Withdrawn rows (Withdrawn marked in the
    Completed column or Notes). Only genuine completions count toward completed_today.
    """
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Search To-Dos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|

        ## Completed
        | Task | Priority | Completed | Notes |
        |------|----------|-----------|-------|
        | Real win today | High | 2026-02-26 | Completed 2026-02-26 |
        | Superseded plan | Med | Withdrawn 2026-02-26 | superseded by specifics |
        | Done-but-noted-withdrawn | Low | 2026-02-26 | Withdrawn 2026-02-26 |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    tasks = [c["task"] for c in result["completed_today"]]
    assert tasks == ["Real win today"]                 # the two withdrawn rows excluded


def test_overdue_active_tasks(tmp_path):
    """Active tasks past their due date are flagged as overdue."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Search To-Dos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
        | Old task | High | 2026-01-01 | Pending | — |
        | Future task | Med | 2026-12-31 | Pending | — |
        | No due | Low | — | Pending | — |

        ## Completed
        | Task | Priority | Completed | Notes |
        |------|----------|-----------|-------|
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert len(result["overdue"]) == 1
    assert result["overdue"][0]["task"] == "Old task"
    assert len(result["active_remaining"]) == 3


def test_outreach_today(tmp_path):
    """Outreach entries for today are extracted."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-26 | cold-outreach | email | Casey Doe | ClosedCo | Hello | Drafted |
        | 2026-02-25 | follow-up | linkedin | Alex | Acme | Follow | Drafted |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert len(result["outreach_sent_today"]) == 1
    assert result["outreach_sent_today"][0]["name"] == "Casey Doe"


def test_streak_calculation(tmp_path):
    """Streak counts consecutive days with completions."""
    write_fixture(tmp_path, "data/job-todos-daily-log.md", """\
        # Job Search — Daily Progress Log

        ### 2026-02-26 (Thursday)
        **Completed today: 2** | Active remaining: 3 | Overdue: 0

        ### 2026-02-25 (Wednesday)
        **Completed today: 1** | Active remaining: 4 | Overdue: 0

        ### 2026-02-24 (Tuesday)
        **Completed today: 0** | Active remaining: 5 | Overdue: 1

        ### 2026-02-23 (Monday)
        **Completed today: 3** | Active remaining: 4 | Overdue: 1
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    # Streak should be 2: Feb 26 + Feb 25. Feb 24 has 0, breaking streak.
    assert result["metrics"]["streak"] == 2
    assert result["metrics"]["this_week"] == 6  # 2+1+0+3 (all 4 entries are within 7-day window)


def test_no_daily_log(tmp_path):
    """Script handles missing daily log gracefully — streak is 0."""
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["metrics"]["streak"] == 0
    assert result["metrics"]["all_time"] == 0


def test_pipeline_snapshot(tmp_path):
    """Active pipeline entries are returned in snapshot."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme AI | Strategy & Ops | Researching | 2026-02-20 | — | — | — | — |
        | ClosedCo | CoS | Applied | 2026-02-22 | — | closedco-cos.md | — | — |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    # pipeline_snapshot is now a categorized dict (active_process /
    # evaluation_backlog / terminal), not a flat list — flatten across buckets.
    snap = result["pipeline_snapshot"]
    companies = [e["company"] for bucket in snap.values() for e in bucket]
    assert len(companies) == 2
    assert "Acme AI" in companies            # Researching -> evaluation_backlog
    assert "ClosedCo" in companies           # Applied -> active_process


# ---------------------------------------------------------------------------
# upcoming_scheduled — interviews/screens parsed from pipeline Next-Action.
# Fixtures MUST use the LIVE pipeline header (Next Action at column index 4),
# not the stale header in test_pipeline_snapshot above. Origin 2026-06-11.
# ---------------------------------------------------------------------------

_LIVE_PIPELINE_HEADER = """\
    # Job Pipeline

    ## Active
    | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
    | --- | --- | --- | --- | --- | --- | --- | --- |
"""


def _pipeline(tmp_path, *rows):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  _LIVE_PIPELINE_HEADER + "".join("    " + r + "\n" for r in rows))


def test_upcoming_scheduled_surfaces_event_in_window(tmp_path):
    """A dated interview in Next-Action within 3 days surfaces, flagged is_event."""
    _pipeline(tmp_path,
        "| ActiveCo | Deployment Strategist | Next round | 2026-06-11 | "
        "Interview w/ Casey Doe Fri 6/12 12:30pm PT (30 min) | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    up = result["upcoming_scheduled"]
    assert len(up) == 1
    assert up[0]["company"] == "ActiveCo"
    assert up[0]["date"] == "2026-06-12"
    assert up[0]["days_until"] == 1
    assert up[0]["is_event"] is True


def test_upcoming_scheduled_iso_date_in_window(tmp_path):
    """ISO-format dates in Next-Action are parsed too."""
    _pipeline(tmp_path,
        "| LiveCo | CoS | Applied | 2026-06-11 | "
        "Screen scheduled 2026-06-13 10am | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    up = result["upcoming_scheduled"]
    assert len(up) == 1
    assert up[0]["date"] == "2026-06-13"
    assert up[0]["is_event"] is True


def test_upcoming_scheduled_excludes_past_and_far_dates(tmp_path):
    """Past dates and dates beyond the window do not surface."""
    _pipeline(tmp_path,
        "| PastCo | Role | Applied | 2026-06-01 | "
        "Followed up 6/2 already | — | — | — |",
        "| FarCo | Role | Applied | 2026-06-11 | "
        "Interview 6/20 2pm | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    assert result["upcoming_scheduled"] == []


def test_upcoming_scheduled_followup_not_flagged_as_event(tmp_path):
    """A dated follow-up with no event keyword surfaces but is_event is False."""
    _pipeline(tmp_path,
        "| FollowCo | Role | Applied | 2026-06-11 | "
        "Follow up if no response by 6/13 | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    up = result["upcoming_scheduled"]
    assert len(up) == 1
    assert up[0]["is_event"] is False


def test_upcoming_scheduled_ignores_none_next_action(tmp_path):
    """'none' / em-dash Next-Action rows are skipped."""
    _pipeline(tmp_path,
        "| QuietCo | Role | Rejected | 2026-06-11 | none | — | — | — |",
        "| DashCo | Role | Applied | 2026-06-11 | — | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    assert result["upcoming_scheduled"] == []


def test_upcoming_scheduled_year_rollover(tmp_path):
    """A bare M/D in early Jan, evaluated in late Dec, infers next year."""
    _pipeline(tmp_path,
        "| RollCo | Role | Next round | 2026-12-30 | "
        "Interview 1/2 9am | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-12-30",
                        "--repo-root", str(tmp_path))
    up = result["upcoming_scheduled"]
    assert len(up) == 1
    assert up[0]["date"] == "2027-01-02"
    assert up[0]["days_until"] == 3


def test_upcoming_scheduled_sorted_event_before_followup(tmp_path):
    """Same date: a true event sorts before a generic follow-up."""
    _pipeline(tmp_path,
        "| FollowCo | Role | Applied | 2026-06-11 | "
        "Follow up by 6/12 | — | — | — |",
        "| EventCo | Role | Next round | 2026-06-11 | "
        "Interview 6/12 3pm | — | — | — |")
    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-06-11",
                        "--repo-root", str(tmp_path))
    up = result["upcoming_scheduled"]
    assert [e["company"] for e in up] == ["EventCo", "FollowCo"]


# ===========================================================================
# repo_root must be resolved before building the memory-dir key (2026-08-19)
#
# The auto-memory directory lives OUTSIDE the repo at
# ~/.claude/projects/<sanitized-cwd>/memory/, and the key is built from the
# repo path TEXT. A relative --repo-root (`.` — the documented convention
# everywhere else in this repo) produced the key "-." , missed the directory,
# and reported memories_today: 0 while 23 memory files had been written that
# day. No error, no warning: a confident wrong number in the /checkout summary,
# which then reads as "quiet day."
# ===========================================================================

# (A companion unit test was written and then DELETED: it recomputed the sanitized
# key itself rather than importing the source, so it passed whether or not the fix
# was present -- a tautological assertion, the shape CLAUDE.md:23 warns about. The
# end-to-end test below is the real guard: verified to FAIL when .resolve() is
# reverted.)
def test_relative_and_absolute_repo_root_agree_on_substantive_work():
    """End-to-end guard: the two spellings must not disagree on any count.

    Runs against the REAL repo deliberately — the whole failure mode was that a
    fixture tree would have passed while the live invocation returned 0, since
    the miss depends on the actual out-of-repo memory directory existing.
    """
    rel = run_script(
        "todo_daily_metrics.py", "--target-date", "2026-08-19", "--repo-root", ".",
    )
    absolute = run_script(
        "todo_daily_metrics.py", "--target-date", "2026-08-19",
        "--repo-root", str(REPO_ROOT),
    )
    assert rel["substantive_work"] == absolute["substantive_work"], \
        "relative vs absolute --repo-root disagree on substantive_work"
