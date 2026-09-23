"""The hedged-time-ask BLOCK patterns in check_draft_voice.py.

ORIGIN (2026-09-23, 4th fire of feedback_check_drafts_for_implied_claims). Nick
rewrites this close every single time it reaches him:

  Claude: "Would still love 15 minutes whenever you have a read."   (2026-09-08)
  Nick:   "...if your calendar allows."
  Claude: "Still happy to take 15 minutes whenever it is useful."   (2026-09-23, send 1)
  Nick:   "Would love 15 minutes on your calendar if you can spare it."
  Claude: "Still glad to take 15 minutes if it is useful."          (2026-09-23, send 2)
  Nick:   "Would love 15 minutes on your calendar!"

THE DEFECT IS ASK SIZE. The hedged form asks the recipient to first decide whether
the meeting is worth having and THEN respond. That is two asks, and it performs
indifference about the answer. The sent form asks only to schedule.

WHY A BLOCK AND NOT A JUDGMENT STEP. This was already promoted on 2026-09-08 as an
"implication pass" that a drafting skill runs before presenting. Claude then ran the
full /follow-up Quality Gate on two drafts 90 minutes apart and wrote the hedged
close both times, with the rule marked Promoted. A judgment step converted at zero,
which is the CLAUDE.md conversion law. Hence exit1.

THE FALSE-POSITIVE RISK IS THE WHOLE DESIGN CONSTRAINT. "happy to" has many
legitimate uses that are not time asks, so both patterns REQUIRE an explicit
minutes token. The clean cases below are not decoration: each one is a form that
either shipped in a real email or is prescribed by framework/voice-reference.md,
and any of them firing would make the hook worse than not having it.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)

HEDGED_KEYS = ("hedged time ask", "deferred time ask")


def matched(text):
    return [desc for (rx, desc, _fix) in cdv.PATTERNS if rx.search(text)]


def hedged_fires(text):
    return any(any(k in d for k in HEDGED_KEYS) for d in matched(text))


# --- must BLOCK: the forms Nick has actually rewritten ----------------

@pytest.mark.parametrize("text", [
    # verbatim from the 2026-09-23 drafts
    "Still happy to take 15 minutes whenever it is useful.",
    "Still glad to take 15 minutes if it is useful.",
    # verbatim from the 2026-09-08 draft
    "Would still love 15 minutes whenever you have a read.",
    # near variants that are the same defect
    "Happy to take 20 mins if that is helpful.",
    "Glad to grab 30 minutes if that's useful.",
    "Happy to jump on 15 minutes if it is helpful.",
    "Would love a few minutes whenever you get to it.",
    "I have some minutes free whenever you want to talk.",
])
def test_hedged_time_ask_fires(text):
    assert hedged_fires(text), f"hook missed a known-bad close: {text!r}"


# --- must stay CLEAN: real sent forms and corpus-validated patterns --------

@pytest.mark.parametrize("text", [
    # what actually shipped, all three times
    "Would love 15 minutes on your calendar if you can spare it.",
    "Would love 15 minutes on your calendar!",
    "Would still love 15 minutes if your calendar allows.",
    # voice-reference.md corpus-validated soft CTA - firing on this would put the
    # hook in direct conflict with a validated pattern
    "I would love 20 minutes to reconnect. If not, no pressure.",
    # "happy to" with no time ask at all
    "Happy to take a look whenever you get a chance.",
    "Happy to send the deck over if it is useful.",
    "Glad to help if that is useful.",
    # "whenever" with no time ask
    "Call me whenever you like.",
    # minutes as plain fact, not an ask
    "The call is 15 minutes.",
    "The round ran 45 minutes.",
    # concrete windows, which content-rules H6 prefers
    "I am open Tuesday between 11:00 and 12:30, or Thursday afternoon.",
])
def test_legitimate_forms_stay_clean(text):
    assert not hedged_fires(text), f"FALSE POSITIVE on a legitimate form: {text!r}"


# --- the hook's own contract ----------------------------------------------

def test_patterns_are_body_scoped_not_header_scoped():
    """The hook runs PATTERNS against extract_body() output, not the whole file.

    A SUBJECT line reading "15 minutes whenever" would be odd but is not what this
    rule governs, and the hook must not start matching headers.
    """
    draft = (
        "TO: someone@example.com\n"
        "SUBJECT: 15 minutes whenever\n"
        "BODY:\n"
        "Would love 15 minutes on your calendar.\n"
    )
    assert not hedged_fires(cdv.extract_body(draft))


def test_both_hedged_patterns_are_present():
    """Guards against a future edit silently dropping one of the two shapes.

    They catch different things: one is the "if it is useful" tail, the other is the
    "whenever" deferral. Losing either leaves half the defect unguarded.
    """
    descs = [d for (_rx, d, _f) in cdv.PATTERNS]
    for key in HEDGED_KEYS:
        assert any(key in d for d in descs), f"pattern missing from PATTERNS: {key}"
