"""
Tests for tools/check_table_integrity.py.

Origin: 2026-08-31. A pipeline row carried 11 pipe-delimited fields where every other row
carried 10, because a writer interpolated a literal " | " into a Notes cell. The row
rendered fine, every parser returned a plausible wrong value, and nothing downstream
complained. It was found by a hand-run integrity check during unrelated verification.

The suite is written so that a mutant which weakens detection kills a test, not so that
it confirms the implementation agrees with itself. Every "detects" test asserts the exact
(line, actual, expected) triple, so a mutant that reports the wrong line or silently
widens the tolerance still fails.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from check_table_integrity import (  # noqa: E402
    GUARDED_FILES,
    MIN_TABLE_ROWS,
    field_count,
    find_malformed_rows,
    scan_files,
)

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_table_integrity.py"


def table(*rows: str) -> str:
    return "\n".join(rows)


HEADER = "| Company | Role | Stage | Notes | URL |"
SEP = "| --- | --- | --- | --- | --- |"
GOOD = "| ActiveCo | Chief of Staff | Researching | fine | https://x |"


# --------------------------------------------------------------------------
# field_count
# --------------------------------------------------------------------------

def test_field_count_five_column_row_is_seven_fields():
    # Leading and trailing empties are real: "|a|".split("|") == ["", "a", ""].
    assert field_count("| a | b | c | d | e |") == 7


def test_field_count_ignores_escaped_pipes():
    assert field_count(r"| a | b \| c | d |") == field_count("| a | bc | d |")


def test_field_count_counts_unescaped_pipe_as_separator():
    assert field_count("| a | b | c |") == 5
    assert field_count("| a | b | c | d |") == 6


# --------------------------------------------------------------------------
# find_malformed_rows — detection
# --------------------------------------------------------------------------

def test_detects_the_origin_defect_stray_pipe_in_notes_cell():
    """The exact 2026-08-31 shape: a provenance fragment split off by a literal pipe."""
    text = table(
        HEADER,
        SEP,
        GOOD,
        "| LiveCo | Ops Lead | Researching | note | Added from inbox/x.md | - |",
        "| Acme | Analyst | Applied | fine | https://y |",
    )
    assert find_malformed_rows(text) == [(4, 8, 7)]


def test_reports_one_indexed_line_numbers():
    """Off-by-one here would send a reader to the wrong row."""
    text = table("prose line", HEADER, SEP, GOOD, "| A | B | C | D | E | F |")
    (line_no, _, _), = find_malformed_rows(text)
    assert line_no == 5


def test_detects_a_row_with_too_FEW_fields():
    """A dropped cell is the mirror defect and must also be caught."""
    text = table(HEADER, SEP, GOOD, "| Acme | Analyst | Applied |")
    assert find_malformed_rows(text) == [(4, 5, 7)]


def test_detects_multiple_bad_rows_in_one_table():
    text = table(
        HEADER, SEP, GOOD,
        "| A | B | C | D | E | F |",
        GOOD,
        "| G | H |",
    )
    assert find_malformed_rows(text) == [(4, 8, 7), (6, 4, 7)]


def test_detects_bad_row_in_the_second_of_two_tables():
    """Runs are independent; a clean first table must not mask a dirty second one."""
    text = table(
        HEADER, SEP, GOOD,
        "",
        "| Name | Email |",
        "| --- | --- |",
        "| Casey Doe | casey@example.com |",
        "| Jordan Sample | jordan@example.com | stray |",
    )
    assert find_malformed_rows(text) == [(8, 5, 4)]


def test_detects_bad_row_in_a_table_that_is_FOLLOWED_by_more_content():
    """
    The real-world layout, and the one every other test here missed: job-pipeline.md is
    an Active table, then a '## Archived' heading, then a second table. A malformed row
    in the FIRST table is flushed by the end-of-run branch, not by the end-of-input
    flush. Mutation 2026-08-31: dropping the in-loop flush() survived 28 tests because
    all of them put the bad row at EOF.
    """
    text = table(
        HEADER, SEP, GOOD,
        "| LiveCo | Ops | Researching | note | stray | - |",
        "",
        "## Archived",
        "",
        HEADER, SEP, GOOD,
    )
    assert find_malformed_rows(text) == [(4, 8, 7)]


def test_majority_defines_arity_not_first_row():
    """A malformed FIRST row must be flagged, not adopted as the schema."""
    text = table(
        "| A | B | C | D | E | F |",
        SEP,
        GOOD,
        GOOD,
        GOOD,
    )
    assert find_malformed_rows(text) == [(1, 8, 7)]


# --------------------------------------------------------------------------
# find_malformed_rows — clean cases (false-positive surface)
# --------------------------------------------------------------------------

def test_clean_table_yields_nothing():
    assert find_malformed_rows(table(HEADER, SEP, GOOD, GOOD)) == []


def test_escaped_pipe_in_a_cell_is_not_a_violation():
    text = table(HEADER, SEP, GOOD, r"| Acme | Ops \| Strategy | Applied | fine | https://y |")
    assert find_malformed_rows(text) == []


def test_short_run_below_minimum_is_prose_not_a_table():
    """A stray '|' line in prose must not be judged against a phantom schema."""
    text = "\n".join(["| this is not a table", "| nor is this"])
    assert len(text.split("\n")) < MIN_TABLE_ROWS
    assert find_malformed_rows(text) == []


def test_run_of_exactly_minimum_length_IS_judged():
    """Boundary: MIN_TABLE_ROWS rows is a table. A mutant loosening this stops detecting."""
    text = table(HEADER, SEP, "| A | B | C | D | E | F |")
    assert len(text.split("\n")) == MIN_TABLE_ROWS
    assert find_malformed_rows(text) == [(3, 8, 7)]


def test_blockquoted_pipe_line_is_ignored():
    """networking.md quotes email bodies; a quoted line starts with '>', not '|'."""
    text = table(HEADER, SEP, GOOD, "> | quoted | content |", GOOD)
    assert find_malformed_rows(text) == []


def test_empty_and_whitespace_input():
    assert find_malformed_rows("") == []
    assert find_malformed_rows("\n\n\n") == []


# --------------------------------------------------------------------------
# scan_files
# --------------------------------------------------------------------------

def test_scan_files_skips_missing_files(tmp_path):
    assert scan_files(tmp_path) == {}


def test_scan_files_reports_the_relative_path_it_found(tmp_path):
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    results = scan_files(tmp_path)
    assert list(results) == [GUARDED_FILES[0]]
    assert results[GUARDED_FILES[0]] == [(4, 4, 7)]


def test_scan_files_covers_every_guarded_file(tmp_path):
    """Coverage, not self-consistency: each declared file must actually be read."""
    for rel in GUARDED_FILES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    assert set(scan_files(tmp_path)) == set(GUARDED_FILES)


# --------------------------------------------------------------------------
# CLI / hook surface — the shipped entry point, not just the helper
# --------------------------------------------------------------------------

def _run(args, stdin="", cwd=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        input=stdin, capture_output=True, text=True, cwd=cwd,
    )


def test_scan_mode_exits_1_and_names_the_line_on_a_bad_file(tmp_path):
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    r = _run(["--scan", "--repo-root", str(tmp_path)])
    assert r.returncode == 1
    assert "line 4" in r.stdout
    assert GUARDED_FILES[0] in r.stdout


def test_scan_mode_exits_0_when_clean(tmp_path):
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD), encoding="utf-8")
    r = _run(["--scan", "--repo-root", str(tmp_path)])
    assert r.returncode == 0
    assert "OK" in r.stdout


def test_hook_mode_exits_2_and_writes_to_stderr(tmp_path):
    """Exit 2 + stderr is the only PostToolUse channel that reaches the model."""
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    r = _run([], stdin=f'{{"cwd": "{tmp_path}"}}')
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr
    assert "line 4" in r.stderr


def test_hook_mode_exits_0_when_clean(tmp_path):
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD), encoding="utf-8")
    r = _run([], stdin=f'{{"cwd": "{tmp_path}"}}')
    assert r.returncode == 0


def test_scan_mode_without_repo_root_defaults_to_cwd(tmp_path):
    """
    --repo-root is optional; without it the scan must run against cwd rather than
    blowing up. Mutation 2026-08-31: forcing the '--repo-root in argv' branch always-true
    raises ValueError on .index(), which the fail-open swallows into a silent exit 0 --
    a guard that scans nothing while reporting success.
    """
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    r = _run(["--scan"], cwd=str(tmp_path))
    assert r.returncode == 1, f"expected a finding from cwd, got {r.returncode}: {r.stdout}{r.stderr}"
    assert "line 4" in r.stdout


def test_scan_mode_does_not_fall_through_into_hook_mode(tmp_path):
    """
    A clean --scan must EXIT, not continue into the stdin-reading hook path. Mutation
    2026-08-31: dropping that sys.exit(0) survived, because in the harness stdin was
    empty and the fail-open produced the same exit code. Here stdin carries a payload
    pointing at a DIRTY repo, so a fall-through would scan it and exit 2 -- and in real
    terminal use the same mutant would hang waiting on stdin.
    """
    clean = tmp_path / "clean"
    dirty = tmp_path / "dirty"
    for root, body in ((clean, table(HEADER, SEP, GOOD)),
                       (dirty, table(HEADER, SEP, GOOD, "| A | B |"))):
        p = root / GUARDED_FILES[0]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    r = _run(["--scan", "--repo-root", str(clean)], stdin=f'{{"cwd": "{dirty}"}}')
    assert r.returncode == 0, "clean --scan must exit 0 and never read stdin"
    assert "OK" in r.stdout
    assert "BLOCKED" not in r.stderr


@pytest.mark.parametrize("payload", ["", "   ", "not json", '{"cwd": 12345}'])
def test_hook_fails_open_on_garbage_payload(payload):
    """A guard that crashes the workflow is worse than the corruption it hunts."""
    assert _run([], stdin=payload).returncode == 0


def test_report_names_the_sanitize_helper_so_the_fix_is_actionable(tmp_path):
    target = tmp_path / GUARDED_FILES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table(HEADER, SEP, GOOD, "| A | B |"), encoding="utf-8")
    out = _run(["--scan", "--repo-root", str(tmp_path)]).stdout
    assert "sanitize_cell" in out
    assert "pipe_write.py" in out


# --------------------------------------------------------------------------
# Live data — the rule that matters is that the guard is quiet on the real files
# --------------------------------------------------------------------------

def test_live_data_files_are_clean():
    """
    Verified on REAL data, not fixtures. If this fails, the repo has a genuinely
    malformed row and the fix is the data, not the test.
    """
    repo = Path(__file__).resolve().parents[2]
    results = scan_files(repo)
    assert results == {}, f"malformed rows in live data: {results}"
