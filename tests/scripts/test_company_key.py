"""Tests for tools/company_key.py: the one answer to "is this the same company?".

Two tiers (ruling, overnight run 2026-11-21, org c0):
- same_company: a strict key. It may auto-resolve, but only where a wrong merge is
  visible to Nick (pipeline_history: the scanner and the skipped list).
- possible_duplicate: a looser key that also drops "ai". It never resolves anything;
  callers only tag a match "possible duplicate of <name>".

Real-data cases come from the S9 audit (2026-11-21; output/overnight-112126/S9-report.md,
gitignored): 452 known company names, 27 pairs that exact matching did not merge but
overlap or similarity did, judged by hand as 21 same company and 5 different, plus one
real pair whose names differ only by "AI" and are two different companies. Names here
are invented: each pair keeps the shape of a real pair without naming a real company.
This repo is public.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import company_key as ck  # noqa: E402

# 8 same-company pairs the STRICT key merges (shape noted per pair).
STRICT_SAME = [
    ("Vantoro Systems", "Vantoro Systems, Inc."),     # suffix ", Inc."
    ("Harbix", "Harbix.com"),                         # suffix ".com"
    ("Orchalo Labs", "Orchalo Labs (Sparrix)"),       # parenthetical alias
    ("Orchalo Labs", "Sparrix (Orchalo Labs)"),       # name and alias swapped
    ("Orchalo Labs (Sparrix)", "Sparrix"),            # alias alone
    ("Sparrix", "Sparrix (Orchalo Labs)"),            # alias alone, other order
    ("Tallyo", "Tallyo (Ledgerpine)"),                    # parenthetical alias
    ("Contoso Works", "ContosoWorks"),                    # spacing
]

# 7 more same-company pairs that only the LOOSE key catches: the "ai" suffix.
LOOSE_ONLY_SAME = [
    ("Brivanta", "Brivanta AI"),
    ("Lumiso", "Lumiso AI"),
    ("Nimbaro", "Nimbaro AI"),
    ("Ledgro", "Ledgro AI"),
    ("Signato", "Signato AI"),
    ("Quorrex", "Quorrex.ai"),
    ("Zeph9", "Zeph9.ai"),
]

# 6 same-company pairs neither tier merges: a descriptor word or product line is part
# of the name. Dropping "health" or "systems" would merge distinct companies.
NEITHER_SAME = [
    ("Granix", "Granix Health"),
    ("Coprix", "Coprix Health"),
    ("Vantoro", "Vantoro Systems"),
    ("Vantoro", "Vantoro Systems, Inc."),
    ("Quillo", "Quillo Movers"),
    ("Prontix", "Prontix Meridian"),
]

# 5 different-company pairs that whole-word overlap or similarity >= 0.84 merged on
# real data. Neither tier may merge them.
DIFFERENT = [
    ("Fabrik", "Fabrikdesk (formerly My AI Fabrik Desk)"),
    ("Quilla", "Quillas"),
    ("Draxo", "Drao"),
    ("Neovesk AI", "Ovesk.ai"),
    ("Tallgrove", "Tallgro"),
]

# Different companies whose names differ only by "AI": the strict key must keep them
# apart. The loose key cannot tell this shape from Brivanta / Brivanta AI, which is why
# the loose tier only ever tags.
AI_SUFFIX_DIFFERENT = ("Kestrix", "Kestrix AI")


@pytest.mark.parametrize("a,b", STRICT_SAME)
def test_strict_same_pairs_merge(a, b):
    assert ck.same_company(a, b)
    assert ck.same_company(b, a)
    assert ck.possible_duplicate(a, b)


@pytest.mark.parametrize("a,b", LOOSE_ONLY_SAME)
def test_ai_suffix_pairs_are_only_possible_duplicates(a, b):
    assert not ck.same_company(a, b)
    assert ck.possible_duplicate(a, b)
    assert ck.possible_duplicate(b, a)


@pytest.mark.parametrize("a,b", NEITHER_SAME)
def test_descriptor_pairs_do_not_merge(a, b):
    assert not ck.same_company(a, b)
    assert not ck.possible_duplicate(a, b)


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_different_companies_never_merge(a, b):
    for x, y in ((a, b), (b, a)):
        assert not ck.same_company(x, y)
        assert not ck.possible_duplicate(x, y)


def test_ai_suffix_different_companies_stay_apart_in_the_strict_tier():
    a, b = AI_SUFFIX_DIFFERENT
    assert not ck.same_company(a, b)
    assert ck.possible_duplicate(a, b)   # tagged for Nick, never resolved


def test_real_data_rates_match_the_audit():
    """S9 audit, measured: strict 8 of 21, loose 15 of 21, 0 false merges in either
    tier on the 5 judged-different pairs."""
    pairs = STRICT_SAME + LOOSE_ONLY_SAME + NEITHER_SAME
    assert len(pairs) == 21 and len(DIFFERENT) == 5
    assert sum(ck.same_company(a, b) for a, b in pairs) == 8
    assert sum(ck.possible_duplicate(a, b) for a, b in pairs) == 15
    assert sum(ck.same_company(a, b) for a, b in DIFFERENT) == 0
    assert sum(ck.possible_duplicate(a, b) for a, b in DIFFERENT) == 0


@pytest.mark.parametrize("a,b", [
    ("Acme", "acme"),            # case
    ("Café Nova", "Cafe Nova"),  # accents
    ("Acme Co.", "Acme Co"),     # punctuation
    ("Acme  Labs", "Acme Labs"), # spacing
    ("Acme (X)", "Acme X"),      # brackets are punctuation to normalize_name
])
def test_everything_exact_merges_still_merges(a, b):
    """No regression from normalize_name (the exact tier pipeline_history and
    pipeline_reconcile used): its merges are a subset of both tiers. The ASCII-only legacy
    normalizers (agent_core._norm_name, company_queue.norm) merged some non-ASCII pairs that
    exact does not; those are now kept, never dropped (test_company_key_wiring.py, Codex r1)."""
    from name_dedup import normalize_name
    assert normalize_name(a) == normalize_name(b)
    assert ck.exact(a, b)
    assert ck.same_company(a, b)
    assert ck.possible_duplicate(a, b)


def test_exact_is_normalize_name_equality():
    assert ck.exact("Acme Co.", "acme co")
    assert not ck.exact("Acme", "Acme Inc")
    assert not ck.exact("Acme", "Acme AI")
    assert not ck.exact("Acme Labs", "AcmeLabs")
    assert not ck.exact("", "")


@pytest.mark.parametrize("a,b", [("", ""), ("", "Acme"), ("   ", "Acme"), (None, "Acme"),
                                 ("()", "()"), (".", "-")])
def test_empty_never_matches(a, b):
    assert not ck.exact(a, b)
    assert not ck.same_company(a, b)
    assert not ck.possible_duplicate(a, b)


def test_a_name_made_only_of_dropped_words_keeps_a_key():
    """"The Co" must not collapse to an empty key and vanish from every comparison."""
    assert ck.company_keys("The Inc Co")
    assert ck.same_company("The Inc Co", "the inc co.")
    assert not ck.same_company("The Inc Co", "Inc Co Labs")
    assert ck.possible_duplicate("AI", "A.I.")


def test_dropped_words_only_drop_as_whole_words():
    """"ai" or "co" inside a word is not a suffix."""
    assert not ck.possible_duplicate("Vaizel", "Vzel")
    assert not ck.same_company("Coral", "Ral")
    assert ck.possible_duplicate("Vaizel AI", "Vaizel")


def test_formerly_prefix_is_stripped_from_an_alias():
    assert ck.same_company("Newbrand (formerly Oldbrand)", "Oldbrand")
    assert ck.same_company("Newbrand (aka Oldbrand)", "Oldbrand")
    assert ck.same_company("Newbrand (dba Oldbrand)", "Oldbrand")


def test_company_keys_is_a_stable_frozenset():
    k = ck.company_keys("Orchalo Labs (Sparrix)")
    assert isinstance(k, frozenset)
    assert k == ck.company_keys("orchalo labs (SPARRIX)")
    assert {"orchalolabs", "sparrix"} <= k


def test_strict_keys_keep_ai_and_loose_keys_drop_it():
    assert "kestrixai" in ck.company_keys("Kestrix AI")
    assert "kestrix" not in ck.company_keys("Kestrix AI")
    assert "kestrix" in ck.company_keys("Kestrix AI", loose=True)


def test_index_lookup_returns_display_names():
    idx = ck.KeyIndex(["Signato AI", "Orchalo Labs (Sparrix)", "", None, "Kestrix"])
    assert idx.same("Sparrix") == ["Orchalo Labs (Sparrix)"]
    assert idx.same("Signato") == []
    assert idx.possible("Signato") == ["Signato AI"]
    assert idx.possible("Kestrix AI") == ["Kestrix"]
    assert idx.exact("signato ai") == ["Signato AI"]
    assert idx.exact("Signato") == []
    assert idx.same("") == [] and idx.possible("") == [] and idx.exact("") == []
    assert idx.same("Granix Health") == []


def test_index_returns_each_name_once_even_when_several_of_its_keys_match():
    """"Orchalo Labs (Sparrix)" answers to three keys; a lookup by itself must list it once."""
    idx = ck.KeyIndex(["Orchalo Labs (Sparrix)", "Sparrix"])
    assert idx.same("Orchalo Labs (Sparrix)") == ["Orchalo Labs (Sparrix)", "Sparrix"]
    assert idx.possible("Orchalo Labs (Sparrix)") == ["Orchalo Labs (Sparrix)", "Sparrix"]


def test_index_never_indexes_a_blank_name():
    idx = ck.KeyIndex(["", "   ", None, "-", "Signato"])
    assert idx._exact == {"signato": ["Signato"]}
    assert idx._tables == {False: ({"signato": ["Signato"]}, {}),
                           True: ({"signato": ["Signato"]}, {})}


# Live data, 2026-11-21: most bracketed text is a descriptor, not an alias. Of 597 live
# company strings, 28 carried brackets: "(former)", "(member)", a city, "(TBD)". Two
# different companies sharing a descriptor must never merge.
DESCRIPTOR_TWINS = [
    ("Brookwater (former)", "Corvane (former)"),
    ("Northbay Commons (member)", "Halvard Club (member)"),
    ("Quietline (Palo Alto)", "Brightvale (Palo Alto)"),
]


@pytest.mark.parametrize("a,b", DESCRIPTOR_TWINS)
def test_two_names_sharing_a_bracketed_descriptor_never_merge(a, b):
    assert not ck.same_company(a, b)
    assert not ck.possible_duplicate(a, b)
    idx = ck.KeyIndex([a])
    assert idx.same(b) == [] and idx.possible(b) == []


@pytest.mark.parametrize("a,b", [("Brookwater", "Brookwater (former)"),
                                 ("Corvane", "Corvane (former)")])
def test_a_descriptor_still_resolves_to_its_own_company(a, b):
    assert ck.same_company(a, b)
    assert ck.KeyIndex([a]).same(b) == [a]


def test_an_alias_matches_the_other_names_main_name_through_the_index():
    idx = ck.KeyIndex(["Sparrix (Orchalo Labs)"])
    assert idx.same("Orchalo Labs") == ["Sparrix (Orchalo Labs)"]
    assert idx.same("Orchalo Labs (Sparrix)") == ["Sparrix (Orchalo Labs)"]
    assert idx.same("Tallyo (Orchalo Labs)") == []   # alias-to-alias: never


@pytest.mark.parametrize("a,b", STRICT_SAME + LOOSE_ONLY_SAME + NEITHER_SAME + DIFFERENT
                         + DESCRIPTOR_TWINS + [AI_SUFFIX_DIFFERENT])
def test_the_index_agrees_with_the_pairwise_functions(a, b):
    idx = ck.KeyIndex([a])
    assert (idx.same(b) == [a]) is ck.same_company(a, b)
    assert (idx.possible(b) == [a]) is ck.possible_duplicate(a, b)
    assert (idx.exact(b) == [a]) is ck.exact(a, b)
