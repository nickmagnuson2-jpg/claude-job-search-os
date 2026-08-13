"""W4 — the proof-domain enum is closed, and its aliases actually collapse.

If `customer-experience` and `customer-ops` stay distinct strings, a prep doc can bind a
"reserve" proof in the same domain as its primary and a string-compare checker goes
green — while one interviewer sentence still takes out both. These tests are what make
check_prep_doc's check 3 mean something.
"""
import pytest

from conftest import TOOLS_DIR, run_script_raw

import sys

sys.path.insert(0, str(TOOLS_DIR))
import proof_domains  # noqa: E402


def test_every_alias_resolves_into_the_canonical_enum():
    for alias, target in proof_domains.ALIASES.items():
        assert target in proof_domains.CANONICAL, f"alias {alias!r} points outside the enum"


def test_no_canonical_tag_is_also_an_alias_key():
    """An alias whose key is itself canonical makes resolution order-dependent."""
    overlap = set(proof_domains.ALIASES) & set(proof_domains.CANONICAL)
    assert not overlap, f"canonical tags used as alias keys: {sorted(overlap)}"


def test_canonicalize_is_idempotent():
    for tag in proof_domains.CANONICAL:
        assert proof_domains.canonicalize(tag) == tag
    for alias in proof_domains.ALIASES:
        once = proof_domains.canonicalize(alias)
        assert proof_domains.canonicalize(once) == once


def test_the_synonym_that_motivated_the_enum_collapses():
    assert (proof_domains.canonicalize("customer-experience")
            == proof_domains.canonicalize("customer-ops")
            == "customer-ops")


@pytest.mark.parametrize("surface", ["Customer Ops", "CUSTOMER_OPS", " customer--ops "])
def test_surface_spelling_is_normalized(surface):
    assert proof_domains.canonicalize(surface) == "customer-ops"


def test_unknown_tag_is_none_not_a_guess():
    assert proof_domains.canonicalize("vibes") is None
    assert proof_domains.is_valid("vibes") is False


def test_cli_unknown_tag_lists_the_valid_set():
    proc = run_script_raw("proof_domains.py", "--canonicalize", "vibes")
    assert proc.returncode == 4
    assert "customer-ops" in proc.stdout
