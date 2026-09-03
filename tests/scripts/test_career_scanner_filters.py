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
        # `errors` is accepted and ignored: these fakes model a SUCCESSFUL fetch.
        # The signature must match the real parser or the dispatch would break here
        # rather than in production, which is the point of pinning it.
        monkeypatch.setattr(mod, "fetch_ashby",
                            lambda slug, errors=None: list(roles))
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
    monkeypatch.setattr(gh, "fetch_greenhouse",
                        lambda slug, errors=None: [_sf_role("GH Strategist")])
    out = fetch_company_roles(_target(ats="greenhouse"))
    assert [r["title"] for r in out] == ["GH Strategist"]


def test_dispatches_to_lever(monkeypatch):
    import tools.career_scanner.lever as lv
    monkeypatch.setattr(lv, "fetch_lever",
                        lambda slug, errors=None: [_sf_role("Lever Strategist")])
    out = fetch_company_roles(_target(ats="lever"))
    assert [r["title"] for r in out] == ["Lever Strategist"]


def test_dispatches_to_generic_with_careers_url(monkeypatch):
    import tools.career_scanner.generic as gen
    monkeypatch.setattr(gen, "fetch_generic",
                        lambda url, name, errors=None: [_sf_role("Generic Strategist")])
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

    def boom(slug, errors=None):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(mod, "fetch_ashby", boom)
    assert fetch_company_roles(_target()) == []
    assert "Error fetching" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The false-zero: a failed board must not be indistinguishable from an empty one.
#
# Found by a cross-model review on 2026-09-02. Every ATS parser returns [] on HTTP
# error, so a dead slug and a genuinely empty board are the same value. scanner.py
# declared `errors = []` and never used it again -- a dead variable that never reached
# the JSON summary. A scheduled scan in which every single board 404s therefore
# reported total_fetched: 0, new_roles: 0 as clean success, with the only signal on
# stderr, in a log that install.sh deletes on every load.
# ---------------------------------------------------------------------------

def test_fetch_records_a_failure_into_a_caller_supplied_list():
    """An unknown ATS is a configuration failure, not an empty board."""
    from tools.career_scanner.scanner import fetch_company_roles
    errors = []
    out = fetch_company_roles(_target(ats="workday"), errors=errors)
    assert out == [], "return contract must not change; callers assert == []"
    assert errors, "a failed fetch recorded nothing; it is indistinguishable from empty"
    assert "workday" in repr(errors).lower() or "unknown" in repr(errors).lower()


def test_a_generic_target_with_no_careers_url_is_recorded_as_a_failure():
    """A config error is a failure, not an empty board. Without this the row is
    skipped every night and the scan still reports a clean zero -- and 24 of 45
    configured companies were silently skipped this way before 2026-09-02."""
    from tools.career_scanner.scanner import fetch_company_roles
    errors = []
    assert fetch_company_roles(_target(ats="generic"), errors=errors) == []
    assert len(errors) == 1
    assert "careers_url" in errors[0]["reason"]
    assert errors[0]["company"] == "Placeholder Co"


def test_fetch_without_an_error_list_still_works():
    """Backward compatibility: existing callers pass no errors list."""
    from tools.career_scanner.scanner import fetch_company_roles
    assert fetch_company_roles(_target(ats="workday")) == []


def test_a_genuinely_empty_board_is_not_recorded_as_an_error(fake_ashby):
    """Guard on the guard. If every empty board logged an error, the signal would be
    noise and would be ignored -- which is how the stderr warning already failed.

    NOTE 2026-09-02: this test previously passed no fake and reached the LIVE Ashby
    API, where slug 'placeholder' 404s. It asserted "no error recorded" against a real
    HTTP failure and passed only because the parser swallowed it -- the exact defect
    under test. Once the parser gained an error channel the test failed, correctly.
    """
    from tools.career_scanner.scanner import fetch_company_roles
    fake_ashby([{"title": "Warehouse Associate", "location": "San Francisco, CA"}])
    errors = []
    fetch_company_roles(_target(), errors=errors)   # filtered to empty, not failed
    assert errors == [], f"an empty-but-working board was recorded as a failure: {errors}"


# --- the parser-caught failure: the case the first false-zero fix MISSED ------
#
# The 2026-09-02 fix recorded only unknown-ATS and missing-careers_url -- CONFIG
# errors. Every parser catches HTTPError/URLError/OSError itself and returns [], so
# those never reached the wrapper's try/except and a 404'd slug still reported
# fetch_failures: 0. Commit b19caaf's message overclaimed this as fixed.

@pytest.mark.parametrize("ats,module,func", [
    ("ashby", "tools.career_scanner.ashby", "fetch_ashby"),
    ("greenhouse", "tools.career_scanner.greenhouse", "fetch_greenhouse"),
    ("lever", "tools.career_scanner.lever", "fetch_lever"),
])
def test_a_parser_caught_http_failure_reaches_the_error_list(monkeypatch, ats, module, func):
    """A 404 the PARSER swallowed must still register as a fetch failure."""
    import importlib
    mod = importlib.import_module(module)

    def dead_board(slug, errors=None):
        if errors is not None:
            errors.append({"reason": "HTTP 404"})
        return []

    monkeypatch.setattr(mod, func, dead_board)
    errors = []
    out = fetch_company_roles(_target(ats=ats), errors=errors)
    assert out == []
    assert errors, f"{ats}: a 404'd board reported a clean zero"
    assert errors[0]["company"] == "Placeholder Co"
    assert "404" in errors[0]["reason"]


def test_a_parser_that_records_then_raises_keeps_both_signals(monkeypatch):
    """The recorded failure must survive the exception, not be replaced by it."""
    import tools.career_scanner.ashby as mod

    def record_then_boom(slug, errors=None):
        if errors is not None:
            errors.append({"reason": "HTTP 404"})
        raise RuntimeError("and then the socket died")

    monkeypatch.setattr(mod, "fetch_ashby", record_then_boom)
    errors = []
    assert fetch_company_roles(_target(), errors=errors) == []
    reasons = " ".join(e["reason"] for e in errors)
    assert "404" in reasons and "RuntimeError" in reasons


@pytest.mark.parametrize("module,func,args", [
    ("tools.career_scanner.ashby", "fetch_ashby", ("slug",)),
    ("tools.career_scanner.greenhouse", "fetch_greenhouse", ("slug",)),
    ("tools.career_scanner.lever", "fetch_lever", ("slug",)),
    ("tools.career_scanner.generic", "fetch_generic", ("url", "name")),
])
def test_every_parser_accepts_the_error_channel(module, func, args):
    """Structural guard: a new or edited parser cannot silently omit `errors`.

    Without this, dropping the parameter from one parser reintroduces the false zero
    for that ATS alone -- and every other test would still pass.
    """
    import importlib
    import inspect
    fn = getattr(importlib.import_module(module), func)
    sig = inspect.signature(fn)
    assert "errors" in sig.parameters, f"{func} has no error channel"
    assert sig.parameters["errors"].default is None, (
        f"{func}'s errors parameter must be optional so existing callers keep working")
    assert len(sig.parameters) == len(args) + 1


# ---------------------------------------------------------------------------
# The seen-set: "new" must mean NEWLY SURFACED, not "not in your pipeline".
#
# Origin 2026-09-02. filter_duplicates compared only against job-pipeline.md, so any
# role the owner had not promoted was re-emitted on every daily scan. Measured: 56
# career-scan blocks in data/inbox.md, the same ~30 roles day after day, the file at
# 7,050 lines. The daily output was structurally unable to answer "what is new today",
# which was the entire question being asked of it.
# ---------------------------------------------------------------------------

def _role(company="Acme", title="Deployment Strategist", url="https://boards.example/1"):
    return {"company": company, "title": title, "url": url}


def test_a_role_is_new_the_first_time_and_standing_after(tmp_path):
    from tools.career_scanner.dedup import split_new_and_standing, load_seen, save_seen
    roles = [_role()]
    seen = load_seen(tmp_path)
    new, standing = split_new_and_standing(roles, seen)
    assert len(new) == 1 and not standing
    save_seen(tmp_path, seen)

    seen2 = load_seen(tmp_path)
    new2, standing2 = split_new_and_standing(roles, seen2)
    assert not new2, "the same role surfaced as new twice; this is the 56-block bug"
    assert len(standing2) == 1, "a seen role must still be reported, just not as new"


def test_the_key_is_the_posting_url_not_the_company(tmp_path):
    """Two different roles at one company must not collapse into one another."""
    from tools.career_scanner.dedup import split_new_and_standing, load_seen, save_seen
    seen = load_seen(tmp_path)
    split_new_and_standing([_role(url="https://boards.example/1")], seen)
    save_seen(tmp_path, seen)
    seen = load_seen(tmp_path)
    new, _ = split_new_and_standing([_role(title="Engagement Manager",
                                           url="https://boards.example/2")], seen)
    assert len(new) == 1, "a second distinct posting was swallowed by the first"


def test_a_role_with_no_url_still_dedupes_on_company_and_title(tmp_path):
    """Not every source supplies a stable posting id; the fallback must still work."""
    from tools.career_scanner.dedup import split_new_and_standing, load_seen, save_seen
    r = {"company": "Acme", "title": "Deployment Strategist", "url": ""}
    seen = load_seen(tmp_path)
    split_new_and_standing([r], seen)
    save_seen(tmp_path, seen)
    new, standing = split_new_and_standing([r], load_seen(tmp_path))
    assert not new and len(standing) == 1


def test_first_seen_is_stamped_so_age_can_be_shown(tmp_path):
    from tools.career_scanner.dedup import split_new_and_standing, load_seen
    new, _ = split_new_and_standing([_role()], load_seen(tmp_path))
    assert new[0].get("first_seen"), "no first_seen stamp; the surface cannot show age"


def test_a_missing_seen_file_is_an_empty_set_not_a_crash(tmp_path):
    """First run on a fresh machine, and the nightly job must not die on it."""
    from tools.career_scanner.dedup import load_seen
    assert load_seen(tmp_path / "nonexistent") == {}
