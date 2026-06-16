"""Tests for tools/check_bare_python.py — the bare-`python` PreToolUse Bash hook.

The hook blocks (exit 2) bare `python` invocations in command position and stays
clean (exit 0) for `python3`, for `python` appearing as an argument/string/pattern,
and for python-prefixed package or path names. The command-position constraint was
added after a live smoke test caught the naive regex blocking its own echo payload.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_bare_python.py"


def _run(command: str):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    return r.returncode


# --- should BLOCK (exit 2): bare python in command position -----------------

@pytest.mark.parametrize("command", [
    "python tools/pipeline_staleness.py",
    "  python tools/x.py",                       # leading whitespace
    "foo | python -c 'import sys'",              # after a pipe
    "ls; python x.py",                           # after a semicolon
    "$(python --version)",                       # subshell
    "cat a && python b.py",                      # after &&
    "make && python run.py || echo fail",        # mid-chain
    'echo "$(python x.py)"',                     # cmd-subst inside double quotes
])
def test_blocks_bare_python(command):
    assert _run(command) == 2


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("command", [
    "PYTHONIOENCODING=utf-8 python3 tools/x.py",        # correct interpreter
    "python3 tools/pipeline_staleness.py",
    "pip install python-dateutil",                      # package name
    "ls /opt/homebrew/Cellar/python@3.14/3.14.3/bin",   # path
    'git commit -m "fix python bug"',                   # word in commit message
    "grep python tools/foo.py",                         # word as grep pattern
    'echo \'{"command":"python tools/x.py"}\'',         # python inside a string
    "cat python.md",                                    # filename
    "./run_python_script.sh",                           # python inside a word
    "git commit -m 'block bare `python` invocations'",  # inline code in commit msg
    "echo 'see `python` docs'",                         # backtick inline code
    'grep -rln "Bash(python " .claude/skills/',         # `(python` inside a grep pattern
    "rg '(python' src/",                                # `(python` inside single quotes
    'grep "[^3]python tools/" docs/',                   # python in a bracketed grep pattern
    # heredoc message bodies — the 2026-06-05 2nd-heredoc-fire gate
    "git commit -F - <<'EOF'\nfix the python crash bug\nran python and crashed\nEOF",
    "git commit -m \"$(cat <<'EOF'\ndescribe the python fix\nEOF\n)\"",   # 6/2 shape
    "cat <<EOF\njust some python prose here\nEOF",       # unquoted heredoc, no subst
])
def test_allows_clean_commands(command):
    assert _run(command) == 0


# --- heredoc must NOT mask a real invocation (no over-strip) -----------------

def test_real_python_before_heredoc_still_blocks():
    cmd = "python tools/x.py && git commit -F - <<'EOF'\npython prose\nEOF"
    assert _run(cmd) == 2


def test_real_subst_in_unquoted_heredoc_still_blocks():
    # Unquoted heredoc body expands; a genuine $(python) inside it must still fire.
    cmd = "cat <<EOF\n$(python danger.py)\nEOF"
    assert _run(cmd) == 2


def test_fail_open_on_bad_json():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0
