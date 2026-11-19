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
    "Closed - not a fit (role too sales-heavy)",
    "Rejected",
    "Withdrawn",
    "Declined",
    "Skipped",
    "Accepted",
    "Archived",
    "Considered - passed (self, 3/2)",
    "Considered - passed (self, 3/2; re-surfaced by a contact 3/9)",
    "Accepted offer",
    "offer accepted",
    # the mid-string case a prefix-only matcher missed (the closed-signal is buried
    # after a "Founder intro complete (...)" narrative — the real regression):
    "Founder intro complete (a founder, CEO, 2/11) - declined the role, no current fit",
    "Rejected post-onsite",
    "Closed - lost in final round",
    # long real-shaped stage that contains active keywords ("screen") but is closed —
    # must stay terminal:
    "Closed - rejected (3/9): a contact passed after 3/2 HM screen; went another way",
])
def test_terminal_stages(stage):
    assert is_terminal_stage(stage) is True


@pytest.mark.parametrize("stage", [
    "To Evaluate",
    "To Apply",
    "To Apply (warm-intro path)",
    "Researching",
    "Applied",
    "Applied 2026-03-04 via a recruiter - resume sent",
    "Phone Screen",
    "Interviewing",
    "Offer",
    "Outreach",
    "Outreach Sent",
    "Deprioritized",          # paused, NOT over
    "Case round complete (3/14)",
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
    "Case round complete (3/14)",
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


# --- Applications (2026-11-18): shared by pipe_write.py (writer) and day_check.py (reader)

def test_applied_dates_reads_each_cell_and_both_phrasings():
    from stage_vocab import applied_dates
    assert applied_dates(["Applied 2026-11-06 via Ashby", "x", "applied on 2026-11-07"]) == [
        "2026-11-06", "2026-11-07"]
    # Stage "Applied" and Date Updated in separate cells are not an applied date.
    assert applied_dates(["Applied", "2026-11-06"]) == []
    assert applied_dates(["reapplied 2026-11-06", None]) == []


def test_is_applied_stage_is_the_bare_stage_only():
    from stage_vocab import is_applied_stage
    assert is_applied_stage(" applied ")
    assert not is_applied_stage("Applied via recruiter")
    assert not is_applied_stage("To Apply")
    assert not is_applied_stage(None)


def test_applied_stamp_is_read_back_as_its_date():
    from stage_vocab import applied_dates, applied_stamp
    assert applied_dates([applied_stamp("2026-11-18")]) == ["2026-11-18"]
