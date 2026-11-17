"""Tests for tools/check_unquoted_glob.py — the unquoted --include=/--exclude=
glob PreToolUse Bash hook.

zsh expands an unquoted `--include=*.md` before the command runs and aborts with
"no matches found" if nothing in cwd matches. The hook has three verdicts:

- REWRITE (exit 0, JSON on stdout): every flagged occurrence has the exact simple
  shape `--include=<plain glob>`, so the single correction is to single-quote the
  value. The hook returns `hookSpecificOutput.updatedInput` (the WHOLE tool_input,
  command changed) and NO permissionDecision, so the rewritten command still goes
  through the normal permission flow (Nick, 2026-11-16, Q7). An additionalContext
  line tells the model what was changed.
- BLOCK (exit 2): an unquoted glob exists but the rewrite is not provably safe
  (brace or bracket glob, backslash, $, heredoc, an occurrence the independent
  shlex check places inside quotes, or anything else outside the simple shape).
- CLEAN (exit 0, no stdout): nothing to fix.
"""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_unquoted_glob.py"


def _call(command: str, extra: dict | None = None):
    tool_input = {"command": command, **(extra or {})}
    payload = json.dumps({"tool_name": "Bash", "tool_input": tool_input})
    return subprocess.run([sys.executable, str(SCRIPT)],
                          input=payload, capture_output=True, text=True)


def _run(command: str):
    return _call(command).returncode


def _rewrite(command: str, extra: dict | None = None) -> dict:
    r = _call(command, extra)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    return out["hookSpecificOutput"]


# --- REWRITE (exit 0 + updatedInput) -----------------------------------------

@pytest.mark.parametrize("command, expected", [
    ("grep -r foo --include=*.md .", "grep -r foo --include='*.md' ."),
    ("grep -rn pattern --exclude=*.pyc src/", "grep -rn pattern --exclude='*.pyc' src/"),
    ("grep --include=*.py -r TODO .", "grep --include='*.py' -r TODO ."),
    ("grep -rl x . --include=*.md --include=*.py",
     "grep -rl x . --include='*.md' --include='*.py'"),
    ("grep -rn x src --include=*.md | head -5; echo done",
     "grep -rn x src --include='*.md' | head -5; echo done"),
    ("grep -rn x src --include=*.md;echo done",
     "grep -rn x src --include='*.md';echo done"),
    ("grep -r 'a b' --include=test_?.py .", "grep -r 'a b' --include='test_?.py' ."),
    ("grep -r foo --include=*.md --exclude-dir=.git .",
     "grep -r foo --include='*.md' --exclude-dir=.git ."),
])
def test_rewrites_simple_shape(command, expected):
    out = _rewrite(command)
    assert out["hookEventName"] == "PreToolUse"
    assert out["updatedInput"]["command"] == expected
    # Nick's ruling: no permission grant rides on the rewrite.
    assert "permissionDecision" not in out
    assert "check_unquoted_glob" in out["additionalContext"]


def test_rewrite_keeps_every_other_tool_input_field():
    # updatedInput REPLACES the whole input object (hooks docs), so a dropped
    # description/timeout would silently change the call.
    extra = {"description": "Search docs", "timeout": 60000, "run_in_background": False}
    out = _rewrite("grep -r foo --include=*.md .", extra)
    ui = out["updatedInput"]
    assert ui["command"] == "grep -r foo --include='*.md' ."
    for k, v in extra.items():
        assert ui[k] == v


@pytest.mark.parametrize("command", [
    "grep -r foo --include=*.md .",
    "grep -rl x . --include=*.md --include=*.py",
    "grep -r 'a b' --include=test_?.py .",
])
def test_rewrite_changes_quoting_only(command):
    # The invariant the rewrite rests on: the shell sees the same words.
    new = _rewrite(command)["updatedInput"]["command"]
    assert shlex.split(new) == shlex.split(command)
    assert _run(new) == 0 and _call(new).stdout == ""   # rewritten form is clean


# --- BLOCK (exit 2): unquoted glob, rewrite not provably safe ----------------

@pytest.mark.parametrize("command", [
    "grep -r foo --include=*.{md,py} .",          # quoting would kill brace expansion
    "grep -r foo --include=*.[ch] .",             # bracket class
    "grep -r foo --include=$EXT*.md .",           # expansion inside the value
    "grep -r foo --include=\\*.md* .",            # backslash
    "grep -r foo --include=*.md . <<'EOF'\nbody\nEOF",   # heredoc: shlex cannot check it
    "grep -r foo --include=*.md --include=*.{c,h} .",    # one unsafe occurrence blocks all
    "grep -r 'unbalanced --include=*.md .",       # shlex cannot parse it
    "grep -r foo --include=*.md\"x\" .",          # value runs into a quote
])
def test_blocks_when_rewrite_not_safe(command):
    r = _call(command)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "BLOCKED: unquoted glob" in r.stderr


def test_blocks_when_strip_literals_misreads_quotes():
    # Until Q11 (2026-11-16) strip_literals blanked '...' then "..." with two regexes,
    # and in `"it's" --include=*.md "x'y"` the single-quote regex paired the apostrophes
    # ACROSS the real double-quoted strings, hiding the flag, so this test pinned a
    # CLEAN verdict. strip_literals now reads shell_tokens spans and pairs each quote
    # correctly, so the unquoted glob is seen and quoted in place like any other.
    cmd = 'echo "it\'s" --include=*.md "x\'y"'
    r = _call(cmd)
    assert r.returncode == 0
    assert '--include=\'*.md\' \\"x\'y\\"' in r.stdout


def test_blocks_when_shlex_places_match_inside_quotes():
    # strip_literals' double-quote pattern refuses spans containing $, so it leaves
    # this "..." in place and detection fires on text that is INSIDE a quoted string.
    # Rewriting would insert quotes into the string; the shlex word check must
    # catch the change and fall back to BLOCK (today's verdict).
    # The trailing " x" makes the occurrence the full simple shape, so only the
    # shlex word comparison (not the SIMPLE regex) can reject it.
    cmd = 'echo "$HOME --include=*.md x"'
    r = _call(cmd)
    assert r.returncode == 2 and r.stdout == ""


# --- CLEAN (exit 0, no output) ------------------------------------------------

@pytest.mark.parametrize("command", [
    "grep -r foo --include='*.md' .",
    'grep -r foo --include="*.md" .',
    "grep -r foo --include=README.md .",   # no glob char, exact filename
    "git status",
    'grep -n "include=\\*.md" tools/somefile.py',  # bare mention inside a quoted search pattern
    "echo 'the flag is --include=*.md'",           # mention inside a quoted string
])
def test_allows_clean_commands(command):
    r = _call(command)
    assert r.returncode == 0
    assert r.stdout == ""


def test_fails_open_on_bad_json():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_fails_open_on_empty_command():
    assert _run("") == 0
