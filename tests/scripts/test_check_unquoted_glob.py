"""Tests for tools/check_unquoted_glob.py — the unquoted --include=/--exclude=
glob PreToolUse Bash hook.

Blocks (exit 2) an unquoted `--include=*.md`-style flag value (zsh expands the
glob before the command runs and aborts with "no matches found" if nothing in
cwd matches). Stays clean (exit 0) when the value is quoted, or the pattern
merely appears inside an unrelated quoted string (e.g. a grep search for this
exact text).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_unquoted_glob.py"


def _run(command: str):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    return r.returncode


# --- should BLOCK (exit 2): unquoted glob ------------------------------------

@pytest.mark.parametrize("command", [
    "grep -r foo --include=*.md .",
    "grep -rn pattern --exclude=*.pyc src/",
    "rg foo --include=*.md .",
    "grep --include=*.py -r TODO .",
])
def test_blocks_unquoted_glob(command):
    assert _run(command) == 2


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("command", [
    "grep -r foo --include='*.md' .",
    'grep -r foo --include="*.md" .',
    "grep -r foo --include=README.md .",   # no glob char, exact filename
    "git status",
    'grep -n "include=\\*.md" tools/somefile.py',  # bare mention inside a quoted search pattern
    "echo 'the flag is --include=*.md'",           # mention inside a quoted string
])
def test_allows_clean_commands(command):
    assert _run(command) == 0


def test_fails_open_on_bad_json():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_fails_open_on_empty_command():
    assert _run("") == 0
