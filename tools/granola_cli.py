#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
granola_cli.py — REST-first Granola CLI for /granola-pull skill.

Designed to keep Granola transcript content OUT of Claude's context window.
Returns compact stdout (meeting tables, short summaries, file paths) while the
full transcript+summary land on disk via tools/granola_save.py.

Subcommands:
  list [--hours N]
      Print a numbered Markdown table of recent meetings. Default 168h (1 week).
      Stdout is short — Claude reads it directly to show a picker.

  pull <ref>
      Fetch transcript + AI summary by REST ID OR by list-position number.
      Persists via granola_save.py (idempotent, --no-overwrite).
      Stdout: title, AI summary (first ~600 chars), file paths.
      The full transcript is NOT printed to stdout — it lives on disk.
      Type is auto-classified from title (therapy keywords → therapy, else networking).
      Override with --type {therapy,recruiter,networking,general}.

  info <ref>
      Print just the AI summary + metadata. No transcript fetch, no persist.
      Useful for quick "what was this call about" without writing a file.

CLI:
  python3 tools/granola_cli.py list
  python3 tools/granola_cli.py list --hours 720           # 30 days
  python3 tools/granola_cli.py pull not_EXAMPLE
  python3 tools/granola_cli.py pull 3                      # by list position
  python3 tools/granola_cli.py pull <id> --type recruiter
  python3 tools/granola_cli.py info <id>

Exit codes:
  0 ok, 1 error, 2 user cancelled

State:
  tools/.cache/granola_last_list.json — caches last list output so position-based
  pull can resolve N → ID without re-listing.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.granola_fetch import (  # noqa: E402
    _get_api_key,
    _make_request,
    BASE_URL,
    fetch_transcript,
    fetch_note_full,
    extract_summary_and_notes,
)
from tools.granola_auto_debrief import (  # noqa: E402
    classify_meeting,
    load_therapy_classifier_config,
    persist_via_granola_save,
)

LIST_CACHE = SCRIPT_DIR / ".cache" / "granola_last_list.json"


def fetch_meetings_window(hours: int) -> list:
    """Fetch all meetings in the last N hours via REST list endpoint."""
    api_key = _get_api_key()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{BASE_URL}?created_after={since_iso}"
    data = _make_request(url, api_key)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("notes", data.get("data", []))
    return []


def utc_to_local_str(iso: str) -> str:
    """Convert REST ISO-8601 UTC timestamp to local-time display string."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16].replace("T", " ")


def cmd_list(args) -> int:
    meetings = fetch_meetings_window(args.hours)
    meetings.sort(key=lambda m: m.get("created_at", ""), reverse=True)

    # Cache for position-based pull
    LIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LIST_CACHE.write_text(json.dumps([
        {"id": m.get("id", ""), "title": m.get("title", ""), "created_at": m.get("created_at", "")}
        for m in meetings
    ]), encoding="utf-8")

    if args.format == "json":
        print(json.dumps([
            {"id": m.get("id"), "title": m.get("title"), "created_at": m.get("created_at"), "local_time": utc_to_local_str(m.get("created_at", ""))}
            for m in meetings
        ], indent=2))
        return 0

    if not meetings:
        print(f"No meetings in last {args.hours}h.")
        return 0

    print(f"Granola meetings (last {args.hours}h, newest first, {len(meetings)} total):\n")
    print("| # | Local Time | Title | ID |")
    print("|---|---|---|---|")
    for i, m in enumerate(meetings, 1):
        title = (m.get("title") or "(untitled)").replace("|", "\\|")
        print(f"| {i} | {utc_to_local_str(m.get('created_at', ''))} | {title} | `{m.get('id', '')}` |")
    print(f"\n_Pull a meeting: `tools/granola_cli.py pull <#>` or `pull <id>`_")
    return 0


def resolve_ref(ref: str) -> str:
    """Resolve a meeting reference (REST ID or position number) to a REST ID."""
    if ref.startswith("not_"):
        return ref
    if ref.isdigit():
        if not LIST_CACHE.exists():
            print("Error: position-based ref but no list cache. Run `granola_cli.py list` first.", file=sys.stderr)
            sys.exit(1)
        cached = json.loads(LIST_CACHE.read_text())
        idx = int(ref) - 1
        if idx < 0 or idx >= len(cached):
            print(f"Error: position {ref} out of range (have {len(cached)} cached).", file=sys.stderr)
            sys.exit(1)
        return cached[idx]["id"]
    print(f"Error: unrecognized ref '{ref}' (expected REST ID 'not_*' or list position number).", file=sys.stderr)
    sys.exit(1)


def cmd_pull(args) -> int:
    meeting_id = resolve_ref(args.ref)
    note = fetch_note_full(meeting_id)
    if not note:
        print(f"Error: failed to fetch note {meeting_id}", file=sys.stderr)
        return 1

    title = note.get("title", "Untitled")
    transcript = note.get("transcript", [])
    if not transcript:
        # Try the transcript-only endpoint as fallback
        transcript = fetch_transcript(meeting_id)
    summary, _ = extract_summary_and_notes(note)

    if isinstance(transcript, list):
        transcript_str = "\n".join(
            f"{seg.get('speaker', 'Speaker')}: {seg.get('text', '')}"
            for seg in transcript if isinstance(seg, dict)
        )
    else:
        transcript_str = transcript or ""

    if not transcript_str:
        print(f"Warning: no transcript available for '{title}' (ID {meeting_id}).", file=sys.stderr)

    # Build meeting dict; thread attendees from the full note for accurate
    # virtual-vs-in-person classification.
    meeting = {
        "id": meeting_id,
        "title": title,
        "created_at": note.get("created_at", ""),
        "transcript": transcript_str,
        "attendees": note.get("attendees", []) if isinstance(note, dict) else [],
    }

    # Classify (override allowed). Falls back to fail-closed 'unknown'.
    config = load_therapy_classifier_config()
    type_ = args.type or classify_meeting(meeting, config)

    if args.no_persist:
        print(f"# {title}\n")
        print(f"**Type:** {type_}")
        print(f"**ID:** `{meeting_id}`")
        print(f"**Captured (local):** {utc_to_local_str(note.get('created_at', ''))}")
        print(f"**Transcript length:** {len(transcript_str)} chars")
        print()
        print("## Granola AI Summary")
        print()
        print(summary or "_(no summary returned by REST API)_")
        print()
        print("_--no-persist set; nothing written to disk._")
        return 0

    # Fail closed in the manual path too: an unclassifiable meeting needs an explicit type.
    if type_ == "unknown":
        print(f"Could not classify '{title}' (no attendees, no therapy signal).", file=sys.stderr)
        print("Re-run with --type therapy|networking to persist.", file=sys.stderr)
        return 2

    # Persist via granola_save.py (type already resolved — passed explicitly).
    result = persist_via_granola_save(meeting, summary, "", type_)

    # Compact stdout — Claude reads this, full content is on disk
    print(f"# {title}\n")
    print(f"**Type:** {type_}")
    print(f"**ID:** `{meeting_id}`")
    print(f"**Captured (local):** {utc_to_local_str(note.get('created_at', ''))}")
    print(f"**Persist status:** {result.get('status', 'unknown')}")
    if result.get("transcript_path"):
        print(f"**Transcript file:** `{result['transcript_path']}`")
    if result.get("summary_path"):
        print(f"**Summary file:** `{result['summary_path']}`")
    if result.get("status") == "skip":
        print(f"**Skip reason:** {result.get('reason', '')}")
    print()
    print("## Granola AI Summary (preview)")
    print()
    if summary:
        # Truncate to ~600 chars for stdout — full version is in the summary file
        preview = summary[:600]
        if len(summary) > 600:
            preview += f"\n\n_(...{len(summary) - 600} more chars in summary file)_"
        print(preview)
    else:
        print("_(no summary returned by REST API)_")
    print()
    print(f"_Note: private_notes are NOT exposed via REST. For therapy/high-stakes calls where_")
    print(f"_Nick typed notes in Granola, fall back to MCP `get_meetings` to backfill._")
    return 0


def cmd_info(args) -> int:
    meeting_id = resolve_ref(args.ref)
    note = fetch_note_full(meeting_id)
    if not note:
        print(f"Error: failed to fetch note {meeting_id}", file=sys.stderr)
        return 1
    summary, _ = extract_summary_and_notes(note)
    print(f"# {note.get('title', 'Untitled')}\n")
    print(f"**ID:** `{meeting_id}`")
    print(f"**Captured (local):** {utc_to_local_str(note.get('created_at', ''))}")
    print(f"**Web URL:** {note.get('web_url', '')}")
    attendees = note.get("attendees", [])
    if attendees:
        names = [a.get("name", a.get("email", "")) for a in attendees if isinstance(a, dict)]
        print(f"**Attendees:** {', '.join(names) or '(none listed)'}")
    print()
    print("## AI Summary")
    print()
    print(summary or "_(no summary)_")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List recent Granola meetings (Markdown table to stdout)")
    p_list.add_argument("--hours", type=int, default=168, help="Hours to look back (default 168 = 1 week)")
    p_list.add_argument("--format", choices=["table", "json"], default="table")

    p_pull = sub.add_parser("pull", help="Fetch + persist a meeting; compact summary to stdout, full content to disk")
    p_pull.add_argument("ref", help="REST ID (not_*) OR list position number from a recent `list` call")
    p_pull.add_argument("--type", choices=["therapy", "recruiter", "networking", "general"], default=None,
                        help="Override auto-classification (default: title-based)")
    p_pull.add_argument("--no-persist", action="store_true", help="Print summary; don't write files")

    p_info = sub.add_parser("info", help="Print summary + metadata only (no transcript fetch, no persist)")
    p_info.add_argument("ref", help="REST ID or list position number")

    args = parser.parse_args()

    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "pull":
        return cmd_pull(args)
    if args.cmd == "info":
        return cmd_info(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
