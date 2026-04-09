#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
granola_auto_debrief.py - Auto-debrief new Granola calls and write summaries to inbox.

Orchestrator script that chains granola_fetch.py and call_analyzer.py.
Called by n8n on schedule (every 3 hours). Fetches new meetings since
last run, analyzes each transcript, and prepends formatted summaries
to data/inbox.md.

Functions:
  format_inbox_entry(meeting, analysis) - Format a single meeting analysis as inbox entry
  auto_debrief_new_calls(dry_run, hours) - Main orchestrator: fetch -> analyze -> inbox write

CLI:
  python tools/granola_auto_debrief.py             # Run auto-debrief (default)
  python tools/granola_auto_debrief.py --dry-run    # Fetch and analyze but print instead of writing
  python tools/granola_auto_debrief.py --hours 8    # Override fetch window

Output: JSON summary to stdout. Errors and status to stderr.

Decisions:
  D-10: Process ALL calls - no filtering by pipeline match
  D-11: Write to data/inbox.md only - not directly to coaching files
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve repo root from script location (tools/ -> repo root)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parent

# Import sibling modules
sys.path.insert(0, str(DEFAULT_REPO_ROOT))
from tools.granola_fetch import fetch_new_since_last_run
from tools.call_analyzer import analyze_transcript


def format_inbox_entry(meeting: dict, analysis: dict) -> str:
    """Format a single meeting's analysis as an inbox entry.

    Args:
        meeting: Dict with keys: id, title, created_at, transcript.
        analysis: Dict from analyze_transcript with keys: filler_counts,
            qa_pairs, total_questions, candidate_word_count,
            interviewer_word_count, talk_ratio.

    Returns:
        Formatted string ready to prepend to inbox.md.
    """
    title = meeting.get("title", "Untitled Call")
    created_at = meeting.get("created_at", "")

    # Format date
    date_display = created_at
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_display = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            pass

    # Talk ratio as percentage
    talk_ratio = analysis.get("talk_ratio", 0.0)
    candidate_pct = round(talk_ratio * 100)
    interviewer_pct = 100 - candidate_pct
    talk_str = f"{candidate_pct}% candidate / {interviewer_pct}% interviewer"

    # Filler words
    filler_counts = analysis.get("filler_counts", {})
    if filler_counts:
        filler_parts = [f"{word} x{count}" for word, count in filler_counts.items()]
        filler_str = ", ".join(filler_parts)
    else:
        filler_str = "None detected"

    # Top 3 Q&A pairs
    qa_pairs = analysis.get("qa_pairs", [])
    qa_lines = []
    for pair in qa_pairs[:3]:
        q = pair.get("question", "")
        a = pair.get("answer", "")
        q_trunc = (q[:77] + "...") if len(q) > 80 else q
        a_trunc = (a[:117] + "...") if len(a) > 120 else a
        qa_lines.append(f"- **Q:** {q_trunc}")
        qa_lines.append(f"  **A:** {a_trunc}")
    qa_block = "\n".join(qa_lines) if qa_lines else "- No Q&A pairs detected"

    # Current timestamp for source line
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry = (
        f"## Call Debrief: {title}\n"
        f"\n"
        f"**Date:** {date_display}\n"
        f"**Questions asked:** {analysis.get('total_questions', 0)}\n"
        f"**Talk ratio:** {talk_str}\n"
        f"\n"
        f"**Filler words:** {filler_str}\n"
        f"\n"
        f"**Top 3 Q&A pairs:**\n"
        f"{qa_block}\n"
        f"\n"
        f"> Run `/debrief` for full analysis with coached-answer comparison and anti-pattern tracking.\n"
        f"\n"
        f"*Source: Granola auto-debrief | {now_str}*\n"
    )
    return entry


def auto_debrief_new_calls(dry_run: bool = False, hours: int = None,
                           repo_root: str = None) -> int:
    """Main orchestrator: fetch new calls, analyze, write to inbox.

    Args:
        dry_run: If True, print entries to stdout instead of writing to inbox.
        hours: Override fetch window in hours (passes to granola_fetch via
            state file override). If None, uses granola_fetch default.
        repo_root: Path to repository root. Defaults to script's parent's parent.

    Returns:
        Number of calls processed.
    """
    root = Path(repo_root) if repo_root else DEFAULT_REPO_ROOT
    inbox_path = root / "data" / "inbox.md"

    # Fetch new meetings
    if hours is not None:
        # Override state file to force a specific time window
        from datetime import timedelta
        from tools.granola_fetch import _get_api_key, _make_request, BASE_URL, fetch_transcript as _fetch_transcript
        import json as _json

        api_key = _get_api_key()
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{BASE_URL}?created_after={since_iso}"
        data = _make_request(url, api_key)

        if isinstance(data, list):
            meetings_raw = data
        elif isinstance(data, dict) and "notes" in data:
            meetings_raw = data["notes"]
        elif isinstance(data, dict) and "data" in data:
            meetings_raw = data["data"]
        else:
            meetings_raw = []

        meetings = []
        for m in meetings_raw:
            mid = m.get("id", "")
            if not mid:
                continue
            transcript = _fetch_transcript(mid)
            meetings.append({
                "id": mid,
                "title": m.get("title", ""),
                "created_at": m.get("created_at", ""),
                "transcript": transcript,
            })
    else:
        meetings = fetch_new_since_last_run()

    if not meetings:
        print("No new calls found", file=sys.stderr)
        return 0

    # Analyze each meeting with non-empty transcript
    entries = []
    for meeting in meetings:
        transcript = meeting.get("transcript", [])
        if not transcript:
            print(f"Skipping '{meeting.get('title', '?')}' - no transcript", file=sys.stderr)
            continue

        analysis = analyze_transcript(transcript)
        entry = format_inbox_entry(meeting, analysis)
        entries.append(entry)

    if not entries:
        print("No calls with transcripts to process", file=sys.stderr)
        return 0

    if dry_run:
        # Print entries to stdout instead of writing
        for entry in entries:
            print(entry)
            print("---")
        print(f"Dry run: {len(entries)} entries would be written to inbox", file=sys.stderr)
        return len(entries)

    # Write to inbox: prepend new entries after the header
    if inbox_path.exists():
        content = inbox_path.read_text(encoding="utf-8")
    else:
        content = "# Inbox\n\n<!-- Items captured via /remember. Review and route to appropriate files periodically. -->\n"

    # Find insertion point: after the header line and any comment block
    lines = content.split("\n")
    insert_after = 0
    for i, line in enumerate(lines):
        if line.startswith("# Inbox"):
            insert_after = i + 1
            # Skip blank lines and HTML comments after header
            while insert_after < len(lines):
                next_line = lines[insert_after].strip()
                if next_line == "" or next_line.startswith("<!--") or next_line.endswith("-->"):
                    insert_after += 1
                else:
                    break
            break

    # Build new content: header + new entries + existing entries
    header_lines = lines[:insert_after]
    existing_lines = lines[insert_after:]

    new_block = "\n".join(entries)
    new_content = "\n".join(header_lines) + "\n" + new_block + "\n" + "\n".join(existing_lines)

    # Full-file write (per CLAUDE.md conventions for data files)
    inbox_path.write_text(new_content, encoding="utf-8")

    print(f"Processed {len(entries)} new calls", file=sys.stderr)

    # JSON summary to stdout
    summary = {
        "processed": len(entries),
        "meetings": [m.get("title", "") for m in meetings if m.get("transcript")],
        "inbox_path": str(inbox_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    return len(entries)


def main():
    parser = argparse.ArgumentParser(
        description="Auto-debrief new Granola calls and write summaries to inbox."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and analyze but don't write to inbox (print entries to stdout)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Override fetch window in hours (default: use state file for incremental fetch)",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Path to repository root (default: auto-detect from script location)",
    )

    args = parser.parse_args()

    count = auto_debrief_new_calls(
        dry_run=args.dry_run,
        hours=args.hours,
        repo_root=args.repo_root,
    )

    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
