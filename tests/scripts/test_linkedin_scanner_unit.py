"""Unit tests for linkedin-scanner pure functions.

Covers llm.py functions and scan.py's build_output_record().
No network calls, no filesystem writes, no browser.

anthropic is not installed in the test environment — it is mocked in
sys.modules before any scanner code is imported.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Mocks and path setup must precede all scanner imports ───────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = REPO_ROOT / "tools" / "linkedin-scanner"

# anthropic is not installed in the test env — mock it so llm.py can import
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = MagicMock()

# scan.py imports Scraper and Ranker at module level; mock them so the module
# loads without launching a browser or needing the full dependency chain
if "Scraper" not in sys.modules:
    sys.modules["Scraper"] = MagicMock()
if "Ranker" not in sys.modules:
    sys.modules["Ranker"] = MagicMock()

# Add scanner root so `import src.X` and `import scan` resolve correctly
if str(SCANNER_DIR) not in sys.path:
    sys.path.insert(0, str(SCANNER_DIR))

# Provide a dummy key so llm.py's module-level Anthropic() construction succeeds
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-unit-tests")

import src.llm as llm  # noqa: E402
import scan            # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────


# ── post_process ──────────────────────────────────────────────────────────────

def test_post_process_aggregate_rating():
    """Four sub-scores should sum to aggregate_rating."""
    rank = {"role_proximity": 8, "education": 7, "connectedness": 6, "industry_fit": 5}
    llm.post_process(rank)
    assert rank["aggregate_rating"] == 26


def test_post_process_missing_fields_default_zero():
    """Missing sub-score keys default to 0 via .get(), not a KeyError."""
    rank = {}
    llm.post_process(rank)
    assert rank["aggregate_rating"] == 0


def test_post_process_partial_fields():
    """Only present keys contribute; absent keys are treated as 0."""
    rank = {"role_proximity": 9, "education": 8}
    llm.post_process(rank)
    assert rank["aggregate_rating"] == 17


# ── pre_process ───────────────────────────────────────────────────────────────

def test_pre_process_removes_company_url():
    """company_url is stripped from each experience entry before LLM ranking."""
    profile = {
        "experience": [
            {
                "position": "CEO",
                "company_url": "https://linkedin.com/company/acme",
                "company": {"name": "Acme"},
            }
        ]
    }
    llm.pre_process(profile)
    assert "company_url" not in profile["experience"][0]


def test_pre_process_no_experience_key():
    """Profiles without an experience key must not raise."""
    profile = {"name": "Jane Smith"}
    llm.pre_process(profile)  # should be a no-op
    assert profile == {"name": "Jane Smith"}


# ── rank_profile / unrank_profile ─────────────────────────────────────────────

def test_rank_profile_adds_metadata():
    """rank_profile() must embed url, rank, and metadata into the profile dict."""
    profile = {"name": "Test User"}
    rank = {"aggregate_rating": 20}
    url = "https://linkedin.com/in/testuser"
    llm.rank_profile(profile, url, rank)
    assert profile["url"] == url
    assert profile["rank"] == rank
    assert "metadata" in profile
    assert "rank_prompt_id" in profile["metadata"]
    assert "date_ranked" in profile["metadata"]


def test_unrank_profile_removes_metadata():
    """unrank_profile() must remove url, rank, and metadata added by rank_profile()."""
    profile = {"name": "Test User"}
    rank = {"aggregate_rating": 20}
    url = "https://linkedin.com/in/testuser"
    llm.rank_profile(profile, url, rank)
    llm.unrank_profile(profile)
    assert "url" not in profile
    assert "rank" not in profile
    assert "metadata" not in profile


# ── id_for_string ─────────────────────────────────────────────────────────────

def test_id_for_string_deterministic():
    """Same input string must always produce the same UUID."""
    assert llm.id_for_string("hello world") == llm.id_for_string("hello world")


def test_id_for_string_different_inputs():
    """Different input strings must produce different UUIDs."""
    assert llm.id_for_string("hello world") != llm.id_for_string("goodbye world")


# ── build_output_record ───────────────────────────────────────────────────────

def test_build_output_record_with_valid_profile():
    """Valid profile dict → output record has name, url, headline, rank fields."""
    profile = {
        "name": "Jane Smith",
        "headline": "CEO at Acme",
        "location": "San Francisco, CA",
        "degree": "2",
        "mutuals": 5,
        "rank": {
            "role_proximity": 9,
            "education": 8,
            "connectedness": 7,
            "industry_fit": 6,
            "aggregate_rating": 30,
            "overall_rating": 9,
        },
    }
    record = scan.build_output_record(
        "Jane Smith", "https://linkedin.com/in/jane", profile, 30
    )
    assert record["name"] == "Jane Smith"
    assert record["url"] == "https://linkedin.com/in/jane"
    assert record["headline"] == "CEO at Acme"
    assert "rank" in record
    assert "error" not in record


def test_build_output_record_none_profile():
    """profile=None → error record with no rank field; score becomes error message."""
    record = scan.build_output_record(
        "Unknown", "https://linkedin.com/in/unknown", None, "No LinkedIn URL"
    )
    assert "error" in record
    assert record["error"] == "No LinkedIn URL"
    assert "rank" not in record


def test_build_output_record_missing_optional_fields():
    """Missing headline/location in profile → defaults to empty string, not KeyError."""
    profile = {"name": "Bob", "rank": {}}
    record = scan.build_output_record(
        "Bob", "https://linkedin.com/in/bob", profile, 0
    )
    assert record["headline"] == ""
    assert record["location"] == ""


def test_build_output_record_rank_fields():
    """All six rank sub-scores map correctly from profile.rank to output record."""
    profile = {
        "name": "Alice",
        "rank": {
            "role_proximity": 10,
            "education": 9,
            "connectedness": 8,
            "industry_fit": 7,
            "aggregate_rating": 34,
            "overall_rating": 9,
        },
    }
    record = scan.build_output_record(
        "Alice", "https://linkedin.com/in/alice", profile, 34
    )
    r = record["rank"]
    assert r["role_proximity"] == 10
    assert r["education"] == 9
    assert r["connectedness"] == 8
    assert r["industry_fit"] == 7
    assert r["aggregate_rating"] == 34
    assert r["overall_rating"] == 9
