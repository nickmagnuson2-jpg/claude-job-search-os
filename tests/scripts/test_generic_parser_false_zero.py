"""The generic scraper must not report a confident zero.

WHY THIS FILE EXISTS
--------------------
2026-09-02, second cross-model verification round. The three ATS parsers signal every
failure path, so a bare `[]` from one of them really does mean "the board is empty".
`generic.py` is different in kind: it reads an ARBITRARY careers page through hard-coded
CSS selectors. There, "found nothing" much more often means the page changed, the
render failed, or every extraction threw, than that the company has no openings. Every
one of those returned `[]` with no failure recorded, so the scan reported a clean zero.

Playwright is not installed in this environment and must not become a test dependency
for logic that has nothing to do with a real browser -- so the module is faked. What is
under test is the parser's OWN accounting of why it came back empty, which is exactly
the part a live-browser test would be worst at exercising.
"""
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


class _Link:
    def __init__(self, text="Deployment Strategist", href="/jobs/1", raises=False):
        self._text, self._href, self._raises = text, href, raises

    def inner_text(self):
        if self._raises:
            raise RuntimeError("detached from DOM")
        return self._text

    def get_attribute(self, _):
        if self._raises:
            raise RuntimeError("detached from DOM")
        return self._href


class _Page:
    def __init__(self, links=None, status=200, selectors_raise=False):
        self._links, self._status = links or [], status
        self._selectors_raise = selectors_raise

    def goto(self, *a, **k):
        return types.SimpleNamespace(status=self._status)

    def wait_for_timeout(self, _):
        pass

    def query_selector_all(self, _selector):
        if self._selectors_raise:
            raise RuntimeError("page did not render")
        return list(self._links)


def _install_playwright(monkeypatch, page):
    """Fake just enough of playwright for the parser's control flow."""
    browser = types.SimpleNamespace(new_page=lambda **k: page, close=lambda: None)
    chromium = types.SimpleNamespace(launch=lambda **k: browser)
    pw = types.SimpleNamespace(chromium=chromium, stop=lambda: None)
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: pw)
    api.Error = RuntimeError
    root = types.ModuleType("playwright")
    root.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", root)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)


@pytest.fixture
def fetch_generic():
    from tools.career_scanner.generic import fetch_generic as f
    return f


def test_a_missing_playwright_is_recorded(monkeypatch, fetch_generic):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "playwright" in errors[0]["reason"]


def test_an_http_error_page_is_not_an_empty_board(monkeypatch, fetch_generic):
    _install_playwright(monkeypatch, _Page(status=404))
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "404" in errors[0]["reason"]


def test_a_page_that_does_not_render_is_recorded(monkeypatch, fetch_generic):
    _install_playwright(monkeypatch, _Page(selectors_raise=True))
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "did not render" in errors[0]["reason"]


def test_a_layout_with_no_matching_links_is_recorded(monkeypatch, fetch_generic):
    _install_playwright(monkeypatch, _Page(links=[]))
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "layout" in errors[0]["reason"]


def test_links_that_all_fail_to_extract_are_recorded(monkeypatch, fetch_generic):
    _install_playwright(monkeypatch, _Page(links=[_Link(raises=True),
                                                  _Link(raises=True)]))
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "failed to extract" in errors[0]["reason"]


def test_links_that_are_all_filtered_out_are_recorded(monkeypatch, fetch_generic):
    """Every candidate was navigation chrome. Real for a page whose job links moved."""
    _install_playwright(monkeypatch, _Page(links=[_Link(text="Login", href="/login"),
                                                 _Link(text="About", href="/about")]))
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "none survived filtering" in errors[0]["reason"]


def test_a_productive_scrape_records_no_failure(monkeypatch, fetch_generic):
    """Guard on the guard: a working page must stay silent or the signal is noise."""
    _install_playwright(monkeypatch, _Page(links=[_Link()]))
    errors = []
    out = fetch_generic("https://x.test/jobs", "Acme", errors=errors)
    assert [r["title"] for r in out] == ["Deployment Strategist"]
    assert out[0]["url"] == "https://x.test/jobs/1"
    assert errors == []


def test_a_browser_crash_is_recorded(monkeypatch, fetch_generic):
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: (_ for _ in ()).throw(RuntimeError("no chromium"))
    api.Error = RuntimeError
    root = types.ModuleType("playwright")
    root.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", root)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    errors = []
    assert fetch_generic("https://x.test/jobs", "Acme", errors=errors) == []
    assert "no chromium" in errors[0]["reason"]


# --- surviving-mutant coverage ------------------------------------------------

def test_short_or_empty_link_text_is_skipped(monkeypatch, fetch_generic):
    """One- and two-character links are icons and pagination, not job titles."""
    _install_playwright(monkeypatch, _Page(links=[
        _Link(text="›", href="/jobs/next"),
        _Link(text="", href="/jobs/blank"),
        _Link(text="Deployment Strategist", href="/jobs/1"),
    ]))
    out = fetch_generic("https://x.test/jobs", "Acme")
    assert [r["title"] for r in out] == ["Deployment Strategist"]


def test_the_browser_is_always_closed(monkeypatch, fetch_generic):
    """A leaked chromium per company would accumulate across a nightly 45-company run."""
    closed = []
    page = _Page(links=[_Link()])
    browser = types.SimpleNamespace(new_page=lambda **k: page,
                                    close=lambda: closed.append("browser"))
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **k: browser),
                               stop=lambda: closed.append("pw"))
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: pw)
    api.Error = RuntimeError
    root = types.ModuleType("playwright")
    root.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", root)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    fetch_generic("https://x.test/jobs", "Acme")
    assert closed == ["browser", "pw"]


def test_the_parser_waits_for_dynamic_content(monkeypatch, fetch_generic):
    """Most careers pages render their listings after load; without the wait the
    scrape reliably returns an empty page and now reports it as a failure."""
    waits = []
    page = _Page(links=[_Link()])
    page.wait_for_timeout = lambda ms: waits.append(ms)
    _install_playwright(monkeypatch, page)
    fetch_generic("https://x.test/jobs", "Acme")
    assert waits == [2000]


def test_failures_are_announced_on_stderr(monkeypatch, fetch_generic, capsys):
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: (_ for _ in ()).throw(RuntimeError("no chromium"))
    api.Error = RuntimeError
    root = types.ModuleType("playwright")
    root.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", root)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    fetch_generic("https://x.test/jobs", "Acme")
    assert "no chromium" in capsys.readouterr().err


def test_a_missing_playwright_is_announced_on_stderr(monkeypatch, fetch_generic, capsys):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    fetch_generic("https://x.test/jobs", "Acme")
    assert "Playwright not installed" in capsys.readouterr().err


def test_omitting_the_error_list_does_not_crash_any_failure_path(monkeypatch,
                                                                 fetch_generic):
    """`errors=None` is the documented default; every _fail site must tolerate it."""
    for page in (_Page(status=404), _Page(selectors_raise=True), _Page(links=[]),
                 _Page(links=[_Link(raises=True)]),
                 _Page(links=[_Link(text="Login", href="/login")])):
        _install_playwright(monkeypatch, page)
        assert fetch_generic("https://x.test/jobs", "Acme") == []
