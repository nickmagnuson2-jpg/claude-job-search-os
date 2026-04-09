"""Tests for the career scanner deduplication module."""
import pytest

from tools.career_scanner.dedup import (
    filter_duplicates,
    is_duplicate,
    load_pipeline_entries,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_pipeline():
    """Pipeline entries mimicking pipe_read.py active_entries output."""
    return [
        {"company": "Ramp", "role": "Senior Product Manager"},
        {"company": "Discord", "role": "Head of Operations"},
        {"company": "Stripe", "role": "Chief of Staff"},
    ]


# ---------------------------------------------------------------------------
# is_duplicate: exact match
# ---------------------------------------------------------------------------

class TestIsDuplicateExact:
    def test_exact_company_and_title(self, sample_pipeline):
        assert is_duplicate("Senior Product Manager", "Ramp", sample_pipeline) is True

    def test_exact_company_and_title_different_case(self, sample_pipeline):
        assert is_duplicate("senior product manager", "ramp", sample_pipeline) is True


# ---------------------------------------------------------------------------
# is_duplicate: fuzzy title match
# ---------------------------------------------------------------------------

class TestIsDuplicateFuzzy:
    def test_fuzzy_title_above_threshold(self, sample_pipeline):
        # "Sr Product Manager" vs "Senior Product Manager" should be >= 0.80
        assert is_duplicate("Sr. Product Manager", "Ramp", sample_pipeline) is True

    def test_fuzzy_title_below_threshold(self, sample_pipeline):
        # Completely different title at same company
        assert is_duplicate("Junior Frontend Developer", "Ramp", sample_pipeline) is False

    def test_very_similar_title(self, sample_pipeline):
        # Minor variation
        assert is_duplicate("Head of Ops", "Discord", sample_pipeline) is True


# ---------------------------------------------------------------------------
# is_duplicate: company matching
# ---------------------------------------------------------------------------

class TestIsDuplicateCompany:
    def test_different_company_same_title(self, sample_pipeline):
        # Same title but different company = not duplicate
        assert is_duplicate("Senior Product Manager", "Google", sample_pipeline) is False

    def test_company_case_insensitive(self, sample_pipeline):
        assert is_duplicate("Chief of Staff", "STRIPE", sample_pipeline) is True

    def test_company_whitespace_tolerance(self, sample_pipeline):
        assert is_duplicate("Chief of Staff", " Stripe ", sample_pipeline) is True


# ---------------------------------------------------------------------------
# is_duplicate: edge cases
# ---------------------------------------------------------------------------

class TestIsDuplicateEdgeCases:
    def test_empty_pipeline(self):
        assert is_duplicate("Any Role", "Any Company", []) is False

    def test_empty_company(self, sample_pipeline):
        assert is_duplicate("Senior Product Manager", "", sample_pipeline) is False

    def test_empty_title(self, sample_pipeline):
        # Empty title won't fuzzy match above 0.80
        assert is_duplicate("", "Ramp", sample_pipeline) is False


# ---------------------------------------------------------------------------
# filter_duplicates
# ---------------------------------------------------------------------------

class TestFilterDuplicates:
    def test_filters_known_duplicates(self, sample_pipeline):
        roles = [
            {"title": "Senior Product Manager", "company": "Ramp"},
            {"title": "New Role", "company": "NewCo"},
            {"title": "Chief of Staff", "company": "Stripe"},
        ]
        new_roles, skipped = filter_duplicates(roles, sample_pipeline)
        assert skipped == 2
        assert len(new_roles) == 1
        assert new_roles[0]["company"] == "NewCo"

    def test_empty_roles_list(self, sample_pipeline):
        new_roles, skipped = filter_duplicates([], sample_pipeline)
        assert new_roles == []
        assert skipped == 0

    def test_no_duplicates_found(self):
        roles = [
            {"title": "Designer", "company": "DesignCo"},
        ]
        new_roles, skipped = filter_duplicates(roles, [])
        assert len(new_roles) == 1
        assert skipped == 0


# ---------------------------------------------------------------------------
# load_pipeline_entries
# ---------------------------------------------------------------------------

class TestLoadPipelineEntries:
    def test_returns_list(self, tmp_path):
        """load_pipeline_entries should return a list (may be empty if no pipeline)."""
        result = load_pipeline_entries(tmp_path)
        assert isinstance(result, list)

    def test_graceful_on_missing_pipe_read(self, tmp_path):
        """If pipe_read.py doesn't exist at the expected path, return empty list."""
        result = load_pipeline_entries(tmp_path)
        assert result == []
