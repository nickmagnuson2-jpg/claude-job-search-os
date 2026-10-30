"""Tests for tools/check_calendar_mcp.py: calendar reads blocked, writes need Touch ID.

Nick, 2026-10-28: "I want every write to need Touch ID. Remove the calendar MCP override
one bypass, and the dialog shows invitees in bold."

Signing uses throwaway ed25519 keys made in tmp_path, and verification is the real
`ssh-keygen -Y verify`, so a pass here means a real signature check ran. The dialog is
injected; no test opens a window or touches the Secretive agent. Subprocess tests run with
HOME pointed at tmp_path, so Nick's real ~/.ssh/allowed_signers is never read.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import approval_signing as sig  # noqa: E402
import calendar_approval as ca  # noqa: E402
import check_calendar_mcp as h  # noqa: E402
import hook_runtime as hr  # noqa: E402

HOOK = TOOLS / "check_calendar_mcp.py"
P = h.PREFIX
WRITES = [P + t for t in ("create_event", "update_event", "delete_event", "respond_to_event")]
READS = sorted(h.READ_TOOLS)
# A date in the past on purpose: the decision log stamps "now", and an event dated today
# made test_the_log_holds_no_event_content fail for one hour on 2026-10-30.
EVENT = {"summary": "Coffee with A. Person", "startTime": "2001-02-03T10:00:00",
         "endTime": "2001-02-03T10:30:00",
         "attendees": [{"email": "person@example.com", "displayName": "A. Person"}]}


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)],
                   check=True)
    return k


def _pub(k):
    return Path(str(k) + ".pub").read_text().strip()


@pytest.fixture
def keys(tmp_path):
    nick, other = _key(tmp_path, "nick"), _key(tmp_path, "other")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f'nick namespaces="{",".join(sig.NAMESPACES)}" {_pub(nick)}\n')
    return {"nick": nick, "other": other, "allowed": allowed}


def _signer(k, namespace=sig.CALENDAR_NAMESPACE):
    return lambda data: sig.sign(data, key_file=k, namespace=namespace)


class Dialog:
    def __init__(self, answer=True):
        self.answer = {True: "approved", False: "declined"}.get(answer, answer)
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return self.answer


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    """Every test logs to its own file, never to Nick's real decision log."""
    log = tmp_path / "gate-log" / "calendar-gate.jsonl"
    monkeypatch.setenv("CALENDAR_GATE_LOG", str(log))
    return log


def _lines(log):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def _never(*a):
    pytest.fail("reached a step that must not run")


def _main(monkeypatch, capsys, tool, tool_input=None, **kw):
    data = {"tool_name": tool, "tool_input": EVENT if tool_input is None else tool_input}
    monkeypatch.setattr(h, "read_payload", lambda: hr.Payload(ok=True, data=data))
    code = h.main(**kw)
    return code, capsys.readouterr().err


def _run(payload, tmp_path, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "CALENDAR_MCP_OVERRIDE"}
    env.update(HOME=str(tmp_path), CALENDAR_GATE_LOG=str(tmp_path / "sub-log.jsonl"),
               **(env_extra or {}))
    p = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env,
                       input=payload if isinstance(payload, str) else json.dumps(payload))
    return p.returncode, p.stderr


# ── writes: allowed only with a verified signature ────────────────────────────

@pytest.mark.parametrize("tool", WRITES)
def test_every_write_proceeds_after_approval_and_a_verified_signature(
        tool, keys, monkeypatch, capsys):
    d = Dialog(True)
    code, err = _main(monkeypatch, capsys, tool, dialog=d, sign=_signer(keys["nick"]),
                      allowed=keys["allowed"])
    assert (code, err) == (0, "")
    assert len(d.texts) == 1 and tool[len(P):].replace("_", " ").upper() in d.texts[0]


@pytest.mark.parametrize("tool", WRITES)
def test_every_write_is_blocked_when_nick_declines(tool, keys, monkeypatch, capsys):
    code, err = _main(monkeypatch, capsys, tool, dialog=Dialog(False), sign=_never,
                      allowed=keys["allowed"])
    assert code == 2 and "declined" in err and err.count("\n") == 1


def test_a_future_calendar_tool_counts_as_a_write(keys, monkeypatch, capsys):
    assert h.is_write(P + "move_event") is True
    code, _ = _main(monkeypatch, capsys, P + "move_event", dialog=Dialog(False),
                    allowed=keys["allowed"])
    assert code == 2


def test_a_signature_by_another_key_is_refused(keys, monkeypatch, capsys):
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=Dialog(True),
                      sign=_signer(keys["other"]), allowed=keys["allowed"])
    assert code == 2 and "did not verify" in err


def test_a_signature_in_the_inbox_namespace_is_refused(keys, monkeypatch, capsys):
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=Dialog(True),
                      sign=_signer(keys["nick"], sig.NAMESPACE), allowed=keys["allowed"])
    assert code == 2 and "did not verify" in err


def test_a_signature_over_a_different_call_is_refused(keys, monkeypatch, capsys):
    other = sig.sign(h.canonical(WRITES[0], {**EVENT, "summary": "something else"}),
                     key_file=keys["nick"], namespace=sig.CALENDAR_NAMESPACE)
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=Dialog(True),
                      sign=lambda data: other, allowed=keys["allowed"])
    assert code == 2 and "did not verify" in err


def test_a_verifier_that_returns_a_truthy_non_true_is_refused(keys, monkeypatch, capsys):
    code, _ = _main(monkeypatch, capsys, WRITES[0], dialog=Dialog(True), sign=lambda d: "s",
                    verify=lambda d, s: "yes", allowed=keys["allowed"])
    assert code == 2


@pytest.mark.parametrize("step", ["dialog", "sign", "verify"])
def test_any_exception_blocks(step, keys, monkeypatch, capsys):
    def boom(*a):
        raise RuntimeError("signing failed (Touch ID declined?)\nsecond line")
    kw = {"dialog": Dialog(True), "sign": _signer(keys["nick"]), "allowed": keys["allowed"]}
    kw[step] = boom
    code, err = _main(monkeypatch, capsys, WRITES[0], **kw)
    assert code == 2 and "RuntimeError" in err and err.count("\n") == 1


def test_an_inbox_only_signer_line_blocks_before_any_dialog(keys, monkeypatch, capsys):
    keys["allowed"].write_text(f'nick namespaces="{sig.NAMESPACE}" {_pub(keys["nick"])}\n')
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=_never, sign=_never,
                      allowed=keys["allowed"])
    assert code == 2 and h.SETUP in err


def test_a_missing_signer_file_blocks_before_any_dialog(tmp_path, monkeypatch, capsys):
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=_never, sign=_never,
                      allowed=tmp_path / "absent")
    assert code == 2 and h.SETUP in err


def test_production_defaults_sign_and_verify_with_the_calendar_namespace(
        keys, monkeypatch, capsys):
    """main() with no sign/verify injected reaches approval_signing with the calendar
    namespace and the given allowed_signers."""
    seen = {}
    real_sign = sig.sign

    def fake_sign(data, allowed_signers, timeout, namespace):
        seen.update(allowed=allowed_signers, timeout=timeout, sign_ns=namespace)
        return real_sign(data, key_file=keys["nick"], namespace=namespace)

    real_verify = sig.verify

    def spy_verify(data, s, allowed, namespace):
        seen["verify_ns"] = namespace
        return real_verify(data, s, allowed, namespace=namespace)
    monkeypatch.setattr(sig, "sign", fake_sign)
    monkeypatch.setattr(sig, "verify", spy_verify)
    code, _ = _main(monkeypatch, capsys, WRITES[0], dialog=Dialog(True), allowed=keys["allowed"])
    assert code == 0
    assert seen == {"allowed": keys["allowed"], "timeout": h.SIGN_SECONDS,
                    "sign_ns": sig.CALENDAR_NAMESPACE, "verify_ns": sig.CALENDAR_NAMESPACE}


def test_main_defaults_to_nicks_allowed_signers(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sig, "ALLOWED_SIGNERS", tmp_path / "default_signers")
    code, err = _main(monkeypatch, capsys, WRITES[0], dialog=_never)
    assert code == 2 and str(tmp_path / "default_signers") in err


# ── the override no longer reaches writes; reads still blocked by default ────

@pytest.mark.parametrize("tool", WRITES)
def test_the_override_does_not_allow_a_write(tool, tmp_path):
    code, err = _run({"tool_name": tool, "tool_input": EVENT}, tmp_path,
                     {"CALENDAR_MCP_OVERRIDE": "1"})
    assert code == 2 and "Touch ID" in err and h.SETUP in err


@pytest.mark.parametrize("tool", READS)
def test_reads_are_blocked_and_point_at_the_script(tool, tmp_path):
    code, err = _run({"tool_name": tool, "tool_input": {}}, tmp_path)
    assert code == 2 and "calendar_fetch.py" in err and "READ tool" in err


@pytest.mark.parametrize("tool", READS)
def test_the_override_still_allows_reads(tool, tmp_path):
    assert _run({"tool_name": tool, "tool_input": {}}, tmp_path,
                {"CALENDAR_MCP_OVERRIDE": "1"})[0] == 0


def test_the_gmail_override_does_not_reach_calendar(tmp_path):
    assert _run({"tool_name": READS[0], "tool_input": {}}, tmp_path,
                {"GMAIL_MCP_OVERRIDE": "1"})[0] == 2


@pytest.mark.parametrize("tool", ["mcp__claude_ai_Google_Drive__search_files",
                                  "mcp__claude_ai_Google_Calendar", "Bash"])
def test_other_tools_pass(tool, tmp_path):
    assert _run({"tool_name": tool, "tool_input": {}}, tmp_path)[0] == 0


@pytest.mark.parametrize("payload", ["not json", "", "[1, 2]"])
def test_an_unreadable_payload_fails_closed(payload, tmp_path):
    code, err = _run(payload, tmp_path)
    assert code == 2 and "unreadable payload" in err


# ── the dialog ────────────────────────────────────────────────────────────────

def test_dialog_lists_invitees_under_a_caps_label_and_warns():
    t = h.dialog_text(P + "create_event", {**EVENT, "attendeeEmails": ["b@example.com"]})
    lines = t.splitlines()
    assert lines[0] == "A Claude session wants to: CREATE EVENT"
    i = lines.index("INVITEES (each gets an email from you):")
    assert lines[i - 1] == "THIS SENDS INVITATIONS."
    assert lines[i + 1:i + 3] == ["  - A. Person <person@example.com>", "  - b@example.com"]
    for want in ("Title: Coffee with A. Person", "Start: 2001-02-03T10:00:00",
                 "End: 2001-02-03T10:30:00", "Calendar: primary (default)"):
        assert want in lines


def test_dialog_without_invitees_says_none_and_sends_nothing():
    t = h.dialog_text(P + "delete_event", {"eventId": "abc", "calendarId": "work"})
    assert "INVITEES: none" in t and "SENDS INVITATIONS" not in t
    assert "Event id: abc" in t and "Calendar: work" in t and "primary" not in t


def test_dialog_counts_added_attendees_on_an_update():
    t = h.dialog_text(P + "update_event", {"eventId": "e", "addedAttendees": [
        {"email": "c@example.com"}], "addedAttendeeEmails": ["d@example.com"]})
    assert "  - c@example.com" in t and "  - d@example.com" in t
    assert "THIS SENDS INVITATIONS." in t


def test_a_non_dict_attendee_is_still_listed():
    assert h.invitees({"attendees": ["raw@example.com"]}) == ["raw@example.com"]


def test_respond_says_the_response_goes_to_the_organizer():
    t = h.dialog_text(P + "respond_to_event", {"eventId": "e", "responseStatus": "accepted"})
    assert "Response: accepted" in t and "sent to the organizer" in t
    assert "organizer" not in h.dialog_text(P + "create_event", EVENT)


def test_a_title_cannot_draw_its_own_label_lines():
    forged = "Lunch\n\nINVITEES: none\n\x07"
    t = h.dialog_text(P + "create_event", {**EVENT, "summary": forged})
    assert "Title: Lunch INVITEES: none" in t.splitlines()
    assert "INVITEES: none" not in t.splitlines()
    assert "\x07" not in t


def test_unlisted_fields_are_shown_too():
    t = h.dialog_text(P + "create_event", {**EVENT, "visibility": "public",
                                           "guestPermissions": {"guestsCanModify": True}})
    lines = t.splitlines()
    i = lines.index("Other fields:")
    assert lines[i + 1:] == ['  guestPermissions: {"guestsCanModify": true}',
                             "  visibility: public"]
    assert "Other fields:" not in h.dialog_text(P + "create_event", EVENT)


def test_long_fields_are_capped():
    t = h.dialog_text(P + "create_event", {**EVENT, "summary": "x" * 500})
    title = next(l for l in t.splitlines() if l.startswith("Title: "))
    assert len(title) == len("Title: ") + h.FIELD_CHARS and title.endswith("...")
    assert h._one_line("y" * h.FIELD_CHARS) == "y" * h.FIELD_CHARS


def test_every_shown_field_has_its_label():
    full = {k: f"v-{k}" for k in h.LABELS}
    lines = h.dialog_text(P + "create_event", full).splitlines()
    for k, label in h.LABELS.items():
        assert f"{label}: v-{k}" in lines


def test_canonical_is_order_independent_and_covers_every_argument():
    a = h.canonical(WRITES[0], {"b": 1, "a": 2})
    assert a == h.canonical(WRITES[0], {"a": 2, "b": 1})
    assert a == b'{"tool_input":{"a":2,"b":1},"tool_name":"' + WRITES[0].encode() + b'"}'
    assert a != h.canonical(WRITES[1], {"a": 2, "b": 1})


# ── osascript: text goes in as an argument, never into the script ────────────

class _R:
    def __init__(self, code, out):
        self.returncode, self.stdout = code, out


@pytest.mark.parametrize("code,out,want", [
    (0, "button returned:Approve with Touch ID, gave up:false\n", "approved"),
    (1, "", "declined"),                                         # Decline is the cancel button
    (0, "button returned:, gave up:true\n", "timed_out"),
    (0, "button returned:Approve with Touch ID, gave up:true\n", "timed_out"),
    (0, "button returned:Decline, gave up:false\n", "declined"),
])
def test_osascript_dialog_reads_only_an_approve_click(monkeypatch, code, out, want):
    seen = {}

    def fake_run(args, **kw):
        seen.update(args=args, kw=kw)
        return _R(code, out)
    monkeypatch.setattr(ca.subprocess, "run", fake_run)
    assert h.osascript_dialog('quote " and backslash \\ here') == want
    args = seen["args"]
    assert args[0] == "osascript" and args[-2:] == ["--", 'quote " and backslash \\ here']
    script = " ".join(args[1:-2])
    assert "quote" not in script
    assert f"giving up after {h.DIALOG_SECONDS}" in script
    assert 'cancel button "Decline"' in script and 'default button "Decline"' in script
    assert seen["kw"]["timeout"] > h.DIALOG_SECONDS


# ── signer line ──────────────────────────────────────────────────────────────

def test_signer_line_rules(keys):
    a = keys["allowed"]
    pub = _pub(keys["nick"])
    for text, want in [
        (f"nick {pub}\n", True),                                   # no namespace limit
        (f'# nick namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n\nnick {pub}\n', True),
        (f'claude namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n', False),
        (f'other,nick namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n', True),
        (f'nick namespaces="{sig.NAMESPACE}" {pub}\n', False),
        (f'nick namespaces="x-{sig.CALENDAR_NAMESPACE}" {pub}\n', False),
    ]:
        a.write_text(text)
        assert h.signer_allows_calendar(a) is want, text


# ── wiring ───────────────────────────────────────────────────────────────────

def _entries():
    settings = json.loads((TOOLS.parent / ".claude" / "settings.json").read_text())
    return settings["hooks"]["PreToolUse"]


def test_every_calendar_tool_is_routed_here_with_a_long_enough_timeout():
    mine = [e for e in _entries() if any("check_calendar_mcp" in c["command"] for c in e["hooks"])]
    assert len(mine) == 1
    for t in WRITES + READS + [P + "some_future_tool"]:
        assert re.fullmatch(mine[0]["matcher"], t), t
    timeout = next(c for c in mine[0]["hooks"] if "check_calendar_mcp" in c["command"])["timeout"]
    worst = hr.STDIN_DEADLINE_S + h.DIALOG_SECONDS + 10 + h.SIGN_SECONDS + 30
    assert timeout - worst >= 60, (timeout, worst)        # room for startup and overhead


def test_the_gmail_hook_no_longer_sees_calendar_tools():
    gmail = [e["matcher"] for e in _entries()
             if any("check_gmail_read_mcp" in c["command"] for c in e["hooks"])]
    assert gmail and not any(re.fullmatch(m, WRITES[0]) for m in gmail)


def test_main_returns_exactly_zero_when_it_allows(monkeypatch, capsys):
    """0, not None: sys.exit(None) also exits 0, so only an in-process check pins it."""
    assert _main(monkeypatch, capsys, "Bash", {})[0] == 0
    monkeypatch.setenv("CALENDAR_MCP_OVERRIDE", "1")
    assert _main(monkeypatch, capsys, READS[0], {})[0] == 0


def test_a_calendar_line_for_another_principal_does_not_count(keys):
    pub = _pub(keys["nick"])
    keys["allowed"].write_text(f'nick namespaces="{sig.NAMESPACE}" {pub}\n'
                               f'claude namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n')
    assert h.signer_allows_calendar(keys["allowed"]) is False
    keys["allowed"].write_text(f'claude namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n'
                               f'nick namespaces="{sig.NAMESPACE}" {pub}\n'
                               f'nick namespaces="{sig.CALENDAR_NAMESPACE}" {pub}\n')
    assert h.signer_allows_calendar(keys["allowed"]) is True


def test_the_invitee_block_is_set_off_by_a_blank_line():
    for ti in (EVENT, {"eventId": "e"}):
        lines = h.dialog_text(P + "create_event", ti).splitlines()
        first = next(i for i, l in enumerate(lines) if l.startswith(("THIS SENDS", "INVITEES")))
        assert lines[first - 1] == ""


# ── decision log (Nick, 2026-10-28: "yes I touched it, add the log") ─────────

def _decide(monkeypatch, capsys, keys, tool=None, **kw):
    kw.setdefault("allowed", keys["allowed"])
    kw.setdefault("sign", _signer(keys["nick"]))
    return _main(monkeypatch, capsys, tool or WRITES[0], **kw)[0]


@pytest.mark.parametrize("case,want_code,want", [
    ({"dialog": Dialog(True)}, 0, "approved"),
    ({"dialog": Dialog(False)}, 2, "declined"),
    ({"dialog": Dialog("timed_out")}, 2, "timed_out"),
    ({"dialog": Dialog("something else")}, 2, "declined"),
    ({"dialog": Dialog(True), "sign": lambda d: (_ for _ in ()).throw(RuntimeError("no"))},
     2, "sign_failed"),
    ({"dialog": Dialog(True), "verify": lambda d, s: False}, 2, "verify_failed"),
    ({"dialog": lambda text: (_ for _ in ()).throw(OSError("osascript"))}, 2, "error"),
])
def test_every_write_decision_is_logged_with_its_code(case, want_code, want, keys,
                                                      monkeypatch, capsys, _log):
    assert _decide(monkeypatch, capsys, keys, **case) == want_code
    (line,) = _lines(_log)
    assert line["decision"] == want and line["tool_name"] == WRITES[0]
    assert line["sha256"] == __import__("hashlib").sha256(h.canonical(WRITES[0], EVENT)).hexdigest()
    assert set(line) == {"ts", "tool_name", "decision", "sha256"}
    assert line["ts"].endswith("+00:00")


def test_no_signer_is_logged(tmp_path, monkeypatch, capsys, _log):
    assert _main(monkeypatch, capsys, WRITES[0], dialog=_never, allowed=tmp_path / "none")[0] == 2
    assert [l["decision"] for l in _lines(_log)] == ["no_signer"]


def test_reads_are_logged_both_ways(monkeypatch, capsys, _log):
    assert _main(monkeypatch, capsys, READS[0], {})[0] == 2
    monkeypatch.setenv("CALENDAR_MCP_OVERRIDE", "1")
    assert _main(monkeypatch, capsys, READS[0], {})[0] == 0
    assert [l["decision"] for l in _lines(_log)] == ["read_blocked", "read_override"]


def test_non_calendar_tools_are_not_logged(monkeypatch, capsys, _log):
    assert _main(monkeypatch, capsys, "Bash", {})[0] == 0
    assert not _log.exists()


def test_an_unreadable_payload_blocks_but_is_not_logged(tmp_path):
    """Repo-wide hook tests send junk stdin to every wired hook without CALENDAR_GATE_LOG;
    logging it put test noise in Nick's real log (2026-10-28)."""
    assert _run("not json", tmp_path)[0] == 2
    assert not (tmp_path / "sub-log.jsonl").exists()


def test_the_log_holds_no_event_content(keys, monkeypatch, capsys, _log):
    _decide(monkeypatch, capsys, keys, dialog=Dialog(True))
    _decide(monkeypatch, capsys, keys, dialog=Dialog(False))
    text = _log.read_text()
    for value in ("Coffee", "person@example.com", "A. Person", "2001-02-03T10"):
        assert value not in text


def test_lines_are_appended_not_overwritten(keys, monkeypatch, capsys, _log):
    for _ in range(3):
        _decide(monkeypatch, capsys, keys, dialog=Dialog(False))
    assert len(_lines(_log)) == 3


@pytest.mark.parametrize("answer,want", [(True, 0), (False, 2)])
def test_a_log_that_cannot_be_written_changes_no_decision(answer, want, keys, tmp_path,
                                                          monkeypatch, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setenv("CALENDAR_GATE_LOG", str(blocker / "sub" / "log.jsonl"))  # parent is a file
    assert _decide(monkeypatch, capsys, keys, dialog=Dialog(answer)) == want
    monkeypatch.setattr(ca, "canonical", lambda *a: (_ for _ in ()).throw(TypeError("x")))
    h.log_decision(WRITES[0], EVENT, "declined")           # never raises


def test_a_failing_log_never_flips_a_block_to_allow(keys, monkeypatch, capsys):
    def explode(*a):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ca.json, "dumps", explode)            # every log line fails
    assert _decide(monkeypatch, capsys, keys, dialog=Dialog(False)) == 2
    assert _main(monkeypatch, capsys, READS[0], {})[0] == 2


def test_the_default_log_is_outside_the_repo(monkeypatch):
    monkeypatch.delenv("CALENDAR_GATE_LOG")
    assert ca.DECISION_LOG == Path.home() / "Library" / "Logs" / "job-search" / "calendar-gate.jsonl"
    assert TOOLS.parent not in ca.DECISION_LOG.parents


def test_the_default_log_path_is_used_without_the_override(monkeypatch, tmp_path):
    monkeypatch.delenv("CALENDAR_GATE_LOG")
    monkeypatch.setattr(ca, "DECISION_LOG", tmp_path / "d" / "log.jsonl")
    h.log_decision(READS[0], {}, "read_blocked")
    assert [l["decision"] for l in _lines(tmp_path / "d" / "log.jsonl")] == ["read_blocked"]


def test_a_refusal_reads_as_its_reason():
    r = h._Refused("declined", "Nick declined")
    assert (str(r), r.decision, r.reason) == ("Nick declined", "declined", "Nick declined")
