#!/usr/bin/env python3
"""THE SLIDE HARNESS: assert a value PRINTED ON A PAGE against a recomputation.

WHAT TRAVELS AND WHAT DOES NOT. The mechanism is "check a printed value against a
recomputation, collect the failures, bind the claim to the rendered page, exit non-zero."
That is identical for every deck. The POLICY -- which population to load, which values a
given slide prints -- stays with the caller, in a per-slide module. The engagement this came
from had already performed that split internally, which is what made the promotion possible:
its `load_inbound()` builds a client-specific frame with derived columns and did NOT travel.

THE ONE THING THIS EXISTS TO PREVENT. Every check in the original compared a COMPUTED value
against a PYTHON LITERAL, and no module ever opened the deck. The labels said "slide: 62.5%",
but that is prose. Change $123,456 to $133,456 in the HTML and all 49 checks still passed,
because nothing in the chain read the HTML. "Every number printed on the page reproduces from
the source" was therefore never established by that gate, and it had been asserted. Found by
cross-model review 2026-09-19.

`bind_deck` closes the loop. A label carrying "slide: X" or "source line: X" has to find X in
the rendered page text, so the chain runs: computed value == literal == the token in the label
== a string actually printed on the page.

WHAT IT STILL DOES NOT SEE, stated because the last version of this docstring overclaimed and
a doc became the reason nobody built the gate. Binding is driven by the LABEL: a value the
page prints but no check mentions is invisible here, and so is a chart segment that prints no
label at all. On 2026-09-21 a chart shipped with one band unlabelled while this harness
reported every check green, because the suite asserted the SHARE and the segment's WIDTH and
nothing asserted that a label existed. `coverage_note()` reports how much of a run was bound;
read it, and do not read a green run as "the page is right".

DECK TEXT COMES FROM tools/check_deck_craft.py, not from a regex here. The engagement version
stripped tags with `re.sub(r"<[^>]+>", " ", raw)` and then hand-decoded five HTML entities.
That table could only ever drift from the real one. `parse_deck` builds a tolerant node tree
with `convert_charrefs=True`, which decodes all of them, and it is already the deck gate's
own reader -- so binding now asks the same question the craft gate asks, of the same parser.

Promoted 2026-09-21 from an engagement's `scripts/slide_check.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from check_deck_craft import parse_deck


def deck_pages(path: Path | str,
               exclude_classes: tuple[str, ...] = ()) -> list[str]:
    """The rendered text of each page, IN ORDER, whitespace-collapsed.

    The page boundary is the unit the parser already produces and `deck_text` used to
    throw away. Keeping it is what lets a check say WHICH page printed a value, rather
    than only that the deck did somewhere.

    `exclude_classes` drops subtrees by CSS class. It defaults to empty because binding
    a label must see everything the page prints, the source line included. A caller
    asking "which CLAIMS does this page make" wants the provenance line gone, and that
    is a different question, so it is a parameter rather than a changed default.

    Entities are decoded by the parser, not by a table maintained here.
    """
    slides, _ = parse_deck(Path(path).read_text(encoding="utf-8"))
    if not exclude_classes:
        return [re.sub(r"\s+", " ", s.all_text()) for s in slides]

    drop = set(exclude_classes)

    def keep(node) -> str:
        cls = (node.attrs or {}).get("class", "")
        if drop & set(cls.split()):
            return ""
        parts = [node.text or ""]
        parts += [keep(c) for c in node.children]
        return " ".join(p for p in parts if p)

    return [re.sub(r"\s+", " ", keep(s)) for s in slides]


def deck_text(path: Path | str, page: int | None = None) -> str:
    """Every string the deck prints, whitespace-collapsed.

    `page` is 1-BASED and selects a single page; omitted, every page is joined, which
    is the historical behaviour and the reason a slide-1 check could satisfy itself
    against a token that only slide 2 printed. Callers that know their page should
    pass it -- `SlideCheck.bind_deck` now does.
    """
    pages = deck_pages(path)
    if page is None:
        return " ".join(pages)
    if not 1 <= page <= len(pages):
        raise IndexError(
            f"page {page} requested from {path}, which renders {len(pages)} page(s)")
    return pages[page - 1]


class SlideCheck:
    """Collects assertions for one slide. Collects rather than raising, so one run lists
    them all.

    A check that PASSES is as much of the record as one that fails: the printed line is what
    a future session diffs against when a number moves, so both are echoed.
    """

    def __init__(self, slide: str, quiet: bool = False) -> None:
        self.slide = slide
        self.quiet = quiet
        self.failures: list[str] = []
        self.out: dict = {}
        self._n = 0
        self._deck_text: str | None = None
        self._deck_path: str | None = None
        self._deck_page: int | None = None
        self._bound = 0
        self._unbound: list[str] = []

    def bind_deck(self, path: Path, page: int | None = None) -> None:
        """Attach the PAGE whose printed values these checks claim to assert.

        THE PAGE IS THE POINT. Binding used to run against the whole deck joined into one
        string, so a slide-1 label asserting a rate bound successfully against a token
        only slide 2 printed -- measured on a shipped deck, where slide 1 stated that
        rate in words and never as the figure the label named. The check was green and
        the claim was on the wrong page. Cross-model review, 2026-09-19, F3.

        `page` is 1-based. Left None it is read out of the slide name ("slide 1" -> 1),
        and only if the name carries no number does binding fall back to the whole deck --
        recorded in `coverage_note()` as a weaker bind, never silently.
        """
        if page is None:
            m = re.search(r"\d+", self.slide)
            page = int(m.group(0)) if m else None
        self._deck_page = page
        try:
            self._deck_text = deck_text(path, page=page)
        except IndexError:
            # A named page the deck does not render is a defect in the caller, not a
            # reason to bind against everything and call it covered.
            self._deck_text = None
            self.failures.append(
                f"{self.slide}: bind_deck asked for page {page} of {path}, which does "
                f"not render that many pages; nothing was bound")
            return
        self._deck_path = str(path)

    _PRINTED = re.compile(r"(?:slide|source line|left column|takeaway):\s*([^)]+?)\s*\)")

    def _verify_printed(self, label: str) -> None:
        """If the label claims the page prints something, check the page prints it."""
        if self._deck_text is None:
            return
        m = self._PRINTED.search(label)
        if not m:
            self._unbound.append(label)
            return
        token = m.group(1).strip()
        if token and token not in self._deck_text:
            self.failures.append(
                f"{self.slide}: {label} -- the label says the page prints {token!r}, "
                f"and it does not appear in {self._deck_path}")
        else:
            self._bound += 1

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n{title}")

    def check(self, label: str, got, want, tol: float = 0) -> bool:
        """Assert one printed value. `tol` is an absolute tolerance for floats."""
        self._n += 1
        if isinstance(want, (int, float)) and isinstance(got, (int, float)):
            ok = abs(float(got) - float(want)) <= tol
        else:
            ok = got == want
        if not self.quiet:
            print(f"  [{'ok  ' if ok else 'FAIL'}] {label:<52} got {got!r}  slide says {want!r}")
        if not ok:
            self.failures.append(
                f"{self.slide}: {label} -- computed {got!r}, slide prints {want!r}")
        self._verify_printed(label)
        return ok

    def pct(self, label: str, numerator, denominator, printed, population: str,
            decimals: int = 0) -> bool:
        """Assert a printed PERCENTAGE and register the denominator it was taken over.

        WHY A SEPARATE METHOD. Every percentage defect on the originating deck was a
        denominator defect, never an arithmetic one: two shares of ELIGIBLE records were
        printed three inches under a third that was a share of ALL records; two more were
        shares of all records while the tab computing them reported shares of the filtered
        remainder.
        In every case the number was right and the population behind it was unstated, so
        nothing could catch it -- `check` asserts the value and says nothing about what it is
        a share OF. Registering the population makes the denominator a FIELD, not a habit.

        THE REGISTER IS A RECORD, NOT AN ENFORCEMENT. Nothing reads it automatically; a
        percentage can still reach a page with no gate behind it. An earlier version of this
        docstring claimed a craft-gate rule consumed it, which was false and made the doc the
        reason nobody built the gate.
        """
        got = round(numerator / denominator * 100, decimals)
        want = round(float(printed), decimals)
        ok = self.check(f"{label} (of {population})", got, want)
        self.out.setdefault("percentages", []).append({
            "printed": f"{want:g}%",
            "value": got,
            "numerator": int(numerator),
            "denominator": int(denominator),
            "population": population,
            "label": label,
        })
        return ok

    def note(self, key: str, value) -> None:
        """Record a value in the JSON payload without asserting it."""
        self.out[key] = value

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.out, indent=2, default=float), encoding="utf-8")
        if not self.quiet:
            print(f"\nwrote {path}")

    def coverage_note(self) -> str:
        """How much of this run was actually bound to the page, stated in the output."""
        if self._deck_text is None:
            return ("NOT BOUND TO A DECK: every value here was compared against a literal "
                    "in this file, and nothing read the page. Call bind_deck().")
        where = (f"page {self._deck_page} of {self._deck_path}" if self._deck_page
                 else f"{self._deck_path} (WHOLE DECK -- the slide name carries no page "
                      f"number, so a value printed on any page satisfies these binds)")
        return (f"{self._bound} assertion(s) bound to {where}; "
                f"{len(self._unbound)} carried no 'slide:' token and were not bound")

    def report(self) -> int:
        """Print the verdict and return a process exit code."""
        if self.failures:
            print(f"\n{len(self.failures)} {self.slide} VALUE(S) DO NOT MATCH THE DATA:")
            for f in self.failures:
                print(f"  - {f}")
            return 1
        if not self.quiet:
            print(f"\nAll {self.slide} values reproduce from the source workbook "
                  f"({self._n} checks).")
        return 0
