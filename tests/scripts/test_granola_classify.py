"""Tests for the fail-closed meeting classifier in tools/granola_auto_debrief.py.

Safety contract: the cron auto-debrief must never post potentially-sealed therapy
content to a non-sealed surface (data/inbox.md or data/voice-corpus/granola/).

Three-way classification:
  therapy    -> seal to personal vault, never inbox
  networking -> voice-corpus + inbox (the only post-to-inbox class)
  unknown    -> fail-closed: persist nowhere, flag for manual /granola-pull

All fixtures use FAKE therapist identities. Real ones live in the gitignored
tools/.therapy-classifier.txt (this repo is public).
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.granola_auto_debrief import classify_meeting, load_therapy_classifier_config


# ---------------------------------------------------------------------------
# Fixtures: a fake gitignored-style config
# ---------------------------------------------------------------------------

CONFIG_TEXT = """\
# Fake therapy classifier config for tests
# attendee: matched against meeting attendees (name or email), case-insensitive
attendee: fake.virtual@example.com
attendee: Virtual Therapist
# name: full name matched against transcript (in-person, no-attendee sessions)
name: Virtual Therapist
name: Inperson Therapist
"""


@pytest.fixture
def config(tmp_path):
    cfg = tmp_path / ".therapy-classifier.txt"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return load_therapy_classifier_config(cfg)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def test_config_loader_parses_attendees_and_names(config):
    assert "fake.virtual@example.com" in config["attendees"]
    assert "virtual therapist" in config["attendees"]
    assert "Inperson Therapist" in config["names"]
    assert "Virtual Therapist" in config["names"]


def test_missing_config_returns_empty_and_does_not_crash(tmp_path):
    cfg = load_therapy_classifier_config(tmp_path / "does-not-exist.txt")
    assert cfg["attendees"] == set()
    assert cfg["names"] == []


# ---------------------------------------------------------------------------
# Therapy classification
# ---------------------------------------------------------------------------

def test_virtual_therapy_by_attendee_email(config):
    """Virtual session (attendees populate): attendee email matches the allowlist."""
    meeting = {
        "title": "Weekly check-in",  # innocuous, no therapy keyword
        "attendees": [
            {"name": "Nick Magnuson", "email": "owner@example.com"},
            {"name": "V. Therapist", "email": "fake.virtual@example.com"},
        ],
        "transcript": "Me: hi. Them: how have you been.",
    }
    assert classify_meeting(meeting, config) == "therapy"


def test_virtual_therapy_by_attendee_name(config):
    meeting = {
        "title": "Weekly check-in",
        "attendees": [{"name": "Virtual Therapist", "email": "unknown@example.com"}],
        "transcript": "",
    }
    assert classify_meeting(meeting, config) == "therapy"


def test_therapy_by_generic_title_keyword(config):
    """Generic, non-PII title keyword still seals."""
    meeting = {"title": "Couples session", "attendees": [], "transcript": ""}
    assert classify_meeting(meeting, config) == "therapy"


def test_inperson_therapy_by_transcript_first_name(config):
    """In-person (no attendees): therapist FIRST name surfaces in transcript -> seal."""
    meeting = {
        "title": "Reflections",  # innocuous
        "attendees": [],
        "transcript": "Me: I wanted to talk about that. Inperson: tell me more.",
    }
    assert classify_meeting(meeting, config) == "therapy"


def test_inperson_therapy_by_transcript_full_name(config):
    meeting = {
        "title": "Session",  # 'session' alone is not a keyword; relies on name scan
        "attendees": [],
        "transcript": "Notes from my talk with Inperson Therapist today.",
    }
    assert classify_meeting(meeting, config) == "therapy"


# ---------------------------------------------------------------------------
# Networking classification
# ---------------------------------------------------------------------------

def test_networking_with_external_attendee(config):
    """A real call with a non-therapist external attendee posts."""
    meeting = {
        "title": "Acme intro",
        "attendees": [
            {"name": "Nick Magnuson", "email": "owner@example.com"},
            {"name": "Jane Recruiter", "email": "jane@acme.com"},
        ],
        "transcript": "Them: tell me about your background.",
    }
    assert classify_meeting(meeting, config) == "networking"


def test_networking_call_mentioning_therapist_first_name_still_networking(config):
    """FP guard: when attendees are present, a transcript that happens to mention a
    therapist first name must NOT override the external-attendee networking signal."""
    meeting = {
        "title": "Acme intro",
        "attendees": [
            {"name": "Nick Magnuson", "email": "owner@example.com"},
            {"name": "Inperson Hiring Manager", "email": "hm@acme.com"},
        ],
        "transcript": "Them: our colleague Inperson will join next round.",
    }
    assert classify_meeting(meeting, config) == "networking"


# ---------------------------------------------------------------------------
# Unknown / fail-closed (the leak cases)
# ---------------------------------------------------------------------------

def test_nameless_attendeeless_innocuous_title_is_unknown(config):
    """THE leak case: mis-titled in-person therapy ('Job search reflections and
    insights', no attendees, no therapist name in transcript) must be unknown,
    NOT networking. Unknown never reaches inbox."""
    meeting = {
        "title": "Job search reflections and insights",
        "attendees": [],
        "transcript": "Me: I have been thinking a lot about where I am.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_only_nick_attendee_is_unknown(config):
    """A solo session where only Nick is an attendee and no therapy signal -> unknown."""
    meeting = {
        "title": "Brain dump",
        "attendees": [{"name": "Nick Magnuson", "email": "owner@example.com"}],
        "transcript": "Me: just talking through some ideas.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_networking_title_but_no_attendees_is_unknown(config):
    """In-person coffee (clear title, but no attendees, no therapy signal) -> fail
    closed. Better to flag for manual pull than risk an unattended mis-post."""
    meeting = {
        "title": "Coffee with Acme founder",
        "attendees": [],
        "transcript": "Them: so what are you looking for.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_leak_case_fails_safe_even_with_empty_config(tmp_path):
    """With no config loaded at all, the leak case must STILL be unknown."""
    empty = load_therapy_classifier_config(tmp_path / "missing.txt")
    meeting = {
        "title": "Job search reflections and insights",
        "attendees": [],
        "transcript": "Me: thinking about things.",
    }
    assert classify_meeting(meeting, empty) == "unknown"


# ---------------------------------------------------------------------------
# Transcript shape tolerance
# ---------------------------------------------------------------------------

def test_transcript_as_segment_list_is_handled(config):
    """granola_fetch returns transcript as a list of segment dicts; classifier
    must normalize it, not crash."""
    meeting = {
        "title": "Reflections",
        "attendees": [],
        "transcript": [
            {"speaker": "Me", "text": "I wanted to talk."},
            {"speaker": "Inperson", "text": "go on."},
        ],
    }
    assert classify_meeting(meeting, config) == "therapy"
