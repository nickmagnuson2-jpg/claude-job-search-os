"""Tests for tools/check_probe_prompt.py (voice-sim probe prompt structural checker).

Every property test works the same way and it is the point of the file: take a prompt
that PASSES, break exactly one property on purpose, and assert the checker fails on THAT
property. Per feedback_regression_test_must_fail_against_the_unfixed_code — a test that
has never been observed to fail is not evidence.

Fixtures are synthetic (public repo; no real probe content here). They mirror the
hand-authored dialect in the probes directory, which is the surface this tool gates.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_probe_prompt.py"

GOOD = '''SINGLE PROBE. Not case mode unless this file says otherwise.

HANDSHAKE - do not skip:
When you receive this message, reply with exactly one word: Ready.
Say nothing else. Do not preview, summarize, or hint at the question.

Then WAIT. When Sam says "go", say the PROBE below verbatim and nothing else, then stop.

Sam turns voice mode on between your "Ready." and his "go", so anything said before "go"
gets read rather than heard.

PROBE (say only after "go"):
"Your system just changed a record inside a customer environment. Who authorised it?"

FOLLOW-UPS, in order, at least two:
- "Someone delegated that authority and then changed roles. Who catches it?"
- "Could you just narrow the permissions? Does that not solve it?"

WATCH FOR (do not correct; ask the follow-up that exposes it, then let it sit):
- Two adjacent terms used interchangeably when the whole answer turns on the difference.
- A log of prior actors claimed as proof of authorisation.

When the follow-ups are exhausted, say exactly: "End of simulation. Take this conversation to the debrief." Nothing else.
'''


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def write(tmp_path, text, name="probe.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def failed_properties(stdout):
    """Property names the checker reported as FAIL."""
    return {line.split()[1] for line in stdout.splitlines() if " FAIL - " in line}


# --- baseline: the unbroken fixture must pass, or every test below is meaningless ---

def test_good_prompt_passes_all_seven(tmp_path):
    r = run(write(tmp_path, GOOD), "--speaker", "Sam")
    assert r.returncode == 0, r.stdout + r.stderr
    assert failed_properties(r.stdout) == set()
    assert "7 of 7 mechanical properties pass" in r.stdout


# --- one deliberately-broken input per property ---

def test_first_words_fails_when_probe_is_buried_at_the_bottom(tmp_path):
    lines = GOOD.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("PROBE ("))
    block = lines[start:start + 2]
    broken = "".join(lines[:start] + lines[start + 2:-1] + block + lines[-1:])
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)


def test_first_words_fails_when_probe_text_is_not_quoted_verbatim(tmp_path):
    broken = GOOD.replace(
        '"Your system just changed a record inside a customer environment. Who authorised it?"',
        "Ask him something about authorisation inside a customer environment.")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)


def test_first_words_fails_when_there_is_no_probe_block_at_all(tmp_path):
    broken = "\n".join(ln for ln in GOOD.splitlines()
                       if not ln.startswith("PROBE ("))
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)
    assert "no literal probe block" in r.stdout


def test_first_words_fails_when_delivery_is_not_constrained(tmp_path):
    """Without 'and nothing else' the partner is free to preface the probe."""
    broken = GOOD.replace("nothing else", "little else")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)
    assert "not constrained" in r.stdout


def test_first_words_fails_when_the_terminator_is_missing(tmp_path):
    broken = GOOD.replace("End of simulation. ", "")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)
    assert "scripted terminator" in r.stdout


def test_first_words_fails_on_ambiguous_trailing_handshake(tmp_path):
    broken = GOOD.rstrip() + '\n\nSay "Start" to begin.\n'
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "FIRST_WORDS" in failed_properties(r.stdout)


def test_role_bound_fails_when_speaker_is_unnamed(tmp_path):
    broken = GOOD.replace("Sam", "the candidate")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "ROLE_BOUND" in failed_properties(r.stdout)
    assert "never named" in r.stdout


def test_role_bound_fails_when_name_appears_but_is_never_bound(tmp_path):
    broken = GOOD.replace('When Sam says "go"', 'When the operator says "go"')
    broken = broken.replace("Sam turns voice mode on", "Voice mode goes on")
    broken = broken.replace("SINGLE PROBE.", "SINGLE PROBE. Sam. Sam.")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "ROLE_BOUND" in failed_properties(r.stdout)
    assert "never bound to the person speaking" in r.stdout


def test_role_bound_fails_when_the_name_is_bound_but_stated_only_once(tmp_path):
    """One mention is a mention, not a binding that survives twenty turns."""
    broken = GOOD.replace("Sam turns voice mode on", "Voice mode goes on")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "ROLE_BOUND" in failed_properties(r.stdout)
    assert "only once" in r.stdout


def test_prohibition_fails_without_a_pre_send_redirect(tmp_path):
    broken = GOOD.replace(
        "WATCH FOR (do not correct; ask the follow-up that exposes it, then let it sit):",
        "WATCH FOR (do not correct these):")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "PROHIBITION" in failed_properties(r.stdout)
    assert "no pre-send redirect" in r.stdout


def test_prohibition_fails_when_absent_entirely(tmp_path):
    broken = GOOD.replace(
        "WATCH FOR (do not correct; ask the follow-up that exposes it, then let it sit):",
        "WATCH FOR:")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "PROHIBITION" in failed_properties(r.stdout)
    assert "no prohibition section" in r.stdout


def test_size_fails_over_the_ceiling(tmp_path):
    broken = GOOD + ("- extra background the partner will not hold by mid-session\n" * 200)
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "SIZE" in failed_properties(r.stdout)
    assert "ceiling is 6144" in r.stdout


def test_size_ceiling_is_configurable(tmp_path):
    r = run(write(tmp_path, GOOD), "--speaker", "Sam", "--max-bytes", "100")
    assert r.returncode == 1
    assert "SIZE" in failed_properties(r.stdout)


def test_standalone_fails_on_a_back_reference_to_another_paste(tmp_path):
    broken = GOOD.replace("HANDSHAKE - do not skip:",
                          "Same rules as before.\n\nHANDSHAKE - do not skip:")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "STANDALONE" in failed_properties(r.stdout)


def test_standalone_fails_when_a_re_stated_element_is_missing(tmp_path):
    """A paste that dropped the prohibition is not standalone, back-reference or not."""
    broken = GOOD.replace(
        "WATCH FOR (do not correct; ask the follow-up that exposes it, then let it sit):",
        "WATCH FOR:")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert {"PROHIBITION", "STANDALONE"} <= failed_properties(r.stdout)


def test_task_shape_fails_on_a_second_probe(tmp_path):
    broken = GOOD.replace(
        "FOLLOW-UPS, in order, at least two:",
        'Opening probe: "And separately, walk me through a rollback."\n\n'
        "FOLLOW-UPS, in order, at least two:")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "TASK_SHAPE" in failed_properties(r.stdout)
    assert "2 probes" in r.stdout


def test_task_shape_fails_when_single_probe_is_not_declared_up_top(tmp_path):
    broken = GOOD.replace("SINGLE PROBE. Not case mode unless this file says otherwise.\n",
                          "A drill.\n")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "TASK_SHAPE" in failed_properties(r.stdout)


def test_task_shape_allows_multiple_probes_when_case_mode_is_declared(tmp_path):
    multi = GOOD.replace(
        "SINGLE PROBE. Not case mode unless this file says otherwise.",
        "CASE MODE. This file runs a full case, not a single probe.").replace(
        "FOLLOW-UPS, in order, at least two:",
        'Opening probe: "And separately, walk me through a rollback."\n\n'
        "FOLLOW-UPS, in order, at least two:")
    r = run(write(tmp_path, multi), "--speaker", "Sam")
    assert "TASK_SHAPE" not in failed_properties(r.stdout)
    assert r.returncode == 0


def test_not_case_mode_line_does_not_count_as_declaring_case_mode(tmp_path):
    """The literal 'Not case mode' phrase must not switch the single-probe rule off."""
    broken = GOOD.replace(
        "FOLLOW-UPS, in order, at least two:",
        'Opening probe: "And separately, walk me through a rollback."\n\n'
        "FOLLOW-UPS, in order, at least two:")
    assert "Not case mode" in broken
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "TASK_SHAPE" in failed_properties(r.stdout)


def test_handshake_fails_when_the_section_is_missing(tmp_path):
    broken = "\n".join(ln for ln in GOOD.splitlines()
                       if not ln.startswith("HANDSHAKE"))
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "HANDSHAKE" in failed_properties(r.stdout)


def test_handshake_fails_without_the_one_word_ready_reply(tmp_path):
    broken = GOOD.replace("reply with exactly one word: Ready.",
                          "let him know you have read this.")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "HANDSHAKE" in failed_properties(r.stdout)


def test_handshake_fails_without_the_go_gate(tmp_path):
    broken = GOOD.replace('When Sam says "go", say the PROBE below verbatim',
                          "Sam is listening, so say the PROBE below verbatim")
    r = run(write(tmp_path, broken), "--speaker", "Sam")
    assert r.returncode == 1
    assert "HANDSHAKE" in failed_properties(r.stdout)


# --- the property no file checker can see ---

def test_position_is_reported_as_unchecked_on_a_clean_pass(tmp_path):
    r = run(write(tmp_path, GOOD), "--speaker", "Sam")
    assert r.returncode == 0
    assert "8 POSITION" in r.stdout and "NOT CHECKED" in r.stdout
    assert "NOT a safety verdict" in r.stdout


def test_position_is_reported_as_unchecked_on_a_failure_too(tmp_path):
    r = run(write(tmp_path, GOOD.replace("HANDSHAKE", "PREAMBLE")), "--speaker", "Sam")
    assert r.returncode == 1
    assert "8 POSITION" in r.stdout and "NOT CHECKED" in r.stdout


# --- CLI surface ---

def test_failing_file_reports_a_not_ready_verdict_with_a_count(tmp_path):
    """A file that fails properties must not print the pass verdict."""
    r = run(write(tmp_path, GOOD.replace("HANDSHAKE", "PREAMBLE")), "--speaker", "Sam")
    assert r.returncode == 1
    assert "NOT READY - 1 of 7 mechanical properties failed." in r.stdout
    assert "7 of 7 mechanical properties pass" not in r.stdout


def test_plain_output_does_not_emit_the_json_report(tmp_path):
    r = run(write(tmp_path, GOOD), "--speaker", "Sam")
    assert r.returncode == 0
    assert '"property"' not in r.stdout
    # Also catches an empty JSON payload leaking into human output.
    assert r.stdout.strip().endswith("actually broke reps.")


def test_json_report_carries_the_position_caveat(tmp_path):
    r = run(write(tmp_path, GOOD), "--speaker", "Sam", "--json")
    payload = json.loads(r.stdout)
    assert payload[0]["passed"] is True
    assert len(payload[0]["properties"]) == 7
    assert "not checkable" in payload[0]["position_property_8"]


def test_json_report_names_the_failed_properties(tmp_path):
    r = run(write(tmp_path, GOOD.replace("HANDSHAKE", "PREAMBLE")),
            "--speaker", "Sam", "--json")
    payload = json.loads(r.stdout)
    assert payload[0]["passed"] is False
    bad = [p["name"] for p in payload[0]["properties"] if not p["ok"]]
    assert bad == ["HANDSHAKE"]


def test_multiple_files_exit_nonzero_if_any_fails(tmp_path):
    ok = write(tmp_path, GOOD, "ok.md")
    bad = write(tmp_path, GOOD.replace("HANDSHAKE", "PREAMBLE"), "bad.md")
    assert run(ok, ok, "--speaker", "Sam").returncode == 0
    assert run(ok, bad, "--speaker", "Sam").returncode == 1


def test_missing_file_is_a_usage_error(tmp_path):
    r = run(tmp_path / "nope.md")
    assert r.returncode == 2
    assert "not a file" in r.stderr


def test_default_speaker_is_the_operator(tmp_path):
    """With no --speaker, the default name must be the one the checker looks for."""
    r = run(write(tmp_path, GOOD.replace("Sam", "Nick")))
    assert r.returncode == 0
