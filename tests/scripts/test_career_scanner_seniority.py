"""Tests for the seniority-scoring fix in tools/career_scanner/scorer.py.

Fable-audit Theme 3 deep dive found two bugs that made the seniority dimension
(25% of every role's score) permanently neutral in real scans:
  1. SENIORITY_LEVELS had no 'manager' key, so Operations/Program Manager titles
     (most of the user's targets AND many scanned roles) defaulted to mid (4).
  2. _extract_target_seniority only read an explicit 'Target seniority:' line;
     goals.md states target *titles*, not levels, so the target was always empty
     and _score_seniority_match returned a flat 5.0 for every role.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scorer import (
    SENIORITY_LEVELS,
    _resolve_target_level,
    _score_seniority_match,
    extract_seniority,
)


def test_manager_is_a_known_level():
    assert "manager" in SENIORITY_LEVELS
    assert extract_seniority("Operations Manager") == SENIORITY_LEVELS["manager"]
    assert extract_seniority("Program Manager") == SENIORITY_LEVELS["manager"]


def test_resolve_prefers_explicit_keyword():
    assert _resolve_target_level("Senior", []) == SENIORITY_LEVELS["senior"]
    assert _resolve_target_level("director", ["Program Manager"]) == SENIORITY_LEVELS["director"]


def test_resolve_infers_median_from_titles_when_no_keyword():
    # Levels: lead(7), manager(5), <default mid>(4) -> median 5.
    titles = ["Engagement Lead", "Program Manager", "Generalist ops role"]
    assert _resolve_target_level("", titles) == 5


def test_resolve_returns_none_without_keyword_or_titles():
    # No signal at all -> None, so scoring stays neutral (pre-fix behavior).
    assert _resolve_target_level("", []) is None


def test_seniority_dimension_is_live_not_neutral():
    """With a resolved target level, a matching-level role must out-score a
    far-off one. Before the fix both returned a flat 5.0 (the bug)."""
    ctx = {"target_seniority_level": 5}  # target ~= manager
    manager_role = {"title": "Program Manager"}          # level 5 -> exact match
    director_role = {"title": "Director of Operations"}  # level 8 -> far off

    manager_score = _score_seniority_match(manager_role, ctx)
    director_score = _score_seniority_match(director_role, ctx)

    assert manager_score > director_score, (manager_score, director_score)
    assert manager_score == 10.0  # |5-5|*2 -> 10
    # A director is 3 levels off: 10 - 3*2 = 4.
    assert director_score == 4.0


def test_neutral_when_level_unresolved():
    # target_seniority_level None and no keyword -> neutral 5.0 preserved.
    assert _score_seniority_match({"title": "Program Manager"}, {}) == 5.0
