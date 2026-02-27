"""Tests for tools/pipeline_staleness.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


def test_empty_pipeline(tmp_path):
    """Script handles missing pipeline file gracefully."""
    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["active_entries"] == []
    assert result["stalled_entries"] == []
    assert result["metrics"]["total_active"] == 0


def test_stalled_entry_detected(tmp_path):
    """An entry updated 10 days ago in 'Applied' stage (threshold 5) is flagged stalled."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | Acme | Engineer | Applied | 2026-02-16 | 2026-02-16 | — | — | — |
        | Fresh | CoS | Researching | 2026-02-24 | 2026-02-24 | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    assert result["metrics"]["total_active"] == 2
    # Acme Applied: 10 days, threshold 5 → stalled
    stalled_companies = [e["company"] for e in result["stalled_entries"]]
    assert "Acme" in stalled_companies
    # Fresh Researching: 2 days, threshold 7 → not stalled
    assert "Fresh" not in stalled_companies


def test_terminal_stages_excluded(tmp_path):
    """Withdrawn/Rejected/Accepted entries are not counted as active."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | Active Co | PM | Researching | 2026-02-25 | 2026-02-25 | — | — | — |

        ## Archived
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | Done Co | CoS | Withdrawn | 2026-02-01 | 2026-02-01 | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    companies = [e["company"] for e in result["active_entries"]]
    assert "Active Co" in companies
    assert "Done Co" not in companies


def test_stage_distribution(tmp_path):
    """Stage distribution counts are correct."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | A | PM | Researching | 2026-02-25 | 2026-02-25 | — | — | — |
        | B | CoS | Researching | 2026-02-25 | 2026-02-25 | — | — | — |
        | C | Eng | Applied | 2026-02-20 | 2026-02-20 | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    dist = result["stage_distribution"]
    assert dist.get("Researching") == 2
    assert dist.get("Applied") == 1


def test_custom_threshold_override(tmp_path):
    """--days-threshold overrides per-stage defaults."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |
        |---------|------|-------|-----------|--------------|---------|-----|-------|
        | A | PM | Researching | 2026-02-25 | 2026-02-25 | — | — | — |
    """)

    # With threshold=1, an entry updated 1 day ago should be stalled
    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold", "1",
                        "--repo-root", str(tmp_path))

    assert len(result["stalled_entries"]) == 1
