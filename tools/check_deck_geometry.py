"""check_deck_geometry.py -- the deck rules that can only be checked against the RENDER.

WHY THIS IS A SEPARATE INSTRUMENT FROM check_deck_craft.py. That script parses the deck's
HTML. Everything it knows, it knows from the markup, and the markup does not contain the
answer to "does this element fit" or "is this edge aligned". Three failures paid for this file:

  - 2026-09-17: a rewrite consumed slide 1's closing </div>, nesting page 2 inside page 1 so
    it rendered BLANK. check_deck_craft found two .slide blocks and passed NINE rules against
    a page that would not render. Only the PNG caught it.
  - 2026-09-19: a table header cell left at white-space:nowrap set the table 28px wider than
    its column and clipped the prior-service values off the right of the page.
  - 2026-09-19: both pages' boxes crossed the source line, and slide 1's flow column sat at
    EXACTLY 0px clearance, one word of copy from breaking.

MEASURE THE RIGHT NODE. The first probe written that day reported "+5px clear" while a box
was visibly crossing the source line, because it measured the COLUMN: a flex child overflows
its container without changing the container's measured height, so a container measurement
reports clearance its own contents do not have. G1 therefore judges LEAVES ONLY.

THAT IS G1's RULE, NOT A UNIVERSAL ONE, and an earlier version of this paragraph claimed
otherwise -- "every rule here walks to leaves" -- which was false and was caught by
cross-model review on 2026-09-19. Containers are the CORRECT subject for most of the others:
G2 checks every parented node against its parent, G3 every block edge, G5 and G7 every node
that renders text, G8 sibling pairs and painted containers. Generalising one rule's policy
into a property of the file made the docstring assert something the code does not do.

AND A LEAF IS A BLOCK WITH NO BLOCK CHILDREN. `children.length === 0` disqualified every
text block containing a <b>, <span> or <br>, so G1 silently judged 11 of 37 nodes on page 1
-- both standfirsts excluded -- and reported that subset's tightest clearance as the page's.

THE NINE RULES:

  G1 CLEARANCE   every visible leaf on the page clears the source line by >= 8px.
                 8 and not 0: zero is not "fits", it is "one word from broken".
  G2 CONTAINMENT no element's box escapes its parent's padding box by more than 1px.
  G3 ALIGNMENT   every block element's LEFT edge, and every VISIBLE RIGHT edge, falls on a
                 4px grid measured from the slide's own content origin. A right edge counts
                 as visible when the element paints a background, carries a border, or is a
                 drawn boundary (svg, img, table, td, th). A run of text ends wherever the
                 sentence ends; that ragged edge is not an alignment surface and forcing it
                 onto a grid would mean giving every paragraph an arbitrary fixed width.
                 This narrows WHICH EDGES ARE ALIGNMENT-BEARING. It does not touch GRID or
                 TOL, and every edge a reader can see is still checked.
  G4 SPACING     every vertical gap between stacked siblings is a multiple of 4px.
  G5 TYPE        every rendered size, ON THE PAGE AND INSIDE THE CHARTS, comes from a
                 six-entry scale, in one family. Chart type is compared at its EFFECTIVE
                 size, after the viewBox scale, because that is what a reader sees.
  G6 SVG SLACK   no chart carries whitespace inside its own viewBox.
  G7 LEADING     every text block's half-leading is trimmed, MEASURED by height rather
                 than taken from the declared property.
  G8 CROWDING    the spacing left after that trim clears 8px between text blocks, and any
                 painted box pads its text by 8px on the edges it actually has.
  G9 FILL        every chart fills its box, so the edge on the grid is the edge a reader
                 sees. G3 measures the element box and G6 the viewBox; only this rule
                 sees the gap between them.

FURNITURE IS A NAMED THING. G1, G2, G4 and G8 skip the source line and the page number,
identified by the deck's own class conventions. An earlier version skipped anything
absolutely positioned, which let real content opt out of four rules by changing one CSS
property.

A PASS OVER ZERO ELEMENTS IS NOT A PASS. Five separate times this file shipped a rule
reporting green while its population was empty -- G8's "all 0 painted box(es)", G7 passing
35 blocks by declaration and 0 by measurement, G1 judging a subset, the probe walking only
`.body`, and `--only BAD` selecting no rules at all. Every rule now reports its denominator
in its own PASS text, MIN_EXECUTED is a coverage floor on the run, and an unknown --only id
is an error rather than a filter.

G3 AND G4 ARE A REAL GRID, NOT A NEAR-MISS HEURISTIC. An earlier draft flagged only edges
1-6px apart, on the argument that a full grid would be noisy. Nick's ruling 2026-09-19: make
it a real grid and make the DECK conform. That ordering is the whole point. A rule that
bends to the artifact measures nothing; the artifact bends to the rule.

WHY THERE ARE NO --grid OR --tolerance FLAGS. A threshold flag is an invitation to loosen the
rule under deadline, which is how a gate becomes decoration. GRID and TOL are module
constants. The tests reach in and patch them; a caller cannot.

TWO EXCLUSIONS, AND THEY ARE CORRECTNESS RATHER THAN LENIENCY:
  - Nothing inside an <svg> is measured. An SVG is ONE layout element with its own internal
    coordinate system; its <rect> and <text> children are chart data plotted from values, not
    page furniture, and putting bar geometry on a layout grid would be meaningless.
  - Inline elements (b, i, span, a, em, strong, br, sup, sub) are not measured. Their edges
    are wherever the line breaks fall, which is a property of the sentence, not the layout.
  Nor is anything NESTED INSIDE an inline element, for the same reason: a coloured swatch
  inside a legend <span> has visible edges but a text-flow position.
  All three are enforced in the probe, so an excluded node never reaches a rule to be
  argued about.

EXIT: 0 no FAILs. 2 at least one FAIL. 4 the deck could not be read, or no browser was found
and therefore NOTHING WAS MEASURED, which is not a pass.

Run:  PYTHONIOENCODING=utf-8 python3 tools/check_deck_geometry.py <deck.html>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_deck_craft import CANNOT_RUN, FAIL, PASS, Result  # noqa: E402

# 8px, not 0. Slide 1 sat at exactly 0 on 2026-09-19: not overlapping, and one word of copy
# anywhere on the page from overlapping. A gate that passes 0 has no useful failing mode.
MIN_CLEARANCE = 8

# 1px absorbs subpixel layout on a containment test. Above it is a real escape.
CONTAIN_TOL = 1

# THE GRID. 4px, from the slide's content origin horizontally and between siblings
# vertically. Not a flag. See the module docstring.
GRID = 4

# Subpixel allowance. A browser computes layout in fractional units and rounds to device
# pixels, so an edge authored at exactly 52px can measure 51.99. This is a RENDERING
# artifact allowance, not a policy knob: 0.5 is below the threshold of a visible
# misalignment, and raising it to absorb a real 2px error would be loosening the rule.
TOL = 0.5

MAX_OFFENDERS = 15

# How many of the nine rules must actually EXECUTE for a run to mean anything. Eight of
# nine: a legitimate deck can lack charts entirely (G6 and G9 cannot run), but a deck on
# which most rules find nothing to measure has not been tested. Without this floor,
# "no FAIL" returned 0 even when every rule reported CANNOT_RUN.
MIN_EXECUTED = 6

# THE TYPE SCALE, Nick 2026-09-19. Before it, the deck carried SEVENTEEN distinct font
# sizes including four near-duplicate pairs (9.5/10/10.5, 11/11.5, 12/12.5, 15/15.5).
# A reader cannot tell 15 from 15.5; the code can, and that is what "the same class of
# text" drifting looks like. Six sizes, roughly a 1.25 ratio:
#   11 source lines, eyebrow labels, chart annotations   13 secondary and table labels
#   16 all body text                                     20 section titles, emphasis rows
#   30 the page lede                                     44 the single KPI number
TYPE_SCALE = (11, 13, 16, 20, 30, 44)

# A declared size may sit this far from a scale entry. Browsers report font-size exactly
# as authored, so this only absorbs float representation, NOT a different size.
TYPE_TOL = 0.01

# SVG viewBox slack, in user units. Above this the drawing does not fill its own box.
SVG_SLACK_TOL = 1.0
# The other side of the same measurement. Below -SVG_CLIP_TOL, drawing is outside the viewBox
# and the reader loses it. Above it and below SVG_SLACK_TOL is a stroke on the boundary, which
# is ink and must not fail. See check_g6 for how the two numbers were measured apart.
SVG_CLIP_TOL = 2.0

# Elements too small to carry a readable type size: a swatch, a rule, a spacer.
MIN_TEXT_CHARS = 1

# THE FLOOR UNDER THE TRIM. Both are grid values, so G4 and G8 cannot pull in opposite
# directions. 8px is one grid step of real separation; below it two text blocks read as
# one. See check_g8's docstring for why trimming the leading makes this necessary.
MIN_TEXT_GAP = 8
MIN_BOX_PAD = 8

# Table internals. Their vertical spacing is cell padding, not a margin between blocks.
TABLE_PARTS = frozenset({"table", "thead", "tbody", "tfoot", "tr", "td", "th"})

# Injected into a copy of the deck. Writes its payload into a <pre> because --dump-dom
# returns the serialised DOM: a <title> truncates and a console message is not in the dump.
PROBE = r"""
<pre id="__geom" style="display:none"></pre>
<script>
function __run() {
  var INLINE = {B:1, I:1, SPAN:1, A:1, EM:1, STRONG:1, BR:1, SUP:1, SUB:1, U:1, SMALL:1};
  function box(el) {
    var r = el.getBoundingClientRect();
    return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height};
  }
  function label(el) {
    var cls = (el.getAttribute && el.getAttribute('class')) || '';
    var txt = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    return el.tagName.toLowerCase() + (cls ? '.' + String(cls).split(/\s+/)[0] : '') +
           (txt ? ' "' + txt + '"' : '');
  }
  // Text this element owns directly, not what it inherits from descendants. Only an
  // element that actually renders glyphs has a type size worth asserting.
  function ownText(el) {
    var t = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3) t += n.nodeValue;
      else if (n.nodeType === 1 && INLINE[n.tagName.toUpperCase()]) t += n.textContent;
    }
    return t.replace(/\s+/g, ' ').trim();
  }
  // WHITESPACE BAKED INTO AN SVG. The drawing sits inside a viewBox that may be larger
  // than the drawing, so the element's box edge is NOT the visible edge and placing the
  // box on the grid places nothing a reader can see. Returns the slack on each side in
  // user units: zero on all four means the viewBox hugs the drawing.
  function svgSlack(el) {
    var vb = (el.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(parseFloat);
    if (vb.length !== 4 || vb.some(isNaN)) return null;
    var bb;
    try { bb = el.getBBox(); } catch (e) { return null; }
    if (!bb || !isFinite(bb.width) || bb.width === 0) return null;
    return {left: bb.x - vb[0], top: bb.y - vb[1],
            right: (vb[0] + vb[2]) - (bb.x + bb.width),
            bottom: (vb[1] + vb[3]) - (bb.y + bb.height),
            vb: vb};
  }
  // How many LINES the element renders. Range rects give one rect per inline fragment,
  // so fragments are merged by their top coordinate: distinct tops are distinct lines.
  function lineCount(el) {
    try {
      var rg = document.createRange();
      rg.selectNodeContents(el);
      var rects = rg.getClientRects(), tops = {}, n = 0;
      for (var i = 0; i < rects.length; i++) {
        if (rects[i].height === 0 && rects[i].width === 0) continue;
        var k = Math.round(rects[i].top * 2) / 2;
        if (!tops[k]) { tops[k] = 1; n++; }
      }
      return n;
    } catch (e) { return 0; }
  }
  // THE TRIM SIGNAL. Range rects always report the UNTRIMMED line box, so when the leading
  // is trimmed the element's content box starts BELOW the line box top by the trimmed
  // amount. Untrimmed reads 0. Confounded for a box the layout stretches -- a table cell in
  // a taller row baseline-aligns and reads NEGATIVE -- which is why check_g7 also accepts
  // the declared property.
  function topSlack(el, cs) {
    try {
      var r = el.getBoundingClientRect();
      var ct = r.top + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.paddingTop) || 0);
      var rg = document.createRange();
      rg.selectNodeContents(el);
      var rs = rg.getClientRects(), top = Infinity;
      for (var i = 0; i < rs.length; i++) {
        if (rs[i].height === 0) continue;
        if (rs[i].top < top) top = rs[i].top;
      }
      return isFinite(top) ? (ct - top) : null;
    } catch (e) { return null; }
  }
  // Effective on-screen size of every <text> inside a chart, counted by size.
  function svgTypeSizes(el) {
    var vb = (el.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(parseFloat);
    var r = el.getBoundingClientRect();
    if (vb.length !== 4 || vb.some(isNaN) || !vb[2] || !vb[3] || !r.width || !r.height) {
      return null;
    }
    // preserveAspectRatio defaults to "meet", so the applied scale is the SMALLER one.
    var scale = Math.min(r.width / vb[2], r.height / vb[3]);
    var out = {}, texts = el.querySelectorAll('text, tspan');
    for (var i = 0; i < texts.length; i++) {
      var fs = parseFloat(getComputedStyle(texts[i]).fontSize);
      if (!fs) continue;
      var eff = Math.round(fs * scale * 100) / 100;
      out[eff] = (out[eff] || 0) + 1;
    }
    return out;
  }
  // How much of the element's box the drawing actually fills. preserveAspectRatio="meet"
  // letterboxes a chart whose box aspect differs from its viewBox aspect, so the visible
  // chart starts somewhere inside an element whose EDGE is perfectly on the grid. G3
  // measures the box and G6 measures the viewBox; neither sees the gap between them.
  function svgLetterbox(el) {
    var vb = (el.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(parseFloat);
    var r = el.getBoundingClientRect();
    if (vb.length !== 4 || vb.some(isNaN) || !vb[2] || !vb[3] || !r.width || !r.height) {
      return null;
    }
    if ((el.getAttribute('preserveAspectRatio') || '').indexOf('none') >= 0) {
      return {x: 0, y: 0};
    }
    var scale = Math.min(r.width / vb[2], r.height / vb[3]);
    return {x: (r.width - vb[2] * scale) / 2, y: (r.height - vb[3] * scale) / 2};
  }
  var seq = 0;
  var pages = [];
  document.querySelectorAll('.slide').forEach(function (slide, idx) {
    var srcEl = slide.querySelector('.src');
    // WALK THE WHOLE SLIDE, not just .body. Until 2026-09-19 this walked `.body` alone,
    // so the LEDE, the STANDFIRST, the rule and the source line -- the entire top third of
    // the page and its most-read sentence -- were never measured by any rule, while the
    // run reported "8 pass, 0 fail". A gate silently not looking at a region is the same
    // defect as measuring containers instead of leaves: the number is green and the
    // question was never asked. Found by rendering the page and seeing the standfirst
    // crammed against the lede on a fully green run.
    var body = slide;
    var cs0 = getComputedStyle(slide);
    // The horizontal origin every G3 edge is measured from: the slide's CONTENT box, so the
    // grid is a property of the page rather than of where the page sits in the viewport.
    var originX = slide.getBoundingClientRect().left +
                  (parseFloat(cs0.paddingLeft) || 0) + (parseFloat(cs0.borderLeftWidth) || 0);
    var nodes = [];
    var walk = function (el) {
      for (var i = 0; i < el.children.length; i++) {
        var c = el.children[i];
        var tag = c.tagName.toUpperCase();
        if (tag === 'SCRIPT' || tag === 'STYLE') continue;
        var r = c.getBoundingClientRect();
        var cs = getComputedStyle(c);
        var drawn = r.width > 0 && r.height > 0 &&
                    cs.visibility !== 'hidden' && cs.display !== 'none';
        var isSvg = (tag === 'SVG');
        // IS THE RIGHT EDGE VISIBLE? A run of text ends wherever the sentence ends, and
        // that ragged edge is not something a reader can align to -- there is no border or
        // fill there to see. A box with a background, a border, or a drawn boundary (svg,
        // img, table, hr) DOES have a right edge you can see, and it must sit on the grid.
        // The left edge is always checked: text starts there, and a reader sees it.
        var bg = cs.backgroundColor || '';
        var bgPaints = bg && bg !== 'transparent' &&
                       !/rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0\s*\)/.test(bg);
        var borderSides = ((parseFloat(cs.borderRightWidth) || 0) > 0 ? 1 : 0) +
                          ((parseFloat(cs.borderLeftWidth) || 0) > 0 ? 1 : 0) +
                          ((parseFloat(cs.borderTopWidth) || 0) > 0 ? 1 : 0) +
                          ((parseFloat(cs.borderBottomWidth) || 0) > 0 ? 1 : 0);
        var hasBorder = borderSides > 0;
        var drawnBoundary = {SVG:1, IMG:1, TABLE:1, HR:1, TD:1, TH:1}[tag] === 1;
        var rightVisible = bgPaints || hasBorder || drawnBoundary;
        if (drawn && !INLINE[tag]) {
          var p = c.parentElement, pbox = null;
          if (p) {
            var pr = p.getBoundingClientRect();
            pbox = {l: pr.left + p.clientLeft, t: pr.top + p.clientTop,
                    r: pr.left + p.clientLeft + p.clientWidth,
                    b: pr.top + p.clientTop + p.clientHeight};
          }
          if (!p.__gid) { p.__gid = 'p' + (++seq); }
          // A LEAF IS A BLOCK WITH NO BLOCK CHILDREN, not an element with no children at
          // all. `children.length === 0` disqualified every text block containing a <b>,
          // <span> or <br> -- on this deck BOTH standfirsts and 18 of 85 blocks -- so G1
          // silently judged a subset and reported that subset's tightest clearance as the
          // page's. Inline children are text, not layout, and must not disqualify.
          var blockKids = 0;
          for (var k = 0; k < c.children.length; k++) {
            var kt = c.children[k].tagName.toUpperCase();
            if (INLINE[kt]) continue;
            var kr = c.children[k].getBoundingClientRect();
            if (kr.width > 0 && kr.height > 0) blockKids++;
          }
          nodes.push({
            box: box(c), label: label(c), tag: tag.toLowerCase(),
            rightVisible: rightVisible,
            fontSize: parseFloat(cs.fontSize) || 0,
            fontFamily: (cs.fontFamily || '').split(',')[0].replace(/['"]/g, '').trim(),
            lineHeight: (cs.lineHeight === 'normal') ? -1 : (parseFloat(cs.lineHeight) || 0),
            ownText: ownText(c),
            absolute: (cs.position === 'absolute' || cs.position === 'fixed') ? 1 : 0,
            // FURNITURE IS A NAMED THING, NOT ANY OUT-OF-FLOW THING. Exempting every
            // absolutely positioned element from G1/G2/G4/G8 let arbitrary CONTENT opt
            // out of four rules by changing one CSS property. Only the source line, the
            // page number and the trackers are furniture, identified by the deck's own
            // class conventions -- the same ones check_deck_craft reads.
            furniture: (function () {
              if (cs.position !== 'absolute' && cs.position !== 'fixed') return 0;
              var cl = (c.getAttribute('class') || '').toString().split(/\s+/);
              for (var q = 0; q < cl.length; q++) {
                if (cl[q] === 'src' || cl[q] === 'source' || cl[q] === 'pg'
                    || cl[q] === 'page' || cl[q] === 'tracker' || cl[q] === 'sticker') {
                  return 1;
                }
              }
              return 0;
            })(),
            // Type INSIDE a chart. The probe does not descend into an SVG for layout, but
            // a chart label is still text a reader reads, and the rule is that one class
            // of text has one size. Reported as the EFFECTIVE rendered size: the authored
            // size scaled by however the viewBox maps onto the element box.
            svgType: isSvg ? svgTypeSizes(c) : null,
            svgLetterbox: isSvg ? svgLetterbox(c) : null,
            hasText: (c.textContent || '').trim().length,
            lineCount: lineCount(c),
            trimDeclared: (cs.textBoxTrim && cs.textBoxTrim !== 'none') ? 1 : 0,
            topSlack: topSlack(c, cs),
            padded: {t: parseFloat(cs.paddingTop) || 0, b: parseFloat(cs.paddingBottom) || 0,
                     l: parseFloat(cs.paddingLeft) || 0, r: parseFloat(cs.paddingRight) || 0},
            // A BOX, for the padding half of G8: something a reader sees as a
            // container. A fill is a box. A border on all four sides is a box. And a
            // LEFT OR RIGHT ACCENT BAR on something holding text is a box too -- that was
            // the hole: the deck's accent callouts were padded 4px top and bottom and G8
            // exempted them as "rules". A genuine rule is a TOP or BOTTOM border, an
            // underline or a divider, which owes its text nothing on the sides it lacks.
            accentOnly: (!bgPaints && borderSides < 4 &&
                         (((parseFloat(cs.borderLeftWidth) || 0) > 0) ||
                          ((parseFloat(cs.borderRightWidth) || 0) > 0))) ? 1 : 0,
            accentLeft: ((parseFloat(cs.borderLeftWidth) || 0) > 0) ? 1 : 0,
            isBox: (bgPaints || borderSides === 4 ||
                    ((parseFloat(cs.borderLeftWidth) || 0) > 0) ||
                    ((parseFloat(cs.borderRightWidth) || 0) > 0)) ? 1 : 0,
            contentH: c.clientHeight - (parseFloat(cs.paddingTop) || 0)
                                     - (parseFloat(cs.paddingBottom) || 0),
            svgSlack: isSvg ? svgSlack(c) : null,
            // An <svg> is a LEAF for every rule here: we never look inside it.
            leaf: isSvg || blockKids === 0,
            parent: pbox, pid: p.__gid,
            ord: Array.prototype.indexOf.call(p.children, c)
          });
        }
        // Never descend into an SVG (children are plotted data, not layout) and never
        // descend into an INLINE element: anything inside a run of text is positioned by
        // where the line broke, which is a property of the sentence. The legend swatches
        // are the case that forced this -- an <svg> square inside a <span>, whose edges
        // are visible but whose POSITION is text flow, not layout.
        if (!isSvg && !INLINE[tag]) walk(c);
      }
    };
    walk(body);
    pages.push({page: idx + 1, originX: originX,
                // A source line must be READABLE: an empty or hidden placeholder is not
                // one (cross-model 2026-09-23, Codex round 2, F3).
                src: (srcEl && srcEl.textContent.trim() && srcEl.getClientRects().length
                      && getComputedStyle(srcEl).visibility !== 'hidden')
                     ? box(srcEl) : null,
                slide: box(slide), nodes: nodes});
  });
  // WHICH FACES WERE ACTUALLY AVAILABLE WHEN THIS WAS MEASURED. The deck loads its
  // family over the network with display=swap, so a slow or absent fetch measures the
  // FALLBACK face -- different metrics, different heights, a different verdict on the
  // same file. Observed 2026-09-19: one run reported 4 failures between three runs that
  // reported none. A gate whose answer depends on a network fetch is not a measurement.
  var fams = {};
  pages.forEach(function (pg) {
    pg.nodes.forEach(function (n) { if (n.fontFamily) fams[n.fontFamily] = 1; });
  });
  // document.fonts.check() is NOT proof the face rendered: it returns true for a family
  // that no @font-face declares and no system provides, so a nonexistent primary family
  // was measured in fallback metrics without a word (cross-model 110.F1 / 111.F1, P0).
  // The test that holds for webfonts and installed faces alike: text set in the family
  // must measure differently from text set in each generic fallback. If it matches all
  // three, the browser drew a fallback.
  var GENERIC = /^(serif|sans-serif|monospace|cursive|fantasy|system-ui|math|emoji|ui-[a-z-]+|-apple-system)$/i;
  var ctx = document.createElement('canvas').getContext('2d');
  var SAMPLE = 'mmmmmmmmmmlli1WQ@#&';
  function rendered(f) {
    if (GENERIC.test(f)) return true;
    var bases = ['monospace', 'serif', 'sans-serif'];
    for (var i = 0; i < bases.length; i++) {
      ctx.font = '72px ' + bases[i];
      var w0 = ctx.measureText(SAMPLE).width;
      ctx.font = '72px "' + f + '", ' + bases[i];
      if (ctx.measureText(SAMPLE).width !== w0) return true;
    }
    return false;
  }
  var missing = [];
  for (var f in fams) {
    try { if (!document.fonts.check('16px "' + f + '"') || !rendered(f)) missing.push(f); }
    catch (e) { missing.push(f); }
  }
  document.getElementById('__geom').textContent =
    JSON.stringify({pages: pages, fontsMissing: missing});
}
// document.fonts.ready, not the load event: load fires before a swapped webfont lands.
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(function () { setTimeout(__run, 0); });
} else {
  window.addEventListener('load', __run);
}
</script>
"""


# find_chrome and CHROME_CANDIDATES moved to tools/chrome_runner.py on 2026-09-21.
# They were byte-identical to deck_to_pdf.py's copy, and a third caller hardcoded a
# path with no fallback. Re-exported here so existing callers and tests that reference
# `cdg.find_chrome` keep working against ONE implementation.
from chrome_runner import CHROME_CANDIDATES, find_chrome  # noqa: E402,F401


def measure(deck: Path, chrome: str, timeout: int = 90) -> list:
    """Render the deck and return per-page geometry. Raises RuntimeError on failure."""
    html = deck.read_text(encoding="utf-8")
    tmpdir = Path(tempfile.mkdtemp(prefix="deckgeom-"))
    try:
        probe_file = tmpdir / "probe.html"
        probe_file.write_text(html + PROBE, encoding="utf-8")
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--window-size=1280,3000", "--virtual-time-budget=4000",
             "--dump-dom", str(probe_file)],
            capture_output=True, text=True, timeout=timeout)
        m = re.search(r'<pre id="__geom"[^>]*>(.*?)</pre>', proc.stdout, re.S)
        if not m or not m.group(1).strip():
            raise RuntimeError(
                "the probe produced no geometry. Chrome exited " + str(proc.returncode)
                + ". This is NOT a pass: nothing was measured.")
        raw = m.group(1)
        for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
            raw = raw.replace(a, b)
        payload = json.loads(raw)
        if isinstance(payload, list):          # pre-2026-09-19 envelope
            return payload
        missing = payload.get("fontsMissing") or []
        if missing:
            # NOT a warning. Every height, every clearance and every trim measurement is
            # a function of the face that rendered, so a fallback measurement is a
            # measurement of a different document.
            raise RuntimeError(
                "measured in a FALLBACK FACE: " + ", ".join(sorted(missing))
                + " did not load, so every height on this page is the wrong height. "
                "This is not a measurement of your deck.")
        return payload.get("pages") or []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def off_grid(value: float) -> float:
    """Distance from `value` to the nearest multiple of GRID. 0 when on the grid."""
    return abs(value - round(value / GRID) * GRID)


def check_g1(pages) -> Result:
    """Every page carries a source line, and every visible LEAF clears it.

    EVERY PAGE NEEDS A SOURCE (Nick, 2026-09-23). Pages without a `.src` used to be
    dropped, so one page's clean measurement certified clearance on a page G1 never
    judged (cross-model 108.F2, P0). A page with no source line is now an offender.
    """
    if not pages:
        return Result("G1", CANNOT_RUN, "no pages were measured")
    offenders = ["page " + str(p["page"]) + ": no source line (.src with visible text) -- "
                 "every page needs one" for p in pages if not p.get("src")]
    measured = [p for p in pages if p.get("src")]
    worst = None
    judged = 0
    for p in measured:
        top = p["src"]["t"]
        for n in p["nodes"]:
            if not n["leaf"] or n.get("furniture"):
                continue          # .src and .pg ARE the furniture; they cannot crowd it
            judged += 1
            gap = round(top - n["box"]["b"], 1)
            if worst is None or gap < worst:
                worst = gap
            if gap < MIN_CLEARANCE:
                offenders.append("page " + str(p["page"]) + ": " + str(gap)
                                 + "px clearance (min " + str(MIN_CLEARANCE) + ") -- "
                                 + n["label"])
    if offenders:
        return Result("G1", FAIL,
                      str(len(offenders)) + " leaf element(s) sit closer than "
                      + str(MIN_CLEARANCE) + "px to the source line",
                      offenders[:MAX_OFFENDERS])
    if not judged:
        # A PASS over zero elements is the defect this gate has now shipped five times.
        return Result("G1", CANNOT_RUN,
                      "no leaf element was judged on any of the " + str(len(measured))
                      + " page(s), so nothing was measured against the source line")
    return Result("G1", PASS,
                  "all " + str(judged) + " leaf element(s) on " + str(len(measured))
                  + " page(s) clear the source line; tightest is " + str(worst) + "px")


def check_g2(pages) -> Result:
    """No element escapes its parent's padding box."""
    offenders = []
    checked = 0
    for p in pages:
        for n in p["nodes"]:
            par = n.get("parent")
            if not par or n.get("furniture"):
                continue
            checked += 1
            b = n["box"]
            # TOP included. Without it a child pulled upward by a negative margin --
            # exactly what the rejected ::before leading-trim hack did -- could escape its
            # parent and overlap whatever sits above, and containment stayed green.
            for side, amount in (("right", b["r"] - par["r"]), ("left", par["l"] - b["l"]),
                                 ("bottom", b["b"] - par["b"]), ("top", par["t"] - b["t"])):
                if amount > CONTAIN_TOL:
                    offenders.append("page " + str(p["page"]) + ": escapes its parent by "
                                     + str(round(amount, 1)) + "px on the " + side
                                     + " -- " + n["label"])
    if not checked:
        return Result("G2", CANNOT_RUN, "no parented elements were measured")
    if offenders:
        return Result("G2", FAIL,
                      str(len(offenders)) + " element(s) escape their parent's padding box",
                      offenders[:MAX_OFFENDERS])
    return Result("G2", PASS,
                  "all " + str(checked) + " measured element(s) sit inside their parent")


def check_g3(pages) -> Result:
    """Every block element's left and right edge sits on the GRID."""
    offenders = []
    checked = 0
    edges_checked_right = []
    for p in pages:
        ox = p["originX"]
        for n in p["nodes"]:
            checked += 1
            # NOTE ON RIGHT-ANCHORED ELEMENTS. An element placed with `right:` has its
            # right edge positioned by layout and its left edge wherever its glyphs begin.
            # Detecting that from the DOM does not work: Chrome's getComputedStyle returns
            # the USED value for `left`, never 'auto', so an anchor test always reads as
            # left-anchored. An earlier version carried such a test and it could never
            # fire. Rather than keep a branch with no reachable path in a guard, the rule
            # stays uniform and the ARTIFACT conforms: give a right-anchored element an
            # explicit width and both of its edges land on the grid.
            sides = [("left", n["box"]["l"] - ox)]
            # See the probe: a ragged text right edge is not an alignment surface. This
            # narrows WHICH EDGES ARE ALIGNMENT-BEARING; it does not touch GRID or TOL,
            # and every edge a reader can actually see is still checked.
            if n.get("rightVisible"):
                edges_checked_right.append(1)
                sides.append(("right", n["box"]["r"] - ox))
            for side, x in sides:
                d = off_grid(x)
                if d > TOL:
                    offenders.append("page " + str(p["page"]) + ": " + side + " edge at "
                                     + str(round(x, 2)) + "px from the content origin is "
                                     + str(round(d, 2)) + "px off the " + str(GRID)
                                     + "px grid -- " + n["label"])
    if not checked:
        return Result("G3", CANNOT_RUN, "no block elements were measured")
    if offenders:
        return Result("G3", FAIL,
                      str(len(offenders)) + " edge(s) off the " + str(GRID) + "px grid",
                      offenders[:MAX_OFFENDERS])
    return Result("G3", PASS,
                  "all " + str(checked + len(edges_checked_right)) + " alignment-bearing "
                  "edge(s) sit on the " + str(GRID) + "px grid: " + str(checked)
                  + " left edge(s) and " + str(len(edges_checked_right))
                  + " visible right edge(s), across " + str(checked) + " element(s)")


def _overlaps_x(a, b) -> bool:
    """Do these two boxes share horizontal extent? If not they sit side by side."""
    return (min(a["box"]["r"], b["box"]["r"]) - max(a["box"]["l"], b["box"]["l"])) > TOL


def _overlap_y(a, b) -> float:
    return round(min(a["box"]["b"], b["box"]["b"]) - max(a["box"]["t"], b["box"]["t"]), 1)


def _non_adjacent_collisions(members):
    """Sibling pairs that are NOT DOM neighbours and whose boxes intersect on both axes.

    G4 and G8 walked zip(members, members[1:]), so a positioned sibling drawn back over an
    earlier one was never compared with it (cross-model 110.F4 / 111.F4, P0). COLLISION is
    checked across all pairs; GAPS stay between neighbours, because only neighbours have a
    gap anyone laid out. Neighbour collisions are reported by the gap loops as before.
    """
    for i, a in enumerate(members):
        for b in members[i + 2:]:
            if _overlaps_x(a, b) and _overlap_y(a, b) > TOL:
                yield a, b


def check_g4(pages) -> Result:
    """Every vertical gap between stacked siblings is a multiple of GRID."""
    offenders = []
    measured = 0
    for p in pages:
        groups = {}
        for n in p["nodes"]:
            groups.setdefault(n["pid"], []).append(n)
        for members in groups.values():
            members = [m for m in members if not m.get("furniture")]
            members.sort(key=lambda n: n["ord"])
            for a, b in _non_adjacent_collisions(members):
                offenders.append("page " + str(p["page"]) + ": two siblings OVERLAP by "
                                 + str(_overlap_y(a, b)) + "px (not DOM neighbours) -- "
                                 + a["label"] + "  ->  " + b["label"])
            for a, b in zip(members, members[1:]):
                # Vertical stack only. Siblings side by side have no vertical gap to reason
                # about, and their horizontal spacing is G3's business. But "side by side"
                # means they do not overlap HORIZONTALLY; two blocks that overlap on both
                # axes are COLLIDING, and skipping those as side-by-side meant no rule in
                # the file could see one block sitting on top of another.
                if b["box"]["t"] < a["box"]["b"] - TOL:
                    if _overlaps_x(a, b):
                        offenders.append("page " + str(p["page"]) + ": two siblings "
                                         "OVERLAP by " + str(round(a["box"]["b"]
                                         - b["box"]["t"], 1)) + "px -- " + a["label"]
                                         + "  ->  " + b["label"])
                    continue
                gap = b["box"]["t"] - a["box"]["b"]
                measured += 1
                d = off_grid(gap)
                if d > TOL:
                    offenders.append("page " + str(p["page"]) + ": vertical gap of "
                                     + str(round(gap, 2)) + "px is " + str(round(d, 2))
                                     + "px off the " + str(GRID) + "px grid -- "
                                     + a["label"] + "  ->  " + b["label"])
    # OFFENDERS FIRST. A page whose only stacked pairs COLLIDE measures zero clean gaps,
    # and checking the empty-population branch first reported "nothing measured" on a page
    # with elements sitting on top of each other.
    if not measured and not offenders:
        return Result("G4", CANNOT_RUN, "no vertically stacked sibling pairs were measured")
    if offenders:
        return Result("G4", FAIL,
                      str(len(offenders)) + " of " + str(measured) + " vertical gap(s) are "
                      "off the " + str(GRID) + "px grid",
                      offenders[:MAX_OFFENDERS])
    return Result("G4", PASS,
                  "all " + str(measured) + " vertical gap(s) are multiples of "
                  + str(GRID) + "px")


def check_g5(pages) -> Result:
    """Every rendered type size comes from TYPE_SCALE, and the deck uses one family."""
    offenders = []
    sizes = {}
    families = {}
    for p in pages:
        for n in p["nodes"]:
            if len(n.get("ownText") or "") < MIN_TEXT_CHARS:
                continue
            fs = n.get("fontSize") or 0
            if not fs:
                continue
            sizes.setdefault(fs, []).append((p["page"], n["label"]))
            fam = n.get("fontFamily") or ""
            if fam:
                families.setdefault(fam, []).append((p["page"], n["label"]))
    # CHART LABELS ARE TEXT A READER READS. The probe does not descend into an SVG for
    # LAYOUT, and that is right -- bar geometry is plotted data, not page furniture -- but
    # excluding the chart from the TYPE rule let this deck carry labels at an effective
    # 8.6, 10.3 and 20.9px while G5 reported every size on the scale. The size compared is
    # the EFFECTIVE one: authored size scaled by the viewBox mapping, i.e. what the reader
    # actually sees, not what the SVG source says.
    chart_sizes = {}
    for p in pages:
        for n in p["nodes"]:
            for eff, count in (n.get("svgType") or {}).items():
                chart_sizes.setdefault(float(eff), []).append((p["page"], n["label"]))
    for fs in sorted(chart_sizes):
        if min(abs(fs - s) for s in TYPE_SCALE) > TYPE_TOL:
            page, lab = chart_sizes[fs][0]
            offenders.append(str(round(fs, 2)) + "px of CHART type is not on the scale "
                             + str(list(TYPE_SCALE)) + " (effective size after the viewBox "
                             "scale; " + str(len(chart_sizes[fs])) + " chart(s), e.g. page "
                             + str(page) + " " + lab + ")")
    if not sizes and not chart_sizes:
        return Result("G5", CANNOT_RUN, "no element renders text of its own")
    for fs in sorted(sizes):
        if min(abs(fs - s) for s in TYPE_SCALE) > TYPE_TOL:
            page, lab = sizes[fs][0]
            offenders.append(str(round(fs, 2)) + "px is not on the scale "
                             + str(list(TYPE_SCALE)) + " (" + str(len(sizes[fs]))
                             + " element(s), e.g. page " + str(page) + " " + lab + ")")
    # No `if len(families) > 1` guard: the slice below is empty when there is only one
    # family, so the guard could never change the result. Mutation testing found it as an
    # equivalent mutant -- a branch no test could ever kill because removing it changes
    # nothing. A guard that cannot fail is noise in a gate, so it is gone rather than
    # allowlisted.
    for fam in sorted(families, key=lambda f: -len(families[f]))[1:]:
        page, lab = families[fam][0]
        offenders.append("second font family '" + fam + "' ("
                         + str(len(families[fam])) + " element(s), e.g. page "
                         + str(page) + " " + lab + ")")
    if offenders:
        return Result("G5", FAIL,
                      str(len(offenders)) + " type violation(s): a size off the scale, or a "
                      "second family", offenders[:MAX_OFFENDERS])
    return Result("G5", PASS,
                  "all " + str(len(sizes)) + " page size(s) and " + str(len(chart_sizes))
                  + " chart size(s) are on the scale " + str(list(TYPE_SCALE))
                  + ", in one family '" + str(next(iter(families), "?")) + "'")


def check_g6(pages) -> Result:
    """An SVG's viewBox carries no whitespace, AND no drawing outside it.

    SLACK IS TWO-SIDED AND THIS RULE TESTED ONE SIDE. `svgSlack` returns
    `(viewBox edge) - (ink edge)` per side: POSITIVE is empty space inside the box, NEGATIVE
    is drawing that falls outside it and is therefore clipped. The test was
    `if slack[side] > SVG_SLACK_TOL`, so every negative value passed in silence.

    On 2026-09-21 this rule certified a shipped deck "CLEAN and FULLY COVERED -- 9 pass,
    0 fail" while a chart's right slack sat at -8.43: a label reading "billin", because the
    final "g" of "billing" was outside the box. The number that proves it was computed here,
    on that run, and compared in one direction. Nick found it by looking at the page.

    A SMALL NEGATIVE IS NOT A CLIP, which is why this is a band and not an abs(). A stroke
    painted on the boundary puts half its width outside the box: 0.5 to 1.2 user units on
    these charts. A clipped glyph is far larger -- about 5 units for one character at the
    11px label size, and the case that forced this measured 8.43. SVG_CLIP_TOL sits between
    the two. The earlier reasoning, that negative slack "is ink, not whitespace, and trimming
    to it would clip the drawing", was right about strokes and wrong to generalise from
    strokes to every negative value.
    """
    empty, clipped = [], []
    checked = 0
    for p in pages:
        for n in p["nodes"]:
            slack = n.get("svgSlack")
            if not slack:
                continue
            checked += 1
            for side in ("left", "top", "right", "bottom"):
                if slack[side] > SVG_SLACK_TOL:
                    empty.append("page " + str(p["page"]) + ": "
                                 + str(round(slack[side], 2))
                                 + " user units of empty " + side + " inside the "
                                 "viewBox, so the box edge is not the visible edge -- "
                                 + n["label"])
                elif slack[side] < -SVG_CLIP_TOL:
                    clipped.append("page " + str(p["page"]) + ": "
                                   + str(round(-slack[side], 2))
                                   + " user units of drawing fall outside the " + side
                                   + " edge of the viewBox and are CLIPPED -- "
                                   + n["label"])
    if not checked:
        return Result("G6", CANNOT_RUN,
                      "no SVG with a viewBox and a measurable bounding box was found")
    if empty or clipped:
        # Clipped content leads: a lost glyph is a defect in the artifact, while slack is a
        # defect in its alignment.
        return Result("G6", FAIL,
                      str(len(clipped)) + " side(s) with drawing CLIPPED outside the viewBox "
                      "and " + str(len(empty)) + " with whitespace baked into it, across "
                      + str(checked) + " SVG(s)", (clipped + empty)[:MAX_OFFENDERS])
    return Result("G6", PASS,
                  "all " + str(checked) + " SVG(s) have a viewBox that hugs the drawing, "
                  "with nothing clipped more than " + str(SVG_CLIP_TOL) + " units outside "
                  "it (the measurement tolerance, not zero)")


def check_g7(pages) -> Result:
    """Every multi-line text block has its half-leading trimmed.

    A line box is taller than its glyphs by (line-height - font-size), half above the first
    line and half below the last. Untrimmed, a box padded 12px reads as padded 12 plus half
    a line: the padding you specified is not the space on the page, and the grid the box
    sits on describes its border rather than anything a reader can see. Nick, 2026-09-19:
    strip the whitespace an object carries in its own construction, so it has the most
    freedom to be placed on the grid.

    MEASURED AS AN OUTCOME, NOT AS A MECHANISM. An earlier version asserted one specific
    implementation (a ::before with a negative margin) and would have FAILED a deck that
    trimmed correctly by another route -- and that route is the one that works: the
    ::before/1lh hack computes the right margin and does not change the height, while
    `text-box: trim-both` does. A gate that encodes the author's first guess at HOW is a
    gate against the fix. The bound below is the em-box trim; trimming to cap height
    removes more and passes, which is correct, because the rule is "the slack is gone",
    not "the slack is gone by this much".
    """
    offenders = []
    checked = 0
    measured = 0
    declared = 0
    for p in pages:
        for n in p["nodes"]:
            if len(n.get("ownText") or "") < MIN_TEXT_CHARS:
                continue
            lh, fs = n.get("lineHeight") or 0, n.get("fontSize") or 0
            lines, ch = n.get("lineCount") or 0, n.get("contentH") or 0
            if lh <= 0 or fs <= 0 or lines <= 0 or ch <= 0:
                continue
            half = (lh - fs) / 2.0
            if half <= TOL:
                continue          # line-height at or below the em box: nothing to trim
            # A MARKER IS NOT A TEXT BLOCK -- the same exemption G8 already makes, for the
            # same reason. A 16px circle holding one digit gets its height from the SHAPE
            # and centres its glyph with line-height; there is no leading to strip, and
            # trimming would shrink the circle. Narrow on purpose: one or two characters
            # in a box no wider than three times its own type.
            if len(n["ownText"]) <= 2 and n["box"]["w"] <= 3 * fs:
                continue
            checked += 1
            # DECLARED, or MEASURED. Either is a real answer and neither is the softer one.
            #
            # text-box-trim is the standard CSS property for this, so reading it is reading
            # the answer rather than guessing at a mechanism. The measured path stays for a
            # deck that trims by some other route, and for the case where the property is
            # declared but the browser does not honour it.
            #
            # THE MEASURED PATH IS A HEIGHT COMPARISON, NOT A POSITION ONE. An earlier
            # version compared the content-box top against the first line's top and
            # required at least a half-leading of offset. That signal is NOT half-leading:
            # Range.getClientRects returns the FONT box, not the line box, so the offset it
            # measures is (ascent - cap height), a property of the typeface that does not
            # move with line-height at all. It happened to clear the threshold at 13px and
            # missed it at 16px in another font -- a rule that passed or failed on which
            # font was installed. Height is font-independent: untrimmed, a block is exactly
            # n line-heights tall; trimmed, it is shorter by the leading.
            # MEASURE FIRST. An earlier version short-circuited on trimDeclared, and on
            # a deck that declares `text-box` universally that meant 35 of 35 blocks passed
            # by DECLARATION and 0 by MEASUREMENT -- the rule reported green having checked
            # nothing, the same zero-denominator defect as G8's "all 0 painted box(es)".
            # The declaration is accepted ONLY where the height comparison genuinely cannot
            # see the trim: a box the layout stretches taller than its own text needs.
            if ch <= lines * lh - (lh - fs) + TOL:
                measured += 1
                continue
            stretched = ch > lines * lh + TOL
            if stretched and n.get("trimDeclared"):
                declared += 1
                continue
            offenders.append("page " + str(p["page"]) + ": " + str(round(half * 2, 1))
                             + "px of leading is untrimmed, and no trim is declared ("
                             + str(lines) + " line(s) of " + str(round(fs, 1))
                             + "px at line-height " + str(round(lh, 1)) + " measure "
                             + str(round(ch, 1)) + "px, bound "
                             + str(round(lines * lh - (lh - fs), 1)) + ") -- "
                             + n["label"])
    if not checked:
        return Result("G7", CANNOT_RUN,
                      "no text block has leading to trim (every line-height is at or below "
                      "its em box)")
    if offenders:
        return Result("G7", FAIL,
                      str(len(offenders)) + " of " + str(checked) + " text block(s) carry "
                      "untrimmed half-leading", offenders[:MAX_OFFENDERS])
    if not measured:
        # Every block taking the stretched-box exit means the height comparison never ran.
        return Result("G7", CANNOT_RUN,
                      "all " + str(checked) + " text block(s) took the declared-trim exit "
                      "for stretched boxes, so the height comparison measured nothing")
    return Result("G7", PASS,
                  str(measured) + " text block(s) are MEASURED as trimmed and "
                  + str(declared) + " accepted on the declared property because the layout "
                  "stretches them; a box's padding is the space a reader sees")


def check_g8(pages) -> Result:
    """After the leading is trimmed, the spacing that is left must be spacing you chose.

    NICK, 2026-09-19: "Trimming the white space has one unintended consequence: spacing
    might get crowded, so we need to ensure spacing."

    He is describing a real and slightly perverse effect of G7. Before the trim, every text
    block carried half a line of slack at its top and bottom, and that slack was doing the
    job of padding. A 4px gap between a label and its body read as roughly 9px, and a box
    padded 8px read as roughly 13px. Trim the leading and all of that disappears at once:
    the numbers in the stylesheet do not change, the page gets measurably tighter, and
    G1-G7 all stay green because every one of them is satisfied by a crowded page.

    So the trim needs a floor underneath it. Two floors, and both are about what a reader
    sees rather than what the stylesheet says:
      - a vertical gap between two text-bearing siblings is at least MIN_TEXT_GAP
      - a box that PAINTS -- a background or a border -- and contains text pads its text
        by at least MIN_BOX_PAD on every side

    G4 already requires those values to be on the grid. This one requires them to be big
    enough. A rule that only checks the grid is happy with 0px.
    """
    offenders = []
    gaps_checked = 0
    boxes_checked = 0
    for p in pages:
        by_parent = {}
        for n in p["nodes"]:
            by_parent.setdefault(n["pid"], []).append(n)
        for members in by_parent.values():
            members = [m for m in members if not m.get("furniture")]
            members.sort(key=lambda n: n["ord"])
            for a, b in _non_adjacent_collisions(members):
                if (a.get("hasText") and b.get("hasText") and a["tag"] not in TABLE_PARTS
                        and b["tag"] not in TABLE_PARTS):
                    gaps_checked += 1
                    offenders.append("page " + str(p["page"]) + ": two text blocks "
                                     "OVERLAP by " + str(_overlap_y(a, b))
                                     + "px (not DOM neighbours) -- " + a["label"]
                                     + "  ->  " + b["label"])
            for a, b in zip(members, members[1:]):
                if b["box"]["t"] < a["box"]["b"] - TOL:
                    # Overlapping on both axes is a COLLISION, not two columns.
                    if (_overlaps_x(a, b) and a.get("hasText") and b.get("hasText")
                            and a["tag"] not in TABLE_PARTS
                            and b["tag"] not in TABLE_PARTS):
                        gaps_checked += 1
                        offenders.append("page " + str(p["page"]) + ": two text blocks "
                                         "OVERLAP by " + str(round(a["box"]["b"]
                                         - b["box"]["t"], 1)) + "px -- " + a["label"]
                                         + "  ->  " + b["label"])
                    continue                       # side by side, not stacked
                # TABLE SCAFFOLDING IS NOT A TEXT GAP. Adjacent <tr>s touch by definition;
                # the space between two rows IS the cells' padding, which the box half of
                # this rule already governs. Demanding an 8px margin between table rows
                # asks for something table layout does not have.
                if a["tag"] in TABLE_PARTS or b["tag"] in TABLE_PARTS:
                    continue
                # hasText, NOT ownText. The box half of this rule was corrected to
                # hasText earlier the same day and THIS half was left behind, so slide 1's
                # flow steps -- whose words live one div down -- were never measured for
                # crowding. Half-fixing a rule leaves the other half green and silent.
                if not (a.get("hasText") and b.get("hasText")):
                    continue                       # only text against text
                gaps_checked += 1
                gap = b["box"]["t"] - a["box"]["b"]
                if gap < MIN_TEXT_GAP - TOL:
                    offenders.append("page " + str(p["page"]) + ": only "
                                     + str(round(gap, 1)) + "px between two text blocks "
                                     "(min " + str(MIN_TEXT_GAP) + " once the leading is "
                                     "trimmed) -- " + a["label"] + "  ->  " + b["label"])
        for n in p["nodes"]:
            # hasText, NOT ownText. A painted box almost always holds its words in CHILD
            # elements, so keying on directly-owned text skipped every real box on this
            # deck and the rule reported "all 0 painted box(es)" -- green, and measuring
            # nothing. Caught by reading the count in a passing run.
            if not n.get("isBox") or not n.get("hasText"):
                continue
            # A MARKER IS NOT A TEXT BOX. A numbered step badge is a 16px circle holding
            # one digit: it is sized as a SHAPE, its glyph is centred by construction, and
            # demanding 8px of padding on every side would make it a 32px circle. The test
            # is deliberately narrow -- one or two characters inside a box no wider than
            # three times its own type -- so it cannot be stretched to excuse a real box.
            if (n["hasText"] <= 2
                    and n["box"]["w"] <= 3 * (n.get("fontSize") or 0)):
                continue
            pad = n.get("padded") or {}
            boxes_checked += 1
            # WHICH SIDES OWE PADDING. Padding exists to stop text touching a visible
            # edge, so a box owes it on the edges it HAS. A filled or fully bordered box
            # has four. An accent-bar callout has one plus the two the text runs between;
            # its far side is open, where there is no edge for the text to touch.
            sides = (("top", "t"), ("bottom", "b"), ("left", "l"), ("right", "r"))
            if n.get("accentOnly"):
                sides = (("top", "t"), ("bottom", "b"),
                         ("left", "l") if n.get("accentLeft") else ("right", "r"))
            for side, key in sides:
                v = pad.get(key, 0)
                if v < MIN_BOX_PAD - TOL:
                    offenders.append("page " + str(p["page"]) + ": painted box pads its "
                                     "text only " + str(round(v, 1)) + "px on the " + side
                                     + " (min " + str(MIN_BOX_PAD) + ") -- " + n["label"])
    if not gaps_checked and not boxes_checked:
        return Result("G8", CANNOT_RUN,
                      "no stacked text pair and no painted text box were measured")
    if offenders:
        return Result("G8", FAIL,
                      str(len(offenders)) + " crowding violation(s) across "
                      + str(gaps_checked) + " text gap(s) and " + str(boxes_checked)
                      + " painted box(es)", offenders[:MAX_OFFENDERS])
    return Result("G8", PASS,
                  "all " + str(gaps_checked) + " text gap(s) clear " + str(MIN_TEXT_GAP)
                  + "px and all " + str(boxes_checked) + " painted box(es) pad their text "
                  "by at least " + str(MIN_BOX_PAD) + "px")


def check_g9(pages) -> Result:
    """A chart must FILL its own box, or its grid-aligned edge is not its visible edge.

    preserveAspectRatio defaults to "meet": an SVG whose box aspect differs from its
    viewBox aspect is scaled to fit and CENTRED, leaving dead space on two sides. The
    element's edge then sits perfectly on the grid while the drawing a reader sees starts
    somewhere inside it.

    Measured on this deck 2026-09-19: the hourly chart's box was 860x160, an aspect of
    5.38, against a viewBox aspect of 3.97. The bars were 634px wide with 113PX OF DEAD
    SPACE ON EACH SIDE, so the chart began 113px right of the heading above it -- a
    misalignment visible in the render, on a page where G3 and G6 both passed. G3 measures
    the element box, G6 measures the viewBox, and NEITHER can see the gap between them.
    That is the whole reason this rule is separate from both.
    """
    offenders = []
    checked = 0
    for p in pages:
        for n in p["nodes"]:
            lb = n.get("svgLetterbox")
            if lb is None:
                continue
            checked += 1
            for axis, key in (("horizontally", "x"), ("vertically", "y")):
                if lb[key] > TOL:
                    offenders.append("page " + str(p["page"]) + ": chart is letterboxed "
                                     + str(round(lb[key], 1)) + "px on EACH side "
                                     + axis + ", so its visible edge is not its box edge "
                                     "-- " + n["label"])
    if not checked:
        return Result("G9", CANNOT_RUN,
                      "no SVG with a viewBox and a measurable box was found")
    if offenders:
        return Result("G9", FAIL,
                      str(len(offenders)) + " axis/axes across " + str(checked)
                      + " chart(s) do not fill their box", offenders[:MAX_OFFENDERS])
    return Result("G9", PASS,
                  "all " + str(checked) + " chart(s) fill their box, so the edge on the "
                  "grid is the edge a reader sees")


def certification(total: int, fails: int, cannot: int, executed: int,
                  under_floor: bool, filtered: bool = False) -> str:
    """The one sentence a human may quote about a run, with every count in it.

    Nick, 2026-09-19, after two of my summaries restated a tool's result in stronger
    vocabulary than the tool uses -- "the 12-rule craft gate passes" for a run that was
    8 pass, 0 fail and 4 CANNOT_RUN, and "every rule walks to leaves" when one did.
    Paraphrasing a gate's output is the same defect as an overclaim on a page: the
    sentence is confident, the number behind it is real, and the two do not match. So the
    gate emits the sentence itself and the rule is to quote this line rather than
    summarise it.
    """
    if fails or under_floor:
        verdict = "NOT CLEAN"
    elif filtered:
        # Never "fully covered" on a subset, whatever the subset reported.
        verdict = "PARTIAL RUN, coverage not established"
    elif cannot:
        verdict = "CLEAN but NOT FULLY COVERED"
    else:
        verdict = "CLEAN and FULLY COVERED"
    return ("CERTIFICATION: " + verdict + " -- " + str(total - fails - cannot)
            + " pass, " + str(fails) + " fail, " + str(cannot) + " cannot run, "
            + str(executed) + " of " + str(total) + " rules actually executed.")


def run_checks(pages) -> list:
    return [check_g1(pages), check_g2(pages), check_g3(pages), check_g4(pages),
            check_g5(pages), check_g6(pages), check_g7(pages),
            check_g8(pages), check_g9(pages)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deck", help="deck HTML to render and measure")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--chrome", help="path to a Chrome or Chromium binary")
    ap.add_argument("--only", help="comma-separated rule ids, e.g. G1,G2")
    args = ap.parse_args(argv)

    path = Path(args.deck)
    if not path.exists():
        print("cannot read " + str(path), file=sys.stderr)
        return 4

    chrome = find_chrome(args.chrome)
    if not chrome:
        # NOT a pass dressed as a CANNOT_RUN: without a browser this file measured NOTHING,
        # and reporting "clean" would be the exact overclaim the vocabulary exists to stop.
        print("no Chrome or Chromium found, so NOTHING WAS MEASURED. This is not a pass. "
              "Pass --chrome <path>.", file=sys.stderr)
        return 4

    try:
        pages = measure(path, chrome)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print("could not measure " + str(path) + ": " + str(e), file=sys.stderr)
        return 4

    if not pages:
        print("no pages matched the .slide convention in " + str(path), file=sys.stderr)
        return 4

    results = run_checks(pages)
    if args.only:
        wanted = {r.strip().upper() for r in args.only.split(",")}
        known = {r.rule for r in results}
        unknown = wanted - known
        if unknown:
            # A typo used to select ZERO rules and report clean:true, exit 0 -- a gate
            # bypassed by a misspelling. An unknown id is an error, never a filter.
            print("unknown rule id(s): " + ", ".join(sorted(unknown))
                  + ". Known: " + ", ".join(sorted(known)), file=sys.stderr)
            return 4
        results = [r for r in results if r.rule in wanted]

    fails = [r for r in results if r.state == FAIL]
    cannot = [r for r in results if r.state == CANNOT_RUN]
    executed = len(results) - len(cannot)
    # THE COVERAGE FLOOR. Without it "no FAIL" meant exit 0 even when nothing ran, so a
    # deck where every rule reported CANNOT_RUN certified clean. Mirrors
    # check_deck_craft's MIN_EXECUTED for the same reason and with the same vocabulary.
    under_floor = executed < MIN_EXECUTED if not args.only else executed < 1
    # A FILTERED RUN IS NEVER "FULLY COVERED". --only G1 executes one rule of nine, and
    # certification() computed over the filtered list called that CLEAN and FULLY COVERED
    # -- a caller could select one rule and quote a sentence claiming the whole deck was
    # measured. Raised independently by Fable and Codex. The filtered count is reported
    # against the FULL rule set so the denominator is visible in the sentence itself.
    payload = {
        "deck": str(path),
        "pages": len(pages),
        "results": [r.as_dict() for r in results],
        "counts": {"pass": len(results) - len(fails) - len(cannot),
                   "fail": len(fails), "cannot_run": len(cannot),
                   "executed": executed},
        "clean": not fails and not under_floor,
        "under_coverage_floor": under_floor,
        # FILTERED RUNS ARE NEVER fully_covered, AND THE JSON MUST SAY SO TOO. The first
        # fix changed only the printed sentence and left this field True, so a machine
        # consumer still read full coverage off a one-rule run. Fixing the prose and
        # leaving the payload lying is the same defect one layer down, and Codex caught
        # it in the pre-push round.
        "filtered": bool(args.only),
        "rules_available": len(run_checks(pages)) if args.only else len(results),
        "fully_covered": (not fails and not cannot and not args.only),
        "certification": certification(len(results), len(fails), len(cannot), executed,
                                       under_floor, filtered=bool(args.only)),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 2 if (fails or under_floor) else 0

    print("\ndeck: " + str(path) + "   (measured in a real browser)")
    print("pages: " + str(len(pages)) + "\n")
    for r in results:
        icon = {PASS: "ok  ", FAIL: "FAIL", CANNOT_RUN: "----"}[r.state]
        print("  [" + icon + "] " + r.rule.ljust(4) + " " + r.detail)
        for o in r.offenders:
            print("          - " + o)
    print("\n  " + payload["certification"])
    if under_floor:
        print("\n  UNDER COVERAGE FLOOR: only " + str(executed) + " rule(s) executed "
              "(min " + str(MIN_EXECUTED) + "). This deck has NOT been meaningfully "
              "measured and is not clean regardless of failures.")
    if cannot:
        print("\n  NOTE: a CANNOT_RUN is not a pass. Those rules were not tested.")
    print()
    return 2 if (fails or under_floor) else 0


if __name__ == "__main__":
    sys.exit(main())
