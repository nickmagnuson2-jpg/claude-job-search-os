"""Tests for tools/check_sendmessage_checkin.py (PreToolUse on SendMessage).

The hook BLOCKS (exit 2) a cross-session message that
  (a) reports a "(3) unlogged:" value other than none/nothing (rule 12: log first, then
      message), or
  (b) carries a guessed clock time: HH:Mx / HH:x anywhere outside quotes and backticks, or
      ~HH:MM / about HH:MM in the same clause as a "by `date`" label.
Every case below is a generalized shape of a real message measured on 2026-11-23
(output/overnight-112226/S1-staging/sendmessage-hook/PLAN.md); no real names.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_sendmessage_checkin.py"
sys.path.insert(0, str(ROOT / "tools"))

import check_sendmessage_checkin as hook  # noqa: E402


def run(payload) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=data, text=True,
                          capture_output=True, env={**os.environ}, timeout=30)


def send(message: str, **extra) -> dict:
    tool_input = {"to": "org-session", "message": message, "summary": "check-in", **extra}
    return {"tool_name": "SendMessage", "tool_input": tool_input}


# ---------------------------------------------------------------- (a) unlogged

UNLOGGED_BLOCK = [
    "S9 check-in. (1) changed: two fires recorded. (2) uncommitted: none. "
    "(3) unlogged: the two fires; logging next. (4) blocked on: nothing.",
    "(1) changed: a ruling\n(2) uncommitted: none\n(3) unlogged: one entry is coming before my next message.\n(4) blocked on: none",
    "(3) Unlogged: this ruling; it goes in at the next entry.",
    "Status (1) changed: x (2) uncommitted: none (3) unlogged: C7 r3 and C1 r4; logging next. (4) blocked on: nothing",
    "- (3) unlogged: the widen ruling",
]

UNLOGGED_ALLOW = [
    "(1) changed: x. (2) uncommitted: none. (3) unlogged: none. (4) blocked on: nothing.",
    "(3) unlogged: None\n(4) blocked on: nothing",
    "(3) unlogged: nothing. (4) blocked on: your go.",
    "(3) Unlogged: none. That section went in append only.",
    # a quoted mention of the pattern is not a check-in
    'Build a hook that blocks a message with a "(3) unlogged:" line whose value is not "none".',
    # the format named in prose, no colon
    'Every reply is (1) changed (2) uncommitted (3) unlogged (4) blocked on, in that order.',
    # mid-sentence mention, not line-leading and not after a (2) segment
    "One correction: your (3) unlogged: field showed an entry; write it now.",
]


@pytest.mark.parametrize("msg", UNLOGGED_BLOCK)
def test_unlogged_value_blocks(msg):
    r = run(send(msg))
    assert r.returncode == 2, (msg, r.stderr)
    assert "unlogged" in r.stderr.lower()


@pytest.mark.parametrize("msg", UNLOGGED_ALLOW)
def test_unlogged_none_or_mention_allows(msg):
    r = run(send(msg))
    assert r.returncode == 0, (msg, r.stderr)


# ---------------------------------------------------------------- (b) guessed time

GUESS_BLOCK = [
    "S9 at 07:3x by `date`. (1) changed: C2 committed.",
    "S9 rule-18 inventory (no writes since 07:4x; standing by).",
    "- Log entries at 08:02 and 08:0x.",
    "From org (13:5x): slot correction.",
    "New board rule 10 (owner, 14:4x): state Why before building.",
    "ack at 18:x, reading the report now",
    "B9 ~08:30 by date. Rebased.",
    "check-in at about 08:02 by `date`: suite running",
    "merge-push 18:2X PST: commit 1 is in",
]

GUESS_ALLOW = [
    # quoted or backticked mentions of a guessed time
    'One correction: your message said "07:3x by `date`". That is a typed estimate.',
    "The pattern `07:4x` is what the hook blocks.",
    "Your 'about 08:02' was typed; use `date`.",
    # forecasts are not stamps
    "ETA to READY (estimate): about 11:15.",
    "so the expected end is about 19:20-19:30. I will commit right after.",
    "Install r3's suite follows yours, around 19:05.",
    # real readings
    "S9 08:47:45 by `date`: suite PASS.",
    "Logged at 14:05 PST by `date`.",
    "(1) changed: x. (2) uncommitted: none. (3) unlogged: none. (4) blocked on: nothing.",
    # near-misses for the token
    "ratio 3:1, hex 0x1f, sha 1a2b3c4x, port 8080:80",
    "timestamp 2026-11-23T08:28:39Z",
]


@pytest.mark.parametrize("msg", GUESS_BLOCK)
def test_guessed_time_blocks(msg):
    r = run(send(msg))
    assert r.returncode == 2, (msg, r.stderr)
    assert "date" in r.stderr


@pytest.mark.parametrize("msg", GUESS_ALLOW)
def test_quoted_forecast_or_real_time_allows(msg):
    r = run(send(msg))
    assert r.returncode == 0, (msg, r.stderr)


# ---------------------------------------------------------------- fields, scope, fail-open

def test_content_field_is_judged_when_message_is_clean():
    r = run(send("clean", content="(3) unlogged: one entry"))
    assert r.returncode == 2


def test_other_tools_are_ignored():
    # the payload carries a blocking message field, so only the tool-name check allows it
    r = run({"tool_name": "Bash", "tool_input": {"command": "echo 07:4x",
                                                 "message": "(3) unlogged: one entry at 07:4x"}})
    assert r.returncode == 0


def test_runs_under_isolated_python():
    # python3 -I leaves the script's own directory off sys.path; the hook must still
    # import hook_runtime and block
    data = json.dumps(send("(3) unlogged: one entry"))
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=data, text=True,
                       capture_output=True, timeout=30)
    assert r.returncode == 2, r.stderr


def test_helpers_return_false_not_none():
    assert hook._unlogged("(3) unlogged: none") is False
    assert hook._guessed_time("at 08:47:45") is False


def test_unreadable_payload_fails_open():
    assert run("not json").returncode == 0
    assert run("").returncode == 0


def test_non_string_message_fails_open():
    r = run({"tool_name": "SendMessage", "tool_input": {"to": "x", "message": {"type": "shutdown"}}})
    assert r.returncode == 0


def test_block_message_is_one_line_naming_the_fix():
    r = run(send("(3) unlogged: one entry"))
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith("BLOCKED check_sendmessage_checkin")
    r = run(send("at 07:4x"))
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and "`date`" in lines[0]


def test_judge_returns_the_reason_kind():
    assert hook.judge("(3) unlogged: x") == "unlogged"
    assert hook.judge("at 07:4x") == "guessed_time"
    assert hook.judge("(3) unlogged: none") is None
