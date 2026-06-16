"""Tests for the personal-recipient provenance exemption in check_draft_voice.py.

Personal correspondence (family/friends on .personal-recipients.txt) has no
job-search drafting skill, so it carries no provenance marker. The hook skips the
provenance gate for a *fully* personal recipient list, but a draft mixing a
personal contact with a job-search recipient stays gated. Voice/content checks are
unaffected (covered by the live smoke + t10 suite).
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)


# --- extract_to -------------------------------------------------------------

@pytest.mark.parametrize("content,expected", [
    ("TO: a@b.com\nSUBJECT: x\nBODY:\nhi", "a@b.com"),
    ("TO:  spaced@b.com  \nBODY:\nhi", "spaced@b.com"),
    ("TO: a@b.com, c@d.com\nBODY:\nhi", "a@b.com, c@d.com"),
    ("SUBJECT: x\nBODY:\nno recipient line", ""),
])
def test_extract_to(content, expected):
    assert cdv.extract_to(content) == expected


# --- recipients_all_personal ------------------------------------------------

ALLOW = {"spouse@example.com", "friend@example.com"}


@pytest.mark.parametrize("to_field", [
    "spouse@example.com",
    "Spouse@Example.com",                # case-insensitive
    "  spouse@example.com  ",            # whitespace
    "spouse@example.com, friend@example.com",  # all personal
])
def test_personal_recipients_pass(to_field):
    assert cdv.recipients_all_personal(to_field, ALLOW) is True


@pytest.mark.parametrize("to_field", [
    "recruiter@company.com",                                   # job-search
    "spouse@example.com, recruiter@company.com",               # mixed -> still gated
    "",                                                         # no recipient
    "   ",                                                      # blank
])
def test_non_personal_recipients_fail(to_field):
    assert cdv.recipients_all_personal(to_field, ALLOW) is False


def test_empty_allowlist_never_personal():
    # No allowlist file / empty set -> nothing is exempt, gate stays on.
    assert cdv.recipients_all_personal("spouse@example.com", set()) is False


# --- load_personal_recipients (reads the live file) -------------------------

def test_live_allowlist_loads_and_lowercases():
    loaded = cdv.load_personal_recipients()
    # Entries are lowercased; comment/blank lines excluded. The file content is the
    # gitignored .personal-recipients.txt, so assert structure, not specific addresses.
    assert all(entry == entry.lower() for entry in loaded)
    assert not any(entry.startswith("#") for entry in loaded)
