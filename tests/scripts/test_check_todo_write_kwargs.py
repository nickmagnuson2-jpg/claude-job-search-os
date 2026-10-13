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


# --- per-segment judging (2026-10-12; rule gate tripped on the 3rd fire, 2026-10-05) ---
# The hook used to read ONE subcommand for the whole Bash call: the first todo_write.py
# on the line. A compound call therefore judged a later segment's flags against an
# earlier segment's subcommand. That failed in both directions:
#   false positive: `withdraw X; update Y --notes-append Z` blocked (10 of the 18
#                   blocks found in the session transcripts on 2026-10-12)
#   false negative: `update Y --notes-append Z; add T --priority High` passed, because
#                   the exempt `update` was the subcommand read for the whole call
# Each case below is the SHAPE of a real blocked call with invented text.

PY = "PYTHONIOENCODING=utf-8 python3 tools/todo_write.py"

FALSE_POSITIVE_REPLAY = [
    # 2026-09-17, first fire: withdraw, then update --notes-append
    f'{PY} withdraw "placeholder task one" 2>&1 | head -3; '
    f'{PY} update "placeholder task two" --notes-append "added context" 2>&1 | head -3',
    # 2026-09-21: done, then update --notes-prepend
    f'{PY} done "placeholder task one" 2>&1 | tail -3; echo ---; '
    f'{PY} update "placeholder task two" --notes-prepend "read this first" 2>&1 | tail -3',
    # 2026-09-25, second fire: three done calls, then update --notes-append
    'export PYTHONIOENCODING=utf-8 && python3 tools/todo_write.py done "task a" | cut -c1-160 '
    '&& python3 tools/todo_write.py done "task b" | cut -c1-160 '
    '&& python3 tools/todo_write.py done "task c" | cut -c1-160 '
    '&& python3 tools/todo_write.py update "task d" --notes-append "call moved" | cut -c1-160',
    # 2026-10-01: done, withdraw, update --due --notes-append, then a positional add
    f'{PY} done "task a" && {PY} withdraw "task b" && '
    f'{PY} update "task c" --due 2026-10-05 --notes-append "new date" && '
    f'{PY} add "task e" High 2026-10-02 "positional notes"',
    # 2026-10-01: two positional adds, then update --notes-append
    'cd /tmp/example && export PYTHONIOENCODING=utf-8 && '
    'python3 tools/todo_write.py add "task a" High 2026-10-02 "notes" && '
    'python3 tools/todo_write.py add "task b" Med 2026-10-03 "notes" && '
    'python3 tools/todo_write.py update "task c" --notes-append "more"',
    # 2026-10-05, third fire, block 1: withdraw; update --task --notes-append
    'export PYTHONIOENCODING=utf-8; python3 tools/todo_write.py withdraw "task a" 2>&1 | cut -c1-110; '
    'python3 tools/todo_write.py update "task b" --task "new title" --notes-append "why" 2>&1 | cut -c1-140',
    # 2026-10-05, third fire, block 2: the same call without --task
    'export PYTHONIOENCODING=utf-8; python3 tools/todo_write.py withdraw "task a" 2>&1 | cut -c1-110; '
    'python3 tools/todo_write.py update "task b" --notes-append "why" 2>&1 | cut -c1-140',
    # 2026-10-06: an inline script first (its prose holds the word add), then withdraw + update
    "python3 - <<'EOF'\nprint('add a line to the notes file')\nEOF\n"
    f'{PY} withdraw "task a" 2>&1 | cut -c1-200; {PY} withdraw "task c" 2>&1 | cut -c1-200; '
    f'{PY} update "task b" --notes-append "see the notes file" 2>&1 | cut -c1-200',
    # 2026-10-07: done, then update --due
    f'{PY} done "task a" | cut -c1-120; {PY} update "task b" --due 2026-10-09 | cut -c1-120',
    # 2026-10-09: done, then update --due --priority
    'export PYTHONIOENCODING=utf-8; python3 tools/todo_write.py done "task a" 2>&1 | cut -c1-140; '
    'python3 tools/todo_write.py update "task b" --due 2026-10-13 --priority High 2>&1 | cut -c1-160',
]


@pytest.mark.parametrize("command", FALSE_POSITIVE_REPLAY)
def test_update_flags_are_clean_when_chained_after_another_subcommand(command):
    assert _run(command) == 0


@pytest.mark.parametrize("command", [
    # the rule file's open question: `update <fragment> --task <new>` run on its own
    f'{PY} update "task b" --task "new title"',
    f'{PY} update "task b" --task "new title" 2>&1 | cut -c1-140',
    f'{PY} update "task b" --task "new title" --notes-append "why"',
    # a flag-shaped string inside the VALUE of an update flag is still a value
    f'{PY} update "task b" --notes-append "next time use add, not --due"',
])
def test_update_task_on_its_own_is_clean(command):
    assert _run(command) == 0


MUST_STILL_BLOCK = [
    # a kwarg `add` chained AFTER a clean segment
    f'{PY} done "task a" 2>&1 | head -5; {PY} add "task b" --priority Med --due 2026-09-15 2>&1 | head -5',
    f'{PY} done "task a" && {PY} add "task b" --notes "y"',
    # a kwarg `add` chained BEFORE an exempt segment
    f'{PY} add "task b" --due 2026-01-01 && {PY} update "task c" --notes-append "y"',
    # an exempt segment first must not shield a later `add` (passed before 2026-10-12)
    f'{PY} update "task c" --notes-append "y"; {PY} add "task b" --priority High',
    f'{PY} update "task c" --notes-append "y" && {PY} add "task b" --notes "z"',
    f'{PY} list --grep task\n{PY} add "task b" --task "t"',
    # the other subcommands reject these flags too (todo_write.py: "Unknown flag(s)")
    f'{PY} done --task "task a" > /tmp/out.txt 2>&1; echo "exit=$?"',
    f'{PY} withdraw "task a" --notes "no longer needed"',
    f'{PY} update "task c" --due 2026-01-01; {PY} done "task a" --notes "finished early"',
    # still an invocation inside a pipe, a command substitution, and a loop body
    f'{PY} add "task b" --priority High | head -3',
    'echo "$(python3 tools/todo_write.py add x --due 2026-01-01)"',
    'for t in a b; do python3 tools/todo_write.py add "$t" --priority Low; done',
]


@pytest.mark.parametrize("command", MUST_STILL_BLOCK)
def test_kwargs_on_a_non_flag_subcommand_still_block_in_any_segment(command):
    assert _run(command) == 2


def test_the_block_message_names_the_segment_that_carries_the_flag():
    """Two todo_write.py calls in one command: the message must name the one at fault,
    or a transcript reader cannot tell a true catch from a false positive."""
    payload = json.dumps({"tool_input": {"command":
        f'{PY} update "task c" --notes-append "y"; {PY} withdraw "task a" --due 2026-01-01'}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 2
    first = r.stderr.splitlines()[0]
    assert first.startswith("BLOCKED: todo_write.py invoked with --due (kwarg)")
    assert "`withdraw`" in first


# --- Mutation survivors, 2026-10-13 ------------------------------------------
# Each case below failed to fail when the named decision was broken on purpose.

def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cw_unit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("tail", [
    "",                              # nothing after the script
    ["--repo-root", "/tmp/x"],       # the pair and then nothing
    "--due 2026-01-01 add",          # a flag before any subcommand
    ["--repo-root", "/tmp/x", "--due", "2026-01-01"],
])
def test_subcommand_of_answers_empty_string_when_there_is_no_subcommand(tail):
    assert _load().subcommand_of(tail) == ""


def test_subcommand_of_skips_a_repeated_repo_root_pair():
    assert _load().subcommand_of(
        ["--repo-root", "/a", "--repo-root", "/b", "Update", "frag"]) == "update"


@pytest.mark.parametrize("command", [
    'python3 -u tools/todo_write.py add "task a" --due 2026-01-01',
    'python3 -X utf8 tools/todo_write.py add "task a" --due 2026-01-01',
    'python3 -W ignore tools/todo_write.py add "task a" --priority High',
    'python3 -u -X utf8 -B tools/todo_write.py done "task a" --notes "x"',
])
def test_interpreter_options_before_the_script_do_not_hide_the_call(command):
    assert _run(command) == 2


@pytest.mark.parametrize("command", [
    # `-m` and `-c` run no script file, and `-` reads the program from stdin.
    'python3 -m tools/todo_write.py add "task a" --due 2026-01-01',
    'python3 -c tools/todo_write.py add "task a" --due 2026-01-01',
    'python3 - tools/todo_write.py add "task a" --due 2026-01-01',
    # Another script with the same flags is not this hook's business.
    'python3 tools/example_write.py add "task a" --due 2026-01-01',
    'python3 -u tools/example_write.py add "task a" --priority High',
])
def test_a_command_that_does_not_run_the_script_is_clean(command):
    assert _run(command) == 0


def test_a_clean_substitution_does_not_end_the_search():
    assert _run(f'echo "$(date +%F)"; {PY} add "task a" --due 2026-01-01') == 2
    assert _run(f'echo "$(echo one) $(echo two)" && {PY} done "task a" --notes "x"') == 2


def test_a_flag_before_any_subcommand_is_named_as_such():
    payload = json.dumps({"tool_input": {"command": f'{PY} --due 2026-01-01 add "task a"'}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "no subcommand before the flag" in r.stderr.splitlines()[0]


@pytest.mark.parametrize("tool_input", [{"command": None}, {"command": ""}, {}, None])
def test_a_payload_with_no_command_exits_clean_without_a_traceback(tool_input):
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=json.dumps({"tool_input": tool_input}),
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stderr == ""
