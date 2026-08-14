"""Tests for tools/check_guard_edit_approval.py — the guard-edit approval hook.

Content hook, so the false-positive surface is PATH SCOPE (tools/HOOK_AUTHORING.md).
The canonical trap for a content hook is judging its own fixtures; these tests pin
that `tests/` is excluded, or the suite could not run at all.

Origin: 2 fires of feedback_never_modify_guard_hook_to_unblock_self (2026-07-28,
2026-08-14), both "the fix is correct" and both the wrong sequence.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_guard_edit_approval.py"


def _run(path, tool="Edit", env=None):
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


# --- BLOCKS: guard infrastructure -------------------------------------------

@pytest.mark.parametrize("path", [
    "tools/check_todo_write_kwargs.py",          # the 2026-08-14 fire
    "tools/check_email_via_skill.py",            # the 2026-07-28 fire
    "tools/check_public_pii.py",
    "tools/hook_command_lint.py",                # shared strip logic; weakening it weakens every command hook
    ".claude/settings.json",                     # the wiring is guard infrastructure too
    ".claude/settings.local.json",
    "/Users/mag/Documents/Obsidian/30-projects/job-search/tools/check_bare_python.py",
])
def test_blocks_guard_edits(path):
    code, err = _run(path)
    assert code == 2, err
    assert "BLOCKED" in err


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_blocks_across_all_write_surfaces(tool):
    """Switching tool surface to dodge the matcher is the documented sibling bypass."""
    code, _ = _run("tools/check_public_pii.py", tool=tool)
    assert code == 2


def test_creating_a_brand_new_guard_is_also_blocked():
    """Deliberate. Building a guard is normal work, but Nick should know it is happening;
    the override makes it one keystroke rather than an argument."""
    code, _ = _run("tools/check_something_new.py", tool="Write")
    assert code == 2


def test_block_message_names_the_path_and_the_override():
    code, err = _run("tools/check_public_pii.py")
    assert code == 2
    assert "check_public_pii.py" in err
    assert "GUARD_EDIT_APPROVED=1" in err
    assert "WAIT for his yes" in err


def test_block_message_names_the_contract_changed_rationalization():
    """The 2026-08-14 fire's specific reasoning must be pre-refuted in the message,
    or the next session reconstructs it from scratch."""
    _, err = _run("tools/check_public_pii.py")
    assert "ask FASTER" in err


def test_block_message_gives_the_fp_logging_path():
    """PreToolUse blocks are invisible to the auto-logger — manual logging is the
    ONLY telemetry path for a false positive (HOOK_AUTHORING.md)."""
    _, err = _run("tools/check_public_pii.py")
    assert "friction_log.py append" in err


# --- CLEAN: the path-scope false-positive surface ---------------------------

@pytest.mark.parametrize("path", [
    "tests/scripts/test_check_guard_edit_approval.py",   # this very file
    "tests/scripts/test_check_todo_write_kwargs.py",     # a test is not a guard
    "tests/fixtures/tools/check_fake.py",
    "tools/HOOK_AUTHORING.md",                           # docs about hooks are not hooks
    "tools/todo_write.py",                               # an ordinary tool
    "tools/friction_log.py",
    "framework/frame-schema.yaml",
    "data/job-todos.md",
    "CLAUDE.md",
    ".claude/skills/apply/SKILL.md",                     # skills are not the hook wiring
])
def test_clean_paths_pass(path):
    code, err = _run(path)
    assert code == 0, f"false positive on {path}: {err}"


def test_its_own_tests_are_not_blocked():
    """THE canonical content-hook trap: a hook that judges its own fixtures makes the
    suite unrunnable. Origin check_prep_doc_format.py, 2026-08-12."""
    code, _ = _run("tests/scripts/test_check_guard_edit_approval.py")
    assert code == 0


def test_non_write_tools_pass():
    for tool in ("Read", "Grep", "Bash", "Glob"):
        code, _ = _run("tools/check_public_pii.py", tool=tool)
        assert code == 0, tool


# --- the override -----------------------------------------------------------

def test_override_allows_the_edit():
    import os
    env = {**os.environ, "GUARD_EDIT_APPROVED": "1"}
    code, _ = _run("tools/check_public_pii.py", env=env)
    assert code == 0


# --- fail-open --------------------------------------------------------------

def test_malformed_json_fails_open():
    """A guard that crashes blocks all work. Fail open on garbage."""
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_missing_file_path_fails_open():
    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_empty_stdin_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="",
                       capture_output=True, text=True)
    assert r.returncode == 0
