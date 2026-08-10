#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
granola_fetch.py - REST API client for fetching Granola meetings and transcripts.

Headless client for launchd automation (granola-auto-debrief, every 3 hrs; the
earlier n8n setup was retired ~2026-04-28). Uses the Granola public REST API with a
Personal API Key (not the MCP server, which requires interactive OAuth).

Environment:
  GRANOLA_API_KEY - Personal API Key from Granola Settings > API

Functions:
  fetch_recent_meetings(since_hours) - List meetings created in the last N hours
  fetch_transcript(note_id) - Fetch transcript for a single meeting
  fetch_new_since_last_run(state_file) - Incremental fetch with state tracking

CLI:
  python3 tools/granola_fetch.py list [--hours 4]
  python3 tools/granola_fetch.py transcript <note_id>
  python3 tools/granola_fetch.py new

Output: JSON to stdout. Errors and status messages to stderr.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://public-api.granola.ai/v1/notes"
DEFAULT_STATE_FILE = "tools/.cache/granola_last_fetch.json"
RETRY_WAIT_SECONDS = 5


def _load_dotenv() -> None:
    """Load .env file from project root if it exists."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.replace("export ", "").strip()
                    os.environ.setdefault(k, v.strip())


def _get_api_key() -> str:
    """Read GRANOLA_API_KEY from environment or .env file. Exit with error if missing."""
    _load_dotenv()
    key = os.environ.get("GRANOLA_API_KEY", "").strip()
    if not key:
        print(
            "Error: GRANOLA_API_KEY environment variable not set.\n"
            "Get your Personal API Key from Granola Settings > API.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def _make_request(url: str, api_key: str, retry_on_429: bool = True) -> dict:
    """Make an authenticated GET request to the Granola API.

    Handles HTTP errors with descriptive messages. Retries once on 429.

    Args:
        url: Full URL to request.
        api_key: Bearer token for Authorization header.
        retry_on_429: Whether to retry on rate limit (default True).

    Returns:
        Parsed JSON response as dict or list.
    """
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("Error: Invalid API key. Check GRANOLA_API_KEY.", file=sys.stderr)
            sys.exit(1)
        elif e.code == 429 and retry_on_429:
            print(
                f"Rate limited (429). Waiting {RETRY_WAIT_SECONDS}s before retry...",
                file=sys.stderr,
            )
            time.sleep(RETRY_WAIT_SECONDS)
            return _make_request(url, api_key, retry_on_429=False)
        else:
            print(f"Error: HTTP {e.code} - {e.reason}", file=sys.stderr)
            sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Network request failed - {e.reason}", file=sys.stderr)
        sys.exit(1)


def fetch_recent_meetings(since_hours: int = 4) -> list:
    """Fetch meetings created in the last N hours.

    Args:
        since_hours: Number of hours to look back (default 4).

    Returns:
        List of meeting objects (id, title, created_at, etc.).
    """
    api_key = _get_api_key()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{BASE_URL}?created_after={since_iso}"
    data = _make_request(url, api_key)

    # API may return a list directly or wrap in an object
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "notes" in data:
        return data["notes"]
    elif isinstance(data, dict) and "data" in data:
        return data["data"]
    return data if isinstance(data, list) else []


def fetch_transcript(note_id: str) -> list:
    """Fetch the transcript for a single meeting.

    Args:
        note_id: The Granola note/meeting ID.

    Returns:
        List of transcript segment dicts, or empty list if unavailable.
    """
    api_key = _get_api_key()
    url = f"{BASE_URL}/{note_id}?include=transcript"
    data = _make_request(url, api_key)

    if isinstance(data, dict):
        transcript = data.get("transcript", [])
        return transcript if isinstance(transcript, list) else []
    return []


def fetch_note_full(note_id: str) -> dict:
    """Fetch the full note object (no include filter), which contains AI summary + private notes.

    Granola's REST API returns a note with multiple optional fields. We pull the bare object
    to surface whatever Granola has populated. Field shape may vary by Granola plan tier.

    Returns:
        Dict with whatever fields the note has. Common keys observed: id, title, created_at,
        updated_at, summary, ai_summary, notes, private_notes, content. Empty dict on error.

    Note: Use this for AI summary + private notes. For just the transcript, use fetch_transcript.
    """
    api_key = _get_api_key()
    url = f"{BASE_URL}/{note_id}"
    data = _make_request(url, api_key)
    return data if isinstance(data, dict) else {}


def extract_summary_and_notes(note: dict) -> tuple:
    """Pull AI summary and private notes from a full REST note object.

    Granola REST API field reference (verified 2026-05-04 against live API):
      summary_markdown  -> AI-generated summary, markdown formatted (preferred)
      summary_text      -> AI-generated summary, plain text (fallback)
      private_notes     -> NOT exposed via REST. Available only via MCP get_meetings.

    Note ID format: REST uses `not_<base62>` (e.g. `not_EXAMPLE`), NOT the UUIDs
    returned by the MCP. Use the LIST endpoint (fetch_recent_meetings) to get REST IDs.

    Returns:
        (summary_str, private_notes_str) — private_notes will always be "" via REST.
    """
    summary = ""
    for key in ("summary_markdown", "summary_text"):
        val = note.get(key, "")
        if isinstance(val, str) and val.strip():
            summary = val.strip()
            break

    # Private notes are MCP-only. REST does not expose them.
    private_notes = ""

    return summary, private_notes


def fetch_new_since_last_run(state_file: str = DEFAULT_STATE_FILE) -> list:
    """Fetch meetings created since the last run, with transcripts.

    Reads the last fetch timestamp from state_file. If missing, defaults
    to 4 hours ago. After fetching, updates the state file with the
    current timestamp.

    Args:
        state_file: Path to JSON state file tracking last fetch time.

    Returns:
        List of meeting dicts, each enriched with a "transcript" key.
    """
    state_path = Path(state_file)

    # Ensure cache directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Read last fetch time
    default_since = datetime.now(timezone.utc) - timedelta(hours=4)
    last_fetch_iso = default_since.strftime("%Y-%m-%dT%H:%M:%SZ")

    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_fetch_iso = state.get("last_fetch", last_fetch_iso)
        except (json.JSONDecodeError, KeyError):
            pass

    # Fetch meetings since last run
    api_key = _get_api_key()
    url = f"{BASE_URL}?created_after={last_fetch_iso}"
    data = _make_request(url, api_key)

    if isinstance(data, list):
        meetings = data
    elif isinstance(data, dict) and "notes" in data:
        meetings = data["notes"]
    elif isinstance(data, dict) and "data" in data:
        meetings = data["data"]
    else:
        meetings = []

    # Fetch transcript for each meeting
    results = []
    for meeting in meetings:
        meeting_id = meeting.get("id", "")
        if not meeting_id:
            continue

        transcript = fetch_transcript(meeting_id)
        results.append({
            "id": meeting_id,
            "title": meeting.get("title", ""),
            "created_at": meeting.get("created_at", ""),
            "transcript": transcript,
        })

    # Update state file with current timestamp
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"last_fetch": now_iso}, f)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Granola meetings and transcripts via REST API."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List recent meetings")
    list_parser.add_argument(
        "--hours", type=int, default=4,
        help="Hours to look back (default: 4)",
    )

    # transcript subcommand
    transcript_parser = subparsers.add_parser("transcript", help="Fetch a single transcript")
    transcript_parser.add_argument("note_id", help="Granola note/meeting ID")

    # new subcommand
    subparsers.add_parser("new", help="Fetch new meetings since last run")

    args = parser.parse_args()

    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(1)

    if args.command == "list":
        result = fetch_recent_meetings(since_hours=args.hours)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "transcript":
        result = fetch_transcript(args.note_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "new":
        result = fetch_new_since_last_run()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
