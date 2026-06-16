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
