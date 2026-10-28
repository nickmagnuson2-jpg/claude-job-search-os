#!/usr/bin/env python3
"""
calendar_write.py — create, update or delete one Google Calendar event, each only after
Nick approves that exact request with Touch ID.

Replaces the claude.ai Calendar connector's writes. Nick, 2026-10-28: "I dont love all the
access that I just gave but The Touch ID gate is nice", and "See how I did the Gmail fetch
with the airgapped script". So this works the way gmail_fetch.py and calendar_fetch.py do:
Nick's own OAuth client (tools/gmail_credentials.json), its OWN token file
(tools/calendar_write_token.json, gitignored, 0600), and ONE scope, calendar.events.owned,
which allows events on calendars Nick owns and nothing else: no settings, no sharing, no
other people's calendars, no mail.

Enforcement tier: a check a gate reads, inside the tool. Before any request reaches Google
the tool shows the same dialog as tools/check_calendar_mcp.py (tools/calendar_approval.py,
one copy), signs the canonical request under the calendar-write-approval namespace with
Nick's Secure Enclave key (Touch ID prompts), and verifies the signature. Decline, timeout,
no signer line, a failed or unverified signature, or any error: exit 2, nothing sent.
Every decision is logged by hash to the gate's log (calendar_approval.DECISION_LOG).

What it does NOT stop: the token on disk is the capability. A process that reads
tools/calendar_write_token.json can call the API without this tool, the same as the Gmail
token. The Touch ID gate covers this tool's path, not the file.

Invitations: sendUpdates is "none" unless attendees are given, so creating, moving or
deleting an event emails nobody. With attendees it is "all", and the dialog says so.

Setup (once):
  PYTHONIOENCODING=utf-8 python3 tools/calendar_write.py --auth

Run:
  ... calendar_write.py create --title T --start 2026-11-02T10:00 --end 2026-11-02T10:30
                               [--location L] [--attendee a@x.com ...] [--calendar primary]
  ... calendar_write.py update EVENT_ID [--title T] [--start S] [--end E] [--location L]
                               [--attendee a@x.com ...]
  ... calendar_write.py delete EVENT_ID
Times are ISO 8601. A date alone (2026-11-02) is an all-day event; a time without an
offset is read in --timezone (default America/Los_Angeles).

Exit codes: 0 done, 1 Google refused or no token, 2 not approved or bad arguments.
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import calendar_approval as ca
import calendar_fetch

SCOPES = ["https://www.googleapis.com/auth/calendar.events.owned"]
TOKEN_FILE = "calendar_write_token.json"
DEFAULT_TZ = "America/Los_Angeles"
NAME = "calendar_write."
# A time line reads "<time> (<zone>)": 40 + 3 + 64 stays under FIELD_CHARS, so it is never
# capped in the dialog (Codex calendar-write r2 F1).
MAX_TIME_CHARS = 40
MAX_TZ_CHARS = 64
UPDATE_NOTE = "Attendees given here REPLACE the event's whole attendee list."


def _usage(msg: str):
    """Bad arguments: one line, exit 2, before any dialog or request."""
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _when(value: str, tz: str) -> dict:
    """One API start/end: a date alone is all-day; a time keeps the given time zone."""
    try:
        return {"date": date.fromisoformat(value).isoformat()}
    except ValueError:
        pass
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        _usage(f"not an ISO 8601 date or time: {value!r}")
    # Google wants RFC 3339, which requires seconds: "2026-10-29T09:00" got HTTP 400 on the
    # first live create (2026-10-28) while the fake service in the tests accepted it.
    # Normalized here, before the dialog, so Nick signs and sees the value that is sent.
    return {"dateTime": when.isoformat(timespec="seconds"), "timeZone": tz}


def _shown_in_full(name: str, value: str, limit: int = ca.FIELD_CHARS) -> None:
    """A value the dialog will show exactly as sent: printable and within FIELD_CHARS.
    Codex calendar-write r1 F2: the dialog caps a field at FIELD_CHARS while the full
    value was signed and sent, so a long title or address could hide its tail."""
    if len(value) > limit or not value.isprintable() or value != " ".join(value.split()):
        _usage(f"{name} must be one printable line, at most {limit} characters, "
               "with single spaces, so the dialog shows it exactly as sent.")


def build_request(args) -> dict:
    """The exact request sent to Google, which is also what Nick signs."""
    for name in ("calendar", "event_id", "title", "location", "start", "end"):
        value = getattr(args, name, None)
        if value is not None:
            limit = MAX_TIME_CHARS if name in ("start", "end") else ca.FIELD_CHARS
            _shown_in_full("--" + name.replace("_", "-"), value, limit)
    for a in getattr(args, "attendee", None) or []:
        _shown_in_full("--attendee", a)
    # Shown beside each time as "<time> (<zone>)"; kept short so that line is never capped
    # (Grok calendar-write r1 F1). IANA zone names are far shorter than this.
    if len(getattr(args, "timezone", None) or "") > MAX_TZ_CHARS or not (
            getattr(args, "timezone", None) or "x").isprintable():
        _usage(f"--timezone must be a zone name, at most {MAX_TZ_CHARS} characters.")
    req = {"action": args.command, "calendarId": args.calendar}
    if args.command != "create":
        req["eventId"] = args.event_id
    if args.command == "delete":
        req["sendUpdates"] = "none"
        return req
    body = {}
    if args.title is not None:
        body["summary"] = args.title
    if args.start is not None:
        body["start"] = _when(args.start, args.timezone)
    if args.end is not None:
        body["end"] = _when(args.end, args.timezone)
    if args.location is not None:
        body["location"] = args.location
    if args.attendee:
        body["attendees"] = [{"email": a} for a in args.attendee]
    if args.command == "create" and not {"summary", "start", "end"} <= set(body):
        _usage("create needs --title, --start and --end.")
    if not body:
        _usage("update needs at least one field to change.")
    req["body"] = body
    req["sendUpdates"] = "all" if args.attendee else "none"
    return req


def dialog_fields(req: dict) -> dict:
    """The request in the dialog's vocabulary (calendar_approval.LABELS)."""
    body = req.get("body", {})
    fields = {k: req[k] for k in ("calendarId", "eventId") if k in req}
    if "summary" in body:
        fields["summary"] = body["summary"]
    for key, label in (("start", "startTime"), ("end", "endTime")):
        if key in body:
            # The time zone decides the instant, so it is shown with the time (Codex r1 F1).
            w = body[key]
            fields[label] = (f"{w['dateTime']} ({w['timeZone']})" if "dateTime" in w
                             else f"{w['date']} (all day)")
    if "location" in body:
        fields["location"] = body["location"]
    if "attendees" in body:
        fields["attendees"] = body["attendees"]
    fields["sendUpdates"] = req["sendUpdates"]
    return fields


def execute(service, req: dict) -> dict:
    events = service.events()
    kw = {"calendarId": req["calendarId"], "sendUpdates": req["sendUpdates"]}
    if req["action"] == "create":
        return events.insert(body=req["body"], **kw).execute()
    if req["action"] == "update":
        return events.patch(eventId=req["eventId"], body=req["body"], **kw).execute()
    events.delete(eventId=req["eventId"], **kw).execute()
    return {"id": req["eventId"]}


def build_service(creds):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--auth", action="store_true", help="run the browser OAuth flow and exit")
    sub = ap.add_subparsers(dest="command")
    for name in ("create", "update", "delete"):
        p = sub.add_parser(name)
        if name != "create":
            p.add_argument("event_id")
        p.add_argument("--calendar", default="primary")
        if name == "delete":
            continue
        p.add_argument("--title")
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--location")
        p.add_argument("--attendee", action="append", default=[])
        p.add_argument("--timezone", default=DEFAULT_TZ)
    args = ap.parse_args(argv)
    if not args.auth and args.command is None:
        ap.error("give --auth or one of create, update, delete")
    return args


def main(argv=None, dialog=ca.osascript_dialog, sign=None, verify=None, allowed=None,
         service_factory=build_service, tools_dir=None) -> int:
    args = parse(argv)
    tools_dir = Path(__file__).resolve().parent if tools_dir is None else tools_dir
    get = lambda auth=False: calendar_fetch.get_creds(  # noqa: E731
        tools_dir, auth_mode=auth, token_file=TOKEN_FILE, scopes=SCOPES,
        tool="calendar_write.py")
    if args.auth:
        get(auth=True)
        return 0
    if not (tools_dir / TOKEN_FILE).exists():
        print("ERROR: no calendar write token. Run: PYTHONIOENCODING=utf-8 python3 "
              "tools/calendar_write.py --auth", file=sys.stderr)
        return 1

    req = build_request(args)
    name = NAME + req["action"]
    allowed = ca.approval_signing.ALLOWED_SIGNERS if allowed is None else allowed
    sign = sign or (lambda data: ca.sign(data, allowed))
    verify = verify or (lambda data, sig: ca.verify(data, sig, allowed))
    note = UPDATE_NOTE if req["action"] == "update" and "attendees" in req.get("body", {}) else ""
    try:
        ca.approve(name, req, f"{req['action'].upper()} EVENT (calendar_write.py)",
                   dialog, sign, verify, allowed, note, shown=dialog_fields(req))
    except ca.Refused as r:
        ca.log_decision(name, req, r.decision)
        print(f"BLOCKED: calendar {req['action']} not sent: {r.reason}.", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 -- fail closed on anything
        ca.log_decision(name, req, "error")
        print(f"BLOCKED: calendar {req['action']} not sent: {type(e).__name__}: "
              f"{ca.one_line(str(e))}.", file=sys.stderr)
        return 2
    ca.log_decision(name, req, "approved")

    try:
        result = execute(service_factory(get()), req)
    except Exception as e:  # noqa: BLE001 -- narrowed by _api_error; anything else re-raises
        msg = calendar_fetch._api_error(e)
        if msg is None:
            raise
        print(msg, file=sys.stderr)
        return 1
    print(json.dumps({"status": req["action"] + "d", "id": result.get("id"),
                      "htmlLink": result.get("htmlLink")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
