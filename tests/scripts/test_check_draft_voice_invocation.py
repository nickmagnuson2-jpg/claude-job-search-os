"""Tests for is_open_draft_invocation's command-position matching in check_draft_voice.py.

The gate must fire only when a command actually INVOKES open_draft.py (python
interpreter + script), not when the token merely appears in a grep pattern, commit
message, or JSON payload string. This is the command-position-not-substring family
(see tools/HOOK_AUTHORING.md); the prior `"open_draft.py" in command` substring
check false-positive-blocked benign greps that only mentioned the token.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)


def detects(command: str) -> bool:
    """The hook's real test: is_open_draft_invocation, exactly as the gate calls it."""
    return cdv.is_open_draft_invocation(command)


# --- real invocations must be detected (gate runs) -------------------------

@pytest.mark.parametrize("command", [
    "python3 tools/open_draft.py",
    "PYTHONIOENCODING=utf-8 python3 tools/open_draft.py",
    "python tools/open_draft.py",
    "cd /repo && python3 tools/open_draft.py",
    "python3 -u tools/open_draft.py",
    'echo "$(python3 tools/open_draft.py)"',   # cmd-subst inside dquotes stays live
])
def test_real_invocation_detected(command):
    assert detects(command) is True


# --- bare mentions must NOT be detected (these were the false positives) ----

@pytest.mark.parametrize("command", [
    'grep -n "open_draft.py" .claude/settings.json',     # token in dquoted pattern
    "grep -n 'open_draft.py' tools/",                     # token in squoted pattern
    'grep -n "open_draft\\|check_draft_voice" file',      # the grep that got blocked
    'git commit -m "fix open_draft.py substring bug"',    # token in commit message
    "printf '%s' '{\"command\":\"python3 tools/open_draft.py\"}'",  # squoted JSON payload
    "cat tools/open_draft.py",                            # reading, not invoking
    "ls tools/",                                          # unrelated
    "rg open_draft src/",                                 # token as rg pattern (no python)
])
def test_bare_mention_not_detected(command):
    assert detects(command) is False


# --- cross-model 110.F3 (P0): quoted and module-form invocations bypassed the gate --
#
# strip_literals blanked the quoted script path, so the regex never saw it. These are
# real invocations that ran open_draft.py with no voice or provenance check.

@pytest.mark.parametrize("command", [
    "python3 'tools/open_draft.py'",
    'python3 "tools/open_draft.py"',
    "PYTHONIOENCODING=utf-8 python3 'tools/open_draft.py'",
    "python3 -m tools.open_draft",
    "PYTHONIOENCODING=utf-8 python3 -m tools.open_draft",
    "python3 -mtools.open_draft",
    "cd tools && python3 -m open_draft",
    "python3 -X utf8 'tools/open_draft.py'",
    "python3 -u \"$HOME/repo/tools/open_draft.py\"",
    "env PYTHONIOENCODING=utf-8 python3 'tools/open_draft.py'",
    "true; python3 'tools/open_draft.py'",
    "ls\npython3 'tools/open_draft.py'",
])
def test_quoted_and_module_invocations_are_detected(command):
    assert detects(command) is True


@pytest.mark.parametrize("command", [
    "python3 -m pytest tests/ -k open_draft",             # module is pytest
    "python3 tools/other.py 'tools/open_draft.py'",      # open_draft is an ARGUMENT
    "git commit -m \"python3 'tools/open_draft.py'\"",   # inside a commit message
    "git commit -F - <<'EOF'\npython3 'tools/open_draft.py'\nEOF",   # heredoc body
    "grep -n \"python3 -m tools.open_draft\" docs/",      # grep pattern
])
def test_quoted_mentions_that_do_not_run_it_are_not_detected(command):
    assert detects(command) is False


def test_a_plain_flag_before_a_quoted_path_is_skipped():
    assert detects("python3 -u 'tools/open_draft.py'") is True
    assert detects("python3 -B -u 'tools/open_draft.py'") is True


def test_a_c_program_string_is_not_a_script_argument():
    assert detects("python3 -c 'open_draft.py'") is False


def test_unbalanced_quotes_fall_back_without_raising():
    """shlex raises on an unterminated quote; the regex pass must stand alone, never a
    crash in a hook that fires on every Bash call."""
    assert detects("echo 'unterminated") is False
    assert detects("echo 'unterminated\npython3 'tools/open_draft.py'") is True
