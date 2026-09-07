"""Tests for check_status_query_verification.py — the UserPromptSubmit status-query reminder.

Built from tools/HOOK_SPEC_status_query_verification.md (specified 2026-07-31, built
2026-09-06). Two properties matter and they pull against each other:

  1. PRECISION. A miss costs nothing; a false positive on every message is noise that
     trains the reader to ignore the reminder. The spec is explicit that this tunes
     toward precision, so the clean cases below are the load-bearing half of this file.
  2. DELIVERY. On UserPromptSubmit, stdout IS the delivery mechanism and exit 0 is
     correct — unlike PreToolUse, where exit 0 + stderr reaches nobody. So "fires"
     means "wrote the reminder to stdout", never "exited 2". A test that only asserted
     the exit code would pass against a hook that printed nothing.
"""
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(REPO_ROOT, "tools", "check_status_query_verification.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import check_status_query_verification as sq  # noqa: E402


def _run(prompt, key="prompt"):
    """Run the real CLI, not the helper. Returns (exit_code, stdout)."""
    payload = json.dumps({key: prompt}) if key else prompt
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return r.returncode, r.stdout


def _fires(prompt):
    code, out = _run(prompt)
    assert code == 0, "this hook must never block; exit 0 on every path"
    return bool(out.strip())


# --------------------------------------------------------------------------
# TRIGGER CASES — one per regex family in spec section 4, plus the two verbatim
# instances from the 2026-07-31 incident that produced the spec.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "Is there anything else I'm missing before I clear?",   # the verbatim 2026-07-31 fire
    "anything else im missing?",
    "What's left?",
    "what is still open on this?",
    "What's outstanding?",
    "is there anything I need to do first",
    "Is there anything else we should look at",
    "are we ready to ship",
    "am i ready for tomorrow",
    "before i clear, walk me through it",
    "before I wrap, what happened today",
    "is the pipeline cleanup done?",
    "is that finished",
    "what do I still need to do",
    "what should i do next on this",
    "status check please",
    "status update on the memory work",
    "where are we",
    "where do we stand on the audit",
    "did i miss anything",
    "did we forget the debrief",
])
def test_fires_on_status_queries(prompt):
    assert _fires(prompt), f"should have fired: {prompt!r}"


# --------------------------------------------------------------------------
# CLEAN CASES — precision. Each of these is a real shape that a naive
# substring matcher would wrongly catch.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "whats taking so long?",                       # verbatim 2026-07-31 non-trigger
    "the file is missing a header",                # 'missing' in a non-status sense
    "add the missing import to tools/foo.py",
    "this dict is missing a key, fix it",
    "grep for anything that looks like a status field",
    "rename the ready flag to is_ready",           # 'ready' as an identifier
    "the readme is out of date",                   # 'read' prefix, not 'ready'
    "write a test for the done column",            # 'done' as a data value
    "left-align the table",                        # 'left' non-status
    "what does this function do",
    "run the mutation check on proof_domains.py",
    "draft an email to the recruiter",
    "explain how the PII gate works",
    "commit this with a message about the wiring",
    "",                                            # empty prompt
])
def test_stays_quiet_on_ordinary_work(prompt):
    assert not _fires(prompt), f"false positive on: {prompt!r}"


# --------------------------------------------------------------------------
# DELIVERY — the reminder must actually carry its instruction, not just be
# non-empty. A mutant that printed "ok" would pass a non-empty assertion.
# --------------------------------------------------------------------------

def test_injected_text_names_the_required_action():
    _, out = _run("Is there anything else I'm missing before I clear?")
    low = out.lower()
    assert "status-query" in low or "status query" in low
    for required in ("ls -la", "git log", "this session", "name the scope"):
        assert required in low, f"reminder must mention {required!r}; got:\n{out}"


def test_reminder_is_short_enough_to_be_read():
    """Spec section 5: keep it SHORT, it competes for attention."""
    _, out = _run("what's left?")
    assert len(out) < 1200, "a long reminder is an ignored reminder"
    assert len(out.splitlines()) <= 16


# --------------------------------------------------------------------------
# FAIL-OPEN — a hook that crashes on malformed input must not break the session.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "not json at all",
    "{}",
    '{"prompt": null}',
    '{"prompt": 12345}',
    '{"other_field": "anything else im missing"}',
    "",
])
def test_fails_open_on_malformed_input(payload):
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0, f"must fail open on {payload!r}"


def test_never_writes_to_stderr_on_the_happy_path():
    r = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"prompt": "what's left?"}), capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.stderr == "", "stderr is not the delivery channel here; stdout is"


# --------------------------------------------------------------------------
# NEGATIVE CONTROL ON THE INSTRUMENT ITSELF.
# Per feedback_a_negative_result_from_an_unvalidated_instrument_is_not_evidence:
# prove the harness can observe a firing before trusting any non-firing result.
# --------------------------------------------------------------------------

def test_the_harness_can_observe_both_outcomes():
    assert _fires("did i miss anything"), "harness cannot see a fire; every quiet result is meaningless"
    assert not _fires("draft an email to the recruiter"), "harness reports a fire on everything"


# --------------------------------------------------------------------------
# UNIT-LEVEL — the matcher, so a narrowing fix cannot be an over-broad widening.
# --------------------------------------------------------------------------

def test_matcher_is_case_insensitive():
    assert sq.is_status_query("WHAT'S LEFT?")
    assert sq.is_status_query("Did We Forget Anything")


def test_matcher_handles_curly_apostrophes():
    """Dictation and macOS smart quotes produce U+2019, not U+0027."""
    assert sq.is_status_query("what’s left?")
    assert sq.is_status_query("is there anything else I’m missing")


def test_matcher_is_not_fooled_by_a_long_prose_prompt_containing_a_trigger_word():
    long_prose = (
        "Here is a paragraph about the project. " * 40
        + "The config is missing a default value and that is the bug."
    )
    assert not sq.is_status_query(long_prose)


# --------------------------------------------------------------------------
# MUTATION-DRIVEN. The first version of this suite had 12 survivors of 26
# mutants: 49 green tests that could not tell the real module from a broken
# one at twelve separate decision points. Each test below was written against
# a specific surviving mutant, and each one FAILS if its guard is removed.
# Written after the fact, which is the wrong order and is why they are grouped
# rather than woven in -- the record of what a green suite was worth here.
# --------------------------------------------------------------------------

def test_a_long_prompt_containing_a_real_trigger_still_does_not_fire():
    """Kills the _MAX_PROMPT_CHARS guard mutant.

    The original length test used prose that matched no pattern, so the real
    function and a guard-less mutant both returned False and no assertion could
    separate them. The guard only has observable behaviour when the long text
    DOES contain a trigger -- that is the case it exists for.
    """
    trigger = "is there anything else I'm missing"
    long_with_trigger = ("Some context about the refactor. " * 20) + trigger
    assert len(long_with_trigger) > sq._MAX_PROMPT_CHARS
    assert sq.is_status_query(trigger), "control: the bare trigger must fire"
    # `is False`, not `not ...`: the guard's mutant returns None, which is falsy,
    # so a truthiness assertion cannot see the difference. This is the same
    # weak-assertion shape that let the guard survive in the first place.
    assert sq.is_status_query(long_with_trigger) is False, \
        "a long work request that happens to contain a trigger phrase must stay quiet"


def test_the_length_boundary_is_where_it_claims_to_be():
    trigger = "what's left?"
    pad = "x " * 400
    just_under = (pad + trigger)[-(sq._MAX_PROMPT_CHARS):]
    assert len(just_under) <= sq._MAX_PROMPT_CHARS
    assert sq.is_status_query(just_under)
    assert sq.is_status_query("y" + pad + trigger) is False


@pytest.mark.parametrize("value", [None, 12345, ["what's left?"], {"a": 1}, object()])
def test_matcher_rejects_non_strings_directly(value):
    """Kills the isinstance guard in is_status_query.

    Unreachable through the CLI because extract_prompt filters non-strings
    first, so it needs a direct call. Without this the guard could be deleted
    and every CLI test would still pass -- then a future caller passing a
    non-string gets a TypeError from re.search instead of False.
    """
    assert sq.is_status_query(value) is False


@pytest.mark.parametrize("payload", [[1, 2, 3], "a bare json string", 42, None])
def test_extract_prompt_rejects_non_dict_payloads(payload):
    """Kills the isinstance guard in extract_prompt."""
    assert sq.extract_prompt(payload) == ""


def test_extract_prompt_skips_a_non_string_value_and_keeps_looking():
    """Kills the IF_TRUE mutant on the isinstance check inside the key loop.

    Forced true, that branch returns the first key present whatever its type.
    The observable difference is a payload whose earlier key holds a non-string
    and whose later key holds the real prompt.
    """
    assert sq.extract_prompt({"prompt": 12345, "text": "what's left?"}) == "what's left?"
    assert sq.extract_prompt({"prompt": None, "message": "did we forget"}) == "did we forget"
    assert sq.extract_prompt({"prompt": ["not", "a", "string"]}) == ""


def test_extract_prompt_returns_empty_when_no_known_key_is_present():
    assert sq.extract_prompt({"unrelated": "anything else im missing"}) == ""
    assert sq.extract_prompt({}) == ""
