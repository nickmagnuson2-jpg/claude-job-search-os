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


# ---------------------------------------------------------------------------
# Direct unit tests of the parsers (added 2026-10-24 to kill mutation survivors).
# The CLI tests above never fed a row the parser should SKIP, never checked any
# column past Stage, and never had two rows to sort, so 28 of 83 mutants lived.
# ---------------------------------------------------------------------------
import sys
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import pipe_read  # noqa: E402

TODAY = date(2026, 2, 28)
HEADER = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
          "|---|---|---|---|---|---|---|---|\n")


def _doc(*rows, section="## Active"):
    return "# Job Pipeline\n\n" + section + "\n" + HEADER + "".join(r + "\n" for r in rows)


FULL_ROW = "| Acme | PM | Applied | 2026-02-26 | Follow up | cv-a.pdf | warm intro | https://example.com/a |"


@pytest.mark.parametrize("parse", ["all_rows", "pipeline"])
def test_rows_the_parser_must_skip(parse):
    """Prose that contains pipes, a row with a blank Company cell, and a two-column
    row are not pipeline entries."""
    content = _doc(
        FULL_ROW,
        "Prose mentioning a | b | c is not a row",
        "|  | PM | Applied | 2026-02-26 | x | — | — | — |",
        "| Short | PM |",
    )
    if parse == "all_rows":
        rows = pipe_read.parse_all_rows(content, TODAY)
    else:
        rows = pipe_read.parse_pipeline(content, TODAY)["active_entries"]
    assert [r["company"] for r in rows] == ["Acme"]


def test_parse_all_rows_reads_every_column():
    (row,) = pipe_read.parse_all_rows(_doc(FULL_ROW), TODAY)
    assert {k: row[k] for k in ("company", "role", "stage", "date_updated", "next_action",
                                "cv_used", "notes", "url", "days_since_update")} == {
        "company": "Acme", "role": "PM", "stage": "Applied", "date_updated": "2026-02-26",
        "next_action": "Follow up", "cv_used": "cv-a.pdf", "notes": "warm intro",
        "url": "https://example.com/a", "days_since_update": 2}


def test_parse_pipeline_reads_every_column_and_leaves_fresh_rows_unlabelled():
    (e,) = pipe_read.parse_pipeline(_doc(FULL_ROW), TODAY)["active_entries"]
    assert (e["role"], e["next_action"], e["cv_used"], e["notes"], e["url"]) == (
        "PM", "Follow up", "cv-a.pdf", "warm intro", "https://example.com/a")
    assert e["stale"] is False
    assert e["stale_label"] == ""


def test_non_terminal_stage_under_an_archived_heading_is_archived():
    """The heading alone archives a row; the stage here is live ("Applied")."""
    content = _doc("| Live | PM | Applied | 2026-02-26 | x | — | — | — |") + (
        "\n## Archived\n" + HEADER + "| Old | PM | Applied | 2026-02-26 | x | — | — | — |\n")
    result = pipe_read.parse_pipeline(content, TODAY)
    assert [e["company"] for e in result["active_entries"]] == ["Live"]
    assert result["metrics"]["archived_count"] == 1
    by_co = {r["company"]: r["archived"] for r in pipe_read.parse_all_rows(content, TODAY)}
    assert by_co == {"Live": False, "Old": True}


def test_active_entries_sort_newest_first_with_undated_rows_last():
    content = _doc(
        "| Older | PM | Researching | 2026-02-01 | x | — | — | — |",
        "| Undated | PM | Researching | — | x | — | — | — |",
        "| Newer | PM | Researching | 2026-02-27 | x | — | — | — |",
    )
    result = pipe_read.parse_pipeline(content, TODAY)
    assert [e["company"] for e in result["active_entries"]] == ["Newer", "Older", "Undated"]


def test_company_index_groups_roles_case_insensitively_without_blanks_or_repeats():
    content = _doc(
        "| Acme | PM | Researching | 2026-02-27 | x | — | — | — |",
        "| ACME | CoS | Researching | 2026-02-26 | x | — | — | — |",
        "| acme | PM | Researching | 2026-02-25 | x | — | — | — |",
        "| Acme |  | Researching | 2026-02-24 | x | — | — | — |",
        "| Beta | Ops | Researching | 2026-02-23 | x | — | — | — |",
    )
    assert pipe_read.parse_pipeline(content, TODAY)["company_index"] == {
        "acme": ["PM", "CoS"], "beta": ["Ops"]}


def test_parse_all_rows_flags_stale_only_past_the_threshold_and_with_a_date():
    """Applied's threshold is 5 days: 5 is stale, 4 is not, an undated row is not."""
    content = _doc(
        "| AtFive | PM | Applied | 2026-02-23 | x | — | — | — |",
        "| AtFour | PM | Applied | 2026-02-24 | x | — | — | — |",
        "| NoDate | PM | Applied | — | x | — | — | — |",
    )
    stale = {r["company"]: r["stale"] for r in pipe_read.parse_all_rows(content, TODAY)}
    assert stale == {"AtFive": True, "AtFour": False, "NoDate": False}
