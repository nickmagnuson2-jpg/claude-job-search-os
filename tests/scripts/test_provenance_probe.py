"""Tests for tools/provenance_probe.py.

These exist to FAIL when the probe breaks, not to be green. Each targets a branch whose silent
loss reproduces the defect the probe was built against: an audit that runs, reports numbers as
backed, and matched them by coincidence -- or one that indexes nothing and calls it clean.

Run alone as well as in the suite -- `pytest tests/scripts/test_provenance_probe.py` must pass
on its own, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from provenance_probe import (  # noqa: E402
    ABSOLUTE_FLOOR,
    BackedCell,
    ProvenanceReport,
    _parse_exempt,
    coincidence_rate,
    find_backing,
    index_formula_cells,
    main,
    numeric_leaves,
    trace_numbers,
)


class Cell:
    """Minimal cell: the probe reads only .value and .coordinate."""

    def __init__(self, coordinate: str, value):
        self.coordinate = coordinate
        self.value = value


class Sheet:
    """Minimal sheet: iter_rows() for the formula side, [coord] for the values side."""

    def __init__(self, cells: dict[str, object]):
        self._cells = {coord: Cell(coord, v) for coord, v in cells.items()}

    def iter_rows(self):
        return [list(self._cells.values())]

    def __getitem__(self, coord):
        return self._cells.get(coord, Cell(coord, None))


class Book:
    """Minimal workbook: the probe reads only .sheetnames and [name]."""

    def __init__(self, sheets: dict[str, dict]):
        self._sheets = {name: Sheet(cells) for name, cells in sheets.items()}

    @property
    def sheetnames(self):
        return list(self._sheets)

    def __getitem__(self, name):
        return self._sheets[name]


def books(sheets: dict[str, dict[str, tuple]]):
    """(formulas, values) from {sheet: {coord: (formula_text, computed_value)}}."""
    formulas = Book({n: {c: f for c, (f, _) in cells.items()} for n, cells in sheets.items()})
    values = Book({n: {c: v for c, (_, v) in cells.items()} for n, cells in sheets.items()})
    return formulas, values


# --- numeric_leaves -----------------------------------------------------------------


def test_leaves_report_the_dotted_path_through_dicts_and_lists():
    found = dict(numeric_leaves({"a": {"b": [10, 20]}}))
    assert found == {"a.b[0]": 10.0, "a.b[1]": 20.0}


def test_booleans_are_not_numbers():
    """isinstance(True, int) is true in Python. A flag matching a cell holding 1 is noise."""
    assert dict(numeric_leaves({"flag": True, "n": 1})) == {"n": 1.0}


def test_strings_and_nulls_are_skipped():
    assert dict(numeric_leaves({"s": "7", "none": None, "n": 7})) == {"n": 7.0}


# --- index_formula_cells ------------------------------------------------------------


def test_only_formula_cells_are_indexed():
    formulas, values = books({"S": {"A1": ("=SUM(B:B)", 5), "A2": ("typed", 9), "A3": (5, 5)}})
    assert [c.ref for c in index_formula_cells(formulas, values)] == ["S!A1"]


def test_excluded_sheets_stay_out_of_the_index():
    """The exclusion IS the correctness of the audit: a per-row sheet floods it with digits."""
    formulas, values = books(
        {"Summary": {"A1": ("=1+1", 2)}, "Per row": {"A1": ("=2+2", 4)}}
    )
    refs = [c.ref for c in index_formula_cells(formulas, values, exclude_sheets=["Per row"])]
    assert refs == ["Summary!A1"]


def test_a_misspelled_exclusion_raises_rather_than_silently_indexing_the_sheet():
    formulas, values = books({"Per row": {"A1": ("=2+2", 4)}, "S": {"A1": ("=1", 1)}})
    with pytest.raises(ValueError, match="not in the workbook"):
        index_formula_cells(formulas, values, exclude_sheets=["per row"])


def test_an_unrecalculated_workbook_raises_instead_of_reporting_a_clean_run():
    """openpyxl never evaluates, so its own output has no cached values: every read is None."""
    formulas, values = books({"S": {"A1": ("=SUM(B:B)", None)}})
    with pytest.raises(ValueError, match="not recalculated"):
        index_formula_cells(formulas, values)


def test_a_formula_computing_to_text_is_not_a_number_and_does_not_rescue_an_empty_index():
    formulas, values = books({"S": {"A1": ('=CONCAT("a","b")', "ab")}})
    with pytest.raises(ValueError):
        index_formula_cells(formulas, values)


def test_a_formula_computing_to_a_boolean_is_not_indexed():
    formulas, values = books({"S": {"A1": ("=A2>1", True), "A2": ("=1+1", 2)}})
    assert [c.value for c in index_formula_cells(formulas, values)] == [2.0]


# --- find_backing -------------------------------------------------------------------


def test_full_precision_in_the_workbook_matches_a_rounded_json_number():
    backed = [BackedCell(451.23456789012, "S!A1")]
    assert find_backing(451.2, backed) == "S!A1"


def test_a_percentage_matches_the_fraction_the_workbook_holds():
    assert find_backing(12.5, [BackedCell(0.125, "S!A1")]) == "S!A1"


def test_fraction_matching_can_be_turned_off():
    backed = [BackedCell(0.125, "S!A1")]
    assert find_backing(12.5, backed, also_try_fraction=False) is None


def test_a_small_integer_matches_within_the_absolute_floor_not_a_relative_tolerance():
    """abs(4)*1e-4 is 0.0004; without the floor a 4.05 would not match a 4."""
    assert find_backing(4.0, [BackedCell(4.0 + ABSOLUTE_FLOOR / 2, "S!A1")]) == "S!A1"


def test_a_number_outside_tolerance_has_no_backing():
    assert find_backing(1000.0, [BackedCell(900.0, "S!A1")]) is None


def test_a_large_number_matches_on_relative_tolerance():
    assert find_backing(1_000_000.0, [BackedCell(1_000_050.0, "S!A1")]) == "S!A1"


# --- trace_numbers ------------------------------------------------------------------


def test_unbacked_numbers_are_reported_with_their_path():
    backed = [BackedCell(10.0, "S!A1")]
    report = trace_numbers({"good": 10, "bad": 5000}, backed)
    assert report.traced == 1
    assert report.unbacked == [("bad", 5000.0)]
    assert report.clean is False


def test_a_value_above_100_never_takes_the_fraction_path():
    """999/100 = 9.99 lands inside the absolute floor around 10. Ungated, that "backs" a call
    count with a rate cell and costs an 11.2% coincidence floor against 1.5% gated."""
    assert find_backing(999.0, [BackedCell(10.0, "S!A1")]) is None


def test_a_plausible_percentage_still_takes_the_fraction_path():
    assert find_backing(99.0, [BackedCell(0.99, "S!A1")]) == "S!A1"


def test_a_negative_value_never_takes_the_fraction_path():
    assert find_backing(-50.0, [BackedCell(-0.5, "S!A1")]) is None


def test_an_exempt_integer_counts_as_traced():
    report = trace_numbers({"given": 4200}, [BackedCell(1.0, "S!A1")], exempt={4200: "stated"})
    assert report.traced == 1 and report.clean


def test_a_non_integer_is_never_exempted_by_an_integer_key():
    report = trace_numbers({"x": 4200.5}, [BackedCell(1.0, "S!A1")], exempt={4200: "stated"})
    assert report.unbacked == [("x", 4200.5)]


def test_an_analysis_with_no_numbers_raises_rather_than_rendering_a_clean_report():
    with pytest.raises(ValueError, match="no numbers"):
        trace_numbers({"note": "nothing numeric here"}, [BackedCell(1.0, "S!A1")])


def test_the_indexed_count_is_the_index_size_not_the_traced_count():
    report = trace_numbers({"a": 1}, [BackedCell(1.0, "S!A1"), BackedCell(2.0, "S!A2")])
    assert report.indexed == 2 and report.traced == 1


# --- coincidence_rate ---------------------------------------------------------------


def test_a_polluted_index_reports_a_high_coincidence_rate():
    """This is the measurement that would have caught the per-row sheet on the day."""
    flooded = [BackedCell(float(n), f"Per row!A{n}") for n in range(1, 7001)]
    assert coincidence_rate(flooded, samples=50, high=7000) == 1.0


def test_a_clean_index_reports_a_near_zero_rate():
    assert coincidence_rate([BackedCell(42.0, "S!A1")], samples=50, high=7000) < 0.05


def test_the_analysis_own_numbers_are_never_sampled():
    """Otherwise a real backing is counted as a coincidence and inflates the floor.

    The only backed cell is 5 and 5 is excluded, so every draw must miss. Without the skip,
    5 is drawn out of a range of five integers and the rate is non-zero.
    """
    backed = [BackedCell(5.0, "S!A5")]
    assert coincidence_rate(backed, samples=60, low=1, high=5, exclude=[5]) == 0.0


def test_without_the_exclusion_the_same_index_reports_coincidences():
    """The control on the test above: the draw really does reach 5."""
    assert coincidence_rate([BackedCell(5.0, "S!A5")], samples=60, low=1, high=5) > 0.0


def test_an_empty_index_raises_rather_than_reporting_a_zero_rate():
    with pytest.raises(ValueError, match="empty index"):
        coincidence_rate([], samples=10)


def test_zero_samples_raises_rather_than_dividing_by_nothing():
    with pytest.raises(ValueError, match="samples must be positive"):
        coincidence_rate([BackedCell(1.0, "S!A1")], samples=0)


def test_an_inverted_sampling_range_raises():
    with pytest.raises(ValueError, match="empty sampling range"):
        coincidence_rate([BackedCell(1.0, "S!A1")], samples=5, low=10, high=1)


def test_the_rate_is_deterministic_for_a_given_seed():
    backed = [BackedCell(float(n), f"S!A{n}") for n in range(1, 200)]
    first = coincidence_rate(backed, samples=100, seed=7)
    assert first == coincidence_rate(backed, samples=100, seed=7)


# --- rendering and CLI parsing ------------------------------------------------------


def test_render_groups_by_top_level_block_and_truncates():
    report = ProvenanceReport(indexed=1, traced=0, unbacked=[(f"ch[{i}].n", i) for i in range(8)])
    out = report.render()
    assert "ch  (8 unbacked)" in out
    assert "... and 2 more" in out


def test_render_prints_each_unbacked_path_and_its_value():
    """The counts alone do not tell a reader WHICH number to go and check."""
    out = ProvenanceReport(indexed=1, traced=0, unbacked=[("channel[3].never", 799.0)]).render()
    assert "channel[3].never" in out and "799.00" in out


def test_render_does_not_claim_more_when_everything_fits():
    out = ProvenanceReport(indexed=1, traced=0, unbacked=[("a.x", 1.0), ("a.y", 2.0)]).render()
    assert "more" not in out


def test_render_reports_the_three_counts():
    out = ProvenanceReport(indexed=3, traced=2, unbacked=[("x", 1.0)]).render()
    assert "indexed: 3" in out and "traced:      2" in out and "UNBACKED:    1" in out


def test_an_exemption_without_a_reason_is_refused():
    with pytest.raises(SystemExit, match="no reason"):
        _parse_exempt(["4200="])


def test_an_exemption_parses_into_number_and_reason():
    assert _parse_exempt(["4200 = stated in the brief"]) == {4200: "stated in the brief"}


# --- CLI end to end ------------------------------------------------------------------


def _cli_fixture(tmp_path, monkeypatch, sheets, analysis):
    import json

    import provenance_probe

    formulas, values = books(sheets)
    monkeypatch.setattr(provenance_probe, "load_workbooks", lambda *_a, **_k: (formulas, values))
    book = tmp_path / "b.xlsx"
    book.write_text("")
    js = tmp_path / "a.json"
    js.write_text(json.dumps(analysis))
    return ["--workbook", str(book), "--json", str(js)]


def test_cli_prints_rather_than_asserting_by_default(tmp_path, monkeypatch, capsys):
    """A miss needs a human decision, so an unbacked number is not a failing exit on its own."""
    argv = _cli_fixture(tmp_path, monkeypatch, {"S": {"A1": ("=1", 1)}}, {"bad": 999})
    assert main(argv) == 0
    assert "UNBACKED:    1" in capsys.readouterr().out


def test_cli_fails_on_unbacked_when_asked(tmp_path, monkeypatch, capsys):
    argv = _cli_fixture(tmp_path, monkeypatch, {"S": {"A1": ("=1", 1)}}, {"bad": 999})
    assert main(argv + ["--fail-on-unbacked"]) == 2


def test_cli_exits_zero_with_fail_flag_when_everything_is_backed(tmp_path, monkeypatch, capsys):
    argv = _cli_fixture(tmp_path, monkeypatch, {"S": {"A1": ("=1", 42)}}, {"good": 42})
    assert main(argv + ["--fail-on-unbacked"]) == 0


def test_cli_reports_the_coincidence_rate_when_asked(tmp_path, monkeypatch, capsys):
    argv = _cli_fixture(tmp_path, monkeypatch, {"S": {"A1": ("=1", 42)}}, {"good": 42})
    assert main(argv + ["--coincidence", "20"]) == 0
    out = capsys.readouterr().out
    assert "coincidence rate" in out
    # A bare percentage beside a verdict reads as a quality score. The line saying what it
    # measures is what stops it being read as one.
    assert "false-positive floor" in out


def test_cli_stays_silent_about_coincidence_when_not_asked(tmp_path, monkeypatch, capsys):
    argv = _cli_fixture(tmp_path, monkeypatch, {"S": {"A1": ("=1", 42)}}, {"good": 42})
    assert main(argv) == 0
    assert "coincidence" not in capsys.readouterr().out


def test_load_workbooks_reads_values_with_data_only_and_formulas_without(monkeypatch):
    """data_only is the whole two-pass design: without it the values side returns formula
    text and nothing ever matches; with it on BOTH sides the formula index is empty."""
    import provenance_probe

    calls = []

    class FakeOpenpyxl:
        @staticmethod
        def load_workbook(path, data_only=False):
            calls.append((str(path), data_only))
            return f"wb:{path}:{data_only}"

    monkeypatch.setitem(sys.modules, "openpyxl", FakeOpenpyxl)
    formulas, values = provenance_probe.load_workbooks("book.xlsx", "recalc.xlsx")
    assert calls == [("book.xlsx", False), ("recalc.xlsx", True)]
    assert (formulas, values) == ("wb:book.xlsx:False", "wb:recalc.xlsx:True")


def test_load_workbooks_defaults_the_values_side_to_the_same_file(monkeypatch):
    import provenance_probe

    calls = []

    class FakeOpenpyxl:
        @staticmethod
        def load_workbook(path, data_only=False):
            calls.append((str(path), data_only))
            return path


    monkeypatch.setitem(sys.modules, "openpyxl", FakeOpenpyxl)
    provenance_probe.load_workbooks("book.xlsx")
    assert calls == [("book.xlsx", False), ("book.xlsx", True)]
