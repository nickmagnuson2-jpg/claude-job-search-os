"""Tests for tools/backfill_transcript_failures.py's surface/nature derivation.

Regression for a code-review finding (2026-07-08): this script had its own
duplicate derive_surface()/derive_nature() that never delegated to
tools/friction_surface.py (the shared module explicitly built to be the
single source of truth for the two live hooks). Wired to delegate so this
script's attribution benefits from the same fixes (heredoc detection,
PascalCase exception matching, PreToolUse-block attribution) instead of
silently drifting from them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import backfill_transcript_failures as bf  # noqa: E402
import friction_surface as fs  # noqa: E402


def test_delegates_to_shared_friction_surface_module():
    assert bf.fs is fs
    assert bf.EXCLUDE_SCRIPTS is fs.EXCLUDE_SCRIPTS
    assert bf.SCRIPT_RE is fs.SCRIPT_RE


def test_heredoc_attribution_now_reaches_this_script():
    # Before the fix, this script's own derive_surface had no heredoc
    # handling at all and would return "bash:cd" here.
    cmd = "cd /repo && python3 <<'EOF'\nimport ss_log_append\nEOF"
    err = 'File "<string>", line 1\nAttributeError: nope'
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_nature_tag_matches_prior_auto_backfill_format():
    nature = fs.derive_nature("SomeError: bad thing", "auto-backfill")
    assert nature.startswith("[auto-backfill] ")
