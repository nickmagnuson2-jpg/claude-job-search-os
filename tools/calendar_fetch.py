#!/usr/bin/env python3
"""
calendar_fetch.py — Read-only list of upcoming Google Calendar events, as JSON.

The morning page needs calls on the calendar: a replay of October found two real
calls (2026-10-13, 2026-10-27) in no file the standup collector reads. Nick approved
read-only access on 2026-10-27: "yeah give google with readonly access".

Enforcement tier: a dependency Google enforces. The token this script writes is
granted only calendar.readonly, so no code path here (or anything importing it)
can create, change, or delete an event; the API refuses. It is a SEPARATE token
from Gmail's, so granting it changes nothing about the Gmail token's scope.

Setup (once):
  1. In the same Google Cloud project as Gmail, enable the Google Calendar API.
  2. Run: PYTHONIOENCODING=utf-8 python3 tools/calendar_fetch.py --auth
     This reuses tools/gmail_credentials.json (the OAuth client) and writes
     tools/calendar_token.json (gitignored).

Run:
  PYTHONIOENCODING=utf-8 python3 tools/calendar_fetch.py [--days 3] [--calendar primary]

Prints {"source", "sanitized", "generated_at", "window_start", "window_end", "events": [...]}
to stdout. Each event: start, end (ISO), all_day, summary, location, calendar; summary and
location are sanitized (gmail_fetch.sanitize_text) because invites are stranger-written, and
`private` is true for an event redacted to "Private appointment" (PRIVATE_DOMAINS). A refusal
from Google prints one ERROR line to stderr and exits 1. Nothing is written
to disk except the refreshed token. Caller: the read-only morning page producer
(plan: output/codify-ui-102726/03-plan.md section 4); until it exists, run by hand.
"""

import argparse
import json
import re
import sys
import unicodedata
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gmail_fetch import _write_token_atomic, sanitize_text  # one copy of each, not a copy

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
# Event text is untrusted: anyone can send an invite, so its title and location are
# stranger-written, the same as an email body (how-i-work-with-ai.md, Claim 17). Both go
# through gmail_fetch's sanitizer, capped short. Descriptions and attendees are never
# fetched into the output at all. The output carries SOURCE and sanitized=true so a
# consumer knows the strings came from outside.
SOURCE = "google-calendar"
MAX_FIELD_CHARS = 200


def _clean(text: str) -> str:
    """Sanitized, on one line: a title or location has no paragraphs to keep."""
    return " ".join(sanitize_text(text, MAX_FIELD_CHARS).split())

# Private appointments. The first real fetch (2026-10-28) printed a health appointment's
# provider and its private session link into the session, though personal health material
# is sealed everywhere else in this repo. An event whose location, description, or
# conference links name one of these telehealth hosts is printed as "Private appointment"
# with its times only: the morning page still shows the hour is taken. Matched by
# hostname, the domain itself or a subdomain, never by substring, so
# "notsimplepractice.com" is not private. Nick approved 2026-10-28: "I like this fix".
# A generic list of common platforms, so it names no one's provider.
PRIVATE_DOMAINS = ("simplepractice.com", "doxy.me", "therapynotes.com", "headway.co",
                   "sondermind.com", "betterhelp.com", "talkspace.com")
PRIVATE_SUMMARY = "Private appointment"
_HOST_RE = re.compile(r"(?<![A-Za-z0-9.-])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


# Dots a hostname may be spelled with that IDNA treats as a full stop (UTS 46): ideographic,
# fullwidth and halfwidth. Codex calendar-gate r1 F1: "video\u3002simplepractice\u3002com" and
# a percent-encoded "%2E" both slipped past an ASCII-only match.
_DOTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})


def _normalize(text: str) -> str:
    """Percent-decoded (twice, for double encoding), NFKC-folded, every dot spelling an ASCII dot."""
    return unicodedata.normalize("NFKC", unquote(unquote(text))).translate(_DOTS)


def _hosts(text: str) -> set[str]:
    """Every hostname-shaped token in `text`, lowercased, with or without a scheme."""
    return {m.group(1).lower().strip(".")
            for m in _HOST_RE.finditer(_normalize(text or ""))}


def is_private(item: dict) -> bool:
    """True when any field of the raw event names a PRIVATE_DOMAINS host."""
    texts = [item.get("location"), item.get("description"), item.get("hangoutLink")]
    texts += [ep.get("uri") for ep in (item.get("conferenceData") or {}).get("entryPoints") or []]
    for host in set().union(*(_hosts(t) for t in texts if isinstance(t, str))):
        if any(host == d or host.endswith("." + d) for d in PRIVATE_DOMAINS):
            return True
    return False


CREDENTIALS_FILE = "gmail_credentials.json"
TOKEN_FILE = "calendar_token.json"


def window(now: datetime, days: int) -> tuple[datetime, datetime]:
    """From local midnight today to local midnight `days` days later."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=days)


def normalize_event(item: dict, calendar: str) -> dict | None:
    """One API event to the page's shape. Cancelled events return None."""
    if item.get("status") == "cancelled":
        return None
    start, end = item.get("start", {}), item.get("end", {})
    all_day = "date" in start and "dateTime" not in start
    private = is_private(item)
    return {
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": all_day,
        "summary": PRIVATE_SUMMARY if private else _clean(item.get("summary") or "") or "(no title)",
        "location": "" if private else _clean(item.get("location") or ""),
        "calendar": calendar,
        "private": private,
    }


def normalize(items_by_calendar: dict[str, list[dict]]) -> list[dict]:
    """All calendars' events, cancelled ones dropped, sorted by start."""
    out = []
    for cal, items in items_by_calendar.items():
        for item in items:
            ev = normalize_event(item, cal)
            if ev is not None:
                out.append(ev)
    return sorted(out, key=lambda e: e["start"] or "")


def get_creds(tools_dir: Path, auth_mode: bool = False, token_file: str = TOKEN_FILE,
              scopes: list = SCOPES, tool: str = "calendar_fetch.py"):
    """Load or create a calendar token. Never touches the Gmail token. token_file, scopes
    and tool let tools/calendar_write.py keep its own token with its own single scope."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = tools_dir / CREDENTIALS_FILE
    token_path = tools_dir / token_file

    if auth_mode:
        if not creds_path.exists():
            sys.exit(f"ERROR: {creds_path} is missing (the Gmail OAuth client; see gmail_fetch.py setup).")
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
        creds = flow.run_local_server(port=0)
        _write_token_atomic(token_path, creds.to_json())
        token_path.chmod(0o600)
        print(f"Token saved to {token_path}")
        return creds

    if not token_path.exists():
        sys.exit(f"ERROR: no calendar token. Run: PYTHONIOENCODING=utf-8 python3 tools/{tool} --auth")
    creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_token_atomic(token_path, creds.to_json())
            token_path.chmod(0o600)
        else:
            sys.exit("ERROR: calendar token invalid and cannot refresh. Re-run with --auth.")
    return creds


def fetch(creds, calendars: list[str], start: datetime, end: datetime) -> dict[str, list[dict]]:
    from googleapiclient.discovery import build

    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    out = {}
    for cal in calendars:
        items, token = [], None
        while True:
            resp = svc.events().list(
                calendarId=cal, timeMin=start.isoformat(), timeMax=end.isoformat(),
                singleEvents=True, orderBy="startTime", pageToken=token,
            ).execute()
            items += resp.get("items", [])
            token = resp.get("nextPageToken")
            if not token:
                break
        out[cal] = items
    return out


def _api_error(e: BaseException) -> str | None:
    """One line for a refusal from Google or the network; None for anything else (a bug,
    which should keep its traceback). The first real run (2026-10-28) hit a disabled API
    and printed a full traceback."""
    kind = type(e).__name__
    mod = type(e).__module__ or ""
    if kind == "HttpError":
        status = getattr(getattr(e, "resp", None), "status", "?")
        reason = str(getattr(e, "reason", "") or "")
        # The reason alone is often just "Bad Request"; the detail says which field.
        details = getattr(e, "error_details", None) or []
        msgs = [d.get("message", "") for d in details if isinstance(d, dict)]
        if any(msgs) and " ".join(msgs) not in reason:
            reason += ": " + "; ".join(m for m in msgs if m)
        reason = " ".join(reason.split())[:300]
        return f"ERROR: Google Calendar API refused the request (HTTP {status}): {reason}"
    if mod.startswith("google.auth") or kind == "RefreshError":
        return (f"ERROR: calendar login failed ({kind}: {' '.join(str(e).split())[:200]}). "
                "Re-run with --auth.")
    if isinstance(e, OSError):
        return f"ERROR: could not reach Google ({kind}: {' '.join(str(e).split())[:200]})."
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--auth", action="store_true", help="run the browser OAuth flow and exit")
    ap.add_argument("--days", type=int, default=3, help="window length from local midnight today (default 3)")
    ap.add_argument("--calendar", action="append", help="calendar id; repeatable (default: primary)")
    args = ap.parse_args(argv)

    tools_dir = Path(__file__).resolve().parent
    now = datetime.now(timezone.utc).astimezone()
    start, end = window(now, args.days)
    try:
        creds = get_creds(tools_dir, auth_mode=args.auth)
        if args.auth:
            return 0
        events = normalize(fetch(creds, args.calendar or ["primary"], start, end))
    except Exception as e:  # noqa: BLE001 -- narrowed by _api_error; anything else re-raises
        msg = _api_error(e)
        if msg is None:
            raise
        print(msg, file=sys.stderr)
        return 1
    print(json.dumps({
        "source": SOURCE,
        "sanitized": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "events": events,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
