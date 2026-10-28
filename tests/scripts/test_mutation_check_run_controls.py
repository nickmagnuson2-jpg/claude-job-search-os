"""tools/mutation_check.py run controls added 2026-10-28 (output/mutation-parallel-102826/plan.md).

A3: in a linked worktree, test subprocesses never inherit a path into the main checkout.
A5: a progress line every N mutants, --max-hours, and run_seconds in the result.
--strict: the status is decided before the result is printed (simplify.md #8).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_check.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))
import mutation_check as mc  # noqa: E402


def _run(args, cwd, env_extra=None):
    # The same stripped environment as test_mutation_check._run, for the same reasons.
    env = {k: v for k, v in os.environ.items()
           if k not in ("MUTATION_CHECK_ACTIVE", "MUTATION_SWEEP_ACTIVE", "CLAUDECODE")}
    env.update({"PYTHONIOENCODING": "utf-8", "MUTATION_REPO_ROOT": str(cwd)})
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          cwd=str(cwd), env=env)


def _pair(tmp_path, body):
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text("def classify(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n",
                   encoding="utf-8")
    t = tmp_path / "test_subject.py"
    t.write_text(textwrap.dedent(body % (tmp_path / "tools")), encoding="utf-8")
    return src, t


STRONG = """
    import sys; sys.path.insert(0, r'%s')
    from subject import classify
    def test_pos(): assert classify(1) == 'pos'
    def test_neg(): assert classify(-1) == 'neg'
"""
# Kills every mutant (so the status would be "ok") but carries one test with no
# assertion, which only --strict treats as a failure.
STRONG_PLUS_ASSERTION_FREE = """
    import sys; sys.path.insert(0, r'%s')
    from subject import classify
    def test_pos(): assert classify(1) == 'pos'
    def test_neg(): assert classify(-1) == 'neg'
    def test_runs(): classify(0)
"""


# ── A5 ────────────────────────────────────────────────────────────────────────

def test_max_hours_zero_leaves_every_mutant_unfinished_and_fails(tmp_path):
    src, t = _pair(tmp_path, STRONG)
    r = _run([str(src), "--tests", str(t), "--max-hours", "0"], tmp_path)
    d = json.loads(r.stdout)
    assert d["status"] == "incomplete"
    assert d["killed"] == 0 and d["survived"] == 0, "an unrun mutant was scored"
    assert d["unfinished"] >= 2 and len(d["unfinished_mutants"]) == d["unfinished"]
    assert all(k.startswith("tools/subject.py::") for k in d["unfinished_mutants"])
    assert d["max_hours"] == 0
    assert r.returncode == 2, "an unfinished run must not exit as a pass"


def test_without_a_limit_nothing_is_unfinished(tmp_path):
    src, t = _pair(tmp_path, STRONG)
    r = _run([str(src), "--tests", str(t)], tmp_path)
    d = json.loads(r.stdout)
    assert d["status"] == "ok" and d["unfinished"] == 0 and d["unfinished_mutants"] == []
    assert d["max_hours"] is None
    assert isinstance(d["run_seconds"], float) and d["run_seconds"] > 0
    assert r.returncode == 0


def test_a_generous_limit_finishes_the_run(tmp_path):
    src, t = _pair(tmp_path, STRONG)
    d = json.loads(_run([str(src), "--tests", str(t), "--max-hours", "5"], tmp_path).stdout)
    assert d["status"] == "ok" and d["unfinished"] == 0 and d["killed"] >= 2


def test_progress_lines_go_to_stderr(tmp_path):
    src, t = _pair(tmp_path, STRONG)
    r = _run([str(src), "--tests", str(t)], tmp_path, {"MUTATION_PROGRESS_EVERY": "1"})
    lines = [l for l in r.stderr.splitlines() if l.startswith("mutation_check: ")
             and "mutants run" in l]
    d = json.loads(r.stdout)
    total = d["killed"] + d["survived"] + d["timed_out"] + d["allowlisted"]
    assert len(lines) == total - 1, "one line after each mutant but the last"
    assert lines[0].startswith(f"mutation_check: 1/") and "killed" in lines[0]
    assert "mutants run" not in r.stdout, "progress must not corrupt the JSON on stdout"


def test_default_progress_interval_is_25():
    assert mc.PROGRESS_EVERY == int(os.environ.get("MUTATION_PROGRESS_EVERY", "25"))


# ── --strict ──────────────────────────────────────────────────────────────────

def test_strict_failure_is_in_the_printed_status(tmp_path):
    src, t = _pair(tmp_path, STRONG_PLUS_ASSERTION_FREE)
    r = _run([str(src), "--tests", str(t), "--strict"], tmp_path)
    d = json.loads(r.stdout)
    assert d["survived"] == 0 and d["assertion_free_tests"]
    assert d["status"] == "strict_failed" and r.returncode == 2


def test_without_strict_an_assertion_free_test_is_reported_not_failed(tmp_path):
    src, t = _pair(tmp_path, STRONG_PLUS_ASSERTION_FREE)
    r = _run([str(src), "--tests", str(t)], tmp_path)
    d = json.loads(r.stdout)
    assert d["assertion_free_tests"] and d["status"] == "ok" and r.returncode == 0


# ── A3 ────────────────────────────────────────────────────────────────────────

def _main_and_worktree(tmp_path, relative=False):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "w01").mkdir(parents=True)
    wt = tmp_path / "w01"
    wt.mkdir()
    gitdir = (main / ".git" / "worktrees" / "w01")
    (wt / ".git").write_text(f"gitdir: {os.path.relpath(gitdir, wt) if relative else gitdir}\n")
    return main, wt


def test_main_checkout_is_found_from_a_linked_worktree(tmp_path):
    main, wt = _main_and_worktree(tmp_path)
    assert mc._main_checkout(wt) == main.resolve()
    _, wt2 = _main_and_worktree(tmp_path / "rel", relative=True)
    assert mc._main_checkout(wt2) == (tmp_path / "rel" / "main").resolve()


def test_main_checkout_is_none_outside_a_linked_worktree(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert mc._main_checkout(tmp_path / "repo") is None          # a .git directory
    assert mc._main_checkout(tmp_path) is None                    # no .git at all
    odd = tmp_path / "odd"
    odd.mkdir()
    (odd / ".git").write_text("not a gitdir line\n")
    assert mc._main_checkout(odd) is None
    (odd / ".git").write_text(f"gitdir: {tmp_path}/somewhere/else\n")
    assert mc._main_checkout(odd) is None


def test_a_worktree_drops_every_path_into_the_main_checkout(tmp_path):
    main, wt = _main_and_worktree(tmp_path)
    env = {"CLAUDE_PROJECT_DIR": str(main), "SEAL_REPO_ROOT": str(main / "sub"),
           "LIST": f"/usr/bin{os.pathsep}{main}/tools", "TILDE": "~/elsewhere",
           "KEEP_PATH": "/usr/bin", "KEEP_WORKTREE": str(wt), "KEEP_TEXT": "hello",
           "KEEP_SIBLING": str(tmp_path / "main-other")}
    out = mc._test_env_base(env, wt)
    assert set(out) == {"TILDE", "KEEP_PATH", "KEEP_WORKTREE", "KEEP_TEXT", "KEEP_SIBLING"}


def test_the_main_checkout_keeps_its_whole_environment(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path / "repo"), "X": "1"}
    assert mc._test_env_base(env, tmp_path / "repo") == env


def test_test_subprocesses_use_the_scrubbed_environment(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(mc, "_test_env_base", lambda: {"MARK": "scrubbed"})
    monkeypatch.setattr(mc, "_run_reaping_descendants",
                        lambda cmd, env, timeout: seen.update(env) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    mc.run_tests([tmp_path / "t.py"], 5)
    assert seen["MARK"] == "scrubbed" and seen["MUTATION_CHECK_ACTIVE"] == "1"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def _main_with_worktree(tmp_path, extra_test=''):
    main = tmp_path / "main"
    (main / "tools").mkdir(parents=True)
    (main / "tests").mkdir()
    (main / "tools" / "subject.py").write_text(
        "def classify(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n", encoding="utf-8")
    (main / "tests" / "test_subject.py").write_text(textwrap.dedent("""
        import os, sys
        from pathlib import Path
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[1])
        sys.path.insert(0, str(root / "tools"))
        from subject import classify
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
    """) + extra_test, encoding="utf-8")
    _git(main, "init", "-q")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "fixture")
    wt = tmp_path / "w01"
    _git(main, "worktree", "add", "-q", "--detach", str(wt))
    return main, wt


def test_a_worktree_run_tests_its_own_copy_and_leaves_the_main_tree_alone(tmp_path):
    """End to end for A3. The test file finds its tool through CLAUDE_PROJECT_DIR, the way
    hooks find the repo. A session exports that as the MAIN checkout, so without the scrub
    a worker would mutate its own copy and import the untouched main one: every mutant
    would 'survive' and the run would look like a weak suite rather than a wrong tree."""
    main, wt = _main_with_worktree(tmp_path)
    before = {p: p.read_bytes() for p in main.rglob("*") if p.is_file() and ".git" not in p.parts}
    r = _run([str(wt / "tools" / "subject.py"), "--tests", str(wt / "tests" / "test_subject.py")],
             wt, {"CLAUDE_PROJECT_DIR": str(main)})
    d = json.loads(r.stdout)
    assert d["killed"] >= 2 and d["survived"] == 0, \
        "the worker's tests imported the main checkout instead of the mutated copy"
    after = {p: p.read_bytes() for p in main.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert after == before, "a worktree run changed the main checkout"


def test_the_isolation_pass_also_tests_the_worktree_copy(tmp_path):
    """Codex commit-3 review F1: --isolation ran each file with os.environ, not the scrubbed
    environment. This test file checks it imported its OWN tree's tool; run alone with a
    leaked CLAUDE_PROJECT_DIR it imports the main checkout's and fails."""
    own_tree = textwrap.dedent("""
        import subject
        def test_imported_from_this_tree():
            assert Path(subject.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1]
    """)
    main, wt = _main_with_worktree(tmp_path, own_tree)
    r = _run([str(wt / "tools" / "subject.py"), "--tests", str(wt / "tests" / "test_subject.py"),
              "--isolation"], wt, {"CLAUDE_PROJECT_DIR": str(main)})
    d = json.loads(r.stdout)
    assert d.get("isolation_failures") == [], d.get("isolation_failures")
    assert d["killed"] >= 2 and d["survived"] == 0



def test_progress_interval_is_floored_at_one(tmp_path):
    # Codex commit-3 round 2 F3: 0 must not divide by zero and abort without a result.
    src, t = _pair(tmp_path, STRONG)
    r = _run([str(src), "--tests", str(t)], tmp_path, {"MUTATION_PROGRESS_EVERY": "0"})
    d = json.loads(r.stdout)
    assert d["status"] == "ok" and d["killed"] >= 2


def test_incomplete_survives_the_isolation_pass(tmp_path):
    # Codex commit-3 round 2 F1: --isolation must not overwrite "incomplete".
    src, t = _pair(tmp_path, STRONG)
    r = _run([str(src), "--tests", str(t), "--max-hours", "0", "--isolation"], tmp_path)
    d = json.loads(r.stdout)
    assert d["status"] == "incomplete" and "isolation_failures" in d
    assert r.returncode == 2


def test_the_sweep_never_counts_an_incomplete_run_as_measured():
    import mutation_sweep as ms
    rows = [{"tool": "tools/a.py", "status": "incomplete", "unfinished": 4},
            {"tool": "tools/b.py", "status": "survivors", "unfinished": 2},
            {"tool": "tools/c.py", "status": "ok", "unfinished": 0}]
    assert ms.partially_measured_tools(rows) == ["tools/a.py", "tools/b.py"]
