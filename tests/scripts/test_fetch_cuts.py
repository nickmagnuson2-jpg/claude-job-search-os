"""Roles cut at fetch are listed with the reason, never silently gone (scan-drops-1106).

Until this branch, fetch_company_roles dropped every role whose title missed a target's
role_filters include-list (29 targets), hit a role_excludes term, had an engineer head
noun, or failed the location gate, and nothing recorded it. A founding Chief of Staff
or BizOps title on a lane-filtered board never reached any list (Codex 940.F1 / Grok
941.F1). The filters themselves are unchanged: what changes is that each cut is
recorded, with its rule and reason, into the role queue the nightly scan writes.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.career_scanner import scanner  # noqa: E402
from tools.career_scanner.scanner import fetch_company_roles, title_cut_reason, title_matches  # noqa: E402

LANE = ["strategist", "deployment"]
EXCL = ["account executive"]


@pytest.mark.parametrize("title,includes,excludes,allow,reason", [
    ("Deployment Strategist", LANE, EXCL, False, None),
    ("Founding Recruiter", LANE, EXCL, False, "title in out-of-lane family 'recruiting'"),
    # title-gate-1118: outside the includes but in no out-of-lane family -> passes to the scorer.
    ("Founding Chief of Staff", LANE, EXCL, False, None),
    ("Account Executive, Deployment", LANE, EXCL, False, "title matches role_excludes 'account executive'"),
    ("Forward Deployed Engineer", LANE, EXCL, False, "engineer title"),
    # allow_engineer lifts only the engineer rule; the out-of-lane families still apply.
    ("Sales Engineer", LANE, EXCL, True, "title in out-of-lane family 'sales'"),
    ("Deployment Engineer", LANE, EXCL, True, None),
    ("Founding Chief of Staff", [], [], False, None),
    ("Pre-Sales Lead, Deployment Engineering", LANE, EXCL, False, None),
])
def test_title_cut_reason(title, includes, excludes, allow, reason):
    assert title_cut_reason(title, includes, excludes, allow) == reason
    # One rule, two readings: title_matches is exactly "no cut reason".
    assert title_matches(title, includes, excludes, allow) is (reason is None)


@pytest.fixture
def fake_ashby(monkeypatch):
    def _install(roles):
        import tools.career_scanner.ashby as mod
        monkeypatch.setattr(mod, "fetch_ashby", lambda slug, errors=None: [dict(r) for r in roles])
    return _install


def _target(**kw):
    t = {"name": "Placeholder Co", "ats": "ashby", "slug": "placeholder",
         "role_filters": LANE, "role_excludes": EXCL}
    t.update(kw)
    return t


ROLES = [
    {"title": "Deployment Strategist", "location": "San Francisco, CA", "url": "u1"},
    {"title": "Founding Recruiter", "location": "San Francisco, CA", "url": "u2"},
    {"title": "Deployment Strategist", "location": "San Mateo, CA", "url": "u3"},
    {"title": "Forward Deployed Engineer", "location": "New York, NY", "url": "u4"},
]


def test_every_cut_is_recorded_with_rule_and_reason(fake_ashby):
    fake_ashby(ROLES)
    cuts = []
    out = fetch_company_roles(_target(), cuts=cuts)
    assert [r["url"] for r in out] == ["u1"]
    assert cuts == [
        {"company": "Placeholder Co", "title": "Founding Recruiter", "location": "San Francisco, CA",
         "url": "u2", "rule": "title_filter", "reason": "title in out-of-lane family 'recruiting'"},
        {"company": "Placeholder Co", "title": "Deployment Strategist", "location": "San Mateo, CA",
         "url": "u3", "rule": "location", "reason": "location: peninsula"},
        {"company": "Placeholder Co", "title": "Forward Deployed Engineer", "location": "New York, NY",
         "url": "u4", "rule": "title_filter", "reason": "engineer title"},
    ]


def test_no_cuts_list_keeps_the_old_contract(fake_ashby):
    fake_ashby(ROLES)
    assert [r["url"] for r in fetch_company_roles(_target())] == ["u1"]


def test_geo_filter_off_records_no_location_cut(fake_ashby):
    fake_ashby(ROLES)
    cuts = []
    out = fetch_company_roles(_target(geo_filter=False), cuts=cuts)
    assert [r["url"] for r in out] == ["u1", "u3"]
    assert [c["rule"] for c in cuts] == ["title_filter", "title_filter"]


def test_the_queue_payload_carries_the_cuts():
    cuts = [{"company": "A", "title": "T", "location": "", "url": "", "rule": "title_filter",
             "reason": "engineer title"}]
    p = scanner._queue_payload([], [], [], [], cuts)
    assert p["cut_at_fetch_count"] == 1
    assert p["cut_at_fetch"] == cuts
    assert scanner._queue_payload([], [], [], [])["cut_at_fetch_count"] == 0


def test_scan_all_targets_passes_cuts_to_the_queue_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "load_targets", lambda root: [_target()])

    def fake_fetch(target, errors=None, cuts=None):
        cuts.append({"company": "Placeholder Co", "title": "Founding Chief of Staff", "location": "",
                     "url": "u2", "rule": "title_filter", "reason": "title not in the target's role_filters"})
        return []
    monkeypatch.setattr(scanner, "fetch_company_roles", fake_fetch)
    (tmp_path / "data").mkdir()
    s = scanner.scan_all_targets(tmp_path)
    assert s["cut_at_fetch"] == 1
    payload = scanner.read_queue_payload(tmp_path)
    assert payload["cut_at_fetch_count"] == 1
    assert payload["cut_at_fetch"][0]["title"] == "Founding Chief of Staff"


def test_role_queue_read_reports_the_cuts_by_reason(tmp_path):
    import tools.role_queue_read as rq
    cuts = [{"company": "A", "title": "T1", "location": "", "url": "", "rule": "title_filter",
             "reason": "engineer title"},
            {"company": "A", "title": "T2", "location": "", "url": "", "rule": "title_filter",
             "reason": "engineer title"},
            {"company": "B", "title": "T3", "location": "Austin", "url": "", "rule": "location",
             "reason": "location: excluded"}]
    scanner.write_queue_payload(tmp_path, scanner._queue_payload([], [], [], [], cuts))
    out = rq.read_queue(tmp_path)
    assert out["cut_at_fetch_count"] == 3
    assert out["cut_at_fetch_by_reason"] == {"engineer title": 2, "location: excluded": 1}


def test_role_queue_read_on_an_old_queue_reports_zero_cuts(tmp_path):
    import tools.role_queue_read as rq
    payload = scanner._queue_payload([], [], [], [])
    del payload["cut_at_fetch_count"], payload["cut_at_fetch"]
    scanner.write_queue_payload(tmp_path, payload)
    out = rq.read_queue(tmp_path)
    assert out["cut_at_fetch_count"] == 0 and out["cut_at_fetch_by_reason"] == {}


# A corrupt cut list is reported as unreadable, never as a smaller count (same class as
# title_cut_ruling's parked P1; Codex residual note on title-cut-guard-1121, 2026-11-21).

_GOOD_CUT = {"company": "A", "title": "T1", "location": "", "url": "", "rule": "title_filter",
             "reason": "engineer title"}


def _read_with(tmp_path, **override):
    import tools.role_queue_read as rq
    payload = scanner._queue_payload([], [], [], [], [_GOOD_CUT])
    payload.update(override)
    scanner.write_queue_payload(tmp_path, payload)
    return rq.read_queue(tmp_path)


def test_role_queue_read_reports_no_cut_error_on_a_sound_queue(tmp_path):
    out = _read_with(tmp_path)
    assert out["cut_at_fetch_error"] is None and out["cut_at_fetch_count"] == 1


@pytest.mark.parametrize("bad", ["junk", None, 3, ["x"]])
def test_role_queue_read_a_non_object_cut_is_an_error_not_a_lower_count(tmp_path, bad):
    out = _read_with(tmp_path, cut_at_fetch=[_GOOD_CUT, bad], cut_at_fetch_count=2)
    assert out["cut_at_fetch_count"] is None and out["cut_at_fetch_by_reason"] is None
    assert "1 of 2 cut_at_fetch entries are not an object" in out["cut_at_fetch_error"]


@pytest.mark.parametrize("bad", [{"a": 1}, "x", None, 5])
def test_role_queue_read_a_cut_list_that_is_not_a_list_is_an_error(tmp_path, bad):
    out = _read_with(tmp_path, cut_at_fetch=bad)
    assert out["cut_at_fetch_count"] is None
    assert "cut_at_fetch is not a list" in out["cut_at_fetch_error"]


@pytest.mark.parametrize("count", [0, 2, "1", True, None])
def test_role_queue_read_a_count_that_disagrees_is_an_error(tmp_path, count):
    out = _read_with(tmp_path, cut_at_fetch_count=count)
    assert out["cut_at_fetch_count"] is None and out["cut_at_fetch_by_reason"] is None
    assert "cut_at_fetch_count" in out["cut_at_fetch_error"]


def test_role_queue_read_an_old_queue_has_no_cut_error(tmp_path):
    import tools.role_queue_read as rq
    payload = scanner._queue_payload([], [], [], [])
    del payload["cut_at_fetch_count"], payload["cut_at_fetch"]
    scanner.write_queue_payload(tmp_path, payload)
    assert rq.read_queue(tmp_path)["cut_at_fetch_error"] is None


@pytest.mark.parametrize("override", [
    {}, {"cut_at_fetch": [_GOOD_CUT, "junk"], "cut_at_fetch_count": 2},
    {"cut_at_fetch_count": 0}, {"cut_at_fetch_count": "1"}, {"cut_at_fetch_count": False},
    {"cut_at_fetch": {"a": 1}}, {"cut_at_fetch": None},
])
def test_both_cut_readers_agree_on_whether_the_list_is_corrupt(tmp_path, override):
    # One check, two readers (title_cut_ruling for the page, role_queue_read for /standup):
    # they must never disagree on whether a queue's cut list is usable.
    from tools import title_cut_ruling as tcr
    out = _read_with(tmp_path, **override)
    loaded = tcr.load_cuts(tmp_path)
    assert (out["cut_at_fetch_error"] is None) == (loaded["error"] is None)
    if loaded["error"]:
        assert out["cut_at_fetch_error"] in loaded["error"]


def test_both_cut_readers_call_the_one_shared_check(tmp_path, monkeypatch):
    import tools.role_queue_read as rq
    from tools import title_cut_ruling as tcr
    monkeypatch.setattr(rq, "cut_list_problem", lambda p: "sentinel")
    monkeypatch.setattr(tcr, "cut_list_problem", lambda p: "sentinel")
    out = _read_with(tmp_path)
    assert out["cut_at_fetch_error"] == "sentinel"
    assert tcr.load_cuts(tmp_path)["error"] == "corrupt role queue: sentinel"
    # A non-list too: neither reader may answer it before the shared check runs.
    out = _read_with(tmp_path, cut_at_fetch={"a": 1})
    assert out["cut_at_fetch_error"] == "sentinel"
    assert tcr.load_cuts(tmp_path)["error"] == "corrupt role queue: sentinel"
