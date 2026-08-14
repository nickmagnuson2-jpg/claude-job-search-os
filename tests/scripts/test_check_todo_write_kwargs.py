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


# --- the `update` exemption (added 2026-08-14) ------------------------------
# `update` is the one MUTATING subcommand that legitimately takes these flags: it
# edits a single field in place, so naming the field IS the interface. The guard has
# to know the contract it guards, or a correct invocation gets blocked forever and
# the next person routes around the hook instead of teaching it.
#
# The exemption is read from the SUBCOMMAND SLOT, never as a substring -- the same
# blind spot this hook family has re-learned repeatedly.

@pytest.mark.parametrize("command", [
    'python3 tools/todo_write.py update frag --notes-append "more"',
    'python3 tools/todo_write.py update frag --notes-prepend "first"',
    'python3 tools/todo_write.py update frag --task "renamed"',
    'python3 tools/todo_write.py update frag --priority Low',
    'python3 tools/todo_write.py update frag --due 2026-09-01',
    'python3 tools/todo_write.py update frag --notes "replaced"',
    # --repo-root may precede the subcommand and must not hide it
    'python3 tools/todo_write.py --repo-root /tmp/x update frag --due 2026-09-01',
    'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py update frag --task "x" --due 2026-09-01',
])
def test_allows_update_subcommand_flags(command):
    assert _run(command) == 0


@pytest.mark.parametrize("command", [
    # "update" as part of a VALUE must not unlock the guard for `add`
    'python3 tools/todo_write.py add "update the deck" --notes "y"',
    'python3 tools/todo_write.py add "Task" --notes "remember to update this"',
    # ...nor as part of another word in the subcommand slot
    'python3 tools/todo_write.py updated frag --task "x"',
])
def test_update_as_a_value_does_not_unlock_the_guard(command):
    assert _run(command) == 2


def test_the_exemption_list_matches_the_tools_real_flag_taking_subcommands():
    """If todo_write grows another flag-taking subcommand and this list is not
    updated, the hook blocks correct usage. Pin them together."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cw", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FLAG_TAKING_SUBCOMMANDS == {"update", "list", "sync"}
    assert mod.subcommand_of(" --repo-root /tmp update frag ") == "update"
    assert mod.subcommand_of(" add frag ") == "add"
