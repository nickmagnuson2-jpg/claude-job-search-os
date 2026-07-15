"""Tests for tools/contact_finder.py - the Exa-based contact finder that retired
the Selenium LinkedIn scraper.

Covers the pure deterministic layer: profile-URL filtering, URL canonicalization
+ dedup, name-from-title extraction (the fabrication-guard surface), reach
parsing, company-mention routing, runtime token loading, and result normalization
+ bucketing. Exa I/O is exercised by the live run, not here.

PII gate: this file is public - all names/companies are placeholders.
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "tools" / "contact_finder.py"

spec = importlib.util.spec_from_file_location("contact_finder", MOD_PATH)
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)


# --------------------------------------------------------------------------- #
# is_profile_url
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/in/jane-doe-12345", True),
    ("https://linkedin.com/in/jsmith", True),
    ("https://de.linkedin.com/in/hans-müller", True),
    ("https://www.linkedin.com/posts/jane-doe_activity-123", False),
    ("https://www.linkedin.com/company/acme-ai", False),
    ("https://www.linkedin.com/school/some-university", False),
    ("", False),
    (None, False),
])
def test_is_profile_url(url, expected):
    assert cf.is_profile_url(url) is expected


# --------------------------------------------------------------------------- #
# canonicalize_url + slug_from_url + dedup semantics
# --------------------------------------------------------------------------- #
def test_canonicalize_strips_params_and_country_subdomain():
    a = cf.canonicalize_url("https://www.linkedin.com/in/jane-doe-9b67?trk=abc&x=1")
    b = cf.canonicalize_url("https://de.linkedin.com/in/jane-doe-9b67/")
    c = cf.canonicalize_url("http://LINKEDIN.com/in/jane-doe-9b67#exp")
    assert a == b == c == "linkedin.com/in/jane-doe-9b67"


def test_slug_trims_trailing_id():
    assert cf.slug_from_url("https://www.linkedin.com/in/jane-doe-26920336") == "jane doe"
    assert cf.slug_from_url("https://www.linkedin.com/in/jsmith") == "jsmith"


# --------------------------------------------------------------------------- #
# extract_name - the fabrication-guard surface
# --------------------------------------------------------------------------- #
def test_extract_name_plain():
    assert cf.extract_name("Jane Doe", "https://www.linkedin.com/in/jane-doe-123") == "Jane Doe"


def test_extract_name_with_headline_and_linkedin_suffix():
    got = cf.extract_name("Jane Doe - Head of Ops at Acme | LinkedIn",
                          "https://www.linkedin.com/in/jane-doe-123")
    assert got == "Jane Doe"


def test_extract_name_all_caps_titlecased():
    assert cf.extract_name("JANE DOE", "https://www.linkedin.com/in/jane-doe-9") == "Jane Doe"


def test_extract_name_post_prefix_stripped():
    got = cf.extract_name("Post by John Roe", "https://www.linkedin.com/in/john-roe-5")
    assert got == "John Roe"


def test_extract_name_rejects_linkedin_member():
    assert cf.extract_name("LinkedIn Member", "https://www.linkedin.com/in/abc") is None


def test_extract_name_rejects_slug_mismatch():
    # Title name shares no token with the URL slug -> untrustworthy -> drop.
    assert cf.extract_name("Marketing Team", "https://www.linkedin.com/in/jane-doe-123") is None


def test_extract_name_rejects_single_token():
    assert cf.extract_name("Jane", "https://www.linkedin.com/in/jane-doe-1") is None


def test_extract_name_empty():
    assert cf.extract_name("", "https://www.linkedin.com/in/x") is None


# --------------------------------------------------------------------------- #
# parse_reach
# --------------------------------------------------------------------------- #
def test_parse_reach_connections_and_followers():
    got = cf.parse_reach("San Francisco Bay Area  500 connections • 4,011 followers  About")
    assert got == "500 connections, 4,011 followers"


def test_parse_reach_none():
    assert cf.parse_reach("No numbers about the network here.") == ""


# --------------------------------------------------------------------------- #
# company_mentioned - routes candidate vs needs_check
# --------------------------------------------------------------------------- #
def test_company_mentioned_true():
    assert cf.company_mentioned("Head of Operations at Acme AI since 2024", "Acme AI") is True


def test_company_mentioned_current_marker():
    assert cf.company_mentioned("Deployment Strategist - Acme (Current) Jun 2026", "Acme") is True


def test_company_mentioned_false():
    assert cf.company_mentioned("Product manager at some other place", "Acme AI") is False


def test_company_mentioned_ignores_short_tokens():
    # "AI" alone (2 chars) shouldn't cause spurious matches; "acme" must be present.
    assert cf.company_mentioned("I work in AI research broadly", "Acme AI") is False


def test_company_mentioned_rejects_firstname_collision():
    # A person merely NAMED like the company is not a work-context match.
    assert cf.company_mentioned("Acme Johnson is a student at Example University", "Acme") is False


def test_company_mentioned_rejects_different_company_same_prefix():
    # "Acme Sciences" (a different company) must not match target "Acme".
    assert cf.company_mentioned("Founder & CEO, Acme Sciences | molecular biologist", "Acme") is False


# --------------------------------------------------------------------------- #
# runtime token loaders (temp fixtures - never the real profile)
# --------------------------------------------------------------------------- #
def test_load_role_tokens(tmp_path):
    goals = tmp_path / "goals.md"
    goals.write_text(
        "## Target Criteria\n\n**Role types** (ranked):\n"
        "1. Deployment Strategist / Engagement Lead at an AI-native company (the seat)\n"
        "2. Business Operations on a strong team\n",
        encoding="utf-8",
    )
    got = cf.load_role_tokens(goals)
    assert "Deployment Strategist" in got
    assert "Engagement Lead" in got
    assert "AI-native" not in got  # cut at "at an"
    assert "(" not in got


def test_load_role_tokens_missing_file(tmp_path):
    assert cf.load_role_tokens(tmp_path / "nope.md") == ""


def test_load_affinity_tokens(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text(
        "### Career Background\n"
        "- **Globex Consulting:** management consultant\n"
        "- **Initech:** operations lead\n"
        "- **Education:** MBA, Example School of Business at Placeholder University; "
        "Bachelor's, Public Policy, Sample College\n",
        encoding="utf-8",
    )
    got = cf.load_affinity_tokens(profile)
    assert "Globex" in got
    assert "Initech" in got
    assert "Placeholder" in got or "Sample" in got
    # Generic education words must not leak in as affinity tokens.
    assert "Public" not in got and "Bachelor" not in got
    assert "Education" not in got and "School" not in got


def test_load_affinity_tokens_ignores_header_field_labels(tmp_path):
    # Regression: the extractor must NOT scoop up profile header/comp field
    # labels (E-Mail, Salary, Flexibility, Equity, English) as "affinities".
    profile = tmp_path / "profile.md"
    profile.write_text(
        "# Profile\n"
        "- **E-Mail:** someone@example.com\n"
        "- **LinkedIn:** https://linkedin.com/in/x\n"
        "## Compensation\n- **Salary expectation:** $x\n- **Flexibility:** yes\n- **Equity:** open\n"
        "## Languages\n- **English:** Native\n"
        "### Career Background\n"
        "- **Globex Consulting:** management consultant\n"
        "- **Initech:** operations lead\n"
        "- **Education:** MBA, Example School at Placeholder University\n",
        encoding="utf-8",
    )
    got = cf.load_affinity_tokens(profile)
    for junk in ("E-Mail", "Salary", "Flexibility", "Equity", "English", "LinkedIn"):
        assert junk not in got
    assert "Globex" in got and "Initech" in got


def test_load_affinity_tokens_missing_file(tmp_path):
    assert cf.load_affinity_tokens(tmp_path / "nope.md") == []


# --------------------------------------------------------------------------- #
# build_queries
# --------------------------------------------------------------------------- #
def test_build_queries_all_layers():
    qs = cf.build_queries("Acme AI", "Deployment Strategist", ["Globex", "Initech"])
    assert len(qs) == 3
    assert all(q.startswith("Acme AI") for q in qs)
    assert any("Deployment Strategist" in q for q in qs)
    assert any("Globex" in q for q in qs)


def test_build_queries_degrades_without_tokens():
    qs = cf.build_queries("Acme AI", "", [])
    assert len(qs) == 1
    assert qs[0].startswith("Acme AI")


# --------------------------------------------------------------------------- #
# normalize_result - end-to-end bucketing
# --------------------------------------------------------------------------- #
def test_normalize_candidate_bucket():
    raw = {
        "title": "Jane Doe - Head of Ops at Acme | LinkedIn",
        "url": "https://www.linkedin.com/in/jane-doe-123?trk=x",
        "text": "Head of Operations at Acme AI. 500 connections. Leading deployment.",
    }
    rec = cf.normalize_result(raw, "Acme AI")
    assert rec["name"] == "Jane Doe"
    assert rec["canonical_url"] == "linkedin.com/in/jane-doe-123"
    assert rec["url"] == "https://www.linkedin.com/in/jane-doe-123"
    assert rec["bucket"] == "candidate"
    assert "500 connections" in rec["reach"]
    assert rec["source_url"].startswith("https://")


def test_normalize_needs_check_when_company_absent():
    raw = {
        "title": "John Roe - Engineer | LinkedIn",
        "url": "https://www.linkedin.com/in/john-roe-9",
        "text": "Software engineer. Interested in distributed systems.",
    }
    rec = cf.normalize_result(raw, "Acme AI")
    assert rec["bucket"] == "needs_check"


def test_normalize_drops_non_profile():
    raw = {"title": "Acme AI", "url": "https://www.linkedin.com/company/acme-ai", "text": "x"}
    assert cf.normalize_result(raw, "Acme AI") is None


def test_normalize_drops_unparseable_name():
    raw = {"title": "LinkedIn Member", "url": "https://www.linkedin.com/in/abc", "text": "Acme AI"}
    assert cf.normalize_result(raw, "Acme AI") is None
