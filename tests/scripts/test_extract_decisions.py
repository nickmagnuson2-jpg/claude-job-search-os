"""Tests for tools/extract_decisions.py.

The artifact this produces is EVIDENCE: an operator reads it and reacts to their own words.
So the failures worth pinning are not crashes, they are silent losses -- a source that
contributes nothing and reports success, a row with no query behind it, a window that
quietly widens to another engagement, an empty run that reads as "no decisions were made".

Run alone as well as in the suite, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import extract_decisions as ed  # noqa: E402
from extract_decisions import (  # noqa: E402
    extract_frame,
    extract_process_log,
    extract_transcripts,
    is_operator_turn,
    main,
    message_text,
    render_markdown,
)

yaml = pytest.importorskip("yaml")


FRAME = {
    "version": 12,
    "compression_ledger": [{"cut": "the scale slide", "reason": "Casey cut it", "at_version": 9}],
    "declines": [{"raised_by": "a reviewer", "objection": "too strong",
                  "date": "2026-09-16", "decided_by": "Casey"}],
    "exclusions": [{"element": "churn", "reason": "not in the file"}],
    "unknowns": {"u1": {"text": "who owns routing", "disposition": "ask the client",
                        "sensitivity": "low"}},
    "d1": {"note": "Casey said the rate stands in for all seven"},
}


@pytest.fixture
def frame(tmp_path):
    p = tmp_path / "frame.yaml"
    p.write_text(yaml.safe_dump(FRAME), encoding="utf-8")
    return p


# --- the frame ------------------------------------------------------------------------


def test_every_structured_section_contributes(frame):
    got = {e.eid for e in extract_frame(frame, "Casey")}
    assert {"CL1", "DEC1", "EX1", "UNK-u1"} <= got


def test_a_free_text_leaf_naming_the_operator_is_surfaced(frame):
    named = [e for e in extract_frame(frame, "Casey") if "named" in e.tags]
    assert [e.locator for e in named] == ["d1.note"]


def test_a_structured_row_is_not_also_reported_as_a_named_leaf(frame):
    """The compression ledger's reason names the operator too. Counting it twice would
    inflate every ratio taken over this ledger."""
    locs = [e.locator for e in extract_frame(frame, "Casey")]
    assert len(locs) == len(set(locs))
    assert not any(loc.startswith("compression_ledger") and "[" in loc.split("]")[-1]
                   for loc in locs if loc.count(".") > 0 and loc.startswith("compression"))


def test_a_different_operator_name_changes_what_is_found(frame):
    """The name is policy. Baking it in is how one engagement's extractor silently
    reports another's."""
    assert [e.locator for e in extract_frame(frame, "Jordan") if "named" in e.tags] == []


def test_every_frame_row_carries_the_query_that_found_it(frame):
    assert all(e.query for e in extract_frame(frame, "Casey"))


def test_a_decline_records_what_it_overruled(frame):
    dec, = [e for e in extract_frame(frame, "Casey") if e.eid == "DEC1"]
    assert dec.overrules == "a reviewer"


def test_a_decline_body_omits_the_bookkeeping_fields(frame):
    dec, = [e for e in extract_frame(frame, "Casey") if e.eid == "DEC1"]
    assert "OBJECTION" in dec.text
    assert "DECIDED_BY" not in dec.text


def test_a_non_dict_row_is_skipped_rather_than_crashing(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "compression_ledger": ["oops"]}),
                 encoding="utf-8")
    assert [e for e in extract_frame(p, "Casey") if e.eid.startswith("CL")] == []


def test_a_frame_with_no_sections_yields_nothing_rather_than_raising(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"version": 1}), encoding="utf-8")
    assert extract_frame(p, "Casey") == []


# --- the process log ------------------------------------------------------------------


LOG = """# log
| D1 | LibreOffice recalculates inside the build | openpyxl never evaluates |
| D2 | Verification runs first | it overrules the earlier ordering |
Casey: the denominator is the thing I keep getting wrong here
| not a decision row |
Casey: short
"""


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "log.md"
    p.write_text(LOG, encoding="utf-8")
    return p


def test_decision_rows_are_extracted_with_their_reason(log):
    d1, = [e for e in extract_process_log(log, "Casey") if e.eid == "D1"]
    assert "LibreOffice" in d1.text and "openpyxl" in d1.text


def test_a_row_naming_an_overrule_is_tagged(log):
    d2, = [e for e in extract_process_log(log, "Casey") if e.eid == "D2"]
    assert "overrule" in d2.tags


def test_a_row_not_naming_an_overrule_is_not_tagged(log):
    d1, = [e for e in extract_process_log(log, "Casey") if e.eid == "D1"]
    assert "overrule" not in d1.tags


def test_a_quoted_passage_is_captured(log):
    quotes = [e for e in extract_process_log(log, "Casey") if "quote" in e.tags]
    assert len(quotes) == 1
    assert "denominator" in quotes[0].text


def test_a_short_quote_is_dropped_as_an_acknowledgement(log):
    assert all("short" not in e.text for e in extract_process_log(log, "Casey"))


def test_every_row_carries_a_line_locator(log):
    assert all(":" in e.locator for e in extract_process_log(log, "Casey"))


def test_a_missing_process_log_yields_nothing_rather_than_raising(tmp_path):
    assert extract_process_log(tmp_path / "absent.md", "Casey") == []


# --- the transcripts ------------------------------------------------------------------


def _session(tmp_path, name, turns, when=date(2026, 9, 16)):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(t) for t in turns), encoding="utf-8")
    ts = datetime(when.year, when.month, when.day, 12).timestamp()
    import os

    os.utime(p, (ts, ts))
    return p


def _turn(text, role="user", typ="user"):
    return {"type": typ, "message": {"role": role, "content": [{"type": "text",
                                                                "text": text}]},
            "timestamp": "2026-09-16T10:00:00Z"}


LONG = "this is a real instruction about the acme engagement and it is long enough"


def test_a_user_turn_in_window_is_extracted(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG)])
    got = extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))
    assert len(got) == 1 and got[0].text == LONG


def test_a_file_outside_the_window_is_not_read(tmp_path):
    """The window is what keeps one engagement's ledger out of another's."""
    _session(tmp_path, "a.jsonl", [_turn(LONG)], when=date(2026, 8, 1))
    assert extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20)) == []


def test_a_file_not_mentioning_the_needle_is_skipped(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn("a long instruction about something else entirely")])
    assert extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20)) == []


def test_the_needle_matches_case_insensitively(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG.replace("acme", "ACME"))])
    assert len(extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))) == 1


def test_an_assistant_turn_is_not_the_operator(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG, role="assistant", typ="assistant")])
    assert extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20)) == []


def test_a_tool_result_carrying_the_user_type_is_not_an_utterance(tmp_path):
    """type=user with role!=user is how tool results arrive. Counting them as the
    operator's words puts machinery in a ledger of decisions."""
    _session(tmp_path, "a.jsonl", [_turn(LONG, role="tool")])
    assert extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20)) == []


def test_a_repeated_turn_is_deduped(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG), _turn(LONG)])
    assert len(extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))) == 1


def test_a_malformed_line_does_not_abort_the_file(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text("{not json\n" + json.dumps(_turn(LONG)), encoding="utf-8")
    import os

    ts = datetime(2026, 9, 16, 12).timestamp()
    os.utime(p, (ts, ts))
    assert len(extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))) == 1


def test_a_missing_transcript_dir_yields_nothing(tmp_path):
    assert extract_transcripts(tmp_path / "absent", "acme", date(2026, 9, 14),
                               date(2026, 9, 20)) == []


def test_every_transcript_row_states_its_window_and_needle(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG)])
    got, = extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))
    assert "2026-09-14" in got.query and "acme" in got.query


# --- turn filters ---------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ed.NOISE_PREFIXES)
def test_machinery_is_not_an_operator_turn(prefix):
    assert is_operator_turn(prefix + " " + LONG) is False


def test_a_short_turn_is_not_a_decision():
    assert is_operator_turn("yes") is False


def test_a_long_turn_is():
    assert is_operator_turn(LONG) is True


def test_message_text_joins_only_text_blocks():
    content = [{"type": "text", "text": "a"}, {"type": "image", "source": {}},
               {"type": "text", "text": "b"}]
    assert message_text(content) == "a\nb"


def test_message_text_accepts_a_bare_string():
    assert message_text("hello") == "hello"


def test_message_text_of_an_unknown_shape_is_empty_not_a_crash():
    assert message_text({"unexpected": True}) == ""


# --- rendering and the CLI ------------------------------------------------------------


def test_the_rendered_coverage_table_states_every_query():
    md = render_markdown([], {"frame": (3, "all entries")}, "T")
    assert "| frame | 3 | all entries |" in md


def test_the_render_quotes_text_verbatim_across_newlines():
    e = ed.Entry(eid="X", source="frame", locator="l", text="line one\nline two")
    md = render_markdown([e], {}, "T")
    assert "> line one\n> line two" in md


def test_the_render_says_it_is_a_ledger_not_an_argument():
    assert "not an argument" in render_markdown([], {}, "T")


def test_cli_writes_both_artifacts_and_exits_zero(frame, log, tmp_path):
    out, js = tmp_path / "o.md", tmp_path / "o.json"
    rc = main(["--frame", str(frame), "--process-log", str(log), "--operator", "Casey", "--needle", "acme",
               "--out", str(out), "--json", str(js)])
    assert rc == 0
    assert "CL1" in out.read_text()
    assert any(e["eid"] == "D1" for e in json.loads(js.read_text()))


def test_cli_refuses_an_unbounded_transcript_sweep(frame, tmp_path):
    """Without a window this collects every session on the machine, which is how one
    engagement's ledger fills with another's decisions."""
    with pytest.raises(SystemExit):
        main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme",
              "--transcript-dir", str(tmp_path)])


def test_cli_refuses_an_inverted_window(frame, tmp_path):
    with pytest.raises(SystemExit):
        main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme", "--transcript-dir", str(tmp_path),
              "--window-start", "2026-09-20", "--window-end", "2026-09-14"])


def test_an_empty_ledger_exits_non_zero_and_says_it_is_not_evidence(tmp_path, capsys):
    """"No decisions found" must never be readable as "no decisions were made"."""
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"version": 1}), encoding="utf-8")
    assert main(["--frame", str(p), "--operator", "Casey", "--needle", "acme"]) == 2
    assert "not evidence" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Survivor kills. Each of these targets a branch whose silent loss makes the
# ledger wrong in a way a reader cannot see: a row counted twice, a row lost,
# a provenance line dropped, or a count printed that nothing produced.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("section", ["compression_ledger", "declines", "exclusions"])
def test_a_non_dict_row_in_any_section_is_skipped(tmp_path, section):
    """A hand-edited frame can put a bare string in a list. Crashing loses the whole
    ledger; processing it puts a fragment in the record."""
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"version": 1, section: ["oops", {"cut": "c", "reason": "r",
                                                                 "element": "e",
                                                                 "raised_by": "x"}]}),
                 encoding="utf-8")
    got = extract_frame(p, "Casey")
    assert len(got) == 1, "exactly the well-formed row should survive"


def test_a_non_dict_unknown_is_skipped(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "unknowns": {"u1": "oops"}}), encoding="utf-8")
    assert extract_frame(p, "Casey") == []


def test_a_decision_row_that_also_names_the_operator_is_counted_once(tmp_path):
    """Without the continue after a D-row, the same line is emitted twice: once as D##
    and once as a quote. A ledger with duplicate rows has a wrong denominator."""
    p = tmp_path / "log.md"
    p.write_text("| D9 | Casey: the rate stands in for all seven and that is the call |\n",
                 encoding="utf-8")
    got = extract_process_log(p, "Casey")
    assert len(got) == 1 and got[0].eid == "D9"


def test_a_comma_form_of_the_operator_name_is_captured(tmp_path):
    p = tmp_path / "log.md"
    p.write_text("Casey, asked for the like-for-like number rather than the headline\n",
                 encoding="utf-8")
    assert len(extract_process_log(p, "Casey")) == 1


def test_a_line_naming_nobody_is_not_captured(tmp_path):
    p = tmp_path / "log.md"
    p.write_text("a long line of narrative prose that names no operator at all here\n",
                 encoding="utf-8")
    assert extract_process_log(p, "Casey") == []


def test_message_text_of_a_string_is_not_routed_through_the_list_branch():
    """A bare string is iterable. Treating it as a block list yields nothing at all."""
    assert message_text("a decision stated as a plain string") == \
        "a decision stated as a plain string"


def test_an_assistant_turn_between_two_user_turns_does_not_break_the_file(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn(LONG), _turn(LONG + " two", role="assistant",
                                                      typ="assistant"),
                                   _turn(LONG + " three")])
    got = extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))
    assert len(got) == 2


def test_a_machinery_turn_among_real_ones_is_the_only_one_dropped(tmp_path):
    _session(tmp_path, "a.jsonl", [_turn("<system-reminder> " + LONG), _turn(LONG + " real")])
    got = extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))
    assert len(got) == 1 and "real" in got[0].text


def test_the_render_emits_a_section_per_source_and_skips_empty_ones():
    e = ed.Entry(eid="X", source="frame", locator="l", text="t")
    md = render_markdown([e], {}, "T")
    assert "The frame" in md
    assert "The session transcripts" not in md, "an empty source must not get a heading"


def test_the_render_prints_the_locator_line_for_provenance():
    e = ed.Entry(eid="X", source="frame", locator="declines[0]", text="t", when="v12")
    assert "`v12 · declines[0]`" in render_markdown([e], {}, "T")


def test_the_render_omits_the_locator_line_when_there_is_none():
    e = ed.Entry(eid="X", source="frame", locator="", text="t")
    md = render_markdown([e], {}, "T")
    assert "``" not in md


def test_an_overrule_is_surfaced_in_the_render():
    e = ed.Entry(eid="X", source="frame", locator="l", text="t", overrules="a reviewer")
    assert "**Overruled:** a reviewer" in render_markdown([e], {}, "T")


def test_an_absent_overrule_prints_no_overruled_line():
    e = ed.Entry(eid="X", source="frame", locator="l", text="t")
    assert "Overruled" not in render_markdown([e], {}, "T")


def test_an_essay_length_overrule_is_not_inlined():
    """The field sometimes holds a paragraph. Inlining it buries the row it belongs to."""
    e = ed.Entry(eid="X", source="frame", locator="l", text="t", overrules="x" * 500)
    assert "Overruled" not in render_markdown([e], {}, "T")


def test_cli_prints_a_count_per_source_and_a_total(frame, log, tmp_path, capsys):
    main(["--frame", str(frame), "--process-log", str(log), "--operator", "Casey", "--needle", "acme"])
    out = capsys.readouterr().out
    assert "frame" in out and "processlog" in out and "TOTAL" in out


def test_cli_names_each_file_it_wrote(frame, tmp_path, capsys):
    out = tmp_path / "o.md"
    main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme", "--out", str(out)])
    printed = capsys.readouterr().out
    assert f"wrote {out}" in printed


def test_cli_writing_nothing_names_no_file(frame, capsys):
    main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme"])
    assert "wrote" not in capsys.readouterr().out


def test_cli_reads_transcripts_when_a_window_is_given(frame, tmp_path, capsys):
    _session(tmp_path, "a.jsonl", [_turn(LONG)])
    main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme", "--transcript-dir", str(tmp_path),
          "--window-start", "2026-09-14", "--window-end", "2026-09-20"])
    assert "transcript" in capsys.readouterr().out


def test_a_window_bound_must_be_a_real_date(frame, tmp_path):
    with pytest.raises(SystemExit):
        main(["--frame", str(frame), "--operator", "Casey", "--needle", "acme", "--transcript-dir", str(tmp_path),
              "--window-start", "not-a-date", "--window-end", "2026-09-20"])


def test_a_summary_record_carrying_a_user_role_is_not_an_utterance(tmp_path):
    """Compaction writes records whose `type` is not "user" while the nested message still
    says role=user. Only the type check separates those from real turns."""
    rec = _turn(LONG)
    rec["type"] = "summary"
    _session(tmp_path, "a.jsonl", [rec, _turn(LONG + " real")])
    got = extract_transcripts(tmp_path, "acme", date(2026, 9, 14), date(2026, 9, 20))
    assert len(got) == 1 and "real" in got[0].text


def test_the_coverage_table_is_separated_from_the_first_section(tmp_path):
    """Without the blank line the table runs into the heading and no markdown renderer
    draws either one."""
    e = ed.Entry(eid="X", source="frame", locator="l", text="t")
    md = render_markdown([e], {"frame": (1, "all entries")}, "T")
    assert "| frame | 1 | all entries |\n\n## " in md


def test_the_operator_has_no_default(frame):
    """A generic tool defaulting to one person's name returns a NON-EMPTY ledger silently
    missing every record naming the real operator, which reads as a successful run."""
    with pytest.raises(SystemExit):
        main(["--frame", str(frame), "--needle", "acme"])
