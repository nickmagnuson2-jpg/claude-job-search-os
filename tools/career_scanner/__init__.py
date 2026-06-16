"""
career_scanner - ATS parser package for fetching open roles from company career pages.

Provides standardized role dicts from Greenhouse, Lever, Ashby APIs and a
Playwright-based generic fallback for custom career pages.
"""

# Standardized role dict keys - all parsers must return dicts with exactly these keys
ROLE_KEYS = {
    "title",
    "company",
    "department",
    "team",
    "location",
    "remote",
    "employment_type",
    "url",
    "apply_url",
    "published_at",
    "description_plain",
    "ats",
}


def validate_role(d: dict) -> bool:
    """Check that a dict has all required Role keys."""
    return set(d.keys()) == ROLE_KEYS


# Parser registry - populated by imports below
PARSERS: dict = {}

try:
    from tools.career_scanner.greenhouse import fetch_greenhouse
    PARSERS["greenhouse"] = fetch_greenhouse
except ImportError:
    pass

try:
    from tools.career_scanner.lever import fetch_lever
    PARSERS["lever"] = fetch_lever
except ImportError:
    pass

try:
    from tools.career_scanner.ashby import fetch_ashby
    PARSERS["ashby"] = fetch_ashby
except ImportError:
    pass

try:
    from tools.career_scanner.generic import fetch_generic
    PARSERS["generic"] = fetch_generic
except ImportError:
    pass
