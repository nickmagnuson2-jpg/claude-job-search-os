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

Decision log (Nick, 2026-10-28, after the first live approval: "yes I touched it, add the
log"): one JSON line per calendar decision, appended to DECISION_LOG outside the repo
({ts, tool_name, decision, sha256}). The hash is of the canonical call, so a line can be
matched to a call without holding any of it: never the title, times, invitees or any other
event text. Writing the log can never change a decision; a failure to write is swallowed.
Decisions: approved, declined, timed_out, no_signer, sign_failed, verify_failed, error,
read_blocked, read_override.

The approval mechanism (dialog, signing, decision log) lives in tools/calendar_approval.py,
shared with tools/calendar_write.py, the local writer that replaces this connector's writes.
This hook stays as the guard for the connector in case it is ever reconnected.

Split from tools/check_gmail_read_mcp.py on 2026-10-28: that hook is Gmail-only again, and
only this one needs the long timeout.

Exit codes: 0 allow, 2 block.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
import approval_signing  # noqa: E402
import calendar_approval as ca  # noqa: E402
from calendar_approval import (  # noqa: E402,F401 -- re-exported for callers and tests
    DIALOG_SECONDS, FIELD_CHARS, LABELS, SETUP, SIGN_SECONDS, canonical, invitees,
    log_decision, osascript_dialog, signer_allows_calendar)

PREFIX = "mcp__claude_ai_Google_Calendar__"
READ_TOOLS = {PREFIX + t for t in (
    "list_events", "get_event", "search_events", "list_calendars", "suggest_time")}
_Refused = ca.Refused
_one_line = ca.one_line


def is_calendar_tool(tool_name) -> bool:
    return isinstance(tool_name, str) and tool_name.startswith(PREFIX)


def is_write(tool_name) -> bool:
    """Every calendar tool that is not a known read."""
    return is_calendar_tool(tool_name) and tool_name not in READ_TOOLS


def _action(tool_name: str) -> str:
    return tool_name[len(PREFIX):].replace("_", " ").upper()


def _note(tool_name: str) -> str:
    return ("Your response is sent to the organizer."
            if tool_name == PREFIX + "respond_to_event" else "")


def dialog_text(tool_name: str, tool_input: dict) -> str:
    return ca.dialog_text(_action(tool_name), tool_input, _note(tool_name))


def approve_write(tool_name, tool_input, dialog, sign, verify, allowed) -> tuple[bool, str]:
    """(True, reason), or _Refused with a decision code. Every failure is a refusal."""
    ca.approve(tool_name, tool_input, _action(tool_name), dialog, sign, verify, allowed,
               _note(tool_name))
    return True, "approved with Touch ID"


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
    sign = sign or (lambda data: ca.sign(data, allowed))
    verify = verify or (lambda data, sig: ca.verify(data, sig, allowed))
    p = read_payload()
    if not p.ok:
        # Only calendar tools are routed here, so an unreadable payload is a calendar
        # call that cannot be inspected. Fail closed. Not logged: repo-wide hook tests feed
        # junk stdin to every wired hook, and those lines would land in the real log.
        print(f"BLOCKED: calendar connector call with an unreadable payload ({p.error}).",
              file=sys.stderr)
        return 2
    tool_name = p.tool_name
    if not is_calendar_tool(tool_name):
        return 0
    short = tool_name[len(PREFIX):]
    if not is_write(tool_name):
        if os.environ.get("CALENDAR_MCP_OVERRIDE") == "1":
            log_decision(tool_name, p.tool_input, "read_override")
            return 0
        log_decision(tool_name, p.tool_input, "read_blocked")
        print(read_message(short), file=sys.stderr)
        return 2
    try:
        ok, reason = approve_write(tool_name, p.tool_input, dialog, sign, verify, allowed)
        decision = "approved" if ok is True else "error"
    except _Refused as r:
        ok, reason, decision = False, r.reason, r.decision
    except Exception as e:  # noqa: BLE001 -- fail closed on anything
        ok, reason, decision = False, f"{type(e).__name__}: {_one_line(str(e))}", "error"
    log_decision(tool_name, p.tool_input, decision)
    if ok is True:
        return 0
    print(f"BLOCKED: calendar {short} needs Nick's Touch ID approval: {reason}.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
