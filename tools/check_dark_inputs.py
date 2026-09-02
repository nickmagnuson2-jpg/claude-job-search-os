#!/usr/bin/env python3
"""check_dark_inputs.py - find extraction that is POPULATED BUT NOT USABLE.

WHY THIS EXISTS
---------------
On 2026-09-02, eleven defects of one class were found in a single subsystem. Every one
sat at a prose->machine boundary: code reading something a human wrote for humans and
treating it as data. A sample:

  - `_extract_target_seniority` matched the word "head" inside a blockquoted FILE PATH,
    resolving target seniority to level 8. The owner's own #1 target title then took a
    distance penalty on 25% of the scoring weight while an engineering-director title
    scored 10/10. The ranking was inverted against his own lane.
  - `_extract_skills` returned whole human-readable bullets ("Strategic operations and
    planning (FY planning, OKRs, budget management)"). Those cannot appear as a substring
    in any job posting, so a REAL job description scored 0.0 on that dimension while an
    EMPTY one scored 3.0. Having a description was a penalty.
  - `_extract_target_industries` returned prose sentences, so its dimension returned the
    same constant for every role ever scored.

None were empty. All looked populated. That is exactly why they survived: the repo's
enforcement rule catches "written down but never built," and this is its sibling -
"populated but not usable."

THE MEASUREMENT, AND WHY IT IS NOT A HEURISTIC
----------------------------------------------
The tempting check is "does this output look like prose?" That is a guess, and a
detector built on a guess is the same bug wearing a lab coat.

So the load-bearing probe is DISCRIMINATION, measurable without understanding meaning:
feed a function two inputs a correct implementation MUST score differently, and check
the outputs actually differ. A dimension whose output never varies cannot inform a
ranking, however populated its inputs look. This is the repo's own mutation-testing
principle - "a green test is not evidence, mutation survival is" - applied one layer
down, to data instead of code.

Term length is reported as a secondary SIGNAL, never a verdict on its own, because a
long term is only a problem relative to what it must match.

WHAT THIS DOES NOT YET DO (stated so the tool does not overclaim, which would be the
same defect it exists to find)
------------------------------------------------------------------------------------
  - LIVENESS: run extracted terms against a live corpus and flag terms that have never
    matched anything real. Needs a corpus wired in; a fixture cannot serve, because a
    fixture is written to make the test pass.
  - REACHABILITY: for each written path/field/variable, assert something reads it.
    Would catch the four 2026-09-02 defects that were never about scoring - a /standup
    glob on a path nothing writes, an `errors = []` declared and never used, a config
    field defined only in its own comment, and a marker pointing at a workflow that
    does not exist.

EXIT CODES
----------
  0  no dark inputs
  2  at least one DARK probe (gate-friendly)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# A comparison term longer than this cannot realistically appear verbatim inside a
# document it must match. Mirrors scorer._MAX_USABLE_TERM_WORDS deliberately: if that
# constant moves, this reporting threshold should move with it.
MAX_USABLE_TERM_WORDS = 4


@dataclass
class Probe:
    name: str
    source: str
    consumer: str
    kind: str                        # "terms" | "scalar" | "dimension"
    # How the CONSUMER matches these terms. The length rule is only meaningful for
    # substring matching: a fuzzy matcher (SequenceMatcher) scores partial overlap, so
    # a long term is not dead there. Applying one rule to both consumers produced a
    # false positive on _extract_target_titles the first time this tool was run, while
    # the discrimination probe on the same function correctly said OK. Recorded here
    # because a detector that cannot be wrong about itself is not trustworthy.
    match_mode: str = "substring"    # "substring" | "fuzzy"
    run: object = None
    discriminate: object = None
    notes: str = ""


@dataclass
class Result:
    name: str
    verdict: str                     # OK | DARK | ERROR
    detail: str
    value_preview: str = ""
    signals: list = field(default_factory=list)


def _preview(value, limit: int = 90) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def classify_terms(value, max_words: int = MAX_USABLE_TERM_WORDS) -> Result:
    """A list of match-terms is dark when NO term is short enough to ever match.

    Not "some terms are long" - a term corpus may legitimately contain long entries so
    long as usable ones exist. It is dark only when every term is unusable, because
    then the dimension can never fire at all.
    """
    if not isinstance(value, (list, tuple)):
        return Result("", "ERROR", f"expected a list of terms, got {type(value).__name__}")
    if not value:
        return Result("", "DARK", "returned no terms at all", _preview(value))

    usable = [t for t in value if isinstance(t, str) and len(t.split()) <= max_words]
    if not usable:
        longest = max(len(str(t).split()) for t in value)
        return Result(
            "", "DARK",
            f"all {len(value)} terms exceed {max_words} words (longest: {longest}); "
            "none can appear verbatim in a target document",
            _preview(value), [f"usable_terms=0/{len(value)}"],
        )
    return Result(
        "", "OK", f"{len(usable)}/{len(value)} terms are usable",
        _preview(value), [f"usable_terms={len(usable)}/{len(value)}"],
    )


def classify_discrimination(outputs) -> Result:
    """A dimension is dark when it returns the same value for inputs that differ.

    The load-bearing check. It needs no understanding of meaning: a scoring dimension
    that cannot tell a strong match from a weak one contributes nothing to a ranking.
    """
    if not isinstance(outputs, (list, tuple)) or len(outputs) < 2:
        return Result("", "ERROR", "discrimination probe needs at least 2 outputs")
    distinct = {repr(o) for o in outputs}
    if len(distinct) == 1:
        return Result(
            "", "DARK",
            f"returned the identical value {outputs[0]!r} for {len(outputs)} inputs that "
            "should score differently; this dimension cannot discriminate",
            _preview(outputs), [f"distinct_outputs=1/{len(outputs)}"],
        )
    return Result(
        "", "OK", f"{len(distinct)} distinct outputs across {len(outputs)} inputs",
        _preview(outputs), [f"distinct_outputs={len(distinct)}/{len(outputs)}"],
    )


def classify_scalar(value) -> Result:
    if value in (None, "", [], {}):
        return Result("", "DARK", "returned an empty value on live data", _preview(value))
    return Result("", "OK", "returned a value", _preview(value))


# ---------------------------------------------------------------------------
# Probe registry. Explicit, not auto-discovered: auto-discovery would silently skip
# anything it failed to import or call, which is the exact failure mode this tool
# exists to catch - a check that examines nothing and reports clean.
# ---------------------------------------------------------------------------

def build_probes(repo_root: Path) -> list[Probe]:
    from tools.career_scanner import scorer

    ctx = scorer.load_scoring_context(repo_root)

    strong = {
        "title": "Deployment Strategist", "company": "X", "location": "San Francisco",
        "department": "Solutions", "team": "Deployment",
        "description_plain": (
            "6+ years in customer-facing roles. Familiarity with LLMs, APIs, JSON. "
            "Basic SQL literacy. Strong Excel / PPT skills."
        ),
    }
    weak = {
        "title": "Zookeeper Assistant", "company": "Y", "location": "San Francisco",
        "department": "Animal Care", "team": "Reptiles",
        "description_plain": "Feed the animals and clean enclosures. Must enjoy early mornings.",
    }

    return [
        Probe("scorer._extract_skills", "data/profile.md",
              "scorer._score_keyword_overlap - substring match vs JD body (20% of weight)",
              "terms", run=lambda: ctx.get("skills", []),
              notes="Returned 9-word prose bullets until 2026-09-02; matched zero real JDs."),
        Probe("scorer._extract_target_industries", "data/goals.md",
              "scorer._score_industry_match - fuzzy match vs department/team (20%)",
              "terms", run=lambda: ctx.get("target_industries", []),
              notes="Returned whole prose sentences; the dimension was a constant."),
        Probe("scorer._extract_target_titles", "data/goals.md",
              "scorer._score_title_match - FUZZY match vs role title (35%)",
              "terms", match_mode="fuzzy", run=lambda: ctx.get("target_titles", []),
              notes="The dimension that was working. Kept as a control."),
        Probe("scorer._score_keyword_overlap", "data/profile.md", "20% of the role score",
              "dimension", discriminate=lambda: [
                  scorer._score_keyword_overlap(strong, ctx),
                  scorer._score_keyword_overlap(weak, ctx)]),
        Probe("scorer._score_industry_match", "data/goals.md", "20% of the role score",
              "dimension", discriminate=lambda: [
                  scorer._score_industry_match(strong, ctx),
                  scorer._score_industry_match(weak, ctx)]),
        Probe("scorer._score_seniority_match", "data/goals.md", "25% of the role score",
              "dimension", discriminate=lambda: [
                  scorer._score_seniority_match({"title": "Deployment Strategist"}, ctx),
                  scorer._score_seniority_match({"title": "VP of Engineering"}, ctx)]),
        Probe("scorer._score_title_match", "data/goals.md", "35% of the role score",
              "dimension", discriminate=lambda: [
                  scorer._score_title_match(strong, ctx),
                  scorer._score_title_match(weak, ctx)]),
        Probe("scorer.score_role (end to end)", "data/goals.md + data/profile.md",
              "the ranked list the owner reads",
              "dimension", discriminate=lambda: [
                  scorer.score_role(strong, ctx), scorer.score_role(weak, ctx)]),
    ]


def run(repo_root: Path) -> list[Result]:
    results: list[Result] = []
    for probe in build_probes(repo_root):
        try:
            if probe.kind == "terms":
                if probe.match_mode == "fuzzy":
                    r = Result("", "OK",
                               "fuzzy-matched consumer; term length is not a liveness "
                               "signal here (see the discrimination probe instead)",
                               _preview(probe.run()))
                else:
                    r = classify_terms(probe.run())
            elif probe.kind == "dimension":
                r = classify_discrimination(probe.discriminate())
            else:
                r = classify_scalar(probe.run())
        except Exception as exc:      # a probe that cannot run is not a pass
            r = Result("", "ERROR", f"{type(exc).__name__}: {exc}")
        r.name = probe.name
        results.append(r)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = run(Path(args.repo_root))
    dark = [r for r in results if r.verdict != "OK"]

    if args.json:
        print(json.dumps({"probes": len(results), "dark": len(dark),
                          "clean": not dark, "results": [vars(r) for r in results]},
                         indent=2, default=str))
    else:
        print(f"Dark-input scan - {len(results)} probes, {len(dark)} dark\n")
        for r in results:
            mark = {"OK": "  ok  ", "DARK": " DARK ", "ERROR": "ERROR "}[r.verdict]
            print(f"[{mark}] {r.name}")
            print(f"          {r.detail}")
            if r.value_preview:
                print(f"          value: {r.value_preview}")
        if dark:
            print("\nA DARK probe means the value is POPULATED BUT NOT USABLE: it exists, "
                  "it looks fine,\nand it cannot affect the outcome it feeds. Fix the "
                  "extraction or the terms - do not\nwiden the threshold to make this pass.")
    return 2 if dark else 0


if __name__ == "__main__":
    sys.exit(main())
