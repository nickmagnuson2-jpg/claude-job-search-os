"""Tests for the friction_log.py append-time non-friction guard (D2-H1).

The auto-log hooks (log_tool_failure.py PostToolUseFailure, scan_transcript_
failures.py Stop) funnel through `friction_log.py append`. A failing test run or
success stdout must NOT be recorded as a tool friction. The guard rejects these
at the append chokepoint, reusing is_false_positive() (shared with dedup).
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "friction_log.py"

# Import the module for direct unit access to is_false_positive().
_spec = importlib.util.spec_from_file_location("friction_log", SCRIPT)
friction_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(friction_log)


# --- unit: is_false_positive classification ---------------------------------

@pytest.mark.parametrize("nature", [
    "[auto] ============================= test session starts ====================",
    "[auto] test_foo (tests.test_bar.TestX.test_foo) ... ok",
    "[auto] test_foo (unittest.loader._FailedTest.test_foo) ... ERROR",
    "[auto] test_x ... FAIL",
    "[auto] PDF written to /Users/x/out.pdf",
    "[auto] Total messages: 3",
    "[auto] -rw-r--r--@ 1 mag staff 2120 May 20 data/x.md",
    "audit-2026-05-21.md",
    # broadened patterns (echoed shell scaffolding, not frictions):
    "[auto] === rerun tests after cleanup ===",
    "[auto] ===== global reflect skill? =====",
    "[auto] ...............                                          [100%]",
    '[auto] """Shared pytest configuration for preprocessing script tests."""',
    "[auto] ?? tests/scripts/test_my_world_synthesis.py",
    "[auto] 151 /Users/mag/.claude/skills/ss/SKILL.md",
    "0",
    "[auto] <tool_use_error>Cancelled: parallel tool call Bash(git commit -m)",
    "[auto] test_route (unittest.loader._FailedTest.test_route) collection error",
])
def test_false_positives_detected(nature):
    assert friction_log.is_false_positive(nature) is True


@pytest.mark.parametrize("nature", [
    "[auto] (eval):1: command not found: python",
    "[auto] <tool_use_error>File has not been read yet.</tool_use_error>",
    "[auto] Unknown flag(s): --priority. todo_write.py is positional-only",
    "[auto] Traceback (most recent call last):",
    "Contact not found: Jordan",
    "No entry found for Acme AI / Strategist, Agent Development",
])
def test_real_frictions_not_flagged(nature):
    assert friction_log.is_false_positive(nature) is False


# --- cross-surface logical-friction collapse (the bare-`python` case) --------

def _dedup(led, dry_run=False):
    env = dict(os.environ, FRICTION_LEDGER=str(led), PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, str(SCRIPT), "dedup"]
    if dry_run:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _rows(led, *lines):
    """Write a ledger with the given pre-built table rows."""
    body = ("# Friction Log\n\n## Entries\n\n"
            "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
            "|---|---|---|---|---|---|\n" + "\n".join(lines) + "\n")
    led.write_text(body, encoding="utf-8")
    return led


def test_dedup_collapses_same_nature_across_surfaces(tmp_path):
    """The same error on different surfaces is ONE logical friction."""
    led = _rows(
        tmp_path / "friction-log.md",
        "| 2026-06-02 | pipeline_staleness.py | (eval):1: command not found: python | — | 1 | — |",
        "| 2026-06-01 | remember_classify.py | (eval):1: command not found: python | — | 1 | — |",
        "| 2026-05-31 | bash:cd | (eval):1: command not found: python | — | 1 | — |",
        "| 2026-05-27 | networking_write.py | (eval):1: command not found: python | — | 1 | — |",
    )
    rep = _dedup(led)
    assert rep["rows_after"] == 1
    assert rep["clusters"][0]["occurrences"] == 4
    assert "script-patch" in rep["clusters"][0]["promotion"]
    assert rep["reconciles"] is True  # all 4 source rows accounted for


def test_dedup_sums_existing_counts(tmp_path):
    """occurrences = sum of rows' running counts, not row count."""
    led = _rows(
        tmp_path / "friction-log.md",
        "| 2026-05-27..2026-06-01 | tool:Edit | File has not been read yet. Read it first before writing | — | 3 | script-patch (mandatory) |",
        "| 2026-05-27..2026-06-01 | tool:Write | File has not been read yet. Read it first before writing | — | 2 | memory (do now) |",
    )
    rep = _dedup(led)
    assert rep["rows_after"] == 1
    assert rep["clusters"][0]["occurrences"] == 5  # 3 + 2, not 2


def test_dedup_keeps_distinct_natures_apart(tmp_path):
    """Different errors do NOT merge even at the same/low surface overlap."""
    led = _rows(
        tmp_path / "friction-log.md",
        "| 2026-06-02 | bash:cd | (eval):1: command not found: python | — | 1 | — |",
        "| 2026-06-02 | bash:cd | String to replace not found in file | — | 1 | — |",
    )
    rep = _dedup(led)
    assert rep["rows_after"] == 2


def test_dedup_drops_broadened_fp_rows(tmp_path):
    led = _rows(
        tmp_path / "friction-log.md",
        "| 2026-06-01 | bash:echo | === rerun tests after cleanup === | — | 1 | — |",
        "| 2026-06-02 | pipeline_staleness.py | (eval):1: command not found: python | — | 1 | — |",
    )
    rep = _dedup(led)
    assert rep["dropped_fp"] == 1
    assert rep["rows_after"] == 1
    assert rep["reconciles"] is True  # 1 dropped + 1 clustered == 2 source rows


def test_append_merges_same_nature_new_surface(tmp_path):
    """A recurring interpreter error on a NEW surface increments, not fragments."""
    led = _rows(
        tmp_path / "friction-log.md",
        "| 2026-06-01 | remember_classify.py | (eval):1: command not found: python | — | 1 | — |",
    )
    out = _append(led, "pipeline_staleness.py", "(eval):1: command not found: python")
    assert out["action"] == "updated"
    assert out["occurrences"] == 2


# --- integration: append rejects FP natures, keeps real ones ----------------

def _ledger(tmp_path):
    led = tmp_path / "friction-log.md"
    led.write_text(
        "# Friction Log\n\n## Entries\n\n"
        "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
        "|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    return led


def _append(led, surface, nature):
    env = dict(os.environ, FRICTION_LEDGER=str(led), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "append", surface, nature],
                       capture_output=True, text=True, env=env)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_append_skips_test_output(tmp_path):
    led = _ledger(tmp_path)
    out = _append(led, "bash:cd", "[auto] test_x (tests.test_y.T.test_x) ... FAIL")
    assert out["status"] == "ok"
    assert out["action"] == "skipped"
    assert "| bash:cd |" not in led.read_text(encoding="utf-8")  # nothing written


def test_append_records_real_friction(tmp_path):
    led = _ledger(tmp_path)
    out = _append(led, "todo_write.py", "[auto] Unknown flag(s): --priority")
    assert out["action"] == "appended"
    assert out["occurrences"] == 1
    assert "todo_write.py" in led.read_text(encoding="utf-8")
