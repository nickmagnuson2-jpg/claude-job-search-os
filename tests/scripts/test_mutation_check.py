"""Tests for tools/mutation_check.py — the gate that asks whether tests test anything.

Dogfooding matters here more than usual: a tool that demands mutation evidence and has
none itself is the exact hypocrisy it exists to detect.

The properties worth protecting, in order:
  1. It must RESTORE the target file, always. It mutates a live file in place, so a
     bug here corrupts source.
  2. It must be DETERMINISTIC. A gate that reports different mutants per run cannot be
     a gate.
  3. It must distinguish a kill-by-assertion from a kill-by-crash, and must NOT report
     an unparseable reason as "nothing was asserted" — a false accusation sends someone
     to rewrite a correct test.
  4. A survivor must fail the run unless allowlisted WITH a reason.
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_check.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))
import mutation_check as mc  # noqa: E402


def _run(args, cwd=None):
    """Spawn mutation_check.py in a DETERMINISTIC environment.

    MUTATION_CHECK_ACTIVE is stripped, never inherited. It is the flag `run_tests` sets to
    exempt its own subprocesses from the conftest stranded-backup refusal, so when these
    tests are themselves run from inside a mutation run, the ambient value leaks into every
    subprocess spawned here and silently changes what is being tested.

    THE INCIDENT (2026-08-27). The corpus sweep banked 109 tools and MEASURED only 101.
    Eight returned `baseline_red` in ~21s, which reads as "this tool's suite is broken" and
    is not what happened: 7 of the 8 map `test_mutation_check.py`, whose refusal test needs
    conftest to actually refuse. Under an inherited MUTATION_CHECK_ACTIVE the refusal never
    fires, `isolation_refused` is never recorded, the assertion fails, and mutation_check
    declares the baseline red for a tool whose own tests are entirely green. The suite
    passed standalone every time, which is exactly why it went unnoticed: the failure only
    exists when the tool runs itself.

    Stripping it here makes these tests measure the tool's behaviour rather than the
    caller's environment. Do NOT replace this with `{**os.environ}`.
    """
    env = {k: v for k, v in os.environ.items() if k != "MUTATION_CHECK_ACTIVE"}
    env["PYTHONIOENCODING"] = "utf-8"
    if cwd:
        env["MUTATION_REPO_ROOT"] = str(cwd)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                       cwd=str(cwd or REPO_ROOT), env=env)
    return r


# --- 1. safety: the target file must survive --------------------------------

def _live_tool_copy(tmp_path):
    """A byte-copy of a REAL tool and its REAL test file, in a throwaway repo.

    These two tests used to run against `tools/vault_paths.py` ITSELF. That makes an
    ordinary `pytest tests/` run a live in-place mutation of a live tool, and on
    2026-08-26 it fired: the suite ran while the corpus sweep was mid-flight, the nested
    mutation run collided with the outer one, and vault_paths.py was left MUTATED on disk
    for two hours -- with NO .mutation_backup, so every stranded-backup check reported the
    tree clean. vault_paths.py is the single resolver for the personal-vault root, used by
    granola_auto_debrief (launchd, every 3h), living_log_append and personal_todo_write.

    Copying keeps what the test was actually for -- a real tool, real complexity, a real
    test file, not a toy -- and removes the live blast radius entirely.
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "scripts").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "vault_paths.py"
    src.write_bytes((REPO_ROOT / "tools" / "vault_paths.py").read_bytes())
    t = tmp_path / "tests" / "scripts" / "test_vault_paths.py"
    t.write_bytes((REPO_ROOT / "tests" / "scripts" / "test_vault_paths.py").read_bytes())
    (tmp_path / "tests" / "conftest.py").write_bytes(
        (REPO_ROOT / "tests" / "conftest.py").read_bytes())
    # conftest.py imports tools/conftest_guard.py (the single source of the refusal code
    # and of what counts as a stranded backup), resolved relative to ITS OWN repo root --
    # which here is tmp_path. Without this the copied conftest raises ImportError, pytest
    # cannot collect, and mutation_check returns an error dict with no isolation keys at
    # all. Added 2026-09-01 when exactly that broke
    # test_a_conftest_refusal_is_not_reported_as_an_isolation_failure.
    (tmp_path / "tools" / "conftest_guard.py").write_bytes(
        (REPO_ROOT / "tools" / "conftest_guard.py").read_bytes())
    return src, t


def test_target_file_is_restored_byte_for_byte(tmp_path):
    """It rewrites the file in place. If this property breaks, the tool corrupts source."""
    target, t = _live_tool_copy(tmp_path)
    before = target.read_bytes()
    _run([str(target), "--tests", str(t)], cwd=tmp_path)
    assert target.read_bytes() == before


# --- 2. determinism ---------------------------------------------------------

def test_mutant_enumeration_is_deterministic():
    import ast
    src = (REPO_ROOT / "tools" / "vault_paths.py").read_text(encoding="utf-8")
    a = mc.enumerate_mutants(ast.parse(src))
    b = mc.enumerate_mutants(ast.parse(src))
    assert a == b and a == sorted(set(a)), "enumeration must be stable and de-duplicated"


def test_mutant_key_survives_line_drift():
    """The key must not be the line number, which changes on every edit above it."""
    k1 = mc.mutant_key("tools/x.py", "f", "IF_FALSE", "    if a and b:")
    k2 = mc.mutant_key("tools/x.py", "f", "IF_FALSE", "if a and b:")
    assert k1 == k2, "leading whitespace must not change identity"
    assert mc.mutant_key("tools/x.py", "g", "IF_FALSE", "if a and b:") != k1


# --- 3. kill classification -------------------------------------------------

def _fixture_pair(tmp_path, test_body):
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text("def classify(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n",
                   encoding="utf-8")
    t = tmp_path / "test_subject.py"
    t.write_text(textwrap.dedent(test_body), encoding="utf-8")
    return src, t


def test_assertion_kill_is_not_reported_as_weak(tmp_path):
    """A test that ASSERTS an output value must register as a strong kill."""
    src, t = _fixture_pair(tmp_path, """
        import sys; sys.path.insert(0, r'%s')
        from subject import classify
        def test_pos(): assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
    """ % (tmp_path / "tools"))
    r = _run([str(src), "--tests", str(t)], cwd=tmp_path)
    d = json.loads(r.stdout)
    assert d["killed"] >= 1
    assert d["weak_kill_count"] == 0, f"assertion kills misreported as weak: {d['weak_kills']}"


def test_crash_only_kill_is_reported_as_weak(tmp_path):
    """A test that merely CALLS the code, asserting nothing about output, observes the
    mutation only by crashing. That is the hollow-but-green state."""
    src, t = _fixture_pair(tmp_path, """
        import sys; sys.path.insert(0, r'%s')
        from subject import classify
        def test_smoke():
            classify(1).upper()      # crashes if None is returned; asserts nothing
    """ % (tmp_path / "tools"))
    r = _run([str(src), "--tests", str(t)], cwd=tmp_path)
    d = json.loads(r.stdout)
    assert d["weak_kill_count"] >= 1, "a crash-only kill must be flagged weak"


def test_unparseable_reason_is_unknown_not_weak(tmp_path, monkeypatch):
    """A false accusation is worse than a missing signal: an unreadable failure reason
    must NOT be reported as 'this test asserts nothing'.

    Rewritten 2026-08-26. The previous version asserted
    `"unknown" in mc.run_tests.__doc__ or True` -- a tautology that cannot fail, the exact
    shape mc.audit_test_quality flags -- plus an exact-set equality on _ASSERTION_KINDS,
    which pinned the implementation rather than the behaviour and had to be edited to
    change a constant. This drives the real function instead.
    """
    class _Result:
        returncode, stdout, stderr = 1, "totally unparseable output\n", ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    passed, kind = mc.run_tests([tmp_path / "test_x.py"], timeout=5)
    assert passed is False, "a non-zero return code means the mutant was killed"
    assert kind == "unknown", (
        f"an unreadable failure reason must classify as 'unknown', not a weak kill; got "
        f"{kind!r} -- reporting it as weak accuses a correct test of checking nothing")


def test_pytest_raises_counts_as_an_assertion():
    """`pytest.raises` that does not fire emits `Failed`, which is an assertion about
    behavior, not an incidental crash."""
    assert "Failed" in mc._ASSERTION_KINDS


# --- 4. survivors and the allowlist -----------------------------------------

def test_survivor_fails_the_run(tmp_path):
    src, t = _fixture_pair(tmp_path, """
        import sys; sys.path.insert(0, r'%s')
        from subject import classify
        def test_nothing(): assert classify(1) is not None
    """ % (tmp_path / "tools"))
    r = _run([str(src), "--tests", str(t)], cwd=tmp_path)
    d = json.loads(r.stdout)
    if d["survived"]:
        assert r.returncode == 2, "a surviving mutant must fail the run"
        assert d["status"] == "survivors"


def test_no_mapped_tests_is_an_error_not_a_pass(tmp_path):
    """A file with no tests must not silently report success — that would make the gate
    trivially satisfiable by deleting tests."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "orphan_xyzzy.py"
    src.write_text("def f():\n    return 1\n", encoding="utf-8")
    r = _run([str(src)], cwd=tmp_path)
    assert r.returncode == 1
    assert json.loads(r.stdout)["code"] == "no_tests"


def test_red_baseline_is_an_error_not_a_result(tmp_path):
    """If the suite is already failing, every mutant result is meaningless."""
    src, t = _fixture_pair(tmp_path, """
        def test_broken(): assert False
    """)
    r = _run([str(src), "--tests", str(t)], cwd=tmp_path)
    assert json.loads(r.stdout)["code"] == "baseline_red"


# --- 5. static test audit ---------------------------------------------------

def test_audit_flags_assertion_free_tests(tmp_path):
    t = tmp_path / "test_x.py"
    t.write_text("def test_nothing():\n    x = 1 + 1\n", encoding="utf-8")
    out = mc.audit_test_quality([t])
    assert any(a["test"] == "test_nothing" for a in out["assertion_free"])


def test_audit_does_not_flag_a_real_assertion(tmp_path):
    t = tmp_path / "test_y.py"
    t.write_text("def test_real():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    assert mc.audit_test_quality([t])["assertion_free"] == []


def test_audit_flags_tautologies(tmp_path):
    t = tmp_path / "test_z.py"
    t.write_text("def test_taut():\n    assert True\ndef test_self():\n    x=1\n    assert x == x\n",
                 encoding="utf-8")
    out = mc.audit_test_quality([t])
    whys = {d["test"] for d in out["tautological"]}
    assert {"test_taut", "test_self"} <= whys


def test_audit_counts_pytest_raises_as_an_assertion(tmp_path):
    t = tmp_path / "test_r.py"
    t.write_text("import pytest\ndef test_raises():\n    with pytest.raises(ValueError):\n        raise ValueError()\n",
                 encoding="utf-8")
    assert mc.audit_test_quality([t])["assertion_free"] == []


# --- 6. corruption safety (the 2026-08-19 incident) -------------------------
# The tool was run ON ITSELF. Its own test suite invokes it against a REAL repo file,
# so a mutant that broke the restore logic executed, restored nothing, and left TWO
# bystander files corrupted -- one with a function silently returning None. Caught only
# because the full suite went red afterwards. These lock the fixes.

def test_self_mutation_is_refused():
    """A tool that rewrites live source must never rewrite ITSELF: a mutant of the
    restore logic corrupts whatever its own tests touch."""
    r = _run([str(TOOL)])
    assert r.returncode == 1
    assert json.loads(r.stdout)["code"] == "self_mutation_refused"


def test_stranded_file_is_recovered_on_next_run(tmp_path):
    """Recovery must not depend on the tool having been correct during the crash."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    good = "def f():\n    # a comment the mutant would strip\n    return 1\n"
    src.write_text("def f():\n    return None\n", encoding="utf-8")   # simulated mutant
    mc.backup_path(src).write_text(good, encoding="utf-8")            # crash-era backup
    msg = mc.recover_if_stranded(src)
    assert msg and "restored" in msg
    assert src.read_text(encoding="utf-8") == good
    assert not mc.backup_path(src).exists()


def test_recovery_is_a_noop_when_nothing_is_stranded(tmp_path):
    src = tmp_path / "clean.py"
    src.write_text("x = 1\n", encoding="utf-8")
    assert mc.recover_if_stranded(src) is None
    assert src.read_text(encoding="utf-8") == "x = 1\n"


def test_assert_intact_repairs_and_aborts_on_mismatch(tmp_path):
    """Belt and braces over the `finally`, which can itself be mutated away."""
    src = tmp_path / "s.py"
    original = "x = 1\n"
    src.write_text("x = 999\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        mc.assert_intact(src, original)
    assert src.read_text(encoding="utf-8") == original, "must repair, not just complain"


def test_a_normal_run_leaves_no_backup_residue(tmp_path):
    target, t = _live_tool_copy(tmp_path)
    _run([str(target), "--tests", str(t)], cwd=tmp_path)
    assert not mc.backup_path(target).exists()


# --- 5. crash-safety: a signalled run must not strand a mutated file ---------
#
# THE INCIDENT (2026-08-26). An overnight sweep was stopped mid-run. `finally` does not
# run when the process is SIGTERMed, so four live tools were left MUTATED on disk with
# stranded backups -- among them pipe_write.py with its archive-section test inverted,
# which would have appended a duplicate `## Archived` header to the real job-pipeline.md
# on the next /pipe call. The report written that same night flagged the hazard in prose
# ("an atexit/signal handler would remove this whole failure mode") and the hazard then
# fired a second time, four hours later, on the same run. Prose is not an enforcement
# tier; this test is.

def _slow_fixture(tmp_path):
    """A subject with several mutation points, whose test is slow enough to interrupt."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text(
        "def classify(n):\n"
        "    if n > 0:\n"
        "        return 'pos'\n"
        "    if n < 0:\n"
        "        return 'neg'\n"
        "    return 'zero'\n",
        encoding="utf-8")
    t = tmp_path / "test_subject.py"
    t.write_text(textwrap.dedent("""
        import sys, time; sys.path.insert(0, r'%s')
        from subject import classify
        def test_pos():
            time.sleep(1.5)
            assert classify(1) == 'pos'
        def test_neg(): assert classify(-1) == 'neg'
        def test_zero(): assert classify(0) == 'zero'
    """ % (tmp_path / "tools")), encoding="utf-8")
    return src, t


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signalled_run_restores_the_target_and_removes_the_backup(tmp_path, sig):
    """SIGTERM does not raise in Python, so `finally` never runs and the target stays
    mutated. The tool must restore it from a signal handler."""
    src, t = _slow_fixture(tmp_path)
    original = src.read_text(encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(TOOL), str(src), "--tests", str(t)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(tmp_path),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MUTATION_REPO_ROOT": str(tmp_path)})

    # Wait until the tool is demonstrably mid-mutation: the file on disk differs from
    # what we wrote. Polling for that exact state is what makes this deterministic.
    deadline = time.time() + 60
    while time.time() < deadline:
        if src.read_text(encoding="utf-8") != original:
            break
        if proc.poll() is not None:
            proc.kill()
            pytest.fail("the run exited before a mutation was ever written to disk")
        time.sleep(0.02)
    else:
        proc.kill()
        pytest.fail("timed out waiting for the tool to mutate the target")

    proc.send_signal(sig)
    try:
        proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("the tool did not exit after being signalled")

    assert src.read_text(encoding="utf-8") == original, (
        f"target left MUTATED on disk after {sig.name} -- this is the 2026-08-26 "
        "corruption incident")
    assert not mc.backup_path(src).exists(), \
        f"a stranded .mutation_backup was left behind after {sig.name}"


# The end-to-end signal test above proves the target survives, but NOT which layer saved
# it: SystemExit from the handler unwinds through the existing `finally`, so gutting
# _restore_in_flight or the backup unlink leaves it green. Both mutants were generated by
# hand and confirmed to survive. These pin each layer directly, the same way
# test_assert_intact_repairs_and_aborts_on_mismatch pins the other belt-and-braces path.

def test_restore_in_flight_repairs_the_target_and_clears_the_backup(tmp_path):
    src = tmp_path / "s.py"
    original = "x = 1\n"
    src.write_text("x = 999\n", encoding="utf-8")            # simulated live mutant
    mc.backup_path(src).write_text(original, encoding="utf-8")
    mc._IN_FLIGHT = (src, original)
    try:
        mc._restore_in_flight()
        assert src.read_text(encoding="utf-8") == original, "the target must be repaired"
        assert not mc.backup_path(src).exists(), "the backup must be removed, not stranded"
    finally:
        mc._IN_FLIGHT = None


def test_restore_in_flight_is_idempotent(tmp_path):
    """The `finally` and atexit both call it; a second call must not raise."""
    src = tmp_path / "s.py"
    src.write_text("x = 1\n", encoding="utf-8")
    mc._IN_FLIGHT = (src, "x = 1\n")
    try:
        mc._restore_in_flight()
        mc._restore_in_flight()
        assert src.read_text(encoding="utf-8") == "x = 1\n"
    finally:
        mc._IN_FLIGHT = None


def test_on_signal_restores_before_exiting_with_the_signal_code(tmp_path):
    src = tmp_path / "s.py"
    original = "x = 1\n"
    src.write_text("x = 999\n", encoding="utf-8")
    mc.backup_path(src).write_text(original, encoding="utf-8")
    mc._IN_FLIGHT = (src, original)
    try:
        with pytest.raises(SystemExit) as exc:
            mc._on_signal(signal.SIGTERM, None)
        assert exc.value.code == 128 + int(signal.SIGTERM), \
            f"must exit 128+signum, got {exc.value.code}"
        assert src.read_text(encoding="utf-8") == original, \
            "the handler must restore on its own, not rely on `finally` unwinding"
        assert not mc.backup_path(src).exists()
    finally:
        mc._IN_FLIGHT = None


def test_arm_restore_records_the_target_so_a_later_restore_repairs_it(tmp_path):
    """Arming has two halves -- install the handler AND record what to put back. With
    only the first, _restore_in_flight is a no-op and the whole layer is decorative.
    Generated as a mutant (drop the record) and confirmed to survive without this test.
    """
    src = tmp_path / "s.py"
    original = "x = 1\n"
    src.write_text(original, encoding="utf-8")
    mc.backup_path(src).write_text(original, encoding="utf-8")
    before = signal.getsignal(signal.SIGTERM)
    try:
        mc.arm_restore(src, original)
        src.write_text("x = 999\n", encoding="utf-8")     # the run mutates the target
        mc._restore_in_flight()
        assert src.read_text(encoding="utf-8") == original, \
            "arm_restore did not record the target, so the restore had nothing to put back"
    finally:
        signal.signal(signal.SIGTERM, before)
        mc._IN_FLIGHT = None


def test_arm_restore_installs_a_handler_for_sigterm(tmp_path):
    """SIGTERM is the one `finally` cannot cover. Default disposition means no cover."""
    src = tmp_path / "s.py"
    src.write_text("x = 1\n", encoding="utf-8")
    before = signal.getsignal(signal.SIGTERM)
    try:
        mc.arm_restore(src, "x = 1\n")
        assert signal.getsignal(signal.SIGTERM) is mc._on_signal, \
            "SIGTERM must be handled, not left at its default disposition"
    finally:
        signal.signal(signal.SIGTERM, before)
        mc._IN_FLIGHT = None


# --- 6. the weak-kill classifier must not misfile bare asserts ---------------
#
# THE DEFECT (found 2026-08-26). weak_kill_count was measuring pytest's RENDERING, not
# whether a test asserted. Under `--tb=line` pytest prefixes "AssertionError:" only when
# it generates a multi-line explanation -- string diffs get it, simple scalar comparisons
# do not. So `assert got == 2` renders as "test.py:3: assert 1 == 2", the regex captures
# the token `assert`, which was absent from _ASSERTION_KINDS, and an ordinary assertion
# kill was filed as a crash.
#
# It is conditional on the compared TYPE, which is worse than a uniform bug: the metric is
# noisy in a way that tracks the data a tool happens to handle rather than test quality,
# so weak_kill_count is not comparable between an int-heavy and a string-heavy tool. The
# earlier write-up called it systematic; it is not, and a string-comparing fixture cannot
# reproduce it at all. That is why this fixture compares INTEGERS.

def _int_fixture(tmp_path, test_body):
    """Subject returning ints, so pytest renders bare asserts WITHOUT 'AssertionError:'."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text("def score(n):\n    if n > 0:\n        return 2\n    return 1\n",
                   encoding="utf-8")
    t = tmp_path / "test_subject.py"
    t.write_text(textwrap.dedent(test_body), encoding="utf-8")
    return src, t


def test_a_bare_scalar_assert_is_an_assertion_kill_not_a_crash(tmp_path):
    """`assert got == 2` with no message is idiomatic pytest. It must not be filed as a
    crash kill merely because pytest renders it without an exception name."""
    src, t = _int_fixture(tmp_path, """
        import sys; sys.path.insert(0, r'%s')
        from subject import score
        def test_pos(): assert score(1) == 2
        def test_neg(): assert score(-1) == 1
    """ % (tmp_path / "tools"))
    d = json.loads(_run([str(src), "--tests", str(t)], cwd=tmp_path).stdout)
    assert d["killed"] >= 1, "the mutants must actually die first, or this proves nothing"
    assert d["weak_kill_count"] == 0, (
        "a bare scalar `assert` was misfiled as a crash kill; weak kills reported: "
        f"{d['weak_kills']}")


def test_a_genuine_crash_kill_is_still_reported_as_weak(tmp_path):
    """The fix must not go the other way and call every kill an assertion. A test that
    only CALLS the code, asserting nothing, observes the mutation solely by crashing."""
    src, t = _int_fixture(tmp_path, """
        import sys; sys.path.insert(0, r'%s')
        from subject import score
        def test_smoke():
            score(1).bit_length()     # AttributeError if None; asserts nothing
    """ % (tmp_path / "tools"))
    d = json.loads(_run([str(src), "--tests", str(t)], cwd=tmp_path).stdout)
    assert d["weak_kill_count"] >= 1, \
        "a crash-only kill must still be flagged weak, or the metric is inverted"


# --- 6. a refusal is not a failure ------------------------------------------

def test_a_conftest_refusal_is_not_reported_as_an_isolation_failure(tmp_path):
    """THE INCIDENT (2026-08-26). One tool hit the sweep's timeout, was SIGKILLed, and
    left a stranded .mutation_backup. tests/conftest.py then refused to run (exit 3) for
    every later --isolation check, and all 41 were recorded as `isolation_failed` -- a
    false accusation against 41 innocent test files, indistinguishable in the report from
    a real suite-ordering defect. The main mutation runs were exempt because they carry
    MUTATION_CHECK_ACTIVE; the isolation subprocess does not, which is the whole asymmetry.

    Unmeasured and failing must not render the same. Exit 3 means `I refused to look`.
    """
    target, t = _live_tool_copy(tmp_path)
    # a SIBLING's wreckage, not this tool's: recover_if_stranded only restores its own
    (tmp_path / "tools" / "other.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "tools" / "other.py.mutation_backup").write_text("X = 1\n", encoding="utf-8")

    r = _run([str(target), "--tests", str(t), "--isolation", "--json"], cwd=tmp_path)
    d = json.loads(r.stdout)

    assert d["isolation_failures"] == [], \
        "a refusal must never be filed as a failing test file"
    assert d.get("isolation_refused"), "the refusal must be recorded, not dropped"
    assert d["status"] == "isolation_unmeasured", \
        "unmeasured is its own status; it must not read as either clean or failed"


def test_a_genuine_isolation_failure_is_still_reported(tmp_path):
    """The other direction: the refusal carve-out must not swallow real failures.

    A real ordering dependency needs TWO files -- one that only passes because a sibling
    ran first in the same process. A single file that fails alone also fails in the
    baseline, which aborts the run before isolation is ever reached (my first attempt at
    this test did exactly that and proved nothing).
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "tools" / "subject.py"
    src.write_text("def f(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n",
                   encoding="utf-8")
    setup = tmp_path / "test_0_setup.py"
    setup.write_text(
        "import builtins\n"
        "def test_sets_process_state(): builtins._SWEPT_MARK = 1\n", encoding="utf-8")
    dep = tmp_path / "test_1_dep.py"
    dep.write_text(
        "import builtins, sys; sys.path.insert(0, r'%s')\n"
        "from subject import f\n"
        "def test_needs_the_neighbour():\n"
        "    assert getattr(builtins, '_SWEPT_MARK', None) == 1\n"
        "    assert f(1) == 'pos'\n" % (tmp_path / "tools"), encoding="utf-8")

    r = _run([str(src), "--tests", str(setup), str(dep), "--isolation", "--json"],
             cwd=tmp_path)
    d = json.loads(r.stdout)
    assert str(dep) in d["isolation_failures"], \
        "a test file that genuinely fails alone must still be named"
    assert d["status"] == "isolation_failed"
    assert not d.get("isolation_refused"), "nothing refused here; only a real failure"


class TestBytecodeCachingCannotFakeAKill:
    """Origin 2026-08-31. mutation_check rewrites the target dozens of times per second;
    CPython invalidates a cached .pyc by (mtime, size), and mtime has coarse granularity,
    so a later mutant could be executed as EARLIER bytecode. Measured on
    ~/.claude/skills/ss/scripts/ss_route_conversation.py: a NEGATE_CMP mutant that
    survives by hand 3 of 3 was reported KILLED in 5 of 6 tool runs. The first run after
    clearing __pycache__ was correct (54/3); runs 2 and 3 reported 55/2.

    A FALSE KILL is the worst defect this tool can have. A false survivor sends someone
    to write a test they may not need; a false kill certifies that a suite protects a
    behavior it does not, which is the exact claim the 'green test is not evidence' Hard
    Rule uses this tool to justify. Every subprocess this tool spawns must therefore run
    with bytecode writing disabled."""

    def test_run_tests_disables_bytecode_writing(self, monkeypatch, tmp_path):
        import mutation_check as mc
        seen = {}

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            return _R()

        monkeypatch.setattr(mc.subprocess, "run", fake_run)
        mc.run_tests([tmp_path / "test_x.py"], timeout=5)
        assert seen.get("PYTHONDONTWRITEBYTECODE") == "1", (
            "run_tests spawns pytest without PYTHONDONTWRITEBYTECODE=1, so a stale .pyc "
            "can make a surviving mutant report as killed"
        )

    def test_every_pytest_subprocess_disables_bytecode_writing(self, monkeypatch, tmp_path):
        """Behavioural, NOT a source grep. The first version of this test searched the
        isolation block's SOURCE TEXT for the env var name and passed even with the
        `env=` kwarg deleted -- because the explanatory comment above the call contains
        the same string. Asserting on a comment is not asserting on behavior; the mutant
        that removed the real kwarg survived it. This version drives the actual code path
        with subprocess.run patched, so only a real env can satisfy it."""
        import ast
        import mutation_check as mc
        envs = []

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kw):
            envs.append(kw.get("env") or {})
            return _R()

        target = tmp_path / "sample.py"
        target.write_text("def f(x):\n    if x > 1:\n        return 1\n    return 0\n",
                          encoding="utf-8")
        tests = tmp_path / "test_sample.py"
        tests.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        monkeypatch.setattr(mc.subprocess, "run", fake_run)
        monkeypatch.setattr(mc, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mc.sys, "argv",
                            ["mutation_check.py", str(target),
                             "--tests", str(tests), "--isolation", "--json"])
        try:
            mc.main()
        except SystemExit:
            pass

        assert envs, "no pytest subprocess was spawned"
        missing = [i for i, e in enumerate(envs)
                   if e.get("PYTHONDONTWRITEBYTECODE") != "1"]
        assert not missing, (
            f"{len(missing)} of {len(envs)} pytest subprocesses ran without "
            f"PYTHONDONTWRITEBYTECODE=1; a stale .pyc can then fake a kill"
        )


class TestMapTestsIgnoresProse:
    """Origin 2026-08-31. map_tests selected test files with a bare substring scan of the
    whole file (`if stem in text`) - no word boundary, comments and docstrings included.

    Measured consequence on tools/todo_write.py: 15 files selected instead of the 6 real
    test_todo_write_*.py files, 56s per run instead of 2s. At 541 mutants that is 8.4h
    against the sweep's 5h cap, so the tool came back UNAUDITED_TIMEOUT - unmeasured, and
    the report says plainly that unmeasured is not clean.

    The three expensive extras named sample_tool only in prose. The sharpest was
    test_mutation_check.py, whose match was `personal_sample_tool` - a DIFFERENT tool whose
    name merely contains this one - inside a comment listing launchd jobs.

    Corpus-wide at the time: at least 16% of the 302 mapped selections were prose-only.
    """

    def _mod(self, tmp_path, body, name="test_x.py"):
        tests = tmp_path / "tests" / "scripts"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / name).write_text(body, encoding="utf-8")
        tools = tmp_path / "tools"
        tools.mkdir(exist_ok=True)
        target = tools / "sample_tool.py"
        target.write_text("def f():\n    return 1\n", encoding="utf-8")
        return target, tests / name

    def _mapped(self, tmp_path, body):
        import mutation_check as mc
        target, _ = self._mod(tmp_path, body)
        return {p.name for p in mc.map_tests(target, repo_root=tmp_path)}

    def test_mention_in_a_comment_does_not_select_the_file(self, tmp_path):
        assert self._mapped(tmp_path, "# sample_tool.py sync does a thing\ndef test_a():\n    assert True\n") == set()

    def test_mention_in_a_module_docstring_does_not_select_the_file(self, tmp_path):
        assert self._mapped(tmp_path, '"""Explains how sample_tool.py behaves."""\ndef test_a():\n    assert True\n') == set()

    def test_mention_in_a_function_docstring_does_not_select_the_file(self, tmp_path):
        assert self._mapped(tmp_path, 'def test_a():\n    """sample_tool.py sync is why."""\n    assert True\n') == set()

    def test_a_longer_tool_name_that_CONTAINS_this_one_does_not_select(self, tmp_path):
        """The exact match that pulled a 20s subprocess suite into sample_tool's set 541
        times: `personal_sample_tool` contains `sample_tool`. Word boundaries, not substrings."""
        assert self._mapped(tmp_path, "import personal_sample_tool\ndef test_a():\n    assert personal_sample_tool\n") == set()

    def test_a_real_import_still_selects(self, tmp_path):
        assert self._mapped(tmp_path, "import sample_tool\ndef test_a():\n    assert sample_tool\n") == {"test_x.py"}

    def test_from_import_still_selects(self, tmp_path):
        assert self._mapped(tmp_path, "from sample_tool import main\ndef test_a():\n    assert main\n") == {"test_x.py"}

    def test_a_string_literal_naming_the_file_still_selects(self, tmp_path):
        """Many tests shell out rather than import: subprocess.run([... 'tools/sample_tool.py']).
        Dropping those would silently un-cover every CLI-invoked tool, turning real kills
        into survivors - the failure this fix must not cause."""
        assert self._mapped(tmp_path, "import subprocess\ndef test_a():\n    subprocess.run(['python3', 'tools/sample_tool.py', 'add'])\n") == {"test_x.py"}

    def test_importlib_by_name_still_selects(self, tmp_path):
        assert self._mapped(tmp_path, "import importlib\ndef test_a():\n    importlib.import_module('sample_tool')\n") == {"test_x.py"}

    def test_a_function_name_alone_does_not_select(self, tmp_path):
        """A test FUNCTION named after the tool, in a file that neither imports it nor
        names its path, cannot kill one of its mutants - killing requires actually running
        the code. The authoring signal that does count is the FILE name, below."""
        assert self._mapped(tmp_path, "def test_sample_tool_roundtrip():\n    assert True\n") == set()

    def test_the_split_suite_filename_convention_still_selects(self, tmp_path):
        """This repo splits a tool's suite across test_<stem>_<aspect>.py - sample_tool has
        six (guard, misfiled, roundtrip, sync, update, withdraw). Selection is by filename
        prefix, so they are chosen regardless of content."""
        import mutation_check as mc
        target, _ = self._mod(tmp_path, "def test_a():\n    assert True\n",
                              name="test_sample_tool_roundtrip.py")
        assert {p.name for p in mc.map_tests(target, repo_root=tmp_path)} == {"test_sample_tool_roundtrip.py"}

    def test_a_longer_tools_split_suite_is_NOT_selected(self, tmp_path):
        """The prefix is anchored: test_personal_sample_tool_*.py belongs to a different
        tool and must not be pulled into this one's set."""
        import mutation_check as mc
        target, _ = self._mod(tmp_path, "def test_a():\n    assert True\n",
                              name="test_personal_sample_tool_sync.py")
        assert {p.name for p in mc.map_tests(target, repo_root=tmp_path)} == set()

    def test_an_unparseable_test_file_FAILS_OPEN_and_is_still_selected(self, tmp_path):
        """A file we cannot parse must be included, not excluded. Excluding it would drop
        real coverage and inflate survivors; including it only costs runtime. When the
        analysis is uncertain, err toward measuring more."""
        assert self._mapped(tmp_path, "def test_a(:\n  sample_tool\n") == {"test_x.py"}

    def test_the_directly_named_test_file_is_always_selected(self, tmp_path):
        """tests/scripts/test_<stem>.py is selected by NAME and must not depend on content."""
        import mutation_check as mc
        target, _ = self._mod(tmp_path, "def test_a():\n    assert True\n",
                              name="test_sample_tool.py")
        assert {p.name for p in mc.map_tests(target, repo_root=tmp_path)} == {"test_sample_tool.py"}


class TestIndirectCoverageIsKept:
    """The tightening of map_tests must drop PROSE, never real coverage. A test can
    exercise a target without naming it, by importing a module that imports it."""

    def _tree(self, tmp_path):
        tools = tmp_path / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "leaf.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tools / "middle.py").write_text("import leaf\ndef g():\n    return leaf.f()\n",
                                         encoding="utf-8")
        tests = tmp_path / "tests" / "scripts"
        tests.mkdir(parents=True, exist_ok=True)
        return tools, tests

    def test_a_test_that_only_imports_the_IMPORTER_still_covers_the_target(self, tmp_path):
        """test imports middle; middle imports leaf. Mutating leaf can fail this test, so
        it is real coverage and must stay selected even though it never says 'leaf'."""
        import mutation_check as mc
        tools, tests = self._tree(tmp_path)
        (tests / "test_middle.py").write_text(
            "import middle\ndef test_g():\n    assert middle.g() == 1\n", encoding="utf-8")
        got = {p.name for p in mc.map_tests(tools / "leaf.py", repo_root=tmp_path)}
        assert "test_middle.py" in got

    def test_an_unrelated_test_is_still_not_selected(self, tmp_path):
        """The transitive rule must not become 'select everything'."""
        import mutation_check as mc
        tools, tests = self._tree(tmp_path)
        (tests / "test_other.py").write_text(
            "def test_x():\n    assert True\n", encoding="utf-8")
        got = {p.name for p in mc.map_tests(tools / "leaf.py", repo_root=tmp_path)}
        assert "test_other.py" not in got

    def test_prose_about_the_importer_still_does_not_select(self, tmp_path):
        """Indirect coverage counts only through CODE. A comment naming the importer is
        still prose and still worthless for killing a mutant."""
        import mutation_check as mc
        tools, tests = self._tree(tmp_path)
        (tests / "test_prosey.py").write_text(
            "# middle.py explains how leaf works\ndef test_x():\n    assert True\n",
            encoding="utf-8")
        got = {p.name for p in mc.map_tests(tools / "leaf.py", repo_root=tmp_path)}
        assert "test_prosey.py" not in got

    def test_an_import_cycle_terminates(self, tmp_path):
        """covering_names runs to a fixpoint; a cycle must not hang it."""
        import mutation_check as mc
        tools, tests = self._tree(tmp_path)
        (tools / "a.py").write_text("import b\n", encoding="utf-8")
        (tools / "b.py").write_text("import a\nimport leaf\n", encoding="utf-8")
        names = mc.covering_names("leaf", tools)
        assert {"leaf", "middle", "b", "a"} <= names
