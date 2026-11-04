"""Tests for mutation_check's speed changes (2026-11-04): diff scope, per-line test
selection, and the verdict cache.

The property that matters most is not speed. Each change must leave every VERDICT what a
full run would give:
  - selection may only make a kill faster; a SURVIVED is only ever reported after the full
    mapped set ran, and a kill by a subset counts only if that subset passes unmutated;
  - the cache reports a cached survivor as a survivor;
  - the overnight sweep keeps measuring whole files.

Unit tests call the functions directly. The end-to-end tests spawn mutation_check once each,
sequentially (one mutation slot at a time).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_check.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))
import mutation_check as mc  # noqa: E402
import mutation_linemap as lm  # noqa: E402


def _run(args, cwd, env_extra=None):
    """Same deterministic environment as test_mutation_check._run: the ambient mutation
    flags and CLAUDECODE are stripped so a nested run measures the tool, not its caller."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("MUTATION_CHECK_ACTIVE", "MUTATION_SWEEP_ACTIVE", "CLAUDECODE",
                        mc.CACHE_DIR_ENV)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["MUTATION_REPO_ROOT"] = str(cwd)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=str(root), check=True, capture_output=True)


SUBJECT = textwrap.dedent("""\
    def classify(n):
        if n > 0:
            return 'pos'
        return 'neg'


    def double(n):
        if n > 100:
            return -1
        return n * 2
""")


def _repo(tmp_path, source=SUBJECT, tests=None):
    """A git repo with tools/subject.py committed, and refs/remotes/origin/main at that
    commit, so the default base (merge-base with origin/main) resolves."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "scripts").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text(source, encoding="utf-8")
    t = tmp_path / "tests" / "scripts" / "test_subject.py"
    t.write_text(textwrap.dedent(tests or f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
        def test_double_cap(): assert double(101) == -1
    """), encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return src, t


def _args(**kw):
    return SimpleNamespace(whole_file=kw.get("whole_file", False), base=kw.get("base"))


# --- 1. diff scope: units ---------------------------------------------------------------

def test_parse_hunks_reads_changed_lines_and_pure_deletions_in_new_numbers():
    changed, deleted = mc.parse_hunks("@@ -3,0 +4,2 @@\n@@ -10,2 +11,0 @@\n@@ -20 +21 @@\n")
    assert changed == {4, 5, 21}
    assert deleted == {11}


def test_an_if_mutant_spans_its_test_not_its_body():
    import ast
    tree = ast.parse("if (a and\n        b):\n    x = 1\n    y = 2\n")
    spans = mc.mutant_spans(tree)
    assert spans[(1, 0, "IF_TRUE")] == (1, 2)


def test_a_changed_line_scopes_only_mutants_overlapping_it():
    scope = {"changed": {9}, "deleted_at": set()}
    assert mc.in_scope((9, 9), scope, [(7, 10)])
    assert not mc.in_scope((2, 2), scope, [(1, 4), (7, 10)])


def test_a_deletion_scopes_every_mutant_in_the_enclosing_function():
    scope = {"changed": set(), "deleted_at": {8}}
    functions = [(1, 4), (7, 10)]
    assert mc.in_scope((9, 9), scope, functions), "same function as the gap"
    assert mc.in_scope((7, 8), scope, functions)
    assert not mc.in_scope((2, 2), scope, functions), "another function is untouched"


def test_a_deletion_outside_every_function_scopes_only_its_two_neighbours():
    scope = {"changed": set(), "deleted_at": {5}}
    assert mc.in_scope((6, 6), scope, [(1, 3)])
    assert not mc.in_scope((9, 9), scope, [(1, 3)])


def test_default_scope_is_the_diff_against_merge_base_with_origin_main(tmp_path):
    src, _ = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 100"), encoding="utf-8")
    s = mc.resolve_scope(_args(), src, tmp_path)
    assert s["mode"] == "diff" and s["changed"] == {8}


def test_the_sweep_keeps_the_whole_file_without_changing_its_argv(tmp_path, monkeypatch):
    src, _ = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 100"), encoding="utf-8")
    monkeypatch.setenv(mc.SWEEP_ENV, "1")
    assert mc.resolve_scope(_args(), src, tmp_path)["mode"] == "whole_file"
    # An explicit --base wins, even under the sweep.
    assert mc.resolve_scope(_args(base="HEAD"), src, tmp_path)["mode"] == "diff"


def test_whole_file_flag_and_an_unresolvable_base_both_measure_everything(tmp_path):
    src, _ = _repo(tmp_path)
    assert mc.resolve_scope(_args(whole_file=True), src, tmp_path)["mode"] == "whole_file"
    s = mc.resolve_scope(_args(base="no-such-ref"), src, tmp_path)
    assert s["mode"] == "whole_file" and "could not resolve" in s["reason"]


def test_a_file_missing_at_the_base_is_measured_whole(tmp_path):
    _repo(tmp_path)
    new = tmp_path / "tools" / "fresh.py"
    new.write_text("def f():\n    return 1\n", encoding="utf-8")
    s = mc.resolve_scope(_args(), new, tmp_path)
    assert s["mode"] == "whole_file" and "does not exist at the base" in s["reason"]


def test_a_changed_mapped_test_makes_the_scope_the_whole_file(tmp_path):
    """A commit that only weakens a test must not report no_changed_mutants: the mutation
    rule exists for that commit (job-search-ee for Nick, 2026-11-04)."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text().replace("assert double(101) == -1", "double(101)"))
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file"
    assert s["tests_changed"] == ["tests/scripts/test_subject.py"]


def test_a_changed_conftest_or_imported_helper_makes_the_scope_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    (tmp_path / "tests" / "conftest.py").write_text("X = 1\n")      # untracked counts
    assert mc.resolve_scope(_args(), src, tmp_path, [t])["tests_changed"] == [
        "tests/conftest.py"]
    (tmp_path / "tests" / "conftest.py").unlink()
    helper = tmp_path / "tests" / "scripts" / "subject_helpers.py"
    helper.write_text("Y = 1\n")
    t.write_text("import subject_helpers\n" + t.read_text())
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert "tests/scripts/subject_helpers.py" in s["tests_changed"]


def test_a_changed_fixture_a_mapped_test_names_makes_the_scope_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    fixture = tmp_path / "tests" / "fixtures" / "subject_cases.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("[1]\n")
    t.write_text(t.read_text() + "\nCASES = 'tests/fixtures/subject_cases.json'\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "fixture")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    fixture.write_text("[]\n")                                    # weakened
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["tests_changed"] == ["tests/fixtures/subject_cases.json"]
    other = tmp_path / "tests" / "fixtures" / "unnamed.json"
    other.write_text("{}\n")
    fixture.write_text("[1]\n")
    assert mc.resolve_scope(_args(), src, tmp_path, [t])["mode"] == "diff", \
        "a fixture no mapped test names does not widen the scope"


def test_an_unrelated_test_file_change_keeps_the_diff_scope(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    (tmp_path / "tests" / "scripts" / "test_other.py").write_text("def test_x(): pass\n")
    (tmp_path / "tests" / "scripts" / "unimported_helper.py").write_text("Z = 1\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "diff" and s["changed"] == {8}


def test_outside_a_git_checkout_the_scope_is_the_whole_file(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    s = mc.resolve_scope(_args(), tmp_path / "x.py", tmp_path)
    assert s["mode"] == "whole_file"


# --- 2. per-line selection: units -------------------------------------------------------

def test_by_line_drops_code_that_ran_outside_any_test():
    m = lm.by_line({lm.OUTSIDE: {1, 2}, "/t.py::a": {2, 3}})
    assert m == {2: {"/t.py::a"}, 3: {"/t.py::a"}}


def test_absolute_nodeid_makes_the_path_absolute_and_keeps_the_rest():
    assert lm.absolute_nodeid("tests/t.py::test_a[x::y]", "/r") == "/r/tests/t.py::test_a[x::y]"
    assert lm.absolute_nodeid("/abs/t.py::a", "/r") == "/abs/t.py::a"


def test_read_records_merges_processes_and_skips_junk(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps({"a": [1, 2]}))
    (tmp_path / "2.json").write_text(json.dumps({"a": [3], "b": ["x", 4]}))
    (tmp_path / "3.json").write_text("{not json")
    (tmp_path / "4.json").write_text(json.dumps([1, 2]))
    assert lm.read_records(str(tmp_path)) == {"a": {1, 2, 3}, "b": {4}}


def test_covering_ids_unions_every_line_of_the_mutant():
    assert mc.covering_ids((2, 3), {2: {"a"}, 3: {"b"}, 4: {"c"}}) == {"a", "b"}
    assert mc.covering_ids((9, 9), {2: {"a"}}) == set()


def test_a_long_subset_falls_back_to_its_files(monkeypatch):
    monkeypatch.setattr(mc, "MAX_ID_ARGV_CHARS", 30)
    ids = {"/r/t1.py::test_a", "/r/t1.py::test_b", "/r/t2.py::test_c"}
    assert mc.subset_argv(ids) == ["/r/t1.py", "/r/t2.py"]


def test_the_line_map_attributes_lines_to_tests_including_a_subprocess(tmp_path):
    """The case that makes selection worth having here: most tools are tested by running
    them as a script. A line only a subprocess executes must still name its test."""
    tool = tmp_path / "tools" / "cli.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(textwrap.dedent("""\
        import sys
        def a():
            return 1
        def b():
            return 2
        if __name__ == '__main__':
            print(b())
    """), encoding="utf-8")
    t = tmp_path / "test_cli.py"
    t.write_text(textwrap.dedent(f"""\
        import subprocess, sys
        sys.path.insert(0, r'{tool.parent}')
        import cli
        def test_a(): assert cli.a() == 1
        def test_script():
            out = subprocess.run([sys.executable, r'{tool}'], capture_output=True,
                                 text=True).stdout
            assert out.strip() == '2'
    """), encoding="utf-8")
    mapping, info = mc.build_line_map(tool, [t], timeout=120)
    assert info["status"] == "ok", info
    tid_a, tid_s = f"{t}::test_a", f"{t}::test_script"
    assert mapping[3] == {tid_a}, "a()'s body ran only in test_a"
    assert tid_s in mapping[5], "b()'s body ran only in the subprocess"
    assert tid_a not in mapping.get(5, set())


def test_a_red_mapping_pass_yields_no_map(tmp_path):
    tool = tmp_path / "m.py"
    tool.write_text("def f():\n    return 1\n", encoding="utf-8")
    t = tmp_path / "test_m.py"
    t.write_text("def test_red():\n    assert False\n", encoding="utf-8")
    mapping, info = mc.build_line_map(tool, [t], timeout=120)
    assert mapping is None and info["status"] == "map_pass_failed"


# --- 3. cache: units --------------------------------------------------------------------

def test_cache_round_trips_and_ignores_anything_but_a_verdict(tmp_path):
    mc.cache_put("ab" + "0" * 62, "survived", "", root=tmp_path)
    assert mc.cache_get("ab" + "0" * 62, root=tmp_path)["verdict"] == "survived"
    bad = tmp_path / ".mutation-cache" / "cd" / ("cd" + "0" * 62 + ".json")
    bad.parent.mkdir(parents=True)
    bad.write_text(json.dumps({"verdict": "timeout"}))
    assert mc.cache_get("cd" + "0" * 62, root=tmp_path) is None


def test_cache_context_changes_with_every_input_it_names(tmp_path):
    src, t = _repo(tmp_path)
    helper = tmp_path / "tools" / "helper.py"
    helper.write_text("X = 1\n", encoding="utf-8")
    src.write_text("import helper\n" + SUBJECT, encoding="utf-8")
    fixture = tmp_path / "tests" / "fixtures" / "data.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("a", encoding="utf-8")
    original = src.read_text(encoding="utf-8")

    def ctx():
        return mc.cache_context(src, src.read_text(encoding="utf-8"), [t], tmp_path)

    first = ctx()
    assert ctx() == first, "same inputs, same key"
    for path, text in ((t, t.read_text() + "\n# edit\n"), (helper, "X = 2\n"),
                       (fixture, "b"), (src, original + "\n# edit\n")):
        before = path.read_text(encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        assert ctx() != first, f"editing {path.name} must change the key"
        path.write_text(before, encoding="utf-8")
    assert ctx() == first


def test_an_unmapped_test_file_does_not_change_the_key(tmp_path):
    src, t = _repo(tmp_path)
    first = mc.cache_context(src, src.read_text(), [t], tmp_path)
    (tmp_path / "tests" / "scripts" / "test_unrelated.py").write_text("def test_x(): pass\n")
    assert mc.cache_context(src, src.read_text(), [t], tmp_path) == first


# --- end to end (one mutation_check process each) ----------------------------------------

def test_e2e_a_diff_run_measures_only_the_changed_function(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "diff"
    assert d["scope"]["changed_lines"] == [8]
    assert 0 < d["scope"]["mutants_in_scope"] < d["scope"]["mutants_in_file"]
    assert d["status"] == "ok" and r.returncode == 0
    assert src.read_text() == SUBJECT.replace("n > 100", "n >= 101"), "restored"


def test_e2e_a_commit_that_only_weakens_a_test_still_finds_the_survivor(tmp_path):
    src, t = _repo(tmp_path)
    t.write_text(t.read_text().replace("assert double(101) == -1", "double(101)"))
    _git(tmp_path, "commit", "-qam", "weaken the cap test")    # HEAD moves, base does not
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "whole_file", d["scope"]
    assert d["scope"]["tests_changed"] == ["tests/scripts/test_subject.py"]
    assert d["status"] == "survivors" and r.returncode == 2
    assert any(s["line"] == 9 for s in d["survivors"]), d["survivors"]


def test_e2e_a_diff_with_no_mutable_line_is_not_reported_as_ok(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT + "\n# a comment only\n", encoding="utf-8")
    d = json.loads(_run([str(src), "--tests", str(t), "--json"], tmp_path).stdout)
    assert d["status"] == "no_changed_mutants" and d["scope"]["mutants_in_scope"] == 0


def test_e2e_an_order_dependent_subset_cannot_produce_a_false_kill(tmp_path):
    """The only way selection could make the verdict WORSE: a test that passes in the full
    run but fails when run alone. Here test_b needs state test_a sets, and test_b is the
    only test that executes `double`'s cap branch. Run alone it fails on unmutated code, so
    without validation every mutant on that branch would be scored KILLED. The full set
    cannot see the RETURN_NONE mutant on `return -1`, so the right verdict is SURVIVED."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        STATE = []
        def test_a(): STATE.append(1); assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
        def test_b():
            assert STATE, 'needs test_a to have run first'
            double(101)
    """)
    d = json.loads(_run([str(src), "--whole-file", "--tests", str(t), "--json"],
                        tmp_path).stdout)
    assert d["selection"]["line_map"]["status"] == "ok"
    assert d["selection"]["subset_invalid"] >= 1
    survived = {(s["line"], s["op"]) for s in d["survivors"]}
    assert (9, "RETURN_NONE") in survived, d["survivors"]
    plain = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                             "--tests", str(t), "--json"], tmp_path).stdout)
    assert {(s["line"], s["op"]) for s in plain["survivors"]} == survived
    assert plain["killed"] == d["killed"]


def test_e2e_a_line_no_test_reaches_is_decided_by_the_full_set(tmp_path):
    """Never SURVIVED because selection ran zero tests: the only test that sees the 'neg'
    branch reads the source text, so the recorder attributes that line to nothing."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from pathlib import Path
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_double(): assert double(3) == 6 and double(101) == -1
        def test_neg_is_written():
            assert "return 'neg'" in Path(r'{tmp_path / "tools" / "subject.py"}').read_text()
    """)
    d = json.loads(_run([str(src), "--whole-file", "--tests", str(t), "--json"],
                        tmp_path).stdout)
    assert d["selection"]["no_covering_test"] >= 1
    assert not [s for s in d["survivors"] if "'neg'" in s["source"]], d["survivors"]


def test_e2e_the_cache_replays_verdicts_and_keeps_survivors(tmp_path):
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
    """)                                               # nothing checks the cap branch
    first = _run([str(src), "--whole-file", "--tests", str(t), "--json"], tmp_path)
    a = json.loads(first.stdout)
    assert a["cache"]["hits"] == 0 and a["cache"]["writes"] > 0
    assert a["survived"] > 0 and first.returncode == 2
    again = _run([str(src), "--whole-file", "--tests", str(t), "--json"], tmp_path)
    b = json.loads(again.stdout)
    assert b["cache"]["hits"] == a["cache"]["writes"]
    assert again.returncode == 2, "a cached survivor still fails the run"
    assert [s["key"] for s in b["survivors"]] == [s["key"] for s in a["survivors"]]
    assert all(s.get("cached") for s in b["survivors"])
    assert b["killed"] == a["killed"]
    off = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                           "--json"], tmp_path).stdout)
    assert off["cache"]["hits"] == 0 and off["cache"]["enabled"] is False
    t.write_text(t.read_text() + "\ndef test_cap(): assert double(101) == -1\n")
    c = json.loads(_run([str(src), "--whole-file", "--tests", str(t), "--json"],
                        tmp_path).stdout)
    assert c["cache"]["hits"] == 0, "an edited test file is a different input"
    assert c["survived"] < a["survived"]


def test_e2e_an_edit_made_during_a_subset_run_is_left_as_found(tmp_path, monkeypatch):
    """The divergence guard on the NEW path: the other session's edit lands while a
    per-line subset runs against a mutant, and the validation write that follows must
    refuse to overwrite it, exactly as write_mutant does between mutants."""
    store = tmp_path / "store"
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(store))
    mark = "# line added by another session\n"
    src, t = _repo(tmp_path, tests=f"""\
        import os, pathlib, sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        TARGET = pathlib.Path(r'{tmp_path / "tools" / "subject.py"}')
        def test_aa_another_session_edits_the_file():
            assert classify(1) == 'pos'          # executes the target, so subsets select it
            text = TARGET.read_text(encoding="utf-8")
            if os.environ.get("MUTATION_CHECK_TARGET") and {mark!r} not in text:
                TARGET.write_text(text + {mark!r}, encoding="utf-8")
                raise AssertionError("fail, so the subset kill goes to validation")
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    original = src.read_text(encoding="utf-8")
    r = _run([str(src), "--whole-file", "--tests", str(t), "--json"], tmp_path,
             env_extra={"MUTATION_BACKUP_DIR": str(store)})
    d = json.loads(r.stdout)
    assert d["code"] == "target_diverged" and r.returncode == 1, d
    left = src.read_text(encoding="utf-8")
    assert left.endswith(mark) and left != original + mark, "the mutant plus the edit"
    assert sorted(store.glob("*" + mc.ASIDE_ORIGINAL))[0].read_text() == original


def test_e2e_a_crash_kill_by_a_subset_gets_its_kind_from_the_old_tiers(tmp_path):
    """--strict fails on weak kills, so a subset must not make a kill weaker than the old
    path would. Here the file runs an ASSERTING test first; the subset (ids sorted by
    name) would run the CRASHING one first. Measured on pipe_write before this existed:
    3 mutants newly weak."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_z_asserts(): assert classify(1) == 'pos'
        def test_a_crashes(): classify(1).upper()
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    d = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                         "--json"], tmp_path).stdout)
    assert d["selection"]["subset_weak_rechecked"] >= 1, d["selection"]
    plain = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                             "--tests", str(t), "--json"], tmp_path).stdout)
    assert sorted(w["key"] for w in d["weak_kills"]) == \
        sorted(w["key"] for w in plain["weak_kills"])


def test_e2e_the_sweep_argv_still_measures_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path,
             env_extra={mc.SWEEP_ENV: "1"})
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "whole_file"
    assert d["scope"]["mutants_in_scope"] == d["scope"]["mutants_in_file"]
