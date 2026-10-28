#!/usr/bin/env python3
"""
check_calendar_mcp.py — PreToolUse hook for every Google Calendar connector tool.

Two policies, one hook, because the matcher (`mcp__claude_ai_Google_Calendar__.*`) routes
every calendar tool here and the two halves share the tool list:

READS (list_events, get_event, search_events, list_calendars, suggest_time) are blocked.
  Read through tools/calendar_fetch.py instead: a calendar.readonly token, sanitized titles
  and locations (an invite is stranger-written text, the same channel as an email body:
  how-i-work-with-ai.md, Claim 17), and private appointments redacted. The connector
  returns the same text raw. Override: CALENDAR_MCP_OVERRIDE=1, for reads only.

WRITES need a fresh Touch ID signature over that exact call. Nick, 2026-10-28: "I like this
fix I want every write to need Touch ID. Remove the calendar MCP override one bypass, and
the dialog shows invitees in bold." Every calendar tool that is not a known read counts as
a write (create_event, update_event, delete_event, respond_to_event, and any tool the
connector adds later), so a new tool fails closed.
  1. Nick's line in ~/.ssh/allowed_signers must allow the calendar namespace, or the call
     is blocked before any dialog, with the setup command.
  2. A macOS dialog shows the action, title, times, calendar and invitees. A dialog cannot
     render bold, so invitees sit under an all-caps label, with "THIS SENDS INVITATIONS"
     when there are any. Each field is collapsed onto one line and capped, so text inside
     a title cannot draw a fake label of its own.
  3. On "Approve with Touch ID", the canonical call (tool_name plus tool_input, sorted JSON)
     is signed through the Secretive agent (Touch ID prompts) under the
     calendar-write-approval namespace, then verified against allowed_signers.
  The call proceeds only if the signature verifies. Decline, the dialog timing out, no
  signer line, an unreachable agent, Touch ID declined, an unreadable payload, or any
  exception: blocked (exit 2), with one line saying why. CALENDAR_MCP_OVERRIDE does NOT
  allow a write; that bypass was removed at Nick's request.

Why a signature and not just a dialog: anything in the session can print a dialog-shaped
prompt, but only Nick's finger produces a signature that verifies. Nothing is stored, so
no approval can be replayed for a different call.

Timing: the dialog gives up after DIALOG_SECONDS and signing after SIGN_SECONDS; with the
payload read and the verify bound the worst case is 162 s, inside the 240 s hook timeout in
.claude/settings.json, so the hook reaches its own verdict instead of being cut off.

Split from tools/check_gmail_read_mcp.py on 2026-10-28: that hook is Gmail-only again, and
only this one needs the long timeout.

Exit codes: 0 allow, 2 block.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
import approval_signing  # noqa: E402

PREFIX = "mcp__claude_ai_Google_Calendar__"
READ_TOOLS = {PREFIX + t for t in (
    "list_events", "get_event", "search_events", "list_calendars", "suggest_time")}
# Worst case, in sequence: payload read 2 s, dialog DIALOG_SECONDS + 10, signing
# SIGN_SECONDS, verify 30 (approval_signing.verify): 162 s, leaving 78 s of the 240 s hook
# timeout for interpreter start and subprocess overhead. Codex calendar-gate r1 F2: the first
# bounds (90 + 15 + 90 + 30) left 13 s.
DIALOG_SECONDS = 60
SIGN_SECONDS = 60
FIELD_CHARS = 160
APPROVE = "Approve with Touch ID"
SETUP = ("PYTHONIOENCODING=utf-8 python3 tools/approval_signing.py signer-line"
         " > ~/.ssh/allowed_signers")


def is_calendar_tool(tool_name) -> bool:
    return isinstance(tool_name, str) and tool_name.startswith(PREFIX)


def is_write(tool_name) -> bool:
    """Every calendar tool that is not a known read."""
    return is_calendar_tool(tool_name) and tool_name not in READ_TOOLS


def canonical(tool_name: str, tool_input) -> bytes:
    """The exact bytes signed: the tool and every argument it was called with."""
    return json.dumps({"tool_name": tool_name, "tool_input": tool_input}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signer_allows_calendar(path) -> bool:
    """True when Nick's allowed_signers line has a key and permits the calendar namespace."""
    if approval_signing.principal_key(path) is None:
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if line.lstrip().startswith("#") or not parts:
            continue
        if approval_signing.PRINCIPAL not in parts[0].split(","):
            continue
        m = re.search(r'namespaces="([^"]*)"', line)
        if m is None or approval_signing.CALENDAR_NAMESPACE in m.group(1).split(","):
            return True
    return False


def _one_line(value) -> str:
    """A field on one line, capped, control characters gone."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text if len(text) <= FIELD_CHARS else text[:FIELD_CHARS - 3] + "..."


def invitees(tool_input: dict) -> list[str]:
    """Everyone this call adds: attendees and the deprecated email lists."""
    out = []
    for key in ("attendees", "addedAttendees"):
        for a in tool_input.get(key) or []:
            if isinstance(a, dict):
                name = a.get("displayName")
                out.append(f"{name} <{a.get('email')}>" if name else str(a.get("email")))
            else:
                out.append(str(a))
    for key in ("attendeeEmails", "addedAttendeeEmails"):
        out += [str(e) for e in tool_input.get(key) or []]
    return [_one_line(x) for x in out]


LABELS = {"summary": "Title", "startTime": "Start", "endTime": "End", "allDay": "All day",
          "calendarId": "Calendar", "eventId": "Event id", "responseStatus": "Response",
          "location": "Location", "recurrenceData": "Repeats",
          "notificationLevel": "Notifications"}
INVITEE_KEYS = ("attendees", "addedAttendees", "attendeeEmails", "addedAttendeeEmails")


def dialog_text(tool_name: str, tool_input: dict) -> str:
    action = tool_name[len(PREFIX):].replace("_", " ").upper()
    lines = [f"A Claude session wants to: {action}", ""]
    shown = {"calendarId": "primary (default)", **tool_input}
    lines += [f"{label}: {_one_line(shown[k])}" for k, label in LABELS.items() if k in shown]
    who = invitees(tool_input)
    lines.append("")
    if who:
        lines += ["THIS SENDS INVITATIONS.", "INVITEES (each gets an email from you):"]
        lines += [f"  - {w}" for w in who]
    else:
        lines.append("INVITEES: none")
    if tool_name == PREFIX + "respond_to_event":
        lines.append("Your response is sent to the organizer.")
    rest = sorted(k for k in tool_input if k not in LABELS and k not in INVITEE_KEYS)
    if rest:
        lines += ["", "Other fields:"] + [f"  {k}: {_one_line(tool_input[k])}" for k in rest]
    return "\n".join(lines)


def osascript_dialog(text: str) -> bool:
    """True only when Nick clicked Approve before the dialog gave up."""
    script = [
        "on run argv",
        # Joined with +, not implicit concatenation: the AppleScript word "of" inside an
        # f-string reads as a printed count to tests/scripts/test_tools_announce_reach.py.
        'display dialog (item 1 of argv) with title "Calendar write" ' +
        f'buttons {{"Decline", "{APPROVE}"}} default button "Decline" '
        f'cancel button "Decline" with icon caution giving up after {DIALOG_SECONDS}',
        "end run",
    ]
    args = ["osascript"]
    for line in script:
        args += ["-e", line]
    r = subprocess.run(args + ["--", text], capture_output=True, text=True,
                       timeout=DIALOG_SECONDS + 10)
    return (r.returncode == 0 and f"button returned:{APPROVE}" in r.stdout
            and "gave up:true" not in r.stdout)


def approve_write(tool_name, tool_input, dialog, sign, verify, allowed) -> tuple[bool, str]:
    """(allowed, reason). Every failure is a refusal."""
    if not signer_allows_calendar(allowed):
        return False, (f"no signer line allows calendar approvals in {allowed}. "
                       f"Nick runs once: {SETUP}")
    if not dialog(dialog_text(tool_name, tool_input)):
        return False, "Nick declined, or the approval dialog timed out"
    data = canonical(tool_name, tool_input)
    if verify(data, sign(data)) is not True:
        return False, "the Touch ID signature did not verify"
    return True, "approved with Touch ID"


def _sign(data: bytes, allowed) -> str:
    return approval_signing.sign(data, allowed_signers=allowed, timeout=SIGN_SECONDS,
                                 namespace=approval_signing.CALENDAR_NAMESPACE)


def _verify(data: bytes, sig: str, allowed) -> bool:
    return approval_signing.verify(data, sig, allowed,
                                   namespace=approval_signing.CALENDAR_NAMESPACE)


def read_message(short: str) -> str:
    return (
        f"BLOCKED: {short} is a Google Calendar connector READ tool.\n\n"
        "  Read events with the script (read-only token, sanitized titles and locations,\n"
        "  private appointments redacted):\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/calendar_fetch.py --days 3\n\n"
        "  Override for reads only (rare, Nick's call): CALENDAR_MCP_OVERRIDE=1."
    )


def main(dialog=osascript_dialog, sign=None, verify=None, allowed=None) -> int:
    allowed = approval_signing.ALLOWED_SIGNERS if allowed is None else allowed
    sign = sign or (lambda data: _sign(data, allowed))
    verify = verify or (lambda data, sig: _verify(data, sig, allowed))
    p = read_payload()
    if not p.ok:
        # Only calendar tools are routed here, so an unreadable payload is a calendar
        # call that cannot be inspected. Fail closed.
        print(f"BLOCKED: calendar connector call with an unreadable payload ({p.error}).",
              file=sys.stderr)
        return 2
    tool_name = p.tool_name
    if not is_calendar_tool(tool_name):
        return 0
    short = tool_name[len(PREFIX):]
    if not is_write(tool_name):
        if os.environ.get("CALENDAR_MCP_OVERRIDE") == "1":
            return 0
        print(read_message(short), file=sys.stderr)
        return 2
    try:
        ok, reason = approve_write(tool_name, p.tool_input, dialog, sign, verify, allowed)
    except Exception as e:  # noqa: BLE001 -- fail closed on anything
        ok, reason = False, f"{type(e).__name__}: {_one_line(str(e))}"
    if ok:
        return 0
    print(f"BLOCKED: calendar {short} needs Nick's Touch ID approval: {reason}.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
