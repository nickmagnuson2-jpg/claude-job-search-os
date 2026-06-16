"""Tests for company_scorer.py - deterministic company-fit scorer."""
import pytest
from tools.career_scanner.company_scorer import (
    geo_gate,
    parse_stage,
    _score_stage,
    _score_sector,
    _score_keyword,
    score_company,
    DEFAULT_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Stage 1: geo_gate tests
# ---------------------------------------------------------------------------

def test_geo_gate_sf_passes_clean():
    g = geo_gate("San Francisco, California, United States")
    assert g["pass"] is True
    assert g["excluded"] is False
    assert g["penalty"] == 0.0
    assert g["flag"] is False


def test_geo_gate_south_san_francisco_is_peninsula_not_sf():
    g = geo_gate("South San Francisco, CA")
    assert g["pass"] is True
    assert g["excluded"] is False
    assert g["penalty"] == 0.6
    assert g["flag"] is True


def test_geo_gate_san_mateo_penalized():
    g = geo_gate("San Mateo, CA")
    assert g["penalty"] == 0.6
    assert g["flag"] is True


def test_geo_gate_bay_area_light_penalty():
    g = geo_gate("Oakland, California")
    assert g["pass"] is True
    assert g["penalty"] == 0.2
    assert g["flag"] is True


def test_geo_gate_outside_bay_excluded():
    g = geo_gate("Austin, Texas")
    assert g["pass"] is False
    assert g["excluded"] is True


def test_geo_gate_unknown_location_not_excluded_but_flagged():
    g = geo_gate("")
    assert g["excluded"] is False
    assert g["flag"] is True


# ---------------------------------------------------------------------------
# Stage 2: parse_stage + _score_stage tests
# ---------------------------------------------------------------------------

def test_parse_stage_series_c():
    assert parse_stage("They raised a Series C in 2024") == 4


def test_parse_stage_handles_bare_letter_and_public():
    assert parse_stage("Series B") == 3
    assert parse_stage("publicly traded company") == 8
    assert parse_stage("no funding info here") is None


def test_score_stage_peaks_at_target_band():
    assert _score_stage(4) == 10.0
    assert _score_stage(3) == 7.5
    assert _score_stage(5) == 7.5
    assert _score_stage(1) <= 3.0
    assert _score_stage(8) == 1.0
    assert _score_stage(None) == 5.0


# ---------------------------------------------------------------------------
# Stage 3: _score_sector, _score_keyword, score_company tests
# ---------------------------------------------------------------------------

def test_score_sector_substring_hit():
    assert _score_sector("an applied AI platform for banks", ["applied AI"]) == 8.0


def test_score_sector_no_target_is_neutral():
    assert _score_sector("anything", []) == 5.0


def test_score_keyword_counts_matches_capped():
    kws = ["deployment", "enterprise", "banking", "onboarding"]
    text = "enterprise deployment for banking customers"
    assert _score_keyword(text, kws) == 6.0
    assert _score_keyword("nothing relevant", kws) == 0.0
    assert _score_keyword("anything", []) == 3.0


def test_score_company_sf_series_c_scores_high():
    candidate = {
        "location": "San Francisco, CA",
        "description": "Applied AI deployment platform for enterprise banking",
        "industry": "Artificial Intelligence",
        "stage_text": "Series C",
    }
    context = {"target_industries": ["applied AI", "AI-native infrastructure"]}
    keywords = ["deployment", "enterprise", "banking"]
    out = score_company(candidate, context, keywords=keywords)
    assert out["excluded"] is False
    assert out["score"] >= 8
    assert "stage" in out["breakdown"]


def test_score_company_outside_bay_excluded_floor():
    candidate = {"location": "Austin, TX", "description": "AI for enterprise",
                 "stage_text": "Series C"}
    out = score_company(candidate, {"target_industries": []}, keywords=[])
    assert out["excluded"] is True
    assert out["score"] == 1


def test_score_company_weights_are_configurable():
    candidate = {"location": "San Francisco, CA",
                 "description": "AI deployment", "stage_text": "Seed"}
    context = {"target_industries": []}
    # Two keyword hits ("ai" + "deployment") -> d_keyword=4.0 > d_stage=2.5 for Seed,
    # so pumping keyword weight must produce a higher score than pumping stage weight.
    kws = ["deployment", "ai"]
    stage_heavy = score_company(candidate, context, weights={"stage": 0.9, "sector": 0.05, "keyword": 0.05}, keywords=kws)
    kw_heavy = score_company(candidate, context, weights={"stage": 0.05, "sector": 0.05, "keyword": 0.9}, keywords=kws)
    assert kw_heavy["score"] > stage_heavy["score"]


def test_score_company_defaults_when_no_weights():
    candidate = {"location": "San Francisco, CA", "description": "AI", "stage_text": "Series C"}
    out = score_company(candidate, {"target_industries": []}, keywords=[])
    assert 1 <= out["score"] <= 10


# ---------------------------------------------------------------------------
# Regression: word-boundary matching (review finding #4)
# ---------------------------------------------------------------------------

def test_parse_stage_no_false_positive_on_public_sector():
    # "public sector" must NOT be read as a public/late-stage company.
    assert parse_stage("we serve public sector clients") is None


def test_parse_stage_seed_not_matched_inside_seeded():
    assert parse_stage("a well-seeded market") is None
    assert parse_stage("raised a seed round") == 1


def test_parse_stage_public_company_still_detected():
    assert parse_stage("now a public company on the nasdaq") == 8


def test_score_keyword_no_substring_false_positive():
    # "ai" must not match inside "maintenance".
    assert _score_keyword("maintenance contracts only", ["ai"]) == 0.0
    assert _score_keyword("an ai platform", ["ai"]) == 2.0
