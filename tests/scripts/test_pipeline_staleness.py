"""Tests for tools/pipeline_staleness.py

Schema note: the canonical job-pipeline.md schema (written by tools/pipe_write.py) is
    | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
The date used for staleness is the "Date Updated" column (index 3). An earlier
version of these fixtures encoded an obsolete "Date Added | Date Updated | CV Used |
URL | Notes" layout that the live file never used, which masked a column-index bug
in the parser (it read the date from "Next Action"). Fixtures below match the live schema.
"""
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
    """An entry whose Date Updated is 10 days ago in 'Applied' (threshold 5) is stalled."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme | Engineer | Applied | 2026-02-16 | — | — | — | — |
        | Fresh | CoS | Researching | 2026-02-24 | — | — | — | — |
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
    # The date must be read from Date Updated (col 3), producing a real day count.
    acme = next(e for e in result["active_entries"] if e["company"] == "Acme")
    assert acme["days_since_update"] == 10


def test_next_action_prose_not_parsed_as_date(tmp_path):
    """Regression: prose in the Next Action column must not break date parsing.

    The old parser read the date from Next Action (col 4); a row with prose there
    yielded days_since_update=None and silently never stalled. The date lives in
    Date Updated (col 3); Next Action prose is ignored for staleness.
    """
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Beacon | BizOps | Applied | 2026-02-16 | Follow up if no response by 2026-03-06; run /prep-interview | — | Remote role | https://example.com |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    entry = result["active_entries"][0]
    assert entry["days_since_update"] == 10        # parsed from Date Updated, not the prose
    assert entry["company"] in [e["company"] for e in result["stalled_entries"]]
    # url/notes must come from the correct columns (not swapped)
    assert entry["url"] == "https://example.com"
    assert entry["notes"] == "Remote role"


def test_backlog_stages_never_stalled(tmp_path):
    """Active-pursuit-only: backlog/non-pursuit stages are excluded from the stalled list
    even when their Date Updated is ancient. They still count in the distribution."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Backlog1 | PM | To Evaluate | 2026-01-01 | — | — | — | — |
        | Backlog2 | PM | To Apply (warm-intro path) | 2026-01-01 | — | — | — | — |
        | Cold3 | PM | Deprioritized | 2026-01-01 | — | — | — | — |
        | Live4 | PM | Applied | 2026-01-01 | — | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    stalled = [e["company"] for e in result["stalled_entries"]]
    assert stalled == ["Live4"]                    # only the active-pursuit row
    # All four still counted as active + in the distribution
    assert result["metrics"]["total_active"] == 4
    assert result["stage_distribution"].get("To Evaluate") == 1


def test_freeform_round_stage_is_active_pursuit(tmp_path):
    """A freeform stage containing an active-pursuit keyword (e.g. 'round') is eligible."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Lumen | Product Ops | Strategic Thinking round complete (6/8); awaiting next-step | 2026-02-10 | — | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    assert [e["company"] for e in result["stalled_entries"]] == ["Lumen"]


def test_terminal_stages_excluded(tmp_path):
    """Withdrawn/Rejected/Accepted entries and Archived sections are not counted active."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Active Co | PM | Researching | 2026-02-25 | — | — | — | — |

        ## Archived
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Done Co | CoS | Withdrawn | 2026-02-01 | — | — | — | — |
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
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | A | PM | Researching | 2026-02-25 | — | — | — | — |
        | B | CoS | Researching | 2026-02-25 | — | — | — | — |
        | C | Eng | Applied | 2026-02-20 | — | — | — | — |
    """)

    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    dist = result["stage_distribution"]
    assert dist.get("Researching") == 2
    assert dist.get("Applied") == 1


def test_custom_threshold_override(tmp_path):
    """--days-threshold overrides per-stage defaults (still active-pursuit gated)."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | A | PM | Researching | 2026-02-25 | — | — | — | — |
    """)

    # With threshold=1, an entry updated 1 day ago should be stalled
    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold", "1",
                        "--repo-root", str(tmp_path))

    assert len(result["stalled_entries"]) == 1
