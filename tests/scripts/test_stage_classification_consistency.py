"""Codified invariants that stop the fable-audit Theme 2 bug class from recurring.

The bug class: the freeform-pipeline-stage classification logic (terminal? active
pursuit?) was reimplemented — inconsistently — in pipe_read, pipeline_staleness,
networking_read and outreach_pending. Fixtures stayed green while the tools disagreed
on real data (pipe_read counted 29 closed companies as "active"; it and
pipeline_staleness reported different totals on the same pipeline).

These tests enforce, automatically:
  1. SINGLE SOURCE OF TRUTH — the four consumers must import from stage_vocab and must
     NOT define their own terminal/active-stage keyword sets (prevents re-duplication).
  2. CROSS-TOOL PARITY — pipe_read and pipeline_staleness must agree on total_active /
     total_stalled, both on a rich synthetic fixture (always) and on the live pipeline
     (in the owner's env; skipped in public/CI where data/ is absent).

If a future change reintroduces a local classifier or breaks the parity, one of these
fails loudly instead of shipping a silently-wrong pipeline count.
"""
import re
import sys
from pathlib import Path

import pytest

from conftest import run_script, write_fixture, TOOLS_DIR

CONSUMERS = ["pipe_read.py", "pipeline_staleness.py", "networking_read.py", "outreach_pending.py"]

# Local re-definitions that used to cause the drift — none of these may reappear in a
# consumer (they belong only in stage_vocab.py).
_BANNED_LOCAL_DEFS = [
    re.compile(r"^\s*TERMINAL_STAGES\s*=", re.M),
    re.compile(r"^\s*TERMINAL_STAGE_PREFIXES\s*=", re.M),
    re.compile(r"^\s*TERMINAL_KEYWORDS", re.M),
    re.compile(r"^\s*_CLOSED_STAGE_PREFIXES\s*=", re.M),
    re.compile(r"^\s*ACTIVE_PURSUIT_KEYWORDS\s*=", re.M),
    re.compile(r"^\s*def\s+_?is_active_pursuit\b", re.M),
    re.compile(r"^\s*def\s+is_terminal_stage\b", re.M),
]


# ---------------------------------------------------------------------------
# 1. Single source of truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", CONSUMERS)
def test_consumer_imports_shared_stage_vocab(fname):
    src = (TOOLS_DIR / fname).read_text(encoding="utf-8")
    assert "from stage_vocab import" in src, (
        f"{fname} must classify stages via the shared stage_vocab module, not its own logic")


@pytest.mark.parametrize("fname", CONSUMERS)
def test_consumer_has_no_local_stage_classifier(fname):
    src = (TOOLS_DIR / fname).read_text(encoding="utf-8")
    offenders = [p.pattern for p in _BANNED_LOCAL_DEFS if p.search(src)]
    assert not offenders, (
        f"{fname} re-defines stage-classification logic locally ({offenders}); "
        f"it must use tools/stage_vocab.py so the four consumers can't drift apart")


# ---------------------------------------------------------------------------
# 2. Cross-tool parity
# ---------------------------------------------------------------------------

# A fixture that exercises the full freeform variety that broke the tools:
# active pursuit, aged active, backlog, exact-closed, descriptive-closed (with an active
# keyword buried in it), considered-passed, deprioritized, and a separator row.
RICH_PIPELINE = """\
    # Job Pipeline

    ## Active
    | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
    | --- | --- | --- | --- | --- | --- | --- | --- |
    | ActiveFresh | PM | Applied | 2026-02-25 | Follow up | — | — | — |
    | ActiveStale | PM | Applied | 2026-01-01 | Follow up | — | — | — |
    | Backlog | PM | To Evaluate | 2026-01-01 | Look into it | — | — | — |
    | Paused | PM | Deprioritized | 2026-01-01 | — | — | — | — |
    | ClosedExact | PM | Rejected | 2026-01-01 | — | — | — | — |
    | ClosedDesc | PM | Closed - rejected after final round | 2026-01-01 | — | — | — | — |
    | Declined | PM | Founder intro complete (a CEO) - declined, no current fit | 2026-01-01 | — | — | — | — |
    | SelfPass | PM | Considered - passed (self, 7/7) | 2026-01-01 | — | — | — | — |

    ## Archived
    | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
    | --- | --- | --- | --- | --- | --- | --- | --- |
    | OldWithdrawn | PM | Withdrawn | 2025-12-01 | — | — | — | — |
"""


def _metrics(script, repo_root, target="2026-02-28"):
    r = run_script(script, "--target-date", target, "--repo-root", str(repo_root))
    return r["metrics"]["total_active"], r["metrics"]["total_stalled"]


def test_pipe_read_and_pipeline_staleness_agree_on_rich_fixture(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", RICH_PIPELINE)
    pr = _metrics("pipe_read.py", tmp_path)
    ps = _metrics("pipeline_staleness.py", tmp_path)
    assert pr == ps, f"pipe_read {pr} != pipeline_staleness {ps} on the rich fixture"
    # Classification is correct: active = ActiveFresh, ActiveStale, Backlog, Paused (4)
    # — backlog + deprioritized are non-terminal so they COUNT as active, they're just
    # never stalled; the 4 closed variants + the Archived-section row are excluded.
    # Stalled = only ActiveStale (aged active pursuit).
    assert pr == (4, 1), pr


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not (REPO_ROOT / "data" / "job-pipeline.md").exists(),
                    reason="live pipeline not present (public/CI env)")
def test_pipe_read_and_pipeline_staleness_agree_on_live_pipeline():
    """The exact bug that shipped: the two tools disagreed on the real pipeline. This
    runs only in the owner's env (data/ is gitignored) and fails if they ever diverge."""
    import datetime  # noqa
    # target date is irrelevant to active/stalled parity as long as both use the same one
    pr = _metrics("pipe_read.py", REPO_ROOT, target="2026-07-14")
    ps = _metrics("pipeline_staleness.py", REPO_ROOT, target="2026-07-14")
    assert pr == ps, f"pipe_read {pr} != pipeline_staleness {ps} on the LIVE pipeline"
