#!/usr/bin/env python3
"""Tests for tools/mutation_report.py — the survivor map the sweep is read through.

This tool exists to prevent one specific failure, and that failure is the reason most of
these assertions are about WORDING rather than arithmetic: in this repo a scan of 64 of
883 files was once reported as a corpus-wide percentage. A number that is real and a
sentence that is confident propagate further than an obvious error does.

So the properties worth protecting are, in order:

  1. COVERAGE IS STATED FIRST AND EVERY UNMEASURED TOOL IS NAMED. A partial sweep must be
     unable to read as a complete one. Naming them is the load-bearing half: a count
     alone lets the reader assume the remainder is fine.
  2. UNAUDITED ROWS NEVER ENTER A RATIO. A timeout has no result; averaging it in as zero
     survivors would turn silence into evidence of safety.
  3. EVERY RATIO CARRIES ITS DENOMINATOR. `38%` on its own is the shape of the original
     defect.
  4. THE RANKING IS THE WORK LIST, so worst-first ordering is a behaviour, not a display
     detail.

Driven in-process against synthetic state files: build() is a pure function of two files
on disk, which is exactly what makes it cheap to assert against.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_report.py"


def load():
    spec = importlib.util.spec_from_file_location("mutation_report_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def target(tool, mutants=10, w=False, h=False, tests=5):
    return {"tool": tool, "w": w, "h": h, "tests": tests, "mutants": mutants}


def result(tool, survived=0, killed=None, mutants=10, status="survivors", **kw):
    return {"tool": tool, "status": status, "survived": survived,
            "killed": mutants - survived if killed is None else killed,
            "mutants": mutants, "elapsed": 60.0, "tests": 5, **kw}


def state(tmp_path, targets, rows):
    d = tmp_path / "state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    (d / "baseline.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


# --- 1. a partial sweep can never read as a complete one --------------------

def test_coverage_is_stated_before_any_finding(tmp_path):
    """FIRST, not buried: the reader must hit the denominator before the headline."""
    d = state(tmp_path, [target("tools/a.py"), target("tools/b.py"), target("tools/c.py")],
              [result("tools/a.py", survived=4)])
    body = load().build(d)
    assert "Sweep coverage: 1 of 3 auditable tools measured." in body
    assert body.index("Sweep coverage") < body.index("survivors of")


def test_every_unmeasured_tool_is_named_not_just_counted(tmp_path):
    """The load-bearing half. A bare count lets a reader assume the remainder is fine;
    the names are what make 68 unmeasured tools feel like 68 unmeasured tools."""
    d = state(tmp_path,
              [target("tools/a.py"), target("tools/gmail_fetch.py"),
               target("tools/pipe_write.py")],
              [result("tools/a.py", survived=1)])
    body = load().build(d)
    assert "2 NOT MEASURED — unaudited, not clean:" in body
    assert "`gmail_fetch.py`" in body and "`pipe_write.py`" in body


def test_a_complete_sweep_says_so_explicitly(tmp_path):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=1)])
    body = load().build(d)
    assert "All auditable tools were measured." in body
    assert "NOT MEASURED" not in body


def test_self_excluded_tools_are_not_counted_as_unmeasured(tmp_path):
    """A -1 row is out of scope by design, not a coverage hole. Counting it as missing
    would make a complete sweep permanently report itself as incomplete."""
    d = state(tmp_path,
              [target("tools/a.py"), target("tools/mutation_check.py", mutants=-1)],
              [result("tools/a.py", survived=1)])
    body = load().build(d)
    assert "Sweep coverage: 1 of 1 auditable tools measured." in body
    assert "2 selected" in body
    assert "All auditable tools were measured." in body


# --- 2. an unaudited tool never enters a ratio ------------------------------

def test_timed_out_and_errored_tools_are_excluded_from_the_totals(tmp_path):
    """A timeout has no result. Averaging it in as zero survivors converts silence into
    evidence of safety, which is the exact inversion this field exists to prevent."""
    d = state(tmp_path,
              [target("tools/ok.py", mutants=100), target("tools/slow.py", mutants=900)],
              [result("tools/ok.py", survived=40, mutants=100),
               {"tool": "tools/slow.py", "status": "UNAUDITED_TIMEOUT", "mutants": 900,
                "elapsed": 2700.0, "tests": 3}])
    body = load().build(d)
    assert "40 survivors of 100 mutants = 40.0% survival" in body
    assert "1 errored or timed out — unaudited, not clean:" in body
    assert "`slow.py` (UNAUDITED_TIMEOUT)" in body


def test_an_unaudited_tool_is_absent_from_the_ranked_work_list(tmp_path):
    d = state(tmp_path,
              [target("tools/ok.py"), target("tools/boom.py")],
              [result("tools/ok.py", survived=3),
               {"tool": "tools/boom.py", "status": "UNAUDITED_ERROR", "mutants": 10}])
    body = load().build(d)
    work_list = body.split("Ranked worst-first")[1]
    assert "`ok.py`" in work_list and "`boom.py`" not in work_list


# --- 3. every ratio carries its denominator ---------------------------------

def test_the_headline_survival_rate_shows_survivors_killed_and_mutants(tmp_path):
    d = state(tmp_path,
              [target("tools/a.py", mutants=1000), target("tools/b.py", mutants=356)],
              [result("tools/a.py", survived=282, killed=718, mutants=1000),
               result("tools/b.py", survived=236, killed=120, mutants=356)])
    body = load().build(d)
    assert "518 survivors of 1356 mutants = 38.2% survival" in body
    assert "838 killed" in body


def test_the_selection_rule_is_restated_with_the_coverage_line(tmp_path):
    """How the denominator was OBTAINED, not just its value — the rule is what makes the
    number checkable by someone who was not there."""
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py")])
    body = load().build(d)
    assert "tests/scripts/test_<name>.py" in body
    assert "mutation-allow.json" in body


def test_a_zero_mutant_row_does_not_divide_by_zero(tmp_path):
    d = state(tmp_path, [target("tools/a.py", mutants=0)],
              [result("tools/a.py", survived=0, mutants=0)])
    body = load().build(d)
    assert "| 0% |" in body


def test_a_missing_mutant_count_is_backfilled_from_the_target_list(tmp_path):
    """Older records predate the field. Falling back to 0 would silently zero a
    denominator and print 0% survival for a tool nobody measured that way."""
    d = state(tmp_path, [target("tools/a.py", mutants=200)],
              [{"tool": "tools/a.py", "status": "survivors", "survived": 50,
                "killed": 150, "elapsed": 60.0, "tests": 5}])
    body = load().build(d)
    assert "50 survivors of 200 mutants = 25.0% survival" in body


# --- 4. the ranking is the work list ----------------------------------------

def test_the_work_list_is_ordered_worst_first(tmp_path):
    d = state(tmp_path,
              [target("tools/small.py"), target("tools/huge.py"), target("tools/mid.py")],
              [result("tools/small.py", survived=2), result("tools/huge.py", survived=282),
               result("tools/mid.py", survived=40)])
    body = load().build(d)
    order = re.findall(r"\| `(\w+\.py)` \|", body.split("Ranked worst-first")[1])
    assert order[:3] == ["huge.py", "mid.py", "small.py"]


def test_tools_at_zero_survivors_are_named(tmp_path):
    """The good news needs naming too — it is what a later run is compared against."""
    d = state(tmp_path, [target("tools/clean.py"), target("tools/leaky.py")],
              [result("tools/clean.py", survived=0), result("tools/leaky.py", survived=9)])
    body = load().build(d)
    assert "**1 tools at zero survivors**: `clean.py`" in body


def test_isolation_failures_are_named_with_what_they_mean(tmp_path):
    """A test file that only passes inside the suite is a live defect, and it hides in a
    green run — so the report has to say it out loud."""
    d = state(tmp_path, [target("tools/a.py"), target("tools/b.py")],
              [result("tools/a.py", survived=1, isolation_failures=["test_a.py"]),
               result("tools/b.py", survived=1)])
    body = load().build(d)
    assert "**1 tools fail `--isolation`**" in body
    assert "`a.py`" in body.split("fail `--isolation`")[1]
    assert "relying on suite ordering" in body


def test_no_isolation_failures_reads_as_a_clean_statement(tmp_path):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=1)])
    assert "**0 tools fail `--isolation`**" in load().build(d)


# --- 5. categories ----------------------------------------------------------

def test_hook_beats_writer_when_a_tool_is_both(tmp_path):
    """One tool, one row: double-counting it would inflate the corpus totals."""
    d = state(tmp_path, [target("tools/both.py", w=True, h=True)],
              [result("tools/both.py", survived=5, w=True, h=True)])
    body = load().build(d)
    cats = body.split("### By category")[1].split("###")[0]
    assert "| hook | 1 |" in cats and "| writer |" not in cats


def test_legacy_long_key_names_still_categorise(tmp_path):
    """Early records used `writer`/`hooked`; a silent fall-through to `other` would make
    the category table disagree with the corpus it summarises."""
    d = state(tmp_path, [target("tools/a.py", w=True)],
              [{"tool": "tools/a.py", "status": "survivors", "survived": 5, "killed": 5,
                "mutants": 10, "writer": True, "elapsed": 1.0, "tests": 1}])
    cats = load().build(d).split("### By category")[1].split("###")[0]
    assert "| writer | 1 |" in cats


def test_an_uncategorised_tool_lands_in_other_not_nowhere(tmp_path):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=5)])
    cats = load().build(d).split("### By category")[1].split("###")[0]
    assert "| other | 1 |" in cats


# --- 6. crash kills ---------------------------------------------------------

def test_the_crash_kill_share_is_reported_per_tool_and_in_total(tmp_path):
    """`few survivors` and `well tested` are not the same claim: a tool whose kills are
    all crashes just blows up readily. This table is the difference."""
    d = state(tmp_path, [target("tools/a.py"), target("tools/b.py")],
              [result("tools/a.py", survived=0, killed=100, weak=75),
               result("tools/b.py", survived=0, killed=100, weak=25)])
    body = load().build(d)
    assert "| `a.py` | 100 | 75 | 75% |" in body
    assert "**100 of 200 kills (50%) were crash-only**" in body


def test_tools_that_cannot_report_a_crash_share_are_left_out_of_it(tmp_path):
    """No weak count, or nothing killed, means no share to compute. Printing 0% would
    read as `all kills were real assertions`, the opposite of unknown."""
    d = state(tmp_path,
              [target("tools/no_weak.py"), target("tools/no_kills.py"),
               target("tools/fine.py")],
              [result("tools/no_weak.py", survived=1, killed=9),
               result("tools/no_kills.py", survived=10, killed=0, weak=0),
               result("tools/fine.py", survived=0, killed=10, weak=3)])
    table = load().build(d).split("Crash kills")[1]
    assert "`fine.py`" in table
    assert "`no_weak.py`" not in table and "`no_kills.py`" not in table


def test_the_crash_table_is_ordered_by_weak_kills_descending(tmp_path):
    d = state(tmp_path, [target(f"tools/{n}.py") for n in ("low", "high", "mid")],
              [result("tools/low.py", survived=0, killed=50, weak=5),
               result("tools/high.py", survived=0, killed=50, weak=40),
               result("tools/mid.py", survived=0, killed=50, weak=20)])
    table = load().build(d).split("Crash kills")[1]
    assert re.findall(r"\| `(\w+)\.py` \|", table)[:3] == ["high", "mid", "low"]


def test_the_pre_fix_incomparability_is_stated_where_the_number_is_shown(tmp_path):
    """Results measured before commit a4a07fe are not comparable to results after it.
    A reader who does not know that will read a real improvement into a classifier
    change — which is how the 31 -> 11 move was nearly reported as progress."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=0, killed=10, weak=3)])
    body = load().build(d)
    assert "a4a07fe" in body and "not comparable" in body


# --- 8. it has to render as a document, not a pile of numbers ---------------
#
# Every assertion below started as a surviving mutant: the heading, each table's header
# row and its |---| separator, and the sentence defining a weak kill could all be deleted
# with the whole suite green. In Markdown a table whose separator row is missing does not
# render as a table at all -- it collapses into a run of pipe characters -- so these are
# not cosmetic. This report is read, and a report nobody can read is not a gate.

def test_the_report_keeps_its_heading(tmp_path):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=1)])
    assert load().build(d).startswith("## Survivor map")


def test_the_clean_run_does_not_announce_zero_failures_as_a_finding(tmp_path):
    """`0 errored or timed out -- unaudited, not clean` on a clean run trains the reader
    to skip the line on the run where it is real."""
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=1)])
    assert "errored or timed out" not in load().build(d)


@pytest.mark.parametrize("header,separator", [
    ("| category | tools | mutants | survivors | survival |", "|---|---|---|---|---|"),
    ("| # | tool | cat | mutants | survivors | survival | tests | mins |",
     "|---|---|---|---|---|---|---|---|"),
    ("| tool | killed | weak (crash-only) | share |", "|---|---|---|"),
])
def test_every_table_keeps_its_header_and_separator(tmp_path, header, separator):
    d = state(tmp_path, [target("tools/a.py", w=True)],
              [result("tools/a.py", survived=3, killed=7, weak=2, w=True)])
    body = load().build(d)
    assert header in body
    assert separator in body.split(header)[1].splitlines()[1], \
        "the separator row must directly follow its header or the table stops rendering"


def test_the_weak_kill_definition_travels_with_the_weak_kill_table(tmp_path):
    """Without it the column reads as a second survivor count. The distinction it draws
    -- the suite noticed a CRASH, not a checked value -- is the whole point of the field."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=0, killed=10, weak=3)])
    body = load().build(d)
    assert "only because the code CRASHED" in body
    assert "not because a test checked a value" in body


# --- 9. the CLI contract ----------------------------------------------------

def test_no_results_file_is_an_error_not_an_empty_report(tmp_path, capsys):
    """An empty report renders exactly like a clean one."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "targets.json").write_text("[]", encoding="utf-8")
    mod = load()
    rc = mod.main(["--state-dir", str(d)])
    assert rc == 1
    assert "no sweep results" in capsys.readouterr().err


def test_out_writes_the_same_body_that_is_printed(tmp_path, capsys):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=3)])
    out = tmp_path / "report_body.md"
    mod = load()
    rc = mod.main(["--state-dir", str(d), "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == 0
    assert out.read_text(encoding="utf-8").strip() == printed.strip()


def test_the_real_tool_runs_against_the_live_sweep_state(tmp_path):
    """Verified on real data, not only fixtures: a green fixture suite hides real-data
    divergence, which is a standing rule in this repo."""
    live = REPO_ROOT / "output" / "analysis" / "082626-mutation-baseline"
    # Guard on the files this test actually READS, not on a neighbour. Guarding on
    # targets.json alone made the test explode in a git worktree, where `--targets` had
    # created targets.json but no results file existed -- a skip condition that skipped
    # the wrong thing.
    results = next((live / n for n in ("baseline.jsonl", "baseline.run1-contaminated.jsonl",
                                       "baseline.pre-weakfix.jsonl")
                    if (live / n).exists()), None)
    if not (live / "targets.json").exists() or results is None:
        pytest.skip("no live sweep state in this tree")
    rows = [json.loads(l) for l in
            results.read_text(encoding="utf-8").splitlines() if l.strip()]
    d = state(tmp_path, json.loads((live / "targets.json").read_text(encoding="utf-8")),
              rows)
    r = subprocess.run(
        [sys.executable, str(TOOL), "--state-dir", str(d)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert f"Sweep coverage: {len(rows)} of" in r.stdout
    assert "NOT MEASURED" in r.stdout
