"""Tests for the dark-input detector.

The detector exists to catch extraction that is POPULATED BUT NOT USABLE. It is
itself guard infrastructure, so it needs to fail when it should - a detector that
cannot report a problem is the exact defect it was built to find.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.check_dark_inputs import (  # noqa: E402
    classify_discrimination,
    classify_scalar,
    classify_terms,
)


# --- discrimination: the load-bearing probe ---------------------------------

def test_identical_outputs_are_dark():
    """The real 2026-09-02 shape: _score_industry_match returned 3.0 for a
    Deployment Strategist and for a Zookeeper Assistant alike."""
    r = classify_discrimination([3.0, 3.0])
    assert r.verdict == "DARK"
    assert "cannot discriminate" in r.detail


def test_differing_outputs_are_ok():
    assert classify_discrimination([7, 4]).verdict == "OK"


def test_a_single_output_cannot_prove_discrimination():
    """One sample is not evidence either way; that must be an ERROR, not a pass."""
    assert classify_discrimination([5.0]).verdict == "ERROR"


def test_near_identical_but_distinct_outputs_are_ok():
    """Guard on the guard: the check must not round distinct values together, or a
    dimension with weak-but-real signal would read as dead."""
    assert classify_discrimination([5.0, 5.0000001]).verdict == "OK"


# --- terms: a secondary signal, only valid for substring consumers -----------

def test_all_long_terms_are_dark():
    """The real _extract_skills shape: 9-word prose bullets that matched zero JDs."""
    r = classify_terms([
        "Strategic operations and planning (FY planning, OKRs, budget management)",
        "Executive-level communication and stakeholder management (board-level deliverables)",
    ])
    assert r.verdict == "DARK"


def test_one_usable_term_is_enough_to_clear_the_terms_check():
    """Deliberate: a term corpus may hold long entries so long as usable ones exist.

    NOTE this is why the terms check alone is insufficient, and why discrimination is
    the load-bearing probe. The live profile has 1/4 usable terms, clears this check,
    and its dimension is STILL dead because that one term matches nothing real.
    Liveness against a real corpus is the unbuilt third probe that would close this.
    """
    assert classify_terms(["SQL", "a very long prose bullet that cannot ever match"]).verdict == "OK"


def test_empty_terms_are_dark():
    assert classify_terms([]).verdict == "DARK"


def test_a_non_list_is_an_error_not_a_pass():
    """An input the probe cannot evaluate must never read as clean."""
    assert classify_terms("SQL, APIs").verdict == "ERROR"


# --- scalar -----------------------------------------------------------------

@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_scalars_are_dark(empty):
    assert classify_scalar(empty).verdict == "DARK"


def test_zero_is_a_value_not_an_absence():
    """0 and False are falsy but are real extracted values; treating them as empty
    would make the detector lie about a working extraction."""
    assert classify_scalar(0).verdict == "OK"
    assert classify_scalar(False).verdict == "OK"
