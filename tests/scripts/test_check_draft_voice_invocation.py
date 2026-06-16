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
    """Mirror the hook's real test: regex over the literal-stripped command."""
    return bool(cdv.OPEN_DRAFT_INVOKE.search(cdv.strip_literals(command)))


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
