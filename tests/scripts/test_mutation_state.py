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


# --- wired-hook status: the mutation workstream's terminal condition (2026-10-13) ---

def _row(tool, h, survived):
    return {"tool": tool, "h": h, "survived": survived, "mutants": 10}


def test_hook_status_counts_only_wired_hooks_and_uses_the_latest_row(ms):
    rows = [_row("tools/hook_a.py", True, 4),
            _row("tools/hook_a.py", True, 0),          # re-run: this one counts
            _row("tools/hook_b.py", True, 2),
            _row("tools/helper.py", False, 9),         # survivors, but not a hook
            _row("tools/broken.py", False, None)]      # errored: no verdict
    s = ms.hook_status(rows)
    assert (s["hooks"], s["hooks_with_survivors"], s["tools_no_verdict"]) == (2, 1, 1)
    assert s["value"] == "1 of 2 wired hooks have survivors | 1 tools with no verdict"
    assert s["done"] is False


def test_hook_status_is_done_only_when_every_hook_is_clean_and_every_tool_has_a_verdict(ms):
    clean = [_row("tools/hook_a.py", True, 0), _row("tools/helper.py", False, 7)]
    assert ms.hook_status(clean)["done"] is True, "a non-hook's survivors do not block it"
    assert ms.hook_status(clean + [_row("tools/x.py", False, None)])["done"] is False
    assert ms.hook_status(clean + [_row("tools/hook_b.py", True, 1)])["done"] is False
    assert ms.hook_status([_row("tools/helper.py", False, 0)])["done"] is False, (
        "a sweep that saw no hook has not shown that the hooks are clean")
    assert ms.hook_status([])["done"] is False


def test_a_wired_hook_with_no_verdict_is_not_counted_as_clean(ms):
    s = ms.hook_status([_row("tools/hook_a.py", True, None)])
    assert (s["hooks"], s["hooks_with_survivors"], s["tools_no_verdict"], s["done"]) == (
        1, 0, 1, False)


def _cli(state_dir, *args):
    import subprocess
    import sys
    code = ("import importlib.util, sys, pathlib\n"
            f"sys.argv = ['mutation_state.py', *{list(args)!r}]\n"
            f"spec = importlib.util.spec_from_file_location('m', {str(TOOL)!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"m.STATE_DIR = pathlib.Path({str(state_dir)!r})\n"
            "src = pathlib.Path(m.__file__).read_text(encoding='utf-8')\n"
            "exec(compile(src[src.index('if __name__ =='):], m.__file__, 'exec'),"
            " {**vars(m), '__name__': '__main__'})\n")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_the_hooks_command_prints_the_status_of_the_recorded_sweep(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    (d / "baseline.jsonl").write_text(
        json.dumps(_row("tools/hook_a.py", True, 3)) + "\n", encoding="utf-8")
    r = _cli(d, "hooks")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["value"] == (
        "1 of 1 wired hooks have survivors | 0 tools with no verdict")


def test_the_hooks_command_errors_when_no_sweep_is_recorded(tmp_path):
    r = _cli(tmp_path / "empty", "hooks")
    assert r.returncode == 2 and "no sweep rows" in json.loads(r.stdout)["error"]


def test_any_other_argument_is_a_usage_error(tmp_path):
    (tmp_path / "baseline.jsonl").write_text(
        json.dumps(_row("tools/hook_a.py", True, 3)) + "\n", encoding="utf-8")
    for args in ((), ("hook",), ("hooks", "extra")):
        r = _cli(tmp_path, *args)
        assert r.returncode == 2 and "usage: mutation_state.py hooks" in r.stderr
        assert r.stdout == "", "a usage error prints no status"
