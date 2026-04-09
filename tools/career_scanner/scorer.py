"""
scorer.py - Four-dimension role scoring engine for career scanner.

Scores discovered roles 1-10 against the user's profile.md and goals.md.

Dimensions (weighted):
  1. Title match (35%) - fuzzy match against target titles in goals.md
  2. Seniority match (25%) - level comparison from title keywords
  3. Industry/domain match (20%) - department/team vs target industries
  4. Keyword overlap (20%) - skills from profile.md vs description

Per D-05: All roles get scores, no filtering threshold.
"""
import re
from difflib import SequenceMatcher
from pathlib import Path


# ---------------------------------------------------------------------------
# Seniority keyword map (from RESEARCH.md)
# ---------------------------------------------------------------------------

SENIORITY_LEVELS: dict[str, int] = {
    "intern": 1,
    "junior": 2,
    "associate": 3,
    "mid": 4,
    "senior": 5,
    "staff": 6,
    "principal": 7,
    "lead": 7,
    "director": 8,
    "head": 8,
    "vp": 9,
    "vice president": 9,
    "chief": 10,
    "c-suite": 10,
}

# Weights per D-04
_W_TITLE = 0.35
_W_SENIORITY = 0.25
_W_INDUSTRY = 0.20
_W_KEYWORD = 0.20


# ---------------------------------------------------------------------------
# Context loading
# ---------------------------------------------------------------------------

def load_scoring_context(repo_root: Path) -> dict:
    """Extract scoring context from profile.md and goals.md.

    Returns dict with keys: target_titles, target_seniority, target_industries, skills.
    Gracefully returns empty values if files are missing.
    """
    data_dir = repo_root / "data"
    result = {
        "target_titles": [],
        "target_seniority": "",
        "target_industries": [],
        "skills": [],
    }

    # Parse goals.md
    goals_path = data_dir / "goals.md"
    if goals_path.exists():
        goals_text = goals_path.read_text(encoding="utf-8")
        result["target_titles"] = _extract_target_titles(goals_text)
        result["target_seniority"] = _extract_target_seniority(goals_text)
        result["target_industries"] = _extract_target_industries(goals_text)

    # Parse profile.md
    profile_path = data_dir / "profile.md"
    if profile_path.exists():
        profile_text = profile_path.read_text(encoding="utf-8")
        result["skills"] = _extract_skills(profile_text)

    return result


def _extract_target_titles(text: str) -> list[str]:
    """Extract target role titles from goals.md.

    Looks for numbered list items under 'Role types' or 'Target Criteria' sections.
    Patterns: '1. Chief of Staff', '2. Head of Operations (remote ok)', etc.
    """
    titles = []
    in_role_section = False
    for line in text.splitlines():
        lower = line.lower().strip()
        # Detect role types section
        if "role type" in lower or "target role" in lower:
            in_role_section = True
            continue
        # End section on next heading or blank line after collecting
        if in_role_section and (line.startswith("**") and ":" in line and "role" not in lower):
            in_role_section = False
            continue
        if in_role_section and line.startswith("#"):
            in_role_section = False
            continue
        if in_role_section:
            # Match numbered list: '1. Chief of Staff' or '- Chief of Staff'
            m = re.match(r"^\s*(?:\d+\.|[-*])\s+(.+)", line)
            if m:
                title = m.group(1).strip()
                # Remove parenthetical qualifiers
                title = re.sub(r"\s*\(.*?\)\s*$", "", title)
                # Skip TODO placeholders
                if title and not title.upper().startswith("TODO"):
                    titles.append(title)
    return titles


def _extract_target_seniority(text: str) -> str:
    """Extract target seniority from goals.md.

    Looks for 'Target seniority:' line or infers from search thesis.
    """
    for line in text.splitlines():
        lower = line.lower().strip()
        if "target seniority" in lower:
            # 'Target seniority: Senior' or '**Target seniority:** Senior'
            m = re.search(r"(?:target seniority)[:\s*]+\s*(\w+)", lower)
            if m:
                return m.group(1).capitalize()
    # Fallback: look for seniority keywords in the search thesis
    for line in text.splitlines():
        if line.startswith(">"):
            for keyword in SENIORITY_LEVELS:
                if keyword in line.lower():
                    return keyword.capitalize()
    return ""


def _extract_target_industries(text: str) -> list[str]:
    """Extract target industries from goals.md.

    Looks for list items under 'Industries' heading.
    """
    industries = []
    in_section = False
    for line in text.splitlines():
        lower = line.lower().strip()
        if "industr" in lower and ("**" in line or line.startswith("#")):
            in_section = True
            continue
        if in_section and (line.startswith("#") or (line.startswith("**") and ":" in line and "industr" not in lower)):
            in_section = False
            continue
        if in_section:
            m = re.match(r"^\s*[-*]\s+(.+)", line)
            if m:
                val = m.group(1).strip()
                if val and not val.upper().startswith("TODO"):
                    industries.append(val)
    return industries


def _extract_skills(text: str) -> list[str]:
    """Extract skills from profile.md.

    Looks for bullet points under 'Skills' or 'Technologies' sections.
    """
    skills = []
    in_section = False
    for line in text.splitlines():
        lower = line.lower().strip()
        if ("skill" in lower or "technolog" in lower) and (line.startswith("#") or line.startswith("**")):
            in_section = True
            continue
        if in_section and line.startswith("#"):
            in_section = False
            continue
        if in_section:
            m = re.match(r"^\s*[-*]\s+(.+)", line)
            if m:
                val = m.group(1).strip()
                if val and not val.upper().startswith("TODO"):
                    skills.append(val)
    return skills


# ---------------------------------------------------------------------------
# Seniority extraction
# ---------------------------------------------------------------------------

def extract_seniority(title: str) -> int:
    """Extract seniority level from a job title.

    Returns integer 1-10 based on SENIORITY_LEVELS map.
    Defaults to 4 (mid-level) if no seniority keyword found.
    """
    title_lower = title.lower()
    # Check multi-word keys first (e.g., 'vice president', 'c-suite')
    for keyword in sorted(SENIORITY_LEVELS.keys(), key=len, reverse=True):
        if keyword in title_lower:
            return SENIORITY_LEVELS[keyword]
    return 4  # default: mid


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_role(role: dict, context: dict) -> int:
    """Score a role on 4 weighted dimensions, returning an integer 1-10.

    Dimensions:
      1. Title match (0.35) - fuzzy match against target titles
      2. Seniority match (0.25) - level distance
      3. Industry/domain match (0.20) - department/team vs target industries
      4. Keyword overlap (0.20) - skills in description

    Args:
        role: Standardized role dict (see __init__.py schema)
        context: Output of load_scoring_context()

    Returns:
        Integer fit score 1-10
    """
    d1 = _score_title_match(role, context)
    d2 = _score_seniority_match(role, context)
    d3 = _score_industry_match(role, context)
    d4 = _score_keyword_overlap(role, context)

    raw = _W_TITLE * d1 + _W_SENIORITY * d2 + _W_INDUSTRY * d3 + _W_KEYWORD * d4
    return max(1, min(10, round(raw)))


def _score_title_match(role: dict, context: dict) -> float:
    """Dimension 1: Title match (weight 0.35).

    Best fuzzy match ratio against all target titles, scaled to 0-10.
    """
    target_titles = context.get("target_titles", [])
    if not target_titles:
        return 5.0

    role_title = role.get("title", "").lower()
    best_ratio = max(
        SequenceMatcher(None, role_title, t.lower()).ratio()
        for t in target_titles
    )
    return best_ratio * 10.0


def _score_seniority_match(role: dict, context: dict) -> float:
    """Dimension 2: Seniority match (weight 0.25).

    Score = 10 - abs(role_level - target_level) * 2, clamped 1-10.
    """
    target_seniority = context.get("target_seniority", "")
    if not target_seniority:
        return 5.0

    role_level = extract_seniority(role.get("title", ""))
    # Get target level from seniority map
    target_level = SENIORITY_LEVELS.get(target_seniority.lower(), 4)

    raw = 10 - abs(role_level - target_level) * 2
    return max(1.0, min(10.0, float(raw)))


def _score_industry_match(role: dict, context: dict) -> float:
    """Dimension 3: Industry/domain match (weight 0.20).

    Fuzzy match role department/team against target industries.
    Score 8 if any match (ratio > 0.6), else 3.
    """
    target_industries = context.get("target_industries", [])
    if not target_industries:
        return 5.0

    role_fields = [
        role.get("department", "").lower(),
        role.get("team", "").lower(),
    ]
    role_fields = [f for f in role_fields if f]

    for field in role_fields:
        for industry in target_industries:
            ratio = SequenceMatcher(None, field, industry.lower()).ratio()
            if ratio > 0.6:
                return 8.0

    return 3.0


def _score_keyword_overlap(role: dict, context: dict) -> float:
    """Dimension 4: Keyword overlap (weight 0.20).

    Count how many skills appear as case-insensitive substrings in description.
    Score = min(10, matched_count * 2).
    """
    skills = context.get("skills", [])
    description = role.get("description_plain", "")
    if not skills or not description:
        return 3.0

    description_lower = description.lower()
    matched = sum(1 for s in skills if s.lower() in description_lower)
    return min(10.0, float(matched * 2))
