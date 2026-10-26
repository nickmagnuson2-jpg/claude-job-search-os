"""Tests for tools/pipeline_staleness.py

Schema note: the canonical job-pipeline.md schema (written by tools/pipe_write.py) is
    | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
The date used for staleness is the "Date Updated" column (index 3). An earlier
version of these fixtures encoded an obsolete "Date Added | Date Updated | CV Used |
URL | Notes" layout that the live file never used, which masked a column-index bug
in the parser (it read the date from "Next Action"). Fixtures below match the live schema.
"""
import json
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
        | Lumen | Product Ops | Case round complete (3/14); awaiting next-step | 2026-02-10 | — | — | — | — |
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


def test_descriptive_closed_stage_not_active(tmp_path):
    """A freeform closed stage must be excluded from active_entries (parity with
    pipe_read), even when it contains active-pursuit keywords. fable-audit Theme 2."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | LiveCo | PM | Applied | 2026-01-01 | Follow up | — | — | — |
        | ClosedCo | PM | Closed - rejected after final round | 2026-01-01 | — | — | — | — |
    """)
    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    active = {e["company"] for e in result["active_entries"]}
    assert active == {"LiveCo"}, active
    assert result["metrics"]["total_active"] == 1


def test_separator_row_not_counted_active(tmp_path):
    """A '| --- | --- | ... |' separator row must not be parsed as a company='---'
    active entry (it inflated total_active by 1 vs pipe_read). fable-audit Theme 2."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        | --- | --- | --- | --- | --- | --- | --- | --- |
        | LiveCo | PM | Applied | 2026-01-01 | Follow up | — | — | — |
    """)
    result = run_script("pipeline_staleness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    companies = {e["company"] for e in result["active_entries"]}
    assert "---" not in companies
    assert result["metrics"]["total_active"] == 1


# ---------------------------------------------------------------------------
# Added 2026-10-24 to kill mutation survivors (11 of 52 lived): the heading rule
# was only ever exercised with an already-terminal stage, no fixture had a row to
# skip, Role was never read, the stalled list never had two rows to order, and the
# schema-drift exit code was unobserved.
# ---------------------------------------------------------------------------
from conftest import run_script_raw

HEADER = """\
| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
|---------|------|-------|--------------|-------------|---------|-------|-----|
"""


@pytest.mark.parametrize("heading", ["## Archived", "## Terminal", "## Withdrawn", "## Rejected"])
def test_a_live_stage_under_a_closed_heading_is_not_active(tmp_path, heading):
    """The heading alone excludes the row: its stage ("Applied") is live."""
    write_fixture(tmp_path, "data/job-pipeline.md", "# Job Pipeline\n\n## Active\n" + HEADER
                  + "| Live | PM | Applied | 2026-02-20 | — | — | — | — |\n\n"
                  + f"{heading}\n" + HEADER
                  + "| Old | PM | Applied | 2026-02-20 | — | — | — | — |\n")
    result = run_script("pipeline_staleness.py", "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert [e["company"] for e in result["active_entries"]] == ["Live"]


def test_blank_company_and_short_rows_are_skipped_and_role_is_read(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", "# Job Pipeline\n\n## Active\n" + HEADER
                  + "| Acme | Product Ops | Researching | 2026-02-25 | — | — | — | — |\n"
                  + "|  | PM | Applied | 2026-02-20 | — | — | — | — |\n"
                  + "| Short | PM |\n")
    result = run_script("pipeline_staleness.py", "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert [(e["company"], e["role"]) for e in result["active_entries"]] == [("Acme", "Product Ops")]


def test_stalled_entries_are_ordered_most_overdue_first(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", "# Job Pipeline\n\n## Active\n" + HEADER
                  + "| Less | PM | Applied | 2026-02-16 | — | — | — | — |\n"
                  + "| Most | PM | Applied | 2026-01-01 | — | — | — | — |\n"
                  + "| Mid | PM | Applied | 2026-02-01 | — | — | — | — |\n")
    result = run_script("pipeline_staleness.py", "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert [e["company"] for e in result["stalled_entries"]] == ["Most", "Mid", "Less"]


def test_schema_drift_exits_1_with_an_error_json(tmp_path):
    """A header whose columns moved must stop the run, not be parsed by position."""
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Pipeline

        ## Active
        | Company | Stage | Role | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|-------|------|--------------|-------------|---------|-------|-----|
        | Acme | Applied | PM | 2026-02-16 | — | — | — | — |
    """)
    proc = run_script_raw("pipeline_staleness.py", "--target-date", "2026-02-26",
                          "--repo-root", str(tmp_path))
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["status"] == "error" and out["code"] == "schema_drift"
    assert "active_entries" not in out
    assert proc.stderr == "", "a clean refusal, not a crash after the error JSON"


@pytest.mark.parametrize("bad_row", [
    "|Company|Role|Stage|Date Updated|Next Action|CV Used|Notes|URL|",   # header, no spaces
    "|  Company | Role | Stage | x | x | x | x | x |",                    # header, two spaces
    "| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |",           # four-dash separator
    "| ---Co | PM | Applied | 2026-02-20 | — | — | — | — |",               # first cell starts with ---
])
def test_header_and_separator_variants_are_not_active_rows(tmp_path, bad_row):
    """BEHAVIOR CHANGE 2026-10-24 (rows now come from pipe_read.parse_all_rows): each of
    these used to be counted as an active company ("Company", "----", "---Co")."""
    write_fixture(tmp_path, "data/job-pipeline.md", "# Job Pipeline\n\n## Active\n" + HEADER
                  + bad_row + "\n| Live | PM | Applied | 2026-02-20 | — | — | — | — |\n")
    result = run_script("pipeline_staleness.py", "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert [e["company"] for e in result["active_entries"]] == ["Live"]
