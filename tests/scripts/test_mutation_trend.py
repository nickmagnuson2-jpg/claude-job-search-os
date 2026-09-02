"""Suite for tools/mutation_trend.py, the longitudinal survival-rate record.

The tool's whole value is that the series is trustworthy, so the tests concentrate on the
two ways a series goes wrong: a row that misreports the corpus, and a duplicate row that
invents a data point. Both are silent -- a wrong percentage looks exactly like a right one.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_trend.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import mutation_trend as mt  # noqa: E402


def row(tool, survived, mutants, own=True, status="survivors"):
    return {"tool": tool, "survived": survived, "mutants": mutants,
            "own": own, "status": status}


# --- summarise --------------------------------------------------------------

def test_survival_pct_is_computed_over_mutants_not_tools():
    """A per-tool average would let one tiny tool outweigh gmail_fetch's 377 mutants."""
    s = mt.summarise([row("a", 1, 100), row("b", 9, 10)])
    assert s["mutants"] == 110
    assert s["survived"] == 10
    assert s["survival_pct"] == pytest.approx(9.09, abs=0.01)


def test_a_tool_with_no_verdict_is_excluded_not_counted_as_clean():
    """An errored tool is UNMEASURED. Folding it in as a zero would report it as
    protected, which is the misreading this whole exercise exists to stop."""
    s = mt.summarise([row("a", 0, 50), {"tool": "b", "survived": None,
                                        "mutants": 30, "status": "error"}])
    assert s["tools_scored"] == 1
    assert s["tools_no_verdict"] == 1
    assert s["mutants"] == 50, "the unmeasured tool's mutants must not enter the denominator"
    assert s["survival_pct"] == 0.0


def test_tools_clean_counts_only_zero_survivor_tools():
    s = mt.summarise([row("a", 0, 10), row("b", 0, 10), row("c", 3, 10)])
    assert s["tools_clean"] == 2


def test_zero_mutant_rows_do_not_divide_by_zero():
    s = mt.summarise([{"tool": "a", "survived": 0, "mutants": 0}])
    assert s["survival_pct"] is None
    assert s["tools_scored"] == 0


def test_empty_baseline_is_reported_as_no_percentage():
    s = mt.summarise([])
    assert s["survival_pct"] is None
    assert s["mutants"] == 0


def test_own_suite_is_counted_and_unknown_is_distinguished_from_false():
    """A baseline written before the `own` field existed must not report as 'no tool has
    its own suite' -- absent and false are different claims."""
    s = mt.summarise([row("a", 1, 5, own=True), row("b", 1, 5, own=False),
                      {"tool": "c", "survived": 1, "mutants": 5}])
    assert s["own_suite"] == 1
    assert s["own_suite_unknown"] == 1


# --- record / show ----------------------------------------------------------

def _state(tmp_path: Path, rows: list[dict]) -> Path:
    (tmp_path / "baseline.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=str(cwd or REPO_ROOT))


def test_record_appends_one_row(tmp_path):
    d = _state(tmp_path, [row("a", 5, 10)])
    r = _run("record", "--state-dir", str(d))
    assert r.returncode == 0, r.stderr
    lines = (d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["survival_pct"] == 50.0


def test_recording_the_same_baseline_twice_does_not_duplicate(tmp_path):
    """Two `record` calls after one sweep would otherwise invent a second data point
    showing zero change, which reads as 'we measured again and nothing moved'."""
    d = _state(tmp_path, [row("a", 5, 10)])
    _run("record", "--state-dir", str(d))
    second = _run("record", "--state-dir", str(d))
    assert second.returncode == 0
    assert json.loads(second.stdout)["status"] == "skipped"
    assert len((d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()) == 1


def test_a_changed_baseline_appends_a_second_row(tmp_path):
    import os, time
    d = _state(tmp_path, [row("a", 5, 10)])
    _run("record", "--state-dir", str(d))
    (d / "baseline.jsonl").write_text(json.dumps(row("a", 2, 10)) + "\n", encoding="utf-8")
    os.utime(d / "baseline.jsonl", (time.time() + 10, time.time() + 10))
    _run("record", "--state-dir", str(d))
    lines = (d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["survival_pct"] == 20.0


def test_history_is_never_rewritten(tmp_path):
    """Append-only is the contract: the first row must survive verbatim."""
    import os, time
    d = _state(tmp_path, [row("a", 5, 10)])
    _run("record", "--state-dir", str(d))
    first = (d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()[0]
    (d / "baseline.jsonl").write_text(json.dumps(row("a", 1, 10)) + "\n", encoding="utf-8")
    os.utime(d / "baseline.jsonl", (time.time() + 10, time.time() + 10))
    _run("record", "--state-dir", str(d))
    assert (d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()[0] == first


def test_record_on_a_missing_baseline_fails_loudly(tmp_path):
    r = _run("record", "--state-dir", str(tmp_path))
    assert r.returncode == 1
    assert json.loads(r.stdout)["status"] == "error"


def test_show_on_an_empty_trend_says_so_instead_of_printing_a_header(tmp_path):
    r = _run("show", "--state-dir", str(tmp_path))
    assert r.returncode == 0
    assert "no trend recorded yet" in r.stdout


def test_show_prints_a_delta_between_points(tmp_path):
    import os, time
    d = _state(tmp_path, [row("a", 5, 10)])
    _run("record", "--state-dir", str(d))
    (d / "baseline.jsonl").write_text(json.dumps(row("a", 2, 10)) + "\n", encoding="utf-8")
    os.utime(d / "baseline.jsonl", (time.time() + 10, time.time() + 10))
    _run("record", "--state-dir", str(d))
    out = _run("show", "--state-dir", str(d)).stdout
    assert "-30.00" in out, "the movement between points is the whole point of the series"


def test_note_is_recorded_when_given(tmp_path):
    d = _state(tmp_path, [row("a", 5, 10)])
    _run("record", "--state-dir", str(d), "--note", "post schema_guard suite")
    assert json.loads((d / mt.TREND_NAME).read_text(encoding="utf-8").splitlines()[0]
                      )["note"] == "post schema_guard suite"
