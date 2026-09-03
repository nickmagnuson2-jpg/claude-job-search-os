"""Each ATS parser must SIGNAL a swallowed failure, not just return [].

WHY THIS FILE EXISTS
--------------------
2026-09-02. A first false-zero fix added an `errors` out-parameter to
`fetch_company_roles`, and its commit message claimed the false zero was closed. It
was not. Every parser catches `HTTPError` / `URLError` / `OSError` ITSELF and returns
`[]`, so those failures never reached the wrapper's `try/except` and a dead slug
returning 404 still reported `fetch_failures: 0`. Only unknown-ATS and missing
careers_url were recorded -- configuration errors, not runtime ones.

The wrapper tests in test_career_scanner_filters.py monkeypatch the parsers, so they
cannot see a regression INSIDE one. These call the real parser with only the network
stubbed, which is the only level at which the swallow is visible.
"""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner import ashby, greenhouse, lever  # noqa: E402

PARSERS = [
    pytest.param(ashby, "fetch_ashby", id="ashby"),
    pytest.param(greenhouse, "fetch_greenhouse", id="greenhouse"),
    pytest.param(lever, "fetch_lever", id="lever"),
]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, mod, raiser=None, payload=None):
    def urlopen(req, timeout=None):
        if raiser is not None:
            raise raiser
        return _Resp(json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)


@pytest.mark.parametrize("mod,func", PARSERS)
def test_http_error_is_recorded_not_swallowed(monkeypatch, mod, func):
    _stub(monkeypatch, mod,
          raiser=urllib.error.HTTPError("u", 404, "Not Found", {}, None))
    errors = []
    assert getattr(mod, func)("dead-slug", errors=errors) == []
    assert errors, "a 404 was swallowed; a dead slug reads as an empty board"
    assert "404" in errors[0]["reason"]


@pytest.mark.parametrize("mod,func", PARSERS)
def test_network_error_is_recorded(monkeypatch, mod, func):
    _stub(monkeypatch, mod, raiser=urllib.error.URLError("dns is down"))
    errors = []
    assert getattr(mod, func)("slug", errors=errors) == []
    assert errors and "URLError" in errors[0]["reason"]


@pytest.mark.parametrize("mod,func,bad,expect", [
    # Each case asserts the SPECIFIC reason, not merely that something was recorded.
    # A generic "errors is non-empty" assertion survives collapsing every distinct
    # failure into one branch, which is what makes a failure report unactionable.
    (ashby, "fetch_ashby", ["not", "an", "object"], "expected a JSON object"),
    (ashby, "fetch_ashby", {"totally": "different"}, "no 'jobs' list"),
    (ashby, "fetch_ashby", {"jobs": "not a list"}, "no 'jobs' list"),
    (greenhouse, "fetch_greenhouse", "a bare string", "expected a JSON object"),
    (greenhouse, "fetch_greenhouse", {"totally": "different"}, "no 'jobs' list"),
    (lever, "fetch_lever", {"ok": False, "error": "unknown account"}, "unknown account"),
    (lever, "fetch_lever", {"jobs": []}, "expected a JSON list"),
])
def test_wrong_shaped_payload_is_recorded(monkeypatch, mod, func, bad, expect):
    """A 200 carrying the wrong thing is a failure too, not an empty board."""
    _stub(monkeypatch, mod, payload=bad)
    errors = []
    assert getattr(mod, func)("slug", errors=errors) == []
    assert errors, f"{func} accepted a malformed payload as an empty board"
    assert expect in errors[0]["reason"], (
        f"{func} recorded '{errors[0]['reason']}', which does not identify the fault")


@pytest.mark.parametrize("mod,func", PARSERS)
@pytest.mark.parametrize("raiser,marker", [
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "404"),
    (urllib.error.URLError("dns is down"), "dns is down"),
])
def test_a_failure_is_also_announced_on_stderr(monkeypatch, mod, func, capsys,
                                               raiser, marker):
    """The nightly job's log is the only trace when the queue write also fails."""
    _stub(monkeypatch, mod, raiser=raiser)
    getattr(mod, func)("dead-slug", errors=[])
    err = capsys.readouterr().err
    assert "dead-slug" in err and marker in err


# --- pre-existing lever field logic, surfaced by the same mutation run ---------

def test_lever_converts_the_millisecond_timestamp(monkeypatch):
    """published_at drives the recency tiebreaker in the queue ordering, so a role
    whose date silently becomes "" sorts as the oldest thing on the list."""
    _stub(monkeypatch, lever, payload=[{"text": "Deployment Strategist",
                                        "createdAt": 1788307200000}])
    assert lever.fetch_lever("slug")[0]["published_at"] == "2026-09-02"


def test_lever_leaves_a_non_numeric_timestamp_empty(monkeypatch):
    _stub(monkeypatch, lever, payload=[{"text": "X", "createdAt": "yesterday"}])
    assert lever.fetch_lever("slug")[0]["published_at"] == ""


def test_greenhouse_reads_remote_from_the_location_name(monkeypatch):
    _stub(monkeypatch, greenhouse, payload={"jobs": [
        {"title": "A", "location": {"name": "Remote - US"}},
        {"title": "B", "location": {"name": "San Francisco"}},
    ]})
    assert [r["remote"] for r in greenhouse.fetch_greenhouse("slug")] == [True, False]


def test_lever_reads_remote_from_all_locations(monkeypatch):
    _stub(monkeypatch, lever, payload=[
        {"text": "A", "categories": {"allLocations": ["Remote - US"]}},
        {"text": "B", "categories": {"allLocations": ["San Francisco"]}},
    ])
    assert [r["remote"] for r in lever.fetch_lever("slug")] == [True, False]


@pytest.mark.parametrize("mod,func,ok_payload", [
    (ashby, "fetch_ashby", {"jobs": []}),
    (greenhouse, "fetch_greenhouse", {"jobs": []}),
    (lever, "fetch_lever", []),
])
def test_a_real_empty_board_records_nothing(monkeypatch, mod, func, ok_payload):
    """Guard on the guard: if every empty board logged a failure the signal is noise."""
    _stub(monkeypatch, mod, payload=ok_payload)
    errors = []
    assert getattr(mod, func)("slug", errors=errors) == []
    assert errors == [], f"{func} called a working empty board a failure: {errors}"


@pytest.mark.parametrize("mod,func", PARSERS)
def test_omitting_the_error_list_still_works(monkeypatch, mod, func):
    """Backward compatibility: the out-parameter is optional."""
    _stub(monkeypatch, mod,
          raiser=urllib.error.HTTPError("u", 500, "boom", {}, None))
    assert getattr(mod, func)("slug") == []
