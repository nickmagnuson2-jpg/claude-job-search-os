"""Tests for audit_rule_violations.py — the back-propagation sweep.

Given a rule's violation signature (a regex), find every existing artifact in
the repo that already breaks it, so a newly-learned rule gets applied backward
(to what's already there) not just forward (via a guard). Origin: 2026-06-02 —
13 skill docs prescribed bare `python` for weeks after the python3 rule existed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "audit_rule_violations.py"


def _run(root: Path, *args):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    last = (r.stdout or "").strip().splitlines()
    return r.returncode, (json.loads(last[-1]) if last else {})


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.md").write_text("run `python tools/x.py` now\nand python3 tools/y.py is fine\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("no violations here\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.md").write_text("also python tools/z.py here\n", encoding="utf-8")
    sub = tmp_path / "__pycache__"
    sub.mkdir()
    (sub / "skip.md").write_text("python tools/should_be_skipped.py\n", encoding="utf-8")
    return tmp_path


def test_finds_violations_across_files(tree):
    # Match bare `python ` (space) but not `python3 ` — same shape as the real rule.
    code, payload = _run(tree, "--pattern", r"python tools/")
    assert code == 0
    assert payload["status"] == "ok"
    files = {v["file"] for v in payload["violations"]}
    assert any(f.endswith("a.md") for f in files)
    assert any(f.endswith("c.md") for f in files)
    # python3 line must NOT match
    assert all("python3" not in v["text"] for v in payload["violations"])


def test_skips_noise_dirs(tree):
    code, payload = _run(tree, "--pattern", r"python tools/")
    assert all("__pycache__" not in v["file"] for v in payload["violations"])


def test_clean_pattern_zero_violations(tree):
    code, payload = _run(tree, "--pattern", r"NEVER_APPEARS_XYZ")
    assert code == 0
    assert payload["count"] == 0
    assert payload["violations"] == []


def test_exclude_filter(tree):
    code, payload = _run(tree, "--pattern", r"python tools/", "--exclude", "nested")
    files = {v["file"] for v in payload["violations"]}
    assert any(f.endswith("a.md") for f in files)
    assert not any("nested" in f for f in files)


# ===========================================================================
# Self-describing scope (2026-08-19)
#
# `--ext ""` normalizes to {"."} and matches no file, turning 588 real
# violations into {"status":"ok","count":0} -- a sweep that covered nothing,
# reading as a clean bill of health. This tool is invoked at CLAUDE.md:86 as a
# MANDATORY step of the self-improvement loop, so a false clean here silently
# skips back-propagating a rule across artifacts that already violate it.
#
# Fixed by stating the applied scope next to the conclusion, not by rejecting
# input: exit codes and `status` are unchanged (a caller &&-chaining this tool
# must not break to prevent a defect that has never fired organically).
# ===========================================================================

def test_scope_is_reported_on_a_normal_run(tmp_path):
    (tmp_path / "a.md").write_text("bare python here\n", encoding="utf-8")
    code, out = _run(tmp_path, "--pattern", "bare python")
    assert out["count"] == 1
    assert out["exts_defaulted"] is True, "no --ext given, so the default set applied"
    assert ".md" in out["exts_applied"]


def test_empty_ext_reports_the_scope_that_made_it_empty(tmp_path):
    """The exact audit reproduction, scaled down: a zero count must carry the
    reason it is zero, or it is indistinguishable from a genuine clean sweep."""
    (tmp_path / "a.md").write_text("bare python here\n", encoding="utf-8")
    code, out = _run(tmp_path, "--pattern", "bare python", "--ext", "")
    assert out["count"] == 0
    assert out["files"] == 0
    assert out["exts_applied"] == ["."], "the nonsense scope must be visible in the report"
    assert out["exts_defaulted"] is False, "must not look like the default set applied"


def test_genuine_clean_sweep_is_distinguishable_from_an_empty_scope(tmp_path):
    """The property that actually matters: two zero-count results that mean
    opposite things must not serialize identically."""
    (tmp_path / "a.md").write_text("nothing to see\n", encoding="utf-8")
    _, real_clean = _run(tmp_path, "--pattern", "bare python")
    _, empty_scope = _run(tmp_path, "--pattern", "bare python", "--ext", "")
    assert real_clean["count"] == empty_scope["count"] == 0
    # `files` counts files WITH violations, so it is 0 in BOTH cases -- which is
    # exactly why it was never the tell the audit first claimed it was.
    assert real_clean["files"] == empty_scope["files"] == 0
    assert real_clean["files_scanned"] > 0, "a real sweep examined files"
    assert empty_scope["files_scanned"] == 0, "the empty scope examined none"
    assert real_clean["exts_applied"] != empty_scope["exts_applied"], \
        "a real clean sweep and a scope-of-nothing must be tellable apart"


def test_exit_code_and_status_unchanged(tmp_path):
    """Additive-only: CLAUDE.md:86 prescribes this command, so the contract holds."""
    (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
    code, out = _run(tmp_path, "--pattern", "zzz", "--ext", "")
    assert code == 0
    assert out["status"] == "ok"
