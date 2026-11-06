"""One location classifier for both gates (scan-drops-1106, 2026-11-06).

Until this branch the role gate (`scanner.geo_ok`) dropped real San Francisco roles:
"SF", "SF, CA", "NYC / SF", "Hybrid (3 days in SF)" all failed because only the
substring "san francisco" counted, and "San Francisco, Palo Alto" failed because any
Peninsula town anywhere in a multi-office string dropped the role. Found by Grok review
(941.F2, F3) and confirmed by running geo_ok on main.

`company_scorer.location_band` classifies a location string once. The company gate
(`geo_gate`) and the role gate (`geo_ok`) each map that band to their own policy, so the
two cannot drift: the company gate penalises the Peninsula, the role gate drops it.
Nick, 2026-11-06: a remote role passes the role gate, flagged; the company gate is
unchanged for remote.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.career_scanner.company_scorer import geo_gate, is_peninsula, location_band  # noqa: E402
from tools.career_scanner.scanner import geo_ok  # noqa: E402


@pytest.mark.parametrize("loc", [
    "SF", "SF, CA", "sf", "NYC / SF", "Hybrid (3 days in SF)", "San Francisco, Palo Alto",
    "Palo Alto; San Francisco", "San Francisco, CA", "San Francisco Bay Area",
    "New York; San Francisco", "San Francisco or Remote", "Remote - San Francisco",
    "San Francisco, California, United States", "SAN FRANCISCO", "New York | SF",
    "Austin, TX or San Francisco, CA", "San Mateo / San Francisco",
])
def test_an_sf_office_anywhere_is_sf(loc):
    assert location_band(loc) == "sf"
    assert geo_ok(loc) is True
    assert geo_gate(loc)["band"] == "sf"
    assert geo_gate(loc)["excluded"] is False


@pytest.mark.parametrize("loc", [
    "Palo Alto, CA", "San Mateo", "South San Francisco, CA", "Redwood City, CA",
    "South San Francisco", "Mountain View; Sunnyvale", "San Jose, CA",
])
def test_peninsula_only_is_peninsula_and_the_role_gate_drops_it(loc):
    assert location_band(loc) == "peninsula"
    assert geo_ok(loc) is False
    assert is_peninsula(loc) is True
    g = geo_gate(loc)
    assert g["band"] == "peninsula" and g["excluded"] is False and g["penalty"] == 0.6


def test_south_san_francisco_alongside_sf_is_sf():
    assert location_band("South San Francisco, CA / San Francisco, CA") == "sf"


@pytest.mark.parametrize("loc", ["SFO", "SFDC", "Transfer team", "asf"])
def test_sf_must_be_a_whole_word(loc):
    assert location_band(loc) != "sf"


@pytest.mark.parametrize("loc", ["Remote", "Remote (US)", "remote - united states", "Fully remote"])
def test_remote_passes_the_role_gate_but_not_the_company_gate(loc):
    assert location_band(loc) == "remote"
    assert geo_ok(loc) is True
    g = geo_gate(loc)
    assert g["band"] == "remote" and g["excluded"] is True


def test_remote_with_a_peninsula_office_is_peninsula():
    # A Peninsula office is named, so it is not a remote-only seat.
    assert location_band("Remote or Palo Alto, CA") == "peninsula"


@pytest.mark.parametrize("loc", ["Oakland, California", "Berkeley", "Bay Area"])
def test_bay_passes_both(loc):
    assert location_band(loc) == "bay"
    assert geo_ok(loc) is True
    assert geo_gate(loc)["band"] == "bay"


@pytest.mark.parametrize("loc", ["Austin, Texas", "New York, NY", "London, UK", "CA"])
def test_outside_is_excluded(loc):
    assert location_band(loc) == "excluded"
    assert geo_ok(loc) is False
    assert geo_gate(loc)["excluded"] is True


@pytest.mark.parametrize("loc", ["", "   ", None])
def test_unknown_is_unknown(loc):
    assert location_band(loc) == "unknown"
    assert geo_ok(loc or "") is True


def test_is_peninsula_is_false_when_sf_is_also_listed():
    assert is_peninsula("San Francisco, Palo Alto") is False
