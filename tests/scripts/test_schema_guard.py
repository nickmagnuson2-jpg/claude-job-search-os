"""Regression suite for tools/schema_guard.py, the shared column-schema guard.

WHY THIS FILE EXISTS AT THIS PATH. Coverage for this module already existed as
`tools/test_schema_guard.py` -- a standalone assert script with its own PASS/FAIL counters
and a `sys.exit()`. pytest cannot collect it (0 test functions; invoking it directly gives
`INTERNALERROR> SystemExit: 0`), the repo suite only collects `tests/`, and
`mutation_check.map_tests` globs `tests/scripts/test_<stem>*.py`. So the tests ran only when
someone remembered to run them by hand, and the module reported 18 of 26 mutants surviving
against test files that merely mention it. The cases below are ported from that script and
extended; the original is left in place until its callers are checked.

WHAT IT GUARDS (2026-06-08 incident). `pipeline_staleness.py` read the staleness date from
cols[4] (Next Action prose) instead of cols[3] (Date Updated) after the live pipeline header
drifted. Fixtures encoded the same stale header the parser assumed, so the suite stayed
green while `days_since_update` returned null for every real row and /standup silently
under-surfaced stalled applications.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from schema_guard import (  # noqa: E402
    SchemaDriftError,
    assert_schema,
    find_header_line,
)

PIPELINE_COLUMNS = ["Company", "Role", "Stage", "Date Updated",
                    "Next Action", "CV Used", "Notes", "URL"]
CANONICAL_HEADER = (
    "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |")
# The exact stale header from the 2026-06-08 incident.
DRIFTED_HEADER = (
    "| Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |")


# --- assert_schema: the passing paths ---------------------------------------

def test_matching_schema_passes_silently():
    assert assert_schema(CANONICAL_HEADER, PIPELINE_COLUMNS) is None


def test_none_header_passes_silently():
    """A freshly-scaffolded file has no header yet; that is not drift."""
    assert assert_schema(None, PIPELINE_COLUMNS) is None


def test_header_without_outer_pipes_still_matches():
    assert assert_schema("Company | Role | Stage", ["Company", "Role", "Stage"]) is None


def test_irregular_inner_whitespace_is_normalised():
    assert assert_schema("|Company|   Role   |Stage|",
                         ["Company", "Role", "Stage"]) is None


def test_trailing_whitespace_on_the_line_is_tolerated():
    assert assert_schema("  | Company | Role |  \n", ["Company", "Role"]) is None


# --- assert_schema: the raising paths ---------------------------------------

def test_the_real_incident_header_raises():
    with pytest.raises(SchemaDriftError) as e:
        assert_schema(DRIFTED_HEADER, PIPELINE_COLUMNS)
    msg = str(e.value)
    assert "Next Action" in msg, "must name the missing column"
    assert "Date Added" in msg, "must name the unexpected column"


def test_missing_column_alone_raises_and_names_it():
    with pytest.raises(SchemaDriftError) as e:
        assert_schema("| Company | Role |", ["Company", "Role", "Stage"])
    msg = str(e.value)
    assert "missing" in msg.lower()
    assert "Stage" in msg
    # Nothing was added, so the message must not also claim an unexpected column. Without
    # this, forcing `if unexpected:` true is invisible and the guard can report phantom
    # findings ("unexpected new column(s) []") while every test stays green.
    assert "unexpected" not in msg.lower()


def test_unexpected_column_alone_raises_and_names_it():
    with pytest.raises(SchemaDriftError) as e:
        assert_schema("| Company | Role | Owner |", ["Company", "Role"])
    msg = str(e.value)
    assert "unexpected" in msg.lower()
    assert "Owner" in msg
    # Mirror of the above: nothing is absent, so no phantom "missing column(s) []".
    assert "missing" not in msg.lower()


def test_reorder_is_reported_as_a_reorder_not_as_missing():
    """The silent-misparse case: same names, different positions, cols[N] shifts."""
    with pytest.raises(SchemaDriftError) as e:
        assert_schema("| Role | Company | Stage |", ["Company", "Role", "Stage"])
    msg = str(e.value).lower()
    assert "reorder" in msg
    assert "missing" not in msg, "a pure reorder must not be reported as a missing column"
    assert "unexpected" not in msg


def test_reorder_message_shows_both_orders():
    with pytest.raises(SchemaDriftError) as e:
        assert_schema("| Role | Company |", ["Company", "Role"])
    assert "['Company', 'Role']" in str(e.value)
    assert "['Role', 'Company']" in str(e.value)


def test_a_duplicated_column_is_caught_even_though_the_name_sets_match():
    """set() equality would call this a match; only the ordered compare catches it."""
    with pytest.raises(SchemaDriftError):
        assert_schema("| Company | Company |", ["Company", "Role"])


def test_case_difference_is_drift():
    with pytest.raises(SchemaDriftError):
        assert_schema("| company | Role |", ["Company", "Role"])


def test_empty_expected_against_a_real_header_raises():
    with pytest.raises(SchemaDriftError):
        assert_schema("| Company |", [])


def test_error_is_specifically_schema_drift_error():
    """Callers catch SchemaDriftError by name; a bare Exception would slip past them."""
    with pytest.raises(SchemaDriftError):
        assert_schema(DRIFTED_HEADER, PIPELINE_COLUMNS)


# --- find_header_line -------------------------------------------------------

def test_find_header_line_returns_the_matching_row():
    content = ("# Pipeline\n\n| Company | Role | Stage |\n|---|---|---|\n"
               "| Acme | PM | Applied |\n")
    assert find_header_line(content, "| Company |") == "| Company | Role | Stage |"


def test_find_header_line_returns_none_when_absent():
    assert find_header_line("no tables here\n", "| Company |") is None


def test_find_header_line_returns_the_first_match_not_a_later_one():
    content = "| Company | Role |\n\n| Company | Role | Stage |\n"
    assert find_header_line(content, "| Company |") == "| Company | Role |"


def test_find_header_line_ignores_leading_whitespace():
    assert find_header_line("   | Company | Role |\n", "| Company |") is not None


def test_find_header_line_does_not_match_mid_line_occurrences():
    assert find_header_line("see the | Company | table\n", "| Company |") is None


def test_find_header_line_on_empty_content():
    assert find_header_line("", "| Company |") is None


# --- integration: the wired parsers actually fire the guard -----------------

def _make_repo(pipeline_header: str) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "data" / "job-pipeline.md").write_text(
        "# Job Application Pipeline\n\n"
        f"{pipeline_header}\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Acme | PM | Applied | 2026-06-01 | Follow up | resume.pdf | notes | "
        "https://example.com |\n",
        encoding="utf-8")
    (root / "data" / "job-todos.md").write_text(
        "# Job Search To-Dos\n\n## Active\n\n"
        "| Task | Priority | Due | Status | Notes |\n| --- | --- | --- | --- | --- |\n"
        "| Example task | Med | 2026-06-05 | Pending | - |\n\n"
        "## Completed\n\n| Task | Priority | Completed | Notes |\n"
        "| --- | --- | --- | --- |\n",
        encoding="utf-8")
    return root


def _run(script_name: str, root: Path):
    out = subprocess.run(
        [sys.executable, str(TOOLS / script_name), "--repo-root", str(root)],
        capture_output=True, text=True, encoding="utf-8")
    return out.returncode, json.loads(out.stdout)


@pytest.mark.parametrize("script", ["pipeline_staleness.py", "todo_daily_metrics.py"])
def test_canonical_schema_parses_cleanly(script):
    rc, out = _run(script, _make_repo(CANONICAL_HEADER))
    assert rc == 0
    assert out.get("status") != "error"


@pytest.mark.parametrize("script", ["pipeline_staleness.py", "todo_daily_metrics.py"])
def test_drifted_schema_surfaces_as_schema_drift_not_a_silent_misparse(script):
    """The point of the guard: a loud typed error, never a null that reads as 'no data'."""
    rc, out = _run(script, _make_repo(DRIFTED_HEADER))
    assert rc != 0, "drift must not exit 0 -- a green exit is what caused the incident"
    assert out.get("code") == "schema_drift"


# --- the assertion that has to fail first -----------------------------------

def test_the_module_under_test_is_the_real_one():
    """Guards against this suite passing while importing something else entirely."""
    import schema_guard
    assert Path(schema_guard.__file__).resolve() == (TOOLS / "schema_guard.py").resolve()
    assert callable(assert_schema) and callable(find_header_line)
