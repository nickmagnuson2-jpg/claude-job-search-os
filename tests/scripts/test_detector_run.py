"""The intake loop: a rule's own fires increment its own counter.

The two cases this file exists for:

  test_a_detector_that_fails_its_control_is_REFUSED_and_never_scans -- an unproven
  detector must not be allowed to report a quiet scan. A quiet scan from an instrument
  never shown able to fire is not evidence of no fires.

  test_a_repeated_fire_in_one_session_counts_once -- without dedupe a single incident
  inflates a counter that gates real build work, and the backlog fills with noise.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import detector_run as dr  # noqa: E402


def _scalar(value: str) -> str:
    """Emit a YAML double-quoted scalar exactly as apply_memory_verdicts.py does.

    Fixtures must produce the on-disk shape the real writer produces. Hand-writing
    `"\\berror\\b"` is not valid YAML escaping, and a fixture that writes something the
    writer never writes tests a file format nothing produces."""
    return yaml.safe_dump(value, default_style='"', width=10**9,
                          allow_unicode=True).rstrip("\n")


def rule(tmp_path: Path, name: str, *, sig=None, control=None, occ=1):
    lines = ["---", f"name: {name.removesuffix('.md')}", "metadata:", f"  occurrences: {occ}"]
    if sig is not None:
        lines.append(f'  detector_signature: {_scalar(sig)}')
    if control is not None:
        lines.append(f'  detector_control: {_scalar(control)}')
    lines += ["---", "", "Body."]
    (tmp_path / name).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- registry

def test_only_rules_with_a_signature_are_registered(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="an error")
    rule(tmp_path, "feedback_b.md")
    ds = dr.load_detectors(tmp_path)
    assert [d["name"] for d in ds] == ["feedback_a.md"]


def test_an_empty_signature_is_not_registered(tmp_path):
    rule(tmp_path, "feedback_a.md", sig="", control="x")
    assert dr.load_detectors(tmp_path) == []


def test_occurrences_is_carried_for_the_increment(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="an error", occ=3)
    assert dr.load_detectors(tmp_path)[0]["occurrences"] == 3


def test_a_malformed_occurrences_does_not_crash_the_registry(tmp_path):
    (tmp_path / "feedback_a.md").write_text(
        '---\nname: a\nmetadata:\n  occurrences: many\n'
        '  detector_signature: "\\\\berror\\\\b"\n  detector_control: "an error"\n---\nB',
        encoding="utf-8")
    assert dr.load_detectors(tmp_path)[0]["occurrences"] == 0


# ---------------------------------------------------------------- the safety property

def test_a_proven_detector_is_allowed_to_scan(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="an error happened")
    proven, refused = dr.validate(dr.load_detectors(tmp_path))
    assert len(proven) == 1 and refused == []


def test_a_detector_that_fails_its_control_is_REFUSED_and_never_scans(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bnever_matches\b", control="an error happened")
    proven, refused = dr.validate(dr.load_detectors(tmp_path))
    assert proven == []
    assert len(refused) == 1 and "does not fire" in refused[0]["why"]


def test_a_detector_with_no_control_is_REFUSED_not_trusted(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b")
    proven, refused = dr.validate(dr.load_detectors(tmp_path))
    assert proven == []
    assert "no detector_control" in refused[0]["why"], (
        "treating a missing control as nothing-to-check is how a guard becomes decorative"
    )


def test_an_uncompilable_regex_is_REFUSED_not_crashed(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"(?i)unclosed(", control="x")
    proven, refused = dr.validate(dr.load_detectors(tmp_path))
    assert proven == [] and "does not compile" in refused[0]["why"]


def test_a_refused_detector_makes_the_whole_run_not_ok(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bnever\b", control="an error")
    report = dr.scan(tmp_path, [])
    assert report["ok"] is False, (
        "a scan carrying a refused detector must not report as a clean run"
    )


def test_a_refused_detector_does_not_contribute_fires(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="NOPE")
    t = tmp_path / "s1.jsonl"
    t.write_text("an error occurred here", encoding="utf-8")
    report = dr.scan(tmp_path, [t])
    assert report["fires"] == [], (
        "the regex matches the transcript, but it was never proven, so it must not count"
    )


# ---------------------------------------------------------------- scanning + dedupe

def test_a_fire_is_found_and_carries_its_line(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="reported NOT FOUND")
    t = tmp_path / "s1.jsonl"
    t.write_text("line one\nthe probe reported NOT FOUND for the phrase\nline three",
                 encoding="utf-8")
    report = dr.scan(tmp_path, [t])
    assert len(report["fires"]) == 1
    assert "NOT FOUND" in report["fires"][0]["line"]
    assert report["fires"][0]["rule"] == "feedback_a.md"


def test_a_repeated_fire_in_one_session_counts_once(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="NOT FOUND")
    t = tmp_path / "s1.jsonl"
    t.write_text("NOT FOUND\nNOT FOUND\nNOT FOUND", encoding="utf-8")
    report = dr.scan(tmp_path, [t])
    assert len(report["fires"]) == 1, (
        "one incident echoed three times is one fire; counting three inflates a number "
        "that gates real build work"
    )


def test_distinct_lines_in_one_session_count_separately(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="NOT FOUND")
    t = tmp_path / "s1.jsonl"
    t.write_text("NOT FOUND for alpha\nNOT FOUND for beta", encoding="utf-8")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 2


def test_the_same_line_in_two_sessions_counts_twice(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="NOT FOUND")
    for n in ("s1.jsonl", "s2.jsonl"):
        (tmp_path / n).write_text("NOT FOUND", encoding="utf-8")
    files = [tmp_path / "s1.jsonl", tmp_path / "s2.jsonl"]
    assert len(dr.scan(tmp_path, files)["fires"]) == 2, (
        "dedupe is per session; the same failure recurring in a later session IS a repeat fire"
    )


def test_an_unreadable_transcript_is_skipped_not_fatal(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bx\b", control="x")
    report = dr.scan(tmp_path, [tmp_path / "absent.jsonl"])
    assert report["transcripts_scanned"] == 0


def test_an_empty_registry_reports_itself(tmp_path):
    report = dr.scan(tmp_path, [])
    assert report["registered"] == 0 and "note" in report


# ---------------------------------------------------------------- the increment

def test_verdict_rows_add_the_fire_count_to_the_existing_occurrences():
    fires = [{"rule": "feedback_a.md", "session": "s1", "line": "x", "occurrences_before": 1},
             {"rule": "feedback_a.md", "session": "s2", "line": "y", "occurrences_before": 1}]
    assert dr.verdict_rows(fires) == ["feedback_a.md\toccurrences=3"]


def test_verdict_rows_are_one_per_rule():
    fires = [{"rule": "feedback_a.md", "session": "s", "line": "x", "occurrences_before": 1},
             {"rule": "feedback_b.md", "session": "s", "line": "y", "occurrences_before": 4}]
    assert dr.verdict_rows(fires) == ["feedback_a.md\toccurrences=2",
                                      "feedback_b.md\toccurrences=5"]


def test_no_fires_yields_no_rows():
    assert dr.verdict_rows([]) == []


# ---------------------------------------------------------------- CLI

def test_main_exits_2_when_a_detector_was_refused(tmp_path, capsys):
    rule(tmp_path, "feedback_a.md", sig=r"\bnever\b", control="an error")
    assert dr.main(["--memory-dir", str(tmp_path)]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_main_exits_0_when_every_detector_is_proven(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="an error")
    assert dr.main(["--memory-dir", str(tmp_path)]) == 0


def test_main_rejects_a_missing_memory_dir(tmp_path, capsys):
    assert dr.main(["--memory-dir", str(tmp_path / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_apply_prints_verdict_rows(tmp_path, capsys):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="NOT FOUND", occ=1)
    t = tmp_path / "s1.jsonl"
    t.write_text("NOT FOUND here", encoding="utf-8")
    dr.main(["--memory-dir", str(tmp_path), "--transcripts", str(tmp_path), "--apply"])
    assert "feedback_a.md\toccurrences=2" in capsys.readouterr().out


def test_dry_run_is_the_default_and_prints_no_verdict_rows(tmp_path, capsys):
    rule(tmp_path, "feedback_a.md", sig=r"\bNOT FOUND\b", control="NOT FOUND")
    t = tmp_path / "s1.jsonl"
    t.write_text("NOT FOUND here", encoding="utf-8")
    dr.main(["--memory-dir", str(tmp_path), "--transcripts", str(tmp_path)])
    out = capsys.readouterr().out
    assert "occurrences=" not in out, "writing must require --apply"
    assert "fires 1" in out


def test_json_output_is_parseable(tmp_path, capsys):
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b", control="an error")
    dr.main(["--memory-dir", str(tmp_path), "--json"])
    assert json.loads(capsys.readouterr().out)["proven"] == 1


def test_parse_frontmatter_returns_empty_without_a_block():
    assert dr.parse_frontmatter("no frontmatter") == {}


def test_parse_frontmatter_returns_empty_on_an_unterminated_block():
    assert dr.parse_frontmatter("---\nname: a\nnever closed") == {}


# ---------------------------------------------------------------- mutation-driven gaps

def test_a_refusal_is_recorded_exactly_once(tmp_path):
    """Kills the mutants that drop the `continue` after a refusal: without it the detector
    falls through into the next check and gets refused a second time, double-counting the
    same defect and making the refusal list a misleading measure of how much is broken."""
    rule(tmp_path, "feedback_a.md", sig=r"\berror\b")
    proven, refused = dr.validate(dr.load_detectors(tmp_path))
    assert len(refused) == 1, f"one broken detector, one refusal; got {len(refused)}"


def test_an_uncompilable_regex_is_refused_exactly_once(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"(?i)unclosed(", control="x")
    _, refused = dr.validate(dr.load_detectors(tmp_path))
    assert len(refused) == 1
    assert "does not compile" in refused[0]["why"], (
        "the reason must name the real cause, not a downstream probe failure"
    )


def test_a_frontmatter_block_not_at_the_top_is_not_parsed():
    """Without the startswith guard, a mid-file --- fence is read as frontmatter and a
    detector_signature quoted in a rule's BODY would silently register as a live detector."""
    assert dr.parse_frontmatter('abc\ndetector_signature: x\n---\nbody') == {}


def test_an_empty_registry_reports_NOT_SCANNED_not_zero(tmp_path):
    """A zero that means 'never looked' must not render as a completed sweep of nothing.

    scan() early-returns when no rule carries a detector_signature. Reporting
    transcripts_scanned: 0 there is indistinguishable from a real sweep that found no
    fires, which is the false-clean shape this tool exists to refuse."""
    (tmp_path / "s1.jsonl").write_text("anything", encoding="utf-8")
    report = dr.scan(tmp_path, [tmp_path / "s1.jsonl"])
    assert report["registered"] == 0
    assert report["transcripts_scanned"] is None, "None means never looked; 0 would lie"
    assert report["transcripts_available"] == 1
    assert "NOTHING WAS SCANNED" in report["note"]


def test_the_empty_registry_summary_line_says_nothing_was_scanned(tmp_path, capsys):
    (tmp_path / "s1.jsonl").write_text("x", encoding="utf-8")
    dr.main(["--memory-dir", str(tmp_path), "--transcripts", str(tmp_path)])
    out = capsys.readouterr().out
    assert "NOTHING SCANNED" in out
    assert "1 transcripts available" in out


def test_an_oversized_line_is_skipped_and_counted_not_silently_dropped(tmp_path):
    """A single JSONL record can be hundreds of KB; 50 regexes over it backtracked into a
    two-minute timeout on the first live run. Skipping is right, but silence is not: a scan
    that quietly dropped its largest inputs looks identical to a clean one."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text("x" * (dr.MAX_LINE_CHARS + 10) + " NEEDLE\nNEEDLE on a small line",
                 encoding="utf-8")
    report = dr.scan(tmp_path, [t])
    assert report["lines_skipped_oversized"] == 1
    assert len(report["fires"]) == 1, "the small line still fires; the huge one is skipped"


def test_no_oversized_lines_reports_zero_skipped(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text("NEEDLE here", encoding="utf-8")
    assert dr.scan(tmp_path, [t])["lines_skipped_oversized"] == 0


def test_frontmatter_that_parses_to_a_non_dict_yields_empty():
    """A `---\\n- a\\n- b\\n---` block is valid YAML but a list. Without the isinstance
    guard the loop over .items() raises and the whole scan dies on one malformed file."""
    assert dr.parse_frontmatter("---\n- a\n- b\n---\nbody\n") == {}


def test_frontmatter_with_no_block_yields_a_real_empty_dict():
    out = dr.parse_frontmatter("no frontmatter at all")
    assert out == {} and isinstance(out, dict)


# ---------------------------------------------------------------- the unit of analysis
# A transcript line is a JSON envelope, not prose. Scanning raw lines produced 2,512
# "fires" whose top hits were queue-operation events and tool_use_id blobs.

def test_transcript_machinery_is_not_scanned(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "queue-operation", "content": "NEEDLE inside machinery"}),
                 encoding="utf-8")
    assert dr.scan(tmp_path, [t])["fires"] == [], (
        "a queue-operation envelope is transcript plumbing, not something anyone wrote"
    )


def test_assistant_text_blocks_are_scanned(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "I found a NEEDLE in the haystack"}]}}),
                 encoding="utf-8")
    fires = dr.scan(tmp_path, [t])["fires"]
    assert len(fires) == 1
    assert fires[0]["line"] == "I found a NEEDLE in the haystack", (
        "the fire must carry the authored text, not the JSON envelope"
    )


def test_a_match_only_in_the_envelope_metadata_does_not_fire(tmp_path):
    """The exact 2,512-fire defect: a regex hitting an id or a wrapper, not any prose."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "assistant", "promptId": "NEEDLE-1234",
                 "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "nothing relevant"}]}}),
                 encoding="utf-8")
    assert dr.scan(tmp_path, [t])["fires"] == []


def test_a_plain_string_content_is_scanned(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"role": "user",
                 "content": "look, a NEEDLE"}}), encoding="utf-8")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 1


def test_nested_tool_result_text_is_scanned(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "text", "text": "output: NEEDLE"}]}]}}),
        encoding="utf-8")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 1


def test_an_unparseable_record_falls_back_to_the_raw_line(tmp_path):
    """Under-reporting and calling it quiet is the failure this whole module refuses."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="a NEEDLE here")
    t = tmp_path / "s1.jsonl"
    t.write_text("this is not JSON but it mentions NEEDLE", encoding="utf-8")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 1


# ---------------------------------------------------------------- extractor edges

def test_a_json_scalar_record_falls_back_to_the_raw_line():
    assert list(dr.iter_text_units('"NEEDLE"')) == ['"NEEDLE"']


def test_a_summary_record_is_skipped():
    assert list(dr.iter_text_units(json.dumps({"type": "summary", "summary": "NEEDLE"}))) == []


def test_a_non_machinery_type_is_not_skipped():
    out = list(dr.iter_text_units(json.dumps(
        {"type": "assistant", "message": {"role": "a", "content": "NEEDLE"}})))
    assert out == ["NEEDLE"]


def test_non_dict_blocks_in_a_content_list_are_ignored():
    out = list(dr.iter_text_units(json.dumps(
        {"type": "assistant", "message": {"content": ["a bare string", {"text": "kept"}]}})))
    assert out == ["kept"]


def test_a_string_valued_inner_content_is_yielded():
    out = list(dr.iter_text_units(json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result",
                                                  "content": "raw output"}]}})))
    assert out == ["raw output"]


def test_non_dict_entries_inside_a_nested_content_list_are_ignored():
    out = list(dr.iter_text_units(json.dumps(
        {"type": "user", "message": {"content": [{"content": ["str", {"text": "kept"}]}]}})))
    assert out == ["kept"]


def test_a_content_value_that_is_neither_string_nor_list_yields_nothing():
    assert list(dr.iter_text_units(json.dumps(
        {"type": "assistant", "message": {"content": 42}}))) == []


def test_an_empty_text_unit_does_not_fire(tmp_path):
    """A regex able to match the empty string must not manufacture a fire per record."""
    rule(tmp_path, "feedback_a.md", sig=r"(?:NEEDLE)?", control="NEEDLE")
    t = tmp_path / "s1.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [{"text": ""}]}}),
                 encoding="utf-8")
    assert dr.scan(tmp_path, [t])["fires"] == []


def test_machinery_is_skipped_even_when_it_carries_a_message_field():
    """The earlier skip-test used a record with no `message`, so it yielded nothing either
    way and could not tell whether the guard did anything. This one can."""
    rec = {"type": "queue-operation", "message": {"content": [{"text": "NEEDLE"}]}}
    assert list(dr.iter_text_units(json.dumps(rec))) == []


def test_the_same_shape_IS_scanned_when_it_is_not_machinery():
    rec = {"type": "assistant", "message": {"content": [{"text": "NEEDLE"}]}}
    assert list(dr.iter_text_units(json.dumps(rec))) == ["NEEDLE"]


def test_unparseable_frontmatter_yields_a_real_dict():
    out = dr.parse_frontmatter("---\n  bad: [unclosed\n---\nbody\n")
    assert out == {} and isinstance(out, dict)


def test_non_dict_frontmatter_yields_a_real_dict():
    out = dr.parse_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert out == {} and isinstance(out, dict)


# ------------------------------------------------- the unit of analysis is a LINE
#
# Measured 2026-08-27 over 86 transcripts: matching against a whole authored text block
# scored a document as one fire because a common fragment appeared somewhere inside it.
# The top-firing rules had a median matched unit of 10-25 KB, and the evidence logged was
# the container's first 300 characters rather than the text the regex matched.

def _transcript(tmp_path: Path, name: str, text: str) -> Path:
    """One assistant record carrying `text` as its authored content."""
    rec = {"type": "assistant",
           "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    p = tmp_path / name
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return p


def test_a_match_is_confined_to_its_own_line(tmp_path):
    """The killer case: needle and a far-away neighbour must not merge into one unit."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "clean prose line\nNEEDLE on its own line\nmore prose")
    fires = dr.scan(tmp_path, [t])["fires"]
    assert len(fires) == 1
    assert fires[0]["line"] == "NEEDLE on its own line", (
        "the evidence must be the matched LINE, never the enclosing block")


def test_two_matching_lines_in_one_block_are_two_fires(tmp_path):
    """Document granularity collapsed these to one, hiding the real hit count."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "NEEDLE one\nfiller\nNEEDLE two")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 2


def test_the_matched_span_is_recorded_for_adjudication(tmp_path):
    """A detector is judged on WHAT it matched. Without the span there is nothing to judge."""
    rule(tmp_path, "feedback_a.md", sig=r"\d+%", control="up 12% today")
    t = _transcript(tmp_path, "s1.jsonl", "revenue rose 12% last quarter")
    assert dr.scan(tmp_path, [t])["fires"][0]["match"] == "12%"


def test_lines_scanned_is_reported_as_the_denominator(tmp_path):
    """A hit count without the lines it was drawn from is a bare numerator."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "one\ntwo\nNEEDLE")
    assert dr.scan(tmp_path, [t])["lines_scanned"] == 3


def test_a_line_over_the_scan_cap_is_counted_not_silently_dropped(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "x " * dr.MAX_SCAN_LINE_CHARS + "NEEDLE")
    report = dr.scan(tmp_path, [t])
    assert report["fires"] == []
    assert report["lines_over_scan_cap"] == 1, (
        "an unscanned line must never be indistinguishable from a clean one")


# ------------------------------------------------- the corpus must not fire its own detectors

def test_a_rule_file_read_into_a_session_does_not_fire_its_own_detector(tmp_path):
    """Otherwise every memory-hygiene session inflates every counter."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl",
                    "---\nname: feedback_a\nmetadata:\n  node_type: memory\n"
                    "  detector_control: NEEDLE here\n---\nNEEDLE in the body")
    assert dr.scan(tmp_path, [t])["fires"] == []


def test_a_detector_control_line_never_counts_as_a_fire(tmp_path):
    """Measured: name_the_scope fired on its own `detector_control:` line."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "detector_control: \"NEEDLE here\"")
    assert dr.scan(tmp_path, [t])["fires"] == []


def test_recalled_memory_in_a_system_reminder_does_not_fire(tmp_path):
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl",
                    "<system-reminder>NEEDLE was recalled</system-reminder>")
    assert dr.scan(tmp_path, [t])["fires"] == []


def test_authored_text_beside_a_system_reminder_still_fires(tmp_path):
    """The exclusion must be scoped to the block, or it silences real work."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl",
                    "<system-reminder>context</system-reminder>\nNEEDLE in real work")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 1


def test_ordinary_prose_mentioning_a_frontmatter_word_still_fires(tmp_path):
    """The corpus filter keys on `key:` at line start, not the bare word anywhere."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "the occurrences of NEEDLE are worth counting")
    assert len(dr.scan(tmp_path, [t])["fires"]) == 1


def test_is_corpus_text_needs_the_memory_marker(tmp_path):
    assert dr.is_corpus_text("metadata:\n  node_type: memory\n")
    assert not dr.is_corpus_text("we discussed node_type memory yesterday")


def test_strip_system_reminders_leaves_surrounding_text(tmp_path):
    out = dr.strip_system_reminders("before<system-reminder>gone</system-reminder>after")
    assert "gone" not in out and "before" in out and "after" in out


def test_the_line_granularity_fixture_detector_is_actually_PROVEN(tmp_path):
    """Guard against the false pass these tests were first written with.

    `sig=r"NEEDLE"` has no regex metacharacters, so `extract_patterns` yields nothing, the
    detector is REFUSED, and every assertion of the form `fires == []` goes green for a
    reason that has nothing to do with the behaviour under test. Four corpus-exclusion
    cases passed that way before this was caught. If the shared fixture ever stops being a
    real detector, this fails first and says so.
    """
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    report = dr.scan(tmp_path, [_transcript(tmp_path, "s1.jsonl", "nothing to see")])
    assert report["proven"] == 1 and report["refused"] == []


def test_a_blank_line_is_not_counted_in_the_denominator(tmp_path):
    """Blank lines would inflate `lines_scanned`, deflating every per-line rate computed
    from it -- a detector would look more restrained than it is."""
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    t = _transcript(tmp_path, "s1.jsonl", "one\n\n   \ntwo")
    assert dr.scan(tmp_path, [t])["lines_scanned"] == 2


def test_an_oversized_raw_record_is_not_scanned_at_all(tmp_path):
    """The catastrophic-backtracking guard: a huge JSONL record must be skipped before the
    regexes ever touch it, and must not contribute fires or denominator."""
    rec = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "x" * (dr.MAX_LINE_CHARS + 10) + "\nNEEDLE"}]}}
    p = tmp_path / "s1.jsonl"
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    rule(tmp_path, "feedback_a.md", sig=r"\bNEEDLE\b", control="NEEDLE here")
    report = dr.scan(tmp_path, [p])
    assert report["fires"] == [] and report["lines_scanned"] == 0
