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


import pytest  # noqa: E402


@pytest.mark.parametrize("title,level", [
    ("International Partnerships", 4),         # not 'intern' (1); no other key, so mid
    ("International Sales Manager", 5),
    ("SVP, Engineering", 9),
    ("EVP Operations", 9),
    ("Directors of Product", 8),
    ("Heads of Growth", 8),
    ("Leadership Development Partner", 4),     # not 'lead' (7); no other key, so mid
    ("Headcount Planning Analyst", 4),         # not 'head' (8)
    ("Software Engineering Intern", 1),
    ("Tech Lead", 7),
    ("Engineering Team Leader", 7),
    ("VP, Product", 9),
])
def test_seniority_matches_whole_words_only(title, level):
    """A substring test read International as intern and Leadership as lead (finding 40.F1)."""
    assert extract_seniority(title) == level


# ---------------------------------------------------------------------------
# RULING Q19 b (2026-11-16, data/workstreams/scoring-integrity.md:470):
# "score a title with no seniority word as on target (neutral)". Evidence: 46 of 62
# Family A titles had no seniority word, defaulted to level 4 against a median
# target of 6, and lost 4 of 10 seniority points for omitting a word.
# Test the ruling pinned: a Family A title with no seniority word gets the same
# seniority sub-score as an on-target title.
# ---------------------------------------------------------------------------

def test_q19b_no_seniority_word_scores_same_as_on_target_title():
    ctx = {"target_seniority_level": 6}
    no_word = _score_seniority_match({"title": "Deployment Strategist"}, ctx)
    on_target = _score_seniority_match({"title": "Chief of Staff"}, ctx)  # parses to 6
    assert on_target == 10.0
    assert no_word == on_target


@pytest.mark.parametrize("title", [
    "Deployment Strategist",
    "Forward Deployed Engineer",
    "Customer Outcomes Partner",
    "Strategy and Operations",
    "Business Operations",
])
@pytest.mark.parametrize("target", [1, 4, 6, 9])
def test_q19b_no_seniority_word_is_on_target_at_any_target_level(title, target):
    assert _score_seniority_match({"title": title}, {"target_seniority_level": target}) == 10.0


@pytest.mark.parametrize("title,expected", [
    ("Senior Deployment Strategist", 8.0),   # 5 vs 6
    ("Head of Deployment", 6.0),             # 8 vs 6
    ("VP of Engineering", 4.0),              # 9 vs 6
    ("Deployment Intern", 1.0),              # 1 vs 6 -> 0, clamped to 1
])
def test_q19b_titles_with_a_seniority_word_still_score_by_distance(title, expected):
    assert _score_seniority_match({"title": title}, {"target_seniority_level": 6}) == expected


def test_q19b_keyword_fallback_path_also_treats_no_word_as_on_target():
    # Contexts with only a 'Target seniority:' keyword take the back-compat branch.
    ctx = {"target_seniority": "director"}
    assert _score_seniority_match({"title": "Deployment Strategist"}, ctx) == 10.0
    assert _score_seniority_match({"title": "Senior Strategist"}, ctx) == 4.0


def test_q19b_no_target_at_all_stays_neutral_five():
    # Without any target there is no "on target"; the pre-existing neutral 5.0 holds.
    assert _score_seniority_match({"title": "Deployment Strategist"}, {}) == 5.0


def test_q19b_target_level_inference_is_unchanged():
    # Q16 c is not decided: the median of target titles still counts a no-word title as 4.
    assert extract_seniority("Deployment Strategist") == 4
    assert _resolve_target_level("", ["Deployment Strategist", "Chief of Staff", "Head of Ops"]) == 6
