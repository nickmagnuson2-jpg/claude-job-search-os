"""tools/career_scanner/yc.py: YC company job pages parsed into the shared role shape.

WHY THIS FILE EXISTS
--------------------
In one overnight run, most in-lane roles found that night at companies with no ATS
board came from YC job pages, and no scanner read them. A YC page embeds its data as HTML-escaped
JSON in a `data-page` attribute: the company jobs page carries `jobPostings` (no descriptions), each
job page carries `job.description`. The fixtures keep the real page SHAPE (component names, keys,
nesting) with synthetic values: placeholder companies and people, round salaries, generic titles,
batch "X00", and only the ages the tests assert.

The false-zero rule from test_parser_error_channel.py applies: a dead slug, a network error or
a page whose shape changed must land in `errors`, never read as a live board with no openings.
"""
import io
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner import yc  # noqa: E402

FIX = REPO_ROOT / "tests" / "fixtures" / "yc"
BOARD_A = (FIX / "board-acme-dev.html").read_text(encoding="utf-8")
BOARD_H = (FIX / "board-example-co.html").read_text(encoding="utf-8")
JOB_H2 = (FIX / "job-example-co-002.html").read_text(encoding="utf-8")

SHARED_KEYS = {"title", "company", "department", "team", "location", "remote",
               "employment_type", "url", "apply_url", "published_at",
               "description_plain", "ats"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _route(monkeypatch, pages: dict, fail: dict | None = None):
    """Stub urlopen: URL -> HTML body, or URL -> exception in `fail`. Unknown URL -> 404."""
    fail = fail or {}
    seen = []

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        seen.append(url)
        if url in fail:
            raise fail[url]
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return _Resp(pages[url].encode("utf-8"))
    monkeypatch.setattr(yc.urllib.request, "urlopen", urlopen)
    return seen


BASE = "https://www.ycombinator.com"


def _job_page(role: dict) -> str:
    """The job-page fixture re-pointed at `role`, so each posting has its own page."""
    import html as _html
    import json as _json
    import re as _re
    m = _re.search(r'data-page="([^"]*)"', JOB_H2)
    d = _json.loads(_html.unescape(m.group(1)))
    d["props"]["job"]["url"] = role["url"].replace(BASE, "")
    d["props"]["job"]["title"] = role["title"]
    # Grok r1 F1: a per-posting description and manager, so a copy onto the wrong role shows.
    d["props"]["job"]["description"] = f"# About\n\nPosting text for {role['title']} only."
    d["props"]["job"]["hiringManager"] = {"first_name": "Manager", "last_name": role["title"].split()[0]}
    return JOB_H2[:m.start(1)] + _html.escape(_json.dumps(d), quote=True) + JOB_H2[m.end(1):]


def _site(board_html: str, slug: str) -> dict:
    """URL -> page for a whole company: the board plus one matching page per posting."""
    pages = {f"{BASE}/companies/{slug}/jobs": board_html}
    pages.update({r["url"]: _job_page(r) for r in yc.parse_board_html(board_html, slug, [])})
    return pages


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    """Pacing and 429 waits are real sleeps in production; tests that check them re-stub."""
    monkeypatch.setattr(yc, "_sleep", lambda s: None)


# ------------------------------------------------------------------ board parsing (pure)

def test_board_parses_every_posting_into_the_shared_role_shape():
    errors = []
    roles = yc.parse_board_html(BOARD_A, "acme-dev", errors)
    assert errors == []
    assert len(roles) == 6
    for r in roles:
        assert SHARED_KEYS <= set(r), SHARED_KEYS - set(r)
        assert r["ats"] == "yc"
        assert r["company"] == "acme-dev"
        assert r["url"].startswith(BASE + "/companies/acme-dev/jobs/")


def test_board_fields_map_from_the_yc_json():
    roles = yc.parse_board_html(BOARD_A, "acme-dev", [])
    ds = next(r for r in roles if r["title"] == "Field Strategist")
    assert ds["location"] == "San Francisco, CA, US"
    assert ds["remote"] is False
    assert ds["employment_type"] == "Full-time"
    assert ds["salary_range"] == "$95K - $145K"
    assert ds["created_age"] == "21 days"
    assert ds["created_age_days"] == 21
    assert ds["last_active"] is None
    assert ds["stale"] is False
    # YC exposes only a relative age, so no absolute date is invented.
    assert ds["published_at"] == ""
    assert ds["description_plain"] == ""


def test_old_listing_with_old_activity_is_marked_stale():
    roles = yc.parse_board_html(BOARD_H, "example-co", [])
    ds = next(r for r in roles if r["title"] == "Field Strategist")
    assert ds["created_age_days"] == 210          # "7 months"
    assert ds["last_active_days"] == 90           # "about 3 months"
    assert ds["stale"] is True
    old = next(r for r in roles if r["title"] == "Ops Strategist")
    assert old["created_age_days"] == 330 and old["last_active"] is None
    assert old["stale"] is True


def test_hiring_manager_is_empty_when_the_board_omits_it():
    roles = yc.parse_board_html(BOARD_H, "example-co", [])
    assert all(r["hiring_manager"] == "" for r in roles)


def test_job_page_fills_the_hiring_manager(monkeypatch):
    _route(monkeypatch, _site(BOARD_H, "example-co"))
    roles = yc.fetch_yc_detailed("example-co", errors=[])
    assert all(r["hiring_manager"] == f"Manager {r['title'].split()[0]}" for r in roles)


# ------------------------------------------------------------------ ages and staleness

@pytest.mark.parametrize("text,days", [
    ("2 days", 2), ("1 day", 1), ("19 days", 19), ("29 days", 29),
    ("about 1 month", 30), ("about 2 months", 60), ("9 months", 270), ("12 months", 360),
    ("about 1 year", 365), ("over 1 year", 365), ("almost 2 years", 730), ("3 years", 1095),
    ("about 3 hours", 0), ("less than a minute", 0), ("1 minute", 0),
    (None, None), ("", None), ("None", None), ("soon", None),
])
def test_age_days(text, days):
    assert yc.age_days(text) == days


@pytest.mark.parametrize("created,last,stale", [
    (19, None, False),      # fresh, never active: not stale
    (149, None, False),     # just under the age line
    (150, None, True),      # old and no activity
    (270, 60, True),        # old, last active 2 months ago
    (270, 29, False),       # old but recently active
    (270, 30, True),        # activity exactly at the line counts as old
    (None, None, False),    # unknown age is not evidence of staleness
])
def test_is_stale(created, last, stale):
    assert yc.is_stale(created, last) is stale


# ------------------------------------------------------------------ fetch: descriptions

def test_fetch_reads_each_job_page_for_its_description(monkeypatch):
    _route(monkeypatch, _site(BOARD_H, "example-co"))
    errors = []
    roles = yc.fetch_yc("example-co", errors=errors)
    assert errors == []
    ds = next(r for r in roles if r["title"] == "Field Strategist")
    assert ds["description_plain"] == "About\n\nPosting text for Field Strategist only."
    assert "#" not in ds["description_plain"].split("\n")[0]  # markdown heading marks stripped


def test_fetch_without_descriptions_reads_only_the_board(monkeypatch):
    seen = _route(monkeypatch, {BASE + "/companies/acme-dev/jobs": BOARD_A})
    errors = []
    roles = yc.fetch_yc("acme-dev", errors=errors, descriptions=False)
    assert errors == [] and len(roles) == 6
    assert seen == [BASE + "/companies/acme-dev/jobs"]


def test_a_failed_job_page_is_recorded_and_the_role_is_kept(monkeypatch):
    roles0 = yc.parse_board_html(BOARD_H, "example-co", [])
    pages = _site(BOARD_H, "example-co")
    bad = roles0[0]["url"]
    del pages[bad]
    _route(monkeypatch, pages)
    errors = []
    roles = yc.fetch_yc("example-co", errors=errors)
    assert len(roles) == len(roles0)
    assert len(errors) == 1
    assert "404" in errors[0]["reason"] and bad in errors[0]["reason"]
    assert next(r for r in roles if r["url"] == bad)["description_plain"] == ""


def test_job_page_without_a_job_object_is_recorded(monkeypatch):
    roles0 = yc.parse_board_html(BOARD_H, "example-co", [])
    pages = _site(BOARD_H, "example-co")
    pages[roles0[1]["url"]] = BOARD_H  # parses, but is the jobs page, not a job page
    _route(monkeypatch, pages)
    errors = []
    yc.fetch_yc("example-co", errors=errors)
    assert len(errors) == 1 and "not a YC job page" in errors[0]["reason"]


# ------------------------------------------------------------------ fetch: false-zero channel

def test_dead_slug_404_is_recorded_not_swallowed(monkeypatch):
    _route(monkeypatch, {})
    errors = []
    assert yc.fetch_yc("dead-slug", errors=errors) == []
    assert len(errors) == 1 and "HTTP 404" in errors[0]["reason"]


def test_network_error_is_recorded(monkeypatch):
    url = BASE + "/companies/slug/jobs"
    _route(monkeypatch, {}, fail={url: urllib.error.URLError("dns is down")})
    errors = []
    assert yc.fetch_yc("slug", errors=errors) == []
    assert errors and "URLError" in errors[0]["reason"]


def test_os_error_is_recorded(monkeypatch):
    url = BASE + "/companies/slug/jobs"
    _route(monkeypatch, {}, fail={url: TimeoutError("timed out")})
    errors = []
    assert yc.fetch_yc("slug", errors=errors) == []
    assert errors and "TimeoutError" in errors[0]["reason"]


@pytest.mark.parametrize("body,expect", [
    ("<html><body>no data here</body></html>", "no data-page"),
    ('<div data-page="{not json"></div>', "data-page is not JSON"),
    ('<div data-page="[1, 2]"></div>', "expected a JSON object"),
    ('<div data-page="{&quot;component&quot;: &quot;Other&quot;, &quot;props&quot;: {}}"></div>',
     "not a YC jobs page"),
    ('<div data-page="{&quot;component&quot;: &quot;WaasShowJobsPage&quot;, &quot;props&quot;: {}}"></div>',
     "no 'jobPostings' list"),
    ('<div data-page="{&quot;component&quot;: &quot;WaasShowJobsPage&quot;, '
     '&quot;props&quot;: {&quot;jobPostings&quot;: 5}}"></div>', "no 'jobPostings' list"),
])
def test_malformed_board_records_the_specific_reason(body, expect):
    # A generic "errors is non-empty" check survives collapsing every failure into one
    # branch; each case asserts its own reason.
    errors = []
    assert yc.parse_board_html(body, "slug", errors) == []
    assert errors and expect in errors[0]["reason"], errors


def test_a_live_board_with_no_postings_is_empty_without_an_error():
    body = ('<div data-page="{&quot;component&quot;: &quot;WaasShowJobsPage&quot;, '
            '&quot;props&quot;: {&quot;jobPostings&quot;: []}}"></div>')
    errors = []
    assert yc.parse_board_html(body, "slug", errors) == []
    assert errors == []


def test_a_posting_without_a_title_or_url_is_skipped_and_recorded():
    body = ('<div data-page="{&quot;component&quot;: &quot;WaasShowJobsPage&quot;, '
            '&quot;props&quot;: {&quot;jobPostings&quot;: [{&quot;title&quot;: &quot;X&quot;}]}}"></div>')
    errors = []
    assert yc.parse_board_html(body, "slug", errors) == []
    assert errors and "missing title or url" in errors[0]["reason"]


def test_errors_none_is_allowed(monkeypatch):
    _route(monkeypatch, {})
    assert yc.fetch_yc("dead-slug") == []


def test_slug_is_quoted_as_a_path_segment(monkeypatch):
    seen = _route(monkeypatch, {})
    yc.fetch_yc("a b/c", errors=[])
    assert seen == [BASE + "/companies/a%20b%2Fc/jobs"]


# ------------------------------------------------------------------ rate limit (HTTP 429)
# A real-data run drew HTTP 429 after about 70 requests in a burst. The parser waits between
# job pages and retries a 429 once, honouring Retry-After.

def test_429_is_retried_once_after_retry_after(monkeypatch):
    url = BASE + "/companies/acme-dev/jobs"
    calls, slept = [], []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        return _Resp(BOARD_A.encode("utf-8"))
    monkeypatch.setattr(yc.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(yc, "_sleep", slept.append)
    errors = []
    roles = yc.fetch_yc("acme-dev", errors=errors, descriptions=False)
    assert len(roles) == 6 and errors == []
    assert calls == [url, url]
    assert slept == [7]


def test_a_second_429_is_recorded(monkeypatch):
    url = BASE + "/companies/acme-dev/jobs"
    _route(monkeypatch, {}, fail={url: urllib.error.HTTPError(url, 429, "Too Many", {}, None)})
    slept = []
    monkeypatch.setattr(yc, "_sleep", slept.append)
    errors = []
    assert yc.fetch_yc("acme-dev", errors=errors) == []
    assert errors and "HTTP 429" in errors[0]["reason"]
    assert slept == [yc.RETRY_DEFAULT_SECONDS]   # no Retry-After header: the default wait


def test_retry_after_is_capped(monkeypatch):
    url = BASE + "/companies/acme-dev/jobs"
    _route(monkeypatch, {}, fail={url: urllib.error.HTTPError(url, 429, "x", {"Retry-After": "9999"}, None)})
    slept = []
    monkeypatch.setattr(yc, "_sleep", slept.append)
    yc.fetch_yc("acme-dev", errors=[])
    assert slept == [yc.RETRY_MAX_SECONDS]


def test_job_pages_are_paced(monkeypatch):
    _route(monkeypatch, _site(BOARD_H, "example-co"))
    slept = []
    monkeypatch.setattr(yc, "_sleep", slept.append)
    yc.fetch_yc("example-co", errors=[])
    assert slept == [yc.PAGE_PAUSE_SECONDS] * 3   # one pause before each of the 3 job pages



# ------------------------------------------------------------------ mutation-survivor pins

def test_an_absolute_posting_url_is_kept_as_is():
    body = ('<div data-page="{&quot;component&quot;: &quot;WaasShowJobsPage&quot;, '
            '&quot;props&quot;: {&quot;jobPostings&quot;: [{&quot;title&quot;: &quot;X&quot;, '
            '&quot;url&quot;: &quot;https://www.ycombinator.com/companies/s/jobs/abc-x&quot;}]}}"></div>')
    roles = yc.parse_board_html(body, "s", [])
    assert roles[0]["url"] == "https://www.ycombinator.com/companies/s/jobs/abc-x"


def test_a_job_page_with_no_data_page_is_recorded(monkeypatch):
    roles0 = yc.parse_board_html(BOARD_H, "example-co", [])
    pages = _site(BOARD_H, "example-co")
    pages[roles0[0]["url"]] = "<html>maintenance</html>"
    _route(monkeypatch, pages)
    errors = []
    roles = yc.fetch_yc("example-co", errors=errors)
    assert len(roles) == 3 and len(errors) == 1
    assert "no data-page" in errors[0]["reason"] and roles0[0]["url"] in errors[0]["reason"]


def test_requests_send_a_browser_user_agent_and_ask_for_html(monkeypatch):
    # YC serves the data-page HTML to a browser-like client; the parser depends on both.
    headers = []

    def urlopen(req, timeout=None):
        headers.append({k.lower(): v for k, v in req.header_items()})
        return _Resp(BOARD_A.encode("utf-8"))
    monkeypatch.setattr(yc.urllib.request, "urlopen", urlopen)
    yc.fetch_yc("acme-dev", errors=[], descriptions=False)
    assert headers[0]["user-agent"].startswith("Mozilla/5.0")
    assert headers[0]["accept"] == "text/html"



# ------------------------------------------------------------------ Codex r1: F2, F3, F4

def test_fetch_yc_returns_exactly_the_shared_role_keys(monkeypatch):
    # F2: every parser returns exactly ROLE_KEYS (tools/career_scanner/__init__.py).
    from tools.career_scanner import ROLE_KEYS, validate_role
    _route(monkeypatch, _site(BOARD_H, "example-co"))
    roles = yc.fetch_yc("example-co", errors=[])
    assert roles and all(validate_role(r) for r in roles)
    detailed = yc.fetch_yc_detailed("example-co", errors=[])
    assert set(detailed[0]) - ROLE_KEYS == {
        "salary_range", "equity_range", "min_experience", "created_age", "last_active",
        "created_age_days", "last_active_days", "stale", "hiring_manager"}
    assert [r["description_plain"] for r in roles] == [r["description_plain"] for r in detailed]


def test_a_job_page_for_a_different_posting_is_not_copied(monkeypatch):
    # F3: one posting's page must never fill another posting's description.
    roles0 = yc.parse_board_html(BOARD_H, "example-co", [])
    pages = _site(BOARD_H, "example-co")
    pages[roles0[0]["url"]] = _job_page(roles0[1])
    _route(monkeypatch, pages)
    errors = []
    roles = yc.fetch_yc_detailed("example-co", errors=errors)
    assert len(errors) == 1 and "different posting" in errors[0]["reason"]
    assert roles[0]["description_plain"] == ""
    assert roles[0]["hiring_manager"] == ""
    assert all(r["description_plain"] for r in roles[1:])


@pytest.mark.parametrize("field,value", [
    ("location", {"city": "SF"}), ("type", 3), ("prettyRole", ["x"]), ("salaryRange", 150000),
    ("applyUrl", 7), ("createdAt", 17), ("hiringManager", "Pat"),
])
def test_field_type_drift_is_recorded_and_the_role_kept(field, value):
    # Codex r1 F4: a non-string field must not raise. Codex r2 F1: it must not pass silently
    # either. The role is kept with the field emptied, and the drift is recorded by name.
    import html as _html
    import json as _json
    job = {"title": "X", "url": "/companies/s/jobs/abc-x", field: value}
    data = {"component": "WaasShowJobsPage", "props": {"jobPostings": [job]}}
    body = f'<div data-page="{_html.escape(_json.dumps(data), quote=True)}"></div>'
    errors = []
    roles = yc.parse_board_html(body, "s", errors)
    assert len(roles) == 1
    assert len(errors) == 1 and repr(field) in errors[0]["reason"], errors
    r = roles[0]
    assert all(isinstance(r[k], str) for k in ("location", "department", "employment_type",
                                               "apply_url", "salary_range", "hiring_manager"))
    assert r["created_age"] is None and r["created_age_days"] is None


@pytest.mark.parametrize("job", [
    {"title": 5, "url": "/companies/s/jobs/a"}, {"title": "X", "url": ["/a"]},
    {"title": "", "url": "/companies/s/jobs/a"},
])
def test_a_posting_with_a_non_string_title_or_url_is_recorded(job):
    import html as _html
    import json as _json
    data = {"component": "WaasShowJobsPage", "props": {"jobPostings": [job]}}
    body = f'<div data-page="{_html.escape(_json.dumps(data), quote=True)}"></div>'
    errors = []
    assert yc.parse_board_html(body, "s", errors) == []
    assert errors and "missing title or url" in errors[0]["reason"]


@pytest.mark.parametrize("props", [{}, {"job": None}, {"job": "x"}])
def test_a_job_page_with_no_job_object_is_recorded(props):
    import html as _html
    import json as _json
    data = {"component": "WaasShowJobPage", "props": props}
    errors = []
    assert yc.parse_job_html(f'<div data-page="{_html.escape(_json.dumps(data), quote=True)}"></div>', errors) is None
    assert errors and "no 'job' object" in errors[0]["reason"]



def test_null_optional_fields_are_not_drift():
    # YC sends null for an unset lastActive, hiringManager or equityRange; that is normal.
    import html as _html
    import json as _json
    job = {"title": "X", "url": "/companies/s/jobs/a", "lastActive": None, "hiringManager": None,
           "equityRange": None, "location": None}
    data = {"component": "WaasShowJobsPage", "props": {"jobPostings": [job]}}
    errors = []
    roles = yc.parse_board_html(f'<div data-page="{_html.escape(_json.dumps(data), quote=True)}"></div>', "s", errors)
    assert errors == [] and len(roles) == 1



def test_each_role_gets_its_own_job_page_details(monkeypatch):
    # Grok r1 F1: enrichment must use the page for THIS posting, not the first page fetched.
    _route(monkeypatch, _site(BOARD_A, "acme-dev"))
    roles = yc.fetch_yc_detailed("acme-dev", errors=[])
    assert len(roles) == 6
    for r in roles:
        assert r["description_plain"].endswith(f"Posting text for {r['title']} only.")
        assert r["hiring_manager"] == f"Manager {r['title'].split()[0]}"
