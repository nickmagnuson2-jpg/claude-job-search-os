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


def test_update_new_role_renames_role_column(tmp_path):
    """--new-role rewrites the Role cell; stage and other columns still update."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
        "--new-role", "AI Field Operations",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["role"] == "AI Field Operations"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    assert len(rows) == 1, "rename must not duplicate the row"
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[1] == "AI Field Operations"   # Role renamed
    assert cols[2] == "Applied"               # Stage still applied


def test_update_without_new_role_preserves_role(tmp_path):
    """Omitting --new-role leaves the Role cell untouched (guards the default path)."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Acme Corp", "Applied"
    )
    assert code == 0
    assert result["role"] == "Director"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[1] == "Director"


def test_update_new_role_selects_with_role_then_renames(tmp_path):
    """--role selects which of two rows to rename; the sibling row is untouched."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_TWO_ROLES)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "MultiCo", "Interview",
        "--role", "Director", "--new-role", "Deployments",
    )
    assert code == 0
    assert result["role"] == "Deployments"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| MultiCo |")]
    assert len(rows) == 2, "rename must not add or drop rows"
    roles = sorted(
        [c.strip() for c in r.strip("|").split("|")][1] for r in rows
    )
    assert roles == ["Deployments", "PM"], "only the selected row is renamed"


def test_update_new_role_sanitizes_pipe(tmp_path):
    """A '|' in --new-role cannot break the table into extra columns."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
        "--new-role", "Deployments | Field Ops",
    )
    assert code == 0

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert len(cols) == 8, "row must still have exactly 8 columns"
    assert "|" not in cols[1]


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


def test_remove_stage_records_the_real_terminal_outcome(tmp_path):
    """--stage lets the archived row say what actually happened.

    Default is Withdrawn, but a loop that ended because the company passed is a
    Rejected, not a Withdrawn. Recording it as Withdrawn inverts the fact and
    corrupts any conversion math computed off this file.
    """
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "remove", "Acme Corp", "--stage", "Rejected"
    )
    assert code == 0
    assert result["status"] == "ok"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    lines = content.splitlines()
    archived_start = next(i for i, l in enumerate(lines) if l.strip() == "## Archived")
    row = next(l for l in lines[archived_start:] if l.startswith("| Acme Corp |"))
    cols = [c.strip() for c in row.strip("|").split("|")]

    assert cols[2] == "Rejected"
    assert "Rejected" in cols[6]        # notes stamp matches the stage
    assert "Withdrawn" not in cols[6]   # and does not contradict it


def test_remove_stage_defaults_to_withdrawn(tmp_path):
    """Existing callers that pass no --stage must behave exactly as before."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "remove", "Acme Corp")
    assert code == 0

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    lines = content.splitlines()
    archived_start = next(i for i, l in enumerate(lines) if l.strip() == "## Archived")
    row = next(l for l in lines[archived_start:] if l.startswith("| Acme Corp |"))
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[2] == "Withdrawn"


def test_remove_rejects_a_non_terminal_stage(tmp_path):
    """--stage must not be able to archive a row under a live-sounding stage.

    todo_write.py sync only recognises Withdrawn/Rejected/Accepted in Archived;
    anything else would sit there invisible to every terminal-stage consumer.
    """
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "remove", "Acme Corp", "--stage", "Onsite"
    )
    assert code != 0
    assert result["status"] == "error"


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


# ---------------------------------------------------------------------------
# Tests: --repo-root ordering contract (fable-audit Theme 2 doc-drift)
# --repo-root/--dry-run are top-level flags; argparse rejects them AFTER the
# subcommand. Locks the behavior the corrected docstrings/SKILL now describe,
# so the "before the subcommand" convention can't silently re-drift.
# ---------------------------------------------------------------------------

def _run_pipe_write_raw(*args):
    """Run pipe_write.py returning (returncode, stdout, stderr) without parsing."""
    script_path = TOOLS_DIR / "pipe_write.py"
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


def test_repo_root_before_subcommand_succeeds(tmp_path):
    """--repo-root BEFORE the subcommand (documented ordering) parses and adds."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "add", "Acme", "PM",
    )
    assert code == 0
    assert result["status"] == "ok"


def test_repo_root_after_subcommand_rejected():
    """--repo-root AFTER the subcommand is rejected by argparse (exit 2) — the
    old docstring form. No JSON is emitted; the error goes to stderr."""
    code, stdout, stderr = _run_pipe_write_raw(
        "add", "Acme", "PM", "--repo-root", ".",
    )
    assert code == 2
    assert "unrecognized arguments" in stderr
    assert stdout.strip() == ""


# ---------------------------------------------------------------------------
# fit-reason capture (source-coverage fix per 071526-machine-vs-human-agreement)
# ---------------------------------------------------------------------------

def _cols(row_line):
    return [c.strip() for c in row_line.strip().strip("|").split("|")]


def test_add_with_fit_reason_and_verdict(tmp_path):
    """add --fit-reason --fit-verdict writes a [fit-reason ...] tag into Notes, 8 cols."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "add", "Beta", "Deployment Strategist",
        "--fit-verdict", "fit", "--fit-reason", "in-lane FDE at AI-native co",
    )
    assert result["status"] == "ok"
    assert result["fit_reason_logged"] is True
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| Beta ")][0]
    assert "[fit-reason" in row and " fit: in-lane FDE at AI-native co]" in row
    assert len(_cols(row)) == 8


def test_update_fit_reason_sanitizes_pipe_and_appends(tmp_path):
    """A '|' in the reason is sanitized (would break the row) and the tag appends
    to existing Notes rather than clobbering them; row stays 8 columns."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    # seed an existing note first
    run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Screen",
                   "--notes", "Recruiter screen booked")
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "update", "Acme Corp", "Closed - passed (self)",
        "--fit-verdict", "not-fit", "--fit-reason", "CoS re-tread | comp below floor",
    )
    assert result["status"] == "ok" and result["fit_reason_logged"] is True
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| Acme Corp ")][0]
    assert "|" not in _cols(row)[6]  # notes cell has no stray pipe
    assert "CoS re-tread / comp below floor" in row  # sanitized
    assert "Recruiter screen booked" in row  # existing note preserved
    assert len(_cols(row)) == 8


def test_fit_reason_without_verdict_omits_verdict_word(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_pipe_write("--repo-root", str(tmp_path), "add", "Gamma", "PM",
                   "--fit-reason", "researching, no verdict yet")
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| Gamma ")][0]
    # tag present but no fit/not-fit/neutral/unknown token before the colon
    import re
    assert re.search(r"\[fit-reason \d{4}-\d{2}-\d{2}: researching", row)


def test_invalid_fit_verdict_rejected(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    code, stdout, stderr = _run_pipe_write_raw(
        "--repo-root", str(tmp_path), "add", "Delta", "PM",
        "--fit-verdict", "great-fit", "--fit-reason", "x",
    )
    assert code == 2
    assert "invalid choice" in stderr
