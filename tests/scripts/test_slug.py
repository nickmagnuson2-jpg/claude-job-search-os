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


def test_punctuation_can_be_dropped_instead_of_split_on():
    """The title/filename callers always deleted punctuation: v1.2 -> v12, wasn't -> wasnt."""
    assert slugify("Cato Wasn't Always", drop_punctuation=True) == "cato-wasnt-always"
    assert slugify("Cato Wasn’t Always", drop_punctuation=True) == "cato-wasnt-always"
    assert slugify("v1.2 Release: notes", drop_punctuation=True) == "v12-release-notes"
    assert slugify("v1.2 Release") == "v1-2-release"


def test_maxlen_cuts_without_leaving_a_trailing_hyphen():
    assert slugify("abc def", maxlen=4) == "abc"


@pytest.mark.parametrize("text", ["", None, "!!!", "日本語"])
def test_fallback_when_nothing_alphanumeric_survives(text):
    assert slugify(text, fallback="untitled") == "untitled"
    assert slugify(text) == ""


@pytest.mark.parametrize("title,expected", [
    ("日本語のタイトル", "日本語のタイトル"),
    ("København guide", "københavn-guide"),
    ("Café notes", "cafe-notes"),
])
def test_title_mode_keeps_letters_in_any_script(title, expected):
    """Two non-Latin titles must not both become the fallback and collide on disk."""
    assert slugify(title, drop_punctuation=True, fallback="video") == expected
