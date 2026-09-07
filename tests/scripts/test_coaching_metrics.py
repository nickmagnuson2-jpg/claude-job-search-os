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
