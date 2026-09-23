"""Tests for tools/check_deck_geometry.py, the G1-G8 render gate.

FIXTURES ARE SYNTHETIC ON PURPOSE. This file is a public artifact; the decks the checker
was built against carry real client figures and company names, so none of that appears
here. Every fixture is an invented deck about a fictional widget line.

TWO LAYERS, AND BOTH ARE NECESSARY.

  1. RULE TESTS over a hand-built geometry payload. The eight rules are pure functions of
     the probe's output, so they can be tested without a browser: fast, deterministic, and
     able to construct defects that are awkward to produce in real CSS.

  2. LIVE SMOKE TESTS that actually render HTML in Chrome. Layer 1 can only ever prove the
     rules agree with my mental model of what the probe returns. If the PROBE is wrong --
     and the probe is where this gate's three worst bugs lived, all three of them silent --
     layer 1 stays green while the gate measures nothing. tools/HOOK_AUTHORING.md requires
     a live smoke for exactly this reason. They skip when no browser is present, and a skip
     is visible in the run.

THE LOAD-BEARING ASSERTIONS ARE THE FAILURES. For each rule, "passes on a good deck" is
nearly worthless on its own: a rule that returns PASS unconditionally satisfies it. What
matters is that the rule FAILS on a deck carrying the defect, and reports CANNOT_RUN rather
than PASS when it has nothing to measure. Three regression tests at the bottom pin bugs
that were green at the time they shipped.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import check_deck_geometry as g  # noqa: E402
from check_deck_craft import CANNOT_RUN, FAIL, PASS  # noqa: E402


# --------------------------------------------------------------------------
# a hand-built geometry payload, shaped exactly like the probe's output
# --------------------------------------------------------------------------

def node(label="div", l=0, t=0, w=100, hh=20, *, leaf=True, parent=None, pid="p1",
         ord_=0, font=16, family="Test Sans", lh=0, own="", has=None,
         right_visible=False, is_box=False, pad=None, lines=1, content_h=None,
         top_slack=None, trim=0, svg_slack=None, absolute=0, furniture=0,
         letterbox=None, accent_only=0, accent_left=1):
    """One probe node. Defaults describe a plain, well-behaved block."""
    box = {"l": l, "t": t, "r": l + w, "b": t + hh, "w": w, "h": hh}
    return {
        "box": box, "label": label, "tag": "div", "leaf": leaf,
        "parent": parent, "pid": pid, "ord": ord_,
        "rightVisible": right_visible, "isBox": 1 if is_box else 0,
        "fontSize": font, "fontFamily": family, "lineHeight": lh,
        "ownText": own, "hasText": len(own) if has is None else has,
        "lineCount": lines,
        "contentH": hh if content_h is None else content_h,
        "topSlack": top_slack, "trimDeclared": trim,
        "padded": pad or {"t": 0, "b": 0, "l": 0, "r": 0},
        "svgSlack": svg_slack, "absolute": absolute, "furniture": furniture,
        "svgLetterbox": letterbox, "accentOnly": accent_only, "accentLeft": accent_left,
    }


def page(nodes, src_top=600, origin=0, num=1):
    return {"page": num, "originX": origin,
            "src": {"l": 0, "t": src_top, "r": 1176, "b": src_top + 40,
                    "w": 1176, "h": 40},
            "slide": {"l": 0, "t": 0, "r": 1280, "b": 720, "w": 1280, "h": 720},
            "nodes": nodes}


# --------------------------------------------------------------------------
# off_grid
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, 0), (4, 0), (8, 0), (-4, 0), (1176, 0),
    (1, 1), (2, 2), (3, 1), (5, 1), (6, 2),
    (0.4, 0.4),
])
def test_off_grid_distance_to_nearest_multiple(value, expected):
    assert g.off_grid(value) == pytest.approx(expected)


def test_off_grid_uses_the_module_constant_not_a_literal_four(monkeypatch):
    """If GRID were ignored the rule could never be retuned, and a test that only ever
    exercises 4 would not notice."""
    monkeypatch.setattr(g, "GRID", 5)
    assert g.off_grid(10) == 0
    assert g.off_grid(4) == 1


# --------------------------------------------------------------------------
# G1 clearance
# --------------------------------------------------------------------------

def test_g1_passes_when_every_leaf_clears_the_source_line():
    r = g.check_g1([page([node(t=0, hh=100)], src_top=600)])
    assert r.state == PASS


def test_g1_fails_on_overlap():
    r = g.check_g1([page([node(t=560, hh=60)], src_top=600)])   # bottom 620 > 600
    assert r.state == FAIL


def test_g1_fails_at_exactly_zero_clearance():
    """Zero is the defect this rule was built for: slide 1 sat at exactly 0, not
    overlapping, and one word of copy from overlapping."""
    r = g.check_g1([page([node(t=500, hh=100)], src_top=600)])
    assert r.state == FAIL
    assert "0px clearance" in r.offenders[0]


def test_g1_fails_just_inside_the_minimum_and_passes_just_outside():
    tight = g.check_g1([page([node(t=0, hh=600 - g.MIN_CLEARANCE + 1)], src_top=600)])
    ok = g.check_g1([page([node(t=0, hh=600 - g.MIN_CLEARANCE)], src_top=600)])
    assert tight.state == FAIL
    assert ok.state == PASS


def test_g1_ignores_containers_and_only_judges_leaves():
    """A container's measured height is not its contents' extent. Measuring containers is
    the bug that reported '+5px clear' on a box visibly crossing the line.

    And a page of nothing BUT containers is CANNOT_RUN, not PASS: judging zero elements
    and reporting green is the defect this file has now shipped five separate times."""
    r = g.check_g1([page([node(t=560, hh=60, leaf=False)], src_top=600)])
    assert r.state == CANNOT_RUN
    assert "no leaf element was judged" in r.detail


def test_g1_ignores_the_source_line_and_page_number_as_furniture():
    """The source line cannot crowd itself. But FURNITURE is a named thing -- .src, .pg,
    .tracker -- not "anything absolutely positioned", which would let real content opt out
    of four rules by changing one CSS property."""
    r = g.check_g1([page([node(t=0, hh=100), node(t=560, hh=60, furniture=1)],
                         src_top=600)])
    assert r.state == PASS


def test_g1_still_judges_absolutely_positioned_CONTENT():
    r = g.check_g1([page([node(t=560, hh=60, absolute=1, furniture=0)], src_top=600)])
    assert r.state == FAIL


def test_g1_fails_a_deck_with_no_source_line():
    """Every page needs a source (Nick, 2026-09-23). A deck with none is a failure of
    the convention, not an absence of something to measure."""
    p = page([node()])
    p["src"] = None
    r = g.check_g1([p])
    assert r.state == FAIL
    assert "page 1" in r.offenders[0] and "no source line" in r.offenders[0]


def test_g1_fails_the_page_without_a_source_even_when_another_page_passes():
    """108.F2 (P0). Pages without a .src were dropped, so a second page's clean
    measurement certified source clearance on a page G1 never judged."""
    good = page([node(t=100, hh=20)], num=1)
    bare = page([node(t=100, hh=20)], num=2)
    bare["src"] = None
    r = g.check_g1([good, bare])
    assert r.state == FAIL
    assert any("page 2" in o and "no source line" in o for o in r.offenders)
    assert not any("page 1" in o for o in r.offenders)


# --------------------------------------------------------------------------
# G2 containment
# --------------------------------------------------------------------------

PARENT = {"l": 0, "t": 0, "r": 400, "b": 300}


def test_g2_passes_when_contained():
    r = g.check_g2([page([node(l=10, t=10, w=100, hh=50, parent=PARENT)])])
    assert r.state == PASS


@pytest.mark.parametrize("kw,side", [
    (dict(l=10, w=500), "right"),
    (dict(l=-20, w=100), "left"),
    (dict(t=10, hh=400), "bottom"),
])
def test_g2_fails_on_each_escape_direction(kw, side):
    base = dict(l=10, t=10, w=100, hh=50, parent=PARENT)
    base.update(kw)
    r = g.check_g2([page([node(**base)])])
    assert r.state == FAIL
    assert side in r.offenders[0]


def test_g2_tolerates_a_subpixel_escape_but_not_a_real_one():
    near = g.check_g2([page([node(l=0, t=0, w=400 + g.CONTAIN_TOL, hh=10, parent=PARENT)])])
    real = g.check_g2([page([node(l=0, t=0, w=400 + g.CONTAIN_TOL + 1, hh=10,
                                  parent=PARENT)])])
    assert near.state == PASS
    assert real.state == FAIL


def test_g2_cannot_run_when_nothing_has_a_parent():
    r = g.check_g2([page([node(parent=None)])])
    assert r.state == CANNOT_RUN


# --------------------------------------------------------------------------
# G3 alignment
# --------------------------------------------------------------------------

def test_g3_passes_on_grid_aligned_left_edges():
    r = g.check_g3([page([node(l=0, w=100), node(l=16, w=100), node(l=1176, w=0)])])
    assert r.state == PASS


def test_g3_fails_on_an_off_grid_left_edge():
    r = g.check_g3([page([node(l=15, w=100)])])
    assert r.state == FAIL
    assert "left edge" in r.offenders[0]


def test_g3_measures_from_the_content_origin_not_the_viewport():
    """An element at x=52 on a page whose content starts at 52 is at 0, i.e. aligned. If
    the origin were ignored the whole deck would fail or pass by accident of its margins."""
    assert g.check_g3([page([node(l=52, w=100)], origin=52)]).state == PASS
    assert g.check_g3([page([node(l=52, w=100)], origin=50)]).state == FAIL


def test_g3_skips_a_ragged_text_right_edge():
    """A text run ends where the sentence ends. Requiring that on a grid would mean
    giving every paragraph an arbitrary fixed width."""
    r = g.check_g3([page([node(l=0, w=101, right_visible=False)])])
    assert r.state == PASS


def test_g3_checks_a_right_edge_that_is_visible():
    r = g.check_g3([page([node(l=0, w=101, right_visible=True)])])
    assert r.state == FAIL
    assert "right edge" in r.offenders[0]


def test_g3_pass_message_reports_both_edge_populations():
    """The count is the coverage signal. 'all edges pass' with a hidden zero denominator
    is the failure this deck's G8 already made once."""
    r = g.check_g3([page([node(l=0, w=100, right_visible=True), node(l=8, w=100)])])
    assert r.state == PASS
    assert "2 left edge(s)" in r.detail and "1 visible right edge(s)" in r.detail


def test_g3_cannot_run_with_no_elements():
    assert g.check_g3([page([])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G4 vertical spacing
# --------------------------------------------------------------------------

def stack(gap, hh=20, **kw):
    a = node(t=0, hh=hh, ord_=0, **kw)
    b = node(t=hh + gap, hh=hh, ord_=1, **kw)
    return [a, b]


def test_g4_passes_on_a_grid_gap():
    assert g.check_g4([page(stack(12))]).state == PASS


def test_g4_fails_on_an_off_grid_gap():
    r = g.check_g4([page(stack(13))])
    assert r.state == FAIL
    assert "13" in r.offenders[0]


def test_g4_ignores_side_by_side_siblings():
    a = node(l=0, t=0, w=100, hh=20, ord_=0)
    b = node(l=104, t=0, w=100, hh=20, ord_=1)
    assert g.check_g4([page([a, b])]).state == CANNOT_RUN


def test_g4_ignores_furniture_siblings():
    a = node(t=0, hh=20, ord_=0)
    b = node(t=33, hh=20, ord_=1, furniture=1)
    assert g.check_g4([page([a, b])]).state == CANNOT_RUN


def test_g4_still_judges_absolutely_positioned_CONTENT():
    a = node(t=0, hh=20, ord_=0)
    b = node(t=33, hh=20, ord_=1, absolute=1, furniture=0)
    assert g.check_g4([page([a, b])]).state == FAIL


def test_g4_does_not_pair_across_different_parents():
    a = node(t=0, hh=20, ord_=0, pid="pA")
    b = node(t=33, hh=20, ord_=1, pid="pB")
    assert g.check_g4([page([a, b])]).state == CANNOT_RUN


def _non_adjacent_collision(**kw):
    """a spans the width; b sits to the right under it; c, the NEXT sibling in DOM order,
    is drawn back up on top of a. Adjacent pairs are (a,b) clean and (b,c) side by side,
    so only a non-adjacent comparison can see c land on a."""
    a = node(l=0, t=0, w=200, hh=20, ord_=0, **kw)
    b = node(l=150, t=24, w=50, hh=20, ord_=1, **kw)
    c = node(l=0, t=10, w=50, hh=20, ord_=2, absolute=1, **kw)
    return [a, b, c]


def test_g4_sees_a_collision_between_non_adjacent_siblings():
    """110.F4 / 111.F4 (P0). zip(members, members[1:]) compared DOM neighbours only."""
    r = g.check_g4([page(_non_adjacent_collision())])
    assert r.state == FAIL
    assert any("OVERLAP" in o for o in r.offenders)


def test_g4_non_adjacent_siblings_that_do_not_touch_are_not_a_collision():
    a = node(l=0, t=0, w=200, hh=20, ord_=0)
    b = node(l=150, t=24, w=50, hh=20, ord_=1)
    c = node(l=0, t=48, w=50, hh=20, ord_=2)
    r = g.check_g4([page([a, b, c])])
    assert not any("OVERLAP" in o for o in r.offenders)


def test_g4_measures_gaps_between_neighbours_only():
    """A gap exists only between neighbours: a and c in a three-block stack have b
    between them, and 24+20+... is not a gap anyone laid out."""
    a = node(t=0, hh=20, ord_=0)
    b = node(t=24, hh=20, ord_=1)
    c = node(t=49, hh=20, ord_=2)                   # b->c is 5px, off grid
    r = g.check_g4([page([a, b, c])])
    assert r.state == FAIL and len(r.offenders) == 1


def test_g4_cannot_run_with_no_stacked_pair():
    assert g.check_g4([page([node()])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G5 type scale
# --------------------------------------------------------------------------

def test_g5_passes_when_every_size_is_on_the_scale():
    ns = [node(font=s, own="text", lh=0) for s in g.TYPE_SCALE]
    assert g.check_g5([page(ns)]).state == PASS


def test_g5_fails_on_a_size_off_the_scale():
    r = g.check_g5([page([node(font=15, own="body copy")])])
    assert r.state == FAIL
    assert "15" in r.offenders[0]


def test_g5_fails_on_a_near_duplicate_of_a_scale_entry():
    """15.5 against 16 is the exact drift this rule was added for: a reader cannot tell
    them apart and the stylesheet can."""
    r = g.check_g5([page([node(font=15.5, own="body copy")])])
    assert r.state == FAIL


def test_g5_fails_on_a_second_font_family():
    ns = [node(font=16, own="a", family="Test Sans"),
          node(font=16, own="b", family="Some Serif")]
    r = g.check_g5([page(ns)])
    assert r.state == FAIL
    assert "second font family" in r.offenders[0]


def test_g5_ignores_elements_that_render_no_text_of_their_own():
    """A wrapper inherits a font-size it never paints. Judging it would make the rule
    depend on markup nesting rather than on what a reader sees."""
    assert g.check_g5([page([node(font=15, own="")])]).state == CANNOT_RUN


def test_g5_cannot_run_when_nothing_renders_text():
    assert g.check_g5([page([node(own="")])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G6 svg whitespace
# --------------------------------------------------------------------------

def slack(**kw):
    d = {"left": 0, "top": 0, "right": 0, "bottom": 0, "vb": [0, 0, 100, 100]}
    d.update(kw)
    return d


def test_g6_passes_when_the_viewbox_hugs_the_drawing():
    assert g.check_g6([page([node(svg_slack=slack())])]).state == PASS


@pytest.mark.parametrize("side", ["left", "top", "right", "bottom"])
def test_g6_fails_on_slack_on_any_side(side):
    r = g.check_g6([page([node(svg_slack=slack(**{side: 20}))])])
    assert r.state == FAIL
    assert side in r.offenders[0]


def test_g6_allows_a_stroke_painted_on_the_viewbox_boundary():
    """Half a stroke outside the box reads as small negative slack. That is ink on the
    edge, not content lost, and failing on it would make the rule unusable.

    This test used to be `test_g6_allows_ink_that_overshoots_the_viewbox` and asserted
    that ALL negative slack passes. The reasoning was right about strokes and generalised
    from strokes to every negative value, which is how a deck shipped with a clipped word
    and this rule reported PASS. -2 was inside the old tolerance-free pass and is now
    outside SVG_CLIP_TOL, so the values here are the stroke-sized ones they should always
    have been."""
    assert g.check_g6([page([node(svg_slack=slack(top=-1.2, bottom=-0.6))])]).state == PASS


@pytest.mark.parametrize("side", ["left", "top", "right", "bottom"])
def test_g6_fails_when_drawing_is_clipped_outside_any_side(side):
    """The shipped case: a sankey 8.43 user units wider than its own viewBox, so the last
    label read "billin". G6 computed that number and compared it in one direction."""
    r = g.check_g6([page([node(svg_slack=slack(**{side: -8.43}))])])
    assert r.state == FAIL
    assert side in r.offenders[0]
    assert "CLIPPED" in r.offenders[0]
    assert "8.43" in r.offenders[0]


def test_g6_reports_clipping_and_slack_together_and_leads_with_the_clip():
    """A lost glyph is a defect in the artifact; slack is a defect in its alignment."""
    r = g.check_g6([page([node(svg_slack=slack(left=20, right=-9))])])
    assert r.state == FAIL
    assert "CLIPPED" in r.offenders[0]
    assert any("empty" in o for o in r.offenders)


def test_g6_passing_message_states_both_halves():
    """A PASS that only says the viewBox hugs the drawing is the message that was true
    while a word was clipped off the page."""
    r = g.check_g6([page([node(svg_slack=slack())])])
    assert r.state == PASS
    assert "clipped" in r.detail


def test_g6_cannot_run_without_a_measurable_svg():
    assert g.check_g6([page([node(svg_slack=None)])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G7 leading trim
# --------------------------------------------------------------------------

def text_node(fs=16, lh=24, lines=1, ch=None, top_slack=None, trim=0):
    return node(own="some words", font=fs, lh=lh, lines=lines,
                content_h=lh * lines if ch is None else ch,
                top_slack=top_slack, trim=trim)


def test_g7_passes_when_the_measured_height_shows_a_trim():
    """Untrimmed, a one-line block is exactly one line-height tall. Trimmed it is shorter
    by the leading."""
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=16)])])
    assert r.state == PASS


def test_g7_fails_when_the_block_is_exactly_n_line_heights_tall():
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=24)])])
    assert r.state == FAIL


def test_g7_fails_on_a_partial_trim():
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=22)])])
    assert r.state == FAIL


def test_g7_handles_multiple_lines():
    assert g.check_g7([page([text_node(fs=16, lh=24, lines=3, ch=64)])]).state == PASS
    assert g.check_g7([page([text_node(fs=16, lh=24, lines=3, ch=72)])]).state == FAIL


def test_g7_accepts_the_declared_property_for_a_stretched_box():
    """A box the layout stretches -- a table cell in a taller row -- is taller than its own
    text needs, so the height comparison cannot see its trim. The declared property is the
    honest answer THERE, alongside blocks the rule actually measured."""
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=16),
                          text_node(fs=16, lh=24, lines=1, ch=48, trim=1)])])
    assert r.state == PASS
    assert "1 text block(s) are MEASURED" in r.detail


def test_g7_cannot_run_when_every_block_took_the_declared_exit():
    """35 of 35 blocks passing by DECLARATION and 0 by MEASUREMENT is a rule reporting
    green having checked nothing. That is what this deck did before the fix."""
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=48, trim=1),
                          text_node(fs=16, lh=24, lines=1, ch=72, trim=1)])])
    assert r.state == CANNOT_RUN
    assert "measured nothing" in r.detail


def test_g7_does_not_accept_a_declaration_on_an_unstretched_block():
    """Declaring the property does not excuse a block whose own height still shows the
    leading. Otherwise one CSS line switches the rule off deck-wide."""
    r = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=16),
                          text_node(fs=16, lh=24, lines=1, ch=24, trim=1)])])
    assert r.state == FAIL


def test_g7_does_not_measure_position_which_is_a_font_metric_not_leading():
    """topSlack measures (ascent - cap height), a property of the typeface that does not
    move with line-height. A rule keyed to it passed at 13px and failed at 16px in a
    different font."""
    a = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=16, top_slack=0.0)])])
    b = g.check_g7([page([text_node(fs=16, lh=24, lines=1, ch=16, top_slack=99.0)])])
    assert a.state == b.state == PASS


def test_g7_ignores_text_with_no_leading_to_trim():
    assert g.check_g7([page([text_node(fs=16, lh=16)])]).state == CANNOT_RUN


def test_g7_cannot_run_when_no_text_has_leading():
    assert g.check_g7([page([node(own="")])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G8 crowding
# --------------------------------------------------------------------------

def test_g8_passes_on_an_adequate_gap():
    a = node(t=0, hh=20, ord_=0, own="first")
    b = node(t=20 + g.MIN_TEXT_GAP, hh=20, ord_=1, own="second")
    assert g.check_g8([page([a, b])]).state == PASS


def test_g8_fails_when_two_text_blocks_crowd():
    a = node(t=0, hh=20, ord_=0, own="first")
    b = node(t=24, hh=20, ord_=1, own="second")             # 4px gap
    r = g.check_g8([page([a, b])])
    assert r.state == FAIL
    assert "4px between two text blocks" in r.offenders[0]


def test_g8_sees_a_text_collision_between_non_adjacent_siblings():
    """110.F4 / 111.F4 (P0), G8 half."""
    r = g.check_g8([page(_non_adjacent_collision(own="words"))])
    assert r.state == FAIL
    assert any("OVERLAP" in o for o in r.offenders)


def test_g8_non_adjacent_collision_needs_text_on_both_sides():
    a, b, c = _non_adjacent_collision(own="words")
    c["hasText"] = 0
    c["ownText"] = ""
    r = g.check_g8([page([a, b, c])])
    assert not any("OVERLAP" in o for o in r.offenders)


def test_g8_only_judges_text_against_text():
    """A gap between a chart and a heading is composition, not crowding."""
    a = node(t=0, hh=20, ord_=0, own="")
    b = node(t=24, hh=20, ord_=1, own="second")
    assert g.check_g8([page([a, b])]).state == CANNOT_RUN


@pytest.mark.parametrize("side", ["t", "b", "l", "r"])
def test_g8_fails_when_a_painted_box_underpads_any_side(side):
    pad = {k: g.MIN_BOX_PAD for k in "tblr"}
    pad[side] = 0
    r = g.check_g8([page([node(is_box=True, own="words in a box", pad=pad, w=200)])])
    assert r.state == FAIL


def test_g8_judges_a_box_by_the_text_it_contains_not_the_text_it_owns():
    """A painted box almost always holds its words in CHILD elements. Keying on
    directly-owned text skipped every real box and the rule reported 'all 0 painted
    box(es)' -- green, and measuring nothing."""
    n = node(is_box=True, own="", has=40, w=200,
             pad={"t": 0, "b": 8, "l": 8, "r": 8})
    r = g.check_g8([page([n])])
    assert r.state == FAIL


def test_g8_exempts_a_small_marker_from_the_padding_rule():
    """A 16px circle holding one digit is a shape, not a text container; padding it 8px
    a side would make it a 32px circle."""
    n = node(is_box=True, own="1", has=1, w=16, hh=16, font=11,
             pad={"t": 0, "b": 0, "l": 0, "r": 0})
    assert g.check_g8([page([n])]).state == CANNOT_RUN


def test_g8_marker_exemption_does_not_cover_a_real_box():
    """The exemption is narrow on purpose: widen it and it becomes the excuse that
    lets any underpadded box through."""
    n = node(is_box=True, own="ab", has=2, w=400, hh=40, font=16,
             pad={"t": 0, "b": 0, "l": 0, "r": 0})
    assert g.check_g8([page([n])]).state == FAIL


def test_g8_cannot_run_with_nothing_to_measure():
    assert g.check_g8([page([node(own="")])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# regressions: each of these was GREEN when it shipped
# --------------------------------------------------------------------------

def test_regression_a_rule_reporting_zero_denominator_is_not_a_pass():
    """G8 once passed with 'all 0 painted box(es)'. The pass text must carry the count so
    a zero denominator is visible in the output a human reads."""
    r = g.check_g8([page([node(t=0, hh=20, ord_=0, own="a"),
                          node(t=28, hh=20, ord_=1, own="b")])])
    assert r.state == PASS
    assert "1 text gap(s)" in r.detail and "0 painted box(es)" in r.detail


def test_regression_run_checks_returns_every_rule():
    """Rules have been added four times. A rule that exists and is never called is the
    cheapest possible silent gap."""
    results = g.run_checks([page([node()])])
    assert [r.rule for r in results] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8",
                                         "G9"]


def test_regression_thresholds_are_constants_not_cli_flags():
    """A threshold flag is an invitation to loosen the rule under deadline. Nick,
    2026-09-19: never loosen the threshold."""
    src = Path(g.__file__).read_text(encoding="utf-8")
    declared = re.findall(r'add_argument\(\s*"(--[a-z-]+)"', src)
    for flag in declared:
        assert flag in ("--json", "--chrome", "--only"), (
            flag + " is a new CLI flag; if it tunes a threshold it must not exist")
    # And the constants really are module-level, so a test can patch them and a caller
    # cannot reach them.
    for name in ("GRID", "TOL", "MIN_CLEARANCE", "MIN_TEXT_GAP", "MIN_BOX_PAD"):
        assert isinstance(getattr(g, name), (int, float))


# --------------------------------------------------------------------------
# live smoke: the probe itself, in a real browser
# --------------------------------------------------------------------------

CHROME = g.find_chrome()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium present")

SMOKE_DECK = """
<style>
  *{box-sizing:border-box}
  *{text-box:trim-both cap alphabetic}
  body{margin:0;font-family:Arial,sans-serif}
  .slide{width:1280px;height:720px;position:relative;padding:40px 52px 100px}
  .src{position:absolute;bottom:20px;left:52px;right:110px;font-size:11px}
  .body{display:flex;gap:24px}
  .col{width:576px}
  .box{background:#eee;padding:8px 12px}
</style>
<div class="slide">
  <div class="body">
    <div class="col"><div class="box"><p style="font-size:16px;line-height:1.4;margin:0">
      Widget output held steady through March.</p></div></div>
    <div class="col"><p style="font-size:16px;line-height:1.4;margin:0">Second column.</p></div>
  </div>
  <p class="src">Source: the fictional widget ledger.</p>
</div>
"""


@needs_chrome
def test_live_probe_returns_geometry_for_a_real_page(tmp_path):
    deck = tmp_path / "smoke.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    pages = g.measure(deck, CHROME)
    assert len(pages) == 1
    assert pages[0]["src"] is not None
    assert pages[0]["nodes"], "the probe found no elements, so no rule measured anything"


@needs_chrome
def test_live_probe_walks_the_whole_slide_not_only_the_body(tmp_path):
    """The probe once walked `.body` alone, leaving the lede, standfirst and source line
    unmeasured while reporting 8 pass / 0 fail."""
    deck = tmp_path / "smoke.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    pages = g.measure(deck, CHROME)
    labels = " ".join(n["label"] for n in pages[0]["nodes"])
    assert "src" in labels, "the source line was not measured by any rule"


@needs_chrome
def test_live_probe_does_not_descend_into_an_svg(tmp_path):
    deck = tmp_path / "svg.html"
    deck.write_text(SMOKE_DECK.replace(
        "<p style=\"font-size:16px;line-height:1.4;margin:0\">Second column.</p>",
        '<svg viewBox="0 0 100 50" width="100" height="50">'
        '<rect x="0" y="0" width="100" height="50" fill="#777"/></svg>'),
        encoding="utf-8")
    pages = g.measure(deck, CHROME)
    tags = [n["tag"] for n in pages[0]["nodes"]]
    assert "svg" in tags
    assert "rect" not in tags, "chart data was measured as page layout"


@needs_chrome
def test_live_clean_deck_passes_every_rule_it_can_run(tmp_path):
    deck = tmp_path / "clean.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    pages = g.measure(deck, CHROME)
    fails = [r for r in g.run_checks(pages) if r.state == FAIL]
    assert not fails, "clean fixture failed: " + "; ".join(
        r.rule + " " + r.detail for r in fails)


@needs_chrome
def test_live_off_grid_padding_is_caught_end_to_end(tmp_path):
    """The whole chain -- render, probe, rule -- against a defect introduced in CSS."""
    deck = tmp_path / "dirty.html"
    deck.write_text(SMOKE_DECK.replace(".box{background:#eee;padding:8px 12px}",
                                       ".box{background:#eee;padding:8px 13px}"),
                    encoding="utf-8")
    pages = g.measure(deck, CHROME)
    assert g.check_g3(pages).state == FAIL


@needs_chrome
def test_live_untrimmed_leading_is_caught_end_to_end(tmp_path):
    deck = tmp_path / "untrimmed.html"
    deck.write_text(SMOKE_DECK.replace("  *{text-box:trim-both cap alphabetic}\n", ""),
                    encoding="utf-8")
    pages = g.measure(deck, CHROME)
    assert g.check_g7(pages).state == FAIL


@needs_chrome
def test_live_missing_browser_exits_four_and_never_reports_clean(tmp_path):
    """Without a browser NOTHING is measured. Reporting 0 would be the exact overclaim
    the CANNOT_RUN vocabulary exists to prevent."""
    deck = tmp_path / "clean.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    assert g.main([str(deck), "--chrome", "/nonexistent/chrome"]) == 4


def test_missing_deck_exits_four():
    assert g.main(["/nonexistent/deck.html"]) == 4


# --------------------------------------------------------------------------
# mutation-driven tests
#
# Every test below exists because a MUTANT SURVIVED the suite above. A green run proves
# the code does what the tests describe; it does not prove the tests describe anything
# that matters. These pin the lines where breaking the code on purpose changed nothing.
# --------------------------------------------------------------------------

def test_g1_pass_message_reports_the_true_tightest_clearance():
    """The running minimum feeds the PASS line. Break it and the gate still says PASS
    while telling a human the wrong number about how close the page came to breaking --
    which is the number they would act on."""
    r = g.check_g1([page([node(t=0, hh=100), node(t=0, hh=560), node(t=0, hh=300)],
                         src_top=600)])
    assert r.state == PASS
    assert "40" in r.detail, r.detail


def test_g1_reports_the_minimum_across_pages_not_just_the_last():
    r = g.check_g1([page([node(t=0, hh=560)], src_top=600, num=1),
                    page([node(t=0, hh=100)], src_top=600, num=2)])
    assert r.state == PASS
    assert "40" in r.detail, r.detail


def test_g4_pairs_siblings_in_dom_order_not_list_order():
    """Nodes arrive in whatever order the probe walked them.

    The ASSERTION IS THE REASON, not the verdict. Reversed and unsorted, these two read as
    a collision rather than as an off-grid gap, so both orderings end in FAIL and a test
    that checked only the state could not tell the sort had been removed."""
    first = node(t=0, hh=20, ord_=0)
    second = node(t=33, hh=20, ord_=1)      # 13px gap: off grid
    r = g.check_g4([page([second, first])])
    assert r.state == FAIL
    assert "off the 4px grid" in r.offenders[0], r.offenders
    assert "OVERLAP" not in r.offenders[0]


def test_g8_pairs_siblings_in_dom_order_not_list_order():
    """Same shape as the G4 case: assert WHY it failed, or removing the sort is invisible."""
    first = node(t=0, hh=20, ord_=0, own="first")
    second = node(t=24, hh=20, ord_=1, own="second")        # 4px gap: crowded
    r = g.check_g8([page([second, first])])
    assert r.state == FAIL
    assert "between two text blocks" in r.offenders[0], r.offenders
    assert "OVERLAP" not in r.offenders[0]


def test_g8_ignores_two_text_blocks_sitting_side_by_side():
    """Two columns of text 4px apart horizontally are not crowded siblings; their
    horizontal spacing is G3's business."""
    a = node(l=0, t=0, w=100, hh=20, ord_=0, own="left column")
    b = node(l=104, t=0, w=100, hh=20, ord_=1, own="right column")
    assert g.check_g8([page([a, b])]).state == CANNOT_RUN


def test_g5_ignores_an_element_with_no_font_size():
    """A node the probe could not measure a size for must be skipped, not counted as a
    size of zero -- which is off every scale and would fail every deck."""
    assert g.check_g5([page([node(font=0, own="text")])]).state == CANNOT_RUN


def test_g5_ignores_an_empty_font_family():
    """An unreported family must not register as a second family."""
    ns = [node(font=16, own="a", family="Test Sans"),
          node(font=16, own="b", family="")]
    r = g.check_g5([page(ns)])
    assert r.state == PASS, r.offenders


def test_g5_one_family_never_reports_a_second_one():
    ns = [node(font=16, own="a", family="Test Sans"),
          node(font=20, own="b", family="Test Sans")]
    r = g.check_g5([page(ns)])
    assert r.state == PASS
    assert not r.offenders


def test_g7_skips_an_element_that_renders_no_text_of_its_own():
    """A container inherits a font-size and line-height it never paints. Judging its
    leading would make the rule depend on markup nesting."""
    n = node(own="", has=50, font=16, lh=24, lines=1, content_h=24)
    assert g.check_g7([page([n])]).state == CANNOT_RUN


@pytest.mark.parametrize("kw", [
    dict(lh=0), dict(font=0), dict(lines=0), dict(content_h=0),
])
def test_g7_skips_a_block_it_cannot_measure(kw):
    """An unmeasurable block must be skipped, never judged. Judged, a zero line-count
    makes the bound negative and every such block fails for no reason."""
    base = dict(own="some words", font=16, lh=24, lines=1, content_h=24)
    base.update(kw)
    assert g.check_g7([page([node(**base)])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# the CLI: exit codes and the reporting path
# --------------------------------------------------------------------------

@needs_chrome
def test_cli_exits_zero_and_prints_every_rule_on_a_clean_deck(tmp_path, capsys):
    deck = tmp_path / "clean.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    assert g.main([str(deck)]) == 0
    out = capsys.readouterr().out
    for rule in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):
        assert rule in out, rule + " never appeared in the report"
    assert "measured in a real browser" in out


@needs_chrome
def test_cli_exits_two_on_a_defect(tmp_path):
    deck = tmp_path / "dirty.html"
    deck.write_text(SMOKE_DECK.replace(".box{background:#eee;padding:8px 12px}",
                                       ".box{background:#eee;padding:8px 13px}"),
                    encoding="utf-8")
    assert g.main([str(deck)]) == 2


@needs_chrome
def test_cli_json_payload_carries_counts_and_the_clean_flag(tmp_path, capsys):
    import json as _json
    deck = tmp_path / "clean.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    assert g.main([str(deck), "--json"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["clean"] is True
    assert payload["counts"]["fail"] == 0
    assert len(payload["results"]) == 9
    assert payload["pages"] == 1


@needs_chrome
def test_cli_only_flag_runs_just_the_named_rules(tmp_path, capsys):
    deck = tmp_path / "clean.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    g.main([str(deck), "--only", "G1,G3", "--json"])
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert [r["rule"] for r in payload["results"]] == ["G1", "G3"]


@needs_chrome
def test_cli_reports_a_cannot_run_as_not_a_pass(tmp_path, capsys):
    """The smoke deck carries no chart, so G6 cannot run. The report must say so in
    words, because a CANNOT_RUN silently counted as a pass is this gate's worst outcome.
    (Until 2026-09-23 this used a deck with no source line, which G1 now FAILS.)"""
    deck = tmp_path / "nochart.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    g.main([str(deck), "--json"])
    import json as _json
    res = {r["rule"]: r["state"] for r in _json.loads(capsys.readouterr().out)["results"]}
    assert res["G6"] == CANNOT_RUN
    g.main([str(deck)])
    assert "a CANNOT_RUN is not a pass" in capsys.readouterr().out


@needs_chrome
def test_cli_fails_G1_on_a_deck_with_no_source_line(tmp_path, capsys):
    """Cross-model 2026-09-23 (Grok round 2, F4). The test above used to pass on a
    no-source deck by asserting only that SOME rule could not run, so reverting the
    every-page-needs-a-source change would have stayed green."""
    deck = tmp_path / "nosrc.html"
    deck.write_text(SMOKE_DECK.replace('<p class="src">Source: the fictional widget ledger.</p>',
                                       ''), encoding="utf-8")
    g.main([str(deck), "--json"])
    import json as _json
    res = {r["rule"]: r["state"] for r in _json.loads(capsys.readouterr().out)["results"]}
    assert res["G1"] == FAIL


# --------------------------------------------------------------------------
# plumbing: browser discovery, measurement failure, and the stderr paths
#
# Also mutation-driven. These are the least glamorous lines in the file and they are
# exactly the ones that decide whether a failed run is reported as a failure or as a
# pass, which is the difference between a gate and a decoration.
# --------------------------------------------------------------------------

def test_find_chrome_accepts_an_explicit_path_that_exists(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    assert g.find_chrome(str(exe)) == str(exe)


def test_find_chrome_rejects_an_explicit_path_that_does_not_exist():
    """An explicit path that is wrong must return None so main() exits 4. Returning it
    anyway would make the run fail later with a confusing error instead of the clear
    'nothing was measured'."""
    assert g.find_chrome("/definitely/not/a/browser") is None


def test_measure_raises_when_the_probe_returns_no_geometry(tmp_path, monkeypatch):
    """A browser that starts and produces nothing must RAISE, not return an empty list.
    An empty list would flow into run_checks as 'no pages' and could read as clean."""
    class FakeProc:
        returncode = 0
        stdout = "<html><body>nothing here</body></html>"
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: FakeProc())
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="NOT a pass"):
        g.measure(deck, "/bin/true")


def test_measure_cleans_up_its_temp_directory(tmp_path, monkeypatch):
    import tempfile as _tf
    made = []
    real = _tf.mkdtemp
    monkeypatch.setattr(_tf, "mkdtemp", lambda **k: made.append(real(**k)) or made[-1])

    class FakeProc:
        returncode = 0
        stdout = '<pre id="__geom">[]</pre>'
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: FakeProc())
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.measure(deck, "/bin/true")
    assert made and not Path(made[0]).exists(), "the probe's temp directory was left behind"


def test_measure_failure_exits_four_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck)]) == 4
    assert "could not measure" in capsys.readouterr().err


def test_a_deck_with_no_pages_exits_four_and_says_so(tmp_path, monkeypatch, capsys):
    """Zero pages is not a clean deck. It is a deck the gate could not find."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [])
    deck = tmp_path / "d.html"
    deck.write_text("<p>no slides here</p>", encoding="utf-8")
    assert g.main([str(deck)]) == 4
    assert "no pages matched" in capsys.readouterr().err


def test_missing_deck_says_it_cannot_read_the_file(tmp_path, capsys):
    assert g.main([str(tmp_path / "absent.html")]) == 4
    assert "cannot read" in capsys.readouterr().err


def test_missing_browser_says_nothing_was_measured(tmp_path, capsys):
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck), "--chrome", "/nonexistent/chrome"]) == 4
    assert "NOTHING WAS MEASURED" in capsys.readouterr().err


def test_report_prints_page_count_and_each_offender_line(tmp_path, monkeypatch, capsys):
    """An offender the report does not print is a finding nobody acts on."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure",
                        lambda *a, **k: [page([node(l=15, w=100)]),
                                         page([node(l=0, w=100)], num=2)])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck)]) == 2
    out = capsys.readouterr().out
    assert "pages: 2" in out
    assert "left edge" in out, "the offender detail never reached the report"


def test_the_cannot_run_note_appears_only_when_something_could_not_run(
        tmp_path, monkeypatch, capsys):
    """The note must be conditional. Printed always it is wallpaper; printed never, a
    CANNOT_RUN silently reads as a pass."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    full = [page([node(l=0, w=100, own="a", font=16, lh=0, is_box=False),
                  node(l=8, w=100, own="b", font=16, lh=0, ord_=1, t=40)])]
    monkeypatch.setattr(g, "measure", lambda *a, **k: full)
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck)])
    out = capsys.readouterr().out
    has_cannot = "cannot run" in out and "0 cannot run" not in out
    assert ("a CANNOT_RUN is not a pass" in out) == has_cannot


def test_json_counts_cannot_run_separately_from_pass(tmp_path, monkeypatch, capsys):
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    p = page([node()])
    p["src"] = None                       # G1 cannot run
    monkeypatch.setattr(g, "measure", lambda *a, **k: [p])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["counts"]["cannot_run"] >= 1
    assert payload["fully_covered"] is False, (
        "a deck with an unrunnable rule must never report as fully covered")


# --------------------------------------------------------------------------
# a page on which every rule actually runs
#
# Every other fixture here leaves some rules CANNOT_RUN, which means the "nothing could
# not run" path -- the one that decides whether the report tells a human the deck was
# fully covered -- was never exercised. Two mutants lived in exactly that gap.
# --------------------------------------------------------------------------

def fully_covered_page():
    """One page that gives all eight rules something real to measure, and passes."""
    parent = {"l": -16, "t": -16, "r": 400, "b": 400}
    a = node(label="p a", l=0, t=0, w=100, hh=16, parent=parent, pid="p1", ord_=0,
             own="Alpha", font=16, lh=24, lines=1, content_h=16)
    b = node(label="p b", l=0, t=32, w=100, hh=16, parent=parent, pid="p1", ord_=1,
             own="Beta", font=16, lh=24, lines=1, content_h=16)
    svg = node(label="svg", l=0, t=96, w=100, hh=48, parent=parent, pid="p2",
               svg_slack=slack(), letterbox={"x": 0, "y": 0})
    box = node(label="div box", l=0, t=200, w=200, hh=40, parent=parent, pid="p3",
               is_box=True, has=10, right_visible=True,
               pad={"t": 8, "b": 8, "l": 8, "r": 8})
    return page([a, b, svg, box], src_top=600)


def test_every_rule_runs_and_passes_on_the_fully_covered_page():
    results = g.run_checks([fully_covered_page()])
    not_run = [r.rule for r in results if r.state == CANNOT_RUN]
    failed = [r.rule + " " + r.detail for r in results if r.state == FAIL]
    assert not not_run, "these rules never ran: " + ", ".join(not_run)
    assert not failed, "; ".join(failed)


def test_cannot_run_count_is_exactly_zero_when_every_rule_runs(tmp_path, monkeypatch,
                                                               capsys):
    """Pinned exactly, not with >=. The comparison that builds this list can be inverted
    and a >= assertion still passes, because the inverted list is LARGER."""
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck), "--json"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["counts"]["cannot_run"] == 0
    assert payload["counts"]["pass"] == 9
    assert payload["counts"]["executed"] == 9
    assert "CLEAN and FULLY COVERED" in payload["certification"]
    assert payload["counts"]["fail"] == 0
    assert payload["fully_covered"] is True


def test_the_cannot_run_note_is_absent_when_nothing_could_not_run(tmp_path, monkeypatch,
                                                                  capsys):
    """Printed unconditionally the note is wallpaper and stops meaning anything."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck)])
    out = capsys.readouterr().out
    assert "0 cannot run" in out
    assert "a CANNOT_RUN is not a pass" not in out


# --------------------------------------------------------------------------
# G9 letterboxing
# --------------------------------------------------------------------------

def test_g9_passes_when_the_chart_fills_its_box():
    assert g.check_g9([page([node(letterbox={"x": 0, "y": 0})])]).state == PASS


@pytest.mark.parametrize("axis,key", [("horizontally", "x"), ("vertically", "y")])
def test_g9_fails_when_the_chart_is_letterboxed(axis, key):
    lb = {"x": 0, "y": 0}
    lb[key] = 113
    r = g.check_g9([page([node(letterbox=lb)])])
    assert r.state == FAIL
    assert axis in r.offenders[0]


def test_g9_is_not_g3_and_not_g6():
    """The element edge can be perfectly on the grid (G3) and the viewBox can hug the
    drawing (G6) while the drawing sits 113px inside the box. Neither rule can see the
    gap between them, which is why this one is separate."""
    n = node(l=0, w=100, svg_slack=slack(), letterbox={"x": 113, "y": 0})
    pages = [page([n])]
    assert g.check_g3(pages).state == PASS
    assert g.check_g6(pages).state == PASS
    assert g.check_g9(pages).state == FAIL


def test_g9_cannot_run_without_a_chart():
    assert g.check_g9([page([node()])]).state == CANNOT_RUN


# --------------------------------------------------------------------------
# G5 reaches chart type
# --------------------------------------------------------------------------

def chart(sizes, **kw):
    return node(label="svg", svg_slack=slack(), letterbox={"x": 0, "y": 0},
                **kw) | {"svgType": {str(k): v for k, v in sizes.items()}}


def test_g5_fails_on_chart_type_off_the_scale():
    """The probe does not descend into an SVG for LAYOUT, and that is right. Excluding
    the chart from the TYPE rule let this deck carry labels at an effective 8.6px while
    G5 reported every size on the scale."""
    n = node(label="svg")
    n["svgType"] = {"8.61": 11, "10.35": 3}
    r = g.check_g5([page([n, node(font=16, own="body")])])
    assert r.state == FAIL
    assert "CHART type" in r.offenders[0]


def test_g5_passes_on_chart_type_that_is_on_the_scale():
    n = node(label="svg")
    n["svgType"] = {"11": 16, "13": 4}
    r = g.check_g5([page([n, node(font=16, own="body")])])
    assert r.state == PASS
    assert "2 chart size(s)" in r.detail


def test_g5_reports_chart_and_page_populations_separately():
    n = node(label="svg")
    n["svgType"] = {"20": 1}
    r = g.check_g5([page([n, node(font=16, own="body"), node(font=30, own="lede")])])
    assert r.state == PASS
    assert "2 page size(s)" in r.detail and "1 chart size(s)" in r.detail


# --------------------------------------------------------------------------
# G2 upward escape, G8 accent boxes and table rows
# --------------------------------------------------------------------------

def test_g2_catches_a_child_escaping_upward():
    """A child pulled up by a negative margin -- what the rejected ::before trim hack did
    -- escaped its parent and overlapped whatever sat above, and containment stayed green."""
    r = g.check_g2([page([node(l=10, t=-20, w=100, hh=50, parent=PARENT)])])
    assert r.state == FAIL
    assert "top" in r.offenders[0]


def test_g8_treats_an_accent_bar_callout_as_a_box():
    """A left accent bar on a block of text is a container a reader sees. Exempting it as
    a 'rule' let the deck's callouts sit at 4px of padding."""
    n = node(is_box=True, has=40, w=300, accent_only=1, accent_left=1,
             pad={"t": 4, "b": 4, "l": 13, "r": 0})
    r = g.check_g8([page([n])])
    assert r.state == FAIL
    assert any("top" in o for o in r.offenders)


def test_g8_does_not_demand_padding_on_an_accent_callouts_open_side():
    """Padding stops text touching a visible edge. An accent callout's far side has no
    edge, so there is nothing there to touch."""
    n = node(is_box=True, has=40, w=300, accent_only=1, accent_left=1,
             pad={"t": 8, "b": 8, "l": 13, "r": 0})
    assert g.check_g8([page([n])]).state == PASS


def test_g8_still_demands_all_four_sides_on_a_filled_box():
    n = node(is_box=True, has=40, w=300, accent_only=0,
             pad={"t": 8, "b": 8, "l": 8, "r": 0})
    assert g.check_g8([page([n])]).state == FAIL


def test_g8_does_not_treat_adjacent_table_rows_as_crowded_text():
    """Two <tr>s touch by definition; the space between rows IS the cells' padding, which
    the box half of this rule already governs."""
    a = node(t=0, hh=20, ord_=0, own="row one")
    b = node(t=20, hh=20, ord_=1, own="row two")
    a["tag"] = b["tag"] = "tr"
    assert g.check_g8([page([a, b])]).state == CANNOT_RUN


def test_g8_gap_half_uses_contained_text_not_owned_text():
    """The box half was corrected to hasText and THIS half was left behind, so the flow
    steps -- whose words live one div down -- were never measured for crowding."""
    a = node(t=0, hh=20, ord_=0, own="", has=30)
    b = node(t=24, hh=20, ord_=1, own="", has=30)
    r = g.check_g8([page([a, b])])
    assert r.state == FAIL


# --------------------------------------------------------------------------
# the coverage floor and the certification sentence
# --------------------------------------------------------------------------

def test_an_unknown_only_id_is_an_error_not_a_filter(tmp_path, monkeypatch, capsys):
    """`--only BAD` used to select zero rules and report clean:true, exit 0 -- a gate
    bypassed by a misspelling."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck), "--only", "G42"]) == 4
    assert "unknown rule id" in capsys.readouterr().err


def test_a_deck_where_nothing_could_run_is_not_clean(tmp_path, monkeypatch, capsys):
    """Without a coverage floor, 'no FAIL' returned 0 even when every rule reported
    CANNOT_RUN."""
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    bare = page([node(own="", has=0, font=0, lh=0)])
    bare["src"] = None
    monkeypatch.setattr(g, "measure", lambda *a, **k: [bare])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    rc = g.main([str(deck), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["counts"]["executed"] < g.MIN_EXECUTED
    assert payload["under_coverage_floor"] is True
    assert payload["clean"] is False
    assert rc == 2


def test_certification_names_every_count():
    """Two of my summaries restated a tool's result in stronger vocabulary than the tool
    uses. The gate emits the sentence so it can be quoted rather than paraphrased."""
    s = g.certification(total=9, fails=0, cannot=0, executed=9, under_floor=False)
    assert "CLEAN and FULLY COVERED" in s
    for fragment in ("9 pass", "0 fail", "0 cannot run", "9 of 9"):
        assert fragment in s


def test_certification_distinguishes_clean_from_fully_covered():
    """8 pass / 0 fail / 4 cannot-run is NOT 'all 12 rules pass'. Collapsing the two is
    the overclaim this sentence exists to make impossible."""
    s = g.certification(total=12, fails=0, cannot=4, executed=8, under_floor=False)
    assert "CLEAN but NOT FULLY COVERED" in s
    assert "8 pass" in s and "4 cannot run" in s and "8 of 12" in s


def test_certification_says_not_clean_under_the_floor():
    s = g.certification(total=9, fails=0, cannot=8, executed=1, under_floor=True)
    assert s.startswith("CERTIFICATION: NOT CLEAN")


def test_certification_says_not_clean_on_a_failure():
    s = g.certification(total=9, fails=2, cannot=0, executed=9, under_floor=False)
    assert "NOT CLEAN" in s and "2 fail" in s


@needs_chrome
def test_live_furniture_is_detected_by_class_not_by_position(tmp_path):
    deck = tmp_path / "f.html"
    deck.write_text(SMOKE_DECK.replace(
        '<p class="src">', '<p class="floating" style="position:absolute;bottom:60px">'
        'Not furniture.</p><p class="src">'), encoding="utf-8")
    pages = g.measure(deck, CHROME)
    by_label = {n["label"]: n for n in pages[0]["nodes"]}
    floating = [n for lab, n in by_label.items() if "floating" in lab]
    assert floating, "the absolutely positioned content was not measured at all"
    assert floating[0]["absolute"] == 1
    assert floating[0]["furniture"] == 0, (
        "arbitrary absolute content was classed as furniture and would skip four rules")


@needs_chrome
def test_live_a_text_block_containing_bold_is_still_a_leaf(tmp_path):
    """`children.length === 0` disqualified every text block containing a <b>, so G1
    silently judged a subset of the page."""
    deck = tmp_path / "b.html"
    deck.write_text(SMOKE_DECK.replace(
        "Widget output held steady through March.",
        "Widget output <b>held steady</b> through March."), encoding="utf-8")
    pages = g.measure(deck, CHROME)
    # The <p> itself, not its ancestors -- their textContent contains the phrase too.
    bolded = [n for n in pages[0]["nodes"]
              if n["tag"] == "p" and "held steady" in n["label"]]
    assert bolded, "the block was not measured at all"
    assert bolded[0]["leaf"] == 1, "a block with inline children was excluded from G1"


# --------------------------------------------------------------------------
# second mutation round: survivors in the code the cross-model review forced
# --------------------------------------------------------------------------

def test_g1_fails_every_page_when_no_page_has_a_source_line():
    """Was CANNOT_RUN until 2026-09-23. Every page needs a source, so a deck with none
    fails on every page rather than going unmeasured. Distinct from 'no leaf was
    judged', which is still CANNOT_RUN (test below)."""
    a, b = page([node()]), page([node()], num=2)
    a["src"] = b["src"] = None
    r = g.check_g1([a, b])
    assert r.state == FAIL
    assert len(r.offenders) == 2


def test_g1_cannot_run_when_a_sourced_page_has_no_leaf_to_judge():
    r = g.check_g1([page([node(furniture=1)])])
    assert r.state == CANNOT_RUN
    assert "no leaf element was judged" in r.detail


def test_g1_cannot_run_on_no_pages():
    r = g.check_g1([])
    assert r.state == CANNOT_RUN and "no pages were measured" in r.detail


def test_g7_exempts_a_marker_but_still_judges_its_neighbours():
    """The exemption must not swallow the rule. A marker passes; a real block beside it
    with the same untrimmed leading still fails."""
    marker = node(own="1", font=11, lh=16, lines=1, content_h=16, w=16)
    block = node(own="a real sentence", font=11, lh=16, lines=1, content_h=16, w=300)
    assert g.check_g7([page([marker])]).state == CANNOT_RUN
    assert g.check_g7([page([marker, block])]).state == FAIL


def test_g7_marker_exemption_needs_both_conditions():
    """One or two characters AND a box no wider than three times its type. Either alone
    would let real content through: a wide box holding '1.', or a narrow box of prose."""
    wide_short = node(own="1", font=11, lh=16, lines=1, content_h=16, w=300)
    narrow_long = node(own="a real sentence", font=11, lh=16, lines=1, content_h=16, w=16)
    assert g.check_g7([page([wide_short])]).state == FAIL
    assert g.check_g7([page([narrow_long])]).state == FAIL


def test_g7_cannot_run_when_every_block_is_exempt_or_has_no_leading():
    r = g.check_g7([page([node(own="1", font=11, lh=16, lines=1, content_h=16, w=16)])])
    assert r.state == CANNOT_RUN
    assert "no text block has leading to trim" in r.detail


def test_coverage_floor_uses_min_executed_for_a_full_run(tmp_path, monkeypatch, capsys):
    """Pinned against MIN_EXECUTED itself, so raising the constant cannot silently stop
    the floor from biting."""
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "MIN_EXECUTED", 9)
    p = page([node()])
    p["src"] = None                              # G1 cannot run -> 8 executed, floor is 9
    monkeypatch.setattr(g, "measure", lambda *a, **k: [p])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    rc = g.main([str(deck), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["under_coverage_floor"] is True
    assert rc == 2


def test_the_floor_relaxes_only_for_an_explicit_only_selection(tmp_path, monkeypatch,
                                                               capsys):
    """--only G1 legitimately executes one rule. That must not trip a floor meant for
    full runs -- but it must still require that ONE rule actually ran."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck), "--only", "G1"]) == 0
    capsys.readouterr()


def test_an_only_selection_that_cannot_run_is_still_under_the_floor(tmp_path, monkeypatch,
                                                                    capsys):
    """--only G1 on a deck with nothing G1 can judge executes ZERO rules. Reporting clean there
    is the same bypass as the unknown-id case, one step further in."""
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    p = page([node(furniture=1)])       # sourced, but no leaf to judge: CANNOT_RUN
    monkeypatch.setattr(g, "measure", lambda *a, **k: [p])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    rc = g.main([str(deck), "--only", "G1", "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["counts"]["executed"] == 0
    assert payload["under_coverage_floor"] is True
    assert payload["clean"] is False
    assert rc == 2


def test_the_floor_prints_its_reason(tmp_path, monkeypatch, capsys):
    """A run that fails on coverage must say so in words, or a human reads 'NOT CLEAN'
    and goes looking for a rule that failed."""
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    p = page([node(furniture=1)])       # sourced, but no leaf to judge: CANNOT_RUN
    monkeypatch.setattr(g, "measure", lambda *a, **k: [p])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck), "--only", "G1"])
    assert "UNDER COVERAGE FLOOR" in capsys.readouterr().out


def test_a_clean_full_run_does_not_print_the_floor_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck)])
    assert "UNDER COVERAGE FLOOR" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# determinism: the font the page was measured in
#
# Observed 2026-09-19: the same deck and the same tool produced "9 pass, 0 fail" three
# times and "5 pass, 4 fail" once. The deck loads its family over the network with
# display=swap, so the probe could measure the FALLBACK face -- different metrics,
# different heights, a different verdict on an unchanged file. Fable raised it as F5
# before it was observed.
# --------------------------------------------------------------------------

def test_measure_refuses_a_fallback_face(tmp_path, monkeypatch):
    """Not a warning. Every height, clearance and trim measurement is a function of the
    face that rendered, so a fallback measurement measures a different document."""
    class FakeProc:
        returncode = 0
        stdout = ('<pre id="__geom">{"pages": [], "fontsMissing": ["Source Sans 3"]}</pre>')
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: FakeProc())
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="FALLBACK FACE"):
        g.measure(deck, "/bin/true")


def test_a_fallback_face_exits_four_not_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("measured in a FALLBACK FACE: Source Sans 3 did not load")))
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.main([str(deck)]) == 4
    assert "FALLBACK FACE" in capsys.readouterr().err


def test_measure_accepts_a_run_with_every_face_present(tmp_path, monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = '<pre id="__geom">{"pages": [{"page": 1}], "fontsMissing": []}</pre>'
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: FakeProc())
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.measure(deck, "/bin/true") == [{"page": 1}]


def test_measure_still_reads_the_older_list_envelope(tmp_path, monkeypatch):
    """A payload shape change must not silently return zero pages, which would read as
    'no pages matched' rather than as a parsing problem."""
    class FakeProc:
        returncode = 0
        stdout = '<pre id="__geom">[{"page": 1}]</pre>'
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: FakeProc())
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    assert g.measure(deck, "/bin/true") == [{"page": 1}]


# --------------------------------------------------------------------------
# colliding siblings
# --------------------------------------------------------------------------

def test_overlaps_x_distinguishes_columns_from_collisions():
    a = node(l=0, t=0, w=100, hh=20)
    beside = node(l=120, t=0, w=100, hh=20)
    on_top = node(l=0, t=10, w=100, hh=20)
    assert g._overlaps_x(a, beside) is False
    assert g._overlaps_x(a, on_top) is True


def test_g4_reports_two_siblings_that_collide():
    """Skipping every vertically-overlapping pair as 'side by side' meant no rule in the
    file could see one block sitting on top of another."""
    a = node(l=0, t=0, w=200, hh=40, ord_=0)
    b = node(l=0, t=20, w=200, hh=40, ord_=1)
    r = g.check_g4([page([a, b])])
    assert r.state == FAIL
    assert "OVERLAP" in r.offenders[0]


def test_g4_still_ignores_genuine_side_by_side_columns():
    a = node(l=0, t=0, w=100, hh=40, ord_=0)
    b = node(l=120, t=0, w=100, hh=40, ord_=1)
    assert g.check_g4([page([a, b])]).state == CANNOT_RUN


def test_g8_reports_colliding_text_blocks():
    a = node(l=0, t=0, w=200, hh=40, ord_=0, own="first")
    b = node(l=0, t=20, w=200, hh=40, ord_=1, own="second")
    r = g.check_g8([page([a, b])])
    assert r.state == FAIL
    assert "OVERLAP" in r.offenders[0]


def test_a_filtered_run_never_claims_full_coverage():
    """--only G1 executes one rule of nine. Calling that "CLEAN and FULLY COVERED" lets a
    caller select one rule and quote a sentence about the whole deck. Raised independently
    by two reviewers."""
    s = g.certification(total=1, fails=0, cannot=0, executed=1, under_floor=False,
                        filtered=True)
    assert "FULLY COVERED" not in s
    assert "PARTIAL RUN" in s


def test_an_unfiltered_clean_run_still_says_fully_covered():
    s = g.certification(total=9, fails=0, cannot=0, executed=9, under_floor=False,
                        filtered=False)
    assert "CLEAN and FULLY COVERED" in s


def test_a_filtered_run_reports_not_fully_covered_in_the_JSON_too(tmp_path, monkeypatch,
                                                                  capsys):
    """The first fix changed the printed sentence and left fully_covered True, so a machine
    consumer still read full coverage off a one-rule run."""
    import json as _json
    monkeypatch.setattr(g, "find_chrome", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(g, "measure", lambda *a, **k: [fully_covered_page()])
    deck = tmp_path / "d.html"
    deck.write_text("<div class='slide'></div>", encoding="utf-8")
    g.main([str(deck), "--only", "G1", "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["fully_covered"] is False
    assert payload["filtered"] is True
    assert payload["rules_available"] == 9


# Browser discovery moved to tests/scripts/test_chrome_runner.py on 2026-09-21.
# Two tests here monkeypatched `g.CHROME_CANDIDATES` to exercise find_chrome. When the
# mechanism moved to tools/chrome_runner.py they began failing -- not because the
# behaviour broke, but because they were testing a GENERALIZABLE INPUT at a CONSUMER,
# and patching a re-export does not reach the implementation. This suite tests what
# check_deck_geometry does that nothing else does: the geometry rules.


@needs_chrome
def test_live_probe_refuses_an_undeclared_primary_family(tmp_path):
    """110.F1 / 111.F1 (P0). document.fonts.check() returns true for a family no
    @font-face declares and no system provides, so the page was measured in the
    fallback face without a word. This fixture's own deck did exactly that until
    2026-09-23: it named "Test Sans", which does not exist."""
    deck = tmp_path / "ghost.html"
    deck.write_text(SMOKE_DECK.replace("font-family:Arial,sans-serif",
                                       'font-family:"Nonexistent Face Qz",sans-serif'),
                    encoding="utf-8")
    with pytest.raises(RuntimeError, match="FALLBACK FACE"):
        g.measure(deck, CHROME)


@needs_chrome
def test_live_probe_accepts_an_installed_system_family(tmp_path):
    deck = tmp_path / "arial.html"
    deck.write_text(SMOKE_DECK, encoding="utf-8")
    assert g.measure(deck, CHROME)


@pytest.mark.parametrize("which", [0, 2])
def test_g8_non_adjacent_collision_skips_table_scaffolding(which):
    """Table rows touch by construction; their spacing is cell padding, governed by the
    box half of G8. A non-adjacent pair involving a table part is not a text collision."""
    nodes = _non_adjacent_collision(own="words")
    nodes[which]["tag"] = "tr"
    r = g.check_g8([page(nodes)])
    assert not any("not DOM neighbours" in o for o in r.offenders)


@needs_chrome
@pytest.mark.parametrize("src", ['<p class="src"></p>',
                                 '<p class="src" style="display:none">Source: ledger.</p>',
                                 '<p class="src">   </p>'])
def test_live_an_empty_or_hidden_source_line_is_no_source_line(tmp_path, src):
    """Cross-model 2026-09-23 (Codex round 2, F3, P0). Any `.src` element counted, so an
    empty or hidden placeholder certified a page with no readable source."""
    deck = tmp_path / "blank-src.html"
    deck.write_text(SMOKE_DECK.replace('<p class="src">Source: the fictional widget ledger.</p>',
                                       src), encoding="utf-8")
    r = g.check_g1(g.measure(deck, CHROME))
    assert r.state == FAIL and "no source line" in r.offenders[0]
