"""Tests for tools/check_pipeline_exit_status.py — the masked-pipeline-exit-status
PreToolUse Bash hook.

The hook blocks (exit 2) when a shell verdict (`||` branch, or a `$?` read in the
next statement) hangs off a pipeline whose LAST stage is a pure formatter, so the
status being consumed belongs to the formatter rather than to the command under
test. It stays clean (exit 0) for unpiped verdicts, for `$(...)`-captured status,
for PIPESTATUS/pipefail handling, for pipelines ending in an INFORMATIVE-status
command (`grep -q`, `test`, `diff`), and for the pattern appearing inside a quoted
string or heredoc body.

Both origin fires from
memory/feedback_pipeline_masks_the_exit_status_you_are_testing.md are pinned
verbatim below (test_blocks_origin_fire_1 / _2) alongside their corrected forms.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_NAME = "check_pipeline_exit_status.py"


def _tools_dir() -> Path | None:
    """Directory holding the repo's shared hook_command_lint.py."""
    installed = Path(__file__).resolve().parents[2] / "tools"
    if (installed / "hook_command_lint.py").exists():
        return installed
    for base in [Path.cwd(), *Path.cwd().parents]:
        cand = base / "tools"
        if (cand / "hook_command_lint.py").exists():
            return cand
    return None


def _script() -> Path:
    sibling = Path(__file__).resolve().parent / TOOL_NAME
    if sibling.exists():
        return sibling
    return Path(__file__).resolve().parents[2] / "tools" / TOOL_NAME


SCRIPT = _script()
TOOLS = _tools_dir()


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if TOOLS is not None and not (SCRIPT.parent / "hook_command_lint.py").exists():
        env["PYTHONPATH"] = str(TOOLS) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(command: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, env=_env())
    assert "Traceback" not in r.stderr, r.stderr
    return r.returncode


def _stderr(command: str) -> str:
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, env=_env())
    return r.stderr


# --- the two origin fires, verbatim -----------------------------------------

ORIGIN_1 = (
    "git check-ignore -v claude-global/settings.json 2>&1 | sed 's/^/  /' \\\n"
    '  || echo "  NOT IGNORED - a new top-level mirror dir would leak to the public repo"'
)

ORIGIN_2 = (
    "PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py --scan somepath 2>&1 | tail -14\n"
    'echo "scan exit=$?"'
)


def test_blocks_origin_fire_1():
    """2026-08-13: `|| echo NOT IGNORED` bound to sed, so it never printed."""
    assert _run(ORIGIN_1) == 2


def test_blocks_origin_fire_2():
    """2026-08-14: `echo $?` after `| tail -14` reported tail's status as the script's."""
    assert _run(ORIGIN_2) == 2


def test_origin_1_corrected_form_is_clean():
    assert _run(
        'if git check-ignore -v claude-global/settings.json; then echo "  ok"; '
        'else echo "  NOT IGNORED"; fi'
    ) == 0


def test_origin_2_corrected_form_is_clean():
    assert _run(
        "PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py --scan somepath "
        "> /tmp/o.txt 2>/tmp/e.txt\n"
        'echo "scan exit=$?"'
    ) == 0


def test_block_message_names_the_masking_command():
    err = _stderr(ORIGIN_2)
    assert "BLOCKED: pipeline masks the exit status" in err
    assert "tail" in err
    assert "PIPESTATUS" in err


# --- should BLOCK (exit 2) ---------------------------------------------------

@pytest.mark.parametrize("command", [
    # shape A: `||` verdict hanging off a formatter-terminated pipeline
    "cmd -q X | sed 's/^/  /' || echo FAILED",
    "make test | tail -20 || echo '  BUILD FAILED'",
    "ls /nope | head -1 || echo missing",
    "git check-ignore -v p 2>&1 | cat || echo 'NOT IGNORED'",
    "foo | grep bar | sed 's/a/b/' || echo FAILED",      # last stage is the formatter
    "cmd | wc -l || echo none",
    "cmd | /usr/bin/sed -n 1p || echo FAILED",           # absolute path to formatter
    # shape B: `$?` read in the next statement
    "python3 tool.py --scan p 2>&1 | tail -14\necho \"scan exit=$?\"",
    "pytest -q | tail -5; echo $?",
    "cmd | head -3\nrc=$?",
    "cmd | tail -14\n\necho \"exit=$?\"",                # blank line between
])
def test_blocks(command):
    assert _run(command) == 2


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("command", [
    # the three correct forms from the rule
    'if cmd -q "$X"; then echo "  ok"; else echo "  FAILED"; fi',
    'out=$(cmd "$X") || { echo "  FAILED"; exit 1; }',
    "cmd \"$X\" | sed 's/a/b/'; [ \"${PIPESTATUS[0]}\" -eq 0 ] || echo FAILED",
    "set -o pipefail; cmd | tail -5 || echo FAILED",
    # informative-status final stages — a real verdict, not a masked one
    "cmd | grep -q foo || echo 'not found'",
    "cmd | grep -q foo; echo $?",
    "diff a b | grep '^>' || echo same",                  # last stage grep
    "cmd | awk 'NR==1' || echo FAILED",
    "cmd | jq -e .ok || echo FAILED",
    "git diff --stat | python3 tools/x.py || echo FAILED",
    # no pipeline at all
    "cmd -q X || echo FAILED",
    "python3 tool.py --scan p > /tmp/o.txt 2>/tmp/e.txt; echo \"exit=$?\"",
    "cmd; echo $?",
    # formatter pipeline with no verdict consumed
    "cat notes.md | sed 's/a/b/'",
    "ls -l | head -20",
    "cmd | tail -5\ngit status",                          # next statement has no $?
    "cmd | tail -5\nls\necho $?",                         # $? belongs to `ls`
    # the pattern as literal text, not a live command
    'git commit -m "fix: cmd | sed s/a/b/ || echo FAILED masked the status"',
    "grep -rn '| tail -14' tools/",
    "git commit -F - <<'EOF'\ncmd | sed 's/^/  /' || echo FAILED\nEOF",
    # unrelated ordinary commands
    "PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py list",
    "ls tools/",
])
def test_allows(command):
    assert _run(command) == 0


# --- fail-open ---------------------------------------------------------------

def _run_raw(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)],
                          input=payload, capture_output=True, text=True, env=_env())


# --- stage parsing: a stage with no command word -----------------------------

def test_non_word_final_stage_is_clean_not_a_crash():
    """Kills IF_FALSE on `if not m:` (_first_word): a final stage starting with a
    non-word char (`(`) has no regex match, so the guard MUST return early; without
    it, `m.group(0)` raises AttributeError and the hook dies instead of exiting 0."""
    r = _run_raw(json.dumps({"tool_input": {
        "command": "cmd | (tail -5) || echo FAILED"}}))
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 0


# --- shape B is only checked when the pipeline ENDS the and-or list ----------

def test_and_or_continuation_suppresses_the_next_statement_dollar_question():
    """Kills IF_TRUE on `if j + 2 >= len(parts):` — with `&& echo ok` after the
    pipeline, `$?` in the next statement belongs to `echo ok`, not to the
    formatter, so this must stay clean. Forcing that guard true blocks it."""
    assert _run("cmd | tail -5 && echo ok\necho $?") == 0


def test_and_or_continuation_still_blocks_the_trailing_pipeline():
    """Companion to the above: when the LAST element of the and-or list is itself a
    formatter-terminated pipeline, the `$?` read next statement IS masked."""
    assert _run("echo hi && cmd | tail -5\necho $?") == 2


# --- the block message names the right consumer ------------------------------

def test_block_message_names_or_as_the_consumer_for_shape_a():
    """Kills NEGATE_CMP on `shape == "||"`: swapping it mislabels the consumer."""
    err = _stderr(ORIGIN_1)
    assert "`||` is reading `sed`" in err
    assert "`$?` is reading" not in err


def test_block_message_names_dollar_question_as_the_consumer_for_shape_b():
    """Other half of the NEGATE_CMP kill on line 171."""
    err = _stderr(ORIGIN_2)
    assert "`$?` is reading `tail`" in err
    assert "`||` is reading" not in err


# --- module import contract --------------------------------------------------

_IMPORT_PROBE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('cpes', sys.argv[1])\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "print('IMPORT_OK', callable(mod.find_violation))\n"
)


def _import_probe() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-c", _IMPORT_PROBE, str(SCRIPT)],
                          capture_output=True, text=True, env=env,
                          cwd=str(Path(__file__).resolve().parent), input="")


def test_module_self_bootstraps_its_sibling_import_path():
    """Kills DROP_CALL on `sys.path.insert(...)` (line 80): loaded by file path with
    tools/ NOT on sys.path and PYTHONPATH cleared, `from hook_command_lint import
    strip_literals` only resolves because the module inserts its own directory."""
    r = _import_probe()
    assert "ModuleNotFoundError" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr
    assert "IMPORT_OK True" in r.stdout


def test_importing_the_module_does_not_run_main():
    """Kills IF_TRUE on `if __name__ == "__main__":` — if main() ran on import it
    would consume stdin and sys.exit(0) before the probe could print."""
    r = _import_probe()
    assert "IMPORT_OK True" in r.stdout, (r.stdout, r.stderr)


# --- fail-open ---------------------------------------------------------------

def test_bad_json_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True, env=_env())
    assert r.returncode == 0


def test_empty_command_fails_open():
    assert _run("") == 0


def test_null_command_fails_open_without_crashing():
    """Kills IF_FALSE and DROP_CALL on the `if not command: sys.exit(0)` guard
    (lines 163/164): a non-string command reaches the regex and raises TypeError
    unless the guard short-circuits first, so the hook must exit 0 with no
    traceback."""
    r = _run_raw(json.dumps({"tool_input": {"command": None}}))
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 0


def test_non_string_command_fails_open_without_crashing():
    """Same guard, other malformed shape (a list instead of a string)."""
    r = _run_raw(json.dumps({"tool_input": {"command": []}}))
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 0
