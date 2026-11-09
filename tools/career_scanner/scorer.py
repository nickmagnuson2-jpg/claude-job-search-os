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
import statistics
from datetime import date
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
    "manager": 5,
    "senior": 5,
    "staff": 6,
    "principal": 7,
    "lead": 7,
    "leader": 7,
    "director": 8,
    "head": 8,
    "vp": 9,
    "svp": 9,
    "evp": 9,
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

def load_scoring_context(repo_root: Path, today: date | None = None) -> dict:
    """Extract scoring context from profile.md and goals.md.

    Returns dict with keys: target_titles, target_seniority, target_industries, skills,
    and goals_last_reviewed / goals_age_days / goals_stale (goals_freshness against
    `today`, default the current date; a missing goals.md is stale).
    Gracefully returns empty values if files are missing.
    """
    data_dir = repo_root / "data"
    result = {
        "target_titles": [],
        "target_seniority": "",
        "target_seniority_level": None,
        "target_industries": [],
        "skills": [],
    }
    result.update(goals_freshness("", today or date.today()))

    # Parse goals.md
    goals_path = data_dir / "goals.md"
    goals_text = _read_text(goals_path)
    if goals_text is not None:
        result.update(goals_freshness(goals_text, today or date.today()))
        result["target_titles"] = _extract_target_titles(goals_text)
        result["target_seniority"] = _extract_target_seniority(goals_text)
        result["target_industries"] = _extract_target_industries(goals_text)
        # Resolve a numeric target level. Prefer an explicit 'Target seniority:'
        # keyword; otherwise infer from the target titles themselves (goals.md
        # states target *titles* like 'Engagement Lead' / 'Program Manager', not
        # levels). Without this the seniority dimension (25%) was permanently
        # neutral in real scans. Origin: fable-audit Theme 3.
        result["target_seniority_level"] = _resolve_target_level(
            result["target_seniority"], result["target_titles"]
        )

    # Parse profile.md
    profile_text = _read_text(data_dir / "profile.md")
    if profile_text is not None:
        result["skills"] = _extract_skills(profile_text)

    # Revealed-fit overlay (deterministic; distilled from Nick's 52 hand-labels).
    result["fit_spec"] = load_fit_spec(repo_root)

    return result


def _read_text(path: Path):
    """A file's text, or None when it is missing or unreadable (bad bytes included).

    An unreadable goals.md is treated like a missing one: no titles, and stale, so the
    scan lists every role and ranks none. A non-UTF-8 byte used to raise out of the
    nightly scan (Codex F2, review r2).
    """
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except (OSError, UnicodeError):
        return None


def load_fit_spec(repo_root: Path) -> dict:
    """Load the revealed-fit overlay (data/calibration/fit-spec.yaml), or {}.

    The overlay encodes the title-shape screen + reasoned not-fit taxonomy from
    Nick's 52 hand-labels (output/analysis/071526-fit-function.md), letting the
    scorer rank against his REVEALED fit function, not just the STATED thesis in
    goals.md. Graceful by design: a missing file, missing PyYAML, or a parse error
    all return {} so score_role behaves exactly as before (overlay = 0).
    """
    path = repo_root / "data" / "calibration" / "fit-spec.yaml"
    if not path.exists():
        return {}
    try:
        import yaml  # PyYAML — already a repo dep (cv themes, discover presets)
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # A valid YAML file whose top level is not a mapping is no overlay: score_role calls
    # .get on it, and a list crashed the nightly scan (Codex F1 / Grok F1, review r3).
    return spec if isinstance(spec, dict) else {}


# The colon may sit inside or just outside the bold (Grok F7, review r1).
_OPEN_TO_WORK = re.compile(r"\*\*Open to Work titles[^*]*(?::\*\*|\*\*:)(.*)$", re.IGNORECASE)
# A word, then a period, then whitespace or the line's end: a candidate sentence end.
_PERIOD_AFTER_WORD = re.compile(r"(\w+)\.(?=\s|$)")
# Title abbreviations of 4+ letters, whose period is part of the title (Grok F2, review r2).
# Shorter ones ("Sr.", "VP.", "Mgr.") never end the list.
_TITLE_ABBREVIATIONS = {"asst", "assoc", "exec", "dept", "corp", "intl", "govt", "mgmt",
                        "admin", "mktg", "coord", "supv", "engr", "prog", "tech", "natl"}
_LAST_REVIEWED = re.compile(r"^Last reviewed:\s*(\S+)", re.MULTILINE)
# Days goals.md may go unreviewed before the scorer refuses to rank (Nick, 2026-11-06:
# "14 days"). target-drift-1106.
GOALS_FRESH_DAYS = 14


def _target_criteria(text: str) -> list[str]:
    """The lines of the `## Target Criteria` section only, up to the next `## ` heading."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "target criteria"
            continue
        if inside:
            out.append(line)
    return out


def _extract_target_titles(text: str) -> list[str]:
    """The titles on the Open to Work line inside goals.md `## Target Criteria`.

    That line is the one source for target titles (Nick, 2026-11-06: "LinkedIn is true").
    Nothing else in the file is read: until target-drift-1106 a "Role types" heading
    anywhere in goals.md started a list, so a superseded ranked list kept feeding the
    scorer after a thesis refresh. Titles are semicolon-separated. The list
    ends at a sentence-ending period: one after a word of 4+ letters that is not a title
    abbreviation (_TITLE_ABBREVIATIONS), followed by a space or the line's end ("Lead. Every
    scanner ..."). A period after a shorter abbreviation ("Sr. Director", "U.S.", "Mgr.") or
    a listed one ("Exec. Director") is part of the title: ending at the first ". " dropped
    every title after it (Codex F1 / Grok F7, review r1; Grok F2, review r2).
    """
    for line in _target_criteria(text):
        m = _OPEN_TO_WORK.search(line)
        if m:
            body = m.group(1)
            for end in _PERIOD_AFTER_WORD.finditer(body):
                word = end.group(1)
                if len(word) >= 4 and word.lower() not in _TITLE_ABBREVIATIONS:
                    body = body[:end.end(1)]
                    break
            body = body.rstrip()
            body = body[:-1] if body.endswith(".") else body   # the list's own final period
            # A TODO placeholder (framework/templates/goals.md) is not a title.
            return [t.strip() for t in body.split(";")
                    if t.strip() and not t.strip().upper().startswith("TODO")]
    return []


def goals_freshness(text: str, today: date) -> dict:
    """Whether goals.md was reviewed within GOALS_FRESH_DAYS of `today`.

    A missing, unparseable or future date is stale: a check that cannot read the date
    must not pass (a typo in the future would otherwise hide staleness for good).
    """
    m = _LAST_REVIEWED.search(text or "")
    try:
        reviewed = date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        reviewed = None
    if reviewed is None or reviewed > today:
        return {"goals_last_reviewed": m.group(1) if m else None, "goals_age_days": None,
                "goals_stale": True}
    age = (today - reviewed).days
    return {"goals_last_reviewed": reviewed.isoformat(), "goals_age_days": age,
            "goals_stale": age > GOALS_FRESH_DAYS}


def unranked_reason(ctx: dict) -> str:
    """The reason a role carries when goals.md is stale and nothing is ranked."""
    age = ctx.get("goals_age_days")
    days = f"{age} days" if age is not None else "no valid date"
    return (f"unranked: goals.md last reviewed {ctx.get('goals_last_reviewed') or 'unknown'} "
            f"({days}; limit {GOALS_FRESH_DAYS})")


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
    # NO blockquote fallback. Removed 2026-09-02.
    #
    # This used to scan every blockquote line for any SENIORITY_LEVELS keyword. In the
    # live goals.md it matched "head" inside a blockquoted FILE PATH, resolving target
    # seniority to 'Head' (level 8). Since extract_seniority() defaults an unkeyworded
    # title to 4, the owner's own #1 target title "Deployment Strategist" then scored
    # 10 - |4-8|*2 = 2.0 on 25% of the weight, while "Regional Director, Forward
    # Deployed Engineering" scored 10.0. The ranking was inverted against his lane.
    #
    # Returning "" is correct and not a regression: _resolve_target_level() infers the
    # level from the target TITLES, which is the deliberate path added for fable-audit
    # Theme 3 precisely because goals.md states titles rather than levels. The
    # blockquote scan was shadowing that inference with prose noise.
    # Guarded by tests/scripts/test_scorer.py::test_a_seniority_word_inside_a_
    # blockquoted_path_is_not_a_target_level.
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
    # Whole words only, plural allowed: a substring test read 'International' as intern and
    # 'Leadership' as lead (40.F1); 'SVP'/'EVP' are their own keys, since a whole-word
    # 'vp' no longer finds them inside the abbreviation (473.F1).
    for keyword in sorted(SENIORITY_LEVELS.keys(), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}s?(?![a-z0-9])", title_lower):
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
    # The standardized schema field is `description_plain`. A caller assembling a role
    # by hand (a skill pasting ad text, a fixture) naturally reaches for `description`,
    # and silently scoring an empty description is how the keyword dimension went dark
    # in the first parity fixtures. Alias here, in the library, so every caller gets the
    # same answer -- putting it in the CLI would make the CLI a second implementation.
    # Found by cross-model review 2026-09-14 (F4).
    if not role.get("description_plain") and role.get("description"):
        role = {**role, "description_plain": role["description"]}

    d1 = _score_title_match(role, context)
    d2 = _score_seniority_match(role, context)
    d3 = _score_industry_match(role, context)
    d4 = _score_keyword_overlap(role, context)

    raw = _W_TITLE * d1 + _W_SENIORITY * d2 + _W_INDUSTRY * d3 + _W_KEYWORD * d4
    raw += _fit_overlay_adjustment(role, context.get("fit_spec") or {})
    return max(1, min(10, round(raw)))


def _fit_overlay_adjustment(role: dict, fit_spec: dict) -> float:
    """Revealed-fit deterministic overlay: points added to the raw 4-dim score.

    Encodes the title-shape screen and the reasoned not-fit taxonomy from Nick's
    52 hand-labels (output/analysis/071526-fit-function.md):
      - an in-lane title (Deployment Strategist / Forward Deployed) is necessary
        but NOT sufficient — if the scope reads GTM/quota/CS it is screened out
        (the Context case);
      - CoS/BizOps/CS/Growth titles and off-lane domains (CPQ/deal-pricing) are
        reasoned down-ranks (Nick's own declines — the highest-signal negatives).
    Returns 0.0 when no fit_spec is loaded, so the scorer is unchanged without it.
    """
    if not fit_spec:
        return 0.0
    title = (role.get("title") or "").lower()
    desc = (role.get("description_plain") or "").lower()
    title_and_desc = f"{title} {desc}"

    def any_in(patterns, hay):
        return any(str(p).lower() in hay for p in (patterns or []))

    # r4 (Grok F2): weights that is not a mapping (7, [1], "high") or holds a non-number crashed
    # scoring. Keep only numeric entries; a missing key falls back to its documented default.
    # target_drift reports the same file as unreadable, so the bad value is not silent.
    raw_w = fit_spec.get("weights")
    w = {k: v for k, v in raw_w.items()
         if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(raw_w, dict) else {}
    pos_title = any_in(fit_spec.get("positive_title_patterns"), title)
    scope_dq = any_in(fit_spec.get("scope_disqualifiers"), title_and_desc)
    notfit_title = any_in(fit_spec.get("not_fit_title_patterns"), title)
    notfit_domain = any_in(fit_spec.get("not_fit_domain_patterns"), title_and_desc)

    adj = 0.0
    if notfit_title:
        adj += w.get("not_fit_title", -4.0)
    if notfit_domain:
        adj += w.get("not_fit_domain", -3.0)
    if pos_title and scope_dq:
        adj += w.get("title_shape_screen", -3.0)   # in-lane title, out-of-lane scope
    elif pos_title:
        adj += w.get("in_lane_title", 2.0)

    lo = w.get("min_adjustment", -5.0)
    hi = w.get("max_adjustment", 3.0)
    return max(lo, min(hi, adj))


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


def _resolve_target_level(target_seniority: str, target_titles: list) -> int | None:
    """Resolve a numeric target seniority level (1-10) or None if indeterminate.

    Priority: an explicit 'Target seniority:' keyword (mapped via
    SENIORITY_LEVELS), else infer from the target titles by taking the median of
    each title's extracted level. Returns None only when there is neither an
    explicit keyword nor any target titles, in which case the seniority
    dimension stays neutral (the pre-fix behavior).
    """
    if target_seniority and target_seniority.lower() in SENIORITY_LEVELS:
        return SENIORITY_LEVELS[target_seniority.lower()]
    levels = [extract_seniority(t) for t in (target_titles or [])]
    if not levels:
        return None
    return round(statistics.median(levels))


def _score_seniority_match(role: dict, context: dict) -> float:
    """Dimension 2: Seniority match (weight 0.25).

    Score = 10 - abs(role_level - target_level) * 2, clamped 1-10.
    """
    target_level = context.get("target_seniority_level")
    if target_level is None:
        # Back-compat: contexts built before the numeric resolver, or goals with
        # no titles and no explicit level — fall back to the keyword, else neutral.
        target_seniority = context.get("target_seniority", "")
        if not target_seniority:
            return 5.0
        target_level = SENIORITY_LEVELS.get(target_seniority.lower(), 4)

    role_level = extract_seniority(role.get("title", ""))
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


# A comparison term longer than this cannot realistically appear verbatim in a job
# posting, so it is unusable input rather than evidence of a poor match. Origin
# 2026-09-02: profile.md skill bullets averaged ~9 words and matched nothing, ever.
_MAX_USABLE_TERM_WORDS = 4


def _score_keyword_overlap(role: dict, context: dict) -> float:
    """Dimension 4: Keyword overlap (weight 0.20).

    Count how many skills appear as case-insensitive substrings in description.
    Score = min(10, matched_count * 2).
    """
    skills = context.get("skills", [])
    description = role.get("description_plain", "")
    if not skills or not description:
        return 3.0

    # Only SHORT terms can match a posting. `_extract_skills` pulls whole
    # human-readable bullets out of profile.md ("Strategic operations and planning
    # (FY planning, OKRs, budget management)"), which cannot appear as a substring in
    # any JD. Before 2026-09-02 those scored a hard 0.0 while a role with NO
    # description scored 3.0 -- so having a job description actively lowered the score,
    # on 20% of the total weight. That is an input problem masquerading as a poor
    # candidate match.
    #
    # A term is usable only if it is <= _MAX_USABLE_TERM_WORDS words. If NO term is
    # usable the dimension is dark: return neutral and say so, rather than scoring a
    # real zero against the role. Guarded by
    # tests/scripts/test_scorer.py::test_a_real_description_never_scores_worse_than_a_missing_one
    usable = [s for s in skills if len(s.split()) <= _MAX_USABLE_TERM_WORDS]
    if not usable:
        return 3.0

    description_lower = description.lower()
    matched = sum(1 for s in usable if s.lower() in description_lower)
    return min(10.0, float(matched * 2))
