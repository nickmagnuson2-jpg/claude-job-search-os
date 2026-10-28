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
    "mcp__claude_ai_Gmail__list_labels",   # gated 2026-10-22: gmail_fetch.py --list-labels
]
ALLOWED_TOOLS = [
    "mcp__claude_ai_Gmail__create_draft",
    "mcp__claude_ai_Gmail__label_message",
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


def test_list_labels_block_names_the_matching_script_command():
    p = subprocess.run([sys.executable, HOOK], capture_output=True, text=True,
                       input=json.dumps({"tool_name": "mcp__claude_ai_Gmail__list_labels",
                                         "tool_input": {}}))
    assert p.returncode == 2 and "gmail_fetch.py --list-labels" in p.stderr
    assert "--search" not in p.stderr, "a label lookup should not be pointed at search"


def test_every_gated_tool_is_routed_to_the_hook_by_the_wired_matcher():
    """GATED_READ_TOOLS and the settings.json matcher are two lists of the same tools. A
    tool added to one only (list_labels, 2026-10-22) is gated in a test and never in use."""
    import re
    settings = json.load(open(os.path.join(os.path.dirname(HOOK), "..", ".claude", "settings.json")))
    matchers = [m["matcher"] for m in settings["hooks"]["PreToolUse"]
                if any("check_gmail_read_mcp" in c["command"] for c in m["hooks"])]
    assert matchers, "the hook is not wired"
    unrouted = {t for t in h.GATED_READ_TOOLS if not any(re.fullmatch(m, t) for m in matchers)}
    assert not unrouted, f"gated in the hook but never routed to it: {sorted(unrouted)}"


def test_a_calendar_call_routed_here_by_a_stale_session_is_blocked():
    """Hooks may be read at session start, so a session that started before the
    2026-10-28 split still sends calendar tools here. Neither override lets one through."""
    for t in ("create_event", "list_events", "some_future_tool"):
        payload = {"tool_name": "mcp__claude_ai_Google_Calendar__" + t, "tool_input": {}}
        assert _run(payload, {"CALENDAR_MCP_OVERRIDE": "1"}) == 2, t
        assert _run(payload, {"GMAIL_MCP_OVERRIDE": "1"}) == 2, t
    assert _run({"tool_name": "mcp__claude_ai_Google_Calendar", "tool_input": {}}) == 0


def test_the_stale_session_block_says_why_and_how_to_fix_it():
    p = subprocess.run([sys.executable, HOOK], capture_output=True, text=True,
                       input=json.dumps({"tool_name": "mcp__claude_ai_Google_Calendar__create_event",
                                         "tool_input": {}}))
    assert p.returncode == 2
    assert "check_calendar_mcp.py" in p.stderr and "Restart the session" in p.stderr
