#!/usr/bin/env python3
"""Tests for tools/check_derived_ratio.py, the Stop hook that blocks a ratio derived in
chat whose value no tool output or user message printed.

Clean and block pairs per tools/HOOK_AUTHORING.md, plus the loop guard, the override,
the fail-open paths, and the formatting variants a printed value may take.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_derived_ratio.py"

spec = importlib.util.spec_from_file_location("cdr", HOOK)
cdr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdr)


def values(text):
    return [h["value"] for h in cdr.find_derived(text)]


# ---------- extraction: each shape is caught ----------

@pytest.mark.parametrize("text, value", [
    ("So 12 / 40 = 30% of the sample.", "30"),
    ("That gives 12/40 = 0.3 overall.", "0.3"),
    ("Recall was 12/40 ≈ 0.30 here.", "0.30"),
    ("It fired 7 of 22 times (31.8%).", "31.8"),
    ("It fired 7/22 (31.8%).", "31.8"),
    ("It fired 7 of 22 turns (31.8%).", "31.8"),
    ("It fired 7 out of 22 (31.8%).", "31.8"),
    ("It fired 7 of 22 sessions, or 31.8%.", "31.8"),
    ("It fired 7 of 22, or about 31.8%.", "31.8"),
    ("Hit rate is 31.8% (7 of 22).", "31.8"),
    ("Hit rate is 31.8% (7/22).", "31.8"),
    ("It fired 7 times, that's 31.8% of 22 sessions.", "31.8"),
    ("It fired 7 times, that is 31.8% of the 22 sessions.", "31.8"),
    ("Big: 1,234 of 5,000 (24.7%).", "24.7"),
    ("It fired 7 of 22 (~31.8%).", "31.8"),
])
def test_shape_caught(text, value):
    assert value in values(text)


@pytest.mark.parametrize("text", [
    "It fired 7 of 22, which is 31.8%.",
    "It fired 7 of 22 times, which is 31.8%.",
    "It fired 7 of 22 times, that's 31.8%.",
    "It fired 7 of 22 times, that is 31.8%.",
    "It fired 7 of 22 times, or 31.8%.",
    "It fired 7 of 22 times, i.e. 31.8%.",
    "It fired 7 of 22 times i.e. 31.8%.",
    "It fired 7 of 22 times - 31.8%.",
    "It fired 7 of 22 times – 31.8%.",
    "It fired 7 out of 22, which was 31.8%.",
])
def test_copular_shape_caught(text):
    hits = cdr.find_derived(text)
    assert [h["value"] for h in hits] == ["31.8"]
    assert hits[0]["operands"] == ["7", "22"]
    assert hits[0]["pct"] is True


@pytest.mark.parametrize("text", [
    "It fired 7 of 22 times, and later 31.8% of the budget went to ads.",
    "It fired 7 of 22 times; the error rate is 31.8%.",
    "It fired 7 of 22 times, which is fine. Budget use is 31.8%.",
    "We closed 12 of 40 tickets - 5 remain open.",          # dash, no percent
    "It fired 7 of 22 big noisy times, which is 31.8%.",    # more than one noun
])
def test_copular_shape_negatives(text):
    assert values(text) == []


def test_pct_flag_recorded():
    hits = cdr.find_derived("It fired 7 of 22 (31.8%).")
    assert hits[0]["pct"] is True
    hits = cdr.find_derived("That gives 12/40 = 0.3 overall.")
    assert hits[0]["pct"] is False


def test_hit_carries_shape_match_and_sentence():
    h = cdr.find_derived("Intro. It fired 7 of 22 (31.8%). Outro.")[0]
    assert h["shape"] == "of-paren"
    assert h["match"] == "7 of 22 (31.8%"
    assert h["sentence"] == "It fired 7 of 22 (31.8%)."


def test_two_ratios_in_one_reply_both_caught():
    got = values("A was 3 of 9 (33.3%). B was 4/16 = 25%.")
    assert got == ["33.3", "25"]


def test_one_value_matched_by_two_shapes_is_reported_once():
    # slash-equals and pct-paren both match the same C here.
    assert values("So 1/3 = 33.3% (1 of 3) overall.") == ["33.3"]


def test_same_value_twice_reported_twice_at_different_positions():
    got = values("A was 3 of 9 (33.3%) and B was 2 of 6 (33.3%).")
    assert got == ["33.3", "33.3"]


# ---------- extraction: out of scope, not caught ----------

@pytest.mark.parametrize("text", [
    "About 31.8% of sessions hit it.",                       # bare percentage
    "Move the rate from 12% to 30% this quarter.",           # target
    "If 3 of 10 fail (30%), we stop.",                       # hypothetical
    "Suppose 12/40 = 30% held.",                             # hypothetical
    "For example, 3 of 10 (30%) would be fine.",             # illustration
    "E.g. 3 of 10 (30%) is fine.",                           # illustration
    "It fired, that's 31.8% of 22 sessions.",                # that's-of without N
    "Run `x = 12/40 = 0.3` to see.",                         # inline code
    "```\nprint(12/40 = 0.3)\n7 of 22 (31.8%)\n```",          # fenced code
    "> The paper found 7 of 22 (31.8%) improved.",           # blockquote
    'The report says "7 of 22 (31.8%) improved" in Q3.',     # double-quoted source
    "The report says “7 of 22 (31.8%) improved” in Q3.",     # smart-quoted source
    "See https://x.com/a/12/40=30 for it.",                  # URL
    "Version 2/3 = stable.",                                 # no number after =
    "Dated 10/23 and 7 of 22 sessions.",                     # no derived value
    "It ran 7 of 22 (fast).",                                # paren is not a percent
    "Open run_7 of 22 (31.8%).",                             # N glued to a word
])
def test_not_caught(text):
    assert values(text) == []


@pytest.mark.parametrize("text", [
    "The results say 7 of 22 (31.8%) failed.",                 # mid-sentence "say"
    "I checked if errors clustered: 7 of 22 (31.8%) did.",     # mid-sentence "if"
    "We stop if 7 of 22 (31.8%) fail.",                        # mid-sentence "if"
])
def test_mid_sentence_hypothetical_word_does_not_veto(text):
    assert values(text) == ["31.8"]


@pytest.mark.parametrize("text", [
    "Say 3 of 10 (30%) fail.",
    "Imagine 3 of 10 (30%) fail.",
    "Hypothetically 3 of 10 (30%) fail.",
    "Supposing 3 of 10 (30%) fail.",
    "For instance, 3 of 10 (30%) fail.",
    "- If 3 of 10 (30%) fail, we stop.",                       # list bullet
    "**Suppose** 12/40 = 30% held.",                           # bold
])
def test_sentence_initial_hypothetical_vetoes(text):
    assert values(text) == []


def test_hypothetical_only_suppresses_its_own_sentence():
    assert values("If it fails we stop. It fired 7 of 22 (31.8%).") == ["31.8"]


def test_unclosed_fence_is_stripped():
    assert values("Here:\n```\n7 of 22 (31.8%)\n") == []


def test_strip_noise_keeps_ordinary_prose():
    out = cdr.strip_noise("It fired 7 of 22 (31.8%).")
    assert "7 of 22 (31.8%)" in out


# ---------- was_printed: formatting variants ----------

@pytest.mark.parametrize("value, pct, printed", [
    ("10", False, {"10"}),
    ("10", False, {"10.0"}),
    ("10.00", False, {"10"}),        # same number, more zeros in chat
    ("10", True, {"0.10"}),          # decimal fraction for a percent
    ("10", True, {"10%"}),           # percent for a percent
    ("0.10", False, {"10%"}),        # percent-marked token for a decimal
    ("31.8", True, {"31.82%"}),      # rounds to the chat precision
    ("31.8", True, {"0.3182"}),      # decimal fraction, rounds
    ("32", True, {"31.82%"}),        # chat coarser than print
    ("24.7", False, {"24.68"}),
    ("100", True, {"1.0"}),          # boundary: a printed fraction of exactly 1
    ("1", False, {"100%"}),          # boundary: a chat fraction of exactly 1
    ("30", False, {"0.3"}),          # x100 from a fraction also serves a plain value
])
def test_printed_variants_count(value, pct, printed):
    assert cdr.was_printed(value, printed, pct) is True


@pytest.mark.parametrize("value, pct, printed", [
    ("31.8", True, {"31.7%"}),       # different number
    ("31.82", True, {"31.8%"}),      # print less precise than chat: does not vouch
    ("31.8", True, {"32%"}),         # print less precise than chat
    ("10", True, set()),
    ("30", True, {"3"}),             # 3 is not 30 under any variant
    ("31.8", True, {"3.18"}),        # x10 is not an allowed variant
    # Round-1 replay coincidences: a large count must not vouch through /100.
    ("39", True, {"3892"}),
    ("10", True, {"962"}),
    ("9.8", True, {"985"}),
    ("3", True, {"300"}),
    ("10.2", False, {"1022"}),
    # x100 applies only to a printed fraction (<= 1).
    ("150", True, {"1.5"}),
    ("101", True, {"1.01"}),
    # /100 applies only when the chat value is a fraction (<= 1).
    ("1.3", False, {"130"}),
    # Codex F1: /100 needs a percent-marked token, x100 a fraction-form token.
    ("0.3", False, {"30"}),          # a bare count of 30 is not 30%
    ("0.10", False, {"10"}),
    ("1", False, {"100"}),
    ("100", True, {"1"}),            # an integer 1 is not the fraction 1.0
    ("30", True, {"0.3%"}),          # x100 never from a percent-marked token
    # Final review (546.F2 / 547.F1): the unit must match on a direct match too.
    ("30", True, {"30"}),            # a bare count of 30 is not 30%
    ("30", False, {"30%"}),          # 30% is not the plain value 30
])
def test_printed_mismatch_does_not_count(value, pct, printed):
    assert cdr.was_printed(value, printed, pct) is False


def test_was_printed_handles_thousands_separator():
    assert cdr.was_printed("1234", {"1,234"}, False) is True


def test_unsourced_filters_by_printed_values():
    text = "A was 3 of 9 (33.3%). B was 4/16 = 25%."
    left = cdr.unsourced(text, [{"3", "9", "33.33%"}])
    assert [h["value"] for h in left] == ["25"]


def test_unsourced_needs_one_text_with_c_n_and_m():
    text = "A was 3 of 9 (33.3%)."
    # C, N and M are all printed, but never in the same text: does not vouch.
    assert [h["value"] for h in cdr.unsourced(text, [{"33.3%"}, {"3", "9"}])] == ["33.3"]
    assert cdr.unsourced(text, [{"33.3%"}, {"3", "9", "33.3%"}]) == []
    assert [h["value"] for h in cdr.unsourced(text, [])] == ["33.3"]


# ---------- operands and the co-location rule ----------

@pytest.mark.parametrize("text, operands", [
    ("So 12 / 40 = 30% of it.", ["12", "40"]),
    ("It fired 7 of 22 turns (31.8%).", ["7", "22"]),
    ("It fired 7 of 22, or 31.8%.", ["7", "22"]),
    ("Hit rate is 31.8% (7/22).", ["7", "22"]),
    ("Big: 1,234 of 5,000 (24.7%).", ["1,234", "5,000"]),
])
def test_operands_are_n_and_m(text, operands):
    h = cdr.find_derived(text)[0]
    assert h["operands"] == operands
    assert h["alternates"] == []


def test_thats_of_operands_are_m_plus_any_other_sentence_number():
    h = cdr.find_derived("It fired 7 times, that's 31.8% of 22 sessions.")[0]
    assert h["operands"] == ["22"]
    assert h["alternates"] == ["7"]


def test_comma_numbers_parse_whole():
    # Round-3 false positive: "123,456/7 = 17,636.57" was read as "456/7 = 17".
    hits = cdr.find_derived("Per customer: 123,456/7 = 17,636.57 dollars.")
    assert len(hits) == 1
    assert hits[0]["value"] == "17,636.57"
    assert hits[0]["operands"] == ["123,456", "7"]
    assert hits[0]["match"] == "123,456/7 = 17,636.57"


@pytest.mark.parametrize("text", [
    "Shares: 1,234/5,678 = 21.7% today.",
    "Total 2,345 of 45,678 calls (5.1%).",
    "Rate 5.1% (2,345 of 45,678) overall.",
])
def test_comma_numbers_in_every_shape(text):
    h = cdr.find_derived(text)[0]
    assert all("," in o for o in h["operands"])


def test_no_match_starts_inside_a_separated_number():
    assert all(h["operands"][0] != "456" for h in cdr.find_derived("x 123,456/7 = 17,636.57"))
    assert values("v 1,856/7 = 265.14") == ["265.14"]


def test_was_printed_accepts_comma_value():
    assert cdr.was_printed("17,636.57", {"17636.57143"}, False) is True
    assert cdr.was_printed("17,636.57", {"17,636.6"}, False) is False


@pytest.mark.parametrize("nums, ok", [
    ({"7", "22", "31.8%"}, True),
    ({"7.0", "22", "31.82%"}, True),       # operands compared as numbers
    ({"7", "22", "31.8"}, False),          # unit: a bare 31.8 is not 31.8%
    ({"22", "31.8"}, False),               # N missing
    ({"7", "31.8"}, False),                # M missing
    ({"7", "22"}, False),                  # C missing
    ({"7", "22", "31.7"}, False),          # wrong C
])
def test_vouches_requires_c_n_and_m(nums, ok):
    h = cdr.find_derived("It fired 7 of 22 (31.8%).")[0]
    assert cdr.vouches(h, nums) is ok


@pytest.mark.parametrize("nums, ok", [
    ({"22", "7", "31.8%"}, True),
    ({"22", "31.8%"}, False),              # no other sentence number printed
    ({"7", "31.8%"}, False),               # M missing
])
def test_vouches_thats_of_shape(nums, ok):
    h = cdr.find_derived("It fired 7 times, that's 31.8% of 22 sessions.")[0]
    assert cdr.vouches(h, nums) is ok


# ---------- evidence collection ----------

def test_numbers_in_strips_read_line_prefixes():
    assert cdr.numbers_in("    32\tfoo bar\n    33→baz 7.5") == {"7.5"}


def test_numbers_in_keeps_percent_marker():
    assert cdr.numbers_in("rate 31.8% and 30 % and 12 hits") == {"31.8%", "30%", "12"}


def test_numbers_in_ignores_word_glued_numbers():
    assert cdr.numbers_in("test_33 v2 (31.8) 1,234") == {"31.8", "1,234"}


def test_evidence_texts_shapes():
    tool = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "a 1"},
        {"type": "tool_result", "content": [{"type": "text", "text": "b 2"},
                                            {"type": "image"}, "junk"]},
        {"type": "text", "text": "c 3"},
        {"type": "image"},
        {"type": "tool_reference", "content": "9 9"},   # not a tool_result
        {"type": "tool_result", "content": None},       # no content at all
        {"type": "tool_result", "content": 5},          # not str or list
        "junk",
    ]}}
    assert cdr.evidence_texts(tool) == ["a 1", "b 2", "c 3"]
    assert cdr.evidence_texts({"type": "user", "message": {"content": "hi 4"}}) == ["hi 4"]


@pytest.mark.parametrize("rec", [
    {"type": "assistant", "message": {"content": "7 of 22 (31.8%)"}},
    {"type": "user", "isSidechain": True, "message": {"content": "31.8"}},
    {"type": "user", "isCompactSummary": True, "message": {"content": "31.8"}},
    {"type": "user", "message": "31.8"},
    {"type": "user", "message": {"content": None}},
    {"type": "user", "message": {"content": [{"type": "text"}]}},
    "not a dict",
])
def test_evidence_texts_excluded_records(rec):
    assert "31.8" not in " ".join(cdr.evidence_texts(rec))


def test_evidence_sets_missing_file_is_empty():
    assert cdr.evidence_sets("/nonexistent/x.jsonl") == []


def test_evidence_sets_one_set_per_text_and_skips_numberless(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        _tool("a 1 b 2"), _user("no numbers here"), _tool("c 3"),
        _asst("assistant 4")]) + "\n", encoding="utf-8")
    assert cdr.evidence_sets(str(t)) == [{"1", "2"}, {"3"}]


# ---------- end-to-end hook behaviour ----------

def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


def _asst(text):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _run(final_text, tmpdir, prior=(), stop_active=False, env_extra=None, raw_lines=()):
    tpath = Path(tmpdir) / "t.jsonl"
    lines = list(raw_lines) + [json.dumps(r) for r in prior] + [json.dumps(_asst(final_text))]
    tpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {"transcript_path": str(tpath), "stop_hook_active": stop_active}
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def test_blocks_unsourced_ratio():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 times (31.8%).", td,
                 prior=[_user("how often?"), _tool("fired: 7\nsessions: 22")])
        assert r.returncode == 2
        assert "BLOCKED check_derived_ratio" in r.stderr
        assert "together with both of its counts" in r.stderr
        assert "31.8%  in: 7 of 22 times (31.8%" in r.stderr
        assert "tools/count_report.py" in r.stderr
        assert "Compute the number in a script" in r.stderr
        assert "tools/friction_log.py append check_derived_ratio.py" in r.stderr
        assert "DERIVED_RATIO_OVERRIDE=1" in r.stderr


def test_block_listing_omits_percent_for_plain_value():
    with tempfile.TemporaryDirectory() as td:
        r = _run("That gives 12/40 = 0.3 overall.", td, prior=[_tool("12 and 40")])
        assert r.returncode == 2
        assert "  - 0.3  in: 12/40 = 0.3" in r.stderr


def test_clean_when_tool_printed_value():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 times (31.8%).", td, prior=[_tool("fired 7 of 22, rate: 31.82%")])
        assert r.returncode == 0, r.stderr


def test_clean_when_tool_printed_decimal_form():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 times (31.8%).", td, prior=[_tool("hits=7 n=22 rate=0.318")])
        assert r.returncode == 0, r.stderr


def test_clean_when_tool_printed_value_in_list_content():
    with tempfile.TemporaryDirectory() as td:
        rec = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "7 22 31.8%"}]}]}}
        r = _run("It fired 7 of 22 times (31.8%).", td, prior=[rec])
        assert r.returncode == 0, r.stderr


def test_clean_when_user_stated_value_with_counts():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Yes, 7 of 22 (31.8%) is right.", td,
                 prior=[_user("7 of 22, is it 31.8%?")])
        assert r.returncode == 0, r.stderr


def test_user_stating_only_the_ratio_does_not_clear():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Yes, 7 of 22 (31.8%) is right.", td,
                 prior=[_user("is it 31.8%?"), _tool("fired 7\nsessions 22")])
        assert r.returncode == 2


def test_coincidental_bare_percent_elsewhere_does_not_clear():
    """The canonical miss: an unrelated quote printed "10%", and the counts came from
    another output. Pooled evidence cleared it; co-location must not."""
    with tempfile.TemporaryDirectory() as td:
        r = _run("Globex converts at 12 / 122 = 10%.", td, prior=[
            _tool('Casey Doe: "roughly 10% faster than last quarter" in a survey.'),
            _tool("Globex signups 12 visitors 122")])
        assert r.returncode == 2
        assert "10%  in: 12 / 122 = 10%" in r.stderr


def test_unrelated_count_does_not_vouch_through_percent_scale():
    """Codex F1: the count 30 must not clear 0.3 by being read as 30%."""
    with tempfile.TemporaryDirectory() as td:
        r = _run("That gives 12/40 = 0.3 overall.", td,
                 prior=[_tool("12 hits, 40 total, 30 errors")])
        assert r.returncode == 2
        assert "0.3  in: 12/40 = 0.3" in r.stderr


def test_bare_count_does_not_vouch_for_a_percent():
    """Final review 547.F1: three counts printed, the division done in chat."""
    with tempfile.TemporaryDirectory() as td:
        r = _run("That gives 12/40 = 30% overall.", td,
                 prior=[_tool("12 hits, 40 total, 30 errors")])
        assert r.returncode == 2
        assert "30%  in: 12/40 = 30%" in r.stderr


def test_percent_printed_with_counts_clears_percent():
    with tempfile.TemporaryDirectory() as td:
        r = _run("That gives 12/40 = 30% overall.", td,
                 prior=[_tool("12 hits, 40 total, 30% hit rate")])
        assert r.returncode == 0, r.stderr


def test_percent_printed_with_counts_clears_decimal():
    with tempfile.TemporaryDirectory() as td:
        r = _run("That gives 12/40 = 0.3 overall.", td,
                 prior=[_tool("12 hits, 40 total, 30% hit rate")])
        assert r.returncode == 0, r.stderr


def test_ratio_printed_with_its_counts_clears():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Globex converts at 12 / 122 = 9.8%.", td,
                 prior=[_tool("Globex 12/122 = 0.0984")])
        assert r.returncode == 0, r.stderr


def test_comma_ratio_printed_by_tool_clears():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Per customer: 123,456/7 = 17,636.57 dollars.", td,
                 prior=[_tool("123456/7 = 17636.571428571428")])
        assert r.returncode == 0, r.stderr


def test_clean_on_target_and_bare_percent():
    with tempfile.TemporaryDirectory() as td:
        r = _run("We are at 31.8% and aim to go from 12% to 30%.", td)
        assert r.returncode == 0, r.stderr


def test_clean_on_quote_and_code():
    with tempfile.TemporaryDirectory() as td:
        text = ("> Source: 7 of 22 (31.8%) improved.\n\n"
                "```\n12/40 = 0.3\n```\nRun `a/b = 0.3` too.")
        r = _run(text, td)
        assert r.returncode == 0, r.stderr


def test_assistant_earlier_text_is_not_evidence():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Still 7 of 22 (31.8%).", td,
                 prior=[_asst("Earlier: 7 of 22 is 31.8%."), _tool("7 and 22")])
        assert r.returncode == 2


def test_read_line_numbers_are_not_evidence():
    with tempfile.TemporaryDirectory() as td:
        r = _run("So 800/25 = 32.", td, prior=[_tool("800 of 25 rows\n    32\tsome line\n    33\tmore")])
        assert r.returncode == 2


def test_sidechain_and_compact_summary_are_not_evidence():
    with tempfile.TemporaryDirectory() as td:
        side = {"type": "user", "isSidechain": True, "message": {"content": "7 22 31.8%"}}
        comp = {"type": "user", "isCompactSummary": True,
                "message": {"content": "7 22 31.8%"}}
        r = _run("It fired 7 of 22 (31.8%).", td, prior=[side, comp])
        assert r.returncode == 2


def test_malformed_transcript_lines_are_skipped():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 (31.8%).", td, prior=[_tool("7 22 31.8%")],
                 raw_lines=["{not json", "[1, 2]"])
        assert r.returncode == 0, r.stderr


def test_recursion_guard():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 (31.8%).", td, stop_active=True)
        assert r.returncode == 0
        assert r.stderr == ""


def test_override_env():
    with tempfile.TemporaryDirectory() as td:
        r = _run("It fired 7 of 22 (31.8%).", td, env_extra={"DERIVED_RATIO_OVERRIDE": "1"})
        assert r.returncode == 0
        r = _run("It fired 7 of 22 (31.8%).", td, env_extra={"DERIVED_RATIO_OVERRIDE": "0"})
        assert r.returncode == 2


def test_no_ratio_is_clean():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Done. The file is written.", td)
        assert r.returncode == 0
        assert r.stderr == ""


def test_missing_transcript_is_noop():
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"transcript_path": "/nonexistent/x.jsonl"}),
                       capture_output=True, text=True,
                       env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0


def test_malformed_stdin_is_noop():
    r = subprocess.run([sys.executable, str(HOOK)], input="{not json",
                       capture_output=True, text=True,
                       env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0
    assert r.stderr == ""


# ---------- main() in-process: exact return values on every path ----------

sys.path.insert(0, str(ROOT / "tools"))
from hook_runtime import Payload  # noqa: E402


def _main_with(monkeypatch, payload, env=None):
    monkeypatch.setattr(cdr, "read_payload", lambda: payload)
    monkeypatch.delenv("DERIVED_RATIO_OVERRIDE", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return cdr.main()


def _transcript(tmp_path, final_text, prior=()):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [*prior, _asst(final_text)]) + "\n",
                 encoding="utf-8")
    return str(t)


def test_main_unread_payload_returns_zero(monkeypatch):
    assert _main_with(monkeypatch, Payload(ok=False, error="empty stdin")) == 0


def test_main_override_returns_zero(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "It fired 7 of 22 (31.8%).")
    p = Payload(ok=True, data={"transcript_path": t})
    assert _main_with(monkeypatch, p, {"DERIVED_RATIO_OVERRIDE": "1"}) == 0


def test_main_loop_guard_returns_zero(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "It fired 7 of 22 (31.8%).")
    p = Payload(ok=True, data={"transcript_path": t, "stop_hook_active": True})
    assert _main_with(monkeypatch, p) == 0


def test_main_no_ratio_returns_zero(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "All done.")
    assert _main_with(monkeypatch, Payload(ok=True, data={"transcript_path": t})) == 0


def test_main_no_ratio_never_scans_evidence(monkeypatch, tmp_path):
    """The evidence scan reads the whole transcript a second time; skip it when the
    reply has no derived ratio, which is almost every turn."""
    def boom(_):
        raise AssertionError("evidence scanned for a reply with no ratio")
    monkeypatch.setattr(cdr, "evidence_sets", boom)
    t = _transcript(tmp_path, "All done, 31.8% of it.")
    assert _main_with(monkeypatch, Payload(ok=True, data={"transcript_path": t})) == 0


def test_main_no_transcript_returns_zero(monkeypatch):
    assert _main_with(monkeypatch, Payload(ok=True, data={})) == 0


def test_main_sourced_ratio_returns_zero(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "It fired 7 of 22 (31.8%).", prior=[_tool("7 of 22: 31.8%")])
    assert _main_with(monkeypatch, Payload(ok=True, data={"transcript_path": t})) == 0


def test_main_unsourced_ratio_returns_two(monkeypatch, tmp_path, capsys):
    t = _transcript(tmp_path, "It fired 7 of 22 (31.8%).", prior=[_tool("7 22")])
    assert _main_with(monkeypatch, Payload(ok=True, data={"transcript_path": t})) == 2
    assert "BLOCKED check_derived_ratio" in capsys.readouterr().err
