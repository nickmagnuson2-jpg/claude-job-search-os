"""Tests for check_sealed_read.py — fable-audit 2026-07-07 #12.

The hook BLOCKS Read/Grep/Glob/Bash that target data/project-background/ (the
sealed vault), with a SEAL_READ_OK=1 override for intentional maintenance. Repo
root is injected via SEAL_REPO_ROOT so the tests never depend on real sealed files.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_sealed_read.py"
SEALED = "data/project-background"


def _run(root, tool, tool_input, env_extra=None):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    env = {**os.environ, "SEAL_REPO_ROOT": str(root)}
    env.pop("SEAL_READ_OK", None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


# --- should BLOCK (exit 2) --------------------------------------------------

def test_blocks_read_of_sealed_file(tmp_path):
    code, err = _run(tmp_path, "Read",
                     {"file_path": str(tmp_path / SEALED / "zuora-corpus/notes.md")})
    assert code == 2
    assert SEALED in err


def test_blocks_grep_into_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Grep", {"pattern": "x", "path": str(tmp_path / SEALED)})
    assert code == 2


def test_blocks_glob_into_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Glob", {"pattern": "*.md", "path": str(tmp_path / SEALED)})
    assert code == 2


def test_blocks_bash_referencing_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash", {"command": f"cat {SEALED}/mckinsey/x.md"})
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_allows_read_of_nonsealed_file(tmp_path):
    code, _ = _run(tmp_path, "Read", {"file_path": str(tmp_path / "data" / "profile.md")})
    assert code == 0


def test_allows_bash_not_referencing_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash", {"command": "ls data/people/"})
    assert code == 0


def test_env_override_allows_sealed_read(tmp_path):
    code, _ = _run(tmp_path, "Read",
                   {"file_path": str(tmp_path / SEALED / "notes.md")},
                   env_extra={"SEAL_READ_OK": "1"})
    assert code == 0


def test_bash_inline_override_allows_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash",
                   {"command": f"SEAL_READ_OK=1 cat {SEALED}/x.md"})
    assert code == 0


def test_ignores_non_target_tool(tmp_path):
    code, _ = _run(tmp_path, "Write",
                   {"file_path": str(tmp_path / SEALED / "x.md"), "content": "hi"})
    assert code == 0


def test_bad_json_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0
