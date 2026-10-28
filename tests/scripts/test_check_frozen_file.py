"""The freeze guard: a Write or Edit to a file with no owner-write bit (chmod a-w, how a build
freezes its shipped files) is blocked, so a frozen file cannot change without an explicit,
logged unlock.

Origin 2026-10-27: during a build freeze the Edit tool wrote a read-only file, and the file
silently differed from its freeze snapshot.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_frozen_file as cff  # noqa: E402

SCRIPT = Path(cff.__file__).resolve()


def run(payload) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(SCRIPT)], input=data, capture_output=True,
                          text=True, timeout=30)


def frozen(tmp_path: Path, name="talk-track.md") -> Path:
    p = tmp_path / name
    p.write_text("frozen\n")
    p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return p


def writable(tmp_path: Path, name="notes.md") -> Path:
    p = tmp_path / name
    p.write_text("open\n")
    return p


@pytest.fixture(autouse=True)
def _restore_modes(tmp_path):
    yield
    for p in tmp_path.rglob("*"):
        if p.is_file():
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


def test_an_edit_to_a_frozen_file_blocks(tmp_path):
    p = frozen(tmp_path)
    r = run({"tool_name": "Edit", "tool_input": {"file_path": str(p), "old_string": "a",
                                                  "new_string": "b"}})
    assert r.returncode == 2
    assert "frozen" in r.stderr and "chmod u+w" in r.stderr and str(p) in r.stderr


def test_a_write_over_a_frozen_file_blocks(tmp_path):
    p = frozen(tmp_path)
    r = run({"tool_name": "Write", "tool_input": {"file_path": str(p), "content": "x"}})
    assert r.returncode == 2


def test_a_multiedit_to_a_frozen_file_blocks(tmp_path):
    p = frozen(tmp_path)
    r = run({"tool_name": "MultiEdit", "tool_input": {"file_path": str(p), "edits": []}})
    assert r.returncode == 2


def test_an_edit_to_a_writable_file_passes(tmp_path):
    p = writable(tmp_path)
    r = run({"tool_name": "Edit", "tool_input": {"file_path": str(p), "old_string": "a",
                                                  "new_string": "b"}})
    assert r.returncode == 0, r.stderr


def test_a_write_to_a_new_file_passes(tmp_path):
    r = run({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "new.md"),
                                                   "content": "x"}})
    assert r.returncode == 0, r.stderr


def test_an_unlocked_file_passes(tmp_path):
    """The unlock step (chmod u+w) is the way through, by design."""
    p = frozen(tmp_path)
    p.chmod(p.stat().st_mode | stat.S_IWUSR)
    r = run({"tool_name": "Edit", "tool_input": {"file_path": str(p)}})
    assert r.returncode == 0, r.stderr


def test_group_write_alone_does_not_unfreeze(tmp_path):
    """The owner bit is the one the freeze removes and the one that decides."""
    p = frozen(tmp_path)
    p.chmod(stat.S_IRUSR | stat.S_IWGRP | stat.S_IRGRP)
    r = run({"tool_name": "Edit", "tool_input": {"file_path": str(p)}})
    assert r.returncode == 2


def test_a_relative_path_resolves_against_cwd(tmp_path):
    frozen(tmp_path, "rel.md")
    r = run({"tool_name": "Edit", "cwd": str(tmp_path), "tool_input": {"file_path": "rel.md"}})
    assert r.returncode == 2


def test_a_frozen_directory_target_is_not_this_guards_business(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    r = run({"tool_name": "Write", "tool_input": {"file_path": str(d)}})
    assert r.returncode == 0


def test_a_tool_that_does_not_write_passes(tmp_path):
    p = frozen(tmp_path)
    r = run({"tool_name": "Read", "tool_input": {"file_path": str(p)}})
    assert r.returncode == 0


def test_a_payload_with_no_path_passes(tmp_path):
    r = run({"tool_name": "Edit", "tool_input": {}})
    assert r.returncode == 0


def test_an_unreadable_payload_fails_open():
    r = run("not json")
    assert r.returncode == 0


def test_is_frozen_is_false_for_a_missing_file(tmp_path):
    assert cff.is_frozen(tmp_path / "nope.md") is False


def test_is_frozen_reads_the_owner_bit(tmp_path):
    assert cff.is_frozen(frozen(tmp_path)) is True
    assert cff.is_frozen(writable(tmp_path)) is False


# --- review round 1 (2026-10-27, runs 734/735) -------------------------------------------

def test_a_notebookedit_to_a_frozen_notebook_blocks(tmp_path):
    """734.F2 / 735.F3: NotebookEdit sends notebook_path, not file_path."""
    p = frozen(tmp_path, "nb.ipynb")
    r = run({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(p),
                                                          "new_source": "x"}})
    assert r.returncode == 2


def test_an_oversized_write_to_a_frozen_file_blocks(tmp_path):
    """734.F3 / 735.F4: a payload past the stdin cap is truncated; the path still decides."""
    p = frozen(tmp_path)
    big = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(p),
                                                           "content": "x" * (2 * 1024 * 1024)}})
    r = run(big)
    assert r.returncode == 2


def test_an_oversized_write_to_a_writable_file_passes(tmp_path):
    p = writable(tmp_path)
    big = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(p),
                                                           "content": "x" * (2 * 1024 * 1024)}})
    assert run(big).returncode == 0


def test_a_malformed_payload_that_was_not_cut_off_fails_open(tmp_path):
    """Only truncation is read from the raw text; garbage JSON stays fail-open."""
    p = frozen(tmp_path)
    r = run('{"tool_name": "Write", "tool_input": {"file_path": "%s", "content": "x"' % p)
    assert r.returncode == 0


def test_a_cut_off_write_whose_path_is_past_the_cap_blocks(tmp_path):
    """736.F2 / 737.F2: a write the guard cannot see the target of is not waved through."""
    p = frozen(tmp_path)
    raw = '{"tool_name": "Write", "tool_input": {"content": "' + "x" * (2 * 1024 * 1024) + \
          '", "file_path": "%s"}}' % p
    r = run(raw)
    assert r.returncode == 2 and "too large" in r.stderr


def test_a_cut_off_payload_with_an_unreadable_path_fails_open(tmp_path):
    raw = '{"tool_name": "Write", "tool_input": {"file_path": "/tmp/a\\qb", "content": "' + \
          "x" * (2 * 1024 * 1024)
    r = run(raw)
    assert r.returncode == 0, r.stderr


def test_a_cut_off_payload_with_no_tool_name_fails_open(tmp_path):
    p = frozen(tmp_path)
    raw = '{"tool_input": {"file_path": "%s", "content": "' % p + "x" * (2 * 1024 * 1024)
    r = run(raw)
    assert r.returncode == 0, r.stderr
