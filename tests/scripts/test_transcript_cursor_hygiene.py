"""Tests for cursor pruning + atomic save in tools/scan_transcript_failures.py.

Regression target (fable-audit Theme 3): the per-session cursor retained every
session ever scanned (133 dead sessions) and wrote non-atomically, so a crash
mid-write could reset every session's scan position.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

MOD_PATH = REPO_ROOT / "tools" / "scan_transcript_failures.py"
spec = importlib.util.spec_from_file_location("scan_transcript_failures", MOD_PATH)
stf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stf)


def _mk_state(n):
    """n sessions with monotonically increasing last_seen_ts."""
    return {
        f"session-{i:03d}": {
            "last_line": i,
            "last_seen_ts": f"2026-07-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00",
            "logged_ids": [],
        }
        for i in range(n)
    }


def test_prune_keeps_only_max_most_recent(monkeypatch):
    monkeypatch.setattr(stf, "MAX_CURSOR_SESSIONS", 5)
    # Build with explicit ordering: higher index = newer timestamp.
    state = {
        f"s{i}": {"last_line": i, "last_seen_ts": f"2026-07-01T00:00:{i:02d}"}
        for i in range(20)
    }
    pruned = stf.prune_cursor(state)
    assert len(pruned) == 5
    # The 5 newest (s15..s19) survive; older ones are dropped.
    assert set(pruned) == {"s15", "s16", "s17", "s18", "s19"}


def test_prune_noop_under_cap(monkeypatch):
    monkeypatch.setattr(stf, "MAX_CURSOR_SESSIONS", 50)
    state = _mk_state(10)
    assert stf.prune_cursor(state) == state


def test_prune_drops_legacy_no_timestamp_first(monkeypatch):
    monkeypatch.setattr(stf, "MAX_CURSOR_SESSIONS", 2)
    state = {
        "legacy": {"last_line": 1},  # no last_seen_ts -> sorts oldest
        "old": {"last_line": 2, "last_seen_ts": "2026-07-01T00:00:00"},
        "new": {"last_line": 3, "last_seen_ts": "2026-07-10T00:00:00"},
    }
    pruned = stf.prune_cursor(state)
    assert "legacy" not in pruned
    assert set(pruned) == {"old", "new"}


def test_save_cursor_atomic_and_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(stf, "CURSOR_FILE", tmp_path / ".transcript-scan-cursor.json")
    monkeypatch.setattr(stf, "MAX_CURSOR_SESSIONS", 3)
    stf.save_cursor(_mk_state(10))
    loaded = stf.load_cursor()
    assert len(loaded) == 3
    # No temp orphan left behind after a successful atomic rename.
    assert list(tmp_path.glob("*.tmp")) == []
