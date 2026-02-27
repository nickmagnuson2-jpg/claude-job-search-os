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
        | 2026-02-26 | cold-outreach | email | Sarah Chen | Stripe | Hello | Drafted |
        | 2026-02-25 | follow-up | linkedin | Alex | Amae | Follow | Drafted |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert len(result["outreach_sent_today"]) == 1
    assert result["outreach_sent_today"][0]["name"] == "Sarah Chen"


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
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | Amae Health | Strategy & Ops | Researching | 2026-02-20 | 2026-02-20 | — | — | — |
        | Stripe | CoS | Applied | 2026-02-15 | 2026-02-22 | stripe-cos.md | — | — |
    """)

    result = run_script("todo_daily_metrics.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert len(result["pipeline_snapshot"]) == 2
    companies = [e["company"] for e in result["pipeline_snapshot"]]
    assert "Amae Health" in companies
    assert "Stripe" in companies
