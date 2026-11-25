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
    """Second add for the same company AND role returns duplicate_warning, no new row."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "add", "acme corp", "director"
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


def test_remove_stamps_date_updated_with_the_close_date(tmp_path):
    """Closing a row is a pipeline move, so the archived row's Date Updated is today,
    not the row's last active date (fixture: 2026-01-01). Nick, 2026-11-25."""
    from datetime import date
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "remove", "Acme Corp", "--stage", "Rejected"
    )
    assert code == 0, result
    lines = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
    archived_start = next(i for i, l in enumerate(lines) if l.strip() == "## Archived")
    row = next(l for l in lines[archived_start:] if l.startswith("| Acme Corp |"))
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[3] == date.today().strftime("%Y-%m-%d")


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
    old docstring form. The error goes to stderr AND to stdout as the error JSON,
    so a caller parsing stdout sees the usage message, not a JSONDecodeError."""
    code, stdout, stderr = _run_pipe_write_raw(
        "add", "Acme", "PM", "--repo-root", ".",
    )
    assert code == 2
    assert stderr == "", "the usage error is the JSON on stdout only (2>&1-safe)"
    d = json.loads(stdout)
    assert d["code"] == "usage" and "unrecognized arguments" in d["message"]


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
    assert "invalid choice" in json.loads(stdout)["message"] and stderr == ""


# ---------------------------------------------------------------------------
# Tests: --dry-run must resolve the row, not just echo "ok"
# (2026-09-28: a dry run reported ok for a --role value the real run rejected
# as not_found, so the dry run tested nothing)
# ---------------------------------------------------------------------------

def test_dry_run_update_unmatched_role_fails_like_real_run(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "update", "Acme Corp", "Applied",
        "--role", "Direct",
    )
    assert code != 0
    assert result["code"] == "not_found"
    assert result["roles"] == ["Director"]  # tells the caller the exact Role text
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == original


def test_dry_run_update_unknown_company_fails(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "update", "Nobody Inc", "Applied",
    )
    assert code != 0
    assert result["code"] == "not_found"


def test_dry_run_update_matched_row_reports_it_and_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "update", "Acme Corp", "Applied",
        "--role", "Director",
    )
    assert code == 0
    assert result["dry_run"] is True
    assert result["role"] == "Director"
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == original


def test_dry_run_remove_unmatched_role_fails(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "remove", "Acme Corp",
        "--role", "Direct",
    )
    assert code != 0
    assert result["code"] == "not_found"


def test_dry_run_remove_matched_row_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write(
        "--repo-root", str(tmp_path), "--dry-run", "remove", "Acme Corp",
    )
    assert code == 0
    assert result["dry_run"] is True
    assert result["role"] == "Director"
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == original


MALFORMED_ROW = PIPELINE_WITH_ROW.replace(
    "| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | — | — |",
    "| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | a | b | — |",
)


@pytest.mark.parametrize("cmd", [["update", "Acme Corp", "Applied"], ["remove", "Acme Corp"]])
def test_malformed_row_rejected_in_real_and_dry_runs(tmp_path, cmd):
    for dry in ([], ["--dry-run"]):
        write_fixture(tmp_path, "data/job-pipeline.md", MALFORMED_ROW)
        result, code = run_pipe_write("--repo-root", str(tmp_path), *dry, *cmd)
        assert code != 0, dry
        assert result["code"] == "malformed_row", dry
        assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == MALFORMED_ROW


def test_ambiguous_update_lists_role_and_stage(tmp_path):
    two = PIPELINE_WITH_ROW.replace(
        "| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | — | — |",
        "| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | — | — |\n"
        "| Acme Corp | PM | Applied | 2026-01-01 | Wait | — | — | — |",
    )
    write_fixture(tmp_path, "data/job-pipeline.md", two)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "--dry-run", "update", "Acme Corp", "X")
    assert code != 0 and result["code"] == "ambiguous_match"
    assert {"role": "PM", "stage": "Applied"} in result["matches"]
    result, code = run_pipe_write("--repo-root", str(tmp_path), "--dry-run", "remove", "Acme Corp")
    assert code != 0 and result["matches"] == [{"role": "Director"}, {"role": "PM"}]


# ---------------------------------------------------------------------------
# --notes-append / --notes-prepend (feedback_pipe_write_has_no_notes_append, 3 fires)
# ---------------------------------------------------------------------------

def _acme_notes(tmp_path):
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    row = [l for l in content.splitlines() if l.startswith("| Acme Corp |")][0]
    return [c.strip() for c in row.strip("|").split("|")][6]


def test_update_notes_append_and_prepend_keep_existing_notes(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  PIPELINE_WITH_ROW.replace("| — | — | — |\n\n## Archived", "| — | orig | — |\n\n## Archived"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested",
                                  "--notes-prepend", "PRE", "--notes-append", "POST")
    assert code == 0, result
    assert _acme_notes(tmp_path) == "PRE orig POST"


def test_update_notes_replace_keeps_fit_reason_tags_and_echoes_previous(tmp_path):
    """--notes replaces the cell but never drops scorer input (feedback_read_tool_row_before_overwrite_update)."""
    tag, kept = "[fit-reason 2026-09-14 fit: x [label] z]", "[fit-reason 2026-09-15: y]"
    old = f"orig {tag} {kept}"
    write_fixture(tmp_path, "data/job-pipeline.md",
                  PIPELINE_WITH_ROW.replace("| — | — | — |\n\n## Archived", f"| — | {old} | — |\n\n## Archived"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested",
                                  "--notes", f"new prose {kept}")
    assert code == 0, result
    assert _acme_notes(tmp_path) == f"new prose {kept} {tag}", "dropped tag re-appended once, kept tag not duplicated"
    assert result["previous_notes"] == old


def test_update_without_notes_flags_leaves_an_empty_notes_cell_as_dash(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested")
    assert code == 0, result
    assert _acme_notes(tmp_path) == "—"


def test_update_notes_append_onto_an_empty_cell_drops_the_dash(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested",
                   "--notes-append", "a | b")
    assert _acme_notes(tmp_path) == "a / b", "a '|' in added text must not split the row"


def test_update_notes_with_notes_append_is_refused_without_writing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
                                  "--notes", "x", "--notes-append", "y")
    assert code == 1 and result["code"] == "conflicting_flags"
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_a_second_role_at_the_same_company_is_added(tmp_path):
    """9d dedup audit: keyed on company alone, a new role there was silently dropped."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "add", "Acme Corp", "VP")
    assert code == 0 and result["action"] == "add"
    assert "other roles: Director" in result["summary"]
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert len([l for l in content.splitlines() if l.startswith("| Acme Corp |")]) == 2


# ---------------------------------------------------------------------------
# One owner for the pipeline table (table-writers, 2026-11-02)
# ---------------------------------------------------------------------------

def _rows(tmp_path):
    text = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    return [l for l in text.splitlines() if l.startswith("| ") and not l.startswith("| Company")
            and not l.startswith("| ---")]


def test_fmt_row_escapes_a_pipe_in_every_cell():
    import importlib, sys as _s
    _s.path.insert(0, str(TOOLS_DIR)); pw = importlib.import_module("pipe_write")
    row = pw.fmt_row("A|B", "R|1", "S|", "D", "N|x", "C", "n|o", "u|rl")
    assert row == "| A/B | R/1 | S/ | D | N/x | C | n/o | u/rl |"
    assert row.count("|") == 9


def test_add_takes_notes_and_next_action_and_escapes_them(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "add", "Beta|Co", "PM",
                                  "--notes", "from inbox | a.md", "--next-action", "Read  it",
                                  "--url", "https://x.test/a|b")
    assert code == 0, result
    (row,) = _rows(tmp_path)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert len(cells) == 8
    assert cells[0] == "Beta/Co" and cells[4] == "Read it" and cells[6] == "from inbox / a.md"
    assert cells[7] == "https://x.test/a/b"


def test_add_without_notes_keeps_the_dash_defaults(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_pipe_write("--repo-root", str(tmp_path), "add", "Beta", "PM")
    (row,) = _rows(tmp_path)
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert (cells[4], cells[6], cells[7]) == ("—", "—", "—")


def test_add_notes_and_fit_reason_keep_both(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_pipe_write("--repo-root", str(tmp_path), "add", "Beta", "PM", "--notes", "seed",
                   "--fit-reason", "good fit")
    (row,) = _rows(tmp_path)
    notes = [c.strip() for c in row.strip("|").split("|")][6]
    assert notes.startswith("seed") and "[fit-reason" in notes and "good fit" in notes


def test_note_changes_only_the_notes_cell(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "acme corp",
                                  "--append", "[2026-02-02]: called | left message")
    assert code == 0 and result["action"] == "note", result
    assert result["company"] == "Acme Corp" and result["role"] == "Director"
    assert _rows(tmp_path) == [
        "| Acme Corp | Director | Researching | 2026-01-01 | Research role | — | "
        "[2026-02-02]: called / left message | — |"]


def test_note_appends_after_existing_notes_without_a_pipe(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  PIPELINE_WITH_ROW.replace("| — | — | — |\n", "| — | first | — |\n", 1))
    run_pipe_write("--repo-root", str(tmp_path), "note", "Acme Corp", "--append", "second")
    (row,) = _rows(tmp_path)
    assert row.count("|") == 9
    assert [c.strip() for c in row.strip("|").split("|")][6] == "first second"


def test_note_needs_a_role_when_a_company_has_two_rows(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_TWO_ROLES)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "MultiCo", "--append", "x")
    assert code != 0 and result["code"] == "ambiguous_match"
    assert result["matches"] == [{"role": "PM"}, {"role": "Director"}]
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "MultiCo", "--role", "director",
                                  "--append", "x")
    assert code == 0 and result["role"] == "Director"
    assert _rows(tmp_path)[1].endswith("| Follow up | — | x | — |")


def test_note_falls_back_to_archived(tmp_path):
    text = PIPELINE_MD + "| OldCo | PM | Withdrawn | 2026-01-01 | — | — | — | — |\n"
    write_fixture(tmp_path, "data/job-pipeline.md", text)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "OldCo", "--append", "x")
    assert code == 0, result
    assert _rows(tmp_path) == ["| OldCo | PM | Withdrawn | 2026-01-01 | — | — | x | — |"]


def test_note_prefers_the_active_row_over_an_archived_one(tmp_path):
    text = PIPELINE_WITH_ROW + "| Acme Corp | PM | Withdrawn | 2025-01-01 | — | — | — | — |\n"
    write_fixture(tmp_path, "data/job-pipeline.md", text)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Acme Corp", "--append", "x")
    assert code == 0 and result["role"] == "Director"
    assert _rows(tmp_path)[1].endswith("| — | — | — | — |")


def test_note_refuses_an_unknown_company_and_an_empty_note(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Nobody", "--append", "x")
    assert code != 0 and result["code"] == "not_found"
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Acme Corp", "--role", "PM",
                                  "--append", "x")
    assert code != 0 and result["code"] == "not_found" and "Acme Corp / PM" in result["message"]
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Acme Corp", "--append", "  ")
    assert code != 0 and result["code"] == "empty_note"


def test_note_pads_a_short_row(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  PIPELINE_MD.replace("| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                                      "| --- | --- | --- | --- | --- | --- | --- | --- |\n| Short | PM | x |\n", 1))
    run_pipe_write("--repo-root", str(tmp_path), "note", "Short", "--append", "n")
    assert _rows(tmp_path)[0] == "| Short | PM | x | — | — | — | n | — |"


def test_note_refuses_an_over_wide_row_instead_of_dropping_its_cells(tmp_path):
    """A stray '|' in a cell splits a row into 9+ cells. Padding to 8 and slicing used to drop
    everything past the 8th; cmd_note must refuse, as cmd_update and cmd_remove do."""
    wide = "| Wide | PM | Applied | 2026-01-01 | — | — | old a | old b | https://x.test |\n"
    write_fixture(tmp_path, "data/job-pipeline.md",
                  PIPELINE_MD.replace("| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                                      "| --- | --- | --- | --- | --- | --- | --- | --- |\n" + wide, 1))
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Wide", "--append", "n")
    assert code != 0 and result["code"] == "malformed_row", result
    assert result["column_count"] == 9
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_note_dry_run_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "--dry-run", "note", "Acme Corp",
                                  "--append", "x")
    assert code == 0 and result["dry_run"] is True and result["role"] == "Director"
    assert result["would_mutate"][0]["row"].endswith("| — | x | — |")
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_escaping_is_idempotent_so_a_rewrite_never_double_escapes():
    import importlib, sys as _s
    _s.path.insert(0, str(TOOLS_DIR)); pw = importlib.import_module("pipe_write")
    for value in ("a|b", "a/b", "  a  |  b  ", "—", "[fit-reason x]", "https://x.test/a|b"):
        once = pw.sanitize_cell(value)
        assert pw.sanitize_cell(once) == once


def test_a_company_with_a_pipe_is_found_again_as_a_duplicate(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_pipe_write("--repo-root", str(tmp_path), "add", "Test|Co", "PM|Lead")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "add", "Test|Co", "PM|Lead")
    assert code == 0 and result["action"] == "duplicate_warning", result
    result, code = run_pipe_write("--repo-root", str(tmp_path), "note", "Test|Co", "--append", "x")
    assert code == 0 and result["company"] == "Test/Co"


# ---------------------------------------------------------------------------
# restage: correct the terminal stage of an ARCHIVED row (2026-11-16: a row archived as
# Withdrawn whose own note said the company passed had no tool path, so it was hand-edited)
# ---------------------------------------------------------------------------

ARCHIVED_OLDCO = PIPELINE_MD + "| OldCo | PM | Withdrawn | 2026-01-01 | — | — | they passed | — |\n"


def test_restage_changes_stage_and_records_it_in_notes(tmp_path):
    from datetime import date
    write_fixture(tmp_path, "data/job-pipeline.md", ARCHIVED_OLDCO)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "oldco",
                                  "--stage", "Rejected")
    assert code == 0 and result["action"] == "restage", result
    assert result["previous_stage"] == "Withdrawn" and result["stage"] == "Rejected"
    today = date.today().isoformat()
    assert _rows(tmp_path) == [
        f"| OldCo | PM | Rejected | 2026-01-01 | — | — | they passed "
        f"[restaged {today}: Withdrawn -> Rejected] | — |"]


def test_restage_refuses_an_active_row(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "Acme Corp",
                                  "--stage", "Rejected")
    assert code != 0 and result["code"] == "not_found" and "remove" in result["message"]
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_restage_refuses_a_non_terminal_or_unchanged_stage(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", ARCHIVED_OLDCO)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "OldCo",
                                  "--stage", "Interview")
    assert code != 0 and result["code"] == "invalid_stage"
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "OldCo",
                                  "--stage", "Withdrawn")
    assert code != 0 and result["code"] == "unchanged_stage"
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_restage_needs_a_role_when_two_archived_rows_match(tmp_path):
    text = (ARCHIVED_OLDCO
            + "| OldCo | Director | Withdrawn | 2026-01-02 | — | — | — | — |\n")
    write_fixture(tmp_path, "data/job-pipeline.md", text)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "OldCo",
                                  "--stage", "Rejected")
    assert code != 0 and result["code"] == "ambiguous_match"
    assert result["matches"] == [{"role": "PM"}, {"role": "Director"}]
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "OldCo",
                                  "--role", "director", "--stage", "Rejected")
    assert code == 0, result
    rows = _rows(tmp_path)
    assert rows[0].startswith("| OldCo | PM | Withdrawn |")
    assert rows[1].startswith("| OldCo | Director | Rejected | 2026-01-02 |")


def test_restage_dry_run_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", ARCHIVED_OLDCO)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "--dry-run", "restage", "OldCo",
                                  "--stage", "Accepted")
    assert code == 0 and result["dry_run"] is True
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_restage_refuses_a_row_with_a_stray_pipe(tmp_path):
    text = PIPELINE_MD + "| OldCo | PM | Withdrawn | 2026-01-01 | — | — | a | b | — |\n"
    write_fixture(tmp_path, "data/job-pipeline.md", text)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_pipe_write("--repo-root", str(tmp_path), "restage", "OldCo",
                                  "--stage", "Rejected")
    assert code != 0 and result["code"] == "malformed_row" and result["column_count"] == 9
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Applications are counted (2026-11-18): a move into Applied stamps "Applied <today>",
# which is the text tools/day_check.py counts. Before this, the documented
# `update <co> Applied` wrote no date and the application was never counted.
# ---------------------------------------------------------------------------

def _row(tmp_path, company, role=None):
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [[c.strip() for c in l.strip("|").split("|")] for l in content.splitlines()
            if l.startswith(f"| {company} |")]
    if role:
        rows = [r for r in rows if r[1] == role]
    assert len(rows) == 1
    return rows[0]


def _counted_today(cols, day=None):
    """The reader's own rule, so writer and reader are tested against each other. `day`
    is the date the writer stamped; it defaults to today."""
    sys.path.insert(0, str(TOOLS_DIR))
    import day_check
    from datetime import date as _d
    return day_check.applied_on(cols, _d.fromisoformat(day) if day else _d.today())


def test_update_into_applied_stamps_the_date_and_day_check_counts_it(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    days = _s6c_today_window()          # the child stamps its own date: either day at midnight
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied")
    days |= _s6c_today_window()
    assert code == 0 and result["applied_stamped"] is True
    cols = _row(tmp_path, "Acme Corp")
    assert cols[6] in {f"Applied {d}" for d in days}
    assert _counted_today(cols, cols[6].split()[-1])


def test_update_of_a_row_already_applied_stamps_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_TWO_ROLES)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "MultiCo", "Applied",
                                  "--role", "Director", "--next-action", "Follow up again")
    assert code == 0 and result["applied_stamped"] is False
    cols = _row(tmp_path, "MultiCo", "Director")
    assert cols[6] == "—"
    assert not _counted_today(cols)


def test_update_into_applied_keeps_an_existing_applied_date(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW.replace(
        "| Research role | — | — | — |", "| Research role | — | applied on 2026-01-03 | — |"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied")
    assert code == 0 and result["applied_stamped"] is False
    assert _row(tmp_path, "Acme Corp")[6] == "applied on 2026-01-03"


def test_update_into_applied_with_replaced_notes_still_stamps(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    days = _s6c_today_window()
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
                                  "--notes", "via Ashby")
    days |= _s6c_today_window()
    assert code == 0
    assert _row(tmp_path, "Acme Corp")[6] in {f"via Ashby Applied {d}" for d in days}


def test_update_to_another_stage_stamps_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested")
    assert code == 0 and result["applied_stamped"] is False
    assert _row(tmp_path, "Acme Corp")[6] == "—"


def test_add_with_stage_applied_stamps_and_default_add_does_not(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    days = _s6c_today_window()
    r1, _ = run_pipe_write("--repo-root", str(tmp_path), "add", "Zed Co", "Ops", "--stage", "Applied")
    days |= _s6c_today_window()
    r2, _ = run_pipe_write("--repo-root", str(tmp_path), "add", "Yel Co", "Ops")
    assert r1["applied_stamped"] is True and r2["applied_stamped"] is False
    zed = _row(tmp_path, "Zed Co")
    assert zed[6] in {f"Applied {d}" for d in days} and _counted_today(zed, zed[6].split()[-1])
    assert _row(tmp_path, "Yel Co")[6] == "—"


def test_add_with_stage_applied_keeps_a_date_already_in_notes(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    r, _ = run_pipe_write("--repo-root", str(tmp_path), "add", "Zed Co", "Ops", "--stage", "Applied",
                          "--notes", "Applied 2026-01-02 via referral")
    assert r["applied_stamped"] is False
    assert _row(tmp_path, "Zed Co")[6] == "Applied 2026-01-02 via referral"


def test_update_into_applied_with_an_earlier_date_appended_counts_that_day_not_today(tmp_path):
    """/apply Step 10: a submission made on an earlier day is logged with --notes-append."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
                                  "--notes-append", "Applied 2026-01-03")
    assert code == 0 and result["applied_stamped"] is False
    cols = _row(tmp_path, "Acme Corp")
    assert cols[6] == "Applied 2026-01-03"
    assert not _counted_today(cols)


def test_notes_replace_on_an_applied_row_keeps_its_applied_date(tmp_path):
    """Codex r1 F1: --notes on a row already Applied must not delete the date day_check counts."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_TWO_ROLES.replace(
        "| Follow up | — | — | — |", "| Follow up | — | Applied 2026-01-05 via form | — |"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "MultiCo", "Applied",
                                  "--role", "Director", "--notes", "recruiter replied")
    assert code == 0 and result["applied_stamped"] is False
    assert _row(tmp_path, "MultiCo", "Director")[6] == "recruiter replied Applied 2026-01-05"


def test_notes_replace_on_a_move_into_applied_keeps_the_old_date_and_does_not_restamp(tmp_path):
    """Codex r1 F2: an older applied date survives --notes, so the move is not counted today."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW.replace(
        "| Research role | — | — | — |", "| Research role | — | applied on 2026-01-03 | — |"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied",
                                  "--notes", "new prose")
    assert code == 0 and result["applied_stamped"] is False
    cols = _row(tmp_path, "Acme Corp")
    assert cols[6] == "new prose Applied 2026-01-03"
    assert not _counted_today(cols)


def test_notes_replace_that_restates_the_date_does_not_duplicate_it(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW.replace(
        "| Research role | — | — | — |", "| Research role | — | Applied 2026-01-03 | — |"))
    run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interested",
                   "--notes", "applied on 2026-01-03, then paused")
    assert _row(tmp_path, "Acme Corp")[6] == "applied on 2026-01-03, then paused"


def test_a_dated_applied_stage_set_to_bare_applied_keeps_its_date_and_is_not_restamped(tmp_path):
    """Grok r1 F1: Stage 'Applied <date> via ...' -> 'Applied' must not move the count to today."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW.replace(
        "| Acme Corp | Director | Researching |", "| Acme Corp | Director | Applied 2026-01-03 via form |"))
    result, code = run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Applied")
    assert code == 0 and result["applied_stamped"] is False
    cols = _row(tmp_path, "Acme Corp")
    assert cols[2] == "Applied" and cols[6] == "Applied 2026-01-03"
    assert not _counted_today(cols)


def test_a_date_in_a_replaced_next_action_moves_to_notes(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW.replace(
        "| Research role |", "| Applied 2026-01-03, chase in a week |"))
    run_pipe_write("--repo-root", str(tmp_path), "update", "Acme Corp", "Interviewing",
                   "--next-action", "Prep the screen")
    cols = _row(tmp_path, "Acme Corp")
    assert cols[4] == "Prep the screen" and cols[6] == "Applied 2026-01-03"


# ---------------------------------------------------------------------------
# --dry-run after the subcommand (friction row: "unrecognized arguments: --dry-run")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["update", "Acme Corp", "Applied", "--dry-run"],
    ["note", "Acme Corp", "--append", "a note", "--dry-run"],
    ["add", "Beta Co", "PM", "--dry-run"],
])
def test_dry_run_after_the_subcommand_writes_nothing(tmp_path, argv):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    code, stdout, stderr = _run_pipe_write_raw("--repo-root", str(tmp_path), *argv)
    d = json.loads(stdout)
    assert code == 0 and d["status"] == "ok" and d.get("dry_run") is True, d
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_dry_run_before_the_subcommand_still_works(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    code, stdout, _ = _run_pipe_write_raw("--repo-root", str(tmp_path), "--dry-run",
                                          "update", "Acme Corp", "Applied")
    assert code == 0 and json.loads(stdout).get("dry_run") is True
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_no_dry_run_anywhere_still_writes(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    code, stdout, _ = _run_pipe_write_raw("--repo-root", str(tmp_path),
                                          "note", "Acme Corp", "--append", "a note")
    assert code == 0 and not json.loads(stdout).get("dry_run")
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") != before


# ---------------------------------------------------------------------------
# S6c (2026-11-25): behaviors the whole-file mutation run showed no test observed
# ---------------------------------------------------------------------------

FULL_ROW_MD = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Director | Researching | 2026-01-01 | Call Jane | cv-acme.pdf | warm lead | https://acme.example/jobs/1 |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""


def _s6c_row(tmp_path, company):
    text = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    return [c.strip() for c in next(l for l in text.splitlines()
                                    if l.startswith(f"| {company} |")).strip().strip("|").split("|")]


def _s6c_today_window():
    """The local date, read on each side of a subprocess: the child stamps its own
    date.today(), so a run that crosses midnight may carry either day (Codex S6c r1 F1)."""
    from datetime import date as _d
    return {f"{_d.today():%Y-%m-%d}"}


def _s6c_pw(tmp_path, *args):
    return run_pipe_write("--repo-root", str(tmp_path), *args)


def test_update_keeps_every_cell_it_was_not_told_to_change(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", FULL_ROW_MD)
    out, code = _s6c_pw(tmp_path, "update", "Acme Corp", "Interviewing")
    assert code == 0, out
    cols = _s6c_row(tmp_path, "Acme Corp")
    assert cols[0:2] == ["Acme Corp", "Director"] and cols[2] == "Interviewing"
    assert cols[4:] == ["Call Jane", "cv-acme.pdf", "warm lead", "https://acme.example/jobs/1"]


def test_remove_archives_the_row_with_its_cells(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", FULL_ROW_MD)
    days = _s6c_today_window()
    out, code = _s6c_pw(tmp_path, "remove", "Acme Corp")
    days |= _s6c_today_window()
    assert code == 0, out
    cols = _s6c_row(tmp_path, "Acme Corp")
    assert cols[:4] == ["Acme Corp", "Director", "Withdrawn", "2026-01-01"]
    assert cols[4:6] == ["Call Jane", "cv-acme.pdf"]
    assert cols[6] in {f"warm lead - Withdrawn {d}" for d in days}
    assert cols[7] == "https://acme.example/jobs/1"
    text = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert text.count("## Archived") == 1, "the row joins the existing Archived table"
    assert text.index("| Acme Corp |") > text.index("## Archived")


def test_remove_with_empty_notes_writes_only_the_stage(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_WITH_ROW)
    days = _s6c_today_window()
    _s6c_pw(tmp_path, "remove", "Acme Corp")
    days |= _s6c_today_window()
    assert _s6c_row(tmp_path, "Acme Corp")[6] in {f"Withdrawn {d}" for d in days}


def test_remove_creates_the_archived_section_when_missing(tmp_path):
    md = FULL_ROW_MD[:FULL_ROW_MD.index("## Archived")].rstrip("\n") + "\n"
    write_fixture(tmp_path, "data/job-pipeline.md", md)
    out, code = _s6c_pw(tmp_path, "remove", "Acme Corp")
    assert code == 0, out
    lines = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
    i = lines.index("## Archived")
    assert lines[i - 1] == "" and lines[i + 1] == ""
    assert lines[i + 2].startswith("| Company | Role |") and lines[i + 3].startswith("| --- |")
    assert lines[i + 4].startswith("| Acme Corp | Director | Withdrawn |")


@pytest.mark.parametrize("argv", [["add", "Beta Co", "PM"], ["update", "Acme Corp", "Interviewing"]])
def test_a_pipeline_without_an_active_section_is_refused(tmp_path, argv):
    md = FULL_ROW_MD.replace("## Active", "## Current")
    write_fixture(tmp_path, "data/job-pipeline.md", md)
    out, code = _s6c_pw(tmp_path, *argv)
    assert code != 0 and out["code"] == "missing_section"
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == md


def test_the_header_row_is_not_a_pipeline_entry(tmp_path):
    """A company named like the header cell is not a duplicate of the header, and is not
    found by update: only data rows count."""
    write_fixture(tmp_path, "data/job-pipeline.md", FULL_ROW_MD)
    out, code = _s6c_pw(tmp_path, "update", "Company", "Interviewing")
    assert code != 0 and out["code"] == "not_found"
    out, code = _s6c_pw(tmp_path, "add", "Company", "Role")
    assert code == 0 and out["action"] == "add", out          # not duplicate_warning
    text = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    active = text[text.index("## Active"):text.index("## Archived")]
    assert sum(l.startswith("| Company | Role |") for l in active.splitlines()) == 2  # header + row


@pytest.mark.parametrize("trailing", [True, False])
def test_a_write_keeps_the_files_trailing_newline_state(tmp_path, trailing):
    write_fixture(tmp_path, "data/job-pipeline.md",
                  FULL_ROW_MD if trailing else FULL_ROW_MD.rstrip("\n"))
    _s6c_pw(tmp_path, "update", "Acme Corp", "Interviewing")
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").endswith("\n") is trailing


def test_fit_reason_on_empty_notes_is_the_tag_alone(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    out, code = _s6c_pw(tmp_path, "add", "Delta", "PM", "--fit-reason", "strong ops match")
    assert code == 0, out
    notes = _s6c_row(tmp_path, "Delta")[6]
    assert notes.startswith("[fit-reason ") and notes.endswith(": strong ops match]")


def test_a_write_adds_the_company_to_the_company_dimension(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    write_fixture(tmp_path, "data/companies.yaml",
                  "schema: 1\ncompanies:\n- canonical: Acme Corp\n  type: target\n")
    _s6c_pw(tmp_path, "add", "Zeta Labs", "PM")
    dim = tmp_path / "data" / "companies.yaml"
    assert dim.is_file() and "Zeta Labs" in dim.read_text(encoding="utf-8")


def test_a_missing_company_dimension_module_warns_and_keeps_the_write(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import pipe_write as pw
    finally:
        sys.path.remove(str(TOOLS_DIR))
    monkeypatch.setitem(sys.modules, "company_dim", None)     # import raises ImportError
    p = tmp_path / "f.md"
    pw.save_lines(p, ["a", "b"], "x\n")
    assert p.read_text(encoding="utf-8") == "a\nb\n"
    assert "WARNING: company dimension not updated" in capsys.readouterr().err


def test_no_subcommand_is_a_usage_error_here_too(tmp_path):
    out, code = _s6c_pw(tmp_path)
    assert code != 0 and out["message"].startswith("Usage:")


def test_pipe_write_imports_as_a_package_module():
    r = subprocess.run([sys.executable, "-c", "import tools.pipe_write"], capture_output=True,
                       text=True, cwd=str(REPO_ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0 and r.stdout == "", r.stderr
