"""Tests for tools/check_gmail_read_mcp.py — gates Gmail READ MCP tools only.

Covers the pure decision (should_block) and a live exit-code smoke through main
via subprocess, including the write-tool allow-through and the override.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import check_gmail_read_mcp as h  # noqa: E402

HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "check_gmail_read_mcp.py")

READ_TOOLS = [
    "mcp__claude_ai_Gmail__search_threads",
    "mcp__claude_ai_Gmail__get_message",
    "mcp__claude_ai_Gmail__get_thread",
]
ALLOWED_TOOLS = [
    "mcp__claude_ai_Gmail__create_draft",
    "mcp__claude_ai_Gmail__label_message",
    "mcp__claude_ai_Gmail__list_labels",
    "mcp__claude_ai_Gmail__list_drafts",
    "Bash",
    "Read",
    None,
]


def _run(payload: object, env_extra=None) -> int:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload) if payload is not None else "not json",
        capture_output=True, text=True, env=env,
    )
    return p.returncode


# --- pure decision ----------------------------------------------------------
def test_should_block_read_tools():
    for t in READ_TOOLS:
        assert h.should_block(t) is True


def test_should_not_block_allowed_tools():
    for t in ALLOWED_TOOLS:
        assert h.should_block(t) is False


# --- live exit codes --------------------------------------------------------
def test_read_tools_block_exit_2():
    for t in READ_TOOLS:
        assert _run({"tool_name": t, "tool_input": {}}) == 2, t


def test_allowed_tools_pass_exit_0():
    for t in ALLOWED_TOOLS:
        payload = {"tool_name": t, "tool_input": {}} if t is not None else {"tool_input": {}}
        assert _run(payload) == 0, t


def test_bad_json_fails_open():
    assert _run(None) == 0  # "not json" on stdin


def test_override_bypasses_block():
    assert _run(
        {"tool_name": "mcp__claude_ai_Gmail__search_threads", "tool_input": {}},
        env_extra={"GMAIL_MCP_OVERRIDE": "1"},
    ) == 0
