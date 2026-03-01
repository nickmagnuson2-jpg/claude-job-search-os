"""ProfileParser tests using minimal HTML fixture strings.

No Selenium, no live LinkedIn, no filesystem writes.
All parser dependencies (bs4, termcolor, tqdm) are installed in the test env.

The fixture HTML must match LinkedIn's DOM structure closely enough that
ProfileParser's BS4 selectors fire correctly. Each helper below constructs
the minimum valid HTML for a given scenario.
"""
import sys
from pathlib import Path

# ─── Path setup must precede all scanner imports ─────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = REPO_ROOT / "tools" / "linkedin-scanner"

if str(SCANNER_DIR) not in sys.path:
    sys.path.insert(0, str(SCANNER_DIR))

from src.ProfileParser import ProfileParser  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────


# ── HTML fixture helpers ──────────────────────────────────────────────────────

def _base_html(name: str = "Test User", extra: str = "") -> str:
    """Minimal valid LinkedIn-style HTML: name h1 + network ul + optional extra."""
    return (
        f'<h1 class="text-heading-xlarge">{name}</h1>'
        '<ul class="pv-top-card--list"><li>500+ connections</li></ul>'
        + extra
    )


def _experience_section(items: list) -> str:
    """Build an experience <section> from a list of span-text lists.

    Each item is [position, company?, ...] — values become visually-hidden spans.
    No whitespace between tags so BS4 children[1] resolves without NavigableString
    noise from indentation.
    """
    lis = ""
    for spans in items:
        span_tags = "".join(
            f'<span class="visually-hidden">{s}</span>' for s in spans
        )
        lis += f'<li class="pvs-list__item--line-separated">{span_tags}</li>'
    return (
        f'<section><div id="experience"></div>'
        f'<ul class="pvs-list">{lis}</ul></section>'
    )


def _education_section(items: list) -> str:
    """Build an education <section> from a list of span-text lists.

    Each item is [school, degree?, ...].
    """
    lis = ""
    for spans in items:
        span_tags = "".join(
            f'<span class="visually-hidden">{s}</span>' for s in spans
        )
        lis += f'<li class="pvs-list__item--line-separated">{span_tags}</li>'
    return (
        f'<section><div id="education"></div>'
        f'<ul class="pvs-list">{lis}</ul></section>'
    )


def _mutuals_span(text: str) -> str:
    """Build a mutuals span whose children[1] (NavigableString) is *text*.

    Structure: <span class="..."><span></span>{text}</span>
    children = [Tag(<span/>), NavigableString(text)]
    [child.get_text() for child in .children] = ["", text]
    [1] = text
    """
    return (
        '<span class="t-normal t-black--light t-14 hoverable-link-text">'
        f"<span></span>{text}"
        "</span>"
    )


# ── Name parsing ──────────────────────────────────────────────────────────────

def test_parse_name_extracted():
    parser = ProfileParser()
    profile = parser.parse(_base_html("Jane Smith"), use_cache=False)
    assert profile is not None
    assert profile["name"] == "Jane Smith"


def test_parse_returns_none_when_name_missing():
    """HTML without the h1.text-heading-xlarge → parse() returns None."""
    parser = ProfileParser()
    html = '<ul class="pv-top-card--list"><li>500+ connections</li></ul>'
    assert parser.parse(html, use_cache=False) is None


def test_parse_returns_none_when_network_missing():
    """HTML without the pv-top-card--list ul → parse() returns None."""
    parser = ProfileParser()
    html = '<h1 class="text-heading-xlarge">Jane Smith</h1>'
    assert parser.parse(html, use_cache=False) is None


# ── Experience parsing ────────────────────────────────────────────────────────

def test_parse_experience_single_entry():
    """Single experience entry → position and company_name extracted correctly."""
    parser = ProfileParser()
    exp = _experience_section([["Software Engineer", "Acme Corp"]])
    profile = parser.parse(_base_html("Jane Smith", exp), use_cache=False)
    assert profile is not None
    assert len(profile["experience"]) == 1
    assert profile["experience"][0]["position"] == "Software Engineer"
    assert profile["experience"][0]["company_name"] == "Acme Corp"


def test_parse_experience_with_employment_type():
    """Company span 'Company · Type' splits into company_name and employment_type."""
    parser = ProfileParser()
    # U+00B7 is the middle dot that LinkedIn uses as the separator character
    exp = _experience_section([["Lead Engineer", "Acme Corp \u00b7 Full-time"]])
    profile = parser.parse(_base_html("Jane Smith", exp), use_cache=False)
    assert profile is not None
    entry = profile["experience"][0]
    assert entry["company_name"] == "Acme Corp"
    assert entry["employment_type"] == "Full-time"


# ── Education parsing ─────────────────────────────────────────────────────────

def test_parse_education_single_entry():
    """Single education entry → school and degree extracted correctly."""
    parser = ProfileParser()
    edu = _education_section([["MIT", "BS Computer Science"]])
    profile = parser.parse(_base_html("Jane Smith", edu), use_cache=False)
    assert profile is not None
    assert len(profile["education"]) == 1
    assert profile["education"][0]["school"] == "MIT"
    assert profile["education"][0]["degree"] == "BS Computer Science"


# ── Mutuals parsing ───────────────────────────────────────────────────────────

def test_parse_mutuals_zero():
    """No mutuals span → profile['mutuals'] == 0."""
    parser = ProfileParser()
    profile = parser.parse(_base_html("Test User"), use_cache=False)
    assert profile["mutuals"] == 0


def test_parse_mutuals_two():
    """'X and Y' text → len(split('and')) == 2 → returns 2."""
    parser = ProfileParser()
    html = _base_html("Test User", _mutuals_span("John and Jane"))
    profile = parser.parse(html, use_cache=False)
    assert profile["mutuals"] == 2


def test_parse_mutuals_many():
    """'X, Y, and N others' → len(split(',')) == 3 → returns N + 2."""
    parser = ProfileParser()
    html = _base_html("Test User", _mutuals_span("John, Jane, and 5 others"))
    profile = parser.parse(html, use_cache=False)
    assert profile["mutuals"] == 7


# ── shorten() ─────────────────────────────────────────────────────────────────

def test_shorten_truncates_long_string():
    """Strings longer than 280 chars are truncated to 280 + '...'."""
    parser = ProfileParser()
    profile = {"text": "a" * 300}
    parser.shorten(profile)
    assert profile["text"] == "a" * 280 + "..."


def test_shorten_strips_unicode():
    """Non-ASCII characters are removed via encode('ascii', 'ignore')."""
    parser = ProfileParser()
    profile = {"text": "caf\u00e9 r\u00e9sum\u00e9"}  # café résumé
    parser.shorten(profile)
    assert "\u00e9" not in profile["text"]
    assert "caf" in profile["text"]  # ASCII portion survives


def test_shorten_normalizes_whitespace():
    """Newlines and multi-space runs collapse to single spaces."""
    parser = ProfileParser()
    profile = {"text": "hello\n  world"}
    parser.shorten(profile)
    assert profile["text"] == "hello world"


def test_shorten_removes_see_more():
    """' ... see more' suffix (LinkedIn artifact) is stripped from strings."""
    parser = ProfileParser()
    profile = {"text": "Some description ... see more"}
    parser.shorten(profile)
    assert " ... see more" not in profile["text"]
    assert "Some description" in profile["text"]


def test_shorten_recurses_into_dict():
    """shorten() must truncate strings nested inside dict values.

    ProfileParser.shorten() delegates nested dicts to the free shorten()
    function from src/shorten.py — this test proves that recursion works
    end-to-end, not just at the top level.
    """
    parser = ProfileParser()
    profile = {"nested": {"deep_text": "x" * 300}}
    parser.shorten(profile)
    assert profile["nested"]["deep_text"] == "x" * 280 + "..."


def test_shorten_recurses_into_list():
    """shorten() must truncate strings inside list items.

    ProfileParser.shorten() delegates list items to the free shorten()
    function from src/shorten.py — this test proves list recursion works.
    """
    parser = ProfileParser()
    profile = {"items": [{"entry_text": "y" * 300}]}
    parser.shorten(profile)
    assert profile["items"][0]["entry_text"] == "y" * 280 + "..."
