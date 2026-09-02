#!/usr/bin/env python3
"""
gmail_fetch.py — Incremental Gmail sync for inbox/ capture.

Reads new job-related emails from Gmail and writes sanitized markdown
files to inbox/ for processing by /act.

Setup (first run):
  1. Create a Google Cloud project, enable Gmail API, download OAuth credentials.
  2. Place credentials file at tools/gmail_credentials.json (gitignored).
  3. Run: PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --auth --repo-root .
     This opens a browser OAuth flow and writes tools/gmail_token.json.
  4. Optional: create a "Job Search" label in Gmail, add a Gmail filter rule
     to auto-label job-related emails, then pass --label-id <ID>.
     Find label IDs by running:
       PYTHONIOENCODING=utf-8 python3 -c "
       import json; from pathlib import Path; import sys
       sys.path.insert(0, 'tools')
       from gmail_fetch import get_or_refresh_creds
       from googleapiclient.discovery import build
       creds = get_or_refresh_creds(Path('tools'))
       svc = build('gmail', 'v1', credentials=creds)
       labels = svc.users().labels().list(userId='me').execute()
       for l in labels['labels']: print(l['id'], l['name'])
       "
  5. Schedule via launchd: bash tools/launchd/install.sh install
     (the gmail-fetch job runs every 15 min). Logs at tools/launchd/logs/.

Subsequent runs (automated):
  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --repo-root .

Ad-hoc search (read-only; does not touch sync state or inbox/):
  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --search "from:jordan acme" --repo-root .
  Full Gmail query syntax. Defaults to the Job Search label scope; add --all-mail
  to search everything, --label-id <ID> for another label, --max N to cap results
  (default 25), --body to print each plain-text body (truncated).

  --search REQUIRES A NON-EMPTY QUERY. There is no "search with no query" listing
  mode. `--search ""` exits 2 rather than doing something surprising: before the
  2026-08-19 guard, an empty string was falsy, so the read-only branch was skipped
  and control fell through to the forward SYNC below, which WRITES to inbox/ and
  advances .gmail_state.json. Same guard on --label-id / --since / --inbox-dir /
  --state-file: an empty or whitespace-only value is an error, never a silent
  fallback to the default.

To LIST a label read-only (the thing `--search ""` looks like it would do):
  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --backfill --label-id <ID> \
      --max 25 --dry-run --repo-root .
  --backfill leaves historyId untouched; --dry-run prints "[dry-run] Would write:"
  plus a 300-char preview per message and creates no files (it does still mkdir the
  inbox dir if absent). Drop --dry-run only when you want the messages imported.

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
import os
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

# Default scope for --search: the "Job Search" Gmail label. Free-text searches
# default to this label unless --label-id overrides it or --all-mail widens it.
# (Matches the label-id used by the gmail-fetch launchd job.)
# GMAIL_LABELS_CONF_PATH overrides for tests, mirroring the PII_REPO_ROOT /
# SESSION_REPO_ROOT seams. Without it the unconfigured-path test cannot be written
# honestly: the CLI exits on the missing Gmail token first, so the test would pass
# for the wrong reason and stay green with the error path deleted.
GMAIL_LABELS_CONF = Path(os.environ.get(
    "GMAIL_LABELS_CONF_PATH",
    str(Path(__file__).resolve().parent / ".gmail-labels.conf")))
GMAIL_LABEL_ENV = "GMAIL_JOB_SEARCH_LABEL_ID"


def gmail_label_id(key: str) -> str | None:
    """A configured Gmail label ID by key ("job_search", "personal").

    Gmail label IDs are account-specific identifiers. They were hardcoded in this
    public file AND in two tracked launchd plists until 2026-08-19. Resolution
    mirrors vault_paths.py:

      1. environment: GMAIL_<KEY>_LABEL_ID  (e.g. GMAIL_JOB_SEARCH_LABEL_ID)
      2. tools/.gmail-labels.conf, `<key>=<id>` (gitignored)

    Returns None when unconfigured; callers choose their failure mode.
    """
    env = os.environ.get(f"GMAIL_{key.upper()}_LABEL_ID", "").strip()
    if env:
        return env
    try:
        for line in GMAIL_LABELS_CONF.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            # `startswith("#")` is redundant today -- a commented line's key keeps the
            # "#" and so never equals the key -- kept as defence-in-depth.
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, value = line.partition("=")
            if k.strip() == key and value.strip():
                return value.strip()
    except OSError:
        pass
    return None


def personal_label_id() -> str | None:
    """The Personal Gmail label ID (see gmail_label_id)."""
    return gmail_label_id("personal")


XML_OPEN = '<email-content source="gmail" sanitized="true">'
XML_CLOSE = "</email-content>"


def job_search_label_id() -> str | None:
    """The "Job Search" Gmail label ID, resolved from private config.

    A Gmail label ID is an account-specific identifier. It was hardcoded here (and in
    the launchd plist) until 2026-08-19, when the /audit-pii semantic pass flagged it:
    this repo is PUBLIC, and the repo's own --personal design deliberately resolves the
    vault path through config for exactly this reason while the label ID sat literal two
    constants away. Resolution order mirrors vault_paths.py:

      1. the GMAIL_JOB_SEARCH_LABEL_ID environment variable
      2. tools/.gmail-labels.conf, `job_search=<id>` (gitignored)

    Returns None when unconfigured. Callers decide their failure mode: --job-search-label
    errors loudly, while the free-text --search default falls back to all-mail scope,
    which is a widening rather than a wrong destination.
    """
    return gmail_label_id("job_search")


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

def sanitize_body(raw_body: str, max_chars: int = MAX_BODY_CHARS) -> str:
    """
    Sanitize an email body for safe storage in inbox/.

    Steps:
      1. Strip HTML tags (BeautifulSoup if available, else regex fallback)
      2. Remove invisible/zero-width unicode characters
      3. Redact known prompt injection phrases
      4. Collapse excessive whitespace
      5. Truncate to max_chars (default MAX_BODY_CHARS)
      6. Wrap in XML delimiter

    Args:
        raw_body: the raw email body to sanitize.
        max_chars: max length before truncation (default MAX_BODY_CHARS).

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
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[...truncated]"

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
        f"PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --auth --repo-root .\n"
        f"```\n"
    )
    alert_path.write_text(alert_content, encoding="utf-8")
    return True


# ── Gmail API functions (require google-api-python-client) ────────────────────

def _write_token_atomic(token_path: Path, content: str) -> None:
    """
    Write the OAuth token via temp-file + os.replace so a mid-write death
    (machine sleep, SIGKILL) can never leave a truncated token behind.

    A plain write_text() truncates the target before writing; if the process
    dies in that window the token becomes empty and every later run fails with
    JSONDecodeError at from_authorized_user_file. os.replace is atomic on the
    same filesystem, so readers see either the old token or the new one.
    Matches the write_atomic convention in act_apply.py.

    The temp file is per-process (".tmp.<pid>"). The gmail-fetch and
    gmail-fetch-personal launchd jobs share this single token file and both
    run on a 900s interval, so a shared temp name let whichever process
    reached os.replace first consume the other's temp file, crashing the
    loser with FileNotFoundError on an otherwise healthy token refresh.
    """
    tmp = token_path.with_suffix(token_path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, token_path)
    finally:
        # Per-pid temps are never reused, so a crash between write and
        # replace would otherwise leave litter behind forever.
        tmp.unlink(missing_ok=True)


def _load_token_or_exit(Credentials, token_path: Path, tools_dir: Path):
    """
    Load the token file, converting a corrupt/truncated token into an
    actionable re-auth message instead of a raw JSONDecodeError traceback.
    """
    try:
        return Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        detail = f"token file at {token_path} is corrupt or truncated ({e})"
        _emit_auth_failure_alert(tools_dir, detail)
        print(
            f"ERROR: {detail}\n"
            "This usually means a previous run was killed mid-write.\n"
            "Re-run auth to regenerate it:\n"
            "  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --auth --repo-root .",
            file=sys.stderr,
        )
        sys.exit(1)


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
        _write_token_atomic(token_path, creds.to_json())
        print(f"Token saved to {token_path}")
        return creds

    if not token_path.exists():
        print(
            "ERROR: No token found. Run auth first:\n"
            "  python3 tools/gmail_fetch.py --auth --repo-root .",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = _load_token_or_exit(Credentials, token_path, tools_dir)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_data = json.loads(creds.to_json())
                token_data["token_last_refresh"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _write_token_atomic(token_path, json.dumps(token_data))
            except Exception as e:
                _emit_auth_failure_alert(tools_dir, str(e))
                raise
        else:
            _emit_auth_failure_alert(tools_dir, "token invalid and no refresh_token available")
            print(
                "ERROR: Token invalid and cannot be refreshed. Re-run auth:\n"
                "  python3 tools/gmail_fetch.py --auth --repo-root .",
                file=sys.stderr,
            )
            sys.exit(1)

    return creds


def _clear_auth_failure_alert(inbox_dir: Path) -> None:
    """
    Delete inbox/GMAIL-AUTH-FAILURE.md if it exists.

    Called after a successful service build to clean up a stale alert that
    was left behind by a previous broken fetch cycle. The user shouldn't
    have to manually delete the file once auth has self-recovered (e.g.,
    via /auth completion or token refresh).
    """
    alert_path = inbox_dir / "GMAIL-AUTH-FAILURE.md"
    if alert_path.exists():
        try:
            alert_path.unlink()
            print(f"Cleared stale auth-failure alert: {alert_path.name}")
        except OSError as e:
            print(f"Warning: could not clear {alert_path.name}: {e}", file=sys.stderr)


def _emit_auth_failure_alert(tools_dir: Path, error_msg: str) -> None:
    """
    Write GMAIL-AUTH-FAILURE alert to inbox/ AND fire macOS notification when
    the OAuth refresh fails (token revoked/expired/invalidated).

    This is the loud-failure path — distinct from check_token_expiry()'s
    quiet age-based heads-up. If this function runs, the launchd job has
    stopped working RIGHT NOW.
    """
    import subprocess
    repo_root = tools_dir.parent
    inbox_dir = repo_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    alert_path = inbox_dir / "GMAIL-AUTH-FAILURE.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_content = (
        f"# Gmail Auth FAILURE — fetcher is BROKEN\n\n"
        f"> **Detected:** {timestamp}\n"
        f"> **Error:** `{error_msg}`\n\n"
        f"The Gmail OAuth token refresh failed. The launchd fetcher "
        f"(`com.nickmagnuson.jobsearch.gmail-fetch`) is currently NOT "
        f"pulling new emails into `inbox/`. This file will keep being "
        f"rewritten on every failed fetch (every 15 min) until you re-auth.\n\n"
        f"**Fix:**\n\n"
        f"```\n"
        f"PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --auth --repo-root .\n"
        f"```\n\n"
        f"After re-auth, this alert file can be deleted.\n"
    )
    alert_path.write_text(alert_content, encoding="utf-8")
    # macOS desktop notification — best-effort, never raises
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'display notification "Gmail fetch is broken — re-auth required. See inbox/GMAIL-AUTH-FAILURE.md" '
                'with title "Gmail Auth FAILED" sound name "Basso"',
            ],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def _friendly_sender(from_header: str) -> str:
    """
    Extract a short display name from a raw From header for notifications.

    "Acme Recruiting <jobs@x.com>" -> "Acme Recruiting"
    "jobs@acme.com"                -> "jobs"
    """
    if not from_header:
        return "new sender"
    from_header = from_header.strip()
    m = re.match(r'^"?([^"<]+?)"?\s*<', from_header)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"([\w.+-]+)@[\w.-]+", from_header)
    if m:
        return m.group(1)
    return from_header[:30]


def _notify_new_emails(written: int, senders: list, *, now: datetime | None = None) -> None:
    """
    Fire ONE batched macOS notification summarizing the job-search emails pulled
    this cycle, and prompt to run /act. Best-effort; never raises.

    - Batched: one ping per fetch cycle, not per email.
    - Counts only emails newly written THIS cycle (not the inbox/ backlog), so
      untriaged items don't re-ping every 15 minutes.
    - Quiet hours: suppressed outside 08:00–21:00. Files still land overnight;
      the first daytime cycle's ping covers them.
    """
    if written <= 0:
        return
    now = now or datetime.now()
    if now.hour < 8 or now.hour >= 21:
        return  # quiet hours — files already written, just no ping

    names = [_friendly_sender(s) for s in senders if s]
    top = names[0] if names else "new sender"
    extra = len(names) - 1
    who = top if extra <= 0 else f"{top} +{extra}"
    noun = "email" if written == 1 else "emails"
    body = f"{written} new job {noun} ({who}) - run /act to triage"
    # AppleScript string is double-quoted; neutralize embedded quotes/backslashes.
    body = body.replace("\\", "").replace('"', "'")

    import subprocess

    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{body}" with title "Job Search" sound name "Glass"',
            ],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


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
        # When scoped to a label, also request labelAdded: emails the user
        # manually tags after arrival are recorded by Gmail as labelAdded
        # events, NOT messageAdded, so a messageAdded-only sync silently skips
        # them and advances historyId past them permanently. Only request it
        # when label_id is set (unscoped labelAdded would pull every label
        # change in the mailbox).
        history_types = ["messageAdded"]
        if label_id:
            history_types.append("labelAdded")
        kwargs = {
            "userId": "me",
            "startHistoryId": history_id,
            "historyTypes": history_types,
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
        # Emails the user manually tagged with the target label after arrival.
        # Only when label-scoped, and only if the target label is among the
        # labels added in this event. The format=full label re-check below
        # (label_id not in msg.labelIds) is the final correctness gate.
        if label_id:
            for labeled in hist.get("labelsAdded", []):
                if label_id not in labeled.get("labelIds", []):
                    continue
                msg_id = labeled["message"]["id"]
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

    label_id:     Gmail label ID (e.g., "Label_XXXXXXXXXXXXXXXXXXX")
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


def _mailbox_marker(label_ids: list[str]) -> str:
    """Render the mailbox a search hit lives in, so a DRAFT can never be read as
    a sent message.

    `--all-mail` search includes the Drafts label, and a draft's printed record
    carries a From:, Date:, Subject: and Snippet: identical in shape to a sent
    message. On 2026-09-02 that made an abandoned draft look like a second email
    delivered to a recipient 16 minutes after the real one. It had never been
    sent. See memory/feedback_draft_archive_is_not_proof_of_send.md.

    DRAFT is checked first and reported alone: a draft is the one state where
    every other label on the message is misleading about delivery.
    """
    labels = set(label_ids or [])
    if "DRAFT" in labels:
        return "DRAFT — NOT SENT"
    for state in ("SENT", "INBOX", "SPAM", "TRASH"):
        if state in labels:
            return state
    return "unknown"


def search_messages(
    service,
    query: str,
    label_id: str | None,
    max_results: int,
    show_body: bool,
) -> int:
    """
    Free-text Gmail search (read-only). Prints sender/subject/date/snippet per hit,
    newest first. Scoped to `label_id` when given (Gmail messages.list accepts both
    labelIds and a q query), or all mail when label_id is None.

    Returns the number of messages printed.
    """
    kwargs: dict = {"userId": "me", "q": query, "maxResults": max_results}
    if label_id:
        kwargs["labelIds"] = [label_id]

    response = service.users().messages().list(**kwargs).execute()
    msgs = response.get("messages", [])

    scope = f"label {label_id}" if label_id else "all mail"
    print(f"Gmail search: q={query!r} in {scope} — {len(msgs)} result(s)\n")
    if not msgs:
        return 0

    for m in msgs:
        fmt = "full" if show_body else "metadata"
        meta_headers = ["From", "To", "Subject", "Date"]
        get_kwargs: dict = {"userId": "me", "id": m["id"], "format": fmt}
        if not show_body:
            get_kwargs["metadataHeaders"] = meta_headers
        full = service.users().messages().get(**get_kwargs).execute()
        headers = full.get("payload", {}).get("headers", [])
        print(f"  id:      {m['id']}")
        print(f"  Mailbox: {_mailbox_marker(full.get('labelIds', []))}")
        print(f"  Date:    {_extract_header(headers, 'Date')}")
        print(f"  From:    {_extract_header(headers, 'From')}")
        print(f"  Subject: {_extract_header(headers, 'Subject') or '(no subject)'}")
        print(f"  Snippet: {full.get('snippet', '')[:200]}")
        if show_body:
            mime_parts = _get_all_mime_parts(full.get("payload", {}))
            body = extract_plain_text(mime_parts).strip()
            print("  --- body ---")
            for line in body[:MAX_BODY_CHARS].splitlines():
                print(f"  {line}")
            if len(body) > MAX_BODY_CHARS:
                print(f"  ... [truncated at {MAX_BODY_CHARS} chars]")
        print()

    return len(msgs)


# ── Main ───────────────────────────────────────────────────────────────────────

def resolve_inbox_dir(personal: bool, inbox_dir_arg, repo_root: Path) -> Path:
    """Where fetched mail is written.

    EXTRACTED so the routing is testable. Through the CLI the destination is
    unobservable (the run exits on the missing Gmail token first), and a mutation
    pass on 2026-08-18 showed that making --personal a no-op broke NO test. The
    behaviour it guards -- personal mail must not land in the PUBLIC job-search
    repo -- is exactly the kind that must not rest on an untested branch.

    --personal resolves through vault_paths so the private vault location never
    appears in this public file or in a tracked launchd plist. require_vault_root
    raises VaultRootMissing when unconfigured: guessing could write personal mail
    into the public repo, so a loud failure beats a silent fallback.
    """
    if personal:
        # Import works whether this runs as `python3 tools/gmail_fetch.py`
        # (sys.path[0] == tools/) or as a `tools.` package module.
        try:
            from vault_paths import personal_mail_dir
        except ModuleNotFoundError:
            from tools.vault_paths import personal_mail_dir
        return personal_mail_dir()
    if inbox_dir_arg:
        return Path(inbox_dir_arg).expanduser().resolve()
    return repo_root / "inbox"


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
    parser.add_argument(
        "--job-search-label",
        action="store_true",
        help=(
            "Scope to the configured Job Search label, resolved from "
            "GMAIL_JOB_SEARCH_LABEL_ID or tools/.gmail-labels.conf. Lets the launchd "
            "plist (tracked, public) avoid hardcoding an account-specific label ID. "
            "Mutually exclusive with --label-id."
        ),
    )
    parser.add_argument(
        "--personal",
        action="store_true",
        help=(
            "Route fetched mail to the personal-OS vault instead of the job-search inbox/. "
            "Resolves the destination through tools/vault_paths.py so the private vault "
            "path never has to be typed on a command line or into a tracked launchd plist "
            "(this repo is public). The personal LABEL is resolved from that same config, "
            "so no label ID is needed on the command line either. "
            "Mutually exclusive with --inbox-dir."
        ),
    )
    parser.add_argument(
        "--inbox-dir",
        default=None,
        help=(
            "Override destination directory for fetched emails. Default: <repo_root>/inbox. "
            "Use to route a different label (e.g., Personal) to a different vault location."
        ),
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help=(
            "Override path to the historyId state file. Default: <repo_root>/tools/.gmail_state.json. "
            "Required when running parallel forward-syncs with different labels — each needs "
            "its own historyId to avoid state corruption."
        ),
    )
    parser.add_argument(
        "--search",
        default=None,
        metavar="QUERY",
        help=(
            "Free-text Gmail search (read-only). Uses full Gmail query syntax "
            "(e.g. 'from:jordan acme', 'subject:resume after:2026/05/01'). "
            "Defaults to the Job Search label scope; use --all-mail to widen, "
            "--label-id to target a different label. Combine with --max and --body."
        ),
    )
    parser.add_argument(
        "--body",
        action="store_true",
        help="With --search: also print each message's plain-text body (truncated).",
    )
    parser.add_argument(
        "--all-mail",
        action="store_true",
        help="With --search: search all mail instead of defaulting to the Job Search label.",
    )
    args = parser.parse_args()

    # ── Empty-string guard for the optional string flags ──────────────────
    # Every flag below defaults to None and is tested for TRUTHINESS downstream,
    # so an empty string is indistinguishable from "never supplied": the flag is
    # accepted, parsed, and silently ignored.
    #
    # For --search that is not a degraded parameter, it is a MODE CHANGE. The
    # read-only search branch is skipped and control falls through to the
    # forward-sync path, which writes files into inbox/ and advances
    # .gmail_state.json. `--search "" --max 25` reads as "list 25 messages" and
    # actually performs a sync. Fail loudly instead of guessing.
    # Origin: 2026-08-19, hit while trying to list a label read-only.
    for _flag, _dest in (
        ("--search", "search"),
        ("--label-id", "label_id"),
        ("--since", "since"),
        ("--inbox-dir", "inbox_dir"),
        ("--state-file", "state_file"),
    ):
        _value = getattr(args, _dest)
        if _value is not None and not _value.strip():
            parser.error(
                f"{_flag} was given an empty value. Omit the flag to use the default, "
                f"or supply a real one. (To list a label read-only, use "
                f"--backfill --max N; a bare --search does not do that.)"
            )

    repo_root = Path(args.repo_root).resolve()
    tools_dir = repo_root / "tools"
    if args.job_search_label and args.label_id:
        parser.error("--job-search-label and --label-id are mutually exclusive.")
    if args.job_search_label:
        args.label_id = job_search_label_id()
        if not args.label_id:
            parser.error(
                f"--job-search-label: no label configured. Set {GMAIL_LABEL_ENV}, or add "
                f"`job_search=<id>` to {GMAIL_LABELS_CONF}."
            )
    # --personal implies the personal mailbox scope as well as the personal
    # destination, so the tracked launchd plist need not carry either.
    if args.personal and not args.label_id:
        args.label_id = personal_label_id()
        if not args.label_id:
            parser.error(
                "--personal: no personal label configured. Set GMAIL_PERSONAL_LABEL_ID, "
                f"or add `personal=<id>` to {GMAIL_LABELS_CONF}."
            )
    if args.personal and args.inbox_dir:
        parser.error("--personal and --inbox-dir are mutually exclusive: "
                     "--personal already resolves the destination via vault_paths.")
    inbox_dir = resolve_inbox_dir(args.personal, args.inbox_dir, repo_root)
    state_path = Path(args.state_file).expanduser().resolve() if args.state_file else tools_dir / ".gmail_state.json"
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
        print("Setup complete. Schedule via launchd: bash tools/launchd/install.sh install")
        return

    # ── Search mode: free-text read-only Gmail search ─────────────────────
    # `is not None`, not truthiness: this selects the MODE, and falling through
    # to the sync path writes files. The empty-string guard above already
    # rejects "", so this is belt-and-braces, but the mode selector should not
    # depend on that guard staying in place.
    if args.search is not None:
        creds = get_or_refresh_creds(tools_dir, auth_mode=False)
        try:
            from googleapiclient.discovery import build
        except ImportError:
            print("ERROR: googleapiclient not installed.", file=sys.stderr)
            sys.exit(1)
        service = build("gmail", "v1", credentials=creds)
        # Default scope: Job Search label. --label-id overrides; --all-mail widens.
        if args.all_mail:
            label_id = None
        elif args.label_id:
            label_id = args.label_id
        else:
            label_id = job_search_label_id()
        max_results = args.max_messages if args.max_messages else 25
        search_messages(
            service,
            query=args.search,
            label_id=label_id,
            max_results=max_results,
            show_body=args.body,
        )
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

    # Auth has self-recovered if we got here — clear any stale failure alert.
    if not args.dry_run:
        _clear_auth_failure_alert(inbox_dir)

    state = _load_state(state_path)
    if not state.get("historyId"):
        # Auto-seed: if this is a fresh state file (e.g., new --state-file path
        # for a parallel forward-sync), seed historyId from the most recent
        # messages without importing them. The shared token (gmail_token.json)
        # provides auth, so a separate --auth call is not required.
        print(
            f"No historyId in {state_path.name} — auto-seeding from last 50 messages "
            "(not importing them).",
            file=sys.stderr,
        )
        history_id = _seed_history_id(service, count=50)
        state = {"historyId": history_id, "last_refresh": datetime.now().isoformat()}
        if not args.dry_run:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            _save_state(state_path, state)
        print(f"Seeded {state_path} with historyId={history_id}.", file=sys.stderr)
        return

    # Cleanup old Gmail files before fetching new ones
    deleted = cleanup_old_inbox_files(inbox_dir, hours=48)
    if deleted:
        print(f"Cleaned up {deleted} old Gmail inbox file(s).")

    messages = fetch_new_messages(service, state, label_id=args.label_id)
    print(f"Found {len(messages)} new message(s).")

    written = 0
    new_senders = []
    for msg in messages:
        try:
            msg_meta, raw_body = _process_message(msg)
            sanitized = sanitize_body(raw_body)
            path = write_inbox_file(inbox_dir, msg_meta, sanitized, dry_run=args.dry_run)
            if not args.dry_run:
                print(f"  Wrote: {path.name}")
                written += 1
                new_senders.append(msg_meta.get("sender", ""))
        except Exception as e:
            print(f"Warning: could not process message {msg.get('id', '?')}: {e}", file=sys.stderr)

    state["last_refresh"] = datetime.now().isoformat()
    if not args.dry_run:
        _save_state(state_path, state)
        _notify_new_emails(written, new_senders)

    print(f"Done. Wrote {written} inbox file(s).")


if __name__ == "__main__":
    main()
