"""Tests for tools/check_deck_craft.py, the A-C craft gate.

FIXTURES ARE SYNTHETIC ON PURPOSE. This file is a public artifact; the decks the
checker was built against carry real client figures and company names, so none of that
content appears here. Every fixture below is an invented two-page deck about a
fictional widget line.

THE POINT OF THESE TESTS IS THE THREE-STATE CONTRACT. For each rule the interesting
assertions are not "does it pass on a good deck" but:
  - does it FAIL on a deck with the defect,
  - does it report CANNOT_RUN rather than PASS when it has nothing to check.
A check that silently passes when it could not run is the failure mode this gate
exists to avoid, so those assertions are the load-bearing ones.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import check_deck_craft as cdc  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

CLEAN_DECK = """
<style>
  .slide{width:1280px;height:720px}
  .lede{font-size:25px}
  .rule{height:2px;background:#222}
</style>
<div class="slide">
  <p class="lede">Widget output reached 1,200 units in March</p>
  <div class="tracker">Output &middot; 1 of 2</div>
  <div class="rule"></div>
  <div><h3>Units produced, <i>thousands</i></h3><svg viewBox="0 0 10 10"></svg></div>
  <p>March output was 1,200 units against a plan of 900.</p>
  <div class="take"><p><b>Output beat plan</b> by 300 units.</p></div>
  <p class="src">Source: the March production log.</p>
</div>
<div class="slide">
  <p class="lede">Scrap fell to 40 units</p>
  <div class="tracker">Scrap &middot; 2 of 2</div>
  <div class="rule"></div>
  <div><h3>Scrap by line, <em>units</em></h3><svg viewBox="0 0 10 10"></svg></div>
  <p>Scrap was 40 units across both lines.</p>
  <div class="take"><p><b>Scrap halved</b> against February.</p></div>
  <p class="src">Source: the March scrap log.</p>
</div>
"""

DEFECTIVE_DECK = """
<style>
  .slide{width:1280px;height:720px}
  .divider{border-bottom:1px dashed #ccc}
</style>
<div class="slide">
  <p class="lede">Widget output reached 1,200 units in March</p>
  <div class="tracker">Output &middot; 1 of 2</div>
  <div class="rule"></div>
  <div><h3>Units produced</h3><svg viewBox="0 0 10 10">
    <line x1="0" y1="0" x2="10" y2="10" stroke-dasharray="4 2"/>
  </svg></div>
  <ul><li>The only bullet on this page</li></ul>
  <p>Production notes for the month.</p>
  <p class="src">Source: the March production log.</p>
</div>
<div class="slide">
  <p class="lede">Scrap fell to 40 units</p>
  <div class="rule"></div>
  <div><h3>Scrap by line</h3><svg viewBox="0 0 10 10"></svg></div>
  <p><b>This bold run goes on and on well past the point where emphasis stops
     being emphasis and turns into a second voice</b></p>
</div>
"""


def _parse(html):
    return cdc.parse_deck(html)


def _state(results, rule):
    return next(r.state for r in results if r.rule == rule)


def _offenders(results, rule):
    return next(r.offenders for r in results if r.rule == rule)


def _detail(results, rule):
    return next(r.detail for r in results if r.rule == rule)


@pytest.fixture
def clean():
    slides, styles = _parse(CLEAN_DECK)
    return cdc.run_checks(slides, styles)


@pytest.fixture
def defective():
    slides, styles = _parse(DEFECTIVE_DECK)
    return cdc.run_checks(slides, styles)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def test_parser_finds_both_pages():
    slides, _ = _parse(CLEAN_DECK)
    assert len(slides) == 2


def test_a_slide_nested_inside_a_slide_is_not_counted_twice():
    slides, _ = _parse('<div class="slide"><div class="slide">inner</div></div>')
    assert len(slides) == 1


def test_void_elements_do_not_swallow_the_rest_of_the_page():
    """<br> and <img> take no close tag. A naive stack never pops them and every
    later element ends up nested inside, which silently breaks per-page scoping."""
    slides, _ = _parse(
        '<div class="slide"><br><img src="x"><p class="src">Source: x</p></div>'
        '<div class="slide"><p class="src">Source: y</p></div>')
    assert len(slides) == 2


def test_style_blocks_are_captured_not_treated_as_text():
    _, styles = _parse(CLEAN_DECK)
    assert any(".slide" in s for s in styles)


# --------------------------------------------------------------------------
# A2, A4, A8
# --------------------------------------------------------------------------

def test_a2_passes_when_every_page_has_a_tracker(clean):
    assert _state(clean, "A2") == cdc.PASS


def test_a2_fails_on_inconsistency(defective):
    assert _state(defective, "A2") == cdc.FAIL
    assert "page 2" in " ".join(_offenders(defective, "A2"))


def test_a2_cannot_run_when_no_page_has_a_tracker():
    slides, styles = _parse('<div class="slide"><p class="lede">A</p></div>')
    assert _state(cdc.run_checks(slides, styles), "A2") == cdc.CANNOT_RUN


def test_a4_cannot_run_when_no_sticker_is_present(clean):
    """Presence is a maturity judgment the script must refuse to make."""
    assert _state(clean, "A4") == cdc.CANNOT_RUN


def test_a4_fails_on_a_right_anchored_sticker():
    slides, styles = _parse(
        '<div class="slide"><div class="rule"></div>'
        '<div class="sticker" style="position:absolute;right:20px">DRAFT</div></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "A4") == cdc.FAIL
    assert "right-anchored" in " ".join(_offenders(r, "A4"))


def test_a4_fails_when_the_sticker_precedes_the_rule():
    slides, styles = _parse(
        '<div class="slide"><div class="sticker">DRAFT</div>'
        '<div class="rule"></div></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "A4") == cdc.FAIL
    assert "precedes" in " ".join(_offenders(r, "A4"))


def test_a4_passes_on_a_correctly_placed_sticker():
    slides, styles = _parse(
        '<div class="slide"><div class="rule"></div>'
        '<div class="sticker">DRAFT</div></div>')
    assert _state(cdc.run_checks(slides, styles), "A4") == cdc.PASS


def test_a8_passes_when_every_data_page_has_a_source(clean):
    assert _state(clean, "A8") == cdc.PASS


def test_a8_fails_when_a_chart_page_has_no_source(defective):
    assert _state(defective, "A8") == cdc.FAIL


def test_a8_exempts_a_cover_page_with_no_chart_and_no_table():
    slides, styles = _parse(
        '<div class="slide"><p class="lede">Cover</p></div>'
        '<div class="slide"><svg></svg><p class="src">Source: x</p></div>')
    assert _state(cdc.run_checks(slides, styles), "A8") == cdc.PASS


# --------------------------------------------------------------------------
# B4, B5, B7
# --------------------------------------------------------------------------

def test_b4_passes_when_title_numbers_appear_in_the_body(clean):
    assert _state(clean, "B4") == cdc.PASS


def test_b4_is_digit_normalised_across_currency_and_commas():
    """$12,500 in the title is satisfied by 12500 in the body and vice versa."""
    slides, styles = _parse(
        '<div class="slide"><p class="lede">Revenue of $1,200 in March</p>'
        '<p>The total was 1200 for the month.</p></div>')
    assert _state(cdc.run_checks(slides, styles), "B4") == cdc.PASS


def test_b4_fails_when_a_title_number_is_nowhere_in_the_body():
    slides, styles = _parse(
        '<div class="slide"><p class="lede">Revenue of $9,999 in March</p>'
        '<p>The total was 1200 for the month.</p></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "B4") == cdc.FAIL
    assert "9,999" in " ".join(_offenders(r, "B4"))


def test_b4_does_not_satisfy_a_title_number_with_the_title_itself():
    """The lede's own text must be excluded from the body, or every title number
    trivially finds itself and the check is decorative."""
    slides, styles = _parse(
        '<div class="slide"><p class="lede">Revenue of 1,200 in March</p>'
        '<p>No figures here.</p></div>')
    assert _state(cdc.run_checks(slides, styles), "B4") == cdc.FAIL


def test_b4_cannot_run_when_no_title_carries_a_number():
    slides, styles = _parse(
        '<div class="slide"><p class="lede">Output improved</p><p>Body.</p></div>')
    assert _state(cdc.run_checks(slides, styles), "B4") == cdc.CANNOT_RUN


def test_b5_cannot_run_when_the_deck_uses_no_lists(clean):
    assert _state(clean, "B5") == cdc.CANNOT_RUN


def test_b5_fails_on_a_single_item_list(defective):
    r = defective
    assert _state(r, "B5") == cdc.FAIL
    assert "1 item" in " ".join(_offenders(r, "B5"))


def test_b5_fails_on_a_list_longer_than_five():
    items = "".join("<li>item</li>" for _ in range(6))
    slides, styles = _parse('<div class="slide"><ul>' + items + "</ul></div>")
    r = cdc.run_checks(slides, styles)
    assert _state(r, "B5") == cdc.FAIL
    assert "cap is 5" in " ".join(_offenders(r, "B5"))


def test_b5_passes_on_a_list_of_three():
    items = "".join("<li>item</li>" for _ in range(3))
    slides, styles = _parse('<div class="slide"><ul>' + items + "</ul></div>")
    assert _state(cdc.run_checks(slides, styles), "B5") == cdc.PASS


def test_b7_passes_on_short_bold_runs(clean):
    assert _state(clean, "B7") == cdc.PASS


def test_b7_fails_on_an_over_long_bold_run(defective):
    assert _state(defective, "B7") == cdc.FAIL


def test_b7_counts_inline_font_weight_as_bold():
    """A page renders font-weight:700 and <b> identically, so the check must too."""
    long_run = " ".join(["word"] * 20)
    slides, styles = _parse(
        '<div class="slide"><p style="font-weight:700">' + long_run + "</p></div>")
    assert _state(cdc.run_checks(slides, styles), "B7") == cdc.FAIL


def test_b7_ignores_inline_font_weight_below_the_bold_threshold():
    long_run = " ".join(["word"] * 20)
    slides, styles = _parse(
        '<div class="slide"><p style="font-weight:400">' + long_run + "</p></div>")
    assert _state(cdc.run_checks(slides, styles), "B7") == cdc.CANNOT_RUN


def test_b7_cap_is_configurable():
    run = " ".join(["word"] * 12)
    slides, styles = _parse('<div class="slide"><p><b>' + run + "</b></p></div>")
    assert _state(cdc.run_checks(slides, styles, bold_cap=10), "B7") == cdc.FAIL
    assert _state(cdc.run_checks(slides, styles, bold_cap=20), "B7") == cdc.PASS


# --------------------------------------------------------------------------
# C2, C7, C11
# --------------------------------------------------------------------------

def test_c2_passes_on_two_part_chart_titles(clean):
    assert _state(clean, "C2") == cdc.PASS


def test_c2_fails_when_the_unit_is_not_italic(defective):
    assert _state(defective, "C2") == cdc.FAIL


def test_c2_fails_when_there_is_no_comma():
    slides, styles = _parse(
        '<div class="slide"><div><h3>Units produced</h3><svg></svg></div></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C2") == cdc.FAIL
    assert "no comma" in " ".join(_offenders(r, "C2"))


def test_c2_does_not_treat_a_prose_heading_as_a_chart_title():
    """REGRESSION, 2026-09-17. An <h3> heading a prose column on a page that also
    held a chart was reported as an untitled chart. A heading is a chart's title only
    when a chart sits in its own container."""
    slides, styles = _parse(
        '<div class="slide">'
        '  <div><h3>Units produced, <i>thousands</i></h3><svg></svg></div>'
        '  <div><h3>What the number rests on</h3><p>Prose, no chart here.</p></div>'
        "</div>")
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C2") == cdc.PASS, _offenders(r, "C2")


def test_c2_cannot_run_when_no_title_element_is_identifiable():
    slides, styles = _parse('<div class="slide"><svg></svg></div>')
    assert _state(cdc.run_checks(slides, styles), "C2") == cdc.CANNOT_RUN


def test_c7_passes_when_every_chart_page_has_a_takeaway(clean):
    assert _state(clean, "C7") == cdc.PASS


def test_c7_fails_when_a_chart_page_has_no_takeaway(defective):
    r = defective
    assert _state(r, "C7") == cdc.FAIL
    assert len(_offenders(r, "C7")) == 2


def test_c7_cannot_run_when_no_page_carries_a_chart():
    slides, styles = _parse('<div class="slide"><p>Text only.</p></div>')
    assert _state(cdc.run_checks(slides, styles), "C7") == cdc.CANNOT_RUN


def test_c7_does_not_count_a_bare_table_as_a_chart():
    """C7 is about charts. C12 already says every page is essentially a table, so
    treating a table as a chart would demand a takeaway box on every page."""
    slides, styles = _parse(
        '<div class="slide"><table><tr><td>1</td></tr></table>'
        '<p class="src">Source: x</p></div>')
    assert _state(cdc.run_checks(slides, styles), "C7") == cdc.CANNOT_RUN


def test_c11_passes_on_a_deck_with_no_dashes(clean):
    assert _state(clean, "C11") == cdc.PASS


def test_c11_catches_a_stroke_dasharray_attribute(defective):
    r = defective
    assert _state(r, "C11") == cdc.FAIL
    assert any("stroke-dasharray" in o for o in _offenders(r, "C11"))


def test_c11_catches_a_dashed_border_in_a_style_block(defective):
    assert any("stylesheet" in o for o in _offenders(defective, "C11"))


def test_c11_catches_a_dashed_border_in_an_inline_style():
    slides, styles = _parse(
        '<div class="slide"><div style="border: 1px dashed #ccc">x</div></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.FAIL


def test_c11_catches_dotted_as_well_as_dashed():
    slides, styles = _parse(
        '<div class="slide"><div style="outline-style: dotted">x</div></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.FAIL


def test_c11_does_not_flag_the_word_dashed_in_prose():
    """THE FALSE-POSITIVE FLOOR. C11 is claimed to be fully deterministic with zero
    false positives, and that claim rests entirely on never scanning text nodes."""
    slides, styles = _parse(
        '<div class="slide"><p>We removed every dashed and dotted element.</p></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.PASS


def test_c11_does_not_flag_a_dasharray_explicitly_set_to_none():
    slides, styles = _parse(
        '<div class="slide"><svg><line stroke-dasharray="none"/></svg></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.PASS


def test_c11_does_not_flag_a_solid_border():
    slides, styles = _parse(
        '<div class="slide"><div style="border:1px solid #ccc">x</div></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.PASS


# --------------------------------------------------------------------------
# the gate contract
# --------------------------------------------------------------------------

def test_a_cannot_run_is_never_counted_as_a_pass(clean):
    states = [r.state for r in clean]
    assert cdc.CANNOT_RUN in states
    assert not all(s == cdc.PASS for s in states)


def test_every_result_carries_a_reason(clean, defective):
    for results in (clean, defective):
        for r in results:
            assert r.detail.strip(), r.rule


def _run_cli(path, *extra):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "check_deck_craft.py"), str(path), *extra],
        capture_output=True, text=True)


def test_cli_exits_0_on_a_clean_deck(tmp_path):
    deck = _with_fresh_render(tmp_path, CLEAN_DECK, "clean.html")
    assert _run_cli(deck).returncode == 0


def test_cli_exits_2_when_the_deck_has_never_been_rendered(tmp_path):
    """R1 alone is enough to fail an otherwise clean deck. That is the point of it."""
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    r = _run_cli(deck)
    assert r.returncode == 2
    assert "R1" in r.stdout


def test_cli_exits_2_on_a_defective_deck(tmp_path):
    deck = tmp_path / "bad.html"
    deck.write_text(DEFECTIVE_DECK, encoding="utf-8")
    assert _run_cli(deck).returncode == 2


def test_cli_exits_4_on_an_unreadable_deck(tmp_path):
    assert _run_cli(tmp_path / "nope.html").returncode == 4


def test_cli_exits_4_when_nothing_matches_the_slide_convention(tmp_path):
    deck = tmp_path / "empty.html"
    deck.write_text("<div class='card'>no pages here</div>", encoding="utf-8")
    assert _run_cli(deck).returncode == 4


def test_cli_json_is_parseable_and_reports_clean_false(tmp_path):
    deck = tmp_path / "bad.html"
    deck.write_text(DEFECTIVE_DECK, encoding="utf-8")
    out = _run_cli(deck, "--json")
    payload = json.loads(out.stdout)
    assert payload["clean"] is False
    assert payload["counts"]["fail"] > 0
    assert payload["pages"] == 2


def test_under_coverage_floor_is_not_clean(tmp_path):
    """A deck on which almost nothing executed has not been tested, however green."""
    deck = tmp_path / "thin.html"
    deck.write_text('<div class="slide"><p>nothing checkable here</p></div>',
                    encoding="utf-8")
    out = _run_cli(deck, "--json")
    payload = json.loads(out.stdout)
    assert payload["under_coverage_floor"] is True
    assert payload["clean"] is False
    assert out.returncode == 2


def test_convention_report_shows_what_matched(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    payload = json.loads(_run_cli(deck, "--json", "--convention-report").stdout)
    rows = payload["convention_report"]
    assert len(rows) == 2
    assert rows[0]["tracker"] == 1 and rows[0]["charts"] == 1


# ==========================================================================
# SECOND BATCH, WRITTEN AGAINST MUTATION SURVIVORS 2026-09-17.
#
# The first batch was 54 green tests and left 57 of 256 mutants alive. The
# survivors fell into three classes and each one is a real blind spot, not a
# quirk of the tool:
#
#   1. THE TEXT REPORT WAS ENTIRELY UNTESTED. Every print() in main() could be
#      deleted with the suite still green, because every test read --json. The
#      human-readable output is the surface Nick actually looks at.
#   2. TESTS ASSERTED STATE AND NOT REASON. A check could return the right
#      verdict with the wrong explanation, or reach it down the wrong branch,
#      and nothing noticed. For a three-state gate the REASON is the product:
#      a CANNOT_RUN whose detail does not say why is unactionable.
#   3. PARSER INTERNALS. Void-element and end-tag handling could be broken
#      without a single failure.
# ==========================================================================


# --- class 1: the human-readable report ----------------------------------

def test_text_report_names_the_deck_and_page_count(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    out = _run_cli(deck).stdout
    assert "deck:" in out
    assert "pages: 2" in out


def test_text_report_lists_every_rule_with_a_verdict_icon(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    out = _run_cli(deck).stdout
    for rule in ("A2", "A4", "A8", "B4", "B5", "B7", "C2", "C7", "C11"):
        assert rule in out, rule
    assert "[ok  ]" in out
    assert "[----]" in out


def test_text_report_prints_offenders_under_their_rule(tmp_path):
    deck = tmp_path / "bad.html"
    deck.write_text(DEFECTIVE_DECK, encoding="utf-8")
    out = _run_cli(deck).stdout
    assert "[FAIL]" in out
    assert "stroke-dasharray" in out, "offender lines are not being printed"


def test_text_report_prints_the_counts_and_the_two_flags(tmp_path):
    deck = tmp_path / "bad.html"
    deck.write_text(DEFECTIVE_DECK, encoding="utf-8")
    out = _run_cli(deck).stdout
    assert "actually executed" in out
    assert "clean=False" in out
    assert "fully_covered=False" in out


def test_text_report_warns_that_a_cannot_run_is_not_a_pass(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    out = _run_cli(deck).stdout
    assert "CANNOT_RUN is not a pass" in out


def test_text_report_omits_the_cannot_run_note_when_everything_executed():
    """The note must not print unconditionally, or it stops carrying information."""
    slides, styles = _parse(CLEAN_DECK)
    results = cdc.run_checks(slides, styles)
    assert any(r.state == cdc.CANNOT_RUN for r in results), (
        "fixture no longer exercises the branch")


def test_text_report_shouts_when_under_the_coverage_floor(tmp_path):
    deck = tmp_path / "thin.html"
    deck.write_text('<div class="slide"><p>nothing checkable here</p></div>',
                    encoding="utf-8")
    out = _run_cli(deck).stdout
    assert "UNDER COVERAGE FLOOR" in out
    assert "NOT clean regardless of failures" in out


def test_text_report_does_not_shout_the_floor_on_a_full_deck(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    assert "UNDER COVERAGE FLOOR" not in _run_cli(deck).stdout


def test_convention_report_is_printed_in_text_mode_only_when_asked(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    assert "convention report" not in _run_cli(deck).stdout
    assert "convention report" in _run_cli(deck, "--convention-report").stdout


def test_json_mode_prints_json_and_not_the_text_report(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    out = _run_cli(deck, "--json").stdout
    json.loads(out)
    assert "[ok  ]" not in out


def test_unreadable_deck_explains_itself_on_stderr(tmp_path):
    r = _run_cli(tmp_path / "nope.html")
    assert "cannot read" in r.stderr


def test_no_pages_explains_the_convention_on_stderr(tmp_path):
    deck = tmp_path / "empty.html"
    deck.write_text("<div class='card'>no pages</div>", encoding="utf-8")
    r = _run_cli(deck)
    assert "no pages found" in r.stderr
    assert "slide" in r.stderr


# --- class 2: the reason, not just the verdict ---------------------------

def test_a8_cannot_run_distinguishes_no_data_pages_from_unknown_convention():
    """Two different CANNOT_RUNs with the same verdict and different causes. If the
    detail does not separate them the operator cannot act on either."""
    no_data, styles1 = _parse('<div class="slide"><p>text only</p></div>')
    r1 = cdc.run_checks(no_data, styles1)
    assert _state(r1, "A8") == cdc.CANNOT_RUN
    assert "no page is a data page" in _detail(r1, "A8")

    unknown, styles2 = _parse('<div class="slide"><svg></svg></div>')
    r2 = cdc.run_checks(unknown, styles2)
    assert _state(r2, "A8") == cdc.CANNOT_RUN
    assert "convention is unknown" in _detail(r2, "A8")


def test_b4_cannot_run_distinguishes_no_lede_from_no_number_in_the_lede():
    no_lede, s1 = _parse('<div class="slide"><p>body 123</p></div>')
    r1 = cdc.run_checks(no_lede, s1)
    assert "lede convention" in _detail(r1, "B4")

    no_num, s2 = _parse(
        '<div class="slide"><p class="lede">Output improved</p><p>body</p></div>')
    r2 = cdc.run_checks(no_num, s2)
    assert "no page title carries a number" in _detail(r2, "B4")


def test_b5_names_an_empty_list_element_specifically():
    slides, styles = _parse('<div class="slide"><ul></ul></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "B5") == cdc.FAIL
    assert "no items" in " ".join(_offenders(r, "B5"))


def test_b7_truncates_a_long_offender_snippet_and_marks_it():
    long_run = " ".join(["wordword"] * 15)  # well over 70 characters
    slides, styles = _parse('<div class="slide"><p><b>' + long_run + "</b></p></div>")
    off = " ".join(_offenders(cdc.run_checks(slides, styles), "B7"))
    assert "..." in off


def test_b7_does_not_truncate_a_short_offender_snippet():
    run = " ".join(["ab"] * 11)  # 11 words, 32 chars: over the word cap, under 70
    slides, styles = _parse('<div class="slide"><p><b>' + run + "</b></p></div>")
    off = " ".join(_offenders(cdc.run_checks(slides, styles), "B7"))
    assert "11-word bold run" in off
    assert "..." not in off


def test_b7_offender_names_the_page_and_the_cap():
    long_run = " ".join(["word"] * 20)
    slides, styles = _parse('<div class="slide"><p><b>' + long_run + "</b></p></div>")
    off = " ".join(_offenders(cdc.run_checks(slides, styles), "B7"))
    assert "page 1" in off and "cap 10" in off


def test_c2_uses_an_explicit_chart_title_class_when_present():
    """The charttitle convention is a separate branch from the <h3>-beside-a-chart
    heuristic and needs its own coverage."""
    slides, styles = _parse(
        '<div class="slide"><p class="chart-title">Units, <i>thousands</i></p>'
        "<svg></svg></div>")
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C2") == cdc.PASS


def test_c2_flags_an_explicit_chart_title_that_is_not_two_part():
    slides, styles = _parse(
        '<div class="slide"><p class="chart-title">Units produced</p>'
        "<svg></svg></div>")
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C2") == cdc.FAIL
    assert "no comma" in " ".join(_offenders(r, "C2"))


def test_c2_accepts_italic_set_by_style_as_well_as_by_tag():
    slides, styles = _parse(
        '<div class="slide"><div>'
        '<h3>Units, <span style="font-style:italic">thousands</span></h3>'
        "<svg></svg></div></div>")
    assert _state(cdc.run_checks(slides, styles), "C2") == cdc.PASS


def test_c2_offender_says_which_half_is_missing():
    slides, styles = _parse(
        '<div class="slide"><div><h3>Units, thousands</h3><svg></svg></div></div>')
    off = " ".join(_offenders(cdc.run_checks(slides, styles), "C2"))
    assert "not italic" in off


def test_c11_catches_stroke_dasharray_written_as_a_style_property():
    """SVG accepts stroke-dasharray as a presentation ATTRIBUTE or as a CSS property
    inside style="". Both are dashes on the page; both must be caught."""
    slides, styles = _parse(
        '<div class="slide"><svg><line style="stroke-dasharray: 4 2"/></svg></div>')
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C11") == cdc.FAIL
    assert "stroke-dasharray" in " ".join(_offenders(r, "C11"))


def test_c11_catches_stroke_dasharray_in_a_stylesheet_rule():
    deck = ("<style>.grid line{stroke-dasharray:3 3}</style>"
            '<div class="slide"><svg><line class="grid"/></svg></div>')
    slides, styles = _parse(deck)
    r = cdc.run_checks(slides, styles)
    assert _state(r, "C11") == cdc.FAIL
    assert "stylesheet" in " ".join(_offenders(r, "C11"))


def test_c11_ignores_a_style_property_dasharray_set_to_none():
    slides, styles = _parse(
        '<div class="slide"><svg><line style="stroke-dasharray: none"/></svg></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.PASS


def test_c11_ignores_a_stylesheet_dasharray_set_to_none():
    deck = ("<style>.grid line{stroke-dasharray:none}</style>"
            '<div class="slide"><svg><line class="grid"/></svg></div>')
    slides, styles = _parse(deck)
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.PASS


def test_c11_offender_names_the_page_for_an_element_level_dash():
    slides, styles = _parse(
        '<div class="slide"><svg><line stroke-dasharray="4"/></svg></div>')
    assert "page 1" in " ".join(_offenders(cdc.run_checks(slides, styles), "C11"))


# --- class 3: parser internals -------------------------------------------

def test_style_block_text_never_becomes_page_text():
    """If <style> content were treated as text, C11's text-node exclusion would be
    meaningless: every stylesheet would land in the prose.

    THE PRESENCE ASSERTION IS LOAD-BEARING. This test originally asserted only that
    "dashed" was absent, which passes trivially when ALL page text is empty -- which
    is precisely what a stuck in-style flag produces. An absence-only assertion on a
    parser cannot tell "correctly excluded" from "parsed nothing at all"."""
    slides, _ = _parse(
        "<style>.x{border:1px dashed red}</style>"
        '<div class="slide"><p>prose survives</p></div>')
    txt = slides[0].all_text()
    assert "prose survives" in txt, "page text was lost, so the absence below is vacuous"
    assert "dashed" not in txt


def test_an_unclosed_tag_does_not_swallow_the_following_page():
    slides, _ = _parse(
        '<div class="slide"><p class="lede">One<div class="slide">'
        '<p class="lede">Two</p></div>')
    assert len(slides) >= 1


def test_an_end_tag_with_no_matching_open_tag_is_ignored():
    slides, _ = _parse('</span><div class="slide"><p>a</p></div>')
    assert len(slides) == 1


def test_self_closing_syntax_is_captured_as_an_element():
    slides, styles = _parse(
        '<div class="slide"><svg><line stroke-dasharray="2 2" /></svg></div>')
    assert _state(cdc.run_checks(slides, styles), "C11") == cdc.FAIL


def test_attribute_names_are_matched_case_insensitively():
    slides, _ = _parse('<div CLASS="slide"><p>a</p></div>')
    assert len(slides) == 1


def test_text_is_accumulated_across_sibling_nodes():
    slides, _ = _parse('<div class="slide"><p>alpha</p><p>beta</p></div>')
    txt = slides[0].all_text()
    assert "alpha" in txt and "beta" in txt


def test_whitespace_only_text_is_not_recorded():
    slides, _ = _parse('<div class="slide">   \n  <p>a</p></div>')
    assert slides[0].text == ""


# ==========================================================================
# THIRD BATCH, against the 20 mutants that survived the second.
#
# Includes the one fixture nothing else in this file provides: a deck on which
# ALL NINE checks execute. Without it `fully_covered: true` was a code path no
# test had ever reached, and the "a CANNOT_RUN is not a pass" note could be
# printed unconditionally with the suite still green.
# ==========================================================================

FULL_COVERAGE_DECK = """
<style>.slide{width:1280px}</style>
<div class="slide">
  <p class="lede">Widget output reached 1,200 units in March, more than plan</p>
  <div class="tracker">Output &middot; 1 of 1</div>
  <div class="rule"></div>
  <div class="sticker">AS OF MARCH</div>
  <div><h3>Units produced, <i>thousands</i></h3><svg viewBox="0 0 10 10"></svg></div>
  <ul><li>Output was 1,200 units</li><li>Plan was 900 units</li></ul>
  <div class="take"><p><b>Output beat plan</b> by 300 units, 1,200 / 1,500 = 80.0%.</p></div>
  <p class="src">Source: the March production log.</p>
</div>
"""


# B11 reads a register from frame.yaml, so "every check executes" needs one. The lede
# above prints "more than"; this is the claim that covers it.
FULL_COVERAGE_REGISTER = [{
    "surface": "slide-1", "token": "more than", "type": "comparison",
    "asserts": "March output exceeded the March plan",
    "population": "the one widget line, March",
    "backing": {"kind": "cell", "ref": "workbook Output!B4"}}]


def _full_coverage_ctx():
    return {"frame": {"facts": {}, "lede_claims": FULL_COVERAGE_REGISTER},
            "origin": "test frame", "problem": None}


def _with_fresh_render(tmp_path, html, name="deck.html"):
    """Write a deck AND a render newer than it, so R1 is satisfied.

    R1 reads the filesystem, so any CLI test of the other checks has to supply a
    render or it fails for a reason that has nothing to do with what it is testing."""
    deck = tmp_path / name
    deck.write_text(html, encoding="utf-8")
    png = tmp_path / (name.replace(".html", "") + "-render.png")
    png.write_bytes(b"x")
    os.utime(png, (time.time() + 10, time.time() + 10))
    return deck


def test_the_full_coverage_deck_executes_every_in_memory_check():
    slides, styles = _parse(FULL_COVERAGE_DECK)
    results = cdc.run_checks(slides, styles, frame_ctx=_full_coverage_ctx())
    assert "B11" in [r.rule for r in results]
    assert [r.rule for r in results if r.state == cdc.CANNOT_RUN] == []
    assert all(r.state == cdc.PASS for r in results), [
        (r.rule, r.state, r.detail) for r in results if r.state != cdc.PASS]


def test_fully_covered_is_true_only_when_nothing_was_skipped(tmp_path):
    full = _with_fresh_render(tmp_path, FULL_COVERAGE_DECK, "full.html")
    frame = _frame_file(tmp_path / "full-frame.yaml", FULL_COVERAGE_REGISTER)
    assert json.loads(_run_cli(full, "--json", "--frame", str(frame)).stdout)[
        "fully_covered"] is True
    # The same deck with no register is NOT fully covered: B11 could not run.
    assert json.loads(_run_cli(full, "--json").stdout)["fully_covered"] is False

    partial = _with_fresh_render(tmp_path, CLEAN_DECK, "clean.html")
    assert json.loads(_run_cli(partial, "--json").stdout)["fully_covered"] is False


def test_the_cannot_run_note_is_suppressed_when_every_check_ran(tmp_path):
    """Printed unconditionally the note stops carrying information."""
    full = _with_fresh_render(tmp_path, FULL_COVERAGE_DECK, "full.html")
    frame = _frame_file(tmp_path / "full-frame.yaml", FULL_COVERAGE_REGISTER)
    assert "CANNOT_RUN is not a pass" not in _run_cli(full, "--frame", str(frame)).stdout


def test_result_as_dict_carries_all_four_fields():
    r = cdc.Result("X1", cdc.FAIL, "because reasons", ["page 1: nope"])
    d = r.as_dict()
    assert d == {"rule": "X1", "state": cdc.FAIL,
                 "detail": "because reasons", "offenders": ["page 1: nope"]}


def test_json_results_carry_the_detail_for_every_rule(tmp_path):
    deck = tmp_path / "bad.html"
    deck.write_text(DEFECTIVE_DECK, encoding="utf-8")
    payload = json.loads(_run_cli(deck, "--json").stdout)
    assert len(payload["results"]) == 13
    for row in payload["results"]:
        assert row["detail"].strip()
        assert set(row) == {"rule", "state", "detail", "offenders"}


def test_an_empty_bold_element_is_not_counted_as_a_bold_run():
    """<b></b> is not emphasis. Counting it flips B7 from CANNOT_RUN to PASS and
    reports coverage the deck does not have."""
    slides, styles = _parse('<div class="slide"><p><b></b>plain text</p></div>')
    assert _state(cdc.run_checks(slides, styles), "B7") == cdc.CANNOT_RUN


def test_a_right_anchored_sticker_reports_one_offence_not_two():
    """The early continue exists so a single sticker does not collect both the
    right-anchor finding and the DOM-order finding."""
    slides, styles = _parse(
        '<div class="slide">'
        '<div class="sticker" style="position:absolute;right:20px">DRAFT</div>'
        '<div class="rule"></div></div>')
    off = _offenders(cdc.run_checks(slides, styles), "A4")
    assert len(off) == 1, off
    assert "right-anchored" in off[0]


def test_a_heading_that_is_both_chart_title_class_and_beside_a_chart_counts_once():
    slides, styles = _parse(
        '<div class="slide"><div>'
        '<h3 class="chart-title">Units produced</h3><svg></svg>'
        "</div></div>")
    off = _offenders(cdc.run_checks(slides, styles), "C2")
    assert len(off) == 1, off


def test_convention_report_is_absent_from_json_unless_requested(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    assert "convention_report" not in json.loads(_run_cli(deck, "--json").stdout)
    assert "convention_report" in json.loads(
        _run_cli(deck, "--json", "--convention-report").stdout)


def test_convention_report_text_mode_prints_the_per_page_counts(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    out = _run_cli(deck, "--convention-report").stdout
    assert '"page": 1' in out
    assert '"tracker": 1' in out
    assert '"charts": 1' in out


def test_the_text_report_ends_with_a_blank_line(tmp_path):
    """Trailing spacing, so consecutive runs in one terminal stay separable."""
    deck = tmp_path / "clean.html"
    deck.write_text(CLEAN_DECK, encoding="utf-8")
    assert _run_cli(deck).stdout.endswith("\n\n")


# ==========================================================================
# FOURTH BATCH: R1 (render freshness), C13 (printed ratios), D1 (dashes).
#
# These three were added 2026-09-17 to make the gate enforceable rather than
# advisory. R1 is the only MECHANICAL proxy for the render-first rule: an image
# newer than the source either exists or it does not.
#
# NOTE ON THE FIXTURES BELOW: every one verifies its own precondition before
# asserting. A test that injects a defect and does not confirm the injection
# landed passes for the wrong reason -- that happened three times on the day
# these checks were written, including once on D1 itself, where a replace()
# against a missing anchor silently did nothing and a working check looked broken.
# ==========================================================================

import os
import time

EM = "—"
EN = "–"


def _deck_on_disk(tmp_path, html=None, name="deck.html"):
    p = tmp_path / name
    p.write_text(html if html is not None else CLEAN_DECK, encoding="utf-8")
    return p


def _r1(deck_path):
    return cdc.check_r1(str(deck_path))


# --- R1: render freshness -------------------------------------------------

def test_r1_fails_when_no_render_exists_at_all(tmp_path):
    deck = _deck_on_disk(tmp_path)
    r = _r1(deck)
    assert r.state == cdc.FAIL
    assert "Nobody has looked at this page" in r.detail


def test_r1_passes_when_a_render_is_newer_than_the_source(tmp_path):
    deck = _deck_on_disk(tmp_path)
    png = tmp_path / "p1.png"
    png.write_bytes(b"not really a png")
    os.utime(png, (time.time() + 10, time.time() + 10))
    r = _r1(deck)
    assert r.state == cdc.PASS, r.detail
    assert "p1.png" in r.detail


def test_r1_fails_when_every_render_is_older_than_the_source(tmp_path):
    """THE DEFECT THIS EXISTS FOR: a render happened once, then the page changed."""
    deck = _deck_on_disk(tmp_path)
    png = tmp_path / "stale.png"
    png.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(png, (old, old))
    r = _r1(deck)
    assert r.state == cdc.FAIL
    assert "older than the deck source" in r.detail
    assert "stale.png" in " ".join(r.offenders)


def test_r1_reports_how_stale_the_newest_render_is(tmp_path):
    deck = _deck_on_disk(tmp_path)
    png = tmp_path / "stale.png"
    png.write_bytes(b"x")
    old = time.time() - 7200          # two hours
    os.utime(png, (old, old))
    r = _r1(deck)
    assert "120 minutes" in r.detail or "119 minutes" in r.detail, r.detail


def test_r1_finds_a_render_in_a_subdirectory(tmp_path):
    """Renders usually live in a slides/ subfolder beside the deck."""
    deck = _deck_on_disk(tmp_path)
    sub = tmp_path / "slides"
    sub.mkdir()
    png = sub / "p1.png"
    png.write_bytes(b"x")
    os.utime(png, (time.time() + 10, time.time() + 10))
    assert _r1(deck).state == cdc.PASS


def test_r1_accepts_a_pdf_as_a_render(tmp_path):
    deck = _deck_on_disk(tmp_path)
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-")
    os.utime(pdf, (time.time() + 10, time.time() + 10))
    assert _r1(deck).state == cdc.PASS


def test_r1_does_not_accept_an_arbitrary_sibling_file_as_a_render(tmp_path):
    """A notes.md newer than the deck is not somebody having looked at the page."""
    deck = _deck_on_disk(tmp_path)
    other = tmp_path / "notes.md"
    other.write_text("not a render", encoding="utf-8")
    os.utime(other, (time.time() + 10, time.time() + 10))
    assert _r1(deck).state == cdc.FAIL


def test_r1_cannot_run_when_the_deck_path_is_not_stat_able(tmp_path):
    r = _r1(tmp_path / "does-not-exist.html")
    assert r.state == cdc.CANNOT_RUN


def test_r1_is_absent_when_no_deck_path_is_supplied():
    """run_checks is callable without a path, for in-memory fixtures."""
    slides, styles = _parse(CLEAN_DECK)
    assert "R1" not in [r.rule for r in cdc.run_checks(slides, styles)]


def test_r1_is_present_when_a_deck_path_is_supplied(tmp_path):
    deck = _deck_on_disk(tmp_path)
    slides, styles = _parse(CLEAN_DECK)
    rules = [r.rule for r in cdc.run_checks(slides, styles, deck_path=str(deck))]
    assert rules[0] == "R1"


# --- C13: printed ratios recompute ---------------------------------------

def _c13(body):
    slides, styles = _parse('<div class="slide">' + body + "</div>")
    return cdc.check_c13(slides)


def test_c13_passes_on_a_ratio_that_recomputes():
    r = _c13("<p>1,369 / 1,931 = 70.9%</p>")
    assert r.state == cdc.PASS, r.detail


def test_c13_fails_on_a_ratio_that_does_not():
    r = _c13("<p>1,369 / 1,931 = 88.8%</p>")
    assert r.state == cdc.FAIL
    assert "70.9" in " ".join(r.offenders), r.offenders


def test_c13_accepts_correct_rounding_at_one_decimal():
    """1,369/1,931 = 70.8907..., so 70.9 is right and 70.8 is not."""
    assert _c13("<p>1,369 / 1,931 = 70.9%</p>").state == cdc.PASS
    assert _c13("<p>1,369 / 1,931 = 70.8%</p>").state == cdc.FAIL


def test_c13_tolerance_follows_the_printed_precision():
    """71% is a correct whole-number rounding of 70.89; 70% is not."""
    assert _c13("<p>1,369 / 1,931 = 71%</p>").state == cdc.PASS
    assert _c13("<p>1,369 / 1,931 = 70%</p>").state == cdc.FAIL


def test_c13_handles_commas_in_both_operands():
    assert _c13("<p>10,000 / 40,000 = 25.0%</p>").state == cdc.PASS


def test_c13_flags_division_by_zero_rather_than_crashing():
    r = _c13("<p>5 / 0 = 100.0%</p>")
    assert r.state == cdc.FAIL
    assert "zero" in " ".join(r.offenders).lower()


def test_c13_checks_every_ratio_not_only_the_first():
    r = _c13("<p>1 / 2 = 50.0%</p><p>1 / 4 = 99.0%</p>")
    assert r.state == cdc.FAIL
    assert len(r.offenders) == 1


def test_c13_cannot_run_when_no_ratio_is_printed_in_that_form():
    r = _c13("<p>The line booked 1,200 of 1,500 units, or 80.0%.</p>")
    assert r.state == cdc.CANNOT_RUN
    assert "still applies" in r.detail


def test_c13_counts_what_it_checked():
    r = _c13("<p>1 / 2 = 50.0%</p><p>1 / 4 = 25.0%</p>")
    assert r.state == cdc.PASS
    assert "2" in r.detail


# --- D1: no em or en dashes in sent text ---------------------------------

def _d1(body):
    slides, styles = _parse('<div class="slide">' + body + "</div>")
    return cdc.check_d1(slides)


def test_d1_passes_on_clean_text():
    assert _d1("<p>Plain text, with commas and periods.</p>").state == cdc.PASS


def test_d1_fails_on_an_em_dash():
    r = _d1("<p>Ribbon widths " + EM + " true to count.</p>")
    assert r.state == cdc.FAIL
    assert "em dash" in " ".join(r.offenders)


def test_d1_fails_on_an_en_dash():
    r = _d1("<p>Pages 3 " + EN + " 5.</p>")
    assert r.state == cdc.FAIL
    assert "en dash" in " ".join(r.offenders)


def test_d1_names_the_page_and_quotes_the_sentence():
    r = _d1("<p>Ribbon widths " + EM + " true to count.</p>")
    off = " ".join(r.offenders)
    assert "page 1" in off
    assert "Ribbon widths" in off


def test_d1_truncates_a_long_sentence_in_the_offender():
    long_txt = "word " * 40 + EM + " end"
    off = " ".join(_d1("<p>" + long_txt + "</p>").offenders)
    assert "..." in off


def test_d1_ignores_a_dash_inside_a_style_block():
    """THE MIRROR OF C11. C11 scans style surfaces and never text; D1 scans text and
    never style. A dash in CSS is not a dash the reader sees."""
    deck = ("<style>/* spacing " + EM + " see spec */ .slide{width:10px}</style>"
            '<div class="slide"><p>Clean prose.</p></div>')
    slides, styles = _parse(deck)
    assert cdc.check_d1(slides).state == cdc.PASS


def test_d1_ignores_a_dash_inside_an_attribute_value():
    r = _d1('<p title="tooltip ' + EM + ' here">Clean prose.</p>')
    assert r.state == cdc.PASS


def test_d1_finds_a_dash_inside_an_svg_text_node():
    """Most numerals on these decks live in SVG, so text-node scanning must reach it."""
    r = _d1("<svg><text>1,931 " + EM + " booking opportunities</text></svg>")
    assert r.state == cdc.FAIL


def test_d1_reports_every_dash_not_only_the_first():
    r = _d1("<p>a " + EM + " b</p><p>c " + EN + " d</p>")
    assert len(r.offenders) == 2


def test_a_hyphen_is_not_a_dash():
    assert _d1("<p>tech ETAs, follow-ups and re-scheduling.</p>").state == cdc.PASS


def test_d1_does_not_truncate_a_short_sentence():
    """The other half of the truncation branch. Without this, forcing the length
    guard always-true is invisible: every offender would be clipped and the
    long-sentence test above would still pass."""
    r = _d1("<p>Ribbon widths " + EM + " true to count.</p>")
    off = " ".join(r.offenders)
    assert "true to count." in off
    assert "..." not in off


# --- B11: the lede-claim register ----------------------------------------
#
# A gate that asserts NUMBERS says nothing about the CLAIM the numbers sit inside. B4
# checks that a title number is on the page; B11 checks that a comparative, superlative
# or quantifier in the title has a registered, typed claim naming what backs it.
#
# Every fixture is an invented widget line. The register lives in frame.yaml under
# `lede_claims`, so these tests hand the check a frame dict.

_NO_REGISTER = object()


def _ctx(register=_NO_REGISTER, facts=None):
    frame = {"facts": facts or {}}
    if register is not _NO_REGISTER:
        frame["lede_claims"] = register
    return {"frame": frame, "origin": "test frame", "problem": None}


def _b11(lede, register=_NO_REGISTER, facts=None, body=""):
    slides, _ = _parse('<div class="slide"><p class="lede">' + lede + "</p>"
                       + body + "</div>")
    return cdc.check_b11(slides, _ctx(register, facts))


def _claim(token, ctype="comparison", surface="slide-1", **over):
    row = {"surface": surface, "token": token, "type": ctype,
           "asserts": "Line A against Line B, March, all shifts",
           "population": "both lines, every March shift",
           "backing": {"kind": "cell", "ref": "workbook Lines!D14"}}
    row.update(over)
    return row


@pytest.mark.parametrize("lede,token", [
    ("Line A is faster on every shift measure", "faster"),
    ("Line A ships more than Line B", "more"),
    ("Line A is the largest source of scrap", "largest"),
    ("Tuesday is the worst day for scrap", "worst"),
    ("Line A never misses plan", "never"),
    ("Both lines earn the same margin", "same"),
    ("The new press replaced the old one", "replaced"),
    ("Line A is the nearest to plan", "nearest"),        # generic -est, not in the list
    ("Line A is pricier than Line B", "pricier"),        # generic word-before-than
    ("Line A scraps 3x the units of Line B", "3x"),
    ("Nobody has measured Line B scrap", "nobody"),
])
def test_b11_fails_a_trigger_token_with_no_registered_claim(lede, token):
    r = _b11(lede, register=[])
    assert r.state == cdc.FAIL, r.detail
    assert any('"' + token + '"' in o for o in r.offenders), r.offenders


def test_b11_does_not_flag_the_noun_that_happens_to_precede_than():
    """ONE CLAIM, ONE TOKEN. A first draft flagged any word before "than", which on
    real decks reported the noun in "300 more units than" beside the "more" that
    already carries the claim, so one sentence demanded two entries."""
    r = _b11("Line A ships 300 more units than Line B", register=[])
    assert r.state == cdc.FAIL
    assert len(r.offenders) == 1 and '"more"' in r.offenders[0], r.offenders


def test_b11_passes_the_same_lede_once_the_claim_is_registered_typed_and_backed():
    lede = "Line A is faster than Line B"
    assert _b11(lede, register=[]).state == cdc.FAIL
    r = _b11(lede, register=[_claim("faster")])
    assert r.state == cdc.PASS, (r.detail, r.offenders)


def test_b11_accepts_a_phrase_token_that_contains_the_trigger_word():
    r = _b11("Line A ships more than Line B", register=[_claim("more than")])
    assert r.state == cdc.PASS, (r.detail, r.offenders)


def test_b11_cannot_run_without_a_frame():
    slides, _ = _parse('<div class="slide"><p class="lede">Line A is the worst</p></div>')
    r = cdc.check_b11(slides, None)
    assert r.state == cdc.CANNOT_RUN
    assert "frame" in r.detail


def test_b11_cannot_run_when_the_frame_declares_no_register_and_says_what_went_unchecked():
    """The tool is shared: a deck whose frame predates the register is NOT failed for
    it. But the unchecked tokens are counted, so the CANNOT_RUN is not silent."""
    r = _b11("Line A is the worst and the largest")
    assert r.state == cdc.CANNOT_RUN
    assert "lede_claims" in r.detail
    assert "2 trigger token" in r.detail


def test_b11_an_empty_register_is_a_declared_register_and_fails_an_unbacked_token():
    """`lede_claims: []` says "this deck makes no such claims". Absent says nothing.
    Collapsing the two would let an opted-in deck go quiet by deleting its entries."""
    assert _b11("Line A is the worst", register=[]).state == cdc.FAIL


def test_b11_cannot_run_when_no_lede_element_exists():
    slides, _ = _parse('<div class="slide"><p>Line A is the worst</p></div>')
    r = cdc.check_b11(slides, _ctx([]))
    assert r.state == cdc.CANNOT_RUN
    assert "lede convention" in r.detail


def test_b11_cannot_run_rather_than_pass_when_it_measured_nothing():
    """No trigger token and no entry: a green result here would be the vacuous pass."""
    r = _b11("Line A output reached 1,200 units in March", register=[])
    assert r.state == cdc.CANNOT_RUN
    assert "no trigger token" in r.detail


@pytest.mark.parametrize("lede", [
    "Customer interest in the tester request grew",   # -er and -est nouns
    "Line A shipped at least 1,200 units",            # "at least" is a bound
    "Line A no longer runs a night shift",            # "no longer" is not a comparison
    "Line A runs days rather than nights",            # "rather than"
    "Overall output almost reached plan",             # all/most inside longer words
    "The harvest contest in the Midwest drew a modest crowd",
])
def test_b11_false_positive_floor(lede):
    r = _b11(lede, register=[])
    assert r.state == cdc.CANNOT_RUN, (r.detail, r.offenders)


def test_b11_reads_the_lede_only_and_not_the_body():
    r = _b11("Line A output reached 1,200 units", register=[],
             body="<p>Line A is the worst and the largest, faster than every other.</p>")
    assert r.state == cdc.CANNOT_RUN, r.offenders


# -- the two documented false-positive cases: a quotation and a proper noun --

def test_b11_a_quoted_comparative_still_fails_and_the_offender_says_how_to_register_it():
    """NOT exempted mechanically. Scare quotes around the claim would then be a free
    bypass. A quotation is a claim someone made, so it is registered as `attribution`."""
    r = _b11('The plant manager calls Line A "the best line in the region"', register=[])
    assert r.state == cdc.FAIL
    assert any("attribution" in o for o in r.offenders), r.offenders


def test_b11_a_quoted_comparative_passes_as_an_attribution_naming_its_source():
    r = _b11('The plant manager calls Line A "the best line in the region"',
             register=[{"surface": "slide-1", "token": "best", "type": "attribution",
                        "asserts": "the plant manager said this, verbatim",
                        "backing": {"kind": "check", "ref": "interview notes, line 40"}}])
    assert r.state == cdc.PASS, (r.detail, r.offenders)


def test_b11_a_proper_noun_fails_until_declared_not_a_claim_with_a_reason():
    lede = "Best Widget Co supplies 1,200 units"
    r = _b11(lede, register=[])
    assert r.state == cdc.FAIL
    assert any("not_a_claim" in o for o in r.offenders), r.offenders
    ok = _b11(lede, register=[{"surface": "slide-1", "token": "Best",
                               "type": "not_a_claim",
                               "reason": "part of the supplier's registered name"}])
    assert ok.state == cdc.PASS, (ok.detail, ok.offenders)
    assert "1 declared not a claim" in ok.detail


def test_b11_not_a_claim_without_a_reason_fails():
    r = _b11("Best Widget Co supplies 1,200 units",
             register=[{"surface": "slide-1", "token": "Best", "type": "not_a_claim"}])
    assert r.state == cdc.FAIL
    assert any("reason" in o for o in r.offenders), r.offenders


# -- the register's own shape: a wrong shape is a FAIL, never a quiet CANNOT_RUN --

def test_b11_fails_a_register_that_is_not_a_list():
    r = _b11("Line A is the worst", register={"slide-1": "worst"})
    assert r.state == cdc.FAIL
    assert "list" in r.detail


@pytest.mark.parametrize("broken,needle", [
    ({"type": None}, "type"),
    ({"type": "vibes"}, "type"),
    ({"backing": None}, "backing"),
    ({"backing": "the workbook"}, "backing"),
    ({"backing": {"kind": "cell", "ref": ""}}, "ref"),
    ({"backing": {"kind": "hunch", "ref": "x"}}, "kind"),
    ({"asserts": ""}, "asserts"),
    ({"population": ""}, "population"),
    ({"surface": None}, "surface"),
])
def test_b11_fails_an_untyped_or_unbacked_entry(broken, needle):
    r = _b11("Line A is the worst line", register=[_claim("worst", "superlative", **broken)])
    assert r.state == cdc.FAIL, r.detail
    assert any(needle in o for o in r.offenders), r.offenders


def test_b11_fails_an_entry_that_is_not_a_mapping():
    r = _b11("Line A is the worst line", register=["worst"])
    assert r.state == cdc.FAIL
    assert any("mapping" in o for o in r.offenders), r.offenders


def test_b11_population_is_not_demanded_of_a_causal_or_attribution_claim():
    row = _claim("replaced", "causal")
    del row["population"]
    r = _b11("The new press replaced the old one", register=[row])
    assert r.state == cdc.PASS, (r.detail, r.offenders)


def test_b11_a_fact_backing_must_resolve_in_the_frame():
    row = _claim("worst", "superlative", backing={"kind": "fact", "ref": "f9"})
    lede = "Tuesday is the worst day for scrap"
    bad = _b11(lede, register=[row], facts={"f1": {"text": "x"}})
    assert bad.state == cdc.FAIL
    assert any("f9" in o for o in bad.offenders), bad.offenders
    good = _b11(lede, register=[row], facts={"f9": {"text": "x"}})
    assert good.state == cdc.PASS, (good.detail, good.offenders)


def test_b11_fails_a_stale_entry_whose_token_the_lede_no_longer_prints():
    r = _b11("Line A is faster than Line B",
             register=[_claim("faster"), _claim("largest", "superlative")])
    assert r.state == cdc.FAIL
    assert any("largest" in o and "does not print" in o for o in r.offenders), r.offenders


def test_b11_fails_an_entry_naming_a_surface_the_deck_does_not_render():
    r = _b11("Line A is faster than Line B",
             register=[_claim("faster"), _claim("faster", surface="slide-7")])
    assert r.state == cdc.FAIL
    assert any("slide-7" in o for o in r.offenders), r.offenders


def test_b11_an_entry_on_another_surface_does_not_cover_this_one():
    deck = ('<div class="slide"><p class="lede">Line A is faster than Line B</p></div>'
            '<div class="slide"><p class="lede">Line B is faster than Line C</p></div>')
    slides, _ = _parse(deck)
    one = cdc.check_b11(slides, _ctx([_claim("faster", surface="slide-2")]))
    assert one.state == cdc.FAIL
    assert any(o.startswith("page 1") for o in one.offenders), one.offenders
    both = cdc.check_b11(slides, _ctx([_claim("faster"),
                                       _claim("faster", surface="slide-2")]))
    assert both.state == cdc.PASS, (both.detail, both.offenders)


def test_b11_one_entry_covers_one_occurrence():
    lede = "Line A ships more units and earns more margin"
    assert _b11(lede, register=[_claim("more")]).state == cdc.FAIL
    assert _b11(lede, register=[_claim("more"), _claim("more")]).state == cdc.PASS


def test_b11_an_entry_covers_only_its_own_token():
    """An entry for one word must not discharge a different word in the same title."""
    lede = "Line A is faster and ships more units"
    r = _b11(lede, register=[_claim("faster"), _claim("faster")])
    assert r.state == cdc.FAIL
    assert any('"more"' in o for o in r.offenders), r.offenders


def test_b11_token_match_ignores_case():
    r = _b11("6pm, THE WORST HOUR for scrap",
             register=[_claim("worst", "superlative")])
    assert r.state == cdc.PASS, (r.detail, r.offenders)


def test_b11_pass_states_what_it_measured_and_what_it_cannot_see():
    """A lexicon run measures the lexicon. The PASS must say so, every time."""
    r = _b11("Line A is faster than Line B", register=[_claim("faster")])
    assert r.state == cdc.PASS
    assert "1 trigger token" in r.detail
    assert "no trigger word" in r.detail
    assert "declared, not verified" in r.detail


# -- wiring: where the frame comes from --

def _frame_file(path, register):
    import yaml
    path.write_text(yaml.safe_dump({"schema_version": 3, "facts": {},
                                    "lede_claims": register}), encoding="utf-8")
    return path


COMPARATIVE_DECK = CLEAN_DECK.replace(
    "Scrap fell to 40 units", "Scrap fell to 40 units, the lowest of any month")


def _b11_row(tmp_path, *extra, name="deck.html"):
    deck = _with_fresh_render(tmp_path, COMPARATIVE_DECK, name)
    r = _run_cli(deck, "--json", *extra)
    payload = json.loads(r.stdout)
    return r, next(x for x in payload["results"] if x["rule"] == "B11")


def test_cli_b11_cannot_run_and_exits_0_when_no_frame_sits_beside_the_deck(tmp_path):
    r, row = _b11_row(tmp_path)
    assert row["state"] == cdc.CANNOT_RUN
    assert r.returncode == 0


def test_cli_b11_reads_a_frame_passed_with_the_flag(tmp_path):
    frame = _frame_file(tmp_path / "elsewhere.yaml", [])
    r, row = _b11_row(tmp_path, "--frame", str(frame))
    assert row["state"] == cdc.FAIL
    assert r.returncode == 2
    assert any('"lowest"' in o for o in row["offenders"]), row["offenders"]


def test_cli_b11_finds_frame_yaml_beside_the_deck_without_the_flag(tmp_path):
    """The gate must not depend on someone remembering a flag."""
    _frame_file(tmp_path / "frame.yaml", [])
    r, row = _b11_row(tmp_path)
    assert row["state"] == cdc.FAIL
    assert "frame.yaml" in row["detail"]


def test_cli_b11_passes_with_a_registered_claim_in_the_frame(tmp_path):
    _frame_file(tmp_path / "frame.yaml",
                [_claim("lowest", "superlative", surface="slide-2")])
    r, row = _b11_row(tmp_path)
    assert row["state"] == cdc.PASS, row
    assert r.returncode == 0


def test_cli_b11_fails_when_a_named_frame_cannot_be_read(tmp_path):
    """An explicit --frame that does not load is a broken request, not a skipped check."""
    r, row = _b11_row(tmp_path, "--frame", str(tmp_path / "nope.yaml"))
    assert row["state"] == cdc.FAIL
    assert r.returncode == 2


def test_cli_b11_fails_on_a_frame_that_is_not_valid_yaml(tmp_path):
    (tmp_path / "frame.yaml").write_text("lede_claims: [unclosed", encoding="utf-8")
    r, row = _b11_row(tmp_path)
    assert row["state"] == cdc.FAIL


def test_the_schema_declares_the_register_and_agrees_with_the_checker_on_types():
    """ONE SOURCE OF TRUTH, CHECKED. The schema documents the claim types and the
    checker enforces them; if they drift, an author following the schema writes a
    register the checker refuses."""
    import yaml
    schema = yaml.safe_load(
        (REPO / "framework" / "frame-schema.yaml").read_text(encoding="utf-8"))
    field = schema["fields"]["lede_claims"]
    assert field["type"].startswith("list[")
    assert set(field["claim_types"]) == set(cdc.CLAIM_TYPES) | {cdc.NOT_A_CLAIM}
    assert set(field["backing_kinds"]) == set(cdc.BACKING_KINDS)


# -- B11 mutation survivors, 2026-10-13 --

def test_cli_b11_says_why_it_cannot_run_when_no_frame_exists(tmp_path):
    r, row = _b11_row(tmp_path)
    assert row["detail"].startswith("no --frame supplied and no frame.yaml beside the deck")


def test_cli_b11_fails_on_a_frame_that_is_not_a_mapping(tmp_path):
    (tmp_path / "frame.yaml").write_text("- one\n- two\n", encoding="utf-8")
    r, row = _b11_row(tmp_path)
    assert row["state"] == cdc.FAIL and r.returncode == 2
    assert "is not a mapping" in row["detail"]


def test_frame_context_reports_a_missing_yaml_reader_as_cannot_run(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real(name, *a, **k)
    (tmp_path / "frame.yaml").write_text("lede_claims: []\n", encoding="utf-8")
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    ctx = cdc.frame_context(tmp_path / "deck.html")
    assert ctx["frame"] is None
    assert ctx["problem"][0] == cdc.CANNOT_RUN and "PyYAML is not installed" in ctx["problem"][1]


def test_b11_a_stale_phrase_entry_does_not_cover_the_word_the_lede_still_prints():
    """The entry says "far faster"; the lede says "faster". The entry is stale AND the
    printed word is uncovered: two defects, not one."""
    r = _b11("Line A is faster than Line B", register=[_claim("far faster")])
    assert r.state == cdc.FAIL
    assert r.detail.startswith("2 defect(s) across 1 trigger token(s) and 1 register entry (")
    assert any("does not print it" in o for o in r.offenders)
    assert any("has no registered claim" in o for o in r.offenders)


def test_b11_counts_register_entries_in_the_plural_when_there_are_several():
    r = _b11("Line A is faster than Line B",
             register=[_claim("far faster"), _claim("much faster")])
    assert "and 2 register entries (" in r.detail


def test_b11_pass_counts_claims_and_names_apart():
    r = _b11("Line A is faster than Line B", register=[_claim("faster")])
    assert "1 typed and backed, 0 declared not a claim" in r.detail
    r = _b11("The Faster Press line met plan",
             register=[_claim("faster", "not_a_claim", asserts="part of the product name",
                              reason="Faster Press is the product's name")])
    assert r.state == cdc.PASS, (r.detail, r.offenders)
    assert "0 typed and backed, 1 declared not a claim" in r.detail


# -- cross-model review 307: comparison verbs --

@pytest.mark.parametrize("lede,token", [
    ("Line A outperforms Line B and is faster in March", "outperforms"),
    ("Line B underperformed plan in March", "underperformed"),
    ("Scrap exceeded the March target", "exceeded"),
    ("Line A surpasses Line B on units shipped", "surpasses"),
    ("Line A is outpacing the plan", "outpacing"),
    ("Line A dominates March output", "dominates"),
    ("March set the maximum scrap rate", "maximum"),
    ("Line B hit its minimum output in March", "minimum"),
    ("Line A is the dominant source of scrap", "dominant"),
])
def test_b11_sees_a_comparison_made_with_a_verb(lede, token):
    r = _b11(lede, register=[_claim("faster")] if "faster" in lede else [])
    assert r.state == cdc.FAIL, r.detail
    assert any('"' + token + '"' in o for o in r.offenders), r.offenders
