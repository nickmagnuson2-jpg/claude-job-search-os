#!/usr/bin/env python3
"""Tests for tools/mutation_sweep.py — the harness that measures the whole tool corpus.

This tool drives the sweep that produces the repo's only real number for how much of a
2,842-test suite protects anything. Until now it had no test file of its own, so it was
invisible to the sweep it runs — the gap its own commit named.

The properties worth protecting, in order:

  1. SELECTION IS DETERMINISTIC. It is a mechanical rule, not a judgment call, and it is
     what makes "68 tools unmeasured" a fact rather than an opinion. A bug here silently
     shrinks the corpus and every downstream ratio is then computed over the wrong
     denominator.
  2. AN UNMEASURED TOOL IS NEVER RECORDED AS CLEAN. A timeout, a crash, or unparseable
     output must land as UNAUDITED_*, because "no survivors found" and "nothing was
     looked at" render identically in a report and mean opposite things.
  3. RESUME MUST NOT LOSE OR REDO WORK. Banked tools are skipped and the state file is
     appended, never truncated: an unattended 5-hour run that costs its whole history
     to a reboot is not resumable, whatever the docstring says.
  4. IT MUST NOT SWEEP ITSELF. The runner executes from this file; mutating it rewrites
     live source under the running process.

Driven in-process against a synthetic repo rooted at tmp_path (MUTATION_REPO_ROOT, the
same seam test_mutation_check.py uses), with a stub mutation_check.py standing in for the
real engine. In-process and value-asserting on purpose: a subprocess test that re-raises
an unexpected exit as AssertionError dresses a crash as an assertion and inflates this
tool's own strong-kill count, which is the caveat documented in the runbook.
"""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_sweep.py"


# --- harness ----------------------------------------------------------------

def load(root, monkeypatch):
    """Import mutation_sweep as a FRESH module object rooted at `root`.

    A fresh object rather than importlib.reload: REPO_ROOT is computed at import time,
    so reloading a shared module leaks the last test's root into the next one.
    """
    monkeypatch.setenv("MUTATION_REPO_ROOT", str(root))
    spec = importlib.util.spec_from_file_location("mutation_sweep_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STUB = '''\
import json, os, sys, time
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ctl = json.load(open(os.path.join(root, "control.json"), encoding="utf-8"))
target = sys.argv[1]
with open(os.path.join(root, "calls.log"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"target": target, "argv": sys.argv[2:],
                         "encoding": os.environ.get("PYTHONIOENCODING")}) + "\\n")
if "--list" in sys.argv:
    print(json.dumps({"mutants": ctl["mutants"][target]})
          if target in ctl.get("mutants", {}) else ctl.get("list_garbage", "not json"))
    sys.exit(0)
time.sleep(ctl.get("sleep", {}).get(target, 0))
res = ctl.get("results", {}).get(target)
if res is None:
    print(ctl.get("run_garbage", "<<not json>>")); sys.exit(1)
print(json.dumps(res)); sys.exit(res.get("_rc", 0))
'''


@pytest.fixture
def repo(tmp_path):
    """A synthetic repo: tools/, tests/scripts/, a stub engine, and a control file."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests" / "scripts").mkdir(parents=True)
    (tmp_path / "tools" / "mutation_check.py").write_text(STUB, encoding="utf-8")
    (tmp_path / "control.json").write_text(json.dumps({"mutants": {}}), encoding="utf-8")
    return tmp_path


def add_tool(repo, name, src="import sys\n\n\ndef go(p):\n    return p.read_text()\n",
             tested=True, mutants=None):
    (repo / "tools" / f"{name}.py").write_text(src, encoding="utf-8")
    if tested:
        (repo / "tests" / "scripts" / f"test_{name}.py").write_text(
            "def test_x():\n    assert 1 == 1\n", encoding="utf-8")
    if mutants is not None:
        set_control(repo, mutants={**control(repo).get("mutants", {}),
                                   f"tools/{name}.py": mutants})
    return f"tools/{name}.py"


def control(repo):
    return json.loads((repo / "control.json").read_text(encoding="utf-8"))


def set_control(repo, **kw):
    (repo / "control.json").write_text(json.dumps({**control(repo), **kw}), encoding="utf-8")


def calls(repo):
    p = repo / "calls.log"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] \
        if p.exists() else []


def write_targets(repo, rows, state=None):
    state = state or (repo / "state")
    state.mkdir(parents=True, exist_ok=True)
    (state / "targets.json").write_text(json.dumps(rows), encoding="utf-8")
    return state


def banked(state):
    p = state / "baseline.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if p.exists() else []


# --- 1. count_tests: AST, not a regex ---------------------------------------

def test_class_nested_and_async_tests_are_counted(repo, monkeypatch):
    """The regression this function exists for. `^def test_` missed class-nested tests
    entirely and undercounted context_file_audit.py as 0 when it has 110 — an undercount
    feeds the ranking that decides which tool gets worked on next."""
    mod = load(repo, monkeypatch)
    f = repo / "t.py"
    f.write_text(textwrap.dedent('''
        def test_module_level():
            pass

        async def test_async():
            pass

        class TestGroup:
            def test_nested_one(self):
                pass

            def test_nested_two(self):
                pass

            def helper(self):
                pass

        def not_a_test():
            pass
    '''), encoding="utf-8")
    assert mod.count_tests(f) == 4


def test_count_tests_returns_zero_for_unparseable_and_missing_files(repo, monkeypatch):
    """0, not a crash: one malformed test file must not abort the whole target build."""
    mod = load(repo, monkeypatch)
    bad = repo / "bad.py"
    bad.write_text("def test_x(:\n", encoding="utf-8")
    assert mod.count_tests(bad) == 0
    assert mod.count_tests(repo / "nope.py") == 0


# --- 2. selection is deterministic ------------------------------------------

def test_only_tools_with_a_matching_test_file_are_selected(repo, monkeypatch):
    add_tool(repo, "has_tests", tested=True, mutants=5)
    add_tool(repo, "no_tests", tested=False)
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == ["tools/has_tests.py"]


def test_the_allowlist_is_not_consulted_at_selection_time_at_all(repo, monkeypatch):
    """SUPERSEDED BEHAVIOUR, kept as a regression. This test used to assert the opposite:
    that a `tools/x.py::mutant-7` entry excluded tools/x.py from the corpus. That was the
    bug. `mutation-allow.json` is keyed per MUTANT and mutation_check.py honours it there
    (counting an allowlisted mutant as `allowlisted`, never as a survivor), so excluding
    at selection time meant justifying ONE mutant deleted the whole tool from measurement
    permanently. All 47 live entries are mutant-scoped; not one is a whole-tool key, so
    there is no case this exclusion was serving."""
    add_tool(repo, "guarded", mutants=9)
    (repo / "tools" / "mutation-allow.json").write_text(
        json.dumps({"tools/guarded.py::f::DROP_CALL::abc": "a written reason"}),
        encoding="utf-8")
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == ["tools/guarded.py"]


def test_an_unreadable_allowlist_cannot_affect_selection(repo, monkeypatch):
    """It is not read here any more, so a corrupt one must be a non-event rather than an
    empty corpus that reads downstream as a clean sweep of nothing."""
    add_tool(repo, "plain", mutants=3)
    (repo / "tools" / "mutation-allow.json").write_text("{not json", encoding="utf-8")
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == ["tools/plain.py"]


def test_the_sweep_refuses_to_target_itself(repo, monkeypatch):
    """The runner executes from mutation_sweep.py. Sweeping it rewrites live source under
    the running process — the hazard mutation_check.py already refuses itself for."""
    (repo / "tools" / "mutation_sweep.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "scripts" / "test_mutation_sweep.py").write_text(
        "def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    set_control(repo, mutants={"tools/mutation_sweep.py": 99})
    mod = load(repo, monkeypatch)
    rows = {r["tool"]: r for r in mod.build_targets()}
    assert rows["tools/mutation_sweep.py"]["mutants"] == -1, \
        "must be recorded as non-auditable, not given a real mutant count"
    assert not any(c["target"] == "tools/mutation_sweep.py" for c in calls(repo)), \
        "must not even be enumerated by the engine"
    assert rows["tools/mutation_sweep.py"]["h"] is False, \
        "the row is skipped from measurement, but its metadata must still be truthful"


def test_self_exclusion_is_visible_in_the_accounting_not_silently_dropped(repo, monkeypatch,
                                                                         capsys):
    """A dropped row would make `selected` and `auditable` agree while a tool went
    unmeasured — exactly the false-completeness this report is built to prevent."""
    (repo / "tools" / "mutation_sweep.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "scripts" / "test_mutation_sweep.py").write_text(
        "def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    add_tool(repo, "real", mutants=4)
    mod = load(repo, monkeypatch)
    mod.main(["--state-dir", str(repo / "state"), "--targets"])
    out = json.loads(capsys.readouterr().out)
    assert out["selected"] == 2 and out["auditable"] == 1
    assert out["self_excluded"] == ["tools/mutation_sweep.py"]


def test_mutant_counts_come_from_the_engine(repo, monkeypatch):
    add_tool(repo, "a", mutants=17)
    mod = load(repo, monkeypatch)
    assert mod.build_targets()[0]["mutants"] == 17


def test_unparseable_engine_output_is_minus_one_not_zero(repo, monkeypatch):
    """-1 and 0 diverge downstream: `mutants > 0` filters both out of the run, but the
    report prints `self_excluded` from the -1 rows and would otherwise call an engine
    failure a self-exclusion."""
    add_tool(repo, "a")                       # no entry in control["mutants"]
    set_control(repo, list_garbage="engine exploded")
    mod = load(repo, monkeypatch)
    assert mod.build_targets()[0]["mutants"] == -1


def test_writer_detection_flags_every_write_shape(repo, monkeypatch):
    """`w` drives the blast-radius ranking: a writer that corrupts a real data file
    outranks a hook that misfires."""
    add_tool(repo, "w_open", src="def f(p):\n    open(p, 'w').write('x')\n", mutants=1)
    add_tool(repo, "w_append", src="def f(p):\n    open(p, 'a').write('x')\n", mutants=1)
    add_tool(repo, "w_replace", src="import os\n\n\ndef f(a, b):\n    os.replace(a, b)\n",
             mutants=1)
    add_tool(repo, "w_text", src="def f(p):\n    p.write_text('x')\n", mutants=1)
    add_tool(repo, "r_only", src="def f(p):\n    return p.read_text()\n", mutants=1)
    mod = load(repo, monkeypatch)
    flags = {r["tool"]: r["w"] for r in mod.build_targets()}
    assert flags == {"tools/w_open.py": True, "tools/w_append.py": True,
                     "tools/w_replace.py": True, "tools/w_text.py": True,
                     "tools/r_only.py": False}


def test_the_self_excluded_row_still_reports_its_hook_wiring(repo, monkeypatch):
    """Skipped from measurement is not the same as unknown: the row still feeds the
    accounting, so an inverted flag there is a quiet lie in the target list."""
    (repo / "tools" / "mutation_sweep.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "scripts" / "test_mutation_sweep.py").write_text(
        "def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("mutation_sweep.py", encoding="utf-8")
    mod = load(repo, monkeypatch)
    rows = {r["tool"]: r for r in mod.build_targets()}
    assert rows["tools/mutation_sweep.py"]["h"] is True


def test_hooked_flag_tracks_the_settings_file(repo, monkeypatch):
    add_tool(repo, "wired_tool", mutants=1)
    add_tool(repo, "loose_tool", mutants=1)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"command": "tools/wired_tool.py"}]}}),
        encoding="utf-8")
    mod = load(repo, monkeypatch)
    flags = {r["tool"]: r["h"] for r in mod.build_targets()}
    assert flags == {"tools/wired_tool.py": True, "tools/loose_tool.py": False}


def test_a_missing_settings_file_leaves_everything_unhooked(repo, monkeypatch):
    add_tool(repo, "a", mutants=1)
    mod = load(repo, monkeypatch)
    assert mod.build_targets()[0]["h"] is False


def test_scheduling_is_cheapest_first_so_a_partial_run_measures_the_most(repo, monkeypatch):
    """SUPERSEDED ORDERING, kept as a regression. This used to sort writers-then-hooked-
    then-most-tested, i.e. blast radius first. That is the right order for a HUMAN work
    list and the wrong one for a scheduler: the report ranks worst-first by survivors on
    its own, and a run is far more often stopped or resumed than finished.

    Cost is mutants x mapped test files, because every mutant re-runs every mapped file.
    Measured 2026-08-26 at ~8s/mutant, so the corpus is ~24h. Under the old order the
    first tools were the giants -- todo_write.py is 541 x 15 = 8115 test-runs, exceeds the
    120-minute cap, and a timeout banks NO information -- so a night could end with a
    handful of timeouts and nothing measured.
    """
    add_tool(repo, "cheap", src="def f(p):\n    return p.read_text()\n", mutants=5)
    add_tool(repo, "huge", src="def f(p):\n    p.write_text('x')\n", mutants=500)
    add_tool(repo, "mid", src="def f(p):\n    p.write_text('x')\n", mutants=50)
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == [
        "tools/cheap.py", "tools/mid.py", "tools/huge.py"]


def test_cost_counts_test_files_not_just_mutants(repo, monkeypatch):
    """A 40-mutant tool mapped to 10 files costs more than a 100-mutant tool mapped to 1.
    Sorting on mutants alone would schedule the expensive one first and mis-spend the run."""
    add_tool(repo, "many_files", mutants=40)
    for i in range(9):
        (repo / "tests" / "scripts" / f"test_extra_{i}.py").write_text(
            "import many_files\n\n\ndef test_a():\n    assert 1 == 1\n", encoding="utf-8")
    add_tool(repo, "one_file", mutants=100)
    mod = load(repo, monkeypatch)
    rows = mod.build_targets()
    assert rows[0]["tool"] == "tools/one_file.py", "100x1 is cheaper than 40x10"
    assert rows[1]["test_files"] == 10


def test_the_target_row_records_how_many_test_files_grade_it(repo, monkeypatch):
    """Recorded so the cost is auditable from targets.json rather than recomputed."""
    add_tool(repo, "a", mutants=3)
    mod = load(repo, monkeypatch)
    assert mod.build_targets()[0]["test_files"] == 1


# --- 3. --targets runs no mutations -----------------------------------------

def test_targets_mode_enumerates_but_never_mutates(repo, monkeypatch, capsys):
    """`--targets` is the safe command: it is what you run to look before leaping."""
    add_tool(repo, "a", mutants=4)
    add_tool(repo, "b", mutants=6)
    mod = load(repo, monkeypatch)
    rc = mod.main(["--state-dir", str(repo / "state"), "--targets"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["mutants"] == 10 and out["auditable"] == 2
    assert (repo / "state" / "targets.json").exists()
    assert all("--list" in c["argv"] for c in calls(repo)), \
        "--targets must only enumerate; an --isolation call here would mutate the tree"
    assert not (repo / "state" / "baseline.jsonl").exists()


def test_targets_mode_creates_a_missing_state_dir(repo, monkeypatch, capsys):
    add_tool(repo, "a", mutants=1)
    mod = load(repo, monkeypatch)
    mod.main(["--state-dir", str(repo / "deep" / "nested"), "--targets"])
    capsys.readouterr()
    assert (repo / "deep" / "nested" / "targets.json").exists()


# --- 4. run_sweep: an unmeasured tool is never recorded as clean -------------

def test_no_target_list_is_an_error_not_an_empty_clean_sweep(repo, monkeypatch, capsys):
    mod = load(repo, monkeypatch)
    state = repo / "state"
    state.mkdir()
    rc = mod.run_sweep(state)
    assert rc == 1
    assert "--targets" in capsys.readouterr().err
    assert not (state / "baseline.jsonl").exists()


def test_each_measured_tool_banks_one_record_with_its_result(repo, monkeypatch, capsys):
    state = write_targets(repo, [{"tool": "tools/a.py", "w": True, "h": False,
                                  "tests": 3, "mutants": 10}])
    set_control(repo, results={"tools/a.py": {"status": "survivors", "killed": 7,
                                              "survived": 3, "weak_kill_count": 2,
                                              "isolation_failures": [],
                                              "survivors": ["m1", "m2", "m3"]}})
    mod = load(repo, monkeypatch)
    assert mod.run_sweep(state) == 0
    capsys.readouterr()

    rows = banked(state)
    assert len(rows) == 1
    r = rows[0]
    assert r["tool"] == "tools/a.py" and r["status"] == "survivors"
    assert r["survived"] == 3 and r["killed"] == 7 and r["weak"] == 2
    assert r["mutants"] == 10 and r["tests"] == 3 and r["w"] is True
    assert r["survivors"] == ["m1", "m2", "m3"]
    assert isinstance(r["elapsed"], float)


def test_tools_with_no_mutants_are_never_run(repo, monkeypatch, capsys):
    """A -1 row is a self-exclusion and a 0 row has nothing to mutate; handing either to
    the engine wastes a slot and banks a meaningless record."""
    state = write_targets(repo, [
        {"tool": "tools/skip_neg.py", "w": False, "h": False, "tests": 1, "mutants": -1},
        {"tool": "tools/skip_zero.py", "w": False, "h": False, "tests": 1, "mutants": 0},
        {"tool": "tools/run_me.py", "w": False, "h": False, "tests": 1, "mutants": 2}])
    set_control(repo, results={"tools/run_me.py": {"status": "clean", "killed": 2,
                                                   "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert [c["target"] for c in calls(repo)] == ["tools/run_me.py"]
    assert [r["tool"] for r in banked(state)] == ["tools/run_me.py"]


def test_a_timeout_is_recorded_as_unaudited_not_as_zero_survivors(repo, monkeypatch, capsys):
    """The whole point of the status field. A tool that never finished has NO result;
    recording it as 0 survivors would promote silence into evidence."""
    state = write_targets(repo, [{"tool": "tools/slow.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 5}])
    set_control(repo, sleep={"tools/slow.py": 30},
                results={"tools/slow.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.TOOL_TIMEOUT = 1
    mod.run_sweep(state)
    capsys.readouterr()

    r = banked(state)[0]
    assert r["status"] == "UNAUDITED_TIMEOUT"
    assert r.get("survived") is None, "a timeout must not carry a survivor count"
    assert "NOT clean" in r["note"]


def test_unparseable_engine_output_is_recorded_as_unaudited_with_the_evidence(
        repo, monkeypatch, capsys):
    """Keeping stdout/stderr is what makes an unattended failure diagnosable the next
    morning instead of just absent."""
    state = write_targets(repo, [{"tool": "tools/boom.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 5}])
    set_control(repo, run_garbage="Traceback: the engine died")
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()

    r = banked(state)[0]
    assert r["status"] == "UNAUDITED_ERROR"
    assert r.get("survived") is None
    assert "the engine died" in r["stdout"]
    assert r["error"]


# --- 5. resume must not lose or redo work -----------------------------------

def test_banked_tools_are_skipped_on_resume(repo, monkeypatch, capsys):
    state = write_targets(repo, [
        {"tool": "tools/done.py", "w": False, "h": False, "tests": 1, "mutants": 3},
        {"tool": "tools/todo.py", "w": False, "h": False, "tests": 1, "mutants": 3}])
    (state / "baseline.jsonl").write_text(
        json.dumps({"tool": "tools/done.py", "status": "clean", "survived": 0}) + "\n",
        encoding="utf-8")
    set_control(repo, results={"tools/todo.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert [c["target"] for c in calls(repo)] == ["tools/todo.py"]


def test_resume_appends_and_never_truncates_the_state_file(repo, monkeypatch, capsys):
    """A 5-hour unattended run that loses its history to a reboot is not resumable,
    whatever the docstring says."""
    state = write_targets(repo, [
        {"tool": "tools/done.py", "w": False, "h": False, "tests": 1, "mutants": 3},
        {"tool": "tools/todo.py", "w": False, "h": False, "tests": 1, "mutants": 3}])
    (state / "baseline.jsonl").write_text(
        json.dumps({"tool": "tools/done.py", "status": "clean", "survived": 0}) + "\n",
        encoding="utf-8")
    set_control(repo, results={"tools/todo.py": {"status": "survivors", "survived": 2}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert [r["tool"] for r in banked(state)] == ["tools/done.py", "tools/todo.py"]


def test_blank_lines_in_the_state_file_do_not_break_resume(repo, monkeypatch, capsys):
    """A run killed mid-write leaves ragged output; resume must survive its own crash."""
    state = write_targets(repo, [{"tool": "tools/todo.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 3}])
    (state / "baseline.jsonl").write_text("\n\n", encoding="utf-8")
    set_control(repo, results={"tools/todo.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    assert mod.run_sweep(state) == 0
    capsys.readouterr()
    assert [c["target"] for c in calls(repo)] == ["tools/todo.py"]


# --- 6. the child environment -----------------------------------------------

def test_the_engine_is_run_with_isolation_and_utf8(repo, monkeypatch, capsys):
    """PYTHONIOENCODING is mandatory — every tools/*.py crashes on Unicode without it,
    and unattended that surfaces as a red baseline recorded as a finding. --isolation is
    what catches a test file that only passes inside the suite."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 3}])
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0}})
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    c = calls(repo)[0]
    assert c["encoding"] == "utf-8"
    assert "--isolation" in c["argv"] and "--json" in c["argv"]


def test_a_nonzero_engine_exit_is_still_banked_with_its_result(repo, monkeypatch, capsys):
    """mutation_check exits nonzero when survivors exist. That is the normal case for an
    unmeasured corpus, not an error — treating it as one would bank nothing at all."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 4}])
    set_control(repo, results={"tools/a.py": {"status": "survivors", "survived": 4,
                                              "killed": 0, "_rc": 2}})
    mod = load(repo, monkeypatch)
    assert mod.run_sweep(state) == 0
    capsys.readouterr()
    r = banked(state)[0]
    assert r["rc"] == 2 and r["survived"] == 4 and r["status"] == "survivors"


# --- 7. the exit code and the default command -------------------------------
#
# Both of these started as surviving mutants, and both are the kind that only bite
# unattended. `if args.targets:` forced true turns the bare command -- the one the
# runbook tells you to run -- into a silent no-op that re-enumerates and measures
# nothing. Dropping the return value hands sys.exit(None), which is exit 0: a failed
# 5-hour run that reports success to whatever launched it.

def test_the_bare_command_runs_the_sweep_rather_than_rebuilding_targets(repo, monkeypatch,
                                                                        capsys):
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 3}])
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    rc = mod.main(["--state-dir", str(state)])
    capsys.readouterr()
    assert rc == 0
    assert [c["target"] for c in calls(repo)] == ["tools/a.py"], \
        "no --targets means MEASURE; re-enumerating instead measures nothing"
    assert banked(state), "a bare run must bank results"


def test_a_failed_sweep_exits_nonzero(repo, monkeypatch, capsys):
    """sys.exit(None) is exit 0. An unattended failure that reports success is worse
    than one that reports nothing."""
    mod = load(repo, monkeypatch)
    (repo / "state").mkdir()
    rc = mod.main(["--state-dir", str(repo / "state")])
    capsys.readouterr()
    assert rc == 1


# --- 8. the log is the only observability an unattended run has -------------
#
# There is no UI. The runbook's "check on it" step is `tail -5 sweep.log`, so these
# three lines ARE the progress bar, the resume receipt, and the did-it-finish signal.
# Each was a surviving mutant: all three could be deleted with the suite green.

def test_the_start_line_reports_how_much_is_left_and_how_much_resumed(repo, monkeypatch,
                                                                     capsys):
    state = write_targets(repo, [
        {"tool": "tools/done.py", "w": False, "h": False, "tests": 1, "mutants": 3},
        {"tool": "tools/todo.py", "w": False, "h": False, "tests": 1, "mutants": 3}])
    (state / "baseline.jsonl").write_text(
        json.dumps({"tool": "tools/done.py", "status": "clean"}) + "\n", encoding="utf-8")
    set_control(repo, results={"tools/todo.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    out = capsys.readouterr().out
    assert "1 tools to measure (1 already banked)" in out


def test_each_tool_reports_its_verdict_as_it_lands(repo, monkeypatch, capsys):
    """Per tool, not just at the end: a run killed at hour four must still have told you
    what it learned in hours one through three."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 10}])
    set_control(repo, results={"tools/a.py": {"status": "survivors", "survived": 4,
                                              "killed": 6}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    out = capsys.readouterr().out
    assert "[1/1] tools/a.py: status=survivors survived=4 of 10" in out


def test_a_finished_sweep_says_so(repo, monkeypatch, capsys):
    """Without this line a completed run and a killed one look identical in the log,
    and the difference is whether the remaining tools are unmeasured or clean."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 3}])
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    assert "SWEEP COMPLETE" in capsys.readouterr().out


# --- 10. the 2026-08-26 contamination, in both directions -------------------
#
# One timeout produced 41 consecutive FALSE `isolation_failed` findings, and separately
# hid a real tool mutated on disk for two hours. Both halves are regressions now.

def test_a_justified_allowlist_entry_does_not_delete_the_tool_from_the_corpus(repo,
                                                                              monkeypatch):
    """mutation-allow.json is keyed per MUTANT and mutation_check honours it that way.
    Excluding the whole TOOL meant justifying one mutant silently removed it from
    measurement forever -- it had removed check_public_pii.py, the always-on hook keeping
    real names out of a PUBLIC repo, plus 8 others. Doing the right thing must not delete
    the measurement."""
    add_tool(repo, "guarded", mutants=40)
    (repo / "tools" / "mutation-allow.json").write_text(
        json.dumps({"tools/guarded.py::f::DROP_CALL::abc123": "justified, see incident"}),
        encoding="utf-8")
    mod = load(repo, monkeypatch)
    rows = {r["tool"]: r for r in mod.build_targets()}
    assert "tools/guarded.py" in rows, "an allowlisted MUTANT must not exclude the TOOL"
    assert rows["tools/guarded.py"]["mutants"] == 40


def test_a_timed_out_tool_is_restored_before_the_next_one_starts(repo, monkeypatch, capsys):
    """subprocess.run(timeout=) kills with SIGKILL, which no handler can catch -- so the
    SIGTERM/SIGINT/SIGHUP restore does NOT cover this path. A file left mutated here
    contaminates every measurement after it: on 2026-08-26 conftest then refused every
    later --isolation run and 41 tools were recorded as isolation_failed."""
    state = write_targets(repo, [
        {"tool": "tools/slow.py", "w": True, "h": False, "tests": 1, "mutants": 5},
        {"tool": "tools/next.py", "w": False, "h": False, "tests": 1, "mutants": 2}])
    target = repo / "tools" / "slow.py"
    target.write_text("PRISTINE = 1\n", encoding="utf-8")
    # Backups live in a cache store outside the working tree since 2026-09-01 (iCloud was
    # making conflict copies of a file rewritten dozens of times per second). Plant it
    # through the SAME function repair_stranded reads, or the wreckage is invisible.
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(repo / "_bakstore"))
    import conftest_guard as _g
    backup = _g.backup_path(target)
    backup.parent.mkdir(parents=True, exist_ok=True)

    # exactly the wreckage SIGKILL leaves: target mutated, backup stranded
    def strand():
        backup.write_text("PRISTINE = 1\n", encoding="utf-8")
        target.write_text("MUTATED = 999\n", encoding="utf-8")
    strand()

    set_control(repo, sleep={"tools/slow.py": 30},
                results={"tools/next.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.TOOL_TIMEOUT = 1
    mod.run_sweep(state)
    out = capsys.readouterr().out

    assert target.read_text(encoding="utf-8") == "PRISTINE = 1\n", \
        "the timed-out target must be restored, not left mutated for the rest of the run"
    assert not backup.exists(), "the stranded backup must be cleared"
    assert "REPAIRED" in out, "a silent repair hides that the run was ever contaminated"
    assert banked(state)[0]["repaired"]


def test_the_repair_is_recorded_not_silent(repo, monkeypatch, capsys):
    """An unattended run that quietly fixes itself teaches you the timeout is harmless."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 2}])
    (repo / "tools" / "a.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(repo / "_bakstore"))
    import conftest_guard as _g
    _b = _g.backup_path(repo / "tools" / "a.py")
    _b.parent.mkdir(parents=True, exist_ok=True)
    _b.write_text("X = 1\n", encoding="utf-8")
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert "restored tools/a.py" in banked(state)[0]["repaired"]


def test_a_clean_tool_records_no_repair(repo, monkeypatch, capsys):
    """The field must mean something: present only when wreckage was actually found."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 2}])
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert "repaired" not in banked(state)[0]


def test_the_timeout_cap_covers_the_slowest_known_tool(repo, monkeypatch):
    """pipe_write.py needs 68 minutes and was lost at 45. A cap below it is a cap that
    loses the highest-blast-radius writer in the corpus every single run."""
    mod = load(repo, monkeypatch)
    assert mod.TOOL_TIMEOUT >= 68 * 60


# --- 12. selection must not restate a rule that lives elsewhere -------------
#
# The sweep runs `mutation_check.py <tool>` with NO --tests, so mutation_check's
# map_tests() is what actually decides which tests grade the mutants. Selection used to
# require the exact name tests/scripts/test_<stem>.py, so the two disagreed and 24 tools
# with real tests under other names were dropped as "untested" -- among them
# todo_write.py, which mutates the real data/job-todos.md and has THREE test files.
# Coverage was reported as 76 of 76 while it was really 76 of 145.

def test_a_tool_tested_under_a_prefixed_name_is_selected(repo, monkeypatch):
    """The todo_write.py case: test_todo_write_roundtrip.py and friends exist, but
    test_todo_write.py does not. Note map_tests matches on file CONTENT, not on the
    filename -- only the exact test_<stem>.py is matched by name -- so the covering file
    has to actually mention the module, as a real one does by importing it."""
    (repo / "tools" / "widget.py").write_text("def f(n):\n    return n + 1\n",
                                              encoding="utf-8")
    (repo / "tests" / "scripts" / "test_widget_roundtrip.py").write_text(
        "import widget\n\n\ndef test_a():\n    assert widget.f(1) == 2\n",
        encoding="utf-8")
    set_control(repo, mutants={"tools/widget.py": 12})
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == ["tools/widget.py"]


def test_a_tool_covered_only_by_reference_is_selected(repo, monkeypatch):
    """A test file that drives the tool without being named after it still covers it."""
    (repo / "tools" / "widget.py").write_text("def f(n):\n    return n + 1\n",
                                              encoding="utf-8")
    (repo / "tests" / "scripts" / "test_something_else.py").write_text(
        "import widget\n\n\ndef test_a():\n    assert widget.f(1) == 2\n",
        encoding="utf-8")
    set_control(repo, mutants={"tools/widget.py": 5})
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == ["tools/widget.py"]


def test_a_tool_with_no_test_presence_is_still_excluded(repo, monkeypatch):
    """The gate must still be a gate: broader is not the same as absent."""
    (repo / "tools" / "orphan.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    mod = load(repo, monkeypatch)
    assert [r["tool"] for r in mod.build_targets()] == []


def test_the_test_count_sums_every_mapped_file(repo, monkeypatch):
    """The count feeds the worst-first ranking. Counting one file of three understates
    how much coverage a tool already has and mis-sorts the work list."""
    (repo / "tools" / "widget.py").write_text("def f(n):\n    return n + 1\n",
                                              encoding="utf-8")
    for name, n in (("test_widget.py", 2), ("test_widget_extra.py", 3)):
        (repo / "tests" / "scripts" / name).write_text(
            "import widget\n\n\n" + "".join(
                f"def test_{i}():\n    assert widget.f({i}) == {i + 1}\n\n\n"
                for i in range(n)), encoding="utf-8")
    set_control(repo, mutants={"tools/widget.py": 7})
    mod = load(repo, monkeypatch)
    assert mod.build_targets()[0]["tests"] == 5


def test_selection_agrees_with_mutation_check_on_the_real_repo(repo, monkeypatch):
    """SINGLE SOURCE OF TRUTH guard, per CLAUDE.md: when the same domain rule appears in a
    second tool, it gets one implementation plus a cross-tool parity test. Selection and
    measurement disagreeing is not hypothetical here -- it is the bug this section exists
    for, and a reimplementation would pass every test above while silently diverging."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import mutation_check as mc

    mod = load(REPO_ROOT, monkeypatch)
    selected = {r["tool"] for r in mod.build_targets()}
    expected = {f"tools/{p.name}" for p in sorted((REPO_ROOT / "tools").glob("*.py"))
                if mc.map_tests(p, REPO_ROOT)}
    assert selected == expected, (
        "build_targets must select exactly what mutation_check.map_tests covers; "
        f"only in selection: {selected - expected}; only in map_tests: {expected - selected}")


# --- 13. the real tool, end to end -------------------------------------------

def test_the_real_tool_reports_its_own_self_exclusion(tmp_path):
    """Guards the wiring, not just the function: run the shipped file as the runbook
    documents and confirm mutation_sweep.py is named as non-auditable."""
    r = subprocess.run(
        [sys.executable, str(TOOL), "--targets", "--state-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "tools/mutation_sweep.py" in out["self_excluded"]
    assert "tools/mutation_check.py" in out["self_excluded"]
    assert out["selected"] - len(out["self_excluded"]) == out["auditable"]


# --- 14. quiescing the scheduled jobs for the duration -----------------------
#
# The sweep mutates one tools/*.py at a time. launchd jobs shell into those same files
# every 15 minutes, so an unattended overnight run executes mutants against real Gmail
# and real data files. tests/conftest.py cannot help: it guards pytest, not launchd.
# These tests are all about the RESTORE, because turning jobs off is the easy half.

class SpyQuiesce:
    """Stands in for the job_quiesce module on the loaded sweep."""

    def __init__(self, fail_restore=False):
        self.events = []
        self.fail_restore = fail_restore
        self.DEFAULT_MARKER = Path("marker.json")

    def quiesce(self, repo_root, marker, runner=None):
        self.events.append("quiesce")
        return {"quiesced": ["com.nickmagnuson.jobsearch.gmail-fetch"],
                "failed": [], "notes": []}

    def restore(self, repo_root, marker, runner=None):
        self.events.append("restore")
        failed = ["com.nickmagnuson.jobsearch.gmail-fetch"] if self.fail_restore else []
        return {"restored": [] if failed else ["com.nickmagnuson.jobsearch.gmail-fetch"],
                "failed": failed, "notes": []}


def _one_tool_state(repo):
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 3}])
    set_control(repo, results={"tools/a.py": {"status": "clean", "survived": 0,
                                              "killed": 3}})
    return state


def test_the_scheduled_jobs_are_quiesced_before_the_first_tool_is_mutated(
        repo, monkeypatch, capsys):
    """After the first mutation lands on disk is too late: a 900s job can fire in the
    seconds between. Ordering is asserted against the engine's own call log rather than
    by patching subprocess -- `mod.subprocess` IS the stdlib module, and replacing its
    `run` breaks pytest itself."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    seen = {}

    def record(repo_root, marker, runner=None):
        seen["engine_had_run"] = (repo / "calls.log").exists()
        return {"quiesced": [], "failed": [], "notes": []}

    monkeypatch.setattr(spy, "quiesce", record)
    monkeypatch.setattr(mod, "job_quiesce", spy)
    mod.run_sweep(state)
    capsys.readouterr()

    assert seen["engine_had_run"] is False, "a tool was mutated before the jobs went down"
    assert calls(repo), "the engine must still have run afterwards"


def test_the_jobs_are_restored_when_the_sweep_finishes(repo, monkeypatch, capsys):
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    monkeypatch.setattr(mod, "job_quiesce", spy)
    mod.run_sweep(state)
    capsys.readouterr()
    assert spy.events == ["quiesce", "restore"]


def test_the_jobs_are_restored_even_when_the_sweep_blows_up_mid_run(
        repo, monkeypatch, capsys):
    """The whole risk of this feature. A sweep that dies between the two calls leaves
    Nick's mail fetch silently off, so the restore cannot sit on the happy path."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    monkeypatch.setattr(mod, "job_quiesce", spy)

    def boom(_tool):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(mod, "repair_stranded", boom)
    with pytest.raises(RuntimeError):
        mod.run_sweep(state)
    capsys.readouterr()
    assert "restore" in spy.events


def test_a_failed_restore_is_printed_loudly_rather_than_returned_quietly(
        repo, monkeypatch, capsys):
    """Exit status belongs to the measurement. A job left down is a separate debt and
    has to be visible in the log the next morning."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    monkeypatch.setattr(mod, "job_quiesce", SpyQuiesce(fail_restore=True))
    mod.run_sweep(state)
    out = capsys.readouterr().out
    assert "gmail-fetch" in out
    assert "STILL DOWN" in out.upper()


def test_a_terminating_signal_restores_the_jobs_before_dying(repo, monkeypatch, capsys):
    """launchd SIGTERMs a job it wants gone, and Nick may ctrl-C the run. Neither unwinds
    a `finally` on its own, so without a handler the jobs stay down until the next sweep.

    The handler is fired MID-RUN, which is the only moment it matters: invoked after the
    sweep has already returned it is correctly a no-op, because the restore is
    once-only and the `finally` has already paid the debt."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    monkeypatch.setattr(mod, "job_quiesce", spy)
    installed = {}
    monkeypatch.setattr(mod.signal, "signal",
                        lambda sig, handler: installed.setdefault(sig, handler))

    def sigterm_arrives(_tool):
        installed[mod.signal.SIGTERM](mod.signal.SIGTERM, None)

    monkeypatch.setattr(mod, "repair_stranded", sigterm_arrives)
    with pytest.raises(SystemExit) as exc:
        mod.run_sweep(state)
    capsys.readouterr()

    assert mod.signal.SIGINT in installed, "ctrl-C must be handled too"
    assert exc.value.code == 128 + int(mod.signal.SIGTERM)
    assert spy.events == ["quiesce", "restore"]


def test_the_restore_is_paid_once_not_once_per_exit_path(repo, monkeypatch, capsys):
    """A signal handler AND a `finally` both run on a signalled exit. Bootstrapping a
    job twice is not harmless -- the second call fails, which would report a healthy
    restore as a failed one."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    monkeypatch.setattr(mod, "job_quiesce", spy)
    installed = {}
    monkeypatch.setattr(mod.signal, "signal",
                        lambda sig, handler: installed.setdefault(sig, handler))
    monkeypatch.setattr(mod, "repair_stranded",
                        lambda _t: installed[mod.signal.SIGTERM](mod.signal.SIGTERM, None))
    with pytest.raises(SystemExit):
        mod.run_sweep(state)
    capsys.readouterr()
    assert spy.events.count("restore") == 1


def test_the_log_records_what_was_quiesced_and_restored_even_on_success(
        repo, monkeypatch, capsys):
    """A clean run that prints nothing leaves no evidence the jobs ever went down. The
    morning read of an unattended 10-hour log has to show the takedown AND the restore,
    or 'did it protect the run?' is unanswerable after the fact."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    monkeypatch.setattr(mod, "job_quiesce", SpyQuiesce())
    mod.run_sweep(state)
    out = capsys.readouterr().out

    assert "quiesce" in out.lower() and "restore" in out.lower()
    assert "gmail-fetch" in out, "the log must name the jobs, not just count them"


def test_a_quiesce_that_took_nothing_down_says_so_rather_than_going_quiet(
        repo, monkeypatch, capsys):
    """Zero jobs quiesced means launchctl was unreachable or nothing was loaded. Both are
    worth seeing: the run went ahead unprotected."""
    state = _one_tool_state(repo)
    mod = load(repo, monkeypatch)
    spy = SpyQuiesce()
    monkeypatch.setattr(spy, "quiesce",
                        lambda *a, **k: {"quiesced": [], "failed": [], "notes": []})
    monkeypatch.setattr(spy, "restore",
                        lambda *a, **k: {"restored": [], "failed": [], "notes": []})
    monkeypatch.setattr(mod, "job_quiesce", spy)
    mod.run_sweep(state)
    out = capsys.readouterr().out
    assert "no launchd jobs" in out.lower()


def test_the_quiesce_module_is_self_excluded_like_the_runner_itself(repo, monkeypatch):
    """job_quiesce.py is the sweep's own restore path and must never be a target.

    The parent process holds it in sys.modules for the whole run, so mutating it on disk
    is harmless WHILE the run is alive. The hazard is the next run: a SIGKILL mid-mutation
    leaves a mutated job_quiesce.py on disk, and the following sweep imports it AT STARTUP
    and uses it to restore the launchd jobs the previous run stranded. A mutated restorer
    deciding whether Nick's mail fetch comes back is the one failure this module exists to
    prevent.

    Recorded as mutants:-1 rather than dropped, matching how mutation_sweep and
    mutation_check name themselves - the selected-vs-auditable accounting still has to add
    up, and it stays measurable by running mutation_check.py on it directly.
    """
    # mutants=5 matters: without it the stub engine returns unparseable output and the
    # row is -1 anyway, so the test would pass whether the exclusion existed or not.
    add_tool(repo, "job_quiesce", mutants=5)
    mod = load(repo, monkeypatch)
    rows = {r["tool"]: r for r in mod.build_targets()}
    assert "tools/job_quiesce.py" in rows, "must be recorded, not silently dropped"
    assert rows["tools/job_quiesce.py"]["mutants"] == -1


# --- 15. fields the record used to drop -------------------------------------

def test_a_recovery_done_by_mutation_check_reaches_the_banked_record(
        repo, monkeypatch, capsys):
    """`repaired` only sees wreckage left AFTER a tool finishes. mutation_check recovers a
    previous crash's wreckage at ITS OWN startup, so that path left `repaired: None` and
    the run read as pristine -- an unattended sweep that quietly fixes itself teaches you
    the crash was harmless. Verified by hand 2026-09-01: real stranded wreckage was
    repaired correctly and the banked row said nothing about it."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 2}])
    set_control(repo, results={"tools/a.py": {
        "status": "clean", "killed": 2, "survived": 0,
        "recovered_stranded_file": "restored a file stranded by a previous crashed run"}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    assert banked(state)[0]["recovered_stranded_file"] == \
        "restored a file stranded by a previous crashed run"


def test_a_conftest_refusal_reaches_the_banked_record(repo, monkeypatch, capsys):
    """The omission that made the 2026-08-31 diagnosis hard: the row carried
    `status: isolation_unmeasured` while `isolation_refused` was absent, so the record
    contradicted itself and could not say WHICH file caused the refusal."""
    state = write_targets(repo, [{"tool": "tools/a.py", "w": False, "h": False,
                                  "tests": 1, "mutants": 2}])
    set_control(repo, results={"tools/a.py": {
        "status": "isolation_unmeasured", "killed": 2, "survived": 0,
        "isolation_failures": [], "isolation_refused": ["tests/scripts/test_a.py"]}})
    mod = load(repo, monkeypatch)
    mod.run_sweep(state)
    capsys.readouterr()
    row = banked(state)[0]
    assert row["isolation_refused"] == ["tests/scripts/test_a.py"], \
        "a refusal must name what refused, or the status cannot be explained"
    assert row["status"] == "isolation_unmeasured"
