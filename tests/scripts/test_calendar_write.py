"""Tests for tools/calendar_write.py: one event write, sent only after a verified Touch ID
approval of that exact request.

No network and no real key: the Google service is faked, the dialog is injected, and
signing uses throwaway ed25519 keys made in tmp_path, verified with the real
`ssh-keygen -Y verify`. Every test logs to its own file (CALENDAR_GATE_LOG).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import approval_signing as sig  # noqa: E402
import calendar_approval as ca  # noqa: E402
import calendar_write as cw  # noqa: E402


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)],
                   check=True)
    return k


@pytest.fixture
def env(tmp_path, monkeypatch):
    nick, other = _key(tmp_path, "nick"), _key(tmp_path, "other")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f'nick namespaces="{",".join(sig.NAMESPACES)}" '
                       f'{Path(str(nick) + ".pub").read_text().strip()}\n')
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / cw.TOKEN_FILE).write_text("{}")
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CALENDAR_GATE_LOG", str(log))
    monkeypatch.setattr(cw.calendar_fetch, "get_creds", lambda *a, **k: "creds")
    return {"nick": nick, "other": other, "allowed": allowed, "tools": tools, "log": log}


class Service:
    """A fake Google Calendar service: records every request, sends nothing."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, creds):
        self.creds = creds
        return self

    def events(self):
        return self

    def _req(self, method, **kw):
        self.calls.append((method, kw))
        outer = self

        class R:
            def execute(self):
                if outer.fail:
                    raise outer.fail
                return {"id": kw.get("eventId", "new-id"), "htmlLink": "https://cal/x"}
        return R()

    def insert(self, **kw):
        return self._req("insert", **kw)

    def patch(self, **kw):
        return self._req("patch", **kw)

    def delete(self, **kw):
        return self._req("delete", **kw)


class Dialog:
    def __init__(self, answer="approved"):
        self.answer, self.texts = answer, []

    def __call__(self, text):
        self.texts.append(text)
        return self.answer


CREATE = ["create", "--title", "Coffee with A. Person", "--start", "2026-11-02T10:00",
          "--end", "2026-11-02T10:30"]


def _run(env, argv, dialog=None, service=None, signer=None, **kw):
    dialog = dialog or Dialog()
    service = service or Service()
    key = signer or env["nick"]
    code = cw.main(argv, dialog=dialog, allowed=env["allowed"], tools_dir=env["tools"],
                   service_factory=service,
                   sign=kw.pop("sign", lambda d: sig.sign(d, key_file=key,
                                                          namespace=sig.CALENDAR_NAMESPACE)),
                   **kw)
    return code, dialog, service


def _log(env):
    return [json.loads(l) for l in env["log"].read_text().splitlines()] if env["log"].exists() else []


# ── requests ─────────────────────────────────────────────────────────────────

def test_create_sends_exactly_the_signed_request_and_invites_nobody(env, capsys):
    code, d, s = _run(env, CREATE + ["--location", "Cafe"])
    assert code == 0
    assert s.calls == [("insert", {"calendarId": "primary", "sendUpdates": "none", "body": {
        "summary": "Coffee with A. Person", "location": "Cafe",
        "start": {"dateTime": "2026-11-02T10:00", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-11-02T10:30", "timeZone": "America/Los_Angeles"}}})]
    assert s.creds == "creds"
    assert json.loads(capsys.readouterr().out) == {"status": "created", "id": "new-id",
                                                    "htmlLink": "https://cal/x"}
    assert [l["decision"] for l in _log(env)] == ["approved"]


def test_attendees_send_invitations_and_the_dialog_says_so(env):
    code, d, s = _run(env, CREATE + ["--attendee", "a@example.com", "--attendee", "b@example.com"])
    assert code == 0
    method, kw = s.calls[0]
    assert kw["sendUpdates"] == "all"
    assert kw["body"]["attendees"] == [{"email": "a@example.com"}, {"email": "b@example.com"}]
    lines = d.texts[0].splitlines()
    assert "THIS SENDS INVITATIONS." in lines
    assert "  - a@example.com" in lines and "  - b@example.com" in lines
    assert "  sendUpdates: all" in lines


def test_the_dialog_shows_the_event_in_plain_fields(env):
    _, d, _ = _run(env, CREATE + ["--calendar", "work", "--location", "Cafe"])
    lines = d.texts[0].splitlines()
    assert "Location: Cafe" in lines
    assert lines[0] == "A Claude session wants to: CREATE EVENT (calendar_write.py)"
    for want in ("Title: Coffee with A. Person", "Start: 2026-11-02T10:00 (America/Los_Angeles)",
                 "End: 2026-11-02T10:30 (America/Los_Angeles)", "Calendar: work", "INVITEES: none",
                 "  sendUpdates: none"):
        assert want in lines


def test_update_patches_only_the_given_fields(env, capsys):
    code, d, s = _run(env, ["update", "ev1", "--title", "Moved"])
    assert code == 0
    assert s.calls == [("patch", {"calendarId": "primary", "eventId": "ev1",
                                  "sendUpdates": "none", "body": {"summary": "Moved"}})]
    assert "Event id: ev1" in d.texts[0].splitlines()
    assert "REPLACE" not in d.texts[0]
    assert json.loads(capsys.readouterr().out)["status"] == "updated"


def test_update_with_attendees_warns_that_the_list_is_replaced(env):
    _, d, s = _run(env, ["update", "ev1", "--attendee", "c@example.com"])
    assert cw.UPDATE_NOTE in d.texts[0].splitlines()
    assert s.calls[0][1]["sendUpdates"] == "all"


def test_delete_sends_no_cancellations(env, capsys):
    code, d, s = _run(env, ["delete", "ev9", "--calendar", "work"])
    assert code == 0
    assert s.calls == [("delete", {"calendarId": "work", "eventId": "ev9", "sendUpdates": "none"})]
    assert d.texts[0].splitlines()[0].endswith("DELETE EVENT (calendar_write.py)")
    assert json.loads(capsys.readouterr().out) == {"status": "deleted", "id": "ev9",
                                                    "htmlLink": None}


def test_a_date_alone_is_an_all_day_event(env):
    _, _, s = _run(env, ["create", "--title", "Off", "--start", "2026-11-02",
                         "--end", "2026-11-03"])
    body = s.calls[0][1]["body"]
    assert (body["start"], body["end"]) == ({"date": "2026-11-02"}, {"date": "2026-11-03"})


def test_timezone_is_carried_on_timed_events(env):
    _, _, s = _run(env, CREATE + ["--timezone", "America/New_York"])
    assert s.calls[0][1]["body"]["start"]["timeZone"] == "America/New_York"


@pytest.mark.parametrize("argv,want", [
    (["create", "--title", "T", "--start", "2026-11-02T10:00"], "create needs"),
    (["update", "ev1"], "at least one field"),
    (["create", "--title", "T", "--start", "tomorrow", "--end", "2026-11-02"], "not an ISO"),
])
def test_bad_arguments_stop_before_any_dialog(env, argv, want, capsys):
    with pytest.raises(SystemExit) as e:
        _run(env, argv, dialog=lambda t: pytest.fail("dialog shown"))
    assert e.value.code == 2
    assert want in capsys.readouterr().err


def test_no_command_is_a_usage_error(env):
    with pytest.raises(SystemExit) as e:
        cw.main([], tools_dir=env["tools"])
    assert e.value.code == 2


# ── nothing reaches Google without a verified approval ───────────────────────

@pytest.mark.parametrize("dialog,decision", [
    (Dialog("declined"), "declined"), (Dialog("timed_out"), "timed_out"),
    (Dialog("anything else"), "declined"),
])
def test_no_approval_sends_nothing(env, capsys, dialog, decision):
    code, _, s = _run(env, CREATE, dialog=dialog)
    assert code == 2 and s.calls == []
    assert "not sent" in capsys.readouterr().err
    assert [l["decision"] for l in _log(env)] == [decision]


def test_a_signature_by_another_key_sends_nothing(env):
    code, _, s = _run(env, CREATE, signer=env["other"])
    assert code == 2 and s.calls == []
    assert [l["decision"] for l in _log(env)] == ["verify_failed"]


def test_a_failed_signature_sends_nothing(env):
    def boom(data):
        raise RuntimeError("signing failed (Touch ID declined?)")
    code, _, s = _run(env, CREATE, sign=boom)
    assert code == 2 and s.calls == []
    assert [l["decision"] for l in _log(env)] == ["sign_failed"]


def test_a_signature_over_a_different_request_sends_nothing(env):
    other = sig.sign(b"something else", key_file=env["nick"], namespace=sig.CALENDAR_NAMESPACE)
    code, _, s = _run(env, CREATE, sign=lambda d: other)
    assert code == 2 and s.calls == []


def test_the_signature_covers_the_request_that_is_sent(env):
    seen = {}

    def spy(data):
        seen["data"] = data
        return sig.sign(data, key_file=env["nick"], namespace=sig.CALENDAR_NAMESPACE)
    _, _, s = _run(env, CREATE + ["--attendee", "a@example.com"], sign=spy)
    signed = json.loads(seen["data"])
    assert signed["tool_name"] == "calendar_write.create"
    method, kw = s.calls[0]
    assert signed["tool_input"]["body"] == kw["body"]
    assert signed["tool_input"]["sendUpdates"] == kw["sendUpdates"]
    assert signed["tool_input"]["calendarId"] == kw["calendarId"]


def test_no_signer_line_sends_nothing_and_shows_no_dialog(env, tmp_path):
    env["allowed"].write_text("")
    code, _, s = _run(env, CREATE, dialog=lambda t: pytest.fail("dialog shown"))
    assert code == 2 and s.calls == []
    assert [l["decision"] for l in _log(env)] == ["no_signer"]


def test_an_unexpected_error_sends_nothing(env, capsys):
    def boom(text):
        raise OSError("osascript missing")
    code, _, s = _run(env, CREATE, dialog=boom)
    assert code == 2 and s.calls == []
    assert "OSError" in capsys.readouterr().err
    assert [l["decision"] for l in _log(env)] == ["error"]


def test_no_token_stops_before_any_dialog(env, capsys):
    (env["tools"] / cw.TOKEN_FILE).unlink()
    code, _, s = _run(env, CREATE, dialog=lambda t: pytest.fail("dialog shown"))
    assert code == 1 and s.calls == []
    assert "--auth" in capsys.readouterr().err and not env["log"].exists()


def test_production_defaults_sign_and_verify_with_the_calendar_namespace(env, monkeypatch):
    seen = {}
    real_sign, real_verify = sig.sign, sig.verify

    def fake_sign(data, allowed_signers, timeout, namespace):
        seen.update(allowed=allowed_signers, sign_ns=namespace)
        return real_sign(data, key_file=env["nick"], namespace=namespace)

    def spy_verify(data, s, allowed, namespace):
        seen["verify_ns"] = namespace
        return real_verify(data, s, allowed, namespace=namespace)
    monkeypatch.setattr(sig, "sign", fake_sign)
    monkeypatch.setattr(sig, "verify", spy_verify)
    service = Service()
    assert cw.main(CREATE, dialog=Dialog(), allowed=env["allowed"], tools_dir=env["tools"],
                   service_factory=service) == 0
    assert seen == {"allowed": env["allowed"], "sign_ns": sig.CALENDAR_NAMESPACE,
                    "verify_ns": sig.CALENDAR_NAMESPACE}
    assert len(service.calls) == 1


def test_default_signer_file_is_nicks(env, monkeypatch, tmp_path):
    monkeypatch.setattr(sig, "ALLOWED_SIGNERS", tmp_path / "absent")
    service = Service()
    assert cw.main(CREATE, dialog=lambda t: pytest.fail("dialog"), tools_dir=env["tools"],
                   service_factory=service) == 2
    assert service.calls == []


# ── after approval ───────────────────────────────────────────────────────────

class HttpError(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.resp = type("R", (), {"status": status})()
        self.reason = reason


def test_a_google_refusal_is_one_line_and_exit_1(env, capsys):
    code, _, _ = _run(env, CREATE, service=Service(fail=HttpError(403, "Insufficient scope")))
    assert code == 1
    err = capsys.readouterr().err.strip()
    assert err == "ERROR: Google Calendar API refused the request (HTTP 403): Insufficient scope"


def test_a_bug_after_approval_keeps_its_traceback(env):
    with pytest.raises(KeyError):
        _run(env, CREATE, service=Service(fail=KeyError("x")))


def test_auth_uses_its_own_token_and_single_scope(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(cw.calendar_fetch, "get_creds",
                        lambda d, **kw: seen.update(dir=d, **kw))
    assert cw.main(["--auth"], tools_dir=env["tools"]) == 0
    assert seen == {"dir": env["tools"], "auth_mode": True, "token_file": "calendar_write_token.json",
                    "scopes": ["https://www.googleapis.com/auth/calendar.events.owned"],
                    "tool": "calendar_write.py"}


def test_the_write_token_is_not_the_read_token():
    assert cw.TOKEN_FILE != cw.calendar_fetch.TOKEN_FILE
    assert cw.SCOPES == ["https://www.googleapis.com/auth/calendar.events.owned"]


def test_creds_are_loaded_only_after_approval(env, monkeypatch):
    order = []
    monkeypatch.setattr(cw.calendar_fetch, "get_creds",
                        lambda *a, **k: order.append("creds") or "c")

    def dialog(text):
        order.append("dialog")
        return "declined"
    _run(env, CREATE, dialog=dialog)
    assert order == ["dialog"]


def test_build_service_asks_for_calendar_v3(monkeypatch):
    import types
    seen = {}
    mod = types.ModuleType("googleapiclient.discovery")
    mod.build = lambda *a, **k: seen.update(args=a, kw=k) or "svc"
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", mod)
    assert cw.build_service("creds") == "svc"
    assert seen == {"args": ("calendar", "v3"), "kw": {"credentials": "creds",
                                                       "cache_discovery": False}}


def test_script_entry_point_runs_main():
    r = subprocess.run([sys.executable, str(TOOLS / "calendar_write.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "create" in r.stdout


def test_the_token_is_gitignored():
    r = subprocess.run(["git", "check-ignore", "-q", "tools/calendar_write_token.json"],
                       cwd=TOOLS.parent)
    assert r.returncode == 0


def test_delete_takes_no_event_fields(env, capsys):
    with pytest.raises(SystemExit) as e:
        _run(env, ["delete", "ev9", "--title", "x"], dialog=lambda t: pytest.fail("dialog"))
    assert e.value.code == 2


def test_a_dialog_without_a_note_ends_at_the_invitee_line():
    assert ca.dialog_text("X", {}).endswith("\nINVITEES: none")   # no blank line after it
    assert ca.dialog_text("X", {}, "a note").splitlines()[-2:] == ["INVITEES: none", "a note"]


def test_the_dialog_shows_the_time_zone_that_decides_the_instant(env):
    _, d, _ = _run(env, CREATE + ["--timezone", "Asia/Tokyo"])
    assert "Start: 2026-11-02T10:00 (Asia/Tokyo)" in d.texts[0].splitlines()
    _, d, _ = _run(env, ["create", "--title", "Off", "--start", "2026-11-02", "--end", "2026-11-03"])
    assert "Start: 2026-11-02 (all day)" in d.texts[-1].splitlines()


@pytest.mark.parametrize("flag,value", [
    ("--title", "x" * 161), ("--location", "a\nb"), ("--title", "two  spaces"),
    ("--attendee", "a" * 150 + "@example.com"), ("--title", " lead"), ("--calendar", "c" * 161),
])
def test_a_value_the_dialog_could_not_show_in_full_is_refused(env, flag, value, capsys):
    with pytest.raises(SystemExit) as e:
        _run(env, CREATE + [flag, value], dialog=lambda t: pytest.fail("dialog shown"))
    assert e.value.code == 2 and "exactly as sent" in capsys.readouterr().err


def test_a_value_at_the_limit_is_shown_whole(env):
    _, d, s = _run(env, ["create", "--title", "y" * 160, "--start", "2026-11-02",
                         "--end", "2026-11-03"])
    assert "Title: " + "y" * 160 in d.texts[0].splitlines()
    assert s.calls[0][1]["body"]["summary"] == "y" * 160


def test_a_long_event_id_is_refused_on_delete(env):
    with pytest.raises(SystemExit):
        _run(env, ["delete", "e" * 161], dialog=lambda t: pytest.fail("dialog shown"))


@pytest.mark.parametrize("tz", ["Z" * 65, "Bad\tZone"])
def test_a_time_zone_that_would_crowd_the_time_line_is_refused(env, tz, capsys):
    with pytest.raises(SystemExit) as e:
        _run(env, CREATE + ["--timezone", tz], dialog=lambda t: pytest.fail("dialog shown"))
    assert e.value.code == 2 and "--timezone" in capsys.readouterr().err


def test_the_longest_accepted_time_line_is_shown_whole(env):
    start = "2026-11-02T10:00:00.000000+00:00"
    tz = "Z" * cw.MAX_TZ_CHARS
    _, d, _ = _run(env, ["create", "--title", "T", "--start", start, "--end", start,
                         "--timezone", tz])
    assert f"Start: {start} ({tz})" in d.texts[0].splitlines()


def test_the_setup_command_appends_to_the_signer_file():
    assert ca.SETUP.endswith(">> ~/.ssh/allowed_signers")


def test_the_time_line_budget_fits_the_dialog():
    assert cw.MAX_TIME_CHARS + len(" ()") + cw.MAX_TZ_CHARS <= ca.FIELD_CHARS


def test_a_start_longer_than_the_time_budget_is_refused(env, capsys):
    long_start = "2026-11-02T10:00:00.000000+00:00" + "0" * 9     # 41 characters
    with pytest.raises(SystemExit) as e:
        _run(env, ["create", "--title", "T", "--start", long_start, "--end", "2026-11-03"],
             dialog=lambda t: pytest.fail("dialog shown"))
    assert e.value.code == 2 and "at most 40" in capsys.readouterr().err
