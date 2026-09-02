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
    env = dict(os.environ, SESSION_MUTATIONS_FILE=str(state_file),
               SESSION_REPO_ROOT=str(Path(state_file).parent))
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


# ===========================================================================
# Bash read recording (added 2026-08-18)
#
# The ledger recorded only the Read tool, so a file opened with cat/sed/head was
# invisible. Measured while building this: the LIVE ledger held one file while
# dozens had genuinely been read via Bash, because the harness instructs
# Bash-first reading. Any downstream "was this cited file actually read?" gate
# built on that ledger would have fired on almost every correct session.
#
# The extractor is deliberately conservative: over-recording is the dangerous
# direction, since a phantom entry would tell such a gate that an unread file
# was read -- exactly what the gate exists to catch.
# ===========================================================================
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("_ceam", HOOK)
_ceam = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_ceam)
extract_bash_read_targets = _ceam.extract_bash_read_targets


def bash_payload(command, session="S1"):
    return {"tool_name": "Bash", "session_id": session,
            "tool_input": {"command": command}}


def _recorded(state_file):
    if not Path(state_file).exists():
        return {}
    return json.loads(Path(state_file).read_text(encoding="utf-8")).get("files", {})


def test_bash_cat_is_recorded(workspace):
    target, state = workspace
    run("record", bash_payload(f"cat {target}"), state)
    assert str(target.resolve()) in _recorded(state)


def test_bash_sed_range_read_is_recorded(workspace):
    target, state = workspace
    run("record", bash_payload(f"sed -n '1,50p' {target}"), state)
    assert str(target.resolve()) in _recorded(state)


def test_bash_read_satisfies_the_edit_check(workspace):
    """The point of the whole change: a Bash read must count as having read it."""
    target, state = workspace
    run("record", bash_payload(f"cat {target}"), state)
    r = run("check", edit_payload(target), state)
    assert "have not Read" not in r.stdout


def test_pipeline_records_the_reading_stage(workspace):
    target, state = workspace
    run("record", bash_payload(f"cat {target} | grep orig"), state)
    assert str(target.resolve()) in _recorded(state)


def test_nonexistent_path_is_not_recorded(tmp_path, workspace):
    """A phantom entry would falsely tell a downstream gate the file was read."""
    _, state = workspace
    run("record", bash_payload(f"cat {tmp_path/'ghost.md'}"), state)
    assert _recorded(state) == {}


def test_quoted_pattern_operand_is_not_recorded(workspace):
    target, state = workspace
    run("record", bash_payload(f'grep -n "orig" {target}'), state)
    files = _recorded(state)
    assert str(target.resolve()) in files
    assert not any("orig" in Path(f).name for f in files)


def test_write_command_is_not_recorded_as_a_read(workspace):
    """`echo x > f` is a write, not a read -- it must not enter the read ledger."""
    target, state = workspace
    run("record", bash_payload(f"echo hello > {target}"), state)
    assert _recorded(state) == {}


def test_flags_are_not_treated_as_paths(workspace):
    target, state = workspace
    run("record", bash_payload(f"head -20 {target}"), state)
    assert list(_recorded(state)) == [str(target.resolve())]


def test_absolute_path_command_prefix_resolves(workspace):
    target, state = workspace
    run("record", bash_payload(f"/usr/bin/grep -c orig {target}"), state)
    assert str(target.resolve()) in _recorded(state)


def test_unknown_command_records_nothing(workspace):
    target, state = workspace
    run("record", bash_payload(f"python3 {target}"), state)
    assert _recorded(state) == {}


# --- extractor unit tests ---------------------------------------------------

def test_extractor_ignores_paths_outside_repo(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    got = extract_bash_read_targets(f"cat {outside}", Path("/nonexistent-root"))
    assert got == []


def test_extractor_handles_multiple_segments(tmp_path):
    a = tmp_path / "a.md"; a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.md"; b.write_text("x", encoding="utf-8")
    got = extract_bash_read_targets(f"cat {a}; head {b}", tmp_path)
    assert len(got) == 2


# --- guards that survived the first mutation pass ---------------------------
# Diagnosed per HOOK_AUTHORING.md's order (weak assertion -> self-consistency ->
# unreachable -> equivalent). Three of the four survivors were reachable and are
# killed below; the remaining two filters are genuinely redundant and are labelled
# as such rather than left ambiguous.

def test_extractor_excludes_nonexistent_paths_directly(tmp_path):
    """Kills the 'existence filter dropped' mutant. Through the hook this guard is
    masked by the downstream mtime lookup (which also drops missing files), so it
    has to be exercised on the extractor itself."""
    got = extract_bash_read_targets(f"cat {tmp_path/'ghost.md'}", tmp_path)
    assert got == []


def test_extractor_skips_a_flag_even_when_a_file_of_that_name_exists(tmp_path):
    """Kills the 'flag filter dropped' mutant. Normally `-20` names nothing and the
    existence check hides the flag filter; here a file literally named `-20` exists,
    so only the flag filter can keep it out."""
    (tmp_path / "-20").write_text("x", encoding="utf-8")
    real = tmp_path / "notes.md"
    real.write_text("x", encoding="utf-8")
    got = extract_bash_read_targets(f"head -20 {real}", tmp_path)
    assert got == [str(real.resolve())]


def test_pipeline_split_finds_a_read_in_a_later_stage(tmp_path):
    """Kills the 'pipeline not split' mutant. Without splitting on `|`, the segment
    begins with `python3`, which is not an allowlisted read command, so the `cat`
    in the second stage would never be inspected."""
    target = tmp_path / "notes.md"
    target.write_text("x", encoding="utf-8")
    got = extract_bash_read_targets(f"python3 build.py | cat {target}", tmp_path)
    assert got == [str(target.resolve())]
