"""Tests for tools/pipe_read.py"""
from pathlib import Path

import pytest

from conftest import run_script, write_fixture


def test_missing_file_no_crash(tmp_path):
    """Missing pipeline file returns empty result without crashing."""
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert result["active_entries"] == []
    assert result["metrics"]["total_active"] == 0


def test_stale_entry_detected(tmp_path):
    """Applied entry updated 10 days ago (threshold=5) is stale and flagged for attention."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Acme | Engineer | Applied | 2026-02-18 | Follow up | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["active_entries"]) == 1
    entry = result["active_entries"][0]
    assert entry["stale"] is True
    assert entry["needs_attention"] is True
    assert entry["days_since_update"] == 10
    assert "10" in entry["stale_label"]


def test_backlog_stage_not_flagged_stale(tmp_path):
    """A backlog stage ('To Evaluate') far past the default threshold must NOT be
    flagged stale — only active-pursuit stages are staleness-eligible. Regression for
    fable-audit Theme 2: pipe_read lacked the active-pursuit gate, so it reported
    ~76 stalled rows vs pipeline_staleness's 3 on the same pipeline."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Backlog Co | PM | To Evaluate | 2026-01-01 | Look into it | — | — | — |
        | Live Co | PM | Applied | 2026-02-18 | Follow up | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    by_co = {e["company"]: e for e in result["active_entries"]}
    # 58 days past threshold, but a backlog stage → never nagged stale.
    assert by_co["Backlog Co"]["stale"] is False
    # Active pursuit past its threshold is still stale.
    assert by_co["Live Co"]["stale"] is True
    assert result["metrics"]["total_stalled"] == 1


def test_descriptive_closed_stage_is_archived_not_active(tmp_path):
    """A freeform closed stage ('Closed - lost in final round') must route to
    archived_count, NOT active_entries — even though it contains active-pursuit
    keywords ('round'/'final') that previously flagged it stale. Regression for
    fable-audit Theme 2: the 4-value exact TERMINAL_STAGES left 29 such closed rows
    inflating total_active on the live pipeline."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | LiveCo | PM | Applied | 2026-02-18 | Follow up | — | — | — |
        | ClosedCo | PM | Closed - lost in final round | 2026-01-01 | — | — | — | — |
        | DeclinedCo | PM | Founder intro complete (a CEO) - declined, no current fit | 2026-01-01 | — | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    active = {e["company"] for e in result["active_entries"]}
    assert active == {"LiveCo"}, active
    assert result["metrics"]["total_active"] == 1
    assert result["metrics"]["archived_count"] == 2
    # the closed rows must not be flagged stale despite containing 'round'/'final'
    assert result["metrics"]["total_stalled"] == 1


def test_missing_next_action_flagged(tmp_path):
    """An entry with '—' next action is flagged missing_action=True and needs_attention=True."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Beta | PM | Researching | 2026-02-27 | — | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["active_entries"]) == 1
    entry = result["active_entries"][0]
    assert entry["missing_action"] is True
    assert entry["needs_attention"] is True


def test_archived_entries_excluded(tmp_path):
    """Entries in the Archived section are excluded from active_entries and counted in archived_count."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Acme | Engineer | Researching | 2026-02-27 | Research role | — | — | — |

        ## Archived
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | OldCo | PM | Withdrawn | 2026-01-15 | — | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    active_companies = [e["company"] for e in result["active_entries"]]
    assert "Acme" in active_companies
    assert "OldCo" not in active_companies
    assert result["metrics"]["archived_count"] == 1


def test_stage_distribution(tmp_path):
    """Stage distribution correctly counts entries per stage."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | A | PM | Researching | 2026-02-27 | Research | — | — | — |
        | B | CoS | Researching | 2026-02-27 | Research | — | — | — |
        | C | Eng | Applied | 2026-02-25 | Follow up | — | — | — |
    """)
    result = run_script("pipe_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    dist = result["stage_distribution"]
    assert dist.get("Researching") == 2
    assert dist.get("Applied") == 1
    assert result["metrics"]["total_active"] == 3
