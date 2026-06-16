"""Tests for tools/pipe_write.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_script, write_fixture, TOOLS_DIR, REPO_ROOT

# ---------------------------------------------------------------------------
# Helper: run pipe_write.py without check=True (for expected-error cases)
# ---------------------------------------------------------------------------

def run_pipe_write(*args, tmp_path=None):
    """Run pipe_write.py, return (result_dict, returncode)."""
    script_path = TOOLS_DIR / "pipe_write.py"
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(REPO_ROOT),
    )
    return json.loads(result.stdout), result.returncode


# ---------------------------------------------------------------------------
# Minimal pipeline fixture
# ---------------------------------------------------------------------------

PIPELINE_MD = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""

PIPELINE_WITH_ROW = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | — | — |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""

PIPELINE_TWO_ROLES = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MultiCo | PM | Researching | 2026-01-01 | Research | — | — | — |
| MultiCo | Director | Applied | 2026-01-05 | Follow up | — | — | — |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""

PIPELINE_NO_ARCHIVED = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | — | — |
"""


# ---------------------------------------------------------------------------
# Tests: add
# ---------------------------------------------------------------------------

def test_add_new_entry(tmp_path):
    """Adds a new 8-column row to the Active section with today's date."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "add", "Test Co", "PM"
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "add"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "| Test Co |" in content
    assert "| PM |" in content
    assert "| Researching |" in content
    # 8 columns per row (7 pipes inside)
    matching = [l for l in content.splitlines() if "Test Co" in l and l.startswith("|")]
    assert len(matching) == 1
    assert matching[0].count("|") == 9  # 8 columns = 9 pipe chars


def test_add_duplicate_returns_warning(tmp_path):
    """Second add for same company returns duplicate_warning, no new row written."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "add", "Acme Corp", "VP"
    )
    assert code == 0
    assert result["action"] == "duplicate_warning"
    assert "Director" in result["existing_roles"]

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    data_rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    assert len(data_rows) == 1  # still only one row


def test_add_with_url(tmp_path):
    """URL argument is stored in the URL column."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_pipe_write(
        "--repo-root", str(tmp_path),
        "add", "StartupXY", "CoS",
        "--url", "https://startupxy.com/jobs/cos"
    )
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "https://startupxy.com/jobs/cos" in content


def test_add_preserves_existing_rows(tmp_path):
    """Adding a new entry does not disturb the existing row."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    run_pipe_write("--repo-root", str(tmp_path), "add", "NewCo", "Analyst")
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "| Acme Corp |" in content
    assert "| NewCo |" in content


def test_missing_file_returns_error(tmp_path):
    """Missing pipeline file returns a JSON error, not a crash."""
    result, code = run_pipe_write("--repo-root", str(tmp_path), "add", "X", "Y")
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


def test_dry_run_returns_no_file_change(tmp_path):
    """--dry-run returns dry_run:true and does not write the file."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "add", "DryRun Co", "PM"
    )
    assert code == 0
    assert result["dry_run"] is True
    after = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert after == original  # file unchanged


# ---------------------------------------------------------------------------
# Tests: update
# ---------------------------------------------------------------------------

def test_update_stage(tmp_path):
    """Stage and Date Updated change; other columns preserved."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Acme Corp", "Applied"
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["stage"] == "Applied"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[2] == "Applied"        # Stage
    assert cols[1] == "Director"       # Role unchanged


def test_update_ambiguous_returns_error(tmp_path):
    """Two roles for same company without --role returns ambiguous_match error."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_TWO_ROLES)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "MultiCo", "Interview"
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "ambiguous_match"
    assert "matches" in result
    assert len(result["matches"]) == 2


def test_update_nonexistent_returns_error(tmp_path):
    """Updating a company not in the pipeline returns not_found error."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Ghost Corp", "Applied"
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "not_found"


# ---------------------------------------------------------------------------
# Tests: remove
# ---------------------------------------------------------------------------

def test_remove_moves_to_archived(tmp_path):
    """Removed entry disappears from Active and appears in Archived with Withdrawn stage."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "remove", "Acme Corp"
    )
    assert code == 0
    assert result["status"] == "ok"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    lines = content.splitlines()

    # Parse section positions
    active_start   = next(i for i, l in enumerate(lines) if l.strip() == "## Active")
    archived_start = next(i for i, l in enumerate(lines) if l.strip() == "## Archived")

    active_rows   = [l for l in lines[active_start:archived_start] if l.startswith("| Acme Corp |")]
    archived_rows = [l for l in lines[archived_start:] if l.startswith("| Acme Corp |")]

    assert len(active_rows) == 0
    assert len(archived_rows) == 1
    archived_cols = [c.strip() for c in archived_rows[0].strip("|").split("|")]
    assert archived_cols[2] == "Withdrawn"
    assert "Withdrawn" in archived_cols[6]  # date appended to notes


def test_remove_creates_archived_section_if_missing(tmp_path):
    """If ## Archived section is absent, remove creates it and places row there."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_NO_ARCHIVED)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "remove", "Acme Corp"
    )
    assert code == 0

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "## Archived" in content
    assert "| Acme Corp |" in content
    # Should not be in Active anymore
    lines = content.splitlines()
    archived_start = next(i for i, l in enumerate(lines) if "## Archived" in l)
    active_rows = [
        l for l in lines[:archived_start]
        if l.startswith("| Acme Corp |")
    ]
    assert len(active_rows) == 0
