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
  python3 tools/granola_auto_debrief.py             # Run auto-debrief (default)
  python3 tools/granola_auto_debrief.py --dry-run    # Fetch and analyze but print instead of writing
  python3 tools/granola_auto_debrief.py --hours 8    # Override fetch window

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
from tools.granola_fetch import fetch_new_since_last_run, fetch_note_full, extract_summary_and_notes
from tools.call_analyzer import analyze_transcript, parse_granola_text


import re

# Generic, non-PII therapy title markers. Real therapist identities (names + emails)
# live in the gitignored tools/.therapy-classifier.txt — this repo is public.
THERAPY_TITLE_KEYWORDS = (
    "therapy", "couples", "psychiatrist", "psychotherapy", "counseling",
    "session with",
)

# Nick's own handle (fork-safe public identity) — used to find EXTERNAL attendees.
NICK_IDENTIFIERS = ("nick magnuson", "nickmagnuson2@gmail.com")

# Gitignored allowlist of real therapist identities.
DEFAULT_THERAPY_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".therapy-classifier.txt"

VAULT_THERAPY_DIR = Path.home() / "Documents/Obsidian/30-projects/personal/data/therapy"
VOICE_CORPUS_DIR = DEFAULT_REPO_ROOT / "data/voice-corpus/granola"


def load_therapy_classifier_config(path=None) -> dict:
    """Load the gitignored therapist allowlist.

    Format (one directive per line; '#' comments and blanks ignored):
      attendee: <name-or-email>   matched against meeting attendees (virtual sessions)
      name: <full name>           matched against transcript text (in-person sessions)

    Returns {'attendees': set[str lowercased], 'names': list[str]}.
    A missing file yields an empty config. The fail-closed default still protects
    safety on its own: an attendee-less, signal-less meeting is 'unknown' regardless.
    """
    path = Path(path) if path else DEFAULT_THERAPY_CONFIG
    attendees: set = set()
    names: list = []
    if not path.is_file():
        return {"attendees": attendees, "names": names}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if not val:
            continue
        if key == "attendee":
            attendees.add(val.lower())
        elif key == "name":
            names.append(val)
    return {"attendees": attendees, "names": names}


def _transcript_text(transcript) -> str:
    """Normalize a transcript (str or list of segment dicts) to plain text.

    Speaker labels are included — in a Granola transcript the speaker label may BE
    the therapist's name, which is exactly the in-person signal we want to catch.
    """
    if isinstance(transcript, list):
        parts = []
        for seg in transcript:
            if isinstance(seg, dict):
                parts.append(f"{seg.get('speaker', '')} {seg.get('text', '')}")
            else:
                parts.append(str(seg))
        return " ".join(parts)
    return transcript or ""


def _is_nick(attendee: dict) -> bool:
    name = str(attendee.get("name", "")).lower()
    email = str(attendee.get("email", "")).lower()
    return any(tok in name or tok in email for tok in NICK_IDENTIFIERS)


def _attendee_is_therapist(attendee: dict, config: dict) -> bool:
    name = str(attendee.get("name", "")).lower()
    email = str(attendee.get("email", "")).lower()
    for ident in config.get("attendees", set()):
        if ident and (ident == email or ident == name or ident in name):
            return True
    return False


def _transcript_names_therapist(text: str, config: dict) -> bool:
    """In-person guard: match a therapist full name OR first name in transcript text.

    The caller scopes this to the no-attendee branch only, where its sole effect is
    unknown -> therapy (the safe direction) — so a real networking call that merely
    mentions a name is never mis-sealed.
    """
    low = text.lower()
    for full in config.get("names", []):
        full_low = full.lower().strip()
        if not full_low:
            continue
        if full_low in low:
            return True
        first = full_low.split()[0]
        if re.search(r"\b" + re.escape(first) + r"\b", low):
            return True
    return False


def classify_meeting(meeting: dict, config: dict = None) -> str:
    """Three-way, fail-closed classification of a Granola meeting.

    Returns 'therapy' | 'networking' | 'unknown':
      therapy    -> seal to the personal vault, never inbox
      networking -> the only class that posts to inbox (+ voice-corpus)
      unknown    -> fail-closed: persist nowhere, flag for manual /granola-pull

    Signals, in priority order:
      1. an attendee matches the therapist allowlist             -> therapy
      2. the title matches a generic therapy keyword             -> therapy
      3. (no-attendee branch only) the transcript names a therapist -> therapy
      4. an external (non-Nick, non-therapist) attendee          -> networking
      5. otherwise                                               -> unknown
    """
    if config is None:
        config = load_therapy_classifier_config()

    title = str(meeting.get("title", "")).lower()
    attendees = [a for a in (meeting.get("attendees") or []) if isinstance(a, dict)]
    text = _transcript_text(meeting.get("transcript", ""))

    # 1. Therapist on the attendee list (the virtual-session pattern: attendees populate).
    if any(_attendee_is_therapist(a, config) for a in attendees):
        return "therapy"

    # 2. Generic therapy keyword in the title.
    if any(kw in title for kw in THERAPY_TITLE_KEYWORDS):
        return "therapy"

    # 3. In-person branch (no attendees at all): transcript name scan. Confined here so
    #    a real call (which HAS attendees) can never be mis-sealed by a passing mention.
    if not attendees:
        if _transcript_names_therapist(text, config):
            return "therapy"
        return "unknown"  # in-person + no therapy signal is ambiguous -> fail closed

    # 4. Attendees present: a non-Nick, non-therapist party means a real call.
    external = [a for a in attendees if not _is_nick(a) and not _attendee_is_therapist(a, config)]
    if external:
        return "networking"

    # 5. Only Nick (or only-therapist, already returned) -> fail closed.
    return "unknown"


def slugify(text: str) -> str:
    """Slugify a meeting title for filename use."""
    import re
    s = (text or "untitled").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "untitled"


def persist_via_granola_save(meeting: dict, summary: str, private_notes: str,
                             meeting_type: str) -> dict:
    """Call tools/granola_save.py to persist transcript+summary pair.

    Idempotent — uses --no-overwrite, skips if file exists.

    meeting_type is the already-computed classification ('therapy' | 'networking');
    the orchestrator classifies once (with attendees) and passes it down so routing
    and persistence can never disagree. 'unknown' meetings are never persisted —
    the caller fails closed before reaching this function.

    Returns the JSON status dict from granola_save.py (status: ok|skip|error).
    """
    import subprocess
    title = meeting.get("title", "Untitled")
    type_ = meeting_type
    created_at = meeting.get("created_at", "")

    # Convert UTC timestamp to local time for date extraction.
    # Granola REST returns ISO-8601 UTC (e.g. "2026-05-05T00:04:58.303Z").
    # A 5pm PT session crosses midnight UTC and would otherwise land on the wrong day.
    if created_at:
        try:
            dt_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone()
            date_part = dt_local.strftime("%Y-%m-%d")
            local_iso = dt_local.strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            date_part = created_at[:10]
            local_iso = created_at[:16].replace("T", " ")
    else:
        date_part = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        local_iso = ""

    # Pick destination per type
    if type_ == "therapy":
        # Therapy filename: YYYY-MM-DD-therapy-{therapist}-transcript.md (matches existing convention)
        therapist_slug = slugify(title.lower().replace("therapy", "").replace("session with", ""))
        if not therapist_slug or therapist_slug == "untitled":
            therapist_slug = slugify(title)
        filename = f"{date_part}-therapy-{therapist_slug}-transcript.md"
        output_path = VAULT_THERAPY_DIR / filename
        session_desc = f"{title} (auto-classified as therapy by title; verify therapist name)"
    else:
        # Networking filename: YYYY-MM-DD-{slug}.md
        time_part = ""
        if "T" in created_at:
            time_part = created_at.split("T")[1][:5].replace(":", "")
            time_part = f"-{time_part}"
        filename = f"{date_part}{time_part}-{slugify(title)}.md"
        output_path = VOICE_CORPUS_DIR / filename
        session_desc = title

    # Build payload
    transcript = meeting.get("transcript", "")
    if isinstance(transcript, list):
        # If granola_fetch returned segments, join them simply
        transcript = "\n".join(
            f"{seg.get('speaker', 'Speaker')}: {seg.get('text', '')}"
            for seg in transcript if isinstance(seg, dict)
        )

    payload = {
        "meeting_id": meeting.get("id", ""),
        "title": title,
        "captured": local_iso,
        "transcript": transcript,
        "summary": summary,
        "private_notes": private_notes,
        "type": type_,
        "session_desc": session_desc,
        "speaker_note": "Auto-persisted by granola_auto_debrief.py from REST API (private_notes not exposed via REST). Speaker labels depend on Granola plan tier.",
    }

    # Pipe to granola_save.py
    try:
        result = subprocess.run(
            ["python3", str(DEFAULT_REPO_ROOT / "tools/granola_save.py"), "write",
             "--output", str(output_path), "--no-overwrite"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            print(f"granola_save.py error for '{title}': {result.stderr}", file=sys.stderr)
            return {"status": "error", "stderr": result.stderr}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "ok", "raw": result.stdout}
    except Exception as e:
        print(f"granola_save.py invocation failed: {e}", file=sys.stderr)
        return {"status": "error", "exception": str(e)}


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
        f"<!-- voice: cloud-generated; source: granola-auto-debrief -->\n"
        f"## Call Debrief: {title} #both\n"
        f"\n"
        f"> **Voice tier:** cloud-generated (call_analyzer metrics over Granola transcript). "
        f"Per `framework/two-tier-capture.md`. Raw transcript persisted via `tools/granola_save.py` to `data/voice-corpus/granola/` (or `personal/data/therapy/` if therapy-classified).\n"
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
    config = load_therapy_classifier_config()
    entries = []
    persist_results = []
    flagged = []  # unknown / fail-closed meetings, surfaced for manual /granola-pull
    for meeting in meetings:
        transcript = meeting.get("transcript", [])
        title = meeting.get("title", "?")
        if not transcript:
            print(f"Skipping '{title}' - no transcript", file=sys.stderr)
            continue

        # Fetch the full note (AI summary + attendees) and classify ONCE, with attendees.
        # The list endpoint the cron uses returns no attendees; the detail endpoint does.
        try:
            note_full = fetch_note_full(meeting.get("id", ""))
            ai_summary, private_notes = extract_summary_and_notes(note_full)
        except Exception as e:
            print(f"  [warn] fetch_note_full failed for '{title}': {e}", file=sys.stderr)
            note_full, ai_summary, private_notes = {}, "", ""

        meeting["attendees"] = note_full.get("attendees", []) if isinstance(note_full, dict) else []
        meeting_type = classify_meeting(meeting, config)

        # FAIL-CLOSED: an unclassifiable meeting (no attendees, no therapy signal) may be
        # mis-titled sealed therapy started cold from the phone. Persist NOWHERE (not even
        # voice-corpus, also a non-sealed surface) and never post to inbox; flag for manual pull.
        if meeting_type == "unknown":
            print(f"  [info] '{title}' could not be classified (no attendees, no therapy signal) — "
                  f"fail-closed: not persisted, not posted. Run /granola-pull to classify manually.",
                  file=sys.stderr)
            flagged.append({"id": meeting.get("id", ""), "title": meeting.get("title", "")})
            continue

        # Persist raw transcript+summary pair via granola_save.py (voice-tier-separated, idempotent)
        try:
            if not ai_summary:
                print(f"  [warn] No AI summary in REST API for '{title}' — persisting transcript only", file=sys.stderr)
            persist_result = persist_via_granola_save(meeting, ai_summary, private_notes, meeting_type)
            persist_results.append({"title": meeting.get("title", ""), **persist_result})
        except Exception as e:
            print(f"  [warn] Persist via granola_save failed for '{title}': {e}", file=sys.stderr)
            persist_results.append({"title": meeting.get("title", ""), "status": "error", "exception": str(e)})

        # Therapy: sealed material persisted to vault, never inbox.
        if meeting_type == "therapy":
            print(f"  [info] '{title}' classified as therapy — persisted to vault, skipping inbox", file=sys.stderr)
            continue

        # Networking: the only class that reaches inbox.
        # Handle both string format ("Me: ... Them: ...") and segment list format
        if isinstance(transcript, str):
            transcript = parse_granola_text(transcript)

        analysis = analyze_transcript(transcript)
        entry = format_inbox_entry(meeting, analysis)
        entries.append(entry)

    if flagged:
        print(f"  [flag] {len(flagged)} meeting(s) held back for manual classification "
              f"(/granola-pull): {', '.join(f.get('title', '?') for f in flagged)}", file=sys.stderr)

    if not entries:
        # No inbox entries to write — but persistence/flagging may have happened. Print summary so cron logs show it.
        print("No inbox entries to write (all calls were therapy, unknown, or had no transcripts)", file=sys.stderr)
        if persist_results or flagged:
            summary = {"processed": 0, "persisted": persist_results, "flagged": flagged}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
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
        "persisted": persist_results,
        "flagged": flagged,
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
