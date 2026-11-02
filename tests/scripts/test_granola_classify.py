"""Tests for the fail-closed meeting classifier in tools/granola_auto_debrief.py.

Safety contract: the cron auto-debrief must never post potentially-sealed sealed
content to a non-sealed surface (data/inbox.md or data/voice-corpus/granola/).

Three-way classification:
  sealed    -> seal to personal vault, never inbox
  networking -> voice-corpus + inbox (the only post-to-inbox class)
  unknown    -> fail-closed: persist nowhere, flag for manual /granola-pull

All fixtures use FAKE sealed-contact identities. Real ones live in the gitignored
tools/.sealed-meetings.txt (this repo is public).
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.granola_auto_debrief import classify_meeting, load_sealed_meetings_config


# ---------------------------------------------------------------------------
# Fixtures: a fake gitignored-style config
# ---------------------------------------------------------------------------

CONFIG_TEXT = """\
# Fake sealed classifier config for tests
# attendee: matched against meeting attendees (name or email), case-insensitive
attendee: fake.virtual@example.com
attendee: Virtual Contact
# name: full name matched against transcript (in-person, no-attendee sessions)
name: Virtual Contact
name: Inperson Contact
# keyword: matched as a substring of the lowercased title
keyword: private session
"""


@pytest.fixture
def config(tmp_path):
    cfg = tmp_path / ".sealed-meetings.txt"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return load_sealed_meetings_config(cfg)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def test_config_loader_parses_attendees_and_names(config):
    assert "fake.virtual@example.com" in config["attendees"]
    assert "virtual contact" in config["attendees"]
    assert config["keywords"] == ["private session"]
    assert "Inperson Contact" in config["names"]
    assert "Virtual Contact" in config["names"]


def test_a_missing_config_is_none_and_classifies_everything_unknown(tmp_path, monkeypatch):
    """No config: the title keywords live in it, so a sealed meeting cannot be recognized.
    Every meeting fails closed, even one with an external attendee that would otherwise
    be networking."""
    import meeting_vocab
    assert load_sealed_meetings_config(tmp_path / "does-not-exist.txt") is None
    monkeypatch.setattr(meeting_vocab, "DEFAULT_SEALED_CONFIG", tmp_path / "does-not-exist.txt")
    call = {"title": "Coffee", "attendees": [{"name": "Sam Carter", "email": "sam@example.com"}],
            "transcript": "Me: hi"}
    assert classify_meeting(call) == "unknown"
    assert classify_meeting(call, {"attendees": set(), "names": [], "keywords": []}) == "networking"


def test_an_empty_config_classifies_normally(tmp_path):
    cfg = tmp_path / ".sealed-meetings.txt"
    cfg.write_text("# no sealed meetings\n", encoding="utf-8")
    empty = load_sealed_meetings_config(cfg)
    assert empty == {"attendees": set(), "names": [], "keywords": []}
    call = {"title": "Coffee", "attendees": [{"name": "Sam Carter", "email": "sam@example.com"}]}
    assert classify_meeting(call, empty) == "networking"


# ---------------------------------------------------------------------------
# Sealed classification
# ---------------------------------------------------------------------------

def test_virtual_sealed_by_attendee_email(config):
    """Virtual session (attendees populate): attendee email matches the allowlist."""
    meeting = {
        "title": "Weekly check-in",  # innocuous, no sealed keyword
        "attendees": [
            {"name": "Nick Magnuson", "email": "owner@example.com"},
            {"name": "V. Contact", "email": "fake.virtual@example.com"},
        ],
        "transcript": "Me: hi. Them: how have you been.",
    }
    assert classify_meeting(meeting, config) == "sealed"


def test_virtual_sealed_by_attendee_name(config):
    meeting = {
        "title": "Weekly check-in",
        "attendees": [{"name": "Virtual Contact", "email": "unknown@example.com"}],
        "transcript": "",
    }
    assert classify_meeting(meeting, config) == "sealed"


def test_sealed_by_generic_title_keyword(config):
    """Generic, non-PII title keyword still seals."""
    meeting = {"title": "Private session", "attendees": [], "transcript": ""}
    assert classify_meeting(meeting, config) == "sealed"


def test_inperson_sealed_by_transcript_first_name(config):
    """In-person (no attendees): sealed contact FIRST name surfaces in transcript -> seal."""
    meeting = {
        "title": "Reflections",  # innocuous
        "attendees": [],
        "transcript": "Me: I wanted to talk about that. Inperson: tell me more.",
    }
    assert classify_meeting(meeting, config) == "sealed"


def test_inperson_sealed_by_transcript_full_name(config):
    meeting = {
        "title": "Session",  # 'session' alone is not a keyword; relies on name scan
        "attendees": [],
        "transcript": "Notes from my talk with Inperson Contact today.",
    }
    assert classify_meeting(meeting, config) == "sealed"


# ---------------------------------------------------------------------------
# Networking classification
# ---------------------------------------------------------------------------

def test_networking_with_external_attendee(config):
    """A real call with a non-sealed contact external attendee posts."""
    meeting = {
        "title": "Acme intro",
        "attendees": [
            {"name": "Nick Magnuson", "email": "owner@example.com"},
            {"name": "Jane Recruiter", "email": "jane@acme.com"},
        ],
        "transcript": "Them: tell me about your background.",
    }
    assert classify_meeting(meeting, config) == "networking"


def test_networking_call_mentioning_contact_first_name_still_networking(config):
    """FP guard: when attendees are present, a transcript that happens to mention a
    sealed contact first name must NOT override the external-attendee networking signal."""
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
    """THE leak case: mis-titled in-person sealed ('Job search reflections and
    insights', no attendees, no sealed contact name in transcript) must be unknown,
    NOT networking. Unknown never reaches inbox."""
    meeting = {
        "title": "Job search reflections and insights",
        "attendees": [],
        "transcript": "Me: I have been thinking a lot about where I am.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_only_nick_attendee_is_unknown(config):
    """A solo session where only Nick is an attendee and no sealed signal -> unknown."""
    meeting = {
        "title": "Brain dump",
        "attendees": [{"name": "Nick Magnuson", "email": "owner@example.com"}],
        "transcript": "Me: just talking through some ideas.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_networking_title_but_no_attendees_is_unknown(config):
    """In-person coffee (clear title, but no attendees, no sealed signal) -> fail
    closed. Better to flag for manual pull than risk an unattended mis-post."""
    meeting = {
        "title": "Coffee with Acme founder",
        "attendees": [],
        "transcript": "Them: so what are you looking for.",
    }
    assert classify_meeting(meeting, config) == "unknown"


def test_leak_case_fails_safe_even_with_empty_config(tmp_path):
    """With an empty config, the leak case must STILL be unknown."""
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    empty = load_sealed_meetings_config(tmp_path / "empty.txt")
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
    assert classify_meeting(meeting, config) == "sealed"


# --- config parsing edges (mutation survivors, 2026-11-02) ----------------------------

def test_an_empty_directive_value_is_ignored(tmp_path):
    """An empty `keyword:` would otherwise add "" as a keyword, and "" is in every title,
    so every meeting would be sealed."""
    cfg = tmp_path / ".sealed-meetings.txt"
    cfg.write_text("keyword:\nattendee:\nname:\n", encoding="utf-8")
    loaded = load_sealed_meetings_config(cfg)
    assert loaded == {"attendees": set(), "names": [], "keywords": []}
    call = {"title": "Coffee", "attendees": [{"name": "Sam Carter", "email": "sam@example.com"}]}
    assert classify_meeting(call, loaded) == "networking"


def test_an_unknown_directive_is_not_read_as_a_keyword(tmp_path):
    cfg = tmp_path / ".sealed-meetings.txt"
    cfg.write_text("color: coffee\nkeyword: private session\n", encoding="utf-8")
    assert load_sealed_meetings_config(cfg)["keywords"] == ["private session"]


def test_with_no_config_passed_the_default_file_is_used(tmp_path, monkeypatch):
    """classify_meeting(meeting) reads the default config; with a keyword there, a titled
    meeting seals. (A missing default classifies everything unknown, tested above.)"""
    import meeting_vocab
    cfg = tmp_path / ".sealed-meetings.txt"
    cfg.write_text("keyword: private session\n", encoding="utf-8")
    monkeypatch.setattr(meeting_vocab, "DEFAULT_SEALED_CONFIG", cfg)
    titled = {"title": "Private session", "attendees": [{"name": "Sam Carter", "email": "sam@example.com"}]}
    assert classify_meeting(titled) == "sealed"
