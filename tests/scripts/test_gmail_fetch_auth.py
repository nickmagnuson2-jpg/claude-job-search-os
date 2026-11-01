"""Tests for tools/gmail_fetch.py: OAuth credential handling and message parsing.

Every Google object here is a fake. No real token or credential file is read, no OAuth
flow starts, no browser opens, and no Gmail API call is made: get_or_refresh_creds imports
its Google classes inside the function, so the fakes are installed in sys.modules under
the exact dotted names it imports. All paths live under tmp_path. The macOS notification
in _emit_auth_failure_alert goes through subprocess.run, which is replaced in every test
here so that a failure-path test cannot pop a real notification.
"""
import base64
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import gmail_fetch  # noqa: E402

RFC_DATE = "Mon, 02 Mar 2026 10:30:00 +0000"
PREFIX = "20260302-103000"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _part(mime_type: str, text: str) -> dict:
    return {"mimeType": mime_type, "body": {"data": _b64(text)}}


@pytest.fixture(autouse=True)
def _no_notifications(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
    return calls


# ── Fake Google auth objects ───────────────────────────────────────────────────

class FakeCreds:
    def __init__(self, valid=True, expired=False, refresh_token="rt-placeholder",
                 refresh_error=None, payload=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_error = refresh_error
        self.refresh_calls = []
        self.payload = payload or {"token": "tok-placeholder"}

    def refresh(self, request):
        self.refresh_calls.append(request)
        if self.refresh_error is not None:
            raise self.refresh_error
        self.valid = True
        self.expired = False

    def to_json(self):
        return json.dumps(self.payload)


class FakeRequest:
    pass


class Harness:
    """Installs fake google modules and records what get_or_refresh_creds did with them."""

    def __init__(self, monkeypatch, tmp_path):
        self.tools_dir = tmp_path / "tools"
        self.tools_dir.mkdir()
        self.token_path = self.tools_dir / "gmail_token.json"
        self.creds_path = self.tools_dir / "gmail_credentials.json"
        self.alert_path = tmp_path / "inbox" / "GMAIL-AUTH-FAILURE.md"
        self.loaded_creds = FakeCreds()
        self.flow_creds = FakeCreds(payload={"token": "from-flow"})
        self.load_calls = []
        self.flow_calls = []
        self.load_error = None
        harness = self

        class FakeCredentials:
            @staticmethod
            def from_authorized_user_file(path, scopes):
                harness.load_calls.append((path, scopes))
                if harness.load_error is not None:
                    raise harness.load_error
                return harness.loaded_creds

        class FakeFlow:
            def run_local_server(self, port):
                harness.flow_calls.append(("run_local_server", port))
                return harness.flow_creds

        class FakeInstalledAppFlow:
            @staticmethod
            def from_client_secrets_file(path, scopes):
                harness.flow_calls.append(("from_client_secrets_file", path, scopes))
                return FakeFlow()

        self.Credentials = FakeCredentials
        mods = {
            "google.oauth2.credentials": types.SimpleNamespace(Credentials=FakeCredentials),
            "google.auth.transport.requests": types.SimpleNamespace(Request=FakeRequest),
            "google_auth_oauthlib.flow": types.SimpleNamespace(InstalledAppFlow=FakeInstalledAppFlow),
        }
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)

    def write_token(self, data=None):
        self.token_path.write_text(json.dumps(data or {"token": "old-placeholder"}),
                                   encoding="utf-8")

    def run(self, auth_mode=False):
        return gmail_fetch.get_or_refresh_creds(self.tools_dir, auth_mode=auth_mode)


@pytest.fixture
def h(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


# ── get_or_refresh_creds ───────────────────────────────────────────────────────

def test_missing_google_libraries_exit_with_install_hint(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", None)
    # A token is present, so if the exit were skipped the function would carry on and
    # fail somewhere else instead of stopping with the install hint.
    (tmp_path / "gmail_token.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        gmail_fetch.get_or_refresh_creds(tmp_path, auth_mode=False)
    assert exc.value.code == 1
    assert "Google API libraries not installed" in capsys.readouterr().err


def test_auth_mode_runs_flow_and_saves_token(h, capsys):
    h.creds_path.write_text("{}", encoding="utf-8")
    result = h.run(auth_mode=True)
    assert result is h.flow_creds
    assert h.flow_calls == [
        ("from_client_secrets_file", str(h.creds_path), gmail_fetch.SCOPES),
        ("run_local_server", 0),
    ]
    assert json.loads(h.token_path.read_text(encoding="utf-8")) == {"token": "from-flow"}
    assert f"Token saved to {h.token_path}" in capsys.readouterr().out
    assert h.load_calls == []


def test_auth_mode_without_credentials_file_exits(h, capsys):
    with pytest.raises(SystemExit) as exc:
        h.run(auth_mode=True)
    assert exc.value.code == 1
    assert f"{h.creds_path} not found" in capsys.readouterr().err
    assert h.flow_calls == []
    assert not h.token_path.exists()


def test_normal_mode_without_token_exits(h, capsys):
    with pytest.raises(SystemExit) as exc:
        h.run()
    assert exc.value.code == 1
    assert "No token found" in capsys.readouterr().err
    assert h.load_calls == []


def test_valid_token_is_returned_without_flow_or_refresh(h):
    h.write_token()
    before = h.token_path.read_text(encoding="utf-8")
    result = h.run()
    assert result is h.loaded_creds
    assert h.load_calls == [(str(h.token_path), gmail_fetch.SCOPES)]
    assert h.flow_calls == []
    assert h.loaded_creds.refresh_calls == []
    assert h.token_path.read_text(encoding="utf-8") == before
    assert not h.alert_path.exists()


def test_expired_token_with_refresh_token_is_refreshed_and_saved(h):
    h.write_token()
    h.loaded_creds = FakeCreds(valid=False, expired=True, payload={"token": "new-placeholder"})
    result = h.run()
    assert result is h.loaded_creds
    assert len(h.loaded_creds.refresh_calls) == 1
    assert isinstance(h.loaded_creds.refresh_calls[0], FakeRequest)
    saved = json.loads(h.token_path.read_text(encoding="utf-8"))
    assert saved["token"] == "new-placeholder"
    stamp = datetime.strptime(saved["token_last_refresh"], "%Y-%m-%dT%H:%M:%S")
    assert abs((datetime.now() - stamp).total_seconds()) < 60
    assert not h.alert_path.exists()


def test_refresh_failure_alerts_and_reraises(h):
    h.write_token()
    h.loaded_creds = FakeCreds(valid=False, expired=True,
                               refresh_error=RuntimeError("invalid_grant placeholder"))
    with pytest.raises(RuntimeError, match="invalid_grant placeholder"):
        h.run()
    assert h.alert_path.exists()
    assert "invalid_grant placeholder" in h.alert_path.read_text(encoding="utf-8")


def test_invalid_token_without_refresh_token_alerts_and_exits(h, capsys):
    h.write_token()
    h.loaded_creds = FakeCreds(valid=False, expired=True, refresh_token=None)
    with pytest.raises(SystemExit) as exc:
        h.run()
    assert exc.value.code == 1
    assert h.loaded_creds.refresh_calls == []
    assert "Token invalid and cannot be refreshed" in capsys.readouterr().err
    assert "no refresh_token available" in h.alert_path.read_text(encoding="utf-8")


def test_invalid_but_not_expired_token_is_not_refreshed(h):
    h.write_token()
    h.loaded_creds = FakeCreds(valid=False, expired=False)
    with pytest.raises(SystemExit):
        h.run()
    assert h.loaded_creds.refresh_calls == []


# ── _load_token_or_exit ────────────────────────────────────────────────────────

def test_load_token_returns_credentials(h):
    h.write_token()
    result = gmail_fetch._load_token_or_exit(h.Credentials, h.token_path, h.tools_dir)
    assert result is h.loaded_creds
    assert h.load_calls == [(str(h.token_path), gmail_fetch.SCOPES)]


@pytest.mark.parametrize("err", [json.JSONDecodeError("bad", "", 0), ValueError("bad"),
                                 KeyError("refresh_token")])
def test_corrupt_token_alerts_and_exits(h, capsys, err):
    h.write_token()
    h.load_error = err
    with pytest.raises(SystemExit) as exc:
        gmail_fetch._load_token_or_exit(h.Credentials, h.token_path, h.tools_dir)
    assert exc.value.code == 1
    err_out = capsys.readouterr().err
    assert "corrupt or truncated" in err_out
    assert "--auth" in err_out
    assert "corrupt or truncated" in h.alert_path.read_text(encoding="utf-8")


# ── _write_token_atomic ────────────────────────────────────────────────────────

def test_write_token_atomic_writes_and_leaves_no_temp(tmp_path):
    token = tmp_path / "gmail_token.json"
    gmail_fetch._write_token_atomic(token, '{"a": 1}')
    assert token.read_text(encoding="utf-8") == '{"a": 1}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gmail_token.json"]


def test_write_token_atomic_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    token = tmp_path / "gmail_token.json"
    token.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(gmail_fetch.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        gmail_fetch._write_token_atomic(token, "new")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gmail_token.json"]
    assert token.read_text(encoding="utf-8") == "old"


# ── check_token_expiry ─────────────────────────────────────────────────────────

def _iso(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def test_expiry_missing_token_is_false(tmp_path):
    inbox = tmp_path / "inbox"
    assert gmail_fetch.check_token_expiry(tmp_path / "nope.json", inbox) is False
    assert not inbox.exists()


def test_expiry_corrupt_token_is_false(tmp_path):
    token = tmp_path / "t.json"
    token.write_text("{not json", encoding="utf-8")
    assert gmail_fetch.check_token_expiry(token, tmp_path / "inbox") is False


def test_expiry_without_timestamp_is_false(tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"token": "x"}), encoding="utf-8")
    assert gmail_fetch.check_token_expiry(token, tmp_path / "inbox") is False


def test_expiry_unparseable_timestamp_is_false(tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"expiry": "not-a-date"}), encoding="utf-8")
    assert gmail_fetch.check_token_expiry(token, tmp_path / "inbox") is False


def test_expiry_recent_refresh_is_false_and_writes_nothing(tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"token_last_refresh": _iso(1)}), encoding="utf-8")
    inbox = tmp_path / "inbox"
    assert gmail_fetch.check_token_expiry(token, inbox) is False
    assert not inbox.exists()


def test_expiry_old_refresh_writes_alert_into_new_dir(tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"expiry": _iso(6) + ".123456Z"}), encoding="utf-8")
    inbox = tmp_path / "deep" / "inbox"
    assert gmail_fetch.check_token_expiry(token, inbox) is True
    alert = (inbox / "GMAIL-AUTH-ALERT.md").read_text(encoding="utf-8")
    assert "last refreshed **6 days ago**" in alert


def test_expiry_boundary_at_five_days_alerts(tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"token_last_refresh": _iso(5.01)}), encoding="utf-8")
    assert gmail_fetch.check_token_expiry(token, tmp_path / "inbox") is True


# ── _friendly_sender ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("header, expected", [
    ("", "new sender"),
    ('"Northwind Talent" <jobs@example.com>', "Northwind Talent"),
    ("Contoso Recruiting <careers@example.com>", "Contoso Recruiting"),
    ("jobs@example.com", "jobs"),
    ("Northwind", "Northwind"),
    ("x" * 40, "x" * 30),
])
def test_friendly_sender(header, expected):
    assert gmail_fetch._friendly_sender(header) == expected


# ── sanitize_body: BeautifulSoup vs regex branches ─────────────────────────────

def test_sanitize_html_uses_beautifulsoup_when_available():
    out = gmail_fetch.sanitize_body("<p>a &copy; b</p><p>c</p>")
    assert out == f"{gmail_fetch.XML_OPEN}\na © b\nc\n{gmail_fetch.XML_CLOSE}"


def test_sanitize_plain_text_is_not_parsed_as_html():
    # Entities in plain text survive: neither the BeautifulSoup nor the regex path runs.
    out = gmail_fetch.sanitize_body("Northwind &amp; Contoso")
    assert out == f"{gmail_fetch.XML_OPEN}\nNorthwind &amp; Contoso\n{gmail_fetch.XML_CLOSE}"


def test_sanitize_html_regex_fallback_without_bs4(monkeypatch):
    monkeypatch.setattr(gmail_fetch, "_BS4_AVAILABLE", False)
    out = gmail_fetch.sanitize_body("<p>a &lt; b &amp; c</p>")
    assert out == f"{gmail_fetch.XML_OPEN}\na < b & c\n{gmail_fetch.XML_CLOSE}"


# ── extract_plain_text ─────────────────────────────────────────────────────────

def test_extract_skips_undecodable_part():
    parts = [{"mimeType": "text/plain", "body": {"data": "A"}}, _part("text/html", "<p>hi</p>")]
    assert gmail_fetch.extract_plain_text(parts) == "hi"


def test_extract_keeps_first_html_part():
    parts = [_part("text/html", "<p>first</p>"), _part("text/html", "<p>second</p>"),
             _part("text/calendar", "third")]
    assert gmail_fetch.extract_plain_text(parts) == "first"


def test_extract_html_uses_beautifulsoup_when_available():
    parts = [_part("text/html", "<p>a &amp; b</p><p>c</p>")]
    assert gmail_fetch.extract_plain_text(parts) == "a & b\nc"


def test_extract_html_regex_fallback_without_bs4(monkeypatch):
    monkeypatch.setattr(gmail_fetch, "_BS4_AVAILABLE", False)
    parts = [_part("text/html", "<p>a &amp; b</p>\n\n<p>c</p>")]
    assert gmail_fetch.extract_plain_text(parts) == "a &amp; b c"


# ── build_inbox_filename / slugs / dates ───────────────────────────────────────

@pytest.mark.parametrize("sender, subject, slug", [
    ("jobs@example.com", "Hello There", "jobs-hello-there"),
    ("", "Hello There", "hello-there"),
    ("jobs@example.com", "", "jobs"),
    ("", "", "email"),
])
def test_build_inbox_filename_slug_combinations(sender, subject, slug):
    assert gmail_fetch.build_inbox_filename(RFC_DATE, sender, subject) == f"{PREFIX}-{slug}.md"


def test_slugify_sender_uses_local_part():
    assert gmail_fetch._slugify_sender("jobs@example.com") == "jobs"
    assert gmail_fetch._slugify_sender("Talent <hiring@example.com>") == "hiring"


def test_slugify_subject_strips_reply_prefix():
    assert gmail_fetch._slugify_subject("Re: Hello World") == "hello-world"


def test_parse_date_none_falls_back_to_now():
    dt = gmail_fetch._parse_email_date(None)
    assert abs((datetime.now() - dt).total_seconds()) < 60


def test_parse_date_signed_number_is_not_treated_as_epoch():
    # "+86400000" is not all digits, so it must NOT be read as epoch ms (1970-01-02);
    # it is unparseable and falls back to now().
    dt = gmail_fetch._parse_email_date("+86400000")
    assert abs((datetime.now() - dt).total_seconds()) < 60


def test_parse_date_garbage_falls_back_to_now():
    dt = gmail_fetch._parse_email_date("not a date")
    assert isinstance(dt, datetime)
    assert abs((datetime.now() - dt).total_seconds()) < 60


# ── write_inbox_file ───────────────────────────────────────────────────────────

META = {"date": RFC_DATE, "sender": "jobs@example.com", "subject": "Hello There",
        "message_id": "<m1@example.com>"}


def test_write_creates_missing_inbox_dir(tmp_path):
    inbox = tmp_path / "a" / "inbox"
    path = gmail_fetch.write_inbox_file(inbox, META, "BODY")
    assert path == inbox / f"{PREFIX}-jobs-hello-there.md"
    assert path.read_text(encoding="utf-8").endswith("BODY\n")


def test_dry_run_writes_nothing_and_ignores_collisions(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    existing = inbox / f"{PREFIX}-jobs-hello-there.md"
    existing.write_text("already here", encoding="utf-8")
    path = gmail_fetch.write_inbox_file(inbox, META, "BODY", dry_run=True)
    assert path == existing
    assert existing.read_text(encoding="utf-8") == "already here"
    assert sorted(p.name for p in inbox.iterdir()) == [existing.name]
    out = capsys.readouterr().out
    assert f"[dry-run] Would write: {existing}" in out
    assert "# Email: Hello There" in out
    assert "..." not in out


def test_dry_run_creates_dir_and_truncates_long_preview(tmp_path, capsys):
    inbox = tmp_path / "new-inbox"
    gmail_fetch.write_inbox_file(inbox, META, "x" * 500, dry_run=True)
    assert inbox.is_dir() and list(inbox.iterdir()) == []
    out = capsys.readouterr().out
    assert out.rstrip("\n").endswith("x...")


# ── _extract_header / _get_all_mime_parts ──────────────────────────────────────

def test_extract_header_missing_returns_empty_string():
    headers = [{"name": "Subject", "value": "Hi"}]
    assert gmail_fetch._extract_header(headers, "subject") == "Hi"
    assert gmail_fetch._extract_header(headers, "From") == ""


def test_get_all_mime_parts_collects_only_parts_with_data():
    leaf_a = {"mimeType": "text/plain", "body": {"data": "YQ"}}
    leaf_b = {"mimeType": "text/html", "body": {"data": "Yg"}}
    inner = {"mimeType": "multipart/alternative", "body": {"size": 0}, "parts": [leaf_a, leaf_b]}
    root = {"mimeType": "multipart/mixed", "body": {}, "parts": [inner]}
    assert gmail_fetch._get_all_mime_parts(root) == [leaf_a, leaf_b]
    single = {"mimeType": "text/plain", "body": {"data": "YQ"}}
    assert gmail_fetch._get_all_mime_parts(single) == [single]
