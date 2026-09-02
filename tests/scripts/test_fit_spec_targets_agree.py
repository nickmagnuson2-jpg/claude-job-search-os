"""The goals.md <-> fit-spec.yaml agreement guard.

Origin 2026-09-02. Two instances of one defect class were found the same day:

  1. `data/goals.md` names "Engagement Manager" as a target role type, while
     `data/calibration/fit-spec.yaml` listed "engagement manager" under
     not_fit_title_patterns at -4.0 (generalised from a single company). Measured
     against the live config, the owner's #1 stated target title scored 1/10 while an
     engineering-director title scored 7/10.
  2. goals.md states a NON-NEGOTIABLE structural filter -- there must be someone on the
     team to learn the function from -- and fit-spec.yaml had no concept of the team at
     all. The company-level `bench:` gate written on 2026-08-26 was never populated on a
     single entry, so it existed only as prose.

Both are the same bug: a requirement stated in goals.md that the scoring model never
learned. These tests make a third instance impossible.

The second test deliberately does NOT demand a scoring dimension per non-negotiable.
Some are correctly enforced elsewhere (geography is a hard gate in
company_scorer.geo_gate, and duplicating it into fit-spec would create two sources of
truth). Some cannot be scored from a job posting at all ("someone who has time for me"
is only knowable from a conversation), and manufacturing a signal for those is worse
than a recorded gap. So what is required is a recorded DISPOSITION, not a dimension:
omission fails, a deliberate "cannot score this" is legal but must be written down.
"""
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scorer import load_scoring_context  # noqa: E402

FIT_SPEC = REPO_ROOT / "data" / "calibration" / "fit-spec.yaml"
GOALS = REPO_ROOT / "data" / "goals.md"

# Valid dispositions for a stated non-negotiable.
#   scored_here          -- fit-spec.yaml carries a weight/dimension for it
#   enforced_elsewhere   -- a different module owns it; must name the path
#   unscoreable          -- cannot be derived from a posting; must give a reason
VALID_DISPOSITIONS = {"scored_here", "enforced_elsewhere", "unscoreable"}


def _load_fit_spec() -> dict:
    if not FIT_SPEC.exists():
        pytest.skip(f"{FIT_SPEC} not present")
    return yaml.safe_load(FIT_SPEC.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Guard 1 -- a target title may never also be a not-fit pattern
# ---------------------------------------------------------------------------

def test_no_target_title_is_also_a_not_fit_pattern():
    """The Engagement Manager bug: goals.md wants it, fit-spec penalises it -4.0.

    Matching is substring-based in both directions because fit-spec patterns are
    fragments ("engagement manager") while goals.md states full titles
    ("Engagement Manager / Implementation Lead").
    """
    if not GOALS.exists():
        pytest.skip("goals.md not present (gitignored; present on the owner's machine)")

    spec = _load_fit_spec()
    not_fit = [p.lower().strip() for p in spec.get("not_fit_title_patterns", [])]
    targets = [t.lower().strip() for t in load_scoring_context(REPO_ROOT).get("target_titles", [])]

    collisions = [
        (t, p) for t in targets for p in not_fit
        if p and (p in t or t in p)
    ]

    assert not collisions, (
        "goals.md and fit-spec.yaml disagree about what Nick wants, and the penalty "
        "wins at scoring time. Collisions (target_title, not_fit_pattern): "
        f"{collisions}. Fix by moving the real learned signal into scope_disqualifiers "
        "(a title-shape screen) rather than vetoing a title he actively targets."
    )


# ---------------------------------------------------------------------------
# Guard 2 -- every stated non-negotiable carries a recorded disposition
# ---------------------------------------------------------------------------

def test_every_non_negotiable_has_a_recorded_disposition():
    """A non-negotiable may be scored, enforced elsewhere, or declared unscoreable.

    What it may NOT be is absent. That is how the team/learn-from filter -- a stated
    non-negotiable since 2026-05-03 -- reached 2026-09-02 with no representation in the
    scoring model whatsoever.
    """
    spec = _load_fit_spec()
    registry = spec.get("non_negotiables")

    assert registry, (
        "fit-spec.yaml has no `non_negotiables:` registry. Every non-negotiable stated "
        "in goals.md must appear here with a disposition of "
        f"{sorted(VALID_DISPOSITIONS)}, so that an omission cannot be silent."
    )

    for key, entry in registry.items():
        assert isinstance(entry, dict), f"non_negotiables.{key} must be a mapping"
        disp = entry.get("disposition")
        assert disp in VALID_DISPOSITIONS, (
            f"non_negotiables.{key}.disposition = {disp!r}; must be one of "
            f"{sorted(VALID_DISPOSITIONS)}"
        )
        # A disposition that shifts responsibility must say where, or why.
        if disp == "enforced_elsewhere":
            assert entry.get("where"), (
                f"non_negotiables.{key} claims enforced_elsewhere but names no path. "
                "An unlocatable claim of enforcement is how a rule goes missing."
            )
        if disp == "unscoreable":
            assert entry.get("reason"), (
                f"non_negotiables.{key} claims unscoreable but gives no reason. "
                "A deliberate gap is legal; an unexplained one is not."
            )
        if disp == "scored_here":
            assert entry.get("dimension"), (
                f"non_negotiables.{key} claims scored_here but names no dimension."
            )
            assert entry["dimension"] in (spec.get("weights") or {}), (
                f"non_negotiables.{key} names dimension {entry['dimension']!r}, which "
                "does not exist in fit-spec weights."
            )


def test_the_learn_from_filter_specifically_is_represented():
    """The regression that motivated this file.

    Pinned by name rather than left to the generic guard above, because this is the
    one that was missing and the generic test would pass on an empty-but-present
    registry.
    """
    spec = _load_fit_spec()
    registry = spec.get("non_negotiables") or {}
    assert "learn_from" in registry, (
        "goals.md: 'the next role must include someone beside me who is good, whose "
        "judgment I respect, and who has time for me.' That non-negotiable has no "
        "entry in fit-spec.yaml non_negotiables."
    )
