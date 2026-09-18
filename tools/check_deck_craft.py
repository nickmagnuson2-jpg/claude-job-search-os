#!/usr/bin/env python3
"""Run the mechanizable subset of Sections A-C of framework/deck-rubric.md against a rendered deck.

THE SIBLING OF check_frame_integrity.py, AND THE GAP IT CLOSES.

Section F had a checker from the day it was promoted. Sections A through E had none,
which is exactly how a deck built minutes after loading the mckinsey-slides skill
reached a rendered artifact with three C11 dash violations and zero C7 takeaway boxes
(measured 2026-09-17). This script is the missing half.

It does NOT score the whole rubric. Nine rules, chosen against one criterion, revised
after all three cross-model reviewers attacked the v1 design:

    EXCLUDE A RULE WHEN NOTHING HAS YET TESTED IT, NOT WHEN IT IS NEW.

v1 proposed excluding every rule authored 2026-09-17 on freshness grounds. Grok refuted
it: "authored today" is not "not taught by the prior engagement". A10 (provenance tier
per number) and E8 (the two-test cut pass) were both typed that day and both taught in
August, so a freshness filter would have dropped two of the best-evidenced rules in the
batch.

What actually keeps a rule out is checkability:
  * A10 needs semantics this script does not have. A weak detector on a good rule trains
    the gate to be ignored, which is worse than no gate.
  * E8 describes a PROCESS (how a cut pass is run), not a property of a rendered
    artifact. No artifact checker can see it.

THE THREE-STATE DESIGN, INHERITED DELIBERATELY FROM ITS SIBLING.

PASS, FAIL, CANNOT_RUN. A check that cannot execute -- the deck uses no lists, no
sticker is present, no chart title element is identifiable -- is NEVER reported as a
pass. Collapsing CANNOT_RUN into PASS produces a green result that means nothing and
reads like coverage.

A4 is the shape worth copying: PRESENCE of a sticker is a judgment call the script must
not make, POSITION is mechanical. So no sticker is CANNOT_RUN, and a misplaced sticker
is FAIL. Check what is mechanical, refuse the rest out loud.

CONVENTION COUPLING IS THE KNOWN LIMIT, AND IT IS REPORTED RATHER THAN HIDDEN.

The script finds a tracker, a source line, a sticker and a takeaway box by CSS class
convention (see CONVENTION below). A deck that names things differently gets
CANNOT_RUN on those rules, not a false pass. --convention-report prints what was
found so a mismatch is visible in one command.

Exit codes are the contract:
  0  no FAILs (there may be CANNOT_RUNs -- read fully_covered)
  2  at least one FAIL, or fewer than MIN_EXECUTED checks actually ran
  4  deck file unreadable, or no pages found

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/check_deck_craft.py <deck.html>
  ... --json
  ... --convention-report
  ... --bold-cap 10
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Em dash and en dash. A project hard rule bans the em dash in anything sent; it slipped
# through twice on 2026-09-17 and was caught by a human both times.
EM_DASH = "\u2014"
EN_DASH = "\u2013"

PASS = "PASS"
FAIL = "FAIL"
CANNOT_RUN = "CANNOT_RUN"

# A deck on which almost nothing executed has not been tested, however green it reads.
MIN_EXECUTED = 5

# Default cap for B7. "Bold only the first few words" -- a bold run longer than this
# stops being emphasis and becomes a second voice on the page.
DEFAULT_BOLD_CAP = 10

CONVENTION = {
    "slide": ("slide", "page"),
    "tracker": ("tracker", "sectiontracker", "section-tracker"),
    "sticker": ("sticker", "stamp", "status-flag"),
    "source": ("src", "source", "srcline"),
    "takeaway": ("take", "takeaway", "keytake", "key-takeaway"),
    "lede": ("lede", "lead", "action-title", "actiontitle"),
    "charttitle": ("charttitle", "chart-title", "chart-label"),
}

# C11. Only ever applied to STYLE surfaces, never to text nodes, which is what makes
# the "zero false positives" claim true: a slide may legitimately contain the word
# "dashed" in its prose and must not be flagged for it.
DASH_TOKENS = ("dashed", "dotted")
DASH_PROPS = ("border", "outline", "border-top", "border-right", "border-bottom",
              "border-left", "border-style", "outline-style", "text-decoration",
              "text-decoration-style", "column-rule", "column-rule-style")


class Result:
    """One rule's verdict. detail must always say WHY, including for CANNOT_RUN."""

    __slots__ = ("rule", "state", "detail", "offenders")

    def __init__(self, rule, state, detail, offenders=None):
        self.rule = rule
        self.state = state
        self.detail = detail
        self.offenders = offenders or []

    def as_dict(self):
        return {"rule": self.rule, "state": self.state,
                "detail": self.detail, "offenders": self.offenders}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent
        self.text = ""

    def classes(self):
        return (self.attrs.get("class") or "").lower().split()

    def style(self):
        return (self.attrs.get("style") or "").lower()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def all_text(self):
        out = [self.text]
        for c in self.children:
            out.append(c.all_text())
        return " ".join(t for t in out if t)


# Void elements never take a close tag, so the naive stack would never pop them.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class DeckParser(HTMLParser):
    """Minimal tolerant tree builder. Deliberately not bs4: no third-party dependency
    for a gate that has to run in any checkout."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.cur = self.root
        self.style_blocks = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        node = Node(tag.lower(), a, self.cur)
        self.cur.children.append(node)
        if tag.lower() == "style":
            self._in_style = True
        if tag.lower() not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        self.cur.children.append(Node(tag.lower(), a, self.cur))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "style":
            self._in_style = False
        if tag in VOID:
            return
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if self._in_style:
            self.style_blocks.append(data)
        elif data.strip():
            self.cur.text = (self.cur.text + " " + data.strip()).strip()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _is(node, kind):
    """True when node's class list matches a CONVENTION alias for kind."""
    want = CONVENTION[kind]
    return any(c in want for c in node.classes())


def _find(scope, kind):
    return [n for n in scope.walk() if _is(n, kind)]


def _charts(slide):
    """A chart is an <svg>, or an element the author marked as one. A bare <table>
    is NOT a chart: C7 is about charts, and C12 already says every page is a table."""
    return [n for n in slide.walk()
            if n.tag == "svg" or "chart" in " ".join(n.classes())]


def _tables(slide):
    return [n for n in slide.walk() if n.tag == "table"]


def _norm_num(tok):
    """Digit-normalise so $12,500 and 12500 and 12,500 all compare equal."""
    return re.sub(r"[^\d.]", "", tok).rstrip(".")


NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _numbers(text):
    out = []
    for m in NUM_RE.finditer(text or ""):
        n = _norm_num(m.group(0))
        if n:
            out.append((m.group(0), n))
    return out


def _bold_runs(scope):
    """Every bold text run, as (text, word_count). <b>, <strong>, and inline
    font-weight >= 600 all count, because the page renders them identically."""
    runs = []
    for n in scope.walk():
        heavy = n.tag in ("b", "strong")
        if not heavy:
            m = re.search(r"font-weight\s*:\s*(\d{3}|bold)", n.style())
            if m:
                v = m.group(1)
                heavy = (v == "bold") or (v.isdigit() and int(v) >= 600)
        if heavy:
            t = n.all_text().strip()
            if t:
                runs.append((t, len(t.split())))
    return runs


def _dash_hits_in_declaration(decl):
    """Return the dash tokens found in a CSS declaration block, scoped to properties
    where a dash is actually a visual dash. Never scans text."""
    hits = []
    low = decl.lower()
    for prop in DASH_PROPS:
        for m in re.finditer(re.escape(prop) + r"\s*:\s*([^;{}]*)", low):
            val = m.group(1)
            for tok in DASH_TOKENS:
                if re.search(r"\b" + tok + r"\b", val):
                    hits.append(prop + ": " + val.strip())
    return hits


# --------------------------------------------------------------------------
# the nine checks
# --------------------------------------------------------------------------

def check_a2(slides):
    """Section tracker on every page. CANNOT_RUN when no page has one: the deck may
    use a convention this script does not know, and guessing would be a false pass."""
    have = [i for i, s in enumerate(slides, 1) if _find(s, "tracker")]
    if not have:
        return Result("A2", CANNOT_RUN,
                      "no element matching the tracker convention on any page, so the "
                      "deck's convention is unknown (looked for class in "
                      + str(CONVENTION["tracker"]) + ")")
    missing = [i for i, s in enumerate(slides, 1) if not _find(s, "tracker")]
    if missing:
        return Result("A2", FAIL,
                      "tracker on " + str(len(have)) + " of " + str(len(slides))
                      + " pages, so the convention exists and is applied inconsistently",
                      ["page " + str(i) + ": no tracker" for i in missing])
    return Result("A2", PASS, "tracker present on all " + str(len(slides)) + " pages")


def check_a4(slides):
    """Sticker in the house position: left, under the subtitle rule.

    PRESENCE is a judgment call this script must not make. POSITION is mechanical.
    The mechanical half is deliberately narrow: DOM order after the rule element, and
    no right-anchoring. It cannot compute true layout geometry and does not claim to."""
    stickers = [(i, n) for i, s in enumerate(slides, 1) for n in _find(s, "sticker")]
    if not stickers:
        return Result("A4", CANNOT_RUN,
                      "no sticker on any page. Whether one is NEEDED is a maturity "
                      "judgment (Preliminary / Draft / As of / HYPOTHESIS), which this "
                      "script refuses to make. Position is checked only when one exists")
    bad = []
    for i, n in stickers:
        st = n.style()
        if re.search(r"\bright\s*:", st) or "text-align:right" in st.replace(" ", ""):
            bad.append("page " + str(i)
                       + ": sticker is right-anchored; house position is left")
            continue
        slide = n
        while slide is not None and not _is(slide, "slide"):
            slide = slide.parent
        order = list(slide.walk()) if slide else []
        rules = [x for x in order if "rule" in x.classes()]
        if rules and order.index(n) < order.index(rules[0]):
            bad.append("page " + str(i)
                       + ": sticker precedes the subtitle rule in DOM order")
    if bad:
        return Result("A4", FAIL,
                      str(len(bad)) + " sticker(s) out of house position", bad)
    return Result("A4", PASS,
                  str(len(stickers))
                  + " sticker(s), all left-anchored and below the rule")


def check_a8(slides):
    """Every data page has a source. A cover page -- no chart, no table -- is exempt."""
    data_pages = [(i, s) for i, s in enumerate(slides, 1) if _charts(s) or _tables(s)]
    if not data_pages:
        return Result("A8", CANNOT_RUN,
                      "no page carries a chart or a table, so no page is a data page")
    if not any(_find(s, "source") for _, s in data_pages):
        return Result("A8", CANNOT_RUN,
                      "no element matching the source convention on any data page, so "
                      "the convention is unknown (looked for class in "
                      + str(CONVENTION["source"]) + ")")
    missing = ["page " + str(i) + ": data page with no source line"
               for i, s in data_pages if not _find(s, "source")]
    if missing:
        return Result("A8", FAIL,
                      str(len(missing)) + " of " + str(len(data_pages))
                      + " data pages have no source", missing)
    return Result("A8", PASS,
                  "source on all " + str(len(data_pages)) + " data pages")


def check_b4(slides):
    """A number in the title must be findable on the page.

    Digit-normalised, so $12,500 in the lede is satisfied by 12,500 in the body."""
    ledes = [(i, s, _find(s, "lede")) for i, s in enumerate(slides, 1)]
    if not any(l for _, _, l in ledes):
        return Result("B4", CANNOT_RUN,
                      "no element matching the lede convention on any page (looked for "
                      "class in " + str(CONVENTION["lede"]) + ")")
    offenders = []
    checked = 0
    for i, slide, lede_nodes in ledes:
        if not lede_nodes:
            continue
        lede_text = " ".join(n.all_text() for n in lede_nodes)
        title_nums = _numbers(lede_text)
        if not title_nums:
            continue
        excluded = set()
        for ln in lede_nodes:
            for sub in ln.walk():
                excluded.add(id(sub))
        body_nums = set()
        for n in slide.walk():
            if id(n) in excluded:
                continue
            body_nums.update(v for _, v in _numbers(n.text))
        for raw, norm in title_nums:
            checked += 1
            if norm not in body_nums:
                offenders.append("page " + str(i) + ": title carries " + raw
                                 + " and the body does not")
    if not checked:
        return Result("B4", CANNOT_RUN, "no page title carries a number")
    if offenders:
        return Result("B4", FAIL,
                      str(len(offenders)) + " of " + str(checked)
                      + " title number(s) not findable in the body", offenders)
    return Result("B4", PASS,
                  "all " + str(checked) + " title number(s) findable in the body")


def check_b5(slides):
    """Bullets: 2 to 5 per list. Never exactly one -- a list by definition has 2+."""
    lists = [(i, n) for i, s in enumerate(slides, 1) for n in s.walk()
             if n.tag in ("ul", "ol")]
    if not lists:
        return Result("B5", CANNOT_RUN,
                      "no <ul> or <ol> on any page, so there is no list to size")
    offenders = []
    for i, n in lists:
        items = [c for c in n.children if c.tag == "li"]
        if len(items) == 1:
            offenders.append("page " + str(i)
                             + ": list of exactly 1 item; a list has 2 or more")
        elif len(items) > 5:
            offenders.append("page " + str(i) + ": list of " + str(len(items))
                             + " items; the cap is 5")
        elif not items:
            offenders.append("page " + str(i) + ": list element with no items")
    if offenders:
        return Result("B5", FAIL,
                      str(len(offenders)) + " of " + str(len(lists))
                      + " lists out of range", offenders)
    return Result("B5", PASS,
                  "all " + str(len(lists)) + " lists carry 2 to 5 items")


def check_b7(slides, cap):
    """Bold only the first few words. Over-bolding distracts and lengthens."""
    runs = [(i, t, w) for i, s in enumerate(slides, 1) for t, w in _bold_runs(s)]
    if not runs:
        return Result("B7", CANNOT_RUN, "no bold runs found on any page")
    offenders = []
    for i, t, w in runs:
        if w > cap:
            snippet = t if len(t) <= 70 else t[:67] + "..."
            offenders.append("page " + str(i) + ": " + str(w) + "-word bold run (cap "
                             + str(cap) + '): "' + snippet + '"')
    if offenders:
        return Result("B7", FAIL,
                      str(len(offenders)) + " of " + str(len(runs))
                      + " bold runs exceed " + str(cap) + " words", offenders)
    return Result("B7", PASS,
                  "all " + str(len(runs)) + " bold runs are " + str(cap)
                  + " words or fewer")


def check_c2(slides):
    """Chart titles are two-part: bold subject, then italic unit after a comma.

    CANNOT_RUN when no chart title element is identifiable -- the title may be drawn
    inside the SVG, where this script cannot tell a title from an axis label."""
    titled = []
    for i, s in enumerate(slides, 1):
        for n in s.walk():
            if _is(n, "charttitle"):
                titled.append((i, n))
                continue
            # An <h3> is a chart title only when a chart sits in ITS OWN container.
            # Scoping to the whole page was a false positive on 2026-09-17: an <h3>
            # heading a prose column on a page that also held a chart was reported as
            # an untitled chart. A heading is only a chart's title if it is next to
            # the chart.
            if n.tag == "h3" and n.parent is not None and _charts(n.parent):
                titled.append((i, n))
    if not titled:
        return Result("C2", CANNOT_RUN,
                      "no chart title element identifiable. Titles set inside the SVG "
                      "are indistinguishable from axis labels to this script")
    offenders = []
    for i, n in titled:
        txt = n.all_text().strip()
        has_italic = any(c.tag in ("i", "em") or "italic" in c.style()
                         for c in n.walk())
        if "," not in txt:
            offenders.append("page " + str(i) + ': no comma, so no unit clause: "'
                             + txt[:60] + '"')
        elif not has_italic:
            offenders.append("page " + str(i)
                             + ': unit after the comma is not italic: "'
                             + txt[:60] + '"')
    if offenders:
        return Result("C2", FAIL,
                      str(len(offenders)) + " of " + str(len(titled))
                      + " chart titles are not two-part", offenders)
    return Result("C2", PASS,
                  "all " + str(len(titled)) + " chart titles are two-part")


def check_c7(slides):
    """Key takeaways box on any chart, and call out the number."""
    chart_pages = [(i, s) for i, s in enumerate(slides, 1) if _charts(s)]
    if not chart_pages:
        return Result("C7", CANNOT_RUN, "no page carries a chart")
    offenders = ["page " + str(i) + ": " + str(len(_charts(s)))
                 + " chart(s) and no key-takeaway element"
                 for i, s in chart_pages if not _find(s, "takeaway")]
    if offenders:
        return Result("C7", FAIL,
                      str(len(offenders)) + " of " + str(len(chart_pages))
                      + " chart pages have no takeaway box", offenders)
    return Result("C7", PASS,
                  "takeaway box on all " + str(len(chart_pages)) + " chart pages")


def check_c11(slides, style_blocks):
    """Do not use dashed elements.

    Scans STYLE SURFACES ONLY -- stroke-dasharray attributes, inline style
    declarations, and <style> blocks. Never text nodes, so prose containing the word
    "dashed" is not a false positive. This is what makes the rule fully deterministic."""
    offenders = []
    for i, s in enumerate(slides, 1):
        for n in s.walk():
            da = n.attrs.get("stroke-dasharray", "").strip().lower()
            if da and da not in ("none", "0"):
                offenders.append("page " + str(i) + ": <" + n.tag
                                 + '> stroke-dasharray="' + da + '"')
            for hit in _dash_hits_in_declaration(n.style()):
                offenders.append("page " + str(i) + ": <" + n.tag
                                 + "> inline style " + hit)
            m = re.search(r"stroke-dasharray\s*:\s*([^;]+)", n.style())
            if m and m.group(1).strip() not in ("none", "0"):
                offenders.append("page " + str(i) + ": <" + n.tag
                                 + "> inline stroke-dasharray: " + m.group(1).strip())
    for block in style_blocks:
        for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            sel, decl = rule.group(1).strip(), rule.group(2)
            for hit in _dash_hits_in_declaration(decl):
                offenders.append("stylesheet: " + sel + " { " + hit + " }")
            m = re.search(r"stroke-dasharray\s*:\s*([^;]+)", decl.lower())
            if m and m.group(1).strip() not in ("none", "0"):
                offenders.append("stylesheet: " + sel + " { stroke-dasharray: "
                                 + m.group(1).strip() + " }")
    if offenders:
        return Result("C11", FAIL,
                      str(len(offenders)) + " dashed or dotted element(s)", offenders)
    return Result("C11", PASS,
                  "no dashed or dotted strokes, borders or outlines on any style surface")


def check_r1(deck_path):
    """RENDER FRESHNESS. Did anyone actually LOOK at this page?

    THE ONLY MECHANICAL PROXY FOR THE RENDER-FIRST RULE, and it is a real one: a rendered
    image either exists and is newer than the source, or it does not. No judgment.

    Origin 2026-09-17: page copy passed four rounds of blind cross-model review while
    neither rendered direction had ever been looked at. On first render, five defects
    surfaced that no copy round had caught. A deck with no render newer than its source
    has not been seen by anybody, however green every other check reads."""
    html = Path(deck_path)
    try:
        html_mtime = html.stat().st_mtime
    except OSError:
        return Result("R1", CANNOT_RUN, "deck file not stat-able")
    siblings = [p for p in html.parent.rglob("*")
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf") and p.is_file()]
    if not siblings:
        return Result("R1", FAIL,
                      "no rendered image or PDF anywhere beside this deck. Nobody has "
                      "looked at this page")
    newest = max(siblings, key=lambda p: p.stat().st_mtime)
    if newest.stat().st_mtime < html_mtime:
        age = (html_mtime - newest.stat().st_mtime) / 60.0
        return Result("R1", FAIL,
                      f"every render is older than the deck source, newest by {age:.0f} "
                      "minutes. The page has changed since anyone last saw it",
                      [f"newest render: {newest.name}"])
    return Result("R1", PASS, f"a render newer than the source exists ({newest.name})")


def check_d1(slides):
    """NO EM DASHES in anything sent. Scans TEXT only, never style surfaces.

    The mirror image of C11, which scans style surfaces and never text."""
    offenders = []
    for i, s in enumerate(slides, 1):
        for n in s.walk():
            for label, ch in (("em dash", EM_DASH), ("en dash", EN_DASH)):
                if ch in (n.text or ""):
                    snippet = " ".join(n.text.split())
                    if len(snippet) > 60:
                        snippet = snippet[:57] + "..."
                    offenders.append(f'page {i}: {label} in "{snippet}"')
    if offenders:
        return Result("D1", FAIL, f"{len(offenders)} dash(es) in sent text", offenders)
    return Result("D1", PASS, "no em or en dashes in any page text")


# "1,369 / 1,931 = 70.9%" -- an explicitly printed ratio the reader can check.
RATIO_RE = re.compile(
    r"(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*=\s*(\d+(?:\.\d+)?)\s*%")


def check_c13(slides):
    """EVERY PRINTED RATIO RECOMPUTES FROM THE PRINTED VALUES.

    Only the explicit "a / b = c%" form, which is unambiguous and needs no semantics.
    The general C13 rule is wider and stays with the human; this catches the shape that
    a reader with a calculator would test first. Tolerance is half a unit in the last
    printed decimal place, so a correctly rounded figure passes and a wrong one does not."""
    checked, offenders = 0, []
    for i, s in enumerate(slides, 1):
        for n in s.walk():
            for m in RATIO_RE.finditer(n.text or ""):
                num = float(m.group(1).replace(",", ""))
                den = float(m.group(2).replace(",", ""))
                printed = m.group(3)
                if den == 0:
                    offenders.append(f"page {i}: division by zero in {m.group(0)!r}")
                    continue
                checked += 1
                decimals = len(printed.split(".")[1]) if "." in printed else 0
                tol = 0.5 * (10 ** -decimals)
                actual = num / den * 100.0
                if abs(actual - float(printed)) > tol:
                    offenders.append(
                        f"page {i}: {m.group(0)!r} but {num:,.0f}/{den:,.0f} "
                        f"= {actual:.{max(decimals,1)}f}%")
    if not checked and not offenders:
        return Result("C13", CANNOT_RUN,
                      "no ratio printed in the explicit 'a / b = c%' form. The wider C13 "
                      "rule still applies and is not checked here")
    if offenders:
        return Result("C13", FAIL,
                      f"{len(offenders)} printed ratio(s) do not recompute", offenders)
    return Result("C13", PASS,
                  f"all {checked} printed ratio(s) recompute from the printed values")


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def parse_deck(text):
    p = DeckParser()
    p.feed(text)
    p.close()
    slides = [n for n in p.root.walk() if _is(n, "slide")]
    # A slide nested inside another slide is a layout div that happens to share the
    # class name; keep only the outermost.
    top = []
    for s in slides:
        anc, nested = s.parent, False
        while anc is not None:
            if _is(anc, "slide"):
                nested = True
                break
            anc = anc.parent
        if not nested:
            top.append(s)
    return top, p.style_blocks


def convention_report(slides):
    rows = []
    for i, s in enumerate(slides, 1):
        rows.append({
            "page": i,
            "tracker": len(_find(s, "tracker")),
            "sticker": len(_find(s, "sticker")),
            "source": len(_find(s, "source")),
            "takeaway": len(_find(s, "takeaway")),
            "lede": len(_find(s, "lede")),
            "charts": len(_charts(s)),
            "tables": len(_tables(s)),
        })
    return rows


def run_checks(slides, style_blocks, bold_cap=DEFAULT_BOLD_CAP, deck_path=None):
    out = [] if deck_path is None else [check_r1(deck_path)]
    return out + [
        check_a2(slides),
        check_a4(slides),
        check_a8(slides),
        check_b4(slides),
        check_b5(slides),
        check_b7(slides, bold_cap),
        check_c2(slides),
        check_c7(slides),
        check_c11(slides, style_blocks),
        check_c13(slides),
        check_d1(slides),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deck", help="rendered deck HTML")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--convention-report", action="store_true",
                    help="print what the class conventions matched, per page")
    ap.add_argument("--bold-cap", type=int, default=DEFAULT_BOLD_CAP)
    args = ap.parse_args(argv)

    path = Path(args.deck)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print("cannot read " + str(path) + ": " + str(e), file=sys.stderr)
        return 4

    slides, style_blocks = parse_deck(text)
    if not slides:
        print("no pages found in " + str(path) + ": nothing matched the slide "
              "convention " + str(CONVENTION["slide"]), file=sys.stderr)
        return 4

    results = run_checks(slides, style_blocks, args.bold_cap, deck_path=path)

    fails = [r for r in results if r.state == FAIL]
    cannot = [r for r in results if r.state == CANNOT_RUN]
    executed = len(results) - len(cannot)
    under_floor = executed < MIN_EXECUTED

    payload = {
        "deck": str(path),
        "pages": len(slides),
        "results": [r.as_dict() for r in results],
        "counts": {"pass": len(results) - len(fails) - len(cannot),
                   "fail": len(fails), "cannot_run": len(cannot),
                   "executed": executed},
        "clean": not fails and not under_floor,
        "under_coverage_floor": under_floor,
        "fully_covered": not fails and not cannot,
        "delegated_not_checked_here": {
            "needs_semantics": ["A10 provenance tier per number",
                                "C13 ratio recomputation",
                                "C14 breakdown conservation",
                                "B1/B8 verb and adjective quality"],
            "process_not_artifact": ["E8 the cut pass", "E7 the read-surface audit",
                                     "E1 the ledes test"],
        },
    }
    if args.convention_report:
        payload["convention_report"] = convention_report(slides)

    if args.json:
        print(json.dumps(payload, indent=2))
        return 2 if (fails or under_floor) else 0

    print("\ndeck: " + str(path))
    print("pages: " + str(len(slides)) + "\n")
    for r in results:
        icon = {PASS: "ok  ", FAIL: "FAIL", CANNOT_RUN: "----"}[r.state]
        print("  [" + icon + "] " + r.rule.ljust(5) + " " + r.detail)
        for o in r.offenders:
            print("          - " + o)
    c = payload["counts"]
    print("\n  " + str(c["pass"]) + " pass, " + str(c["fail"]) + " fail, "
          + str(c["cannot_run"]) + " cannot run (" + str(c["executed"])
          + " actually executed)")
    print("  clean=" + str(payload["clean"])
          + "  fully_covered=" + str(payload["fully_covered"]))
    if args.convention_report:
        print("\n  convention report (what the class names matched):")
        for row in payload["convention_report"]:
            print("   ", json.dumps(row))
    if under_floor:
        print("\n  UNDER COVERAGE FLOOR: only " + str(executed)
              + " check(s) executed (min " + str(MIN_EXECUTED) + "). This deck has not "
              "been meaningfully tested and is NOT clean regardless of failures.")
    if cannot:
        print("\n  NOTE: a CANNOT_RUN is not a pass. Those rules were not tested.")
    print()
    return 2 if (fails or under_floor) else 0


if __name__ == "__main__":
    sys.exit(main())
