"""slug.slugify: the one slug function behind thirteen tools (2026-10-22 merge)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from slug import slugify  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("Acme AI", "acme-ai"),
    ("  Northwind -- Labs!  ", "northwind-labs"),
    ("Ana Placeholder-Núñez", "ana-placeholder-nunez"),     # accents fold, never split
    ("Jordan O'Lee", "jordan-o-lee"),                    # apostrophe splits by default
])
def test_the_output_slug_convention(text, expected):
    assert slugify(text) == expected


def test_apostrophes_can_be_elided_including_the_curly_one():
    assert slugify("Cato Wasn't Always", elide_apostrophes=True) == "cato-wasnt-always"
    assert slugify("Cato Wasn’t Always", elide_apostrophes=True) == "cato-wasnt-always"


def test_maxlen_cuts_without_leaving_a_trailing_hyphen():
    assert slugify("abc def", maxlen=4) == "abc"


@pytest.mark.parametrize("text", ["", None, "!!!", "日本語"])
def test_fallback_when_nothing_alphanumeric_survives(text):
    assert slugify(text, fallback="untitled") == "untitled"
    assert slugify(text) == ""
