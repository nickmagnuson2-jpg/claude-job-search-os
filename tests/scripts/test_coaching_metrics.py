"""Tests for tools/coaching_metrics.py — longitudinal interview-performance metrics."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import coaching_metrics as cm  # noqa: E402

HDR = ("| Date | Call | Format | Dim 1 | Dim 2 | Dim 3 | Dim 4 | Dim 5 "
       "| Overall | Int. Signal |")
SEP = "|---|---|---|---|---|---|---|---|---|---|"


def row(date, call="Acme / Someone (screen)", fmt="unstructured-chat (recruiter)",
        d1="3", d2="3", d3="3", d4="3", d5="3", overall="3.0/5",
        signal="high (Nick) / high (Claude)"):
    return f"| {date} | {call} | {fmt} | {d1} | {d2} | {d3} | {d4} | {d5} | {overall} | {signal} |"


def doc(*rows):
    return "\n".join(["# Summary", "", "## Per-Dimension Trend", "", HDR, SEP, *rows]) + "\n"


# --- num ---

@pytest.mark.parametrize("cell,expected", [
    ("3.8/5", 3.8), ("4", 4.0), ("4.5", 4.5), ("~3.5/5 (clean non-fit)", 3.5),
    ("n/a", None), ("", None), ("-", None), (None, None),
])
def test_num_extracts_first_number_or_none(cell, expected):
    assert cm.num(cell) == expected


# --- parse_trend ---

def test_parse_trend_reads_only_iso_dated_rows():
    content = doc(row("2026-05-01"), "| Not a date | x | y | 1 | 1 | 1 | 1 | 1 | 1 | z |",
                  row("2026-06-01"))
    assert [r["date"] for r in cm.parse_trend(content)] == ["2026-05-01", "2026-06-01"]


def test_parse_trend_sorts_oldest_first_regardless_of_file_order():
    content = doc(row("2026-08-01"), row("2026-04-01"), row("2026-06-01"))
    assert [r["date"] for r in cm.parse_trend(content)] == [
        "2026-04-01", "2026-06-01", "2026-08-01"]


def test_parse_trend_ignores_tables_with_too_few_columns():
    content = doc(row("2026-05-01")) + "\n| 2026-05-02 | short | row |\n"
    assert len(cm.parse_trend(content)) == 1


def test_parse_trend_strips_parentheticals_from_call_and_format():
    r = cm.parse_trend(doc(row("2026-05-01", call="Acme / Jane (CEO screen)",
                              fmt="founder-vibe-check (founder-meet, TP2)")))[0]
    assert r["call"] == "Acme"
    assert r["format"] == "founder-vibe-check"


def test_parse_trend_flags_drills():
    rows = cm.parse_trend(doc(row("2026-05-01", fmt="drill"),
                              row("2026-05-02", fmt="unstructured-chat")))
    assert [r["is_drill"] for r in rows] == [True, False]


def test_parse_trend_maps_na_dimension_to_none():
    r = cm.parse_trend(doc(row("2026-05-01", d3="n/a")))[0]
    assert r["scores"]["star_quality"] is None
    assert r["scores"]["format_resilience"] == 3.0


# --- half_split ---

def test_half_split_returns_none_below_four_points():
    assert cm.half_split([("2026-01-01", 1.0)] * 3) is None


def test_half_split_computes_chronological_early_vs_late():
    s = [("2026-01-01", 1.0), ("2026-02-01", 1.0), ("2026-03-01", 3.0), ("2026-04-01", 3.0)]
    out = cm.half_split(s)
    assert out == {"n": 4, "early": 1.0, "late": 3.0, "delta": 2.0}


def test_half_split_delta_is_negative_when_performance_declines():
    s = [("2026-01-01", 4.0), ("2026-02-01", 4.0), ("2026-03-01", 2.0), ("2026-04-01", 2.0)]
    assert cm.half_split(s)["delta"] == -2.0


def test_half_split_odd_length_puts_middle_point_in_the_late_half():
    s = [("2026-01-01", 0.0), ("2026-02-01", 0.0), ("2026-03-01", 3.0)]
    s.append(("2026-04-01", 3.0))
    s.append(("2026-05-01", 3.0))
    out = cm.half_split(s)
    assert out["n"] == 5 and out["early"] == 0.0 and out["late"] == 3.0


# --- calibration ---

def test_calibration_counts_agreement_and_disagreement():
    content = doc(
        row("2026-05-01", signal="high (Nick) / high (Claude)"),
        row("2026-05-02", signal="med (Nick) / high (Claude)"),
        row("2026-05-03", signal="low (Nick) / low (Claude)"),
    )
    out = cm.calibration(cm.parse_trend(content))
    assert out == {"scored_both": 3, "agree": 2, "disagree": 1, "agreement_pct": 67}


def test_calibration_skips_rows_without_both_reads():
    content = doc(row("2026-05-01", signal="high (Nick)"),
                  row("2026-05-02", signal="warm relationship"))
    out = cm.calibration(cm.parse_trend(content))
    assert out["scored_both"] == 0 and out["agreement_pct"] is None


# --- floors ---

def test_floors_count_scores_at_or_below_three_inclusive():
    """Fixture is deliberately ASYMMETRIC: 3 at-or-below, 1 above. A comparison
    flipped to `> 3.0` yields 1, not 3, so the direction of the test is load-bearing.
    An earlier symmetric fixture (3/2/3.5/4) returned 2 either way and proved nothing."""
    content = doc(row("2026-05-01", d2="3"), row("2026-05-02", d2="2"),
                  row("2026-05-03", d2="2.5"), row("2026-05-04", d2="4"))
    f = cm.compute(content)["floors"]["delivery_crispness"]
    assert f["n"] == 4
    assert f["at_or_below_3"] == 3, "3.0 itself must count as at-or-below"
    assert f["max"] == 4.0


def test_floors_treats_exactly_three_as_below_not_above():
    content = doc(*[row(f"2026-05-0{i}", d2="3") for i in range(1, 5)])
    assert cm.compute(content)["floors"]["delivery_crispness"]["at_or_below_3"] == 4


def test_overall_trend_skips_rows_with_no_overall_score():
    """Guards `r["overall"] is not None`: inverting it drops every real score."""
    rows = [row(f"2026-05-0{i}", overall="4.0/5") for i in range(1, 5)]
    rows.append(row("2026-05-05", overall="n/a"))
    t = cm.compute(doc(*rows))["trends"]["overall"]
    assert t is not None
    assert t["n"] == 4, "the n/a row must be excluded, and the scored rows kept"
    assert t["late"] == 4.0


def test_by_format_means_are_scoped_to_their_own_format():
    """Guards `r["format"] == fmt`: inverting it averages every OTHER format's scores."""
    content = doc(
        row("2026-05-01", fmt="unstructured-chat", overall="2.0/5"),
        row("2026-05-02", fmt="unstructured-chat", overall="2.0/5"),
        row("2026-05-03", fmt="founder-vibe-check", overall="5.0/5"),
        row("2026-05-04", fmt="founder-vibe-check", overall="5.0/5"),
    )
    bf = cm.compute(content)["by_format"]
    assert bf["unstructured-chat"] == {"n": 2, "mean_overall": 2.0}
    assert bf["founder-vibe-check"] == {"n": 2, "mean_overall": 5.0}


def test_parse_trend_ignores_a_dated_line_that_is_not_a_table_row():
    """Guards the `startswith("|")` gate: prose carrying enough pipes to survive the
    column check must still be rejected, or narrative text becomes a scored session."""
    content = doc(row("2026-05-01"))
    content += "2026-05-02 | note | a | 1 | 1 | 1 | 1 | 1 | 1 | x |\n"
    assert [r["date"] for r in cm.parse_trend(content)] == ["2026-05-01"]


# --- compute / main ---

def test_compute_reports_drills_separately_from_real_calls():
    content = doc(row("2026-05-01", fmt="drill"), row("2026-05-02"), row("2026-05-03"))
    s = cm.compute(content)["sessions"]
    assert s == {"scored_rows": 3, "real": 2, "drills": 1,
                 "date_range": ["2026-05-01", "2026-05-03"]}


def test_compute_always_emits_caveats():
    out = cm.compute(doc(row("2026-05-01")))
    assert out["caveats"], "caveats must ship with the numbers"
    assert any(cm.RUBRIC_LOCKED in c for c in out["caveats"])


def test_main_refuses_a_suspiciously_small_parse(tmp_path, capsys):
    p = tmp_path / "coaching" / "progress"
    p.mkdir(parents=True)
    (p / "_summary.md").write_text(doc(row("2026-05-01")), encoding="utf-8")
    assert cm.main(["--repo-root", str(tmp_path)]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_main_succeeds_at_the_threshold(tmp_path, capsys):
    p = tmp_path / "coaching" / "progress"
    p.mkdir(parents=True)
    rows = [row(f"2026-05-0{i}") for i in range(1, 6)]
    (p / "_summary.md").write_text(doc(*rows), encoding="utf-8")
    assert cm.main(["--repo-root", str(tmp_path)]) == 0


def test_main_prints_json_to_stdout_when_no_out_file(tmp_path, capsys):
    """stdout IS the tool's interface when --out is absent; a silent success is a bug."""
    p = tmp_path / "coaching" / "progress"
    p.mkdir(parents=True)
    (p / "_summary.md").write_text(doc(*[row(f"2026-05-0{i}") for i in range(1, 6)]),
                                   encoding="utf-8")
    assert cm.main(["--repo-root", str(tmp_path)]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["sessions"]["scored_rows"] == 5


def test_main_errors_when_summary_missing(tmp_path, capsys):
    assert cm.main(["--repo-root", str(tmp_path)]) == 1
    assert "not found" in capsys.readouterr().err


def test_main_writes_out_file_when_asked(tmp_path):
    p = tmp_path / "coaching" / "progress"
    p.mkdir(parents=True)
    (p / "_summary.md").write_text(doc(*[row(f"2026-05-0{i}") for i in range(1, 6)]),
                                   encoding="utf-8")
    dest = tmp_path / "m.json"
    assert cm.main(["--repo-root", str(tmp_path), "--out", str(dest)]) == 0
    assert dest.exists() and '"scored_rows": 5' in dest.read_text()


# --- live file ---

def test_live_summary_still_parses_and_finds_the_delivery_floor():
    """Guards the real file's shape. Fixtures cannot catch a table reorder upstream."""
    src = REPO / "coaching" / "progress" / "_summary.md"
    if not src.exists():
        pytest.skip("live summary not present")
    out = cm.compute(src.read_text(encoding="utf-8"))
    assert out["sessions"]["scored_rows"] >= 20
    assert out["trends"]["delivery_crispness"] is not None


def test_main_reports_the_session_count_when_writing_out(tmp_path, capsys):
    """Kills the DROP_CALL mutant on the --out confirmation print."""
    p = tmp_path / "coaching" / "progress"
    p.mkdir(parents=True)
    (p / "_summary.md").write_text(doc(*[row(f"2026-05-0{i}") for i in range(1, 6)]),
                                   encoding="utf-8")
    dest = tmp_path / "m.json"
    assert cm.main(["--repo-root", str(tmp_path), "--out", str(dest)]) == 0
    assert "5 scored sessions" in capsys.readouterr().out


# --- ledger mode: coaching/hypotheses.md test logs ---
#
# Every fixture below is invented. The defect these guard is a classifier that
# searches the whole row for a word that belongs to one column.

H_A = "| Date | Call | Format | Drill state | Pred | Obs | Verdict | Note |"
H_B = "| Date | Call | Format | Pred | Obs | Verdict | Note |"


def ledger_doc(*sections):
    """sections: (heading, header, [rows]). Returns (content, {row_text: 1-based line})."""
    lines = ["# Registry", ""]
    for heading, header, rows in sections:
        lines += [heading, "", "### Test log (append-only)", header,
                  "|" + "---|" * (header.count("|") - 1), *rows, ""]
    return "\n".join(lines) + "\n", {l: i for i, l in enumerate(lines, 1)}


def only(content, hyp="H9"):
    return cm.compute_ledger(content)["hypotheses"][hyp]


def test_ledger_real_row_whose_drill_state_cell_says_drill_is_real():
    """The fire-3 row: the Drill state column records that a drill preceded a REAL
    call. A whole-row match on 'drill' files it as a drill."""
    r = ("| 2026-01-05 | Acme / Jane Doe | unstructured-chat (recruiter) "
         "| drill RAN 20 min before | low | low | **Support** | the drill helped |")
    content, _ = ledger_doc(("## H9: Invented", H_A, [r]))
    assert "drill" in r.lower()
    h = only(content)
    assert h["real"]["n"] == 1 and h["drill"]["n"] == 0


def test_ledger_drill_is_read_from_the_format_cell():
    rows = [
        "| 2026-01-05 | Acme sim | drill | controlled | low | low | Support | x |",
        "| 2026-01-06 | Acme sim 2 | technical-deep-dive drill | n/a | low | low | Support | x |",
        "| 2026-01-07 | Acme sim 3 | drill (voice, degraded) | n/a | low | low | Refute | x |",
        "| 2026-01-08 | Globex / Sam Roe | peer-screen | skipped | hi | hi | Support | x |",
    ]
    h = only(ledger_doc(("## H9: Invented", H_A, rows))[0])
    assert h["drill"]["n"] == 3 and h["real"]["n"] == 1
    assert h["drill"]["verdicts"] == {"Support": 2, "Refute": 1}


def test_ledger_call_cell_mentioning_drill_does_not_make_the_row_a_drill():
    r = ("| 2026-01-05 | Acme / Jane Doe (post-drill, real screen) | peer-screen "
         "| skipped | hi | hi | Support | x |")
    h = only(ledger_doc(("## H9: Invented", H_A, [r]))[0])
    assert h["real"]["n"] == 1 and h["drill"]["n"] == 0


def test_ledger_drill_inside_a_format_parenthetical_is_a_qualifier_not_the_format():
    r = ("| 2026-01-05 | Acme / Jane Doe | unstructured-chat (drill ran that morning) "
         "| ran | hi | hi | Support | x |")
    h = only(ledger_doc(("## H9: Invented", H_A, [r]))[0])
    assert h["real"]["n"] == 1 and h["drill"]["n"] == 0


def test_ledger_verdict_comes_from_the_verdict_column_not_the_note():
    """The second fire-3 defect: a right-to-left or whole-row scan finds the Note's
    verdict word first and flips the tally."""
    rows = [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | **Support** "
        "| would be a Refute if the timing differed |",
        "| 2026-01-06 | Globex / Sam Roe | peer-screen | hi | lo | **Refute (mechanism)** "
        "| earlier rows were Support |",
        "| 2026-01-07 | Initech / Lee Poe | peer-screen | hi | lo "
        "| **Inconclusive (leans Refute)** | not Support |",
    ]
    h = only(ledger_doc(("## H9: Invented", H_B, rows))[0])
    assert h["real"]["verdicts"] == {"Support": 1, "Refute": 1, "Inconclusive": 1}


def test_ledger_verdict_is_the_leading_label_of_its_cell():
    """'Soft-miss (not clean Support)' is not a Support; 'Not tested' is not a verdict."""
    rows = [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi "
        "| **Soft-miss (not clean Support)** | x |",
        "| 2026-01-06 | Acme sim | drill | n/a | n/a | **Not tested — logged** | x |",
    ]
    out = cm.compute_ledger(ledger_doc(("## H9: Invented", H_B, rows))[0])
    h = out["hypotheses"]["H9"]
    assert h["real"]["verdicts"] == {"Other": 1}
    assert h["drill"]["verdicts"] == {"Other": 1}
    assert sorted(o["label"] for o in out["other_verdicts"]) == ["Not tested", "Soft-miss"]


def test_ledger_narrow_row_reads_verdict_from_the_right_and_format_from_the_left():
    """One cell short (Pred and Obs merged). Verdict sits one index left of where the
    header puts it; Format has not moved."""
    r = "| 2026-01-05 | Acme sim | drill | controlled | low, as predicted | Refute | Support was expected |"
    content, at = ledger_doc(("## H9: Invented", H_A, [r]))
    out = cm.compute_ledger(content)
    h = out["hypotheses"]["H9"]
    assert h["drill"] == {"n": 1, "verdicts": {"Refute": 1}}
    assert out["malformed_rows"] == [
        {"line": at[r], "hypothesis": "H9", "cells": 7, "header_cells": 8}]


def test_ledger_narrow_row_missing_its_format_cell_is_unclassified_not_guessed():
    """The Format cell itself is the missing one, so index 2 holds the Drill state
    text. Reading it by index would file a real call as a drill."""
    r = ("| 2026-01-05 | Acme onsite | drill ran the night before, reset unconfirmed "
         "| elevated | dim2 = 3 | **Inconclusive** | x |")
    content, at = ledger_doc(("## H9: Invented", H_A, [r]))
    out = cm.compute_ledger(content)
    h = out["hypotheses"]["H9"]
    assert h["drill"]["n"] == 0 and h["real"]["n"] == 0
    assert h["unclassified"] == {"n": 1, "verdicts": {"Inconclusive": 1}}
    assert [u["line"] for u in out["unclassified_rows"]] == [at[r]]


def test_ledger_narrow_row_with_a_qualified_drill_format_is_still_a_drill():
    r = ("| 2026-01-05 | Acme sim | **CONTROLLED: drill (self-monitored) + one topic** "
         "| n/a | LOW | **Support (controlled)** | not a real call |")
    h = only(ledger_doc(("## H9: Invented", H_A, [r]))[0])
    assert h["drill"] == {"n": 1, "verdicts": {"Support": 1}}


def test_ledger_narrow_row_whose_verdict_slot_holds_no_verdict_is_unparsed():
    """Missing the trailing Note: right-alignment lands on Obs. Report, never search."""
    r = "| 2026-01-05 | Acme / Jane Doe | peer-screen | skipped | hi | Support seemed likely | Refute |"
    content, at = ledger_doc(("## H9: Invented", H_A, [r]))
    out = cm.compute_ledger(content)
    assert out["hypotheses"]["H9"]["real"]["verdicts"] == {"Unparsed": 1}
    assert [u["line"] for u in out["unparsed_verdicts"]] == [at[r]]


def test_ledger_corrective_rows_are_not_counted_as_tests():
    rows = [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | ran | lo | lo | **Support** | x |",
        "| 2026-01-05 | **CORRECTIVE ROW to the entry above** | peer-screen | same | same "
        "| lo | **Support (downgraded)** | x |",
        "| 2026-01-05 | **SECOND CORRECTIVE ROW (append-only)** | peer-screen | same | same "
        "| lo | **Support (unchanged)** | x |",
    ]
    h = only(ledger_doc(("## H9: Invented", H_A, rows))[0])
    assert h["rows"] == 3
    assert h["real"] == {"n": 1, "verdicts": {"Support": 1}}
    assert h["corrective"]["n"] == 2


def test_ledger_keeps_hypotheses_apart_and_reads_each_tables_own_header():
    """Verdict is index 6 under H_A and index 5 under H_B. One fixed index misreads one."""
    a = ["| 2026-01-05 | Acme / Jane Doe | peer-screen | ran | Support | lo | Refute | x |"]
    b = ["| 2026-01-06 | Globex / Sam Roe | drill | hi | hi | Support | x |",
         "| 2026-01-07 | Globex / Sam Roe | peer-screen | hi | hi | Inconclusive | x |"]
    out = cm.compute_ledger(ledger_doc(("## H8a: First", H_A, a), ("## H9: Second", H_B, b))[0])
    assert out["hypotheses"]["H8a"]["real"] == {"n": 1, "verdicts": {"Refute": 1}}
    assert out["hypotheses"]["H9"]["drill"] == {"n": 1, "verdicts": {"Support": 1}}
    assert out["hypotheses"]["H9"]["real"] == {"n": 1, "verdicts": {"Inconclusive": 1}}
    assert out["totals"] == {"rows": 3, "real": 2, "drill": 1, "corrective": 0,
                             "unclassified": 0}


def test_ledger_ignores_tables_outside_a_hypothesis_or_without_the_named_columns():
    content, _ = ledger_doc(("## H9: Invented", H_B, [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | Support | x |"]))
    content += ("\n## Backlog\n\n| Date | Call | Format | Pred | Obs | Verdict | Note |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 2026-01-09 | Acme | drill | a | b | Refute | c |\n"
                "\n## H7: No verdict column\n\n| Date | Call | Format | Note |\n|---|---|---|---|\n"
                "| 2026-01-09 | Acme | drill | Refute |\n")
    out = cm.compute_ledger(content)
    assert list(out["hypotheses"]) == ["H9"]
    assert out["totals"]["rows"] == 1
    assert [t["hypothesis"] for t in out["tables_missing_columns"]] == ["H7"]


def test_ledger_line_numbers_are_one_based_file_lines():
    r = ("| 2026-01-05 | Acme onsite | some prep text | elevated | 3 | Inconclusive | x |")
    content, at = ledger_doc(("## H9: Invented", H_A, [r]))
    line = cm.compute_ledger(content)["unclassified_rows"][0]["line"]
    assert content.splitlines()[line - 1] == r and line == at[r]


def test_main_ledger_prints_the_ledger_json(tmp_path, capsys):
    (tmp_path / "coaching").mkdir()
    content, _ = ledger_doc(("## H9: Invented", H_B, [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | Support | x |"]))
    (tmp_path / "coaching" / "hypotheses.md").write_text(content, encoding="utf-8")
    assert cm.main(["--repo-root", str(tmp_path), "--ledger"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["hypotheses"]["H9"]["real"] == {"n": 1, "verdicts": {"Support": 1}}


def test_main_ledger_refuses_when_no_test_log_table_is_found(tmp_path, capsys):
    """An empty parse must not read as 'no hypotheses tested'."""
    (tmp_path / "coaching").mkdir()
    (tmp_path / "coaching" / "hypotheses.md").write_text("# Registry\n\n## H9: x\n",
                                                        encoding="utf-8")
    assert cm.main(["--repo-root", str(tmp_path), "--ledger"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_main_ledger_errors_when_the_registry_is_missing(tmp_path, capsys):
    assert cm.main(["--repo-root", str(tmp_path), "--ledger"]) == 1
    assert "hypotheses.md" in capsys.readouterr().err


def test_live_hypotheses_ledger_accounts_for_every_row():
    """Real-file shape guard: every row lands in exactly one bucket, per hypothesis."""
    src = REPO / "coaching" / "hypotheses.md"
    if not src.exists():
        pytest.skip("live registry not present")
    out = cm.compute_ledger(src.read_text(encoding="utf-8"))
    assert out["totals"]["rows"] >= 50
    for h in out["hypotheses"].values():
        assert h["rows"] == sum(h[k]["n"] for k in cm.LEDGER_KINDS)
        for k in cm.LEDGER_KINDS:
            assert h[k]["n"] == sum(h[k]["verdicts"].values())


# --- mutation survivors killed 2026-10-12 --------------------------------------------------

def test_ledger_well_formed_rows_are_not_reported_as_malformed():
    rows = ["| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | Support | x |"]
    out = cm.compute_ledger(ledger_doc(("## H9: Invented", H_B, rows))[0])
    assert out["malformed_rows"] == []


def test_ledger_narrow_row_with_a_known_other_label_is_counted_as_other():
    r = "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | Soft-miss | x |"
    content, at = ledger_doc(("## H9: Invented", H_A, [r]))
    out = cm.compute_ledger(content)
    assert out["hypotheses"]["H9"]["real"]["verdicts"] == {"Other": 1}
    assert out["other_verdicts"] == [{"line": at[r], "hypothesis": "H9", "label": "Soft-miss"}]
    assert out["unparsed_verdicts"] == []


def test_ledger_prose_line_above_a_rule_is_not_reported_as_a_table(tmp_path):
    content = ("# Registry\n\n## H9: Invented\n\nA sentence, then a rule made of pipes.\n"
               "|---|---|\n")
    assert cm.compute_ledger(content)["tables_missing_columns"] == []


def test_dimension_trend_skips_rows_with_no_score_for_that_dimension():
    """Guards `r["scores"][name] is not None`: inverting it drops every real score."""
    rows = [row(f"2026-05-0{i}", d1="4") for i in range(1, 5)]
    rows.append(row("2026-05-05", d1="n/a"))
    t = cm.compute(doc(*rows))["trends"]["format_resilience"]
    assert t is not None
    assert t["n"] == 4 and t["late"] == 4.0


def test_main_ledger_writes_the_out_file_and_says_how_many_rows(tmp_path, capsys):
    (tmp_path / "coaching").mkdir()
    content, _ = ledger_doc(("## H9: Invented", H_B, [
        "| 2026-01-05 | Acme / Jane Doe | peer-screen | hi | hi | Support | x |"]))
    (tmp_path / "coaching" / "hypotheses.md").write_text(content, encoding="utf-8")
    out = tmp_path / "ledger.json"
    assert cm.main(["--repo-root", str(tmp_path), "--ledger", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["totals"]["rows"] == 1
    said = capsys.readouterr().out
    assert str(out) in said and "1 test-log rows" in said
