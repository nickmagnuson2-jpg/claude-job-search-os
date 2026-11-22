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
import http.client
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner import ashby, greenhouse, lever, yc  # noqa: E402

PARSERS = [
    pytest.param(ashby, "fetch_ashby", id="ashby"),
    pytest.param(greenhouse, "fetch_greenhouse", id="greenhouse"),
    pytest.param(lever, "fetch_lever", id="lever"),
    pytest.param(yc, "fetch_yc", id="yc"),
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
    (http.client.InvalidURL("bad url text"), "bad url text"),
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


# --- 2026-11-18: a slug the URL cannot carry, and a live Ashby board whose API 404s ---
# Origin: output/funnel-intake/111826-noboard-pilot/report.md (a Lever slug with a space
# raised http.client.InvalidURL past the parser) and a second no-board scan report that day
# (Ashby's posting API returned 404 for a board whose hosted page listed 11 roles).

import http.client  # noqa: E402

API_PREFIX = {
    "fetch_ashby": "https://api.ashbyhq.com/posting-api/job-board/",
    "fetch_greenhouse": "https://boards-api.greenhouse.io/v1/boards/",
    "fetch_lever": "https://api.lever.co/v0/postings/",
    "fetch_yc": "https://www.ycombinator.com/companies/",
}
EMPTY_OK = {"fetch_ashby": {"jobs": []}, "fetch_greenhouse": {"jobs": []}, "fetch_lever": [],
            "fetch_yc": ""}  # yc reads HTML; only the request URL is asserted here


def _capture(monkeypatch, mod, payload):
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req.full_url)
        return _Resp(json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    return seen


@pytest.mark.parametrize("mod,func", PARSERS)
@pytest.mark.parametrize("slug,in_url", [
    ("Acme AI", "Acme%20AI"),        # a space: unquoted, http.client raises InvalidURL
    ("Acme%20AI", "Acme%20AI"),      # copied from a board URL: must not become %2520
    ("acme/../x", "acme%2F..%2Fx"),  # a slash cannot change the path
])
def test_the_slug_is_quoted_once_in_the_request_url(monkeypatch, mod, func, slug, in_url):
    seen = _capture(monkeypatch, mod, EMPTY_OK[func])
    getattr(mod, func)(slug, errors=[])
    assert seen and seen[0].startswith(API_PREFIX[func] + in_url), seen


@pytest.mark.parametrize("mod,func", PARSERS)
def test_an_invalid_url_is_recorded_not_raised(monkeypatch, mod, func):
    _stub(monkeypatch, mod, raiser=http.client.InvalidURL("URL can't contain control characters"))
    errors = []
    assert getattr(mod, func)("slug", errors=errors) == []
    assert errors and "InvalidURL" in errors[0]["reason"]


def test_lever_keeps_the_raw_slug_as_the_company(monkeypatch):
    _capture(monkeypatch, lever, [{"text": "Deployment Strategist", "categories": {}}])
    roles = lever.fetch_lever("Acme AI", errors=[])
    assert [r["company"] for r in roles] == ["Acme AI"]


def _hosted_html(page_slug, n_postings, marker="window.__appData = "):
    app = {"organization": {"name": "Acme", "hostedJobsPageSlug": page_slug},
           "jobBoard": {"jobPostings": [{"id": str(i), "title": f"Role {i}"}
                                        for i in range(n_postings)]}}
    return (f"<html><script>{marker}{json.dumps(app)};\n</script></html>").encode("utf-8")


def _ashby_404_then(monkeypatch, hosted):
    """The posting API 404s; the hosted page answers with `hosted` (bytes or exception)."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url.startswith(ashby.HOSTED):
            # The hosted page is a browser page; the scanner names itself on every request.
            assert "career-scanner" in (req.get_header("User-agent") or "")
        if req.full_url.startswith("https://api.ashbyhq.com/"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        if isinstance(hosted, Exception):
            raise hosted
        return _Resp(hosted)
    monkeypatch.setattr(ashby.urllib.request, "urlopen", urlopen)
    return calls


def test_ashby_404_with_a_live_hosted_board_is_marked_live(monkeypatch):
    calls = _ashby_404_then(monkeypatch, _hosted_html("acme", 3))
    errors = []
    assert ashby.fetch_ashby("acme", errors=errors) == []
    assert calls == ["https://api.ashbyhq.com/posting-api/job-board/acme",
                     "https://jobs.ashbyhq.com/acme"]
    assert len(errors) == 1 and errors[0].get("live_board") is True
    assert "3 postings" in errors[0]["reason"]
    assert "https://jobs.ashbyhq.com/acme" in errors[0]["reason"]


@pytest.mark.parametrize("hosted", [
    _hosted_html("someone-else", 3),          # page belongs to another slug
    urllib.error.HTTPError("u", 404, "Not Found", {}, None),   # no hosted board either
], ids=["other-slug", "page-404"])
def test_ashby_404_with_a_conclusively_absent_hosted_board_stays_a_plain_404(monkeypatch, hosted):
    _ashby_404_then(monkeypatch, hosted)
    errors = []
    assert ashby.fetch_ashby("acme", errors=errors) == []
    assert errors == [{"reason": "HTTP 404"}]


@pytest.mark.parametrize("hosted,why", [
    (b"<html>no app data here</html>", "no __appData"),
    (_hosted_html("acme", 3, marker="window.__other = "), "no __appData"),
    (b"<script>window.__appData = {not json</script>", "not JSON"),
    (urllib.error.URLError("dns is down"), "URLError"),
    (urllib.error.HTTPError("u", 503, "Unavailable", {}, None), "HTTP 503"),
    (TimeoutError("timed out"), "TimeoutError"),
    (b'<script>window.__appData = {"organization": {"name": "Acme"}, '
     b'"jobBoard": {"jobPostings": [{"id": "1"}]}};</script>', "no organization slug"),
    (b'<script>window.__appData = {"organization": null};</script>', "no organization slug"),
    (b'<script>window.__appData = [1, 2];</script>', "not an object"),
    (b'<script>window.__appData = {"organization": {"hostedJobsPageSlug": "acme"}};</script>',
     "no jobPostings list"),
], ids=["no-appdata", "wrong-marker", "bad-json", "page-neterr", "page-503", "page-timeout",
        "no-page-slug", "null-org", "appdata-list", "no-postings-list"])
def test_a_failed_live_board_check_is_not_proof_of_absence(monkeypatch, hosted, why):
    # Review 2026-11-18 (Codex F2): a check that could not run must not read as "no board".
    from tools import company_queue as cq
    _ashby_404_then(monkeypatch, hosted)
    errors = []
    assert ashby.fetch_ashby("acme", errors=errors) == []
    assert len(errors) == 1 and errors[0]["not_found"] is False
    assert why in errors[0]["reason"] and "live-board check failed" in errors[0]["reason"]
    assert cq._is_not_found(errors[0]) is False


def test_a_live_board_with_zero_postings_is_named_and_counted(monkeypatch):
    # Review 2026-11-18 (Codex F1), reversed by Nick 2026-11-19 ("empty boards can stay
    # scan targets, but I want them to bring it up"): the fetcher reports the count, and
    # detect_board turns a live board with 0 postings into an empty board.
    from tools import company_queue as cq
    _ashby_404_then(monkeypatch, _hosted_html("acme", 0))
    errors = []
    ashby.fetch_ashby("acme", errors=errors)
    assert errors[0]["live_board"] is True and "(0 postings)" in errors[0]["reason"]
    assert errors[0]["postings"] == 0
    assert cq._is_not_found(errors[0]) is True


def test_a_live_board_with_postings_reports_its_count(monkeypatch):
    _ashby_404_then(monkeypatch, _hosted_html("acme", 3))
    errors = []
    ashby.fetch_ashby("acme", errors=errors)
    assert errors[0]["postings"] == 3


@pytest.mark.parametrize("marker", ["window.__appData=", "window.__appData =\n  "])
def test_the_app_data_assignment_is_found_whatever_the_spacing(monkeypatch, marker):
    # Review 2026-11-18 (Codex F3).
    _ashby_404_then(monkeypatch, _hosted_html("acme", 2, marker=marker))
    errors = []
    ashby.fetch_ashby("acme", errors=errors)
    assert errors[0].get("live_board") is True and "(2 postings)" in errors[0]["reason"]


def test_ashby_only_checks_the_hosted_page_on_a_404(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)
    monkeypatch.setattr(ashby.urllib.request, "urlopen", urlopen)
    errors = []
    ashby.fetch_ashby("acme", errors=errors)
    assert calls == ["https://api.ashbyhq.com/posting-api/job-board/acme"]
    assert errors == [{"reason": "HTTP 503"}]


def test_the_compensation_variant_gets_the_same_quoting_and_live_check(monkeypatch):
    # Both public Ashby functions go through _fetch_board; this pins that they still do.
    calls = _ashby_404_then(monkeypatch, _hosted_html("Acme AI", 2))
    errors = []
    assert ashby.fetch_ashby_with_compensation("Acme AI", errors=errors) == []
    assert calls[0] == ("https://api.ashbyhq.com/posting-api/job-board/Acme%20AI"
                        "?includeCompensation=true")
    assert calls[1] == "https://jobs.ashbyhq.com/Acme%20AI"
    assert errors[0].get("live_board") is True and "2 postings" in errors[0]["reason"]


def test_a_live_board_404_without_an_error_list_returns_quietly(monkeypatch):
    # The error channel is optional; a caller that passes none still gets [] back.
    _ashby_404_then(monkeypatch, _hosted_html("acme", 3))
    assert ashby.fetch_ashby("acme") == []
