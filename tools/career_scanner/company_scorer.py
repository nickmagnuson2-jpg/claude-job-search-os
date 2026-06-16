"""company_scorer.py - deterministic company-fit scorer for /discover-companies.

Scores a discovered company 1-10 against goals.md. Geography is a hard GATE
applied before the weighted dimensions (per the goals.md SF hard filter:
SF the city in-person/hybrid; not Peninsula/South-Bay commute; not outside Bay).

Weighted dimensions (geo-valid companies only), defaults configurable via
discover-presets.yaml scoring_weights:
  stage   (0.50) - funding stage vs target band (Series B/C/D preferred)
  sector  (0.30) - industry/description vs target_industries from goals.md
  keyword (0.20) - lane keywords present in description (tiebreaker)
"""
import re
from difflib import SequenceMatcher

DEFAULT_WEIGHTS = {"stage": 0.50, "sector": 0.30, "keyword": 0.20}

# Peninsula / South Bay commute towns (checked BEFORE the "san francisco"
# substring so "South San Francisco" is correctly treated as Peninsula).
_PENINSULA_SOUTHBAY = [
    "south san francisco", "san mateo", "redwood city", "mountain view",
    "palo alto", "menlo park", "sunnyvale", "santa clara", "san jose",
    "cupertino", "foster city", "burlingame", "daly city", "millbrae",
]
_BAY_TERMS = ["bay area", "oakland", "berkeley", "alameda", "emeryville"]


def geo_gate(location: str) -> dict:
    """Hard geography gate.

    Returns dict: pass (bool), penalty (0-1 multiplier subtracted),
    flag (bool, surface for review), excluded (bool, floor score to 1).
    """
    loc = (location or "").strip().lower()
    if not loc:
        # Unknown location: don't exclude, but flag for human review.
        return {"pass": True, "penalty": 0.0, "flag": True, "excluded": False}
    if any(t in loc for t in _PENINSULA_SOUTHBAY):
        return {"pass": True, "penalty": 0.6, "flag": True, "excluded": False}
    if "san francisco" in loc:
        return {"pass": True, "penalty": 0.0, "flag": False, "excluded": False}
    if any(t in loc for t in _BAY_TERMS):
        return {"pass": True, "penalty": 0.2, "flag": True, "excluded": False}
    return {"pass": False, "penalty": 1.0, "flag": True, "excluded": True}


# Funding stage ladder. Target band is Series B/C/D; center = Series C (4).
# Note: no bare "public" key - it false-matches "public sector" in the
# description fallback path; use "public company"/"publicly traded"/ipo/exchange.
STAGE_LEVELS = {
    "pre-seed": 0, "preseed": 0,
    "seed": 1,
    "series a": 2,
    "series b": 3,
    "series c": 4,
    "series d": 5,
    "series e": 6,
    "public company": 8, "publicly traded": 8, "ipo": 8, "acquired": 8,
    "nasdaq": 8, "nyse": 8,
}
_TARGET_STAGE = 4  # Series C


def _word_match(token: str, text: str) -> bool:
    """Whole-word (boundary-anchored) membership test, so 'ai' does not match
    'maintenance' and 'seed' does not match 'seeded'."""
    return re.search(r"\b" + re.escape(token) + r"\b", text) is not None


def parse_stage(text: str):
    """Return an integer stage level from free text, or None if not found.

    Longest keys first so 'series c' wins over a stray 'c'. Word-boundary
    matched so 'seed' does not fire on 'seeded'.
    """
    t = (text or "").lower()
    for key in sorted(STAGE_LEVELS, key=len, reverse=True):
        if _word_match(key, t):
            return STAGE_LEVELS[key]
    return None


def _score_stage(stage_level) -> float:
    """Distance-penalized score around the target band. Unknown -> neutral 5."""
    if stage_level is None:
        return 5.0
    raw = 10.0 - abs(stage_level - _TARGET_STAGE) * 2.5
    return max(1.0, min(10.0, raw))


def _clean_industry_term(term: str) -> str:
    """Strip parenthetical qualifiers and surrounding punctuation."""
    return re.sub(r"\s*\(.*?\)\s*", " ", term).strip().lower()


def _score_sector(text: str, target_industries: list) -> float:
    """Dimension: sector/lane fit. Substring hit on any target industry -> 8,
    else a weak fuzzy fallback, else neutral when no targets configured."""
    if not target_industries:
        return 5.0
    t = (text or "").lower()
    if not t:
        return 3.0
    for ind in target_industries:
        term = _clean_industry_term(ind)
        if term and term in t:
            return 8.0
    best = 0.0
    for ind in target_industries:
        term = _clean_industry_term(ind)
        if term:
            best = max(best, SequenceMatcher(None, t[:300], term).ratio())
    return max(3.0, best * 10.0)


def _score_keyword(text: str, keywords: list) -> float:
    """Dimension: lane keyword overlap. Count distinct keyword hits, * 2, cap 10."""
    if not keywords:
        return 3.0
    t = (text or "").lower()
    matched = sum(1 for k in keywords if _word_match(k.lower(), t))
    return min(10.0, float(matched * 2))


def score_company(candidate: dict, context: dict,
                  weights: dict = None, keywords: list = None) -> dict:
    """Score a discovered company 1-10.

    candidate keys used: location, description, industry, about, stage_text.
    context: output of scorer.load_scoring_context (uses target_industries).
    Returns: {score, breakdown, geo_flag, excluded}.
    """
    weights = weights or DEFAULT_WEIGHTS
    keywords = keywords or []

    gate = geo_gate(candidate.get("location") or "")
    if gate["excluded"]:
        return {"score": 1, "breakdown": {"geo": "excluded"},
                "geo_flag": True, "excluded": True}

    desc = " ".join(filter(None, [
        candidate.get("description", ""),
        candidate.get("industry", ""),
        candidate.get("about", ""),
    ]))
    stage_text = candidate.get("stage_text") or desc

    d_stage = _score_stage(parse_stage(stage_text))
    d_sector = _score_sector(desc, context.get("target_industries", []))
    d_keyword = _score_keyword(desc, keywords)

    raw = (weights["stage"] * d_stage
           + weights["sector"] * d_sector
           + weights["keyword"] * d_keyword)
    raw *= (1.0 - gate["penalty"])
    score = max(1, min(10, round(raw)))

    return {
        "score": score,
        "breakdown": {
            "stage": round(d_stage, 1),
            "sector": round(d_sector, 1),
            "keyword": round(d_keyword, 1),
            "geo_penalty": gate["penalty"],
        },
        "geo_flag": gate["flag"],
        "excluded": False,
    }
