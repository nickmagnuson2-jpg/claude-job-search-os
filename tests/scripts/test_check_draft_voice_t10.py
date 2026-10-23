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


# --- recipient directive in a live-thread reply (content-rules C6) ----------
#
# Origin: a /follow-up reply to a recruiter told him to "make sure that version
# goes through with the submission" and to send similar roles "my way". Both
# instruct a competent recipient in his own job. The draft passed the full
# judgment-based quality gate, so the check has to be mechanical. The two
# patterns are deliberately narrow: "please make sure" (aimed at the recipient),
# never a bare "make sure", because "I wanted to make sure ..." is Nick's own
# sent phrasing and is self-directed.

C6_KEY = "C6"


@pytest.mark.parametrize("text", [
    "Please make sure that version goes through with the submission.",
    "please  make sure the tailored resume is the one attached",
    "If you see that shape elsewhere, send it my way.",
    "Happy to look at more like this, send them my way.",
    "If anything else comes up, send any my way.",
])
def test_recipient_directive_fires(text):
    assert fires(text, C6_KEY), f"should block: {text!r}"


@pytest.mark.parametrize("text", [
    # The sent close that replaced the directive in the origin incident.
    "Thanks for letting me know what Acme says.",
    # Self-directed "make sure" is Nick's own corpus phrasing.
    "I wanted to make sure I left no stone unturned.",
    "Want to make sure it's a real fit on both sides.",
    # "my way" without a send directive.
    "I'd love to keep you updated with anything else that comes my way.",
    "Applied this morning, including a resume.",
])
def test_recipient_directive_no_false_positive(text):
    hits = [d for d in matched(text) if C6_KEY in d]
    assert hits == [], f"should be clean: {text!r} -> {hits}"


def _run_hook(monkeypatch, tmp_path, capsys, body, override=False):
    """Run main() end to end on a draft body: open_draft.py invocation, fresh
    provenance marker, so only the content checks decide the exit code."""
    draft = tmp_path / ".pending-draft.txt"
    draft.write_text(
        "TO: casey.doe@example.com\nSUBJECT: Re: Acme\nBODY:\n" + body + "\n",
        encoding="utf-8",
    )
    source = tmp_path / ".pending-draft.source"
    source.write_text("follow-up\n", encoding="utf-8")
    monkeypatch.setattr(cdv, "DRAFT_FILE", draft)
    monkeypatch.setattr(cdv, "SOURCE_FILE", source)
    monkeypatch.setattr(cdv, "read_hook_command",
                        lambda: "PYTHONIOENCODING=utf-8 python3 tools/open_draft.py")
    if override:
        monkeypatch.setenv("DRAFT_OVERRIDE", "1")
    else:
        monkeypatch.delenv("DRAFT_OVERRIDE", raising=False)
    with pytest.raises(SystemExit) as exc:
        cdv.main()
    return exc.value.code, capsys.readouterr().err


@pytest.mark.parametrize("body", [
    "Hi Casey, applied this morning. Please make sure that version goes through with the submission.",
    "Hi Casey, passed on the second one. If you see that shape elsewhere, send it my way.",
])
def test_recipient_directive_blocks_end_to_end(monkeypatch, tmp_path, capsys, body):
    code, err = _run_hook(monkeypatch, tmp_path, capsys, body)
    assert code == 2
    assert C6_KEY in err


def test_recipient_directive_clean_reply_passes_end_to_end(monkeypatch, tmp_path, capsys):
    code, err = _run_hook(monkeypatch, tmp_path, capsys,
                          "Thanks for letting me know what Acme says.")
    assert code == 0, err


def test_recipient_directive_override_still_bypasses(monkeypatch, tmp_path, capsys):
    code, _err = _run_hook(monkeypatch, tmp_path, capsys,
                           "Please make sure that version goes through.", override=True)
    assert code == 0
