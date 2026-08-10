"""Tests for tools/hook_trace.py — the size-capped shared trace-log writer.

Regression target (fable-audit Theme 3): tools/.hook-trace.log grew unbounded
past 1MB because both auto-capture hooks appended with no rotation.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import hook_trace


def test_append_writes_line(tmp_path):
    log = tmp_path / ".hook-trace.log"
    hook_trace.append(log, "hello")
    assert log.read_text(encoding="utf-8") == "hello\n"


def test_append_adds_single_newline(tmp_path):
    log = tmp_path / ".hook-trace.log"
    hook_trace.append(log, "already-newlined\n")
    hook_trace.append(log, "second")
    assert log.read_text(encoding="utf-8") == "already-newlined\nsecond\n"


def test_rotates_when_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_trace, "MAX_TRACE_BYTES", 50)
    log = tmp_path / ".hook-trace.log"
    # Fill past the cap.
    for i in range(20):
        hook_trace.append(log, f"line-{i:03d}-padding-padding")
    # Rotation must have kicked in: a .1 sibling exists and the live log is small.
    rotated = tmp_path / ".hook-trace.log.1"
    assert rotated.exists(), "expected rotated generation"
    assert log.stat().st_size < 200, "live log should be small after rotation"
    # Total disk stays bounded at ~2x the cap (live + one .1).
    assert list(tmp_path.glob(".hook-trace.log*")) != []
    assert not (tmp_path / ".hook-trace.log.2").exists()


def test_never_raises_on_bad_path(tmp_path):
    # A path whose parent is a file (can't mkdir under it) must not raise.
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "nested" / ".hook-trace.log"
    hook_trace.append(bad, "should be swallowed")  # no exception
