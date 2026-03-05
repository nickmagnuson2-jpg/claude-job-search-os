#!/usr/bin/env python3
"""
gmail_fetch.py — Incremental Gmail sync for inbox/ capture.

Reads new job-related emails from Gmail and writes sanitized markdown
files to inbox/ for processing by /act.

Setup (first run):
  1. Create a Google Cloud project, enable Gmail API, download OAuth credentials.
  2. Place credentials file at tools/gmail_credentials.json (gitignored).
  3. Run: PYTHONIOENCODING=utf-8 python tools/gmail_fetch.py --auth --repo-root .
     This opens a browser OAuth flow and writes tools/gmail_token.json.
  4. Optional: create a "Job Search" label in Gmail, add a Gmail filter rule
     to auto-label job-related emails, then pass --label-id <ID>.
     Find label IDs by running:
       python -c "
       import json; from pathlib import Path; import sys
       sys.path.insert(0, 'tools')
       from gmail_fetch import get_or_refresh_creds
       from googleapiclient.discovery import build
       creds = get_or_refresh_creds(Path('tools'))
       svc = build('gmail', 'v1', credentials=creds)
       labels = svc.users().labels().list(userId='me').execute()
       for l in labels['labels']: print(l['id'], l['name'])
       "
  5. Schedule via Task Scheduler: run tools/run_gmail_fetch.bat every 15–30 minutes.

Subsequent runs (automated):
  PYTHONIOENCODING=utf-8 python tools/gmail_fetch.py --repo-root .

State file: tools/.gmail_state.json
  {"historyId": "...", "last_refresh": "ISO-datetime"}

Security: All email content is treated as untrusted external data.
Body is sanitized before writing to inbox/:
  HTML stripped → invisible unicode removed → injection phrases redacted
  → truncated at 2000 chars → wrapped in XML delimiter.
The XML delimiter (<email-content source="gmail" sanitized="true">) is
detected by act_classify.py to tag items with source_type="gmail", ensuring
the /act skill requires explicit confirmation before writing Gmail items to
any data file.
"""

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Optional dep: BeautifulSoup (better HTML stripping; falls back to regex) ──
try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MAX_BODY_CHARS = 2000

XML_OPEN = '<email-content source="gmail" sanitized="true">'
XML_CLOSE = "</email-content>"

# Prompt injection phrases that should be redacted from email content.
# These patterns cover the most common LLM injection vectors seen in adversarial emails.
INJECTION_PHRASES = [
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous\s+)?instructions",
    r"you\s+are\s+now\s+(?:a|an|the)\b",
    r"new\s+instructions\s*:",
    r"system\s+prompt\s*:",
    r"\[INST\]",
    r"\bassistant\s*:",
]

INJECTION_REGEX = re.compile(
    "|".join(f"(?:{p})" for p in INJECTION_PHRASES),
    re.IGNORECASE,
)

INVISIBLE_UNICODE_REGEX = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)


# ── Pure functions (testable without Gmail API) ────────────────────────────────

def sanitize_body(raw_body: str) -> str:
    """
    Sanitize an email body for safe storage in inbox/.

    Steps:
      1. Strip HTML tags (BeautifulSoup if available, else regex fallback)
      2. Remove invisible/zero-width unicode characters
      3. Redact known prompt injection phrases
      4. Collapse excessive whitespace
      5. Truncate to MAX_BODY_CHARS
      6. Wrap in XML delimiter

    Returns a sanitized string ready for inbox file writing.
    """
    # 1. Strip HTML
    has_html = bool(re.search(r"<(html|body|p|div|span|table|br)\b", raw_body, re.IGNORECASE))
    if has_html and _BS4_AVAILABLE:
        text = BeautifulSoup(raw_body, "html.parser").get_text(separator="\n")
    elif has_html:
        # Regex fallback: strip all tags, decode common entities
        text = re.sub(r"<[^>]+>", " ", raw_body)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&#\d+;", " ", text)
    else:
        text = raw_body

    # 2. Remove invisible unicode
    text = INVISIBLE_UNICODE_REGEX.sub("", text)

    # 3. Redact injection phrases
    text = INJECTION_REGEX.sub("[REDACTED - potential injection]", text)

    # 4. Collapse excessive whitespace (preserve paragraph breaks)
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    text = re.sub(r"[ \t]{2,}", " ", text)

    # 5. Truncate
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + "\n\n[...truncated]"

    # 6. Wrap in XML delimiter
    return f"{XML_OPEN}\n{text}\n{XML_CLOSE}"


def extract_plain_text(mime_parts: list) -> str:
    """
    Extract plain text from a list of Gmail MIME part dicts.

    Prefers text/plain. Falls back to BeautifulSoup (or regex) parsing of text/html.
    Returns empty string if no text parts found.

    mime_parts: list of dicts with keys:
      - 'mimeType': MIME type string
      - 'body': dict containing 'data' (base64url-encoded bytes)
    """
    plain_text = None
    html_text = None

    for part in mime_parts:
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")
        if not body_data:
            continue
        try:
            decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        except Exception:
            continue

        if mime_type == "text/plain" and plain_text is None:
            plain_text = decoded
        elif mime_type == "text/html" and html_text is None:
            html_text = decoded

    if plain_text is not None:
        return plain_text

    if html_text is not None:
        if _BS4_AVAILABLE:
            return BeautifulSoup(html_text, "html.parser").get_text(separator="\n")
        else:
            # Regex fallback
            text = re.sub(r"<[^>]+>", " ", html_text)
            return re.sub(r"\s+", " ", text).strip()

    return ""


def build_inbox_filename(date_str: str, sender: str, subject: str) -> str:
    """
    Build an inbox filename from email metadata.

    date_str: RFC 2822 date from Gmail header, or epoch milliseconds as string.
              Examples: "Mon, 01 Mar 2026 10:30:00 +0000" or "1740825000000"
    sender:   From header (e.g., "Nick <nick@example.com>" or "jobs@company.com")
    subject:  Email subject line

    Returns: "YYYYMMDD-HHMMSS-{slug}.md"
    where slug is derived from sender local-part + subject (max ~60 chars total).
    """
    dt = _parse_email_date(date_str)
    date_prefix = dt.strftime("%Y%m%d-%H%M%S")

    sender_slug = _slugify_sender(sender)
    subject_slug = _slugify_subject(subject)

    if sender_slug and subject_slug:
        slug = f"{sender_slug}-{subject_slug}"
    elif subject_slug:
        slug = subject_slug
    elif sender_slug:
        slug = sender_slug
    else:
        slug = "email"

    return f"{date_prefix}-{slug}.md"


def _parse_email_date(date_str: str) -> datetime:
    """Parse an email date string into a datetime. Falls back to now() on failure."""
    if not date_str:
        return datetime.now()

    # Epoch milliseconds (Gmail internalDate field)
    if date_str.strip().isdigit():
        try:
            from datetime import timezone
            return datetime.fromtimestamp(int(date_str) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError):
            pass

    # RFC 2822 and common variants
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%a, %d %b %Y %H:%M:%S",
    ]
    # Strip timezone offset suffix for simpler parsing
    clean = re.sub(r"\s+[+-]\d{4}\s*$", "", date_str.strip())
    for fmt in formats:
        for candidate in (date_str.strip(), clean):
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=None)
            except ValueError:
                continue

    return datetime.now()


def _slugify_sender(sender: str) -> str:
    """Extract local email part and slugify (max 20 chars)."""
    m = re.search(r"<([^>]+)>", sender)
    email_addr = m.group(1) if m else sender.strip()
    local = email_addr.split("@")[0] if "@" in email_addr else email_addr
    slug = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    return slug[:20] if slug else ""


def _slugify_subject(subject: str) -> str:
    """Convert email subject to a compact slug (max 40 chars)."""
    # Strip common reply/forward prefixes
    text = re.sub(r"^(re:|fwd?:|fw:)\s*", "", subject.strip(), flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] if slug else ""


def write_inbox_file(
    inbox_dir: Path,
    msg_meta: dict,
    sanitized_body: str,
    dry_run: bool = False,
) -> Path:
    """
    Write a sanitized email to inbox/ as a markdown file.

    msg_meta: dict with keys 'date', 'sender', 'subject', 'message_id'
    dry_run:  If True, prints what would be written without creating files.

    Returns: path that was (or would be) written.
    """
    filename = build_inbox_filename(
        msg_meta.get("date", ""),
        msg_meta.get("sender", ""),
        msg_meta.get("subject", ""),
    )

    inbox_dir.mkdir(parents=True, exist_ok=True)
    candidate = inbox_dir / filename

    # Collision avoidance: increment counter suffix if file already exists
    if not dry_run:
        counter = 2
        stem = candidate.stem
        while candidate.exists():
            candidate = inbox_dir / f"{stem}-{counter}.md"
            counter += 1

    content = (
        f"# Email: {msg_meta.get('subject', '(no subject)')}\n\n"
        f"> **From:** {msg_meta.get('sender', '')}\n"
        f"> **Date:** {msg_meta.get('date', '')}\n"
        f"> **Message-ID:** {msg_meta.get('message_id', '')}\n\n"
        f"{sanitized_body}\n"
    )

    if dry_run:
        print(f"[dry-run] Would write: {candidate}")
        print(content[:300] + ("..." if len(content) > 300 else ""))
    else:
        candidate.write_text(content, encoding="utf-8")

    return candidate


def cleanup_old_inbox_files(inbox_dir: Path, hours: int = 48) -> int:
    """
    Delete Gmail-sourced inbox files older than `hours` hours.

    Only deletes files whose content contains source="gmail" (from XML delimiter).
    Preserves all other inbox files regardless of age — manual drops, README, etc.

    Returns: number of files deleted.
    """
    if not inbox_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(hours=hours)
    deleted = 0

    for f in sorted(inbox_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
        except (OSError, PermissionError):
            continue
        if 'source="gmail"' not in content:
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                continue

    return deleted


def check_token_expiry(token_path: Path, inbox_dir: Path) -> bool:
    """
    Check if the Gmail OAuth token is approaching expiry.

    Writes a GMAIL-AUTH-ALERT.md to inbox/ if the token's last refresh was
    more than 5 days ago (Google tokens expire after ~7 days of inactivity).

    Returns True if an alert was written.
    """
    if not token_path.exists():
        return False

    try:
        token_data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    # google-auth-oauthlib writes 'expiry' as an ISO string.
    # We also check 'token_last_refresh' which we write on manual refresh.
    last_refresh_str = token_data.get("token_last_refresh") or token_data.get("expiry")
    if not last_refresh_str:
        return False

    try:
        # Normalize: strip fractional seconds and trailing Z
        normalized = re.sub(r"\.\d+Z?$", "", str(last_refresh_str)).rstrip("Z")
        last_refresh = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False

    days_since = (datetime.now() - last_refresh).days
    if days_since < 5:
        return False

    inbox_dir.mkdir(parents=True, exist_ok=True)
    alert_path = inbox_dir / "GMAIL-AUTH-ALERT.md"
    alert_content = (
        f"# Gmail Auth Alert\n\n"
        f"> **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Gmail OAuth token last refreshed **{days_since} days ago**.\n"
        f"Token may expire soon (Google tokens last ~7 days without use).\n\n"
        f"**Action required:** Re-run the auth flow to refresh:\n"
        f"```\n"
        f"PYTHONIOENCODING=utf-8 python tools/gmail_fetch.py --auth --repo-root .\n"
        f"```\n"
    )
    alert_path.write_text(alert_content, encoding="utf-8")
    return True


# ── Gmail API functions (require google-api-python-client) ────────────────────

def get_or_refresh_creds(tools_dir: Path, auth_mode: bool = False):
    """
    Load or create Gmail OAuth credentials.

    auth_mode=True (--auth flag): runs full browser OAuth flow, writes token.json.
    auth_mode=False: loads existing token.json, refreshes silently if expired.

    Returns google.oauth2.credentials.Credentials object.
    Raises SystemExit with helpful message if setup is incomplete.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "ERROR: Google API libraries not installed.\n"
            "Install with:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib",
            file=sys.stderr,
        )
        sys.exit(1)

    creds_path = tools_dir / "gmail_credentials.json"
    token_path = tools_dir / "gmail_token.json"

    if auth_mode:
        if not creds_path.exists():
            print(
                f"ERROR: {creds_path} not found.\n"
                "Download OAuth 2.0 credentials from Google Cloud Console:\n"
                "  APIs & Services → Credentials → Create OAuth 2.0 Client ID\n"
                "  Application type: Desktop app\n"
                f"  Save as: {creds_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"Token saved to {token_path}")
        return creds

    if not token_path.exists():
        print(
            "ERROR: No token found. Run auth first:\n"
            "  python tools/gmail_fetch.py --auth --repo-root .",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data = json.loads(creds.to_json())
            token_data["token_last_refresh"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            token_path.write_text(json.dumps(token_data), encoding="utf-8")
        else:
            print(
                "ERROR: Token invalid and cannot be refreshed. Re-run auth:\n"
                "  python tools/gmail_fetch.py --auth --repo-root .",
                file=sys.stderr,
            )
            sys.exit(1)

    return creds


def _load_state(state_path: Path) -> dict:
    """Load sync state from .gmail_state.json, or return empty dict."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: Path, state: dict) -> None:
    """Save sync state to .gmail_state.json."""
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _seed_history_id(service, count: int = 50) -> str:
    """
    Seed historyId from the last `count` messages without importing them.

    Called on --auth and on 404 recovery. Returns the historyId string.
    """
    try:
        result = service.users().messages().list(userId="me", maxResults=count).execute()
        messages = result.get("messages", [])
        if not messages:
            profile = service.users().getProfile(userId="me").execute()
            return str(profile.get("historyId", "1"))
        msg = service.users().messages().get(
            userId="me", id=messages[0]["id"], format="metadata"
        ).execute()
        return str(msg.get("historyId", "1"))
    except Exception as e:
        print(f"Warning: could not seed historyId: {e}", file=sys.stderr)
        return "1"


def fetch_new_messages(service, state: dict, label_id: str | None = None) -> list:
    """
    Fetch new messages since last sync using the Gmail history API.

    On 404 (historyId expired): reseeds from last 20 messages and returns [].
    State dict is updated in-place with the new historyId.

    Returns list of full message dicts (format=full).
    """
    history_id = state.get("historyId")
    if not history_id:
        return []

    try:
        kwargs = {
            "userId": "me",
            "startHistoryId": history_id,
            "historyTypes": ["messageAdded"],
        }
        if label_id:
            kwargs["labelId"] = label_id
        response = service.users().history().list(**kwargs).execute()
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "invalidHistoryId" in err_str.lower():
            print("Warning: historyId expired — reseeding from last 20 messages.", file=sys.stderr)
            state["historyId"] = _seed_history_id(service, count=20)
            return []
        raise

    histories = response.get("history", [])
    message_ids: list[str] = []
    seen: set[str] = set()
    for hist in histories:
        for added in hist.get("messagesAdded", []):
            msg_id = added["message"]["id"]
            if msg_id not in seen:
                seen.add(msg_id)
                message_ids.append(msg_id)

    if "historyId" in response:
        state["historyId"] = response["historyId"]

    if not message_ids:
        return []

    messages = []
    for msg_id in message_ids:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            # Re-check label membership (history labelId filter is not always reliable)
            if label_id and label_id not in msg.get("labelIds", []):
                continue
            messages.append(msg)
        except Exception as e:
            print(f"Warning: could not fetch message {msg_id}: {e}", file=sys.stderr)

    return messages


def fetch_labeled_messages(
    service,
    label_id: str,
    since_date: str | None = None,
    max_messages: int | None = None,
) -> list:
    """
    Fetch all messages with a given label using messages.list (not history API).

    Used for --backfill. Does NOT touch historyId state — forward-sync is unaffected.

    label_id:     Gmail label ID (e.g., "Label_7175134973725917628")
    since_date:   ISO date string "YYYY-MM-DD" — only fetch messages after this date.
                  Uses Gmail query syntax (after:YYYY/MM/DD).
    max_messages: Hard cap on total messages fetched (newest first).

    Returns list of full message dicts (format=full), newest first.
    """
    kwargs: dict = {"userId": "me", "labelIds": [label_id], "maxResults": 100}
    if since_date:
        kwargs["q"] = "after:" + since_date.replace("-", "/")

    message_ids: list[str] = []
    page_token = None

    while True:
        if page_token:
            kwargs["pageToken"] = page_token
        response = service.users().messages().list(**kwargs).execute()
        for m in response.get("messages", []):
            message_ids.append(m["id"])
        if max_messages and len(message_ids) >= max_messages:
            message_ids = message_ids[:max_messages]
            break
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    total = len(message_ids)
    print(f"Backfill: found {total} message(s) with label. Fetching full content...")

    messages = []
    for i, msg_id in enumerate(message_ids, 1):
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            messages.append(msg)
            if i % 10 == 0 or i == total:
                print(f"  Fetched {i}/{total}...")
        except Exception as e:
            print(f"Warning: could not fetch message {msg_id}: {e}", file=sys.stderr)

    return messages


def _extract_header(headers: list, name: str) -> str:
    """Extract a specific header value from Gmail headers list."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _get_all_mime_parts(payload: dict) -> list:
    """Recursively collect all MIME parts from a message payload."""
    parts = []
    if payload.get("body", {}).get("data"):
        parts.append(payload)
    for part in payload.get("parts", []):
        parts.extend(_get_all_mime_parts(part))
    return parts


def _process_message(msg: dict) -> tuple[dict, str]:
    """
    Extract metadata and raw body from a full Gmail message dict.

    Returns (msg_meta, raw_body) where msg_meta has keys:
      message_id, sender, subject, date
    """
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    msg_meta = {
        "message_id": msg.get("id", ""),
        "sender": _extract_header(headers, "From"),
        "subject": _extract_header(headers, "Subject") or "(no subject)",
        "date": _extract_header(headers, "Date") or str(msg.get("internalDate", "")),
    }
    mime_parts = _get_all_mime_parts(payload)
    raw_body = extract_plain_text(mime_parts)
    return msg_meta, raw_body


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Incremental Gmail sync — writes sanitized emails to inbox/."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Run OAuth flow to create gmail_token.json, seed historyId, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without creating any files.",
    )
    parser.add_argument(
        "--label-id",
        default=None,
        help=(
            "Gmail label ID to filter (e.g., Label_12345). "
            "Create a 'Job Search' label + filter in Gmail, then paste the label ID here."
        ),
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "Fetch all historical messages with --label-id and write them to inbox/. "
            "Does not affect forward-sync state (historyId unchanged). "
            "Combine with --since and/or --max to limit scope."
        ),
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Backfill only messages after this date (e.g., --since 2026-01-01).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        dest="max_messages",
        metavar="N",
        help="Cap the number of messages fetched during --backfill (e.g., --max 50).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    tools_dir = repo_root / "tools"
    inbox_dir = repo_root / "inbox"
    state_path = tools_dir / ".gmail_state.json"
    token_path = tools_dir / "gmail_token.json"

    # ── Auth mode: OAuth flow + seed historyId + exit ─────────────────────
    if args.auth:
        creds = get_or_refresh_creds(tools_dir, auth_mode=True)
        try:
            from googleapiclient.discovery import build
        except ImportError:
            print("ERROR: googleapiclient not installed.", file=sys.stderr)
            sys.exit(1)
        service = build("gmail", "v1", credentials=creds)
        print("Auth successful. Seeding historyId from last 50 messages (not importing them)...")
        history_id = _seed_history_id(service, count=50)
        state = {"historyId": history_id, "last_refresh": datetime.now().isoformat()}
        _save_state(state_path, state)
        print(f"State saved: {state_path} (historyId={history_id})")
        print("Setup complete. Schedule tools/run_gmail_fetch.bat to run every 15–30 minutes.")
        return

    # ── Backfill mode: fetch historical labeled messages ──────────────────
    if args.backfill:
        if not args.label_id:
            print("ERROR: --backfill requires --label-id.", file=sys.stderr)
            sys.exit(1)
        creds = get_or_refresh_creds(tools_dir, auth_mode=False)
        try:
            from googleapiclient.discovery import build
        except ImportError:
            print("ERROR: googleapiclient not installed.", file=sys.stderr)
            sys.exit(1)
        service = build("gmail", "v1", credentials=creds)
        messages = fetch_labeled_messages(
            service,
            label_id=args.label_id,
            since_date=args.since,
            max_messages=args.max_messages,
        )
        written = 0
        for msg in messages:
            try:
                msg_meta, raw_body = _process_message(msg)
                sanitized = sanitize_body(raw_body)
                path = write_inbox_file(inbox_dir, msg_meta, sanitized, dry_run=args.dry_run)
                if not args.dry_run:
                    print(f"  Wrote: {path.name}")
                    written += 1
            except Exception as e:
                print(f"Warning: could not process message {msg.get('id', '?')}: {e}", file=sys.stderr)
        print(f"Backfill done. Wrote {written} inbox file(s). Forward-sync state unchanged.")
        return

    # ── Sync mode ─────────────────────────────────────────────────────────
    check_token_expiry(token_path, inbox_dir)

    creds = get_or_refresh_creds(tools_dir, auth_mode=False)
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: googleapiclient not installed.", file=sys.stderr)
        sys.exit(1)
    service = build("gmail", "v1", credentials=creds)

    state = _load_state(state_path)
    if not state.get("historyId"):
        print(
            "No historyId found. Run --auth first:\n"
            "  python tools/gmail_fetch.py --auth --repo-root .",
            file=sys.stderr,
        )
        sys.exit(1)

    # Cleanup old Gmail files before fetching new ones
    deleted = cleanup_old_inbox_files(inbox_dir, hours=48)
    if deleted:
        print(f"Cleaned up {deleted} old Gmail inbox file(s).")

    messages = fetch_new_messages(service, state, label_id=args.label_id)
    print(f"Found {len(messages)} new message(s).")

    written = 0
    for msg in messages:
        try:
            msg_meta, raw_body = _process_message(msg)
            sanitized = sanitize_body(raw_body)
            path = write_inbox_file(inbox_dir, msg_meta, sanitized, dry_run=args.dry_run)
            if not args.dry_run:
                print(f"  Wrote: {path.name}")
                written += 1
        except Exception as e:
            print(f"Warning: could not process message {msg.get('id', '?')}: {e}", file=sys.stderr)

    state["last_refresh"] = datetime.now().isoformat()
    if not args.dry_run:
        _save_state(state_path, state)

    print(f"Done. Wrote {written} inbox file(s).")


if __name__ == "__main__":
    main()
