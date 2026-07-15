"""Tests for tools/stage_vocab.py — the shared freeform-stage classifier.

Single source of truth for is_terminal_stage / is_active_pursuit across pipe_read,
pipeline_staleness, networking_read, outreach_pending (fable-audit Theme 2).
"""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
from stage_vocab import is_terminal_stage, is_active_pursuit  # noqa: E402


# ---------------------------------------------------------------------------
# is_terminal_stage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", [
    "Closed",
    "Closed - they passed",
    "Closed - they passed (door open)",
    "Closed - no response",
    "Closed - not a fit (CS-shaped, not deployment lane)",
    "Rejected",
    "Withdrawn",
    "Declined",
    "Skipped",
    "Accepted",
    "Archived",
    "Considered - passed (self, 7/7)",
    "Considered - passed (self, 7/7; re-surfaced by a contact 7/14)",
    "Accepted offer",
    "offer accepted",
    # the mid-string case a prefix-only matcher missed (the closed-signal is buried
    # after a "Founder intro complete (...)" narrative — the real regression):
    "Founder intro complete (a founder, CEO, 6/29) - declined FA/CoS, no current fit",
    "Rejected post-onsite",
    "Closed - lost in final round",
    # long real-shaped stage that contains active keywords ("screen") but is closed —
    # must stay terminal:
    "Closed - rejected (7/14): a contact passed after 7/7 HM screen; nearing end of process",
])
def test_terminal_stages(stage):
    assert is_terminal_stage(stage) is True


@pytest.mark.parametrize("stage", [
    "To Evaluate",
    "To Apply",
    "To Apply (warm-intro path)",
    "Researching",
    "Applied",
    "Applied 2026-07-08 via a recruiter - CV emailed",
    "Phone Screen",
    "Interviewing",
    "Offer",
    "Outreach",
    "Outreach Sent",
    "Deprioritized",          # paused, NOT over
    "Strategic Thinking round complete (6/8)",
    "passed the screen",      # mid-string "passed" is NOT terminal (still active)
    "They passed on me",      # bare "passed" without a closed/rejected co-signal
    "Passed",
    "Closing round scheduled",  # "closing" != "closed"
    # the HIGH regression guard: bare "accepted" (accepted an intro/invite, not an
    # accepted OFFER) must NOT read as terminal — that would silently drop a live opp:
    "Accepted intro, screening next",
    "",
])
def test_non_terminal_stages(stage):
    assert is_terminal_stage(stage) is False


def test_none_is_not_terminal():
    assert is_terminal_stage(None) is False


# ---------------------------------------------------------------------------
# is_active_pursuit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", [
    "Applied",
    "Researching",         # stem of "research"
    "Phone Screen",
    "Screening",           # stem of "screen"
    "Interviewing",        # stem of "interview"
    "Offer",
    "Recruiter screen",
    "Final round",
    "Onsite loop",
    "Strategic Thinking round complete (6/8)",
])
def test_active_pursuits(stage):
    assert is_active_pursuit(stage) is True


@pytest.mark.parametrize("stage", [
    "To Evaluate",
    "To Apply",
    "Deprioritized",          # backlog: not an active pursuit
    "Outreach",
    "Background check scheduled",  # "round" inside "background" must NOT match
    "showcase",                    # "case" inside "showcase" must NOT match
    "",
])
def test_non_pursuit_stages(stage):
    assert is_active_pursuit(stage) is False


def test_none_is_not_active_pursuit():
    assert is_active_pursuit(None) is False


def test_terminal_stage_is_never_active_pursuit():
    """A closed stage that happens to contain a pursuit keyword ('round', 'screen')
    must NOT be an active pursuit — otherwise a closed row gets flagged stale."""
    assert is_active_pursuit("Closed - lost in final round") is False
    assert is_active_pursuit("Rejected post-onsite") is False
    assert is_active_pursuit("Closed - rejected after HM screen") is False
