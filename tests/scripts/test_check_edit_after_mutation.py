"""Tests for tools/check_edit_after_mutation.py (read-state guard hook).

Drives the hook end-to-end via subprocess, with the session-state file
redirected to a tmp path via SESSION_MUTATIONS_FILE. mtimes are advanced
deterministically with os.utime (no sleeps).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "tools" / "check_edit_after_mutation.py"


def run(mode, payload, state_file):
    env = dict(os.environ, SESSION_MUTATIONS_FILE=str(state_file))
    return subprocess.run(
        [sys.executable, str(HOOK), "--mode", mode],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


@pytest.fixture
def workspace(tmp_path):
    target = tmp_path / "networking.md"
    target.write_text("orig\n", encoding="utf-8")
    state = tmp_path / ".session-mutations.json"
    return target, state


def read_payload(path, session="S1"):
    return {"tool_name": "Read", "session_id": session,
            "tool_input": {"file_path": str(path)}}


def edit_payload(path, session="S1"):
    return {"tool_name": "Edit", "session_id": session,
            "tool_input": {"file_path": str(path), "old_string": "a",
                           "new_string": "b"}}


def write_payload(path, session="S1"):
    return {"tool_name": "Write", "session_id": session,
            "tool_input": {"file_path": str(path), "content": "x"}}


def _bump_mtime(path):
    later = time.time() + 100
    os.utime(path, (later, later))


def test_fresh_read_then_edit_is_silent(workspace):
    target, state = workspace
    assert run("record", read_payload(target), state).returncode == 0
    r = run("check", edit_payload(target), state)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_external_mutation_warns(workspace):
    target, state = workspace
    run("record", read_payload(target), state)
    _bump_mtime(target)  # simulate a script/Bash write between Read and Edit
    r = run("check", edit_payload(target), state)
    assert r.returncode == 0
    assert "changed on disk" in r.stdout


def test_unread_file_warns(workspace):
    target, state = workspace
    r = run("check", edit_payload(target), state)
    assert r.returncode == 0
    assert "have not Read" in r.stdout


def test_new_session_no_stale_false_positive(workspace):
    target, state = workspace
    run("record", read_payload(target, session="S1"), state)
    _bump_mtime(target)
    r = run("check", edit_payload(target, session="S2"), state)
    assert r.returncode == 0
    # Different session: must NOT emit a (wrong) stale claim; unread is correct.
    assert "changed on disk" not in r.stdout
    assert "have not Read" in r.stdout


def test_write_is_not_checked(workspace):
    target, state = workspace
    run("record", read_payload(target), state)
    _bump_mtime(target)
    r = run("check", write_payload(target), state)
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # Write (full rewrite) is intentionally not checked


def test_record_via_write_then_edit_silent(workspace):
    target, state = workspace
    # A Write records the post-write mtime; a subsequent Edit with no
    # intervening mutation should be silent.
    assert run("record", write_payload(target), state).returncode == 0
    r = run("check", edit_payload(target), state)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_on_garbage_stdin():
    r = subprocess.run([sys.executable, str(HOOK), "--mode", "check"],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_record_then_re_read_clears_warning(workspace):
    target, state = workspace
    run("record", read_payload(target), state)
    _bump_mtime(target)
    # stale -> warns
    assert "changed on disk" in run("check", edit_payload(target), state).stdout
    # Claude re-Reads (records the new mtime) -> next check is silent
    run("record", read_payload(target), state)
    r = run("check", edit_payload(target), state)
    assert r.stdout.strip() == ""
