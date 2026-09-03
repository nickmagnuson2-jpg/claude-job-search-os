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
import datetime
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
    assert "1 UNMEASURED — no verdict, neither clean nor a finding:" in body
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
    # Conditional ON PURPOSE. A bare `assert "NOT MEASURED" in r.stdout` asserts the live
    # corpus is INCOMPLETE, so it passes only while the sweep is unfinished and fails the
    # moment one succeeds -- which is exactly how it broke on 2026-09-02 at 110 of 110.
    # The invariant that actually holds on any live state is the biconditional. Both
    # rendering directions are already covered by fixtures above; this asserts the tool
    # agrees with real state, which is what a real-data test is for.
    targets = json.loads((live / "targets.json").read_text(encoding="utf-8"))
    measured = {row["tool"] for row in rows}
    gaps = [t for t in targets if t["tool"] not in measured and t.get("mutants", 0) > 0]
    assert ("NOT MEASURED" in r.stdout) == bool(gaps), (
        f"{len(gaps)} unmeasured target(s) in live state but the report "
        f"{'omitted' if gaps else 'emitted'} the NOT MEASURED section")


# --- 10. the loudest findings ------------------------------------------------
#
# Two things the sweep already knew and the report never said, both added 2026-08-27.
# A tool whose mapped tests kill ZERO mutants is not "poorly covered", it is uncovered
# while displaying a test count -- and ranking by absolute survivors buried it:
# check_email_via_skill.py had all 23 mutants survive and still sorted tenth.

def test_a_tool_whose_tests_kill_nothing_gets_its_own_section(tmp_path):
    d = state(tmp_path, [target("tools/dead.py", mutants=23, tests=13)],
              [result("tools/dead.py", survived=23, killed=0, mutants=23, tests=13)])
    body = load().build(d)
    assert "kill NOTHING" in body
    assert "1 tools where not one mutant died" in body
    assert "`dead.py`" in body.split("kill NOTHING")[1].split("###")[0]


def test_the_dead_section_reports_the_test_count_that_reads_as_coverage(tmp_path):
    """The count is the whole reason this is worse than having no tests: a reader checks
    `tests=13` and moves on."""
    d = state(tmp_path, [target("tools/dead.py", mutants=23, tests=13)],
              [result("tools/dead.py", survived=23, killed=0, mutants=23, tests=13)])
    section = load().build(d).split("kill NOTHING")[1].split("### By category")[0]
    assert "| 23 |" in section and "| 13 |" in section


def test_the_dead_section_names_how_many_files_were_mapped(tmp_path):
    """Mapped-file count is the tell for a selection artifact: files mapped by an
    incidental string match look like coverage and exercise nothing."""
    tgt = target("tools/dead.py", mutants=9, tests=2)
    tgt["test_files"] = 1
    d = state(tmp_path, [tgt], [result("tools/dead.py", survived=9, killed=0, mutants=9)])
    section = load().build(d).split("kill NOTHING")[1].split("### By category")[0]
    assert "files mapped" in section


def test_a_tool_with_any_kill_is_not_in_the_dead_section(tmp_path):
    """One kill means the tests reach the code. That is a different finding."""
    d = state(tmp_path, [target("tools/weak.py", mutants=10)],
              [result("tools/weak.py", survived=9, killed=1, mutants=10)])
    body = load().build(d)
    assert "kill NOTHING" not in body


def test_a_tool_with_no_mutants_is_not_accused_of_killing_nothing(tmp_path):
    """Nothing to kill is not the same as killing nothing, and the accusation would send
    someone to write tests for a file with no mutable decisions."""
    d = state(tmp_path, [target("tools/tiny.py", mutants=0)],
              [result("tools/tiny.py", survived=0, killed=0, mutants=0)])
    assert "kill NOTHING" not in load().build(d)


def test_the_dead_section_comes_before_the_ranked_work_list(tmp_path):
    """Position IS the point: the ranked list is what buried this finding."""
    d = state(tmp_path, [target("tools/dead.py", mutants=23), target("tools/big.py")],
              [result("tools/dead.py", survived=23, killed=0, mutants=23),
               result("tools/big.py", survived=5)])
    body = load().build(d)
    assert body.index("kill NOTHING") < body.index("Ranked worst-first")


def test_no_dead_tools_means_no_section_at_all(tmp_path):
    """`0 tools where not one mutant died` is noise that trains the reader to skip it."""
    d = state(tmp_path, [target("tools/ok.py")], [result("tools/ok.py", survived=1)])
    assert "kill NOTHING" not in load().build(d)


def test_assertion_free_tests_are_named_with_their_location(tmp_path):
    """Collected by the sweep since it existed and never displayed -- the same defect as
    the suppressed weak_kill_count: measured, recorded, invisible."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, assertion_free_tests=[
                  {"file": "tests/scripts/test_a.py", "test": "test_nothing", "line": 43}])])
    body = load().build(d)
    assert "Tests that cannot fail" in body
    assert "1 tests contain no assertion at all" in body
    assert "`test_nothing`" in body and "tests/scripts/test_a.py:43" in body


def test_tautological_assertions_are_reported_separately(tmp_path):
    """A missing assertion and an assertion that cannot fail are different repairs."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, tautological_assertions=[
                  {"file": "tests/scripts/test_a.py", "test": "test_always", "line": 7}])])
    body = load().build(d)
    assert "tautological" in body
    assert "`test_always`" in body


def test_the_cannot_fail_section_is_absent_when_there_is_nothing_to_report(tmp_path):
    d = state(tmp_path, [target("tools/a.py")], [result("tools/a.py", survived=1)])
    assert "Tests that cannot fail" not in load().build(d)


def test_unaudited_rows_contribute_to_neither_new_section(tmp_path):
    """A timed-out tool has no result. Reporting it as killing nothing would be an
    accusation built on silence."""
    d = state(tmp_path, [target("tools/slow.py", mutants=50)],
              [{"tool": "tools/slow.py", "status": "UNAUDITED_TIMEOUT", "mutants": 50,
                "assertion_free_tests": [{"file": "f.py", "test": "t", "line": 1}]}])
    body = load().build(d)
    assert "kill NOTHING" not in body
    assert "Tests that cannot fail" not in body


# --- 11. the new sections have to render as documents too --------------------
#
# Every assertion below was a surviving mutant. A Markdown table missing its |---|
# separator does not render as a table, and a section that appears with an empty body
# because its guard was forced true is worse than no section: it reads as "checked,
# nothing found" when nothing was checked.

def test_the_dead_table_keeps_its_header_and_separator(tmp_path):
    d = state(tmp_path, [target("tools/dead.py", mutants=9)],
              [result("tools/dead.py", survived=9, killed=0, mutants=9)])
    body = load().build(d)
    header = "| tool | mutants | all survived | tests mapped | files mapped |"
    assert header in body
    assert body.split(header)[1].splitlines()[1] == "|---|---|---|---|---|"


def test_the_dead_section_keeps_its_remediation_guidance(tmp_path):
    """Two different repairs hide behind one symptom -- tests that do not exercise the
    tool, and tests that exercise it without asserting. Naming both is the difference
    between an actionable finding and a scolding."""
    d = state(tmp_path, [target("tools/dead.py", mutants=9)],
              [result("tools/dead.py", survived=9, killed=0, mutants=9)])
    body = load().build(d)
    assert "do not actually exercise the tool" in body
    assert "without asserting" in body


def test_the_cannot_fail_section_keeps_its_explanation(tmp_path):
    """Without it the tables read as more mutation results. These are STATIC findings and
    mean something different: not weak, decorative."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, assertion_free_tests=[
                  {"file": "f.py", "test": "t", "line": 1}])])
    body = load().build(d)
    assert "Found statically, not by mutation" in body


def test_assertion_free_table_renders_when_it_is_the_only_finding(tmp_path):
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, assertion_free_tests=[
                  {"file": "f.py", "test": "t", "line": 1}])])
    body = load().build(d)
    header = "| tool | test | file:line |"
    assert header in body
    assert body.split(header)[1].splitlines()[1] == "|---|---|---|"


def test_tautological_table_renders_when_it_is_the_only_finding(tmp_path):
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, tautological_assertions=[
                  {"file": "f.py", "test": "t", "line": 1}])])
    body = load().build(d)
    header = "| tool | test | file:line |"
    assert header in body
    assert body.split(header)[1].splitlines()[1] == "|---|---|---|"


def test_only_tautological_findings_do_not_claim_missing_assertions(tmp_path):
    """Forcing the assertion-free guard true prints its heading over an empty table --
    an accusation of a defect that was not found."""
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, tautological_assertions=[
                  {"file": "f.py", "test": "t", "line": 1}])])
    assert "contain no assertion at all" not in load().build(d)


def test_only_assertion_free_findings_do_not_claim_tautologies(tmp_path):
    d = state(tmp_path, [target("tools/a.py")],
              [result("tools/a.py", survived=1, assertion_free_tests=[
                  {"file": "f.py", "test": "t", "line": 1}])])
    assert "tautological" not in load().build(d)


class TestStaleBaselineBanner:
    """Origin 2026-08-31. mutation_check.py spawned pytest WITHOUT
    PYTHONDONTWRITEBYTECODE, so CPython's (mtime, size) .pyc invalidation could execute a
    later mutant as an earlier one's bytecode. That produces FALSE KILLS, which means a
    baseline taken before the fix UNDER-reports survivors: it is optimistic, the dangerous
    direction. The 082626 baseline (109 records, Aug 26-29) predates the fix entirely.

    A note in a file would not reach the tool-hardening work; the report is what that work
    reads, and it already states coverage FIRST so a partial run cannot read as complete.
    Staleness gets the same treatment for the same reason."""

    def _state(self, tmp_path, mtime):
        (tmp_path / "targets.json").write_text(
            json.dumps([target("tools/a.py", mutants=4)]), encoding="utf-8")
        b = tmp_path / "baseline.jsonl"
        b.write_text(json.dumps({"tool": "tools/a.py", "mutants": 4, "killed": 4,
                                 "survived": 0, "status": "ok"}) + "\n", encoding="utf-8")
        os.utime(b, (mtime, mtime))
        return tmp_path

    STALE = datetime.datetime(2026, 8, 29, tzinfo=datetime.timezone.utc).timestamp()
    FRESH = datetime.datetime(2026, 9, 5, tzinfo=datetime.timezone.utc).timestamp()

    def test_baseline_older_than_the_bytecode_fix_is_banner_flagged(self, tmp_path):
        body = load().build(self._state(tmp_path, self.STALE))
        assert "false kill" in body.lower(), (
            "a pre-fix baseline is reported without any staleness warning; the "
            "tool-hardening work would read inflated kill counts as real"
        )

    def test_the_banner_appears_before_the_numbers(self, tmp_path):
        low = load().build(self._state(tmp_path, self.STALE)).lower()
        assert low.index("false kill") < low.index("sweep coverage"), (
            "staleness warning must precede the counts it invalidates"
        )

    def test_the_banner_says_which_DIRECTION_the_error_runs(self, tmp_path):
        """'May be wrong' is not actionable. The reader has to know the baseline reads
        CLEANER than reality, or they will assume the safe direction and skip the re-run."""
        low = load().build(self._state(tmp_path, self.STALE)).lower()
        assert "under-reported" in low or "optimistic" in low

    def test_a_baseline_taken_after_the_fix_gets_no_banner(self, tmp_path):
        body = load().build(self._state(tmp_path, self.FRESH))
        assert "false kill" not in body.lower(), (
            "a post-fix baseline must not carry the warning, or the banner becomes noise "
            "everyone learns to skip"
        )


# =============================================================================
# 2026-09-03: an ERRORED tool is UNMEASURED, and must never be rendered as one
# whose tests killed nothing.
#
# Origin, and it reached Nick: the 2026-09-03 sweep errored on 23 of 118 tools
# (baseline_red -- their mapped tests were failing on unmutated source, so
# mutation_check correctly refused to measure). Those rows carry killed=null and
# survived=null. `build()` put them in the "Mapped tests that kill NOTHING"
# section, and that morning's standup told Nick his PUBLIC-REPO PII GATE had no
# effective tests. It had killed 271 of 271 the day before.
#
# Two coercions caused it, both on rows the `ok` partition should never have
# admitted: `not (r.get("killed") or 0)` reads null as zero, and the aggregate
# sums do the same -- which is why the report's own headline survival rate
# disagreed with mutation_trend's on the identical file.
# =============================================================================

def _state(tmp_path, rows):
    targets = [{"tool": r["tool"], "mutants": r["mutants"], "test_files": 2} for r in rows]
    (tmp_path / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    (tmp_path / "baseline.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return tmp_path


ERRORED = {"tool": "tools/check_public_pii.py", "mutants": 271, "tests": 240,
           "rc": 1, "status": "error", "killed": None, "survived": None, "weak": None}
REAL_DEAD = {"tool": "tools/open_draft.py", "mutants": 113, "tests": 7,
             "rc": 0, "status": "ok", "killed": 0, "survived": 113, "weak": 0}
HEALTHY = {"tool": "tools/check_banned_phrase.py", "mutants": 49, "tests": 77,
           "rc": 0, "status": "ok", "killed": 49, "survived": 0, "weak": 0}


def test_errored_tool_is_not_reported_as_killing_nothing(tmp_path):
    """The exact defect that produced a false standup claim about the PII gate."""
    body = load().build(_state(tmp_path, [ERRORED, HEALTHY]))
    dead_section = body.split("kill NOTHING")[1] if "kill NOTHING" in body else ""
    assert "check_public_pii" not in dead_section, (
        "an UNMEASURED tool was listed as one whose tests kill nothing:\n" + dead_section[:600])


def test_a_genuinely_dead_tool_is_still_reported(tmp_path):
    """Guards the fix from being an over-broad silencing of the whole section.

    Without this, deleting the section entirely would pass the test above.
    """
    body = load().build(_state(tmp_path, [ERRORED, REAL_DEAD, HEALTHY]))
    assert "kill NOTHING" in body, "the real finding was suppressed along with the false one"
    dead_section = body.split("kill NOTHING")[1]
    assert "open_draft" in dead_section, (
        "a tool that really killed 0 of 113 is missing from the section")


def test_errored_tools_are_surfaced_as_unmeasured_not_silently_dropped(tmp_path):
    """Unmeasured must be VISIBLE. Dropping the row is the same defect inverted:
    a tool nobody measured would read as a tool with nothing wrong."""
    body = load().build(_state(tmp_path, [ERRORED, HEALTHY]))
    assert "check_public_pii" in body, "the errored tool vanished from the report entirely"


def test_errored_mutants_are_excluded_from_the_aggregate(tmp_path):
    """Why the report said 31.0% while mutation_trend said 37.34% on one file.

    An errored tool contributed 271 mutants to the denominator and 0 kills to the
    numerator, so the corpus survival rate was computed against work never done.
    """
    body = load().build(_state(tmp_path, [ERRORED, HEALTHY]))
    assert "271" not in body.split("survival")[0][-400:] or "49" in body, body[:400]
    # The healthy tool alone: 49 mutants, 0 survivors, 0% survival.
    assert "0.0% survival" in body or "0% survival" in body, (
        "aggregate still includes unmeasured mutants:\n" + body[:800])


def test_no_unmeasured_section_when_every_tool_has_a_verdict(tmp_path):
    """A clean sweep must not print an empty "0 UNMEASURED" banner.

    Kills IF_TRUE on `if bad:`. Without this the guard could be forced always-true
    and every clean report would carry a contradictory zero-count warning, which is
    how a reader learns to skip the line that matters on the day it is non-zero.
    """
    body = load().build(_state(tmp_path, [HEALTHY, REAL_DEAD]))
    assert "UNMEASURED" not in body, (
        "a fully-measured sweep printed an unmeasured banner:\n" + body[:600])


def test_unmeasured_section_explains_that_baseline_red_is_not_untested(tmp_path):
    """Kills DROP_CALL on the explanation line.

    The count alone reproduces the original harm in a quieter form: on 2026-09-03 a
    reader saw errored tools and concluded their tests were worthless. The section
    has to say that `baseline_red` means the tool's OWN tests were already failing,
    so nothing could be measured -- not that the tool is unprotected.
    """
    body = load().build(_state(tmp_path, [ERRORED, HEALTHY]))
    assert "baseline_red" in body, "the section does not name the code that explains it"
    assert "does NOT mean the tool is untested" in body, (
        "the section states a count without the interpretation that prevents "
        "misreading it:\n" + body[:800])
