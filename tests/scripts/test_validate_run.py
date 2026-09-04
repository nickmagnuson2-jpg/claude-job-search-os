"""validate_run.py: schema gate rejecting a run whose evidence was not preserved."""
import subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "contact_runs"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(run_json):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "validate_run.py"),
                           str(FIX / run_json), "--manifest", str(FIX / "manifest.csv")],
                          capture_output=True, text=True, env=ENV)


def test_valid_run_passes():
    """The gate must be able to pass something. A gate that rejects everything is as
    useless as one that passes everything."""
    p = run("run_valid.json")
    assert p.returncode == 0, p.stdout


def test_hollow_evidence_is_rejected():
    """Blank title variants and placeholder evidence certified as VALID before this fired."""
    p = run("run_hollow.json")
    assert p.returncode == 1
    assert "placeholder" in p.stdout.lower() or "blank" in p.stdout.lower()


def test_blank_title_variants_are_caught_not_just_counted():
    """Three empty strings satisfy a length check and preserve nothing."""
    p = run("run_hollow.json")
    assert "blank" in p.stdout.lower()
