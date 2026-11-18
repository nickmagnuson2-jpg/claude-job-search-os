"""Tests for tools/ats_board.py and fetch_ashby_with_compensation.

Network is never touched: urllib.request.urlopen is replaced with a fake that
returns a canned Ashby payload or raises an HTTPError.
"""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools import ats_board  # noqa: E402
from tools.career_scanner import ashby  # noqa: E402

BOARD = {"jobs": [
    {"id": "id-1", "title": "Deployment Strategist", "location": "San Francisco",
     "jobUrl": "https://jobs.example/1",
     "compensation": {"compensationTierSummary": "$150K – $200K"}},
    {"id": "id-2", "title": "Software Engineer", "location": "Remote",
     "jobUrl": "https://jobs.example/2", "compensation": {}},
    {"id": "id-3", "title": "Business Operations Lead", "location": "San Francisco",
     "jobUrl": "https://jobs.example/3"},
]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def seen_urls(monkeypatch):
    urls = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return _Resp(json.dumps(BOARD).encode())
    monkeypatch.setattr(ashby.urllib.request, "urlopen", fake_urlopen)
    return urls


@pytest.fixture
def http_404(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(ashby.urllib.request, "urlopen", fake_urlopen)


def test_plain_fetch_keeps_the_shared_contract(seen_urls):
    roles = ashby.fetch_ashby("acme", [])
    assert seen_urls == ["https://api.ashbyhq.com/posting-api/job-board/acme"]
    assert "compensation_summary" not in roles[0] and "id" not in roles[0]


def test_comp_fetch_requests_pay_and_adds_two_keys(seen_urls):
    errors = []
    roles = ashby.fetch_ashby_with_compensation("acme", errors)
    assert seen_urls == ["https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"]
    assert errors == []
    assert [r["id"] for r in roles] == ["id-1", "id-2", "id-3"]
    assert [r["compensation_summary"] for r in roles] == ["$150K – $200K", "", ""]
    plain = set(ashby.fetch_ashby("acme", [])[0])
    assert set(roles[0]) - plain == {"id", "compensation_summary"}


def test_comp_fetch_reports_failure_on_the_error_channel(http_404):
    errors = []
    assert ashby.fetch_ashby_with_compensation("nope", errors) == []
    assert errors == [{"reason": "HTTP 404"}]


def test_read_board_title_filter_is_case_insensitive(seen_urls):
    roles, errors = ats_board.read_board("ashby", "acme", title="deployment|business op")
    assert errors == []
    assert [r["title"] for r in roles] == ["Deployment Strategist", "Business Operations Lead"]


def test_format_line_with_and_without_pay(seen_urls):
    roles, _ = ats_board.read_board("ashby", "acme", comp=True)
    assert ats_board.format_line(roles[0]) == (
        "Deployment Strategist | San Francisco | $150K – $200K | https://jobs.example/1")
    assert "no pay posted" in ats_board.format_line(roles[1])
    plain, _ = ats_board.read_board("ashby", "acme")
    assert ats_board.format_line(plain[0]) == (
        "Deployment Strategist | San Francisco | https://jobs.example/1")


def test_main_prints_count_and_lines(seen_urls, capsys):
    assert ats_board.main(["ashby", "acme", "--comp", "--title", "strategist"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "ashby/acme: 1 role(s) matching /strategist/"
    assert "$150K – $200K" in out


def test_main_json(seen_urls, capsys):
    assert ats_board.main(["ashby", "acme", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 3


def test_main_failed_fetch_exits_1_and_never_prints_zero(http_404, capsys):
    assert ats_board.main(["ashby", "nope"]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "FETCH FAILED for ashby/nope: HTTP 404" in cap.err
    assert "not an empty board" in cap.err


def test_comp_is_ashby_only(capsys):
    assert ats_board.main(["lever", "acme", "--comp"]) == 2
    assert "Ashby only" in capsys.readouterr().err


def test_bad_regex_is_a_usage_error(capsys):
    assert ats_board.main(["ashby", "acme", "--title", "("]) == 2
    assert "not a valid regex" in capsys.readouterr().err


def test_fetcher_dispatch():
    from tools.career_scanner.greenhouse import fetch_greenhouse
    from tools.career_scanner.lever import fetch_lever
    assert ats_board._fetcher("greenhouse", False) is fetch_greenhouse
    assert ats_board._fetcher("lever", False) is fetch_lever
    assert ats_board._fetcher("ashby", False) is ashby.fetch_ashby
    assert ats_board._fetcher("ashby", True) is ashby.fetch_ashby_with_compensation


def test_request_sends_accept_and_user_agent(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen.update({k.lower(): v for k, v in req.header_items()})
        return _Resp(json.dumps(BOARD).encode())
    monkeypatch.setattr(ashby.urllib.request, "urlopen", fake_urlopen)
    ashby.fetch_ashby_with_compensation("acme", [])
    assert seen.get("accept") == "application/json"
    assert "Mozilla/5.0" in seen.get("user-agent", "")


def test_runs_as_a_script_and_propagates_the_exit_code(tmp_path):
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "tools" / "ats_board.py"), "lever", "acme", "--comp"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "Ashby only" in r.stderr


@pytest.mark.parametrize("body", [b"<html>not json</html>", b'{"jobs": ["not-an-object"]}'])
def test_malformed_body_is_a_failed_read_not_zero(monkeypatch, capsys, body):
    monkeypatch.setattr(ashby.urllib.request, "urlopen", lambda req, timeout=None: _Resp(body))
    assert ats_board.main(["ashby", "acme"]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "FETCH FAILED for ashby/acme:" in cap.err


def test_scan_jobs_skill_no_longer_prescribes_curl():
    text = (REPO / ".claude" / "skills" / "scan-jobs" / "SKILL.md").read_text()
    assert "calling it directly by\ncurl" not in text
    assert "tools/ats_board.py ashby <slug> --comp" in text


def test_non_string_fields_do_not_crash(monkeypatch, capsys):
    # r2 F3 (Codex): a numeric or null title must render, not raise
    body = json.dumps({"jobs": [{"id": "x", "title": 7, "location": None, "jobUrl": "u"}]}).encode()
    monkeypatch.setattr(ashby.urllib.request, "urlopen", lambda req, timeout=None: _Resp(body))
    assert ats_board.main(["ashby", "acme", "--title", "7"]) == 0
    assert "- 7 |  | u" in capsys.readouterr().out
