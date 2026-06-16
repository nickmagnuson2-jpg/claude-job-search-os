"""Tests for tools/check_todo_write_kwargs.py — the todo_write kwarg PreToolUse hook.

The hook blocks (exit 2) a real `python … todo_write.py … --(task|priority|due|notes)`
invocation and stays clean (exit 0) for positional usage, for --repo-root, and
crucially for the pattern appearing INSIDE a quoted argument (friction-log nature,
`python3 -c` body, grep pattern, commit message). The quote-awareness + python
anchor were added 2026-06-02 after the substring regex fired twice in one session
on legitimate work. See feedback_command_hook_match_position_not_substring.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_todo_write_kwargs.py"


def _run(command: str):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    return r.returncode


# --- should BLOCK (exit 2): a real kwarg invocation -------------------------

@pytest.mark.parametrize("command", [
    'python3 tools/todo_write.py add Task --priority High',
    'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add Task --due 2026-06-02',
    'cd ~/proj && python3 tools/todo_write.py add Task --task foo',
    'python tools/todo_write.py add Task --notes bar',
    # quoted task value (stripped) but the flag itself is unquoted -> still fires
    'python3 tools/todo_write.py add "Call Tom" --priority High',
])
def test_blocks_real_kwarg_invocation(command):
    assert _run(command) == 2


# --- should NOT block (exit 0): the pattern inside a quoted literal ----------

@pytest.mark.parametrize("command", [
    # the two real false-positives that fired this session:
    'python3 tools/friction_log.py append "todo_write.py" "rejected the --due kwarg"',
    'cd ~/x && python3 -c "import x; run(\'todo_write.py add a --priority High\')"',
    # other literal contexts
    'grep "todo_write.py --due" notes.md',
    "git commit -m 'fix todo_write.py --priority bug'",
    'echo "usage: todo_write.py add <task> --priority is WRONG"',
    # heredoc message body (2026-06-05 heredoc-stripping addition)
    "git commit -F - <<'EOF'\nnote: python3 tools/todo_write.py add x --priority High is wrong\nEOF",
    "cat <<EOF\npython3 tools/todo_write.py add x --due 2026-06-05\nEOF",
])
def test_clean_when_pattern_is_inside_a_literal(command):
    assert _run(command) == 0


# --- should NOT block (exit 0): legitimate positional usage -----------------

@pytest.mark.parametrize("command", [
    'python3 tools/todo_write.py add "Task" "High" "2026-06-02" "notes"',
    'python3 tools/todo_write.py add "Task" "High" --repo-root .',   # repo-root allowed
    'python3 tools/todo_write.py done "fragment"',
    'python3 tools/todo_write.py sync --repo-root .',
    'python3 tools/pipe_write.py --repo-root . update --priority High',  # different script
])
def test_clean_for_positional_and_other_scripts(command):
    assert _run(command) == 0


def test_bare_reference_without_python_does_not_fire():
    # No `python` before todo_write.py -> not an invocation.
    assert _run("echo todo_write.py --priority High") == 0


def test_parse_failure_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0
