"""Tests for tools/mutation_state.py: where mutation state lives and how it is read.

This module had no test file of its own until 2026-10-06. It was measured through tests
written for the sweep, the report and the watchdog, and 18 of its 33 mutants survived.
Thirteen of those were in code nothing needed (a fallback to a directory that no longer
exists, a `base` argument nobody passed, two functions nobody called) and were deleted.
What is tested here is what is left.
"""
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "mutation_state.py"


@pytest.fixture
def ms(tmp_path, monkeypatch):
    """A fresh module object with the store pointed into tmp_path."""
    spec = importlib.util.spec_from_file_location("mutation_state_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path / "output" / "mutation-state")
    return mod


def test_the_store_and_each_file_in_it_are_named_once(ms):
    """/standup, the sweep, the report, the trend recorder and job_quiesce all resolve
    these here; a helper that returned nothing would send each of them somewhere else."""
    assert ms.state_dir() == ms.STATE_DIR
    assert ms.baseline_path() == ms.STATE_DIR / "baseline.jsonl"
    assert ms.trend_path() == ms.STATE_DIR / "survival-trend.jsonl"
    assert ms.quiesce_marker_path() == ms.STATE_DIR / ".quiesced-jobs.json"


def test_the_real_store_is_inside_the_repo_and_not_date_prefixed():
    """Real data, not a fixture: a dated name is what made every session read the
    permanent store as an old one-off and start its own."""
    spec = importlib.util.spec_from_file_location("mutation_state_real", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STATE_DIR == TOOL.parents[1] / "output" / "mutation-state"


# --- reading rows -----------------------------------------------------------------

def test_reading_a_baseline_that_does_not_exist_is_an_empty_list(ms, tmp_path):
    assert ms.read_rows(tmp_path / "absent.jsonl") == []
    assert ms.read_rows() == []


def test_rows_come_back_in_file_order_with_blank_lines_skipped(ms, tmp_path):
    f = tmp_path / "b.jsonl"
    f.write_text('{"tool": "a"}\n\n   \n{"tool": "b"}\n', encoding="utf-8")
    assert ms.read_rows(f) == [{"tool": "a"}, {"tool": "b"}]


def test_reading_with_no_argument_reads_the_store(ms):
    ms.STATE_DIR.mkdir(parents=True)
    (ms.STATE_DIR / "baseline.jsonl").write_text(json.dumps({"tool": "a"}) + "\n",
                                                 encoding="utf-8")
    assert ms.read_rows() == [{"tool": "a"}]


def test_the_last_row_speaks_for_a_tool_and_a_row_naming_no_tool_is_dropped(ms):
    rows = [{"tool": "tools/a.py", "status": "error"}, {"status": "ok"},
            {"tool": "", "status": "ok"}, {"tool": "tools/b.py", "status": "ok"},
            {"tool": "tools/a.py", "status": "ok"}]
    assert ms.latest_per_tool(rows) == [{"tool": "tools/a.py", "status": "ok"},
                                        {"tool": "tools/b.py", "status": "ok"}]


@pytest.mark.parametrize("row, failed", [
    ({"status": "error"}, True), ({"status": "UNAUDITED_TIMEOUT"}, True),
    ({"status": "UNAUDITED_ERROR"}, True), ({"status": "survivors"}, False),
    ({"status": "ok"}, False), ({"status": "isolation_unmeasured"}, False),
    ({"status": "mutants_timed_out"}, False), ({}, False), (None, False),
])
def test_an_engine_failure_is_an_error_or_an_unaudited_row_and_nothing_else(ms, row, failed):
    assert ms.is_engine_failure(row) is failed
