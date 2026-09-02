"""detect_stale_routed: which routed emails are noise, and the guards against over-firing.

Ported 2026-09-02 from tools/test_act_classify_stale.py, which pytest never collected --
a PASS/FAIL-counter script ending in sys.exit(). One test per behaviour so a failure names
the behaviour instead of printing it to stdout.

WHAT IT GUARDS (2026-06-02). Two classes of routed email are noise: a transactional
notification (a booking confirmation, an application receipt) and a duplicate intro to a
company already engaged in the pipeline. Both are auto-deletable. The risk is not missing
one -- it is deleting something real, so the false-positive guards below carry as much
weight as the detections: a company only at Researching stage may be genuinely new, a
short single-word company name matches coincidental prose, and the transactional
signatures are gmail-only so ordinary notes are never touched.

All fixture people and companies are the repo's fictional cast. Greenhouse, Y Combinator
and cal.com are generic vendor platforms, public-safe per the audit-pii boundary.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import act_classify as ac  # noqa: E402


def _repo(pipeline_rows: str = "", networking: str = "") -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    (d / "data" / "job-pipeline.md").write_text(
        "# Pipeline\n\n| Company | Role | Stage | Date Added | Notes |\n"
        "|---|---|---|---|---|\n" + pipeline_rows, encoding="utf-8")
    (d / "data" / "networking.md").write_text(networking, encoding="utf-8")
    return d


def _gmail(subject: str, sender: str, body: str = "Some content.") -> str:
    return (f"# Email: {subject}\n\n> **From:** {sender}\n"
            f"> **Date:** Tue, 02 Jun 2026 13:45:37 -0700\n\n"
            f'<email-content source="gmail" sanitized="true">\n{body}\n</email-content>\n')


def reason(content: str, repo: Path):
    r = ac.detect_stale_routed(content, repo)
    return r["reason"] if r else None


@pytest.fixture
def engaged():
    return _repo("| Northwind | FDAS | Phone Screen | 2026-06-02 | x |\n")


# --- transactional notifications --------------------------------------------

@pytest.mark.parametrize("subject,sender,body", [
    ("Candidate Vibe Check - Robin between Northwind and Nick",
     "Jordan Lee <hello@cal.com>", "Your event has been scheduled"),
    ("Your application for Vertex - Deployment Strategist",
     "Y Combinator <workatastartup@ycombinator.com>",
     "Your application to Vertex has been received."),
    ("Application received", "Greenhouse <no-reply@us.greenhouse-mail.io>",
     "We received your application."),
], ids=["booking-confirmation", "application-receipt", "subject-line-receipt"])
def test_transactional_notifications_are_detected(engaged, subject, sender, body):
    assert reason(_gmail(subject, sender, body), engaged) == "transactional_notification"


# --- duplicate intro to an already-engaged company --------------------------

def test_an_intro_to_an_engaged_pipeline_company_is_a_duplicate(engaged):
    c = _gmail("Nick & Robin (Northwind): referred on Talentbridge",
               "Casey Morgan <casey.morgan@talentbridge.com>",
               "I want to introduce you to Robin at Northwind.")
    assert reason(c, engaged) == "company_already_in_pipeline"


# --- false-positive guards: deleting something real is the expensive error ---

def test_an_intro_at_researching_stage_is_left_alone():
    """Researching is not engagement -- that intro may be the thing that moves it."""
    early = _repo("| Northwind | FDAS | Researching | 2026-06-02 | x |\n")
    c = _gmail("Nick & Robin (Northwind): referred on Talentbridge",
               "Casey Morgan <casey.morgan@talentbridge.com>", "intro")
    assert reason(c, early) is None


def test_a_company_outside_the_pipeline_is_left_alone(engaged):
    c = _gmail("Intro to someone at Foobar Industries", "X <x@example.com>", "intro")
    assert reason(c, engaged) is None


def test_a_short_company_name_matching_ordinary_prose_is_left_alone():
    """A one-word company name is a substring of innocent sentences; matching it would
    delete real mail."""
    short = _repo("| Pens | Ops | Applied | 2026-06-02 | x |\n")
    assert reason(_gmail("Re: your pens are ready", "X <x@example.com>", "hi"), short) is None


def test_the_transactional_signatures_are_gmail_only(engaged):
    """A plain note saying 'has been scheduled' is not a booking confirmation."""
    assert reason("# Note\n\nReminder: the offsite has been scheduled for Friday.\n",
                  engaged) is None


# --- the pre-existing signal still fires ------------------------------------

def test_a_sender_already_in_networking_is_still_detected():
    net = _repo("", networking="Contact: jane@known.com logged 2026-05-01\n")
    assert reason(_gmail("Hello", "Jane <jane@known.com>", "hi"),
                  net) == "sender_already_in_networking"
