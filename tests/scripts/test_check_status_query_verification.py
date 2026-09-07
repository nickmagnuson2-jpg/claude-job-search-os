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
import threading
import time
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

# --------------------------------------------------------------------------
# ADVERSARIAL. Every one of these was produced by an external model (codex,
# 2026-09-07, F3) and every one FIRED against the first implementation. They are
# here rather than in the list above because of what they showed: my own clean
# cases were, in the reviewer's words, "too hand-picked to establish precision".
# I chose inputs that felt like counterexamples; these are the ones that were.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "Before I commit, add the missing tests.",       # imperative, not a status question
    "Before we ship, update the changelog.",
    "Before I push, run the formatter.",
    "Where are we adding the new function?",         # 'where are we' + gerund = implementation
    "Where are we putting the config?",
    "Please give the status update field a timestamp.",   # 'status update' as a noun phrase
    "Rename the status check column.",
    "What is left-padding in CSS?",                  # \bleft\b matched before the hyphen
    "What is left-align doing here?",
])
def test_adversarial_false_positives_stay_quiet(prompt):
    assert not _fires(prompt), f"codex F3 counterexample still fires: {prompt!r}"


# The triggers these fixes must NOT break. Each was a passing trigger case
# before the F3 fix and has to stay one after it -- a precision fix that
# silences real status queries has just moved the defect.
@pytest.mark.parametrize("prompt", [
    "before i clear, walk me through it",
    "before I wrap, what happened today",
    "status update on the memory work",
    "status check please",
    "where are we",
    "where do we stand on the audit",
    "What's left?",
    "what is still open on this?",
])
def test_real_triggers_survive_the_precision_fix(prompt):
    assert _fires(prompt), f"precision fix silenced a real trigger: {prompt!r}"


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


# --------------------------------------------------------------------------
# BOUNDED-READ AND BROKEN-PIPE CONTRACT (codex F1 and F2, 2026-09-07).
#
# The original suite asserted "exit 0 on every path" using subprocess input=,
# which CLOSES the child's stdin. Every test therefore landed in the EOF case
# and none could observe what happens when EOF never arrives -- the same
# structural blindness recorded in
# feedback_a_test_whose_input_fails_twice_cannot_see_its_target. These two
# tests hold the pipe open and close the output, which are the conditions the
# old suite could not express.
# --------------------------------------------------------------------------

def test_a_retained_open_stdin_does_not_stall_past_the_deadline():
    """codex F1. A producer that writes and keeps the pipe open must not hang us.

    Asserts the process EXITS, and does so inside its own deadline plus slack --
    not that it merely returns 0 eventually, which an unbounded read would also
    do once the pipe finally closed.
    """
    start = time.monotonic()
    p = subprocess.Popen(
        [sys.executable, HOOK], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    p.stdin.write('{not-json')      # partial, invalid
    p.stdin.flush()                  # ... and deliberately NOT closed
    try:
        code = p.wait(timeout=sq._STDIN_DEADLINE_S + 8)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        raise AssertionError(
            f"hook did not exit within {sq._STDIN_DEADLINE_S + 8}s on a retained "
            "open stdin -- the read is unbounded and will stall the session")
    finally:
        try: p.stdin.close()
        except Exception: pass
    elapsed = time.monotonic() - start
    assert code == 0, f"expected exit 0, got {code}"
    assert elapsed < sq._STDIN_DEADLINE_S + 8, f"took {elapsed:.1f}s"


def test_a_closed_stdout_does_not_produce_exit_120_or_stderr_noise():
    """codex F2. A triggering write to a closed stdout must still exit 0, silently.

    The BrokenPipeError surfaces during interpreter shutdown, outside both try
    blocks, so catching it around the write alone is not enough.
    """
    p = subprocess.Popen(
        [sys.executable, HOOK], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    p.stdout.close()                 # consumer goes away before the hook writes
    out, err = "", ""
    try:
        _, err = p.communicate(json.dumps({"prompt": "what's left?"}), timeout=20)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        raise AssertionError("hook hung when stdout was closed")
    assert p.returncode == 0, f"expected exit 0 on a closed stdout, got {p.returncode}"
    assert "BrokenPipe" not in (err or ""), f"stderr carried a shutdown diagnostic: {err!r}"


def test_the_deadline_is_short_enough_to_matter():
    """A deadline above the host's own timeout would be decoration."""
    assert 0 < sq._STDIN_DEADLINE_S <= 5, (
        "UserPromptSubmit's documented default host timeout is 30s; the hook's "
        "own deadline must be well under it or it never fires first")


def test_the_normal_eof_path_returns_immediately_not_at_the_deadline():
    """The deadline is a CEILING, not a wait.

    Found by mutation: removing the EOF break left every test green, because
    nothing asserted timing on the ordinary path. Without it the loop spins on
    a readable-at-EOF fd until the deadline expires, so every single prompt the
    user types would carry the full _STDIN_DEADLINE_S delay. The suite would
    have shipped that.
    """
    start = time.monotonic()
    code, out = _run("what's left?")
    elapsed = time.monotonic() - start
    assert code == 0 and out.strip()
    assert elapsed < sq._STDIN_DEADLINE_S, (
        f"normal EOF path took {elapsed:.2f}s against a {sq._STDIN_DEADLINE_S}s "
        "deadline -- it is waiting out the clock instead of stopping at EOF")


def test_a_producer_that_never_stops_writing_still_exits_at_the_deadline():
    """The case the deadline check itself exists for.

    A retained-open pipe that goes quiet is caught by select's own timeout. A
    producer that keeps WRITING keeps select readable indefinitely, so the only
    thing that ends the loop is the elapsed-time check.

    The writing happens on a daemon thread and the assertion times `p.wait()`,
    so what is measured is when the HOOK exits, not how long this test chose to
    keep writing. An earlier version looped in the foreground for a fixed window
    and therefore measured itself.
    """
    p = subprocess.Popen(
        [sys.executable, HOOK], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    stop = threading.Event()

    def flood():
        while not stop.is_set():
            try:
                p.stdin.write(b"x" * 4096)
                p.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return

    t = threading.Thread(target=flood, daemon=True)
    t.start()
    started = time.monotonic()
    try:
        code = p.wait(timeout=sq._STDIN_DEADLINE_S + 8)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        raise AssertionError(
            "a continuously-writing producer kept the hook alive past its deadline "
            "-- the elapsed-time check is not ending the read loop")
    finally:
        stop.set()
        try: p.stdin.close()
        except Exception: pass
    elapsed = time.monotonic() - started
    assert code == 0, f"expected exit 0, got {code}"
    assert elapsed < sq._STDIN_DEADLINE_S + 8, f"hook took {elapsed:.1f}s to exit"


def test_a_payload_larger_than_one_read_chunk_is_assembled_whole():
    """Kills the volume-ceiling mutants (IF_TRUE / NEGATE_CMP on the byte check).

    Forced true, or with the comparison inverted, the loop breaks after the first
    64 KiB chunk and the JSON is truncated to garbage -- so a legitimate large
    payload would silently stop firing. Real payloads exceed one chunk whenever a
    wrapper adds context around the prompt, so this is a live path, not a synthetic
    one.
    """
    payload = json.dumps({"padding": "x" * 200_000, "prompt": "what's left?"})
    assert len(payload) > 65536 * 2, "fixture must span several read chunks"
    r = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0
    assert r.stdout.strip(), (
        "a multi-chunk payload did not fire -- the read was truncated before the "
        "prompt key was reached")


def test_a_slow_trickle_that_never_ends_is_stopped_by_the_deadline():
    """Kills the elapsed-time check.

    Distinct from the flood test, which now exits via the VOLUME ceiling and so no
    longer exercises the clock at all. A trickle stays under the byte ceiling and
    never sends EOF, leaving the deadline as the only thing that can end the loop.
    """
    p = subprocess.Popen(
        [sys.executable, HOOK], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    stop = threading.Event()

    def trickle():
        while not stop.is_set():
            try:
                p.stdin.write(b" ")
                p.stdin.flush()
                time.sleep(0.05)
            except (BrokenPipeError, OSError, ValueError):
                return

    t = threading.Thread(target=trickle, daemon=True)
    t.start()
    started = time.monotonic()
    try:
        code = p.wait(timeout=sq._STDIN_DEADLINE_S + 8)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        raise AssertionError(
            "a slow trickle kept the hook alive -- the elapsed-time check is not "
            "ending the read loop, and the volume ceiling cannot catch this case")
    finally:
        stop.set()
        try: p.stdin.close()
        except Exception: pass
    assert code == 0
    assert time.monotonic() - started < sq._STDIN_DEADLINE_S + 8


@pytest.mark.parametrize("prompt", [
    "Where are we at here?",       # the one the first F3 fix wrongly silenced
    "where are we at?",
    "where are we at on the audit",
])
def test_where_are_we_at_still_fires(prompt):
    """REGRESSION. The first F3 fix required 'where are we' to be followed by
    punctuation or a preposition, which killed codex's 'Where are we adding the
    new function?' AND this, a genuine status query the user asked in the very
    session the fix landed. Precision bought by silencing real triggers is not
    precision, it has just moved the error."""
    assert _fires(prompt), f"real status query silenced: {prompt!r}"


def test_a_payload_whose_prompt_sits_past_the_byte_ceiling_does_not_fire():
    """Kills the volume-ceiling mutant DETERMINISTICALLY.

    The flood test covered this by timing, which made it machine-speed dependent:
    the same mutant survived one run and died the next. A guard that is sometimes
    a guard is not one. This pins the same behaviour with no clock in it -- the
    prompt key sits beyond _MAX_STDIN_BYTES, so a correctly-bounded read truncates
    to invalid JSON and stays quiet, while an unbounded one reads through and
    fires.
    """
    padding = "x" * (sq._MAX_STDIN_BYTES + 500_000)
    payload = json.dumps({"padding": padding, "prompt": "what's left?"})
    assert payload.index('"prompt"') > sq._MAX_STDIN_BYTES, "fixture must straddle the ceiling"
    r = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0
    assert not r.stdout.strip(), (
        "read past the byte ceiling -- the payload was truncated to invalid JSON, "
        "so firing means the ceiling did not hold")
