#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting_vocab.py — the one place a Granola meeting is CLASSIFIED.

Extracted from granola_auto_debrief.py on 2026-08-19.

WHY: two orchestrators need this vocabulary — granola_auto_debrief.py (the launchd
job) and granola_cli.py (the interactive /granola-pull path). Before this module,
the CLI imported the rules FROM the 1149-line launchd script, so the interactive
path dragged in the whole scheduled-run module for a handful of functions. The
rules were never duplicated (there was one source of truth, correctly), but that
source lived inside a sibling orchestrator rather than in a module of its own.

This is the `tools/stage_vocab.py` shape named in CLAUDE.md: one module owns a
domain rule, every consumer imports it, and a guard test keeps a second copy from
appearing.

WHAT LIVES HERE: the therapy/personal/networking/unknown classifier, the therapist
allowlist and personal-project config loaders, the owner-identity list, and the
attendee/transcript predicates the classifier is built from.

SEALING NOTE: classification decides whether a meeting is sealed to the personal
vault. It is fail-closed by design — an unclassifiable meeting returns 'unknown'
and is persisted NOWHERE rather than guessed into a destination. Preserve that
property in any change here.
"""
import json
import os
import re
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_OWNER_IDENTITY_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".owner-identity.txt"
DEFAULT_PERSONAL_PROJECTS_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".personal-projects.txt"
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

    The shape the REST API actually returns, measured 2026-08-24 against two live
    transcripts, is neither of the above:

        {'source': 'microphone', 'attribution': 'me'}
        {'source': 'speaker',    'attribution': 'them'}

    `source` is the AUDIO CHANNEL, not the person. `attribution` is the field
    carrying who spoke, and it maps onto the Me:/Them: convention every downstream
    consumer splits on.

    Measured across the whole saved corpus on 2026-08-24: 9,144 corrupted lines in
    25 files, in two shapes. 814 carry `attribution` alongside `source`, and that
    overlap establishes the mapping for the 8,330 that do not -- `microphone` (the
    local mic) always pairs with `me`, `speaker` (system audio) always pairs with
    `them`. So the channel IS recoverable to a person; it just is not the person,
    and must be translated rather than printed.

    Precedence: attribution -> diarization label -> plain-string speaker ->
    source-as-channel -> raw source -> 'Speaker'.
    """
    sp = seg.get("speaker")
    if isinstance(sp, dict):
        attribution = str(sp.get("attribution") or "").strip().lower()
        if attribution == "me":
            return "Me"
        if attribution == "them":
            return "Them"
        label = sp.get("diarization_label") or sp.get("label")
        if label:
            return str(label)
        source = str(sp.get("source") or "").strip().lower()
        if source == "microphone":
            return "Me"
        if source == "speaker":
            return "Them"
        return str(sp.get("source")) if sp.get("source") else "Speaker"
    if sp:
        return str(sp)
    return "Speaker"

THERAPY_TITLE_KEYWORDS = (
    "therapy", "couples", "psychiatrist", "psychotherapy", "counseling",
)
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
DEFAULT_THERAPY_CONFIG = DEFAULT_REPO_ROOT / "tools" / ".therapy-classifier.txt"
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


# ---------------------------------------------------------------------------
# Text-level speaker splitting (the READ path)
# ---------------------------------------------------------------------------
# _speaker_label above maps a label at FETCH time, from the API's segment dicts. This
# section maps labels at READ time, out of an already-persisted transcript file. Both need
# the same vocabulary, so it lives here once rather than being re-derived per consumer.
#
# WHY (2026-08-24): three label formats exist on disk and every downstream consumer
# hardcoded `Me:` only, so anything else parsed to ZERO attributable turns and dropped out
# of per-speaker analysis with no error. That silent dropout narrowed a filler-density
# baseline enough to make a false "lowest in the corpus" claim survive review. A parse
# failure that returns an empty list is indistinguishable from a quiet meeting.

_SPEAKER_ALIASES = {
    # canonical
    "me": "Me", "them": "Them",
    # Granola desktop export: local mic vs system audio
    "microphone": "Me", "speaker": "Them",
}

# Matches a speaker label at the start of a line OR inline mid-paragraph, because Granola
# writes both shapes: newline-delimited turns in some exports, one long run-on in others.
_TURN_RE = re.compile(
    r"(?:(?<=^)|(?<=\s))(Me|Them|Microphone|Speaker)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)

_CORRUPT_DICT_LABEL_RE = re.compile(r"\{'source':[^}]*\}:")


# A NAMED-speaker turn: `**Nick:**`, `Nick:`, `*Taylor:*`. Markdown bold around the label is
# common in hand-formatted transcripts and was what hid this format for months -- the earlier
# pattern required a capital letter directly after whitespace, and `**` sat in between.
_NAMED_TURN_RE = re.compile(
    r"(?:(?<=^)|(?<=\s))\*{0,2}([A-Z][A-Za-z.'-]{1,20})\s*:\*{0,2}\s",
    re.MULTILINE,
)

# A named label must appear at least this often to count as a speaker rather than a prose
# colon. `Format:`, `Speakers:`, `Cross-references:` each appear once in these files; real
# speakers appear dozens of times. Without this floor, a header line becomes a participant.
_MIN_NAMED_TURNS = 3


def _named_speaker_turns(text: str, owner_ids: tuple) -> tuple[list[str], list[str]] | None:
    """Split a transcript labelled with real names, e.g. `**Nick:**` / `**Taylor:**`.

    Returns None when the text does not clearly use this scheme, so the caller can fall
    through rather than guess. Deliberately conservative: it must find at least two distinct
    labels each used >= _MIN_NAMED_TURNS times, and EXACTLY ONE of them must match an owner
    identifier. Anything else -- one speaker, three speakers, no owner match, two owner
    matches -- returns None instead of picking. Guessing who is who is worse than declining,
    because a wrong split silently attributes the counterpart's words to Nick and every
    downstream number inherits it.
    """
    counts: dict[str, int] = {}
    for m in _NAMED_TURN_RE.finditer(text):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    speakers = {n for n, c in counts.items() if c >= _MIN_NAMED_TURNS}
    if len(speakers) < 2:
        return None

    owner_tokens = set(owner_ids) | {i.split()[0] for i in owner_ids if i}
    owners = {s for s in speakers if s.lower() in owner_tokens}
    if len(owners) != 1:
        return None
    owner_label = owners.pop()

    parts = _NAMED_TURN_RE.split(text)
    if len(parts) < 3:
        return None
    owner: list[str] = []
    other: list[str] = []
    for i in range(1, len(parts) - 1, 2):
        label, body = parts[i], parts[i + 1].strip()
        if label not in speakers or not body:
            continue
        (owner if label == owner_label else other).append(body)
    if not owner and not other:
        return None
    return owner, other


def split_transcript_turns(text: str) -> tuple[list[str], list[str]]:
    """Split persisted transcript text into (owner_turns, counterpart_turns).

    Handles every label format known to appear on disk:
      * `Me:` / `Them:`                  -- canonical
      * `Microphone:` / `Speaker:`       -- Granola desktop export
      * `{'source': 'microphone'}:`      -- the 2026-08 REST corruption (repaired in the
                                            corpus, still handled so a stale file cannot
                                            silently score zero)

    Returns two lists of turn strings. An empty owner list means NO labels were recognised,
    which callers must treat as a parse failure to report, never as a quiet participant.
    """
    text = _CORRUPT_DICT_LABEL_RE.sub(
        lambda m: "Me: " if "microphone" in m.group(0) else "Them: ", text)

    parts = _TURN_RE.split(text)
    if len(parts) < 3:
        # No channel-style label. Try NAMED speakers before giving up: a transcript written
        # `**Nick:** / **Taylor:**` is perfectly attributable, and treating it as unparseable
        # is what kept a real behavioural screen out of every per-speaker analysis.
        named = _named_speaker_turns(text, NICK_IDENTIFIERS)
        return named if named else ([], [])
    owner: list[str] = []
    other: list[str] = []
    # parts = [pre, label, body, label, body, ...]
    for i in range(1, len(parts) - 1, 2):
        canon = _SPEAKER_ALIASES.get(parts[i].lower())
        body = parts[i + 1].strip()
        if not body:
            continue
        (owner if canon == "Me" else other).append(body)
    return owner, other
