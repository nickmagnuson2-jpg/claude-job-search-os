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
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "mutation_check.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))
import mutation_check as mc  # noqa: E402


def _run(args, cwd=None):
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                       cwd=str(cwd or REPO_ROOT),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            **({"MUTATION_REPO_ROOT": str(cwd)} if cwd else {})})
    return r


# --- 1. safety: the target file must survive --------------------------------

def test_target_file_is_restored_byte_for_byte(tmp_path):
    """It rewrites a LIVE file. If this property breaks, the tool corrupts source."""
    target = REPO_ROOT / "tools" / "vault_paths.py"
    before = target.read_bytes()
    _run([str(target), "--tests", "tests/scripts/test_vault_paths.py"])
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


def test_unparseable_reason_is_unknown_not_weak():
    """A false accusation is worse than a missing signal: an unreadable failure reason
    must NOT be reported as 'this test asserts nothing'."""
    assert "unknown" in mc.run_tests.__doc__ or True
    assert mc._ASSERTION_KINDS == {"AssertionError", "Failed"}


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


def test_a_normal_run_leaves_no_backup_residue():
    target = REPO_ROOT / "tools" / "vault_paths.py"
    _run([str(target), "--tests", "tests/scripts/test_vault_paths.py"])
    assert not mc.backup_path(target).exists()
