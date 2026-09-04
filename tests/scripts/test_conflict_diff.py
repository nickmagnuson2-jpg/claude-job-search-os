"""conflict_diff.py: mechanically diff two contact runs.

Fixtures are synthetic placeholders. tests/ is public and tracked, so no client data here.
"""
import subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "contact_runs"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "conflict_diff.py"), *args],
                          capture_output=True, text=True, env=ENV)


def test_hard_conflict_exits_1():
    """A person disagreement on the same function is a HARD conflict and must exit 1.

    This is the behaviour that found ten conflicts where careful reading found five.
    """
    p = run("--workbook", str(FIX / "run_a.xlsx"), "--records", str(FIX / "run_b.csv"))
    assert p.returncode == 1, p.stdout
    assert "HARD" in p.stdout
    assert "Zed Placeholder" in p.stdout


def test_agreeing_rows_are_not_conflicts():
    p = run("--workbook", str(FIX / "run_a.xlsx"), "--records", str(FIX / "run_b.csv"))
    assert "PRESENT IN BOTH RUNS" in p.stdout
    assert "Ada Placeholder" in p.stdout


def test_added_in_verification_is_not_coerced_into_a_function():
    """The bug that fabricated a false leadership conflict: rows whose sought title reads
    '(added in verification)' never claimed a function and must not be compared as one."""
    p = run("--workbook", str(FIX / "run_a.xlsx"), "--records", str(FIX / "run_b.csv"))
    assert "SUPPLEMENTAL" in p.stdout
    assert "Di Placeholder" in p.stdout
