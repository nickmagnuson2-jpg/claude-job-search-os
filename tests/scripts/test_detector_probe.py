"""A detector that cannot fire on its own control is not a detector.

This file is the control-probe for the control-prober. The two cases that carry it are
test_a_pattern_that_matches_nothing_does_not_fire (a detector must be able to FAIL
validation, or this tool is decorative) and test_no_extractable_pattern_is_a_failure_not_a_pass
(an empty extraction and a working detector must never look alike, which is the exact
false-clean shape this whole project keeps hitting).
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import detector_probe as dp  # noqa: E402


# ---------------------------------------------------------------- firing

def test_a_real_pattern_fires_on_its_control():
    prose = "match `(?i)no such file or directory` in the tool result"
    r = dp.probe(prose, "(eval):cd:1: no such file or directory: output/example")
    assert r["fired"] and r["status"] == "fired"
    assert r["matched_pattern"] == "(?i)no such file or directory"


def test_a_pattern_that_matches_nothing_does_not_fire():
    """The tool must be able to FAIL, or validating with it proves nothing."""
    prose = "match `(?i)zzz_never_appears_\\d+` in the transcript"
    r = dp.probe(prose, "(eval):cd:1: no such file or directory")
    assert not r["fired"] and r["status"] == "did_not_fire"
    assert r["patterns_tried"] == 1


def test_no_extractable_pattern_is_a_failure_not_a_pass():
    r = dp.probe("Scan the transcript for anything that looks wrong.", "some control line")
    assert r["status"] == "no_pattern_found"
    assert r["fired"] is False, (
        "an unextractable detector must never report the same as a working one"
    )


def test_a_backticked_filename_is_not_mistaken_for_a_pattern():
    r = dp.probe("run `tools/detect_thing.py` over the transcripts", "tools/detect_thing.py")
    assert r["status"] == "no_pattern_found", (
        "a plain path in backticks has no metacharacters and is not a detector"
    )


def test_an_uncompilable_pattern_is_skipped_not_crashed():
    r = dp.probe("match `(?i)unclosed(` then `\\berror\\b`", "an error occurred")
    assert r["fired"], "a broken candidate must not prevent a working one from being tried"
    assert r["matched_pattern"] == "\\berror\\b"


def test_double_backtick_spans_are_extracted():
    r = dp.probe("pattern: ``\\bNOT FOUND\\b`` in the result", "reported NOT FOUND for the phrase")
    assert r["fired"]


# ---------------------------------------------------------------- whitespace

def test_a_line_wrapped_control_still_fires_after_normalization():
    """The 2nd fire of the origin rule was a phrase present but wrapped in a table cell."""
    r = dp.probe("match `demote\\s+the\\s+rung`", "demote\n   the    rung unilaterally")
    assert r["fired"]


def test_normalization_is_reported_when_it_was_required():
    r = dp.probe("match `foo\\s?bar\\s?baz`", "foo    bar\n baz")
    assert r["fired"]
    assert r["needed_normalization"] is True, (
        "the raw control has newlines and runs of spaces the pattern cannot span; "
        "reporting that normalization was required keeps the caveat visible"
    )


def test_a_literal_phrase_with_no_metacharacters_is_reported_unproven():
    """A known and deliberate limitation, and it fails in the safe direction.

    Accepting bare literals would make every backticked filename a 'pattern', and a
    filename that happens to appear in the control would report as a firing detector.
    Conservative here means unproven, never falsely proven."""
    r = dp.probe("look for `demote the rung` in the transcript", "demote the rung unilaterally")
    assert r["status"] == "no_pattern_found"


def test_normalization_flag_is_false_when_the_raw_control_matched():
    r = dp.probe("match `foo\\s+bar`", "foo bar")
    assert r["fired"] and r["needed_normalization"] is False


def test_normalize_collapses_and_strips():
    assert dp.normalize("  a\n\t b  ") == "a b"


# ---------------------------------------------------------------- extraction

def test_patterns_are_returned_longest_first():
    pats = dp.extract_patterns("`\\bfoo\\b` and `\\bfoo\\b|\\bbar\\b|\\bbazzz\\b`")
    assert pats[0] == "\\bfoo\\b|\\bbar\\b|\\bbazzz\\b", (
        "reporting a fragment as 'the detector' overstates what was validated"
    )


def test_duplicate_spans_are_collapsed():
    assert dp.extract_patterns("`\\berr\\b` then again `\\berr\\b`") == ["\\berr\\b"]


def test_extract_handles_empty_prose():
    assert dp.extract_patterns("") == []
    assert dp.extract_patterns(None) == []


# ---------------------------------------------------------------- aggregate + CLI

def test_probe_records_counts_each_outcome():
    recs = [
        {"name": "a", "prose": "`\\berror\\b`", "control": "an error here"},
        {"name": "b", "prose": "`\\bnope\\b`", "control": "an error here"},
        {"name": "c", "prose": "no pattern at all", "control": "x"},
    ]
    r = dp.probe_records(recs)
    assert (r["probed"], r["fired"], r["did_not_fire"], r["no_pattern_found"]) == (3, 1, 1, 1)
    assert r["ok"] is False


def test_probe_records_ok_only_when_all_fired():
    r = dp.probe_records([{"name": "a", "prose": "`\\berror\\b`", "control": "an error"}])
    assert r["ok"] is True


def test_an_empty_run_is_an_error_not_a_clean_bill():
    with pytest.raises(ValueError):
        dp.probe_records([])


def test_main_blocks_with_exit_2_on_an_unproven_detector(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"name": "a", "prose": "`\\bnope\\b`", "control": "x"}]),
                 encoding="utf-8")
    assert dp.main(["--records", str(p)]) == 2
    assert "UNPROVEN" in capsys.readouterr().out


def test_main_returns_0_when_all_fire(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"name": "a", "prose": "`\\berror\\b`", "control": "an error"}]),
                 encoding="utf-8")
    assert dp.main(["--records", str(p)]) == 0


def test_main_reports_a_missing_records_file(tmp_path, capsys):
    assert dp.main(["--records", str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err


def test_main_reports_malformed_json(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text("{not json", encoding="utf-8")
    assert dp.main(["--records", str(p)]) == 1
    assert capsys.readouterr().err.strip()


def test_summary_line_reports_every_bucket(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"name": "a", "prose": "`\\berror\\b`", "control": "an error"}]),
                 encoding="utf-8")
    dp.main(["--records", str(p)])
    out = capsys.readouterr().out
    assert "probed 1" in out and "fired 1" in out and "no pattern found 0" in out


def test_json_output_is_parseable(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"name": "a", "prose": "`\\berror\\b`", "control": "an error"}]),
                 encoding="utf-8")
    dp.main(["--records", str(p), "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------- mutation-driven gaps

def test_an_uncompilable_span_is_never_returned_as_a_pattern():
    """Directly pins extract_patterns. Testing this only through probe() hid it: probe
    swallows re.error and moves on, so an uncompilable pattern leaking out of extraction
    was invisible from the outside while still corrupting patterns_tried."""
    assert dp.extract_patterns("try `(?i)unclosed(` here") == []


def test_extract_keeps_the_compilable_span_and_drops_the_broken_one():
    assert dp.extract_patterns("`(?i)unclosed(` and `\\berror\\b`") == ["\\berror\\b"]


def test_a_clean_run_prints_no_UNPROVEN_lines(tmp_path, capsys):
    """Kills the mutant that reports every result as unproven, which would make the
    tool's own output indistinguishable between a validated and an unvalidated corpus."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"name": "a", "prose": "`\\berror\\b`", "control": "an error"}]),
                 encoding="utf-8")
    dp.main(["--records", str(p)])
    assert "UNPROVEN" not in capsys.readouterr().out
