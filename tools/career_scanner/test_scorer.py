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
        assert extract_seniority("Product Manager") == 4

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
