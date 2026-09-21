"""Tests for tools/slide_check.py, the promoted slide harness.

These exist to FAIL when the harness stops establishing what it claims. The defect it was
built against is not a wrong number: it is a suite of green checks that never opened the
page, so "every printed value reproduces from the source" was asserted and never established.
Most of what follows targets the binding, not the arithmetic.

Run alone as well as in the suite -- `pytest tests/scripts/test_slide_check.py` must pass on
its own, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from slide_check import SlideCheck, deck_text  # noqa: E402


DECK = """<html><head><style>.slide{color:#000}</style></head><body>
<div class="slide"><h1>Revenue was $123,456 in November</h1>
<p>Acme booked 62.5% &middot; the prior service 37.5%</p>
<svg viewBox="0 0 10 10"><text>44.0%</text></svg></div>
</body></html>"""


@pytest.fixture
def deck(tmp_path):
    p = tmp_path / "deck.html"
    p.write_text(DECK, encoding="utf-8")
    return p


# --- deck_text ----------------------------------------------------------------------


def test_deck_text_returns_what_the_page_prints(deck):
    t = deck_text(deck)
    assert "$123,456" in t and "62.5%" in t


def test_deck_text_reads_svg_labels(deck):
    """Charts print numbers as <text> inside <svg>. A reader that skips them would bind
    every prose claim and silently bind nothing on any chart."""
    assert "44.0%" in deck_text(deck)


def test_deck_text_decodes_entities_via_the_parser(deck):
    """The engagement version hand-decoded five entities from a table that could only drift.
    A middot arriving as "&middot;" would make a label carrying one fail to bind."""
    t = deck_text(deck)
    assert "·" in t and "&middot;" not in t


def test_deck_text_excludes_the_stylesheet(deck):
    """CSS is not printed. Including it lets a token "bind" against a colour or a class."""
    assert "color:#000" not in deck_text(deck)


def test_deck_text_collapses_whitespace(deck):
    assert "  " not in deck_text(deck)


# --- check ---------------------------------------------------------------------------


def test_a_matching_value_passes_and_records_no_failure():
    sc = SlideCheck("slide 1", quiet=True)
    assert sc.check("x", 5, 5) is True
    assert sc.failures == []


def test_a_mismatch_is_collected_rather_than_raised():
    """One run must list every mismatch. Raising on the first hides the rest."""
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("a", 1, 2)
    sc.check("b", 3, 4)
    assert len(sc.failures) == 2


def test_the_tolerance_is_absolute_and_respected():
    sc = SlideCheck("s", quiet=True)
    assert sc.check("close", 1.004, 1.0, tol=0.01) is True
    assert sc.check("far", 1.02, 1.0, tol=0.01) is False


def test_a_zero_tolerance_rejects_a_near_miss():
    """The default must not quietly accept a rounding difference."""
    assert SlideCheck("s", quiet=True).check("x", 1.0001, 1.0) is False


def test_non_numeric_values_compare_by_equality():
    sc = SlideCheck("s", quiet=True)
    assert sc.check("t", "Inbound", "Inbound") is True
    assert sc.check("t", "Inbound", "Outbound") is False


# --- binding, which is the point -----------------------------------------------------


def test_an_unbound_run_says_so_rather_than_implying_coverage():
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("revenue (slide: $123,456)", 123456, 123456)
    assert "NOT BOUND TO A DECK" in sc.coverage_note()


def test_a_bound_claim_that_the_page_prints_passes(deck):
    sc = SlideCheck("slide 1", quiet=True)
    sc.bind_deck(deck)
    sc.check("revenue (slide: $123,456)", 123456, 123456)
    assert sc.failures == []
    assert "1 assertion(s) bound" in sc.coverage_note()


def test_a_claim_the_page_does_NOT_print_fails_even_though_the_arithmetic_matches(deck):
    """THE WHOLE POINT. Computed == literal, and the page prints something else. Before
    binding existed, every check of this shape passed and the deck was wrong."""
    sc = SlideCheck("slide 1", quiet=True)
    sc.bind_deck(deck)
    sc.check("revenue (slide: $133,456)", 133456, 133456)
    assert len(sc.failures) == 1
    assert "does not appear" in sc.failures[0]


def test_a_label_with_no_printed_token_is_counted_unbound_not_passed(deck):
    """Silence is not agreement. A check that claims nothing about the page must not be
    counted as evidence about the page."""
    sc = SlideCheck("slide 1", quiet=True)
    sc.bind_deck(deck)
    sc.check("an internal consistency check", 5, 5)
    assert sc.failures == []
    assert "1 carried no 'slide:' token" in sc.coverage_note()


@pytest.mark.parametrize("word", ["slide", "source line", "left column", "takeaway"])
def test_every_claim_keyword_binds(deck, word):
    sc = SlideCheck("s", quiet=True)
    sc.bind_deck(deck)
    sc.check(f"rev ({word}: $133,456)", 1, 1)
    assert len(sc.failures) == 1, f"{word} claims were not checked against the page"


def test_binding_reports_the_deck_it_checked_against(deck):
    sc = SlideCheck("s", quiet=True)
    sc.bind_deck(deck)
    sc.check("rev (slide: $133,456)", 1, 1)
    assert str(deck) in sc.failures[0]


def test_an_svg_label_binds(deck):
    """A chart value must be bindable, or charts are exempt from the only rule that reads
    the page."""
    sc = SlideCheck("s", quiet=True)
    sc.bind_deck(deck)
    sc.check("band (slide: 44.0%)", 44.0, 44.0)
    assert sc.failures == []


# --- pct -----------------------------------------------------------------------------


def test_pct_registers_the_denominator_it_was_taken_over():
    """Every percentage defect on the originating deck was a denominator defect. The
    register is what makes the population a field instead of a habit."""
    sc = SlideCheck("s", quiet=True)
    sc.pct("booked", 625, 1000, 62.5, "eligible records", decimals=1)
    row, = sc.out["percentages"]
    assert row["denominator"] == 1000
    assert row["population"] == "eligible records"
    assert row["numerator"] == 625


def test_pct_fails_when_the_recomputation_disagrees_with_the_printed_share():
    sc = SlideCheck("s", quiet=True)
    assert sc.pct("booked", 500, 1000, 62.5, "eligible records", decimals=1) is False


def test_pct_rounds_to_the_requested_precision():
    sc = SlideCheck("s", quiet=True)
    assert sc.pct("booked", 6254, 10000, 62.5, "records", decimals=1) is True
    assert sc.pct("booked", 6254, 10000, 63, "records", decimals=0) is True


def test_pct_names_the_population_in_the_label_it_binds():
    """"62.5%" printed under two different denominators is the defect. The bound label
    has to carry which one."""
    sc = SlideCheck("s", quiet=True)
    sc.pct("booked", 625, 1000, 62.5, "eligible records", decimals=1)
    assert sc.out["percentages"][0]["label"] == "booked"


# --- report and write ----------------------------------------------------------------


def test_report_exits_non_zero_when_anything_failed(capsys):
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("a", 1, 2)
    assert sc.report() == 1
    assert "DO NOT MATCH" in capsys.readouterr().out


def test_report_exits_zero_when_everything_matched():
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("a", 1, 1)
    assert sc.report() == 0


def test_report_lists_every_failure_not_just_the_count(capsys):
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("first", 1, 2)
    sc.check("second", 3, 4)
    sc.report()
    out = capsys.readouterr().out
    assert "first" in out and "second" in out


def test_note_records_without_asserting():
    sc = SlideCheck("s", quiet=True)
    sc.note("k", 12)
    assert sc.out["k"] == 12 and sc.failures == []


def test_write_emits_the_payload(tmp_path):
    sc = SlideCheck("s", quiet=True)
    sc.note("k", 12)
    out = tmp_path / "o.json"
    sc.write(out)
    assert json.loads(out.read_text())["k"] == 12


def test_quiet_suppresses_the_per_check_echo(capsys):
    SlideCheck("s", quiet=True).check("a", 1, 1)
    assert capsys.readouterr().out == ""


def test_a_loud_run_echoes_passes_as_well_as_failures(capsys):
    """A passing line is the record a future session diffs against when a number moves."""
    SlideCheck("s").check("a", 1, 1)
    assert "[ok" in capsys.readouterr().out


def test_section_prints_only_when_loud(capsys):
    SlideCheck("s", quiet=True).section("Populations")
    assert capsys.readouterr().out == ""
    SlideCheck("s").section("Populations")
    assert "Populations" in capsys.readouterr().out


def test_a_quiet_write_stays_silent_and_a_loud_one_names_the_file(tmp_path, capsys):
    out = tmp_path / "o.json"
    SlideCheck("s", quiet=True).write(out)
    assert capsys.readouterr().out == ""
    SlideCheck("s").write(out)
    printed = capsys.readouterr().out
    assert "wrote" in printed and str(out) in printed


def test_a_clean_loud_report_states_what_it_established_and_how_many_checks(capsys):
    """A bare exit 0 is the shape this harness exists to distrust. The line has to say
    what reproduced and over how many checks, or a green run carries no record."""
    sc = SlideCheck("slide 1")
    sc.check("a", 1, 1)
    capsys.readouterr()
    assert sc.report() == 0
    out = capsys.readouterr().out
    assert "All slide 1 values reproduce" in out
    assert "(1 checks)" in out


def test_a_clean_quiet_report_prints_nothing_but_still_exits_zero(capsys):
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("a", 1, 1)
    assert sc.report() == 0
    assert capsys.readouterr().out == ""


def test_a_failing_report_speaks_even_when_quiet(capsys):
    """Quiet suppresses the running commentary, never the verdict. A silent failure is
    a failure nobody reads."""
    sc = SlideCheck("slide 1", quiet=True)
    sc.check("a", 1, 2)
    assert sc.report() == 1
    assert "DO NOT MATCH" in capsys.readouterr().out
