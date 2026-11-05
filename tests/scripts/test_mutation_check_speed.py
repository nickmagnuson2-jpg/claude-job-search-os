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


def _run(args, cwd, env_extra=None, default=False):
    """Same deterministic environment as test_mutation_check._run: the ambient mutation
    flags and CLAUDECODE are stripped so a nested run measures the tool, not its caller."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("MUTATION_CHECK_ACTIVE", "MUTATION_SWEEP_ACTIVE", "CLAUDECODE",
                        mc.CACHE_DIR_ENV)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["MUTATION_REPO_ROOT"] = str(cwd)
    env.update(env_extra or {})
    # Selection is opt-in since 2026-11-05; most tests here are ABOUT selection, so they
    # opt in. _run_default runs the tool exactly as a caller with no flags does.
    if "--no-line-map" not in args and "--list" not in args and not default:
        args = [*args, "--line-map"]
    # The cache is opt-in too (Nick, 2026-11-05 06:30). Same treatment: the tests written
    # for it opt in; `default=True` runs the tool with neither flag.
    if "--no-cache" not in args and "--list" not in args and not default:
        args = [*args, "--cache"]
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


def test_diff_scope_is_unknown_when_git_cannot_be_asked_or_cannot_diff(tmp_path, monkeypatch):
    """Both git-failure paths return None, which resolve_scope turns into the whole file.
    Self-mutation survivors, 2026-11-04: with either check removed the code read the
    failed call's output as an empty diff, i.e. no mutants in scope."""
    src, _ = _repo(tmp_path)
    monkeypatch.setattr(mc, "_git", lambda args, root, timeout=30: None)
    assert mc.diff_scope(src, "HEAD", tmp_path) is None
    def fake(args, root, timeout=30):              # the file exists, the diff fails
        rc = 0 if args[0] == "cat-file" else 128
        return subprocess.CompletedProcess(args, rc, "", "fatal")
    monkeypatch.setattr(mc, "_git", fake)
    assert mc.diff_scope(src, "HEAD", tmp_path) is None, "a failed diff is not an empty one"


def test_function_spans_lists_every_def_including_nested_and_async():
    import ast
    tree = ast.parse(SUBJECT + "\nasync def g():\n    def h():\n        pass\n")
    assert sorted(mc._function_spans(tree)) == [(1, 4), (7, 10), (12, 14), (13, 14)]


def test_a_multi_line_call_or_return_is_in_scope_from_any_of_its_lines():
    """A diff that touches only the 2nd or 3rd line of a call or a return must still
    select its mutant (self-mutation survivor, 2026-11-04: with the span dropped, the
    fallback (line, line) covers the first line only)."""
    import ast
    src = "def f(x):\n    g(\n        x,\n    )\n    return (x\n            + 1)\n"
    tree = ast.parse(src)
    spans = mc.mutant_spans(tree)
    assert spans[(2, 4, "DROP_CALL")] == (2, 4)
    assert spans[(5, 4, "RETURN_NONE")] == (5, 6)
    funcs = mc._function_spans(tree)
    only_tail = {"changed": {3, 6}, "deleted_at": set()}
    assert mc.in_scope(spans[(2, 4, "DROP_CALL")], only_tail, funcs)
    assert mc.in_scope(spans[(5, 4, "RETURN_NONE")], only_tail, funcs)


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


def test_a_changed_mapped_test_scopes_the_lines_it_runs_not_nothing(tmp_path):
    """A commit that only weakens a test must not report no_changed_mutants: the mutation
    rule exists for that commit (job-search-ee for Nick, 2026-11-04). The scope becomes the
    changed code plus the lines the changed tests execute, resolved after the line map."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text() + "def test_cap_again(): assert double(102) == -1\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "diff" and s["changed"] == set()
    assert s["pending_tests"] == ["tests/scripts/test_subject.py"]
    assert s["pending_test_names"] == {"tests/scripts/test_subject.py": {"test_cap_again"}}


def test_editing_an_existing_test_measures_the_whole_file(tmp_path):
    """The edited test may stop executing a line its old version checked, and the line
    map only sees the new version (Codex 110526 A.F1): so the whole file."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text().replace("assert classify(-1) == 'neg'", "assert classify(1) == 'pos'"))
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "existing test edited: test_neg" in s["reason"], s


def test_a_module_level_change_in_a_test_file_measures_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    t.write_text("import os\n" + t.read_text() + "def test_new(): assert double(4) == 8\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "module-level test code changed" in s["reason"], s


def test_resolve_test_lines_filters_by_test_name_and_strips_params(tmp_path):
    a = tmp_path / "tests" / "test_a.py"
    m = {3: {f"{a}::t1[x]"}, 4: {f"{a}::t2"}}
    assert mc.resolve_test_lines(m, ["tests/test_a.py"], tmp_path,
                                 {"tests/test_a.py": {"t1"}}) == {3}


def test_a_deleted_test_file_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    extra = tmp_path / "tests" / "scripts" / "test_subject_extra.py"
    extra.write_text("from subject import double\ndef test_cap(): assert double(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "extra")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    extra.unlink()
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "deleted test file" in s["reason"], s


def _front_repo(tmp_path, delete_front=False, tests=None):
    """Codex round 3, 8.F3: tools/front.py imports the target; tests/test_front.py imports
    front and never spells the target's name. Committed as the base, then the test (and,
    with delete_front, front.py too) is deleted and nothing else changes."""
    src, t = _repo(tmp_path, tests=tests)
    (tmp_path / "tools" / "front.py").write_text(
        "import subject\ndef cap(n): return subject.double(n)\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text(f"import sys; sys.path.insert(0, r'{tmp_path / 'tools'}')\n"
                  "import front\ndef test_cap(): assert front.cap(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "front")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    ft.unlink()
    if delete_front:
        (tmp_path / "tools" / "front.py").unlink()
    return src, t


def test_a_deleted_test_that_reached_the_target_through_another_module_forces_the_whole_file(
        tmp_path):
    src, t = _front_repo(tmp_path)
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "deleted test file" in s["reason"], s
    assert "tests/scripts/test_front.py" in s["reason"], s


def test_the_same_holds_when_the_module_in_between_was_deleted_too(tmp_path):
    """front.py is gone from the working tree, so only its BASE source shows the path."""
    src, t = _front_repo(tmp_path, delete_front=True)
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "tests/scripts/test_front.py" in s["reason"], s


def test_a_chain_through_a_deleted_module_below_a_live_one_is_still_followed(tmp_path):
    """Codex round 4 F1: test_front imports LIVE front, front imports DELETED gone, gone
    imports the target. The closure has to cross from the base graph back into the
    working tree; a loop over deleted modules alone added gone and never revisited front."""
    src, t = _repo(tmp_path)
    tools = tmp_path / "tools"
    (tools / "gone.py").write_text("import subject\ndef cap(n): return subject.double(n)\n")
    (tools / "front.py").write_text("import gone\ndef cap(n): return gone.cap(n)\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text("import front\ndef test_cap(): assert front.cap(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "chain")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    ft.unlink()
    (tools / "gone.py").unlink()                     # front.py stays, still `import gone`
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/scripts/test_front.py", "deleted test file") in got["whole"], got
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "tests/scripts/test_front.py" in s["reason"], s


def test_a_live_module_is_read_as_it_is_now_not_at_the_base(tmp_path):
    """Only DELETED modules come from the base. front.py is edited in this diff to import
    the target; a deleted test that imports front reaches the target through the CURRENT
    front, so it forces the whole file (more measurement, the safe side)."""
    src, t = _repo(tmp_path)
    front = tmp_path / "tools" / "front.py"
    front.write_text("def cap(n): return -1\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text("import front\ndef test_cap(): assert front.cap(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "front without subject")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    front.write_text("import subject\ndef cap(n): return subject.double(n)\n")
    ft.unlink()
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/scripts/test_front.py", "deleted test file") in got["whole"], got


def test_an_import_the_old_regex_saw_still_links_a_live_module(tmp_path):
    """Codex round 5 F1: an import inside a string run by exec() is invisible to ast but
    was seen by covering_names' regex. The graph must never hold fewer edges than the
    regex gave, or a deleted test reaching the target that way is dropped."""
    src, t = _repo(tmp_path)
    (tmp_path / "tools" / "front.py").write_text(
        'code = """\nimport subject\n"""\nexec(code)\n'
        "def cap(n): return subject.double(n)\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text("import front\ndef test_cap(): assert front.cap(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "exec import")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    ft.unlink()
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/scripts/test_front.py", "deleted test file") in got["whole"], got


def test_a_live_module_that_dropped_its_import_keeps_its_base_edge(tmp_path):
    """Codex round 6 F1 (P0): front.py imported the target at the base and no longer
    does; test_front.py, which reached the target through front, is deleted in the same
    diff. At the base map_tests selected that test, so the deletion must force the whole
    file. A graph built from front's CURRENT source alone loses the edge and drops it."""
    src, t = _repo(tmp_path)
    front = tmp_path / "tools" / "front.py"
    front.write_text("import subject\ndef cap(n): return subject.double(n)\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text("import front\ndef test_cap(): assert front.cap(101) == -1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "front imports subject")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    front.write_text("def cap(n): return -1\n")          # the import is gone
    ft.unlink()
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/scripts/test_front.py", "deleted test file") in got["whole"], got
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file", s


def test_any_deleted_test_file_forces_the_whole_file_even_an_unrelated_one(tmp_path):
    """The flat rule (2026-11-05, replacing four rounds of import-graph inference: 8.F3,
    11.F1, 12.F1, 13.F1): a deleted test file is never inferred to be irrelevant. Whole
    file is a superset of the diff, so this costs time on a rare kind of diff, never a
    verdict."""
    src, t = _repo(tmp_path)
    other = tmp_path / "tests" / "scripts" / "test_other.py"
    other.write_text("import json\ndef test_x(): assert json.loads('1') == 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "other")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    other.unlink()
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file", s
    assert "tests/scripts/test_other.py" in s["reason"] and "deleted test file" in s["reason"]


def _unmapped_repo(tmp_path, before):
    """test_front.py is NOT in the mapped list; committed with `before` as the base."""
    src, t = _repo(tmp_path)
    (tmp_path / "tools" / "front.py").write_text(
        "import subject\ndef cap(n): return subject.double(n)\n")
    ft = tmp_path / "tests" / "scripts" / "test_front.py"
    ft.write_text(before)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "front")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return src, t, ft


FRONT_TESTS = ("import front\n"
               "def test_cap(): assert front.cap(101) == -1\n"
               "def test_small(): assert front.cap(3) == 6\n")


def test_an_unmapped_test_file_that_lost_a_test_forces_the_whole_file(tmp_path):
    """The edit shape of the same class: test_front.py drops `import front` and its cap
    test. It no longer names front, so it is not mapped, and before this rule a changed
    UNMAPPED test file fell through every branch and was ignored."""
    src, t, ft = _unmapped_repo(tmp_path, FRONT_TESTS)
    ft.write_text("def test_small(): assert 3 * 2 == 6\n")
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert [w for w in got["whole"] if w[0] == "tests/scripts/test_front.py"], got
    assert mc.resolve_scope(_args(), src, tmp_path, [t])["mode"] == "whole_file"


def test_an_unmapped_test_file_with_an_edited_test_or_module_code_forces_the_whole_file(
        tmp_path):
    src, t, ft = _unmapped_repo(tmp_path, FRONT_TESTS)
    ft.write_text(FRONT_TESTS.replace("front.cap(101) == -1", "True"))
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert [w for w in got["whole"] if "test_cap" in w[1]], got
    ft.write_text(FRONT_TESTS.replace("import front", "import front as f2\nimport front"))
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert [w for w in got["whole"] if w[0] == "tests/scripts/test_front.py"], got


def test_an_unmapped_test_file_names_up_to_three_lost_tests(tmp_path):
    """The reason names the tests that went, and says "..." only past three."""
    five = "".join(f"def test_{c}(): pass\n" for c in "abcde")
    src, t, ft = _unmapped_repo(tmp_path, five)
    ft.write_text("def test_e(): pass\n")                     # four removed
    why = dict(mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")["whole"])
    assert why["tests/scripts/test_front.py"] == (
        "unmapped test file: test removed or edited: test_a, test_b, test_c, ...")
    ft.write_text("def test_b(): pass\ndef test_c(): pass\ndef test_d(): pass\n"
                  "def test_e(): pass\n")                     # one removed
    why = dict(mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")["whole"])
    assert why["tests/scripts/test_front.py"] == (
        "unmapped test file: test removed or edited: test_a")


def test_an_add_only_change_to_an_unmapped_test_file_keeps_the_diff_scope(tmp_path):
    """Appending a test cannot remove coverage, so it must not cost a whole-file run."""
    src, t, ft = _unmapped_repo(tmp_path, FRONT_TESTS)
    ft.write_text(FRONT_TESTS + "def test_more(): assert front.cap(4) == 8\n")
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert got["whole"] == [], got
    assert mc.resolve_scope(_args(), src, tmp_path, [t])["mode"] == "diff"


def test_a_new_unmapped_test_file_keeps_the_diff_scope(tmp_path):
    src, t = _repo(tmp_path)
    (tmp_path / "tests" / "scripts" / "test_new.py").write_text("def test_n(): pass\n")
    assert mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")["whole"] == []


def test_an_unmapped_test_file_that_no_longer_parses_forces_the_whole_file(tmp_path):
    src, t, ft = _unmapped_repo(tmp_path, FRONT_TESTS)
    ft.write_text(FRONT_TESTS + "def broken(:\n")
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/scripts/test_front.py", "test file does not parse") in got["whole"], got


def test_any_deleted_file_under_tests_forces_the_whole_file(tmp_path):
    """A deleted fixture or helper that no mapped test names, and that is not a conftest:
    the flat rule still measures the whole file rather than infer it was unused."""
    src, t = _repo(tmp_path)
    fx = tmp_path / "tests" / "fixtures" / "rows.json"
    fx.parent.mkdir(parents=True)
    fx.write_text("[]\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "fixture")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    fx.unlink()
    got = mc.changed_test_inputs("HEAD", tmp_path, [t], "subject")
    assert ("tests/fixtures/rows.json", "file deleted under tests/") in got["whole"], got


def test_e2e_deleting_the_only_indirect_test_never_exits_0_on_zero_mutants(tmp_path):
    """The exact shape from 8.F3: before the fix this run reported no_changed_mutants and
    exited 0, on the commit that removed the only test that killed the cap mutants."""
    src, t = _front_repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
    """)                     # only test_front.py checked the cap branch, and it is deleted
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path, default=True)
    d = json.loads(r.stdout)
    assert d["status"] != "no_changed_mutants", d
    assert d["scope"]["mode"] == "whole_file", d["scope"]
    assert d["survived"] > 0 and r.returncode == 2, (d["survived"], r.returncode)


def test_a_test_function_removed_from_a_mapped_file_forces_the_whole_file(tmp_path):
    """The lines a deleted test ran are in no CURRENT map, so they cannot bound it."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text().replace(
        "def test_double_cap(): assert double(101) == -1\n", ""))
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "test removed: test_double_cap" in s["reason"], s


def test_resolve_test_lines_keeps_only_lines_the_changed_files_run(tmp_path):
    a, b = tmp_path / "tests" / "test_a.py", tmp_path / "tests" / "test_b.py"
    lm_ = {3: {f"{a}::t1"}, 4: {f"{b}::t2"}, 5: {f"{a}::t3", f"{b}::t4"}}
    assert mc.resolve_test_lines(lm_, ["tests/test_a.py"], tmp_path) == {3, 5}


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


def test_nested_shims_reach_the_real_sitecustomize_and_still_record(tmp_path):
    """A mapping pass whose tests run mutation_check starts a second pass, so two shim
    directories sit on PYTHONPATH. Each must skip every shim directory, not only its own,
    or the two run each other until startup fails (found 2026-11-04 measuring the recorder
    with its own tests: the inner pass went red with no test named)."""
    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\nf()\n", encoding="utf-8")
    dirs = []
    for name in ("inner", "outer"):
        d = tmp_path / name
        d.mkdir()
        (d / "_mutation_linemap.py").write_bytes(Path(lm.__file__).read_bytes())
        (d / "sitecustomize.py").write_text(lm.SITECUSTOMIZE, encoding="utf-8")
        dirs.append(str(d))
    out = tmp_path / "out"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(dirs), lm.ENV_TARGET: str(target),
           lm.ENV_OUT: str(out), lm.ENV_TEST: "/t.py::test_x"}
    r = subprocess.run([sys.executable, str(target)], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0 and "Error in sitecustomize" not in r.stderr, r.stderr[-800:]
    assert lm.read_records(str(out)).get("/t.py::test_x") == {1, 2, 3}   # def, body, call


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


def test_any_file_in_the_checkout_changes_the_key_but_caches_do_not(tmp_path):
    """A test can read any file in the checkout, so the key covers the checkout (round 2
    A.F3/B.F2/C.F1/D.F2): a root-level fixture, an untracked file. The interpreter's and
    this tool's own caches do not count, or no rerun could ever hit."""
    src, t = _repo(tmp_path)
    ctx = lambda: mc.cache_context(src, src.read_text(), [t], tmp_path)
    first = ctx()
    (tmp_path / ".pytest_cache").mkdir(); (tmp_path / ".pytest_cache" / "v").write_text("1")
    (tmp_path / ".mutation-cache").mkdir(); (tmp_path / ".mutation-cache" / "k").write_text("1")
    assert ctx() == first
    (tmp_path / "fixture.json").write_text("{}")
    second = ctx()
    assert second != first, "an untracked root-level file is an input"
    (tmp_path / "fixture.json").write_text('{"a": 1}')
    assert ctx() != second, "and so is its content"


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
    # Neither fallback happened, so neither is said (the notes are only for fallbacks).
    assert "scope: whole file" not in r.stderr and "per-line selection off" not in r.stderr
    assert d["fast_tests"] is not None, "the file-tier probe ran"


def test_e2e_a_commit_that_only_weakens_a_test_still_finds_the_survivor(tmp_path):
    """The weakened test runs line 9 (`return -1`) and no longer checks it. The scope is
    the lines the changed test executes, so that survivor is found, and the lines no
    changed test reaches are not measured."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text().replace("assert double(101) == -1", "double(101)"))
    _git(tmp_path, "commit", "-qam", "weaken the cap test")    # HEAD moves, base does not
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "whole_file", d["scope"]       # an EDITED test (A.F1)
    assert "existing test edited: test_double_cap" in d["scope"]["reason"]
    assert d["scope"]["tests_changed"] == ["tests/scripts/test_subject.py"]
    assert d["status"] == "survivors" and r.returncode == 2
    assert any(s["line"] == 9 for s in d["survivors"]), d["survivors"]


def test_e2e_changed_tests_with_no_line_map_measure_the_whole_file(tmp_path):
    """The map pass fails here (a test that refuses the recorder's environment) while the
    baseline passes, and a mapped test changed: no map, no bound, so the whole file."""
    src, t = _repo(tmp_path)
    # An ADDED test (so the scope waits on the map), with no module-level change.
    t.write_text(t.read_text() + "\ndef test_no_recorder():\n    import os\n"
                 "    assert 'MUTATION_LINEMAP_TARGET' not in os.environ\n")
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert d["selection"]["line_map"]["status"] == "map_pass_failed"
    assert d["scope"]["mode"] == "whole_file", d["scope"]
    assert "line map could not be built" in d["scope"]["reason"]
    assert d["scope"]["mutants_in_scope"] == d["scope"]["mutants_in_file"]
    # Said on stderr, both the selection fallback and the scope fallback.
    assert "per-line selection off (map_pass_failed)" in r.stderr, r.stderr[-600:]
    assert "scope: whole file (mapped tests changed and the line map could not be built" \
        in r.stderr, r.stderr[-600:]
    # A whole-file scope carries no diff-only fields that would read as a diff.
    assert "changed_lines" not in d["scope"] and "deleted_after_lines" not in d["scope"]


def test_e2e_a_diff_with_no_mutable_line_is_not_reported_as_ok(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT + "\n# a comment only\n", encoding="utf-8")
    d = json.loads(_run([str(src), "--tests", str(t), "--json"], tmp_path).stdout)
    assert d["status"] == "no_changed_mutants" and d["scope"]["mutants_in_scope"] == 0
    assert d["fast_tests"] is None and d["selection"]["line_map"]["status"] == "skipped", \
        "nothing in scope: neither the probe nor the mapping pass runs"



def _cross_file_state_repo(tmp_path):
    """State set by a test in ONE file that a test in ANOTHER file needs. Per-file prefixes
    cannot see it (the file tier never could either), so a covering subset fails alone on
    unmutated source: an invalid subset, decided by the full set."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        import shared_state
        from subject import classify, double
        def test_a(): shared_state.READY.append(1); assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
        def test_reads_cap():   # sees cap mutants by READING the source, runs no cap line
            text = open(r'{tmp_path / "tools" / "subject.py"}').read()
            assert "if n > 100:\\n        return -1" in text
    """)
    # Only test_b (the OTHER file) executes `return -1`, so that line's subset is test_b
    # alone, which fails unmutated without test_a's state: an invalid subset.
    (tmp_path / "tests" / "scripts" / "shared_state.py").write_text("READY = []\n")
    later = tmp_path / "tests" / "scripts" / "test_subject_z_later.py"
    later.write_text(f"import sys; sys.path.insert(0, r'{tmp_path / 'tools'}')\n"
                     "import shared_state\nfrom subject import double\n"
                     "def test_b():\n    assert shared_state.READY, 'needs test_a first'\n"
                     "    double(101)\n")
    return src, [t, later]


def test_e2e_an_order_dependent_subset_cannot_produce_a_false_kill(tmp_path):
    """test_b (another file) needs state test_a sets, and only test_b executes double()'s
    cap branch. Alone it fails unmutated, so its subset is invalid and the full set decides:
    verdicts must equal a --no-line-map run's."""
    src, tests = _cross_file_state_repo(tmp_path)
    args = [str(src), "--whole-file", "--no-cache", "--tests", *map(str, tests), "--json"]
    d = json.loads(_run(args, tmp_path).stdout)
    plain = json.loads(_run(args[:-1] + ["--no-line-map", "--json"], tmp_path).stdout)
    assert d["selection"]["line_map"]["status"] == "ok"
    # The subset is a true prefix of the run, so test_a (other file) runs first: valid.
    assert d["selection"]["subset_invalid"] == 0, d["selection"]
    assert {(x["line"], x["op"]) for x in d["survivors"]} == \
        {(x["line"], x["op"]) for x in plain["survivors"]}
    assert d["killed"] == plain["killed"]
    assert plain["selection"]["line_map"]["status"] == "skipped"
    assert plain["selection"]["subset_runs"] == 0, "--no-line-map means no subset runs"

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
    assert "scope: whole file" not in first.stderr, "asked for, so not a fallback"
    assert a["cache"]["hits"] == 0 and a["cache"]["writes"] > 0
    assert a["survived"] > 0 and first.returncode == 2
    sel = a["selection"]
    assert sel["subsets_validated"] < sel["subset_kills"], "a subset is validated once"
    assert sel["subset_kind_from_full_subset"] == 0, "assertion kills decided directly"
    assert sel["subset_weak_rechecked"] == 0, "no crash kill, so nothing to recheck"
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
    """--strict fails on weak kills, so a subset must never make a kill WEAKER than the old
    path. The crashing test runs first in the file, so the subset's first failure is a
    crash; the recheck (same subset, no -x) finds the assertion. The old path stops at the
    crash and calls it weak: the branch may be more lenient there, never stricter."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_a_crashes(): classify(1).upper()
        def test_z_asserts(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    d = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                         "--json"], tmp_path).stdout)
    plain = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                             "--tests", str(t), "--json"], tmp_path).stdout)
    assert d["selection"]["subset_weak_rechecked"] >= 1, d["selection"]
    assert d["selection"]["subset_kind_from_full_subset"] >= 1, d["selection"]
    assert {w["key"] for w in d["weak_kills"]} <= {w["key"] for w in plain["weak_kills"]}
    # The recheck must run against the MUTANT: on the unmutated source (which validation
    # leaves on disk) a crash kill would turn into a false survivor.
    assert d["survived"] == plain["survived"] and d["killed"] == plain["killed"]

def test_e2e_the_sweep_argv_still_measures_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path,
             env_extra={mc.SWEEP_ENV: "1"})
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "whole_file"
    assert d["scope"]["mutants_in_scope"] == d["scope"]["mutants_in_file"]
    assert "scope: whole file" not in r.stderr, "the sweep's whole file is not a fallback"


def test_e2e_an_unresolvable_base_measures_the_whole_file_and_says_so(tmp_path):
    """No origin/main ref: the default base cannot be resolved. The run must measure the
    whole file AND say so on stderr, never measure nothing quietly."""
    src, t = _repo(tmp_path)
    _git(tmp_path, "update-ref", "-d", "refs/remotes/origin/main")
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    r = _run([str(src), "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert d["scope"]["mode"] == "whole_file"
    assert "scope: whole file (could not resolve merge-base" in r.stderr, r.stderr[-600:]


def test_e2e_changed_tests_with_no_map_path_measure_the_whole_file(tmp_path):
    """--list and --no-line-map build no line map, so changed tests cannot be bounded by
    the lines they run: the whole file, with the reason. --list also prints the scope."""
    src, t = _repo(tmp_path)
    t.write_text(t.read_text() + "def test_added(): assert double(102) == -1\n")
    listed = _run([str(src), "--tests", str(t), "--list"], tmp_path)
    d = json.loads(listed.stdout)
    assert d["scope"]["mode"] == "whole_file" and "--list runs no tests" in d["scope"]["reason"]
    assert d["mutants"] == d["scope"]["mutants_in_file"] > 0
    nomap = json.loads(_run([str(src), "--tests", str(t), "--no-line-map", "--json"],
                            tmp_path).stdout)
    assert nomap["scope"]["mode"] == "whole_file"
    assert "(--no-line-map)" in nomap["scope"]["reason"]


# --- self-mutation batch (a): fallback and error paths (2026-11-04) ----------------------

def test_a_deletion_scopes_a_mutant_away_from_the_gap_in_the_same_function():
    # The function rule, not the neighbour rule: line 10 is two lines from the gap.
    assert mc.in_scope((10, 10), {"changed": set(), "deleted_at": {8}}, [(7, 10)])


def test_changed_test_inputs_is_unknown_when_git_cannot_list(tmp_path, monkeypatch):
    src, t = _repo(tmp_path)
    monkeypatch.setattr(mc, "_git", lambda args, root, timeout=30: None)
    assert mc.changed_test_inputs("HEAD", tmp_path, [t], "subject") is None
    monkeypatch.setattr(mc, "_git", lambda args, root, timeout=30:
                        subprocess.CompletedProcess(args, 128, "", "fatal"))
    assert mc.changed_test_inputs("HEAD", tmp_path, [t], "subject") is None


def test_resolve_scope_measures_the_whole_file_when_git_cannot_answer(tmp_path, monkeypatch):
    src, t = _repo(tmp_path)
    monkeypatch.setattr(mc, "diff_scope", lambda *a: None)
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "could not diff" in s["reason"]
    monkeypatch.undo()
    monkeypatch.setattr(mc, "changed_test_inputs", lambda *a, **k: None)
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "could not list changed test files" in s["reason"]


def test_a_plain_diff_says_plain_diff_and_has_no_pending_tests(tmp_path):
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["reason"] == "diff against the base" and "pending_tests" not in s


def test_an_unreadable_mapped_test_file_does_not_break_the_listing(tmp_path):
    src, t = _repo(tmp_path)
    t.write_text(t.read_text() + "def test_added(): assert double(102) == -1\n")
    out = mc.changed_test_inputs(mc.resolve_base(tmp_path, None), tmp_path,
                                 [tmp_path / "missing_test.py", t], "subject")
    assert out["tests"] == ["tests/scripts/test_subject.py"]


def test_a_deleted_conftest_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    conf = tmp_path / "tests" / "conftest.py"
    conf.write_text("X = 1\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "conftest")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    conf.unlink()
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "conftest changed (deleted)" in s["reason"], s


def test_deleting_an_unrelated_test_or_data_file_now_measures_the_whole_file(tmp_path):
    """Inverted 2026-11-05 by the flat rule: "unrelated" was an inference, and four review
    rounds each found a way it was wrong. A deletion under tests/ costs a whole-file run."""
    src, t = _repo(tmp_path)
    other = tmp_path / "tests" / "scripts" / "test_unrelated.py"
    other.write_text("def test_x(): assert 1\n")
    data = tmp_path / "tests" / "fixtures" / "unused.json"
    data.parent.mkdir(parents=True)
    data.write_text("{}\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "extra")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    other.unlink(); data.unlink()
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file", s
    assert s["tests_changed"] == ["tests/fixtures/unused.json",
                                  "tests/scripts/test_unrelated.py"], s


def test_a_new_mapped_test_file_counts_every_test_in_it(tmp_path):
    src, t = _repo(tmp_path)
    new = tmp_path / "tests" / "scripts" / "test_subject_more.py"
    new.write_text("from subject import classify\ndef test_more(): assert classify(2)\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t, new])
    assert s["pending_tests"] == ["tests/scripts/test_subject_more.py"]
    assert s["pending_test_names"] == {"tests/scripts/test_subject_more.py": None}


def test_a_mapped_test_file_that_no_longer_parses_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    t.write_text(t.read_text() + "\ndef broken(:\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "does not parse" in s["reason"], s
    assert s["tests_changed"] == ["tests/scripts/test_subject.py"]


def test_many_removed_tests_are_abbreviated_and_the_file_is_listed_once(tmp_path):
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_a(): assert classify(1) == 'pos'
        def test_b(): assert classify(-1) == 'neg'
        def test_c(): assert double(3) == 6
        def test_d(): assert double(101) == -1
        def test_e(): assert double(0) == 0
    """)
    t.write_text("\n".join(l for l in t.read_text().splitlines()
                           if not l.startswith(("def test_a", "def test_b", "def test_c",
                                                "def test_d"))) + "\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert "test removed: test_a, test_b, test_c, ..." in s["reason"], s["reason"]
    assert s["tests_changed"] == ["tests/scripts/test_subject.py"]


def test_more_than_five_wholesale_inputs_are_abbreviated(tmp_path):
    src, t = _repo(tmp_path)
    for i in range(6):
        d = tmp_path / "tests" / f"pkg{i}"
        d.mkdir()
        (d / "conftest.py").write_text("X = 1\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["reason"].endswith("; ...") and len(s["tests_changed"]) == 6


def test_test_classes_are_tracked_per_method_and_by_their_other_lines():
    plain = "class TestX:\n    def test_a(self):\n        assert 1\n    def test_b(self):\n        assert 2\n"
    tests, rest = mc._test_sources(plain)
    assert set(tests) == {"TestX::test_a", "TestX::test_b"}
    edited, rest2 = mc._test_sources(plain.replace("assert 2", "assert 3"))
    assert edited["TestX::test_a"] == tests["TestX::test_a"]
    assert edited["TestX::test_b"] != tests["TestX::test_b"] and rest2 == rest
    _, rest3 = mc._test_sources(plain.replace("class TestX:\n", "class TestX:\n    K = 1\n"))
    assert rest3 != rest, "a class attribute is the class's other code"
    _, rest4 = mc._test_sources("import pytest\n@pytest.mark.slow\n" + plain)
    assert rest4 != mc._test_sources("import pytest\n" + plain)[1], "decorators count"


def test_the_recorder_being_unimportable_means_no_map(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mutation_linemap", None)     # import raises
    tool = tmp_path / "m.py"
    tool.write_text("X = 1\n")
    t = tmp_path / "test_m.py"
    t.write_text("def test_x(): assert True\n")
    mapping, info = mc.build_line_map(tool, [t], timeout=60)
    assert mapping is None and info["status"].startswith("recorder_unavailable")


def test_a_green_pass_that_never_runs_the_target_yields_no_map(tmp_path):
    tool = tmp_path / "m.py"
    tool.write_text("def f():\n    return 1\n")
    t = tmp_path / "test_m.py"
    t.write_text(f"def test_reads(): assert 'return 1' in open(r'{tool}').read()\n")
    mapping, info = mc.build_line_map(tool, [t], timeout=60)
    assert mapping is None and info["status"] == "map_empty"


def test_file_sha_of_a_missing_file_is_a_fixed_marker(tmp_path):
    assert mc._file_sha(tmp_path / "nope") == "unreadable"


def test_local_deps_skips_an_unreadable_module(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "a.py").write_text("import b\n")
    (tools / "b.py").write_text("import c\n")
    (tools / "c.py").write_text("X = 1\n")
    os.chmod(tools / "b.py", 0)
    try:
        deps = mc._local_deps(["import a\n"], tools)
    finally:
        os.chmod(tools / "b.py", 0o644)
    assert {p.name for p in deps} == {"a.py", "b.py"}, "b is a dep; c is not reachable"


def test_cache_context_tells_apart_every_support_and_outside_root_file(tmp_path):
    src, t = _repo(tmp_path)
    s1 = tmp_path / "tests" / "aaa_helpers.py"; s1.write_text("A = 1\n")
    s2 = tmp_path / "tests" / "zzz_data.txt"; s2.write_text("z")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    o1, o2 = outside / "test_o1.py", outside / "test_o2.py"
    o1.write_text("def test_1(): pass\n"); o2.write_text("def test_2(): pass\n")
    tests = [t, o1, o2]

    def ctx():
        return mc.cache_context(src, src.read_text(), tests, tmp_path)
    first = ctx()
    for path, text in ((s1, "A = 2\n"), (o1, "def test_1(): assert 1\n")):
        before = path.read_text()
        path.write_text(text)
        assert ctx() != first, f"{path.name} must count on its own"
        path.write_text(before)
    cache = tmp_path / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "x.cpython-314.pyc").write_bytes(b"\0")
    (tmp_path / "tests" / "subdir_only").mkdir()
    assert ctx() == first, "bytecode and bare directories are not inputs"


def test_a_tool_that_only_a_test_imports_is_in_the_key(tmp_path):
    src, t = _repo(tmp_path)
    helper = tmp_path / "tools" / "only_tests_use_me.py"
    helper.write_text("X = 1\n")
    t.write_text("import only_tests_use_me\n" + t.read_text())
    first = mc.cache_context(src, src.read_text(), [t], tmp_path)
    helper.write_text("X = 2\n")
    assert mc.cache_context(src, src.read_text(), [t], tmp_path) != first


def _run_env(cwd):
    """The environment _run gives the child, which is what its cache key hashes."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("MUTATION_CHECK_ACTIVE", "MUTATION_SWEEP_ACTIVE", "CLAUDECODE",
                        mc.CACHE_DIR_ENV)}
    env.update(PYTHONIOENCODING="utf-8", MUTATION_REPO_ROOT=str(cwd))
    return env


def _seed_cache(tmp_path, src, t, verdicts, monkeypatch):
    """Write cache entries for every mutant of `src`, keyed exactly as main() keys them:
    under the child's own environment, since the key includes it."""
    import ast
    env = _run_env(tmp_path)
    for k in list(os.environ):
        if k not in env:
            monkeypatch.delenv(k)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    original = src.read_text(encoding="utf-8")
    ctx = mc.cache_context(src, original, [t], tmp_path)
    store = tmp_path / ".mutation-cache"
    for line, col, op in mc.enumerate_mutants(ast.parse(original)):
        verdict, kind = verdicts(line, op)
        key = mc.cache_key(ctx, {"line": line, "col": col, "op": op})
        mc.cache_put(key, verdict, kind, root=tmp_path)
    return store


def test_e2e_cached_weak_and_unclassified_kills_are_replayed_as_such(tmp_path, monkeypatch):
    src, t = _repo(tmp_path)
    # Three kinds, so each replay branch has to pick out exactly its own.
    kinds = lambda line, op: ("killed", "error" if line < 5 else
                              "unknown" if line < 9 else "assertion")
    _seed_cache(tmp_path, src, t, kinds, monkeypatch)
    import ast
    seeded = [kinds(l, o)[1] for l, _, o in mc.enumerate_mutants(ast.parse(src.read_text()))]
    d = json.loads(_run([str(src), "--whole-file", "--tests", str(t), "--json"],
                        tmp_path).stdout)
    assert d["cache"]["hits"] == d["scope"]["mutants_in_file"] > 0
    assert d["killed"] == len(seeded)
    assert d["weak_kill_count"] == seeded.count("error") > 0
    assert all(w["cached"] for w in d["weak_kills"])
    assert d["unclassified_kills"] == seeded.count("unknown") > 0
    assert seeded.count("assertion") > 0, "the fixture must seed assertion kills too"


def test_e2e_a_cached_survivor_on_the_allowlist_is_reported_allowlisted(tmp_path, monkeypatch):
    src, t = _repo(tmp_path)
    listed = json.loads(_run([str(src), "--whole-file", "--tests", str(t), "--list"],
                             tmp_path).stdout)
    keys = [e["key"] for e in listed["catalog"] if e["line"] == 9]
    # The allowlist is written BEFORE seeding: the cache key covers the checkout.
    (tmp_path / "tools" / "mutation-allow.json").write_text(
        json.dumps({keys[0]: "test: an equivalent mutant, reason written"}))
    _seed_cache(tmp_path, src, t, lambda line, op: ("survived", "") if line == 9
                else ("killed", "assertion"), monkeypatch)
    r = _run([str(src), "--whole-file", "--tests", str(t), "--json"], tmp_path)
    d = json.loads(r.stdout)
    assert [a["key"] for a in d["allowlisted_detail"]] == keys[:1]
    assert d["allowlisted_detail"][0]["cached"] is True
    assert {s["key"] for s in d["survivors"]} == set(keys[1:]), "the rest still fail"


def test_e2e_a_kill_only_a_crash_sees_stays_weak(tmp_path):
    """The 'pos' branch is executed only by a test that crashes on a mutant and asserts
    nothing. Every kill there is weak on the old path, and must stay weak here: the
    recheck may promote a kill to assertion only when an assertion actually failed."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_a_crashes(): classify(1).upper()
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    d = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                         "--json"], tmp_path).stdout)
    plain = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                             "--tests", str(t), "--json"], tmp_path).stdout)
    weak = sorted(w["key"] for w in d["weak_kills"])
    assert weak and weak == sorted(w["key"] for w in plain["weak_kills"]), (weak, plain)


def test_e2e_a_mutant_only_an_unexecuting_file_sees_is_killed_by_the_full_set(tmp_path):
    """The file tier holds only files that execute the target; a mutant that survives it
    must still meet the file that only READS the source. Same property as the file-tier
    test in test_mutation_check.py, here with the line map on."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    reads = tmp_path / "tests" / "scripts" / "test_reads_subject.py"
    reads.write_text(f"def test_neg_written():\n"
                     f"    assert \"return 'neg'\" in open(r'{src}').read()\n")
    d = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                         str(reads), "--json"], tmp_path).stdout)
    assert d["fast_tests"] == [str(t)] and d["full_reruns"] >= 1, d["fast_tests"]
    assert not [s for s in d["survivors"] if "'neg'" in s["source"]], d["survivors"]



def test_e2e_after_an_invalid_subset_the_full_set_measures_the_mutant(tmp_path):
    """Validation leaves the UNMUTATED source on disk. When it rules a subset invalid, the
    mutant must be written back before the full set runs, or the full set measures the
    original and reports a false SURVIVED. Here every mutant on the invalid subset is one
    the full set kills (test_double checks the cap), so a missing rewrite shows."""
    src, tests = _cross_file_state_repo(tmp_path)
    args = [str(src), "--whole-file", "--no-cache", "--tests", *map(str, tests), "--json"]
    d = json.loads(_run(args, tmp_path).stdout)
    plain = json.loads(_run(args[:-1] + ["--no-line-map", "--json"], tmp_path).stdout)
    assert d["survived"] == plain["survived"] == 0, (d["survivors"], plain["survivors"])
    assert d["killed"] == plain["killed"]

def test_e2e_the_full_set_reruns_only_mutants_that_survive_the_file_tier(tmp_path):
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_double(): assert double(3) == 6 and double(101) == -1
    """)
    reads = tmp_path / "tests" / "scripts" / "test_reads_subject.py"
    reads.write_text(f"def test_neg_written():\n"
                     f"    assert \"return 'neg'\" in open(r'{src}').read()\n")
    d = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                         "--tests", str(t), str(reads), "--json"], tmp_path).stdout)
    total = d["killed"] + d["survived"] + d["allowlisted"]
    assert d["fast_tests"] == [str(t)]
    assert 1 <= d["full_reruns"] < total, (d["full_reruns"], total)


# --- Codex 110526 P0 fixes -----------------------------------------------------------------

def test_imported_names_reads_package_imports():
    names = mc._imported_names("from tools import policy, other\nimport tools.helper\n"
                               "from tools.deep import x\nimport plain\n")
    assert {"policy", "other", "helper", "deep", "x", "plain", "tools"} <= names


def test_local_deps_follows_tools_package_imports(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "policy.py").write_text("X = 1\n")
    (tools / "helper.py").write_text("Y = 1\n")
    deps = mc._local_deps(["from tools import policy\nimport tools.helper\n"], tools)
    assert {p.name for p in deps} == {"policy.py", "helper.py"}


def test_a_changed_tools_helper_a_test_imports_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    helper = tmp_path / "tools" / "policy_helper.py"
    helper.write_text("STRICT = True\n")
    t.write_text("import policy_helper\n" + t.read_text())
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "helper")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    helper.write_text("STRICT = False\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "imported helper changed" in s["reason"], s


def test_a_changed_root_level_file_a_test_names_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    cfg = tmp_path / "subject_policy.json"
    cfg.write_text("{}\n")
    t.write_text(t.read_text() + "\nPOLICY = 'subject_policy.json'\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "cfg")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    cfg.write_text('{"strict": false}\n')
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "referenced fixture changed" in s["reason"], s


def test_the_cache_key_covers_the_test_environment_but_not_volatile_variables(tmp_path,
                                                                             monkeypatch):
    src, t = _repo(tmp_path)

    def ctx():
        return mc.cache_context(src, src.read_text(), [t], tmp_path)
    first = ctx()
    monkeypatch.setenv("STRICT_VALUE", "1")
    assert ctx() != first, "an environment variable a test reads is a verdict input"
    monkeypatch.delenv("STRICT_VALUE")
    monkeypatch.setenv("PWD", "/somewhere/else")
    monkeypatch.setenv("TERM_SESSION_ID", "x-123")
    assert ctx() == first, "shell bookkeeping must not empty the cache"


def test_e2e_state_an_earlier_test_sets_cannot_turn_into_a_false_kill(tmp_path):
    """Codex 110526 A.F4 / B.F2. test_a sets module state and never executes value(), so
    the line map gives value()'s mutants to test_b alone. Alone, test_b fails on the
    RETURN_NONE mutant (it passes unmutated, so the subset validates), but in the full
    order test_a has set `ready` and the mutant SURVIVES. Running each covering test with
    the tests before it in its file removes the false kill."""
    src, t = _repo(tmp_path, source="ready = False\n\n\ndef value():\n    return 1\n",
                   tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        import subject
        def test_a(): subject.ready = True
        def test_b(): assert subject.value() == 1 or subject.ready
    """)
    d = json.loads(_run([str(src), "--whole-file", "--no-cache", "--tests", str(t),
                         "--json"], tmp_path).stdout)
    plain = json.loads(_run([str(src), "--whole-file", "--no-line-map", "--no-cache",
                             "--tests", str(t), "--json"], tmp_path).stdout)
    survived = {(s["line"], s["op"]) for s in plain["survivors"]}
    assert (5, "RETURN_NONE") in survived, plain["survivors"]
    assert {(s["line"], s["op"]) for s in d["survivors"]} == survived, d["survivors"]


def test_more_than_three_edited_tests_are_abbreviated(tmp_path):
    src, t = _repo(tmp_path)
    text = t.read_text()
    for a, b in (("== 'pos'", "== 'pos' or 1"), ("== 'neg'", "== 'neg' or 1"),
                 ("== 6", "== 6 or 1"), ("== -1", "== -1 or 1")):
        text = text.replace(a, b)
    t.write_text(text)
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file", s
    assert "existing test edited: test_double, test_double_cap, test_neg, ..." in s["reason"], s


def test_subset_argv_runs_the_file_prefix_in_order_and_keeps_unseen_ids():
    order = ["/r/t1.py::a", "/r/t1.py::b", "/r/t1.py::c", "/r/t2.py::x", "/r/t2.py::y"]
    argv = mc.subset_argv({"/r/t1.py::b", "/r/t2.py::x", "/r/t3.py::new"}, order)
    assert argv == ["/r/t1.py::a", "/r/t1.py::b", "/r/t1.py::c", "/r/t2.py::x",
                    "/r/t3.py::new"], argv    # a true prefix of the run, across files
    assert mc.subset_argv({"/r/t1.py::c"}, []) == ["/r/t1.py::c"], "no order: ids as before"


def test_mutation_and_git_variables_are_in_the_cache_key(tmp_path, monkeypatch):
    src, t = _repo(tmp_path)
    ctx = lambda: mc.cache_context(src, src.read_text(), [t], tmp_path)
    first = ctx()
    monkeypatch.setenv("MUTATION_STRICT_ASSERTS", "1")
    assert ctx() != first


def test_a_helper_the_tests_reach_through_another_module_forces_the_whole_file(tmp_path):
    src, t = _repo(tmp_path)
    (tmp_path / "tools" / "front.py").write_text("import deep_policy\n")
    deep = tmp_path / "tools" / "deep_policy.py"
    deep.write_text("STRICT = True\n")
    t.write_text("import front\n" + t.read_text())
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "deps")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    deep.write_text("STRICT = False\n")
    s = mc.resolve_scope(_args(), src, tmp_path, [t])
    assert s["mode"] == "whole_file" and "imported helper changed" in s["reason"], s


def test_a_long_prefix_falls_back_to_its_files_in_run_order(monkeypatch):
    monkeypatch.setattr(mc, "MAX_ID_ARGV_CHARS", 30)
    order = ["/r/t2.py::a", "/r/t1.py::b", "/r/t2.py::c", "/r/t3.py::d"]
    assert mc.subset_argv({"/r/t2.py::c"}, order) == ["/r/t2.py", "/r/t1.py"], \
        "first-appearance order, no duplicates, nothing after the last covering test"


def test_repo_state_outside_git_is_unique_so_nothing_replays(tmp_path):
    a, b = mc._repo_state(tmp_path), mc._repo_state(tmp_path)
    assert a.startswith("unknown-") and a != b


def test_e2e_by_default_no_subset_runs_but_changed_tests_still_get_their_map(tmp_path):
    """Selection is OFF by default (2026-11-05): no subset runs. A test-first change still
    builds the line map, because the scope needs the lines the added test executes."""
    src, t = _repo(tmp_path)
    src.write_text(SUBJECT.replace("n > 100", "n >= 101"), encoding="utf-8")
    d = json.loads(_run([str(src), "--tests", str(t), "--json"], tmp_path, default=True).stdout)
    assert d["selection"]["enabled"] is False and d["selection"]["subset_runs"] == 0
    assert d["selection"]["line_map"]["status"] == "skipped", "a plain diff needs no map"
    t.write_text(t.read_text() + "def test_added(): assert double(102) == -1\n")
    d = json.loads(_run([str(src), "--tests", str(t), "--json"], tmp_path, default=True).stdout)
    assert d["scope"]["mode"] == "diff" and d["scope"]["test_lines"], d["scope"]
    assert d["selection"]["line_map"]["status"] == "ok" and d["selection"]["subset_runs"] == 0


def test_e2e_by_default_the_cache_is_neither_read_nor_written(tmp_path, monkeypatch):
    """The cache is OFF by default (Nick, 2026-11-05 06:30): a seeded cache that says every
    mutant was killed must not hide a survivor, and the run must not create the cache dir.
    --cache reads the same seeded entries; --no-cache wins over --cache."""
    src, t = _repo(tmp_path, tests=f"""\
        import sys; sys.path.insert(0, r'{tmp_path / "tools"}')
        from subject import classify, double
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_double(): assert double(3) == 6
    """)                                               # nothing checks the cap branch
    store = _seed_cache(tmp_path, src, t, lambda line, op: ("killed", "assertion"),
                        monkeypatch)
    before = sorted(p.name for p in store.iterdir())
    args = [str(src), "--whole-file", "--tests", str(t), "--json"]
    plain = _run(args, tmp_path, default=True)
    d = json.loads(plain.stdout)
    assert d["cache"] == {"enabled": False, "hits": 0, "writes": 0, "dir": None}, d["cache"]
    assert d["survived"] > 0 and plain.returncode == 2, "the seeded KILLED was not read"
    assert sorted(p.name for p in store.iterdir()) == before, "and nothing was written"
    on = json.loads(_run([*args, "--cache"], tmp_path, default=True).stdout)
    assert on["cache"]["enabled"] is True
    assert on["cache"]["hits"] == on["scope"]["mutants_in_file"] > 0
    both = json.loads(_run([*args, "--cache", "--no-cache"], tmp_path, default=True).stdout)
    assert both["cache"]["enabled"] is False and both["cache"]["hits"] == 0


def test_e2e_a_default_run_creates_no_cache_directory(tmp_path):
    src, t = _repo(tmp_path)
    r = _run([str(src), "--whole-file", "--tests", str(t), "--json"], tmp_path, default=True)
    assert r.returncode in (0, 2), r.stderr
    assert not (tmp_path / ".mutation-cache").exists()
