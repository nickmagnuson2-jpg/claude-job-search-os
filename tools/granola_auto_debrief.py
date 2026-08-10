#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
granola_auto_debrief.py - Auto-debrief new Granola calls and write summaries to inbox.

Orchestrator script that chains granola_fetch.py and call_analyzer.py.
Called by launchd on schedule (granola-auto-debrief, every 3 hours; the earlier
n8n setup was retired ~2026-04-28). Fetches new meetings since
last run, analyzes each transcript, and prepends formatted summaries
to data/inbox.md.

Functions:
  format_inbox_entry(meeting, analysis) - Format a single meeting analysis as inbox entry
  auto_debrief_new_calls(dry_run, hours) - Main orchestrator: fetch -> analyze -> inbox write
  resolve_destination(meeting, type, project, vault_root) - Where the pair would land
  pull_single_meeting(meeting_id, ...) - Targeted one-meeting persist, no inbox, no cursor

CLI:
  python3 tools/granola_auto_debrief.py             # Run auto-debrief (default)
  python3 tools/granola_auto_debrief.py --dry-run    # Fetch and analyze but print instead of writing
  python3 tools/granola_auto_debrief.py --hours 8    # Override fetch window

  # Targeted single-meeting pull (for skills). REST id is `not_<base62>`, not the MCP UUID.
  python3 tools/granola_auto_debrief.py --meeting-id not_EXAMPLE
  python3 tools/granola_auto_debrief.py --meeting-id not_EXAMPLE --dry-run
  python3 tools/granola_auto_debrief.py --meeting-id not_EXAMPLE \
      --force-type personal --project side-project

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
from tools.granola_fetch import (
    fetch_new_since_last_run,
    fetch_note_full,
    fetch_transcript,
    extract_summary_and_notes,
)
from tools.call_analyzer import analyze_transcript, parse_granola_text
from tools import inbox_lock


import re

# Generic, non-PII therapy title markers. Real therapist identities (names + emails)
# live in the gitignored tools/.therapy-classifier.txt — this repo is public.
# "session with" was removed 2026-08-07: as a bare substring it sealed ordinary
# business calls ("Strategy session with the contractor") into the therapy vault,
# where they vanish from every surface with only a line in an unread log.
THERAPY_TITLE_KEYWORDS = (
    "therapy", "couples", "psychiatrist", "psychotherapy", "counseling",
)

# Nick's own handle — used to find EXTERNAL attendees. The NAME is fork-safe public
# identity; a live email ADDRESS is not (in a public repo it is a scrape surface, and
# hardcoding it here contradicted the pattern used for therapist and personal-project
# identities below). Additional identifiers — email addresses, alternate handles — load
# from the gitignored tools/.owner-identity.txt, one per line, '#' comments allowed.
# With no config file present the name alone is used, so forks work unchanged.
DEFAULT_OWNER_IDENTITY_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".owner-identity.txt"


def _load_owner_identifiers(config_path=None) -> tuple:
    """Owner identifiers: the public name plus any gitignored private ones.

    Best-effort: a missing or unreadable config degrades to the name only.
    """
    ids = ["nick magnuson"]
    path = config_path or DEFAULT_OWNER_IDENTITY_CONFIG
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            tok = line.strip().lower()
            if tok and not tok.startswith("#"):
                ids.append(tok)
    except OSError:
        pass
    return tuple(ids)


NICK_IDENTIFIERS = _load_owner_identifiers()

# Gitignored allowlist of real therapist identities.
DEFAULT_THERAPY_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".therapy-classifier.txt"

VAULT_THERAPY_DIR = Path.home() / "Documents/Obsidian/30-projects/personal/data/therapy"
VOICE_CORPUS_DIR = DEFAULT_REPO_ROOT / "data/voice-corpus/granola"

# Personal-OS (non-job-search) routing. Side-business partner calls, contractor
# meetings, family logistics: these belong in the personal vault, not the
# job-search repo. The allowlist that maps people to projects is gitignored
# because it holds real names; this repo is public.
PERSONAL_VAULT = Path.home() / "Documents/Obsidian/30-projects/personal"
PERSONAL_VOICE_CORPUS_DIR = PERSONAL_VAULT / "data/voice-corpus/granola"
PERSONAL_INBOX = PERSONAL_VAULT / "data/inbox.md"
DEFAULT_PERSONAL_PROJECTS_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".personal-projects.txt"

# Durable review surface for meetings the collector refused to route. Previously
# these were printed to stderr only, which lands in a gitignored launchd log that
# nobody reads — and because the fetch cursor advances unconditionally, the next
# run never sees them again. "Fail closed" was really "fail silent and permanent".
# Gitignored: it holds meeting titles.
PENDING_CLASSIFICATION = DEFAULT_REPO_ROOT / "tools" / ".granola-pending-classification.json"


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


def load_personal_projects_config(path=None) -> dict:
    """Load the gitignored personal-OS project allowlist.

    Format (one directive per line; '#' comments and blanks ignored). Directives
    attach to the most recent 'project:' header, so a file reads as blocks:

      project: <project-slug>
      attendee: <email-or-name>    matched against meeting attendees
      name: <full name>            matched against transcript text (in-person)
      title: <keyword>             matched as a substring of the meeting title

    Returns {'rules': [ {project, attendees:set, names:list, titles:list}, ... ]}
    in file order; the first matching rule wins. A missing file yields no rules,
    which preserves the pre-existing routing exactly.
    """
    path = Path(path) if path else DEFAULT_PERSONAL_PROJECTS_CONFIG
    rules: list = []
    if not path.is_file():
        return {"rules": rules}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if not val:
            continue
        if key == "project":
            current = {"project": val, "attendees": set(), "names": [], "titles": [],
                       "vault": None}
            rules.append(current)
            continue
        if key == "vault" and current is not None:
            # Destination seam. Personal-OS is the default, but work meetings are
            # not personal-OS under the synthesis-destination test, so a project
            # can name its own vault root and the collector follows it. Set this
            # per project rather than hardcoding, so standing up a work vault is a
            # config line rather than a migration.
            current["vault"] = str(Path(val).expanduser())
            continue
        # Directives before any 'project:' header have no home — ignore rather
        # than crash, so a malformed allowlist degrades instead of killing cron.
        if current is None:
            continue
        if key == "attendee":
            current["attendees"].add(val.lower())
        elif key == "name":
            current["names"].append(val)
        elif key == "title":
            current["titles"].append(val.lower())
    return {"rules": rules}


def vault_for_project(project: str, pconfig: dict = None) -> Path:
    """Vault root for a project slug. Defaults to the personal vault.

    The destination seam: a project rule may carry `vault: <path>`. Work meetings
    belong in a work vault, not personal-OS, and this keeps that a one-line config
    change instead of a rewrite once Nick is employed.
    """
    for rule in (pconfig or {}).get("rules", []):
        if rule.get("project") == project and rule.get("vault"):
            return Path(rule["vault"])
    return PERSONAL_VAULT


def match_personal_project(meeting: dict, pconfig: dict = None):
    """Return the personal-OS project slug this meeting belongs to, or None.

    Checks, per rule in file order: attendee identity, then a title keyword,
    then (only when the meeting has no attendees, the in-person case) a name in
    the transcript text. The transcript scan is deliberately scoped the same way
    the therapy one is: a call that HAS attendees is never routed on a passing
    mention of someone's name.
    """
    if not pconfig:
        return None
    title = str(meeting.get("title", "")).lower()
    attendees = [a for a in (meeting.get("attendees") or []) if isinstance(a, dict)]
    text = _transcript_text(meeting.get("transcript", "")).lower()

    for rule in pconfig.get("rules", []):
        for a in attendees:
            name = str(a.get("name", "")).lower()
            email = str(a.get("email", "")).lower()
            for ident in rule["attendees"]:
                if ident and (ident == email or ident == name or ident in name):
                    return rule["project"]
        if any(kw and kw in title for kw in rule["titles"]):
            return rule["project"]
        if not attendees:
            for full in rule["names"]:
                full_low = full.lower().strip()
                if full_low and full_low in text:
                    return rule["project"]
    return None


def _transcript_text(transcript) -> str:
    """Normalize a transcript (str or list of segment dicts) to plain text.

    Speaker labels are included — in a Granola transcript the speaker label may BE
    the therapist's name, which is exactly the in-person signal we want to catch.
    """
    if isinstance(transcript, list):
        parts = []
        for seg in transcript:
            if isinstance(seg, dict):
                # "Speaker: text", one per line. The colon and the line break are
                # load-bearing: speaker-position detection (see
                # _transcript_names_therapist) needs to tell a speaker label from a
                # passing mention, and a space-joined blob erases that distinction.
                parts.append(f"{_speaker_label(seg)}: {seg.get('text', '')}")
            else:
                parts.append(str(seg))
        return "\n".join(parts)
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
    """Guard: does the transcript indicate a therapist was actually IN this session?

    Two tiers of evidence, because the two have very different false-positive rates:

    - **Full name anywhere** ("Inperson Therapist") — strong. Coincidence is unlikely,
      so a passing mention counts.
    - **First name alone** — weak, and only counts at a SPEAKER-LABEL position
      (`Firstname:` at the start of a line). A first name is common enough that a
      passing mention proves nothing: on 2026-08-07 a therapist's first name appeared
      as an ordinary third party in a side-business transcript, and bare first-name
      matching sealed the entire business call into the therapy vault. In a real
      session the therapist speaks, so requiring the speaker position keeps the
      signal and drops the coincidences.
    """
    low = text.lower()
    for full in config.get("names", []):
        full_low = full.lower().strip()
        if not full_low:
            continue
        if full_low in low:
            return True
        first = full_low.split()[0]
        # Speaker label: the name followed by a colon, at the start of a line OR
        # after a sentence break (Granola sometimes returns turns run together on
        # one line: "Me: I wanted to talk about that. Inperson: tell me more.").
        if re.search(r"(?mi)(?:^|[.!?]\s+)\s*" + re.escape(first) + r"[^:\n]{0,20}:", text):
            return True
    return False


def classify_meeting(meeting: dict, config: dict = None, pconfig: dict = None) -> str:
    """Four-way, fail-closed classification of a Granola meeting.

    Returns 'therapy' | 'personal' | 'networking' | 'unknown':
      therapy    -> seal to the personal vault, never inbox
      personal   -> personal-OS: personal vault corpus + personal inbox
      networking -> job-search corpus + job-search inbox
      unknown    -> fail-closed: persist nowhere, flag for manual /granola-pull

    Signals, in priority order:
      1. an attendee matches the therapist allowlist             -> therapy
      2. the title matches a generic therapy keyword             -> therapy
      3. (no-attendee branch only) the transcript names a therapist -> therapy
      4. the meeting matches a personal-OS project rule          -> personal
      5. an external (non-Nick, non-therapist) attendee          -> networking
      6. otherwise                                               -> unknown

    Therapy always outranks personal: a therapy signal can never be downgraded
    by a project keyword. Personal outranks networking so a side-business call
    with a real attendee stops landing in the job-search inbox.

    `pconfig` defaults to no rules rather than loading from disk, so callers that
    predate project routing keep their exact previous behavior.
    """
    if config is None:
        config = load_therapy_classifier_config()
    if pconfig is None:
        pconfig = {"rules": []}

    title = str(meeting.get("title", "")).lower()
    attendees = [a for a in (meeting.get("attendees") or []) if isinstance(a, dict)]
    text = _transcript_text(meeting.get("transcript", ""))

    # 1. Therapist on the attendee list (the virtual-session pattern: attendees populate).
    if any(_attendee_is_therapist(a, config) for a in attendees):
        return "therapy"

    # 2. Generic therapy keyword in the title.
    if any(kw in title for kw in THERAPY_TITLE_KEYWORDS):
        return "therapy"

    # An EXTERNAL party (not Nick, not a known therapist) is what makes a meeting
    # verifiably a real multi-party call. Granola populates attendees from the
    # calendar, so an in-person session has either no attendees at all OR just
    # Nick (a self-created event). Both mean "nobody external is on record."
    external = [a for a in attendees if not _is_nick(a) and not _attendee_is_therapist(a, config)]

    # 3. No external attendee on record: scan the transcript for a therapist name.
    #    Gated on `external`, NOT on `attendees`. Gating on `attendees` skipped this
    #    scan whenever Granola listed Nick as sole attendee, so a solo-attendee
    #    therapy session fell through to the project matcher and could be routed
    #    'personal' with the therapist's name sitting in the transcript. Before
    #    project routing existed that case merely fell to 'unknown' (safe); adding
    #    step 4 turned a fail-closed hole into a sealed-material leak.
    #    Confined to the no-external case so a real multi-party call can never be
    #    mis-sealed by a passing mention of someone's name.
    if not external and _transcript_names_therapist(text, config):
        return "therapy"

    # 4. Personal-OS project match beats the networking default, and rescues the
    #    in-person case that would otherwise fail closed and be dropped entirely
    #    (the 2026-08-06 in-person regression). Reached only after every therapy
    #    signal above has been given its chance.
    if match_personal_project(meeting, pconfig):
        return "personal"

    # 5. An external party means a real job-search-side call.
    if external:
        return "networking"

    # 6. No external party, no therapy signal, no project match -> fail closed.
    return "unknown"


def slugify(text: str) -> str:
    """Slugify a meeting title for filename use."""
    import re
    s = (text or "untitled").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "untitled"


def _speaker_label(seg: dict) -> str:
    """Human-readable speaker label for one transcript segment.

    Granola's REST transcript sometimes returns `speaker` as a plain string
    ("Me", "Speaker A") and sometimes as a dict like
    {'source': 'microphone', 'diarization_label': 'Speaker A'}. The old code
    interpolated the segment's `speaker` value directly, so the dict form was
    stringified into every single line of the saved transcript:

        {'source': 'microphone', 'diarization_label': 'Speaker A'}: And then...

    That is unreadable, inflates the file by ~25%, and corrupts the voice corpus
    (the tier that exists precisely to be a clean record of what was said).
    Prefer the diarization label, then a plain-string speaker, then 'Speaker'.
    """
    sp = seg.get("speaker")
    if isinstance(sp, dict):
        label = sp.get("diarization_label") or sp.get("label") or sp.get("source")
        return str(label) if label else "Speaker"
    if sp:
        return str(sp)
    return "Speaker"


def resolve_destination(meeting: dict, meeting_type: str, project: str = None,
                        vault_root: Path = None) -> dict:
    """Compute where a meeting's two-tier pair would land, without writing anything.

    Split out of persist_via_granola_save so `--meeting-id --dry-run` can report the
    real destination instead of a guess. Returns
    {output_path, session_desc, save_type, local_iso}; `save_type` is the type token
    granola_save.py understands (personal maps to 'general').
    """
    title = meeting.get("title", "Untitled")
    type_ = meeting_type
    created_at = meeting.get("created_at", "")
    vault_root = Path(vault_root) if vault_root else PERSONAL_VAULT

    # Convert UTC timestamp to local time for date extraction.
    # Granola REST returns ISO-8601 UTC (e.g. "2026-05-05T00:04:58.303Z").
    # A 5pm PT session crosses midnight UTC and would otherwise land on the wrong day.
    # time_part must come from the SAME local datetime as date_part. Deriving the
    # date locally but the time from the raw UTC string produced hybrid filenames
    # (a 2:34pm PT call saved as "2026-08-06-2134-..."), and for an evening PT call
    # the two halves can disagree about the day outright.
    time_part = ""
    if created_at:
        try:
            dt_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone()
            date_part = dt_local.strftime("%Y-%m-%d")
            time_part = f"-{dt_local.strftime('%H%M')}"
            local_iso = dt_local.strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            date_part = created_at[:10]
            local_iso = created_at[:16].replace("T", " ")
            if "T" in created_at:
                time_part = f"-{created_at.split('T')[1][:5].replace(':', '')}"
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
    elif type_ == "personal":
        # Personal-OS (or any non-job-search vault): same two-tier filename
        # convention, destination resolved per project via the `vault:` seam.
        filename = f"{date_part}{time_part}-{slugify(title)}.md"
        output_path = vault_root / "data/voice-corpus/granola" / filename
        proj = f" [{project}]" if project else ""
        session_desc = f"{title}{proj} (personal-OS; auto-routed by granola_auto_debrief)"
        # granola_save.py has no 'personal' type; 'general' carries exactly the
        # right semantics (mixed-voice, not sealed, non-job-search).
        type_ = "general"
    else:
        # Networking filename: YYYY-MM-DD-HHMM-{slug}.md
        filename = f"{date_part}{time_part}-{slugify(title)}.md"
        output_path = VOICE_CORPUS_DIR / filename
        session_desc = title

    return {
        "output_path": output_path,
        "session_desc": session_desc,
        "save_type": type_,
        "local_iso": local_iso,
    }


def persist_via_granola_save(meeting: dict, summary: str, private_notes: str,
                             meeting_type: str, project: str = None,
                             vault_root: Path = None) -> dict:
    """Call tools/granola_save.py to persist transcript+summary pair.

    Idempotent — uses --no-overwrite, skips if file exists.

    meeting_type is the already-computed classification ('therapy' | 'personal' |
    'networking'); the orchestrator classifies once (with attendees) and passes it
    down so routing and persistence can never disagree. 'unknown' meetings are
    never persisted — the caller fails closed before reaching this function.

    project is the personal-OS project slug for 'personal' meetings; it is recorded
    in the session description so the transcript says which project it belongs to.

    Returns the JSON status dict from granola_save.py (status: ok|skip|error).
    """
    import subprocess
    title = meeting.get("title", "Untitled")
    dest = resolve_destination(meeting, meeting_type, project, vault_root)
    output_path = dest["output_path"]
    session_desc = dest["session_desc"]
    type_ = dest["save_type"]
    local_iso = dest["local_iso"]

    # Build payload
    transcript = meeting.get("transcript", "")
    if isinstance(transcript, list):
        # If granola_fetch returned segments, join them into "Label: text" lines.
        transcript = "\n".join(
            f"{_speaker_label(seg)}: {seg.get('text', '')}"
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

    # Vault-existence guard. granola_save.py mkdirs its parent, so an unmounted or
    # renamed vault would silently grow a shadow tree that Obsidian and every skill
    # ignore — transcripts landing where nothing will ever look. Fail closed instead.
    if type_ != "networking":
        # Use the resolved vault root, never parent-walking from output_path: the two
        # destinations sit at different depths (<vault>/data/therapy/ vs
        # <vault>/data/voice-corpus/granola/), so a fixed number of .parent hops is
        # right for one and wrong for the other. It previously pointed at
        # <vault>/data for personal calls, which still failed closed by luck but
        # named the wrong path in the error.
        vault_marker = VAULT_THERAPY_DIR.parent.parent if type_ == "therapy" else vault_root
        if not vault_marker.is_dir():
            print(f"  [error] vault not found at {vault_marker} — refusing to create it. "
                  f"'{title}' NOT persisted.", file=sys.stderr)
            return {"status": "error", "reason": "vault-missing", "expected_vault": str(vault_marker)}

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


# The REST API keys notes as `not_<base62>`. The Granola MCP server returns UUIDs
# for the same meetings, and pasting an MCP id into a REST call yields an opaque
# 404 — so catch the shape up front and say what to do instead.
UUID_SHAPED = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def pull_single_meeting(meeting_id: str, force_type: str = None, project: str = None,
                        dry_run: bool = False) -> dict:
    """Persist ONE meeting's two-tier transcript+summary pair by REST id.

    The targeted counterpart to auto_debrief_new_calls: a skill that already knows
    which meeting it wants calls this instead of pulling a 60k-char transcript
    through an LLM's context and re-emitting the text into a payload file. That
    re-emission costs a fortune in tokens and risks corrupting the one artifact
    whose entire job is verbatim fidelity.

    Deliberately narrower than the collector:
      - writes NO inbox entry (neither vault) — persistence only
      - does NOT advance the fetch-state cursor, so the next scheduled collector
        run still sees this window exactly as it would have

    force_type overrides classification (use when the classifier fails closed to
    'unknown'); project overrides the personal-OS project match.

    Returns a result dict; the caller prints it as JSON and picks an exit code.
    """
    if not meeting_id:
        return {"status": "error", "meeting_id": "", "reason": "missing-meeting-id",
                "message": "A --meeting-id is required."}
    if UUID_SHAPED.match(meeting_id.strip()):
        return {
            "status": "error", "meeting_id": meeting_id, "reason": "uuid-shaped-id",
            "message": ("That looks like a Granola MCP UUID. The REST API this tool uses "
                        "keys notes as `not_<base62>`. Get the REST id from "
                        "`python3 tools/granola_fetch.py list --hours 24`."),
        }

    try:
        transcript = fetch_transcript(meeting_id)
    except Exception as e:
        return {"status": "error", "meeting_id": meeting_id, "reason": "transcript-fetch-failed",
                "message": str(e)}
    if not transcript:
        return {"status": "error", "meeting_id": meeting_id, "reason": "no-transcript",
                "message": "Granola returned no transcript for this id."}

    try:
        note_full = fetch_note_full(meeting_id)
        summary, private_notes = extract_summary_and_notes(note_full)
    except Exception as e:
        return {"status": "error", "meeting_id": meeting_id, "reason": "note-fetch-failed",
                "message": str(e)}
    if not isinstance(note_full, dict):
        note_full = {}

    meeting = {
        "id": meeting_id,
        "title": note_full.get("title", "") or "Untitled",
        "created_at": note_full.get("created_at", ""),
        "transcript": transcript,
        "attendees": note_full.get("attendees", []) or [],
    }

    if force_type:
        meeting_type = force_type
        print(f"  [info] classification forced to '{force_type}'", file=sys.stderr)
    else:
        meeting_type = classify_meeting(meeting, load_therapy_classifier_config(),
                                        load_personal_projects_config())

    if meeting_type == "unknown":
        return {
            "status": "error", "meeting_id": meeting_id, "type": "unknown",
            "reason": "unclassified",
            "message": ("Fail-closed: no attendees, no therapy signal, no project match. "
                        "Re-run with --force-type once you have confirmed what this call is."),
        }

    if meeting_type == "personal":
        pconfig = load_personal_projects_config()
        project = project or match_personal_project(meeting, pconfig)
        vault_root = vault_for_project(project, pconfig)
    else:
        project = None
        vault_root = None

    if dry_run:
        dest = resolve_destination(meeting, meeting_type, project, vault_root)
        out = dest["output_path"]
        print(f"  [dry-run] would persist '{meeting['title']}' as {meeting_type}"
              + (f" [{project}]" if project else ""), file=sys.stderr)
        return {
            "status": "dry-run", "meeting_id": meeting_id, "type": meeting_type,
            "project": project, "title": meeting["title"],
            "transcript_path": str(out),
            "summary_path": str(out.with_name(out.stem + "-summary.md")),
        }

    result = persist_via_granola_save(meeting, summary, private_notes,
                                      meeting_type, project, vault_root)
    return {
        "status": result.get("status", "error"),
        "meeting_id": meeting_id,
        "type": meeting_type,
        "project": project,
        "title": meeting["title"],
        # A meeting-id dedup skip reports `existing_path` instead of the pair;
        # surface it under the same key so callers have one field to read.
        "transcript_path": result.get("transcript_path") or result.get("existing_path"),
        "summary_path": result.get("summary_path"),
        "persist": result,
    }


def record_pending_classification(flagged: list, path: Path = None) -> None:
    """Append unroutable meetings to a durable review file (newest last, deduped by id).

    Written atomically. A corrupt or unreadable file is replaced rather than
    allowed to crash the collector — losing the backlog is bad, losing the run is
    worse.
    """
    if not flagged:
        return
    path = Path(path) if path else PENDING_CLASSIFICATION
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        if not isinstance(existing, list):
            existing = []
    except (json.JSONDecodeError, OSError):
        existing = []
    seen = {e.get("id") for e in existing if isinstance(e, dict)}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in flagged:
        if f.get("id") and f["id"] not in seen:
            existing.append({**f, "flagged_at": stamp})
            seen.add(f["id"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def prepend_inbox_entries(inbox_path: Path, entries: list) -> None:
    """Prepend inbox entries after the '# Inbox' header, locked and atomically.

    Delegates to tools/inbox_lock.prepend_entries — the single source of truth for
    inbox writes. This used to hand-roll the header scan and a fixed-name temp
    file: atomic against a crash, but not against a concurrent writer, so a live
    session routing items out of the inbox during the cron's read-write window had
    its changes silently reverted.

    Raises are caught and reported: an inbox collision must not kill a cron run
    that has already persisted transcripts.
    """
    try:
        inbox_lock.prepend_entries(inbox_path, entries)
    except (inbox_lock.LockTimeout, inbox_lock.ConcurrentModification) as exc:
        print(f"  [error] inbox {inbox_path} not updated ({exc}) — {len(entries)} "
              f"entr(ies) NOT written. Re-run the collector or add them manually.",
              file=sys.stderr)


def format_inbox_entry(meeting: dict, analysis: dict, meeting_type: str = "networking",
                       project: str = None) -> str:
    """Format a single meeting's analysis as an inbox entry.

    meeting_type selects the destination vault's idiom: a 'personal' entry points
    at /meeting and names its project, a 'networking' entry points at /debrief.
    Pointing a personal-OS call at /debrief is what sent Nick into the interview
    rubric for a partner meeting on 2026-08-06.

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

    if meeting_type == "personal":
        proj_label = project or "unassigned"
        heading = f"## Call Capture: {title} #personal"
        corpus_line = (
            "Raw transcript persisted via `tools/granola_save.py` to "
            "`personal/data/voice-corpus/granola/`."
        )
        project_line = f"**Project:** `{proj_label}`\n"
        cta = (
            f"> Run `/meeting` to write the structured call note "
            f"(`personal/data/{proj_label}/`), route to-dos, and reconcile against "
            f"standing commitments. This is NOT a job-search call — do not run `/debrief`.\n"
        )
    else:
        heading = f"## Call Debrief: {title} #both"
        corpus_line = (
            "Raw transcript persisted via `tools/granola_save.py` to "
            "`data/voice-corpus/granola/` (or `personal/data/therapy/` if therapy-classified)."
        )
        project_line = ""
        cta = (
            "> Run `/debrief` for full analysis with coached-answer comparison "
            "and anti-pattern tracking.\n"
        )

    entry = (
        f"<!-- voice: cloud-generated; source: granola-auto-debrief -->\n"
        f"{heading}\n"
        f"\n"
        f"> **Voice tier:** cloud-generated (call_analyzer metrics over Granola transcript). "
        f"Per `framework/two-tier-capture.md`. {corpus_line}\n"
        f"\n"
        f"**Date:** {date_display}\n"
        f"{project_line}"
        f"**Questions asked:** {analysis.get('total_questions', 0)}\n"
        f"**Talk ratio:** {talk_str}\n"
        f"\n"
        f"**Filler words:** {filler_str}\n"
        f"\n"
        f"**Top 3 Q&A pairs:**\n"
        f"{qa_block}\n"
        f"\n"
        f"{cta}"
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
    pconfig = load_personal_projects_config()
    entries = []
    personal_entries = {}   # vault root -> [entry, ...]; one bucket per destination vault
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
        attendees_known = True
        try:
            note_full = fetch_note_full(meeting.get("id", ""))
            ai_summary, private_notes = extract_summary_and_notes(note_full)
        except Exception as e:
            print(f"  [warn] fetch_note_full failed for '{title}': {e}", file=sys.stderr)
            note_full, ai_summary, private_notes = {}, "", ""
            attendees_known = False

        meeting["attendees"] = note_full.get("attendees", []) if isinstance(note_full, dict) else []

        if not attendees_known:
            # We do not know who was on this call. An empty attendee list here means
            # "lookup failed", not "nobody external" — classifying on it would let a
            # transient API error decide the routing of possibly-sealed material.
            print(f"  [info] '{title}' attendee lookup failed — fail-closed to unknown; "
                  f"not persisted, not posted. Run /granola-pull to classify manually.",
                  file=sys.stderr)
            flagged.append({"id": meeting.get("id", ""), "title": meeting.get("title", ""),
                            "reason": "attendee-lookup-failed"})
            continue

        meeting_type = classify_meeting(meeting, config, pconfig)
        project = match_personal_project(meeting, pconfig) if meeting_type == "personal" else None
        vault_root = vault_for_project(project, pconfig) if meeting_type == "personal" else None

        # FAIL-CLOSED: an unclassifiable meeting (no attendees, no therapy signal) may be
        # mis-titled sealed therapy started cold from the phone. Persist NOWHERE (not even
        # voice-corpus, also a non-sealed surface) and never post to inbox; flag for manual pull.
        if meeting_type == "unknown":
            print(f"  [info] '{title}' could not be classified (no attendees, no therapy signal) — "
                  f"fail-closed: not persisted, not posted. Run /granola-pull to classify manually.",
                  file=sys.stderr)
            flagged.append({"id": meeting.get("id", ""), "title": meeting.get("title", "")})
            continue

        # Persist raw transcript+summary pair via granola_save.py (voice-tier-separated, idempotent).
        # --dry-run must not touch disk. It used to persist anyway, which made a
        # "safe" preview run silently create a second copy of a transcript that had
        # already been saved by hand under a different slug (2026-08-06).
        if dry_run:
            print(f"  [dry-run] would persist '{title}' as {meeting_type}"
                  + (f" [{project}]" if project else ""), file=sys.stderr)
            persist_results.append({"title": meeting.get("title", ""), "status": "dry-run",
                                    "type": meeting_type, "project": project})
        else:
            try:
                if not ai_summary:
                    print(f"  [warn] No AI summary in REST API for '{title}' — persisting transcript only",
                          file=sys.stderr)
                persist_result = persist_via_granola_save(meeting, ai_summary, private_notes,
                                                          meeting_type, project, vault_root)
                persist_results.append({"title": meeting.get("title", ""), **persist_result})
            except Exception as e:
                print(f"  [warn] Persist via granola_save failed for '{title}': {e}", file=sys.stderr)
                persist_results.append({"title": meeting.get("title", ""), "status": "error",
                                        "exception": str(e)})

        # Therapy: sealed material persisted to vault, never inbox.
        if meeting_type == "therapy":
            print(f"  [info] '{title}' classified as therapy — persisted to vault, skipping inbox", file=sys.stderr)
            continue

        # Networking: the only class that reaches inbox.
        # Handle both string format ("Me: ... Them: ...") and segment list format
        if isinstance(transcript, str):
            transcript = parse_granola_text(transcript)

        # Interview analytics (talk ratio "candidate/interviewer", filler counts) are
        # job-search idiom. On a personal-OS call they are wrong-framed and unused.
        analysis = {} if meeting_type == "personal" else analyze_transcript(transcript)
        entry = format_inbox_entry(meeting, analysis, meeting_type, project)
        if meeting_type == "personal":
            print(f"  [info] '{title}' routed to personal-OS project "
                  f"'{project or 'unassigned'}' — personal vault, not job-search",
                  file=sys.stderr)
            personal_entries.setdefault(str(vault_root), []).append(entry)
        else:
            entries.append(entry)

    if flagged and not dry_run:
        record_pending_classification(flagged)
    if flagged:
        print(f"  [flag] {len(flagged)} meeting(s) held back for manual classification "
              f"(/granola-pull): {', '.join(f.get('title', '?') for f in flagged)}", file=sys.stderr)

    personal_count = sum(len(v) for v in personal_entries.values())
    total = len(entries) + personal_count

    if not total:
        # No inbox entries to write — but persistence/flagging may have happened. Print summary so cron logs show it.
        print("No inbox entries to write (all calls were therapy, unknown, or had no transcripts)", file=sys.stderr)
        if persist_results or flagged:
            summary = {"processed": 0, "persisted": persist_results, "flagged": flagged}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if dry_run:
        # Print entries to stdout instead of writing
        for entry in entries + [e for v in personal_entries.values() for e in v]:
            print(entry)
            print("---")
        print(f"Dry run: {len(entries)} job-search + {personal_count} personal "
              f"entries would be written", file=sys.stderr)
        return total

    # Write to each inbox: prepend new entries after the header (atomic).
    # Two destinations, written independently so a failure on one vault cannot
    # lose the other's entries.
    if entries:
        prepend_inbox_entries(inbox_path, entries)
    for vault_str, bucket in personal_entries.items():
        inbox = Path(vault_str) / "data" / "inbox.md"
        if inbox.parent.is_dir():
            prepend_inbox_entries(inbox, bucket)
        else:
            # Destination vault not mounted. Do NOT fall back to the job-search
            # inbox — that is the bug this whole feature removes.
            print(f"  [error] vault not found at {vault_str} — {len(bucket)} entr(ies) "
                  f"NOT written. Run /granola-pull to recover.", file=sys.stderr)

    print(f"Processed {total} new calls "
          f"({len(entries)} job-search, {personal_count} personal)", file=sys.stderr)

    # JSON summary to stdout
    summary = {
        "processed": total,
        "job_search_entries": len(entries),
        "personal_entries": personal_count,
        "personal_vaults": sorted(personal_entries),
        "meetings": [m.get("title", "") for m in meetings if m.get("transcript")],
        "inbox_path": str(inbox_path),
        "personal_inbox_path": str(PERSONAL_INBOX),
        "persisted": persist_results,
        "flagged": flagged,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    return total


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
    parser.add_argument(
        "--meeting-id",
        type=str,
        default=None,
        help="Targeted mode: persist ONE meeting by its REST id (not_<base62>). "
             "Writes no inbox entry and does not advance the fetch cursor.",
    )
    parser.add_argument(
        "--force-type",
        choices=("personal", "networking", "therapy"),
        default=None,
        help="With --meeting-id: override classification (use when it fails closed to unknown)",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="With --meeting-id: override the personal-OS project slug",
    )

    args = parser.parse_args()

    if args.meeting_id:
        result = pull_single_meeting(
            args.meeting_id,
            force_type=args.force_type,
            project=args.project,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # 'skip' is idempotent success (already persisted), not a failure.
        sys.exit(0 if result.get("status") in ("ok", "skip", "dry-run") else 1)

    count = auto_debrief_new_calls(
        dry_run=args.dry_run,
        hours=args.hours,
        repo_root=args.repo_root,
    )

    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
