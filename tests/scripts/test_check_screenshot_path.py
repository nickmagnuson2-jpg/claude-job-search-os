"""Tests for tools/check_screenshot_path.py — the hand-typed-screenshot-path
PreToolUse Bash hook.

Blocks (exit 2) any Bash command containing a `Screenshot <date> at <time>
AM/PM.png` filename with a REGULAR space before AM/PM (the real macOS filename
uses U+202F there). Stays clean (exit 0) for the correct U+202F filename and
for unrelated commands.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_screenshot_path.py"

NNBSP = " "  # narrow no-break space — the real macOS filename separator


def _run(command: str):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    return r.returncode


# --- should BLOCK (exit 2): hand-typed path with a regular space -----------

@pytest.mark.parametrize("command", [
    'cp "/Users/mag/Documents/screenshots/Screenshot 2026-06-15 at 1.28.08 PM.png" dest/',
    'mv ~/Documents/screenshots/"Screenshot 2026-01-02 at 9.40.21 AM.png" archive/',
    'python3 -c "import shutil; shutil.move(\'Screenshot 2026-06-15 at 1.28.08 PM.png\', \'x\')"',
    "ls -la 'Screenshot 2026-12-31 at 11.59.59 PM.png'",
])
def test_blocks_hand_typed_path(command):
    assert _run(command) == 2


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("command", [
    f'cp "/Users/mag/Documents/screenshots/Screenshot 2026-06-15 at 1.28.08{NNBSP}PM.png" dest/',
    "ls -t ~/Documents/screenshots/*.png | head -1",
    "git status",
    "echo 'no screenshot here'",
    "ls ~/Documents/screenshots/",
    'python3 tools/friction_log.py resolve "ss_log_append.py" '
    '"FileNotFoundError: Screenshot 2026-06-15 at 1.28.08 PM.png" --note "fixed"',
])
def test_allows_clean_commands(command):
    assert _run(command) == 0


def test_fails_open_on_bad_json():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_fails_open_on_empty_command():
    assert _run("") == 0
