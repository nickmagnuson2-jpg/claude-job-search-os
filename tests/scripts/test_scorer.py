"""Tests for the career scanner scoring engine."""
import os
import tempfile
from pathlib import Path

import pytest

from tools.career_scanner.scorer import (
    SENIORITY_LEVELS,
    extract_seniority,
    load_scoring_context,
    score_role,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_context():
    """A scoring context with known values for deterministic testing."""
    return {
        "target_titles": ["Chief of Staff", "Head of Operations", "Strategy & Operations Lead"],
        "target_seniority": "Senior",
        "target_industries": ["Health", "Wellness", "Fintech"],
        "skills": ["Python", "SQL", "project management", "cross-functional", "GTM"],
    }


@pytest.fixture
def good_fit_role():
    """A role that should score high across all dimensions."""
    return {
        "title": "Senior Head of Operations",
        "company": "HealthCo",
        "department": "Operations",
        "team": "Health",
        "location": "San Francisco, CA",
        "remote": False,
        "employment_type": "FullTime",
        "url": "https://example.com/job/123",
        "apply_url": "https://example.com/job/123/apply",
        "published_at": "2026-04-01",
        "description_plain": "We need someone with Python, SQL, project management, cross-functional leadership, and GTM experience.",
        "ats": "greenhouse",
    }


@pytest.fixture
def poor_fit_role():
    """A role that should score low across all dimensions."""
    return {
        "title": "Junior Frontend Developer",
        "company": "CryptoStartup",
        "department": "Engineering",
        "team": "Frontend",
        "location": "Remote",
        "remote": True,
        "employment_type": "FullTime",
        "url": "https://example.com/job/456",
        "apply_url": "https://example.com/job/456/apply",
        "published_at": "2026-04-01",
        "description_plain": "Looking for a React and TypeScript developer to build user interfaces.",
        "ats": "lever",
    }


# ---------------------------------------------------------------------------
# score_role: basic contract
# ---------------------------------------------------------------------------

class TestScoreRoleContract:
    def test_returns_integer(self, good_fit_role, sample_context):
        result = score_role(good_fit_role, sample_context)
        assert isinstance(result, int)

    def test_score_between_1_and_10(self, good_fit_role, sample_context):
        result = score_role(good_fit_role, sample_context)
        assert 1 <= result <= 10

    def test_poor_fit_also_between_1_and_10(self, poor_fit_role, sample_context):
        result = score_role(poor_fit_role, sample_context)
        assert 1 <= result <= 10


# ---------------------------------------------------------------------------
# score_role: differentiation
# ---------------------------------------------------------------------------

class TestScoreRoleDifferentiation:
    def test_good_fit_scores_higher_than_poor_fit(self, good_fit_role, poor_fit_role, sample_context):
        good_score = score_role(good_fit_role, sample_context)
        poor_score = score_role(poor_fit_role, sample_context)
        assert good_score > poor_score, (
            f"Good fit ({good_score}) should score higher than poor fit ({poor_score})"
        )

    def test_exact_title_match_scores_high(self, sample_context):
        role = {
            "title": "Chief of Staff",
            "company": "AnyCompany",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "",
            "ats": "greenhouse",
        }
        score = score_role(role, sample_context)
        # Exact title match on highest-weighted dimension should produce decent score
        assert score >= 5

    def test_unrelated_title_scores_lower(self, sample_context):
        role = {
            "title": "Zookeeper Assistant",
            "company": "AnyCompany",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "",
            "ats": "greenhouse",
        }
        score = score_role(role, sample_context)
        assert score <= 5


# ---------------------------------------------------------------------------
# score_role: seniority dimension
# ---------------------------------------------------------------------------

class TestScoreRoleSeniority:
    def test_senior_role_scores_well_for_senior_target(self, sample_context):
        role_senior = {
            "title": "Senior Product Manager",
            "company": "Co",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "",
            "ats": "",
        }
        role_junior = {
            "title": "Junior Product Manager",
            "company": "Co",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "",
            "ats": "",
        }
        score_sr = score_role(role_senior, sample_context)
        score_jr = score_role(role_junior, sample_context)
        assert score_sr >= score_jr, (
            f"Senior ({score_sr}) should score >= Junior ({score_jr}) when target is Senior"
        )


# ---------------------------------------------------------------------------
# score_role: keyword dimension
# ---------------------------------------------------------------------------

class TestScoreRoleKeywords:
    def test_many_matching_skills_score_higher(self, sample_context):
        role_match = {
            "title": "Operations Manager",
            "company": "Co",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "Requires Python, SQL, project management, GTM strategy, and cross-functional collaboration.",
            "ats": "",
        }
        role_nomatch = {
            "title": "Operations Manager",
            "company": "Co",
            "department": "",
            "team": "",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "Must enjoy long walks on the beach and solving puzzles.",
            "ats": "",
        }
        score_match = score_role(role_match, sample_context)
        score_nomatch = score_role(role_nomatch, sample_context)
        assert score_match >= score_nomatch


# ---------------------------------------------------------------------------
# score_role: weighted average
# ---------------------------------------------------------------------------

class TestScoreRoleWeightedAverage:
    def test_known_dimension_scores_produce_expected_result(self):
        """Verify the weighting formula: 0.35*d1 + 0.25*d2 + 0.20*d3 + 0.20*d4."""
        # If all dimensions score 10, final should be 10
        context_all_match = {
            "target_titles": ["Exact Match Title"],
            "target_seniority": "Senior",
            "target_industries": ["Tech"],
            "skills": ["a", "b", "c", "d", "e"],
        }
        role = {
            "title": "Exact Match Title",
            "company": "Co",
            "department": "Tech",
            "team": "Tech",
            "location": "",
            "remote": False,
            "employment_type": "",
            "url": "",
            "apply_url": "",
            "published_at": "",
            "description_plain": "a b c d e and more",
            "ats": "",
        }
        score = score_role(role, context_all_match)
        # Should be high (8-10) with all dimensions aligning
        assert score >= 7


# ---------------------------------------------------------------------------
# extract_seniority
# ---------------------------------------------------------------------------

class TestExtractSeniority:
    def test_senior_keyword(self):
        assert extract_seniority("Senior Product Manager") == 5

    def test_junior_keyword(self):
        assert extract_seniority("Junior Analyst") == 2

    def test_director_keyword(self):
        assert extract_seniority("Director of Operations") == 8

    def test_no_seniority_defaults_to_mid(self):
        # A title with no seniority keyword defaults to mid (4). ("Product
        # Manager" is no longer keyword-free — 'manager' now maps to 5.)
        assert extract_seniority("Software Engineer") == 4

    def test_manager_keyword(self):
        # fable-audit Theme 3: 'manager' was missing from the level map, so the
        # user's Operations/Program Manager targets silently defaulted to mid.
        assert extract_seniority("Product Manager") == 5

    def test_vp_keyword(self):
        assert extract_seniority("VP of Engineering") == 9

    def test_case_insensitive(self):
        assert extract_seniority("SENIOR Engineer") == 5


# ---------------------------------------------------------------------------
# SENIORITY_LEVELS dict
# ---------------------------------------------------------------------------

class TestSeniorityLevels:
    def test_has_at_least_10_entries(self):
        assert len(SENIORITY_LEVELS) >= 10

    def test_intern_is_lowest(self):
        assert SENIORITY_LEVELS["intern"] == 1

    def test_chief_is_highest(self):
        assert SENIORITY_LEVELS["chief"] == 10


# ---------------------------------------------------------------------------
# load_scoring_context
# ---------------------------------------------------------------------------

class TestLoadScoringContext:
    def test_extracts_from_mock_files(self, tmp_path):
        """Create mock profile.md and goals.md, verify extraction."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        goals_content = """# Job Search Goals & Thesis

## Search Thesis

> Operator-minded CoS or Head of Ops at a mission-driven Series A-C health/wellness company.

## Target Criteria

**Role types** (ranked, most to least preferred):
1. Chief of Staff
2. Head of Operations
3. Strategy & Operations Lead

**Company stage / size:**
- Series A-C, 50-500 employees

**Target seniority:** Senior

**Industries:**
- Health
- Wellness
- Fintech

**Geography:**
- SF Bay Area
"""
        (data_dir / "goals.md").write_text(goals_content, encoding="utf-8")

        profile_content = """# Profile

## Skills

- Python
- SQL
- Project Management
- Cross-functional leadership
- GTM strategy
"""
        (data_dir / "profile.md").write_text(profile_content, encoding="utf-8")

        context = load_scoring_context(tmp_path)
        assert "target_titles" in context
        assert "target_seniority" in context
        assert "target_industries" in context
        assert "skills" in context
        assert len(context["target_titles"]) > 0
        assert len(context["skills"]) > 0

    def test_missing_files_returns_empty(self, tmp_path):
        """Graceful degradation when files are missing."""
        context = load_scoring_context(tmp_path)
        assert context["target_titles"] == []
        assert context["target_seniority"] == ""
        assert context["target_industries"] == []
        assert context["skills"] == []

    def test_empty_files_returns_empty(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "goals.md").write_text("", encoding="utf-8")
        (data_dir / "profile.md").write_text("", encoding="utf-8")
        context = load_scoring_context(tmp_path)
        assert context["target_titles"] == []
        assert context["skills"] == []


# ---------------------------------------------------------------------------
# Revealed-fit overlay (fit-spec.yaml -> title-shape screen + not-fit taxonomy)
# ---------------------------------------------------------------------------

from tools.career_scanner.scorer import _fit_overlay_adjustment, load_fit_spec

_FIT_SPEC = {
    "positive_title_patterns": ["deployment strateg", "forward deployed"],
    "scope_disqualifiers": ["quota", "land and expand", "customer success"],
    "not_fit_title_patterns": ["chief of staff", "business operations"],
    "not_fit_domain_patterns": ["cpq", "deal desk"],
    "weights": {
        "not_fit_title": -4.0, "not_fit_domain": -3.0,
        "title_shape_screen": -3.0, "in_lane_title": 2.0,
        "min_adjustment": -5.0, "max_adjustment": 3.0,
    },
}


def test_overlay_noop_without_fit_spec():
    """No fit_spec -> overlay is exactly 0 (scorer unchanged)."""
    role = {"title": "Deployment Strategist", "description_plain": "carry a quota"}
    assert _fit_overlay_adjustment(role, {}) == 0.0
    assert _fit_overlay_adjustment(role, None or {}) == 0.0


def test_overlay_in_lane_title_boost():
    role = {"title": "Founding Deployment Strategist",
            "description_plain": "own end-to-end enterprise deployments, build the playbook"}
    assert _fit_overlay_adjustment(role, _FIT_SPEC) == 2.0


def test_overlay_title_shape_screen_catches_gtm_in_disguise():
    """Same in-lane title, GTM scope -> screened DOWN below the clean version.

    This is the Context case: a 'Deployment Strategist' that is really a GTM seat.
    """
    clean = {"title": "Deployment Strategist",
             "description_plain": "scope pilots to production, forward-deployed"}
    gtm = {"title": "Deployment Strategist",
           "description_plain": "land and expand accounts, carry a quota, customer success"}
    assert _fit_overlay_adjustment(clean, _FIT_SPEC) == 2.0
    assert _fit_overlay_adjustment(gtm, _FIT_SPEC) == -3.0
    assert _fit_overlay_adjustment(gtm, _FIT_SPEC) < _fit_overlay_adjustment(clean, _FIT_SPEC)


def test_overlay_not_fit_title_downranks():
    role = {"title": "Chief of Staff to the CEO", "description_plain": "board prep, cadences"}
    assert _fit_overlay_adjustment(role, _FIT_SPEC) == -4.0


def test_overlay_clamped_to_bounds():
    """A role tripping multiple penalties is clamped, not unbounded."""
    role = {"title": "Chief of Staff, Business Operations",
            "description_plain": "own our cpq and deal desk platform"}
    adj = _fit_overlay_adjustment(role, _FIT_SPEC)
    assert adj == -5.0  # -4 (title) + -3 (domain) = -7, clamped to min -5.0


def test_real_fit_spec_loads_and_screens():
    """Integration: the real data/calibration/fit-spec.yaml loads and the
    title-shape screen fires on a GTM-in-disguise deployment role."""
    repo_root = Path(__file__).resolve().parents[2]
    ctx = load_scoring_context(repo_root)
    spec = ctx.get("fit_spec")
    assert spec and spec.get("positive_title_patterns")
    gtm = {"title": "Deployment Strategist",
           "description_plain": "land and expand, carry a quota, drive renewals"}
    real = {"title": "Deployment Strategist",
            "description_plain": "own enterprise deployments, build the playbook, forward-deployed"}
    assert score_role(gtm, ctx) < score_role(real, ctx)


# ---------------------------------------------------------------------------
# Seniority extraction must not match keywords inside prose or file paths
#
# Origin 2026-09-02. `_extract_target_seniority` fell through to scanning any
# blockquote line in goals.md for a seniority keyword, and matched "head" inside a
# blockquoted FILE PATH ("> Origin and full working: data/workbooks/..."). Target level
# resolved to 8. Because `extract_seniority` defaults an unkeyworded title to 4, the
# owner's own target title "Deployment Strategist" then scored 10 - |4-8|*2 = 2.0 on
# 25% of the weight, while "Regional Director, Forward Deployed Engineering" scored
# 10.0. The ranking was inverted against his lane.
# ---------------------------------------------------------------------------

def test_a_seniority_word_inside_a_blockquoted_path_is_not_a_target_level():
    """The regression: prose containing 'head' must not set target seniority."""
    from tools.career_scanner.scorer import _extract_target_seniority
    goals = (
        "## Search Thesis\n"
        "A deployment-strategist seat at an AI-native company.\n"
        "\n"
        "> Origin and full working: data/workbooks/head-of-lane-value-prop.md. Written\n"
        "> because the cold email took nine rounds and the argument was being invented.\n"
    )
    assert _extract_target_seniority(goals) == "", (
        "a seniority keyword appearing inside blockquoted prose or a file path was "
        "treated as an explicit target seniority"
    )


def test_an_explicit_target_seniority_line_is_still_honoured():
    """Guard on the guard: removing the fallback must not break the real path."""
    from tools.career_scanner.scorer import _extract_target_seniority
    assert _extract_target_seniority("**Target seniority:** Senior\n") == "Senior"


def test_target_level_falls_back_to_titles_not_to_prose():
    """With no explicit line, the level must come from the target TITLES.

    That inference path was added deliberately (fable-audit Theme 3) and is correct;
    the blockquote scan was shadowing it with noise.
    """
    from tools.career_scanner.scorer import _extract_target_seniority, _resolve_target_level
    goals = "> a blockquote mentioning head of something\n"
    assert _extract_target_seniority(goals) == ""
    lvl = _resolve_target_level("", ["Deployment Strategist", "Engagement Manager"])
    assert lvl is None or lvl <= 6, (
        f"target level {lvl} inferred from IC-shaped titles is too senior; an "
        "unkeyworded target title must not resolve to director/head level"
    )


# ---------------------------------------------------------------------------
# A dimension whose comparison terms are unusable must go NEUTRAL, not zero.
#
# Origin 2026-09-02. `_extract_skills` pulls whole human-readable bullets out of
# profile.md ("Strategic operations and planning (FY planning, OKRs, budget
# management)"). Those can never appear as a substring in a job posting, so
# `_score_keyword_overlap` matched nothing and returned 0.0 -- while a role with NO
# description at all returned 3.0. Having a job description actively lowered the score,
# on 20% of the total weight.
#
# The fix is not to invent matches. It is that an unusable input yields a neutral score
# and is reported as dark, rather than being scored as a real zero.
# ---------------------------------------------------------------------------

def test_a_real_description_never_scores_worse_than_a_missing_one():
    """The perverse asymmetry, stated as an invariant."""
    from tools.career_scanner.scorer import _score_keyword_overlap
    ctx = {"skills": [
        "Strategic operations and planning (FY planning, OKRs, budget management)",
        "Executive-level communication and stakeholder management (board-level deliverables)",
    ]}
    real_jd = (
        "6+ years in customer-facing roles. Strong technical acumen: workflows, APIs, "
        "system behavior. Familiarity with LLMs, APIs, JSON. Basic SQL literacy."
    )
    with_desc = _score_keyword_overlap({"description_plain": real_jd}, ctx)
    without_desc = _score_keyword_overlap({"description_plain": ""}, ctx)
    assert with_desc >= without_desc, (
        f"a real JD scored {with_desc} while an empty one scored {without_desc}; "
        "having a description must never be a penalty"
    )


def test_unusable_skill_terms_score_neutral_not_zero():
    """Long prose terms are unusable input, not evidence of a bad match."""
    from tools.career_scanner.scorer import _score_keyword_overlap
    ctx = {"skills": ["Strategic operations and planning (FY planning, OKRs, budget management)"]}
    score = _score_keyword_overlap({"description_plain": "Deployment Strategist, SQL, APIs"}, ctx)
    assert score >= 3.0, (
        f"unusable comparison terms produced a hard {score}; an input problem must not "
        "masquerade as a poor candidate match"
    )


def test_short_usable_terms_still_match_and_score():
    """Guard on the guard: the dimension must still WORK on usable terms, or the fix
    is just 'always return neutral', which is a dimension that does nothing."""
    from tools.career_scanner.scorer import _score_keyword_overlap
    ctx = {"skills": ["SQL", "APIs", "JSON", "Excel"]}
    score = _score_keyword_overlap(
        {"description_plain": "Basic SQL literacy. Familiarity with LLMs, APIs, JSON."}, ctx)
    assert score > 3.0, f"three usable terms matched but scored {score}"
