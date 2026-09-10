"""The seat is defined in three places. This test makes them agree.

Built 2026-09-08 after the three drifted apart unnoticed for a month:

  A. data/scan-targets.yaml  x-lane-filters / x-lane-excludes
     The definition actually ENFORCED. career_scanner.scanner.title_matches()
     applies it at fetch time with allow_engineer=False, before scoring.
  B. data/calibration/fit-spec.yaml  positive_title_patterns
     The CALIBRATED overlay, derived from Nick's hand-labelled companies and
     applied by scorer.score_role() AFTER A's gate.
  C. data/discover-presets.yaml  <person preset>.seat_titles + query
     What the weekly discovery collector SOURCES.

What went wrong without this test:

  * C hunted "forward-deployed engineer" while A hard-rejects any title whose
    head noun contains "engineer". Of 21 people the preset produced, 2 passed
    the seat test and 19 were engineer-titled. The preset was sourcing exactly
    what the seat definition screens out.
  * B listed "solutions engineer" and "field engineer" as positives. Because A
    runs first, those titles never reached the scorer, so the patterns were
    unreachable - dead policy that still read as intent.
  * A carried "forward deployed" and B carried both that and "forward-deployed".
    A title spelled with the hyphen whose only in-lane token was that one
    ("Forward-Deployed Lead") was dropped at the gate.

Each of those is a silent recall or precision failure that a green suite did
not notice, because no test compared the files to each other.

These are DATA files under data/, which is gitignored. The tests skip when the
files are absent so a fresh public clone stays green; they are not decorative
on Nick's machine, which is where the drift happens.
"""
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scanner import title_matches  # noqa: E402

TARGETS = REPO_ROOT / "data" / "scan-targets.yaml"
FIT_SPEC = REPO_ROOT / "data" / "calibration" / "fit-spec.yaml"
PRESETS = REPO_ROOT / "data" / "discover-presets.yaml"


def _load(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(REPO_ROOT)} absent (gitignored tree)")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _lane_filters():
    doc = _load(TARGETS)
    includes = doc.get("x-lane-filters") or []
    excludes = doc.get("x-lane-excludes") or []
    assert includes, "x-lane-filters is empty; the seat definition would match everything"
    return includes, excludes


def _person_presets():
    doc = _load(PRESETS)
    return {name: p for name, p in (doc.get("presets") or {}).items()
            if p.get("entity_type") == "person"}


# ---------------------------------------------------------------- C against A

def test_every_seat_title_survives_the_enforced_gate():
    """C may not hunt a title A screens out.

    This is the one that fired in the wild: `deployment-leads` asked for
    "forward-deployed engineer" and A rejects it, so a weekly job spent a month
    sourcing people the seat definition excludes.
    """
    includes, excludes = _lane_filters()
    presets = _person_presets()
    assert presets, "no person presets found; this test would pass vacuously"
    for name, preset in presets.items():
        seat_titles = preset.get("seat_titles")
        assert seat_titles, (
            f"person preset {name!r} has no seat_titles. Without a structured "
            f"list there is nothing to bind to x-lane-filters and the prose "
            f"query can drift silently, which is what this test exists to stop.")
        for title in seat_titles:
            assert title_matches(title, includes, excludes, allow_engineer=False), (
                f"preset {name!r} sources {title!r}, which career_scanner drops "
                f"at the fetch gate. Either add it to x-lane-filters or stop "
                f"sourcing it.")


def test_seat_titles_appear_in_the_prose_query():
    """The prose the Agent actually receives must match the structured field.

    seat_titles is what this test can check; `query` is what the Exa Agent is
    given. If they diverge, the guard passes while the producer does something
    else - the exact failure shape this whole test file exists for.
    """
    presets = _person_presets()
    for name, preset in presets.items():
        query = (preset.get("query") or "").lower()
        assert query, f"person preset {name!r} has no query"
        for title in preset.get("seat_titles") or []:
            assert title.lower() in query, (
                f"preset {name!r} declares seat title {title!r} but its query "
                f"does not contain it. The structured field and the prose sent "
                f"to the Agent have drifted.")


# ---------------------------------------------------------------- B against A

def test_every_fit_spec_positive_is_reachable_behind_the_gate():
    """B may not score a title A has already dropped.

    scanner.title_matches() runs at fetch (scanner.py:192); scorer.score_role()
    runs after (scanner.py:255). A positive pattern that A rejects can never
    fire on a scanned role, so it is dead policy that still reads as intent.
    Caught "solutions engineer" and "field engineer"; also caught A carrying
    only the unhyphenated "forward deployed".
    """
    includes, excludes = _lane_filters()
    spec = _load(FIT_SPEC)
    patterns = spec.get("positive_title_patterns") or []
    assert patterns, "fit-spec has no positive_title_patterns"
    unreachable = [p for p in patterns
                   if not title_matches(p, includes, excludes, allow_engineer=False)]
    assert not unreachable, (
        f"fit-spec positive_title_patterns unreachable behind the fetch gate: "
        f"{unreachable}. Either add the vocabulary to x-lane-filters or remove "
        f"the pattern - as written the scorer can never see these titles.")


def test_no_engineer_titles_anywhere_in_the_seat_definition():
    """Nick's standing call, 2026-09-02 and reaffirmed 2026-09-08: not the
    engineering shape of this function.

    Asserted across all three files at once so the decision cannot be honoured
    in two of them and quietly reversed in the third.
    """
    spec = _load(FIT_SPEC)
    offenders = []
    for p in spec.get("positive_title_patterns") or []:
        if "engineer" in p.lower():
            offenders.append(f"fit-spec positive_title_patterns: {p!r}")
    for name, preset in _person_presets().items():
        for t in preset.get("seat_titles") or []:
            if "engineer" in t.lower():
                offenders.append(f"preset {name} seat_titles: {t!r}")
        if "engineer" in (preset.get("query") or "").lower():
            q = preset["query"].lower()
            # A query may legitimately say "NOT engineering roles". Only flag a
            # bare mention that is not part of an explicit exclusion.
            if "not engineering" not in q and "exclude" not in q:
                offenders.append(f"preset {name} query mentions engineer without excluding it")
    assert not offenders, (
        "the no-engineering-titles decision is contradicted in: " + "; ".join(offenders))
