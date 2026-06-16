"""Tests for tools/todo_write.py's positional-only flag guard.

The guard rejects kwarg-style `--flag` tokens (which would silently slot into the
wrong column) but must ALLOW a bare `--`/`-`/`""` placeholder, which cmd_add
treats as "use default". The bare-`--` case was unreachable before 2026-06-06 —
the guard rejected it while cmd_add documented it as valid, so the /todo SKILL.md
instruction to pass `--` failed. See feedback_command_hook_match_position_not_substring
and the cmd_add sentinel at tools/todo_write.py.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "todo_write.py"

ACTIVE_TEMPLATE = """# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|------|----------|-----|--------|-------|

## Completed

| Task | Priority | Completed | Notes |
|------|----------|-----------|-------|
"""

DEFAULT_DUE = "—"  # the em-dash the script writes for an omitted due


def _seed(tmp_path):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ACTIVE_TEMPLATE, encoding="utf-8")
    return p


def _run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(tmp_path), *args],
        capture_output=True, text=True,
    )


def test_bare_double_dash_placeholder_accepted(tmp_path):
    _seed(tmp_path)
    r = _run(tmp_path, "add", "Task A", "--", "--", "--")
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok"
    assert out["priority"] == "Med"      # placeholder -> default
    assert out["due"] == DEFAULT_DUE


def test_bare_single_dash_placeholder_accepted(tmp_path):
    _seed(tmp_path)
    r = _run(tmp_path, "add", "Task B", "-", "-", "real note")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["priority"] == "Med"


def test_empty_string_placeholder_accepted(tmp_path):
    _seed(tmp_path)
    r = _run(tmp_path, "add", "Task C", "Low", "", "n")
    out = json.loads(r.stdout)
    assert out["priority"] == "Low"
    assert out["due"] == DEFAULT_DUE


def test_kwarg_flag_still_rejected(tmp_path):
    _seed(tmp_path)
    r = _run(tmp_path, "add", "Task D", "--priority", "High")
    out = json.loads(r.stdout)
    assert out["status"] == "error"
    assert "Unknown flag" in out["message"]


def test_repo_root_flag_not_rejected(tmp_path):
    # --repo-root is extracted before the guard; it must not trip it.
    _seed(tmp_path)
    r = _run(tmp_path, "add", "Task E", "High")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "ok"
