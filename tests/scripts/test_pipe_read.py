"""Tests for tools/pipe_read.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


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
