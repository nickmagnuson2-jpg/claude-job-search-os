"""Tests for title and geography filtering in tools/career_scanner/scanner.py.

Company names here are deliberately fictional placeholders - this file is public.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.company_scorer import is_peninsula
from tools.career_scanner.scanner import fetch_company_roles, geo_ok, title_matches

LANE = ["strategist", "deployment", "forward deployed", "architect"]


# --- title_matches: the Engineer head-noun rule -------------------------------

def test_engineer_as_head_noun_is_dropped():
    assert title_matches("Deployment Engineer", LANE) is False


def test_engineer_only_in_team_suffix_is_kept():
    """The load-bearing case: role proper is 'Lead', the team is Engineering.

    A naive substring exclusion drops this, and it is exactly the Strategist-
    shaped seat the filter exists to surface.
    """
    assert title_matches("Pre-Sales Program Lead, Forward Deployed Engineering", LANE) is True


def test_engineering_gerund_as_head_noun_is_dropped():
    assert title_matches("Engineering Manager, Deployments", ["deployment"]) is False


def test_known_limit_manager_of_an_engineering_org_still_passes():
    """Documented false positive, kept deliberately.

    'Manager, Forward Deployed Engineering' is structurally identical to
    'Pre-Sales Program Lead, Forward Deployed Engineering' - a non-engineer head
    noun with an Engineering team suffix - but the first manages the engineers
    and the second does not. The head-noun rule cannot separate them. We accept
    the false positive because the failure costs one glance in the inbox, while
    tightening the rule would drop the Strategist-shaped seat entirely.
    """
    assert title_matches("Manager, Forward Deployed Engineering", ["forward deployed"]) is True


def test_engineer_plural_head_noun_is_dropped():
    assert title_matches("Deployment Engineers", LANE) is False


def test_engineer_substring_does_not_false_match():
    """'Engineered' must not trip the boundary-anchored pattern."""
    assert title_matches("Strategist, Engineered Systems", LANE) is True


def test_allow_engineer_titles_overrides_the_rule():
    assert title_matches("Deployment Engineer", LANE, allow_engineer=True) is True


# --- title_matches: includes / excludes ---------------------------------------

def test_empty_includes_passes_everything():
    assert title_matches("Warehouse Associate", []) is True


def test_non_matching_title_is_dropped():
    assert title_matches("Warehouse Associate", LANE) is False


def test_exclude_beats_include():
    """An excluded token wins even though 'strategist' is an include hit."""
    assert title_matches("Content Strategist", LANE, ["content strategist"]) is False


def test_exclude_applies_to_whole_title_not_just_head():
    assert title_matches("Solutions Architect, Sales Performance", LANE,
                         ["sales performance"]) is False


def test_include_match_is_case_insensitive():
    assert title_matches("DEPLOYMENT STRATEGIST", ["deployment"]) is True


def test_empty_includes_still_honours_engineer_rule():
    """No title filter must not become an escape hatch for the Engineer rule."""
    assert title_matches("Forward Deployed Engineer", []) is False


# --- geography ----------------------------------------------------------------

def test_sf_passes():
    assert geo_ok("San Francisco, CA") is True


def test_peninsula_is_dropped_on_the_role_path():
    assert geo_ok("San Mateo, CA") is False


def test_south_san_francisco_is_peninsula_not_sf():
    assert geo_ok("South San Francisco, CA") is False


def test_outside_bay_is_dropped():
    assert geo_ok("Austin, Texas") is False


def test_remote_is_dropped():
    assert geo_ok("Remote") is False


def test_east_bay_passes():
    assert geo_ok("Oakland, California") is True


def test_unknown_location_is_not_dropped():
    """Unknown is not the same as disqualifying; geo_gate flags rather than excludes."""
    assert geo_ok("") is True


def test_is_peninsula_helper_does_not_flag_sf():
    assert is_peninsula("San Francisco, CA") is False
    assert is_peninsula("Redwood City, CA") is True


# --- fetch_company_roles integration ------------------------------------------

@pytest.fixture
def fake_ashby(monkeypatch):
    def _install(roles):
        import tools.career_scanner.ashby as mod
        monkeypatch.setattr(mod, "fetch_ashby", lambda slug: list(roles))
    return _install


def _target(**kw):
    t = {"name": "Placeholder Co", "ats": "ashby", "slug": "placeholder",
         "role_filters": LANE}
    t.update(kw)
    return t


def test_filters_by_title_and_geography(fake_ashby):
    fake_ashby([
        {"title": "Deployment Strategist", "location": "San Francisco, CA"},
        {"title": "Deployment Strategist", "location": "San Mateo, CA"},
        {"title": "Warehouse Associate", "location": "San Francisco, CA"},
        {"title": "Forward Deployed Engineer", "location": "San Francisco, CA"},
    ])
    out = fetch_company_roles(_target())
    assert [r["title"] for r in out] == ["Deployment Strategist"]
    assert out[0]["location"] == "San Francisco, CA"


def test_geo_filter_can_be_disabled_per_target(fake_ashby):
    fake_ashby([{"title": "Deployment Strategist", "location": "Austin, Texas"}])
    assert fetch_company_roles(_target(geo_filter=False)) != []
    assert fetch_company_roles(_target()) == []


def test_zero_raw_roles_warns_loudly(fake_ashby, capsys):
    """A dead slug must not look like an empty board."""
    fake_ashby([])
    assert fetch_company_roles(_target()) == []
    assert "ZERO raw roles" in capsys.readouterr().err


def test_empty_after_filtering_does_not_warn(fake_ashby, capsys):
    """A board that legitimately has no matching roles is not a slug failure."""
    fake_ashby([{"title": "Warehouse Associate", "location": "San Francisco, CA"}])
    assert fetch_company_roles(_target()) == []
    assert "ZERO raw roles" not in capsys.readouterr().err


def test_company_name_is_overridden_from_config(fake_ashby):
    fake_ashby([{"title": "Deployment Strategist", "location": "San Francisco, CA",
                 "company": "wrong-name"}])
    assert fetch_company_roles(_target())[0]["company"] == "Placeholder Co"


# --- exclude list must not be a blanket reject -------------------------------

def test_non_matching_exclude_leaves_the_title_alone():
    """A populated exclude list must reject only what it names."""
    assert title_matches("Deployment Strategist", LANE, ["content strategist"]) is True


# --- ATS dispatch -------------------------------------------------------------

def _sf_role(title="Deployment Strategist"):
    return {"title": title, "location": "San Francisco, CA"}


def test_dispatches_to_greenhouse(monkeypatch):
    import tools.career_scanner.greenhouse as gh
    monkeypatch.setattr(gh, "fetch_greenhouse", lambda slug: [_sf_role("GH Strategist")])
    out = fetch_company_roles(_target(ats="greenhouse"))
    assert [r["title"] for r in out] == ["GH Strategist"]


def test_dispatches_to_lever(monkeypatch):
    import tools.career_scanner.lever as lv
    monkeypatch.setattr(lv, "fetch_lever", lambda slug: [_sf_role("Lever Strategist")])
    out = fetch_company_roles(_target(ats="lever"))
    assert [r["title"] for r in out] == ["Lever Strategist"]


def test_dispatches_to_generic_with_careers_url(monkeypatch):
    import tools.career_scanner.generic as gen
    monkeypatch.setattr(gen, "fetch_generic",
                        lambda url, name: [_sf_role("Generic Strategist")])
    out = fetch_company_roles(_target(ats="generic", careers_url="https://example.test/jobs"))
    assert [r["title"] for r in out] == ["Generic Strategist"]


def test_generic_without_careers_url_returns_empty_and_warns(capsys):
    assert fetch_company_roles(_target(ats="generic")) == []
    assert "No careers_url" in capsys.readouterr().err


def test_unknown_ats_returns_empty_and_warns(capsys):
    assert fetch_company_roles(_target(ats="workday")) == []
    assert "Unknown ATS" in capsys.readouterr().err


def test_parser_exception_is_caught_and_warns(monkeypatch, capsys):
    """A throwing parser must not abort the whole nightly scan."""
    import tools.career_scanner.ashby as mod

    def boom(slug):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(mod, "fetch_ashby", boom)
    assert fetch_company_roles(_target()) == []
    assert "Error fetching" in capsys.readouterr().err
