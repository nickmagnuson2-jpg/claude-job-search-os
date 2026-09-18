"""Tests for the T10 voice-anchor bundle added to check_draft_voice.py.

Validates the four new BLOCK patterns (load-bearing, if-you-have-a-sec family,
meta-narration labels, not-as-X-but-as-Y) match their banned forms AND do not
false-positive on safe drafts. The semantic patterns (coined compounds,
speculative framing, etc.) are intentionally NOT in the hook.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)


def matched(text):
    """Descriptions of every PATTERN whose regex fires on text (mirrors the hook)."""
    return [desc for (rx, desc, _fix) in cdv.PATTERNS if rx.search(text)]


def fires(text, key):
    return any(key in d for d in matched(text))


# --- banned forms must fire ------------------------------------------------

@pytest.mark.parametrize("text", [
    "that was the load-bearing assumption",
    "the load bearing piece of the plan",
    "this is loadbearing for the whole thread",
])
def test_load_bearing_fires(text):
    assert fires(text, "load-bearing")


@pytest.mark.parametrize("text", [
    "if you have a sec, take a look",
    "when you get a minute let me know",
    "if you've got a second",
    "if you have a chance to review",
])
def test_if_you_have_a_sec_fires(text):
    assert fires(text, "performative-casual scaffold")


@pytest.mark.parametrize("text", [
    "Quick reframe since then: I have been building.",
    "Quick re-frame on the role.",
    "Quick note on timing.",
    "To summarize, I am excited.",
    "Here's where I'm headed next.",
])
def test_meta_narration_fires(text):
    assert fires(text, "meta-narration")


@pytest.mark.parametrize("text", [
    "not as an observer, but as someone who builds",
    "I come not as a candidate but as a peer",
])
def test_not_as_x_but_as_y_fires(text):
    assert fires(text, "performs the contrast")


# --- safe drafts must NOT trip the new patterns ----------------------------

NEW_KEYS = ["load-bearing", "performative-casual scaffold", "meta-narration",
            "performs the contrast"]

@pytest.mark.parametrize("text", [
    "I would love to chat about the role when you have time.",
    "I build with AI tools day to day rather than reading about it.",
    "The work carries real weight and I am drawn to it.",
    "Let me know what works on your end. Looking forward to it.",
    "I came away excited after seeing the office in person.",
])
def test_safe_text_trips_no_new_pattern(text):
    descs = matched(text)
    assert not any(any(k in d for k in NEW_KEYS) for d in descs), descs


def test_x_rather_than_y_is_allowed():
    # The corpus-validated replacement must NOT match not-as-X-but-as-Y.
    assert not fires("building with AI tools rather than reading about it",
                     "performs the contrast")


# --- resonance-frame family (added 2026-09-17, 3rd variant) ----------------
#
# Origin: a /follow-up draft to an interviewer opened its callback with
# "the part that stuck with me is that...". content-rules F1 already banned the
# abstraction-preamble family and content-rules.yaml carried the literal
# "has stuck with me since", but no pattern matched this variant, so nothing
# fired and the draft reached the user, who caught it. These tests pin the
# variant and its sibling.

@pytest.mark.parametrize("text", [
    "the part that stuck with me is that the models default to service-rep behavior",
    "The thing that stuck with me was the harness.",
    "A few things stuck with me, but the one I keep coming back to is pricing",
    "that has stuck with me since our call",
    "What stuck with me: they refuse to ship what they know converts worse.",
])
def test_resonance_frame_fires(text):
    assert fires(text, "resonance frame"), f"should block: {text!r}"


@pytest.mark.parametrize("text", [
    "the one I keep coming back to is the December handover",
    "I kept coming back to the transfer timing question",
])
def test_resonance_frame_sibling_fires(text):
    assert fires(text, "resonance frame sibling"), f"should block: {text!r}"


@pytest.mark.parametrize("text", [
    # The corrected sentence that shipped -- states the mechanism, no announcement.
    "If the base models default to service-rep behavior, most of what makes a call convert has to live in the harness.",
    # Legitimate uses of the component words must not trip the pattern.
    "The agent stuck to the script even after the caller objected.",
    "I will come back to you with the doc tomorrow morning.",
    "The call got stuck in a loop and never reached a next step.",
])
def test_resonance_frame_no_false_positive(text):
    hits = [d for d in matched(text) if "resonance frame" in d]
    assert hits == [], f"should be clean: {text!r} -> {hits}"
