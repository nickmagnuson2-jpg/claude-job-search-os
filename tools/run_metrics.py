#!/usr/bin/env python3
"""run_metrics.py - rebuild one run's metrics from Claude Code session transcripts.

WHY. Of the 9 overnight runs from 10/26 to 11/21, none recorded dispatches, failures,
outcomes and timings together, and none recorded timings at all (DECISIONS.md row C7,
2026-11-21). Choosing what to codify next was judged rather than measured. This script
measures it from the transcripts, which every session writes whether or not anyone
remembers to log.

WHAT IT REPORTS for a time window (one run):
  * per session: name, first and last line in the window, active minutes (gaps over the
    idle cap excluded), Nick's prompts and Nick-minutes, branches seen.
  * per branch: first commit, landing time on the main ref, minutes from one to the other.
  * failure candidates: each event that reads as a caught failure, and who caught it
    (nick | org | peer | hook | self). CANDIDATES, not a count: validated 2026-11-23 on
    the 11-21 run, the catcher matched 6 of 14 hand-coded failure rows, and many
    candidates are not failures (see output/overnight-112226/S6-C7-report.md). A run's
    failure table is still written by the org; these candidates are its starting list.
  * run fields (RUN_FIELDS), which the overnight brief template asks every run to write.

DEFINITIONS (stated here because each one is a choice):
  * Nick prompt: a user line whose `origin.kind` is "human". Peer messages, task
    notifications, tool results and meta lines are not Nick.
  * Nick-minutes: for each Nick prompt, the time since the session's previous assistant
    line, capped at --nick-cap-min (default 10). A first prompt with no earlier output
    counts 0. This measures reading-and-replying time, not wall-clock attention.
  * Session name: found by matching the body of a SendMessage this session sent to a
    cross-session message another session received (which carries `from-name`). A session
    that sent nothing anyone received is named "sid:<first 8 chars>".
  * Failure and catcher. These are text heuristics, so validate them against a hand-coded
    table before trusting counts:
      - hook: a tool error naming a hook script, with "BLOCKED" or "hook error";
      - nick: a Nick prompt matching CORRECTION_RE;
      - org/peer: a received cross-session message matching CORRECTION_RE, classed org
        when the sender is in --org;
      - self: a message this session sent matching SELF_CORRECTION_RE.
    A plain tool error (file not found, a failing command) is not a caught failure.
  * Sealed filter: a typed turn within SEALED_WINDOW (5) typed turns of a record that
    touches a sealed marker keeps its counts and loses its text, so it is never a failure
    candidate; sealed_excluded counts those turns (see scan_sealed).
  * Branch commits: the commit, rebase and cherry-pick entries in the branch's reflog that
    are still in its history (so a reset or rebase onto a later main does not count main's
    commits); without a reflog, main..tip. First commit = the earliest author time among
    them. A branch with no commits of its own has no timing. Commits brought in by
    `git reset` are not counted, so `reset_entries` > 0 means first_commit may be late.
  * Landing: the earliest entry of --land-ref's reflog (default origin/main: each push)
    that contains the tip. Without such an entry, the commit time of the first main
    first-parent commit containing the tip (land_source says which). A push this clone
    never recorded reads as not landed.

OUTPUT. JSON on stdout, or to --out. An --out path inside the repo must be gitignored
(run output is personal data and this repo is public).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/run_metrics.py \
      --start 2026-01-10T16:00:00-08:00 --end 2026-01-11T09:00:00-08:00 \
      --org <org-session-name> --run-id <id> --out output/<dir>/run-metrics.json
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# tools/ is sys.path[0] only for a script run; this line keeps `import tools.run_metrics` working
# (tests/scripts/test_shared_module_package_import.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_runtime import claude_project_dir  # noqa: E402
# The sealed markers come from the guard by import, never typed or re-parsed here: a rename
# there breaks tests/scripts/test_run_metrics_sealed.py, not this filter silently (KB-S2).
import check_sealed_read  # noqa: E402
import meeting_vocab  # noqa: E402
import vault_paths  # noqa: E402



def transcripts_dir_for(repo: Path) -> Path:
    """Claude Code's transcript folder for a repo, from the repo's one canonical helper
    (hook_runtime.claude_project_dir: "/" becomes "-", dots and spaces kept,
    CLAUDE_CONFIG_DIR honoured). Derived, so no home-directory name is typed into this
    public file; delegated, so there is no second copy of the rule (Grok r2 F1)."""
    return claude_project_dir(repo)

EXCERPT_CHARS = 240
MATCH_CHARS = 200

RUN_FIELDS = (
    "run_id", "window_start", "window_end", "org", "sessions_count", "sessions",
    "org_messages_sent", "nick_prompts_total", "nick_minutes_total",
    "branches", "failure_candidates", "candidates_by_catcher_class", "definitions",
    "sealed_excluded_total",
)
SESSION_FIELDS = (
    "name", "session_id", "first", "last", "wall_min", "active_min",
    "nick_prompts", "nick_minutes", "sent_messages", "branches", "sealed_excluded",
)

CORRECTION_RE = re.compile(
    r"\b(wrong|incorrect|correction|corrected|mistake|stale|missed|overwr\w*|race|"
    r"not true|isn't true|that's not|you didn't|didn't you|why (?:did|didn't|aren't|isn't)|duplicate)\b",
    re.I)
# Nick catches by asking or redirecting more often than by naming an error.
NICK_CATCH_RE = re.compile(r"^\s*(?:why\b|are you\b|did you\b|have you\b|again\b|make sure\b)", re.I)
SELF_CORRECTION_RE = re.compile(r"\b(correction|corrected|i was wrong|my mistake|i misstated|retract\w*)\b", re.I)
HOOK_RE = re.compile(r"(check_[a-z0-9_]+\.py|[a-z0-9_]+_guard\.py)")
WORKTREE_RE = re.compile(r"(?:claude-worktrees|\.claude/worktrees)/([A-Za-z0-9._-]+)")
XSESSION_RE = re.compile(r'<cross-session-message[^>]*from-name="([^"]*)"[^>]*>\n?(.*?)(?:</cross-session-message>|$)', re.S)


def parse_ts(s: str) -> float:
    """ISO 8601 (with Z or an offset) to epoch seconds."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def active_minutes(stamps: list[float], idle_cap_min: float = 10) -> float:
    stamps = sorted(stamps)
    total = 0.0
    for a, b in zip(stamps, stamps[1:]):
        gap = b - a
        if gap <= idle_cap_min * 60:
            total += gap
    return total / 60


def _norm(text: str) -> str:
    return " ".join(text.split())[:MATCH_CHARS]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                parts.append(c if isinstance(c, str) else _text_of(c))
    return "\n".join(parts)


@dataclass
class Session:
    session_id: str
    stamps: list = field(default_factory=list)
    assistant_stamps: list = field(default_factory=list)
    human: list = field(default_factory=list)      # (t, text)
    received: list = field(default_factory=list)   # (t, from_name, body)
    sent: list = field(default_factory=list)       # (t, to, body)
    hook_errors: list = field(default_factory=list)  # (t, text)
    branches: set = field(default_factory=set)
    worktrees: set = field(default_factory=set)
    lookbehind: float | None = None
    sealed_excluded: int = 0     # typed turns in the window whose text was dropped (sealed filter)
    _seen_received: set = field(default_factory=set, repr=False)

    @property
    def first(self):
        return min(self.stamps)

    @property
    def last(self):
        return max(self.stamps)

    @property
    def nick_prompts(self):
        return len(self.human)

    def nick_minutes(self, cap_min: float = 10) -> float:
        prior = sorted(self.assistant_stamps + ([self.lookbehind] if self.lookbehind else []))
        total = 0.0
        for t, _ in self.human:
            before = [a for a in prior if a < t]
            if before:
                total += min(t - before[-1], cap_min * 60)
        return total / 60


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _add_received(s: "Session", t: float, text: str, sealed: bool = False) -> None:
    """Record each cross-session message in `text` once per session (same sender and body).
    A message inside a sealed window keeps its sender and time; its body is None."""
    for m in XSESSION_RE.finditer(text):
        key = (m.group(1), _norm(m.group(2)))
        if key in s._seen_received:
            continue
        s._seen_received.add(key)
        s.received.append((t, m.group(1), None if sealed else m.group(2)))


# ---------------------------------------------------------------- sealed filter (KB-S2, 2026-11-26)
#
# A transcript turn near a sealed read is sealed by content, not only by path (KB brief
# constraint 5). The rule, accepted by org 3e for KB-A: a record that TOUCHES a sealed marker
# in typed turn k excludes typed turns k-window .. k+window. A touch before the first typed
# turn counts as turn 0. Touches are looked for in every record, sidechain and pre-window
# ones included; the always-loaded instruction records below name the sealed folder in
# nearly every session and are not touches (KB-A: reading them as touches removed 70%).
#
# Only class names leave this filter (touches_by_class); marker values never do, because
# the vault folder names are private (tools/.sealed-folders.conf is gitignored).

SEALED_WINDOW = 5
# None = the guard's own reading of tools/.sealed-folders.conf (check_sealed_read.VAULT_RELS).
DEFAULT_SEALED_CONFIG: Path | None = None
NOT_TOUCH_TYPES = frozenset({"api-request", "api-request-blob", "api-request-shape", "queue-operation"})
NOT_TOUCH_ATTACHMENTS = frozenset({"instructions", "invoked_skills", "skill_listing",
                                   "queued_command", "nested_memory"})


def sealed_markers(sealed_config: Path | None = None) -> tuple:
    """(class, lowercased marker) pairs, built only from the guard's names.

    sibling: the sealed sibling directory; in_tree: the old in-repo folder's own name;
    override: the SEAL_READ_OK variable, in any spelling of its value; vault_parent: a
    directory above a sealed folder inside the vault, counted only as a path that ENDS there
    (the guard's _PARENT_END), never a named file beside the folder; vault: each folder in the sealed-folders config;
    fail_closed_vault: the whole vault data/ when that config is missing, malformed, empty
    or names a folder that is not there (the guard fails closed the same way)."""
    marks = [("sibling", check_sealed_read.SEALED_HOME.lower()),
             ("in_tree", check_sealed_read.SEALED_REL.rsplit("/", 1)[-1].lower()),
             # The variable's bare name: `NAME='1'` and `export NAME="1"` override as well, and
             # the whole `NAME=1` word missed 9 of KB-A's 2,325 exclusions (reconciled 11-26).
             ("override", check_sealed_read.OVERRIDE_WORD.split("=", 1)[0].lower())]
    vault = check_sealed_read.PERSONAL.rsplit("/", 1)[-1]
    if sealed_config is None:
        rels, closed = check_sealed_read.VAULT_RELS, check_sealed_read.FAIL_CLOSED
    else:
        try:
            rels = tuple(r.lower() for r in (vault_paths.sealed_rels(Path(sealed_config)) or ()))
        except ValueError:
            rels = ()
        root = check_sealed_read._root() / check_sealed_read.PERSONAL
        closed = not rels or not all((root / r).is_dir() for r in rels)
    if closed:   # first, so the same string as a vault ancestor keeps this class
        marks.append(("fail_closed_vault", f"{vault}/data".lower()))
    for r in rels:
        # The guard also counts a folder by its basename when path-shaped (`<name>/`) and a
        # read of any directory above it inside the vault (`grep -R x ../<vault>/data`), so
        # each of those is a marker too (Codex 2026-11-26 F1, P0). Wider, never narrower.
        parts = r.split("/")
        marks += [("vault", r), ("vault", parts[-1] + "/")]
        marks += [("vault_parent", f"{vault}/" + "/".join(parts[:i]).lower()) for i in range(1, len(parts))]
    seen, out = set(), []
    for c, m in marks:
        if m not in seen:
            seen.add(m)
            out.append((c, m))
    return tuple(out)


def is_touch(record: dict) -> bool:
    """Can this record touch the sealed set? KB-A's accepted reading (org 3e, 2026-11-26)."""
    kind = record.get("type")
    if kind in NOT_TOUCH_TYPES:
        return False
    if kind == "attachment" and (record.get("attachment") or {}).get("type") in NOT_TOUCH_ATTACHMENTS:
        return False
    if kind == "user" and record.get("isMeta") and record.get("toolUseResult") is None:
        return False
    return True


def is_typed(record: dict) -> bool:
    """run_metrics' Nick-prompt rule: a human-origin user line, not meta, not a sidechain."""
    return (record.get("type") == "user" and (record.get("origin") or {}).get("kind") == "human"
            and not record.get("isMeta") and not record.get("isSidechain"))


@functools.lru_cache(maxsize=None)
def _parent_re(marker: str):
    return re.compile(re.escape(marker) + "/*" + check_sealed_read._PARENT_END)


def marker_hits(low: str, markers) -> list:
    """Classes of the markers in lowercased text, each once, in marker order."""
    return list(dict.fromkeys(
        c for c, m in markers
        if (_parent_re(m).search(low) if c == "vault_parent" else m in low)))


@dataclass
class SealedScan:
    turn_of_line: dict           # 1-based line -> typed-turn index it falls in (0 before the first)
    typed_lines: list            # 1-based lines of typed records, in order
    bad_turns: set               # typed-turn indexes inside a sealed window
    touches_by_class: dict

    def sealed(self, line: int) -> bool:
        return self.turn_of_line.get(line, 0) in self.bad_turns


def _guard_names(d, raw: str) -> bool:
    """The guard's own matcher (quote stripping, basenames, parents) on every decoded string
    of a record, or on the raw line when it does not parse. JSON escapes would hide a name
    from it on the raw line (`\\n<name>` reads as one word), hence the decoded strings."""
    texts = _strings(d) if isinstance(d, (dict, list)) else (raw,)
    return (any(check_sealed_read._names_sealed(t) for t in texts)
            or (isinstance(d, dict) and _guard_tool_reads(d)))


def _guard_tool_reads(d: dict) -> bool:
    """Would the guard block a tool call in this record? A walk from the vault root
    (`cd <vault> && grep -r x .`, Grep on the vault root) names no sealed folder, so only
    the guard's Bash parsing with cd tracking and its target test see it (Codex 2026-11-26
    round 2 F1, P0). Fields per tool follow check_sealed_read.main; an error is a touch."""
    content = (d.get("message") or {}).get("content")
    root = check_sealed_read._root()
    for b in content if isinstance(content, list) else ():
        if not (isinstance(b, dict) and b.get("type") == "tool_use"):
            continue
        name, ti = b.get("name"), b.get("input") or {}
        try:
            if name == "Bash" and check_sealed_read._bash_reads_sealed(str(ti.get("command") or "")):
                return True
            if name in ("Read", "Grep", "Glob"):
                base = str(ti.get("file_path") or ti.get("path") or "")
                glob = str(ti.get("pattern") if name == "Glob" else ti.get("glob") or "")
                wild = re.search(r"[*?\[]", glob)
                start = os.path.dirname(glob[:wild.start()]) if wild else ""
                for t in (base, os.path.join(base, start) if start else ""):
                    if t and check_sealed_read._under_sealed(root, t):
                        return True
        except Exception:   # the guard could not judge it: count it, never let it pass
            return True
    return False


def scan_sealed(path: Path, markers=None, window: int = SEALED_WINDOW, typed=is_typed,
                guard: bool | None = None) -> SealedScan:
    """One pass over a transcript: which typed turns sit inside a sealed window.

    markers None = the default config's markers. guard: also ask the guard's own matcher,
    which reads the real config, so it applies only when the default config is in use
    (None = decide from that)."""
    if guard is None:
        guard = markers is None and DEFAULT_SEALED_CONFIG is None
    markers = sealed_markers(DEFAULT_SEALED_CONFIG) if markers is None else markers
    turn_of_line, typed_lines, touched, by_class = {}, [], [], {}
    cur = -1
    with open(path, errors="replace") as f:
        for n, raw in enumerate(f, 1):
            try:
                d = json.loads(raw)
            except ValueError:
                d = None
            if isinstance(d, dict) and typed(d):
                cur += 1
                typed_lines.append(n)
            turn_of_line[n] = max(cur, 0)
            # An unparseable line is checked too: failing to read it is no evidence it is clean.
            if isinstance(d, dict) and not is_touch(d):
                continue
            low = raw.lower()
            hit = marker_hits(low, markers)
            if not hit and guard and _guard_names(d, raw):
                hit = ["guard"]
            if hit:
                touched.append(max(cur, 0))
                for c in hit:
                    by_class[c] = by_class.get(c, 0) + 1
    bad = set()
    for k in touched:
        bad.update(range(k - window, k + window + 1))
    return SealedScan(turn_of_line, typed_lines, bad, by_class)


@dataclass
class SealedResult:
    kept: list                   # {"uuid", "ts", "text", "line"} per kept typed message
    typed_total: int
    excluded: int
    touches_by_class: dict


def nick_messages(path: Path, sealed_config: Path | None = None, window: int = SEALED_WINDOW,
                  is_typed=is_typed) -> SealedResult:
    """Nick's typed messages in one transcript, with every sealed-window turn removed.
    `line` is the record's 1-based line in the file. Text of excluded turns is never read
    into the result."""
    scan = scan_sealed(path, sealed_markers(sealed_config), window, is_typed,
                       guard=sealed_config is None)
    want = {n for n in scan.typed_lines if not scan.sealed(n)}
    kept = []
    with open(path, errors="replace") as f:
        for n, raw in enumerate(f, 1):
            if n not in want:
                continue
            d = json.loads(raw)
            kept.append({"uuid": d.get("uuid"), "ts": d.get("timestamp"),
                         "text": _text_of((d.get("message") or {}).get("content")), "line": n})
    return SealedResult(kept, len(scan.typed_lines), len(scan.typed_lines) - len(kept),
                        scan.touches_by_class)


def sealed_text(text: str, meetings_config: dict | None, sealed_config: Path | None = None,
                config_loader=meeting_vocab.load_sealed_meetings_config) -> bool:
    """Is a free-text row (a Wispr dictation) sealed? A sealed marker, a sealed meeting
    identity, a sealed name or a sealed keyword says yes. With no sealed-meetings config
    there is no way to recognise one, so every row is sealed (fail closed, as
    meeting_vocab.classify_meeting does). Granola meetings use classify_meeting itself."""
    cfg = meetings_config if meetings_config is not None else config_loader()
    if cfg is None:
        return True
    low = (text or "").lower()
    if marker_hits(low, sealed_markers(sealed_config)):
        return True
    if sealed_config is None and check_sealed_read._names_sealed(text or ""):
        return True
    if any(a and a in low for a in cfg.get("attendees", ())):
        return True
    if any(k and k in low for k in cfg.get("keywords", ())):
        return True
    return meeting_vocab._transcript_names_sealed_contact(text or "", cfg)


def load_session(path: Path, start: float, end: float, markers=None,
                 guard: bool | None = None) -> Session | None:
    """One session's activity in [start, end]. Counts include sealed-window turns; their
    TEXT does not: it is None in human, received, sent and hook_errors, and the session's
    sealed_excluded says how many typed turns were dropped."""
    s = Session(session_id=path.stem)
    scan = scan_sealed(path, markers, guard=guard)
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            sealed = scan.sealed(n)
            try:
                d = json.loads(line)
            except ValueError:
                continue
            raw = d.get("timestamp")
            if not raw or d.get("isSidechain"):
                continue
            try:
                t = parse_ts(raw)
            except ValueError:
                continue
            if t < start:
                # Keep the last output before the window: a Nick prompt just inside the
                # window replies to it (Codex r1 F1). It is look-behind, not activity.
                if d.get("type") == "assistant":
                    s.lookbehind = max(s.lookbehind or t, t)
                continue
            if t > end:
                continue
            kind = d.get("type")
            if kind not in ("user", "assistant"):
                # A busy session receives peer messages as queue operations or attachments,
                # not as user lines. They are not activity, so they add no timestamp.
                if kind in ("queue-operation", "attachment"):
                    for text in _strings(d):
                        if "cross-session-message" in text:
                            _add_received(s, t, text, sealed)
                continue
            s.stamps.append(t)
            br = d.get("gitBranch")
            if br:
                s.branches.add(br)
            content = (d.get("message") or {}).get("content")
            if kind == "assistant":
                s.assistant_stamps.append(t)
                for b in content or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        for text in _strings(b.get("input") or {}):
                            s.worktrees.update(WORKTREE_RE.findall(text))
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage":
                        inp = b.get("input") or {}
                        if inp.get("message"):
                            s.sent.append((t, inp.get("to", ""), None if sealed else inp["message"]))
                continue
            origin = (d.get("origin") or {}).get("kind")
            text = _text_of(content)
            if origin == "human" and not d.get("isMeta"):
                s.human.append((t, None if sealed else text))
                s.sealed_excluded += sealed
                continue
            _add_received(s, t, text, sealed)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        et = _text_of([b])
                        if HOOK_RE.search(et) and re.search(r"BLOCKED|hook error", et):
                            s.hook_errors.append((t, None if sealed else et))
    return s if s.stamps else None


def resolve_names(sessions: list[Session]) -> dict:
    received = {}
    for s in sessions:
        for _, from_name, body in s.received:
            if body is not None:
                received.setdefault(_norm(body), from_name)
    names = {}
    for s in sessions:
        found = None
        for _, _, body in s.sent:
            if body is None:
                continue
            hit = received.get(_norm(body))
            if hit:
                found = hit
                break
        names[s.session_id] = found
    return names


def display_name(s: Session, names: dict) -> str:
    return names.get(s.session_id) or f"sid:{s.session_id[:8]}"


def _event(t, session_name, catcher, cls, kind, text):
    flat = " ".join(text.split())
    return {"ts": iso(t), "session": session_name, "catcher": catcher, "catcher_class": cls,
            "kind": kind, "excerpt": flat[:EXCERPT_CHARS],
            "fingerprint": hashlib.sha1(flat.encode()).hexdigest()[:12]}


def failures(s: Session, names: dict, org_names: set) -> list:
    me = display_name(s, names)
    out = []
    # Text is None inside a sealed window: such an event is not a candidate, so no excerpt
    # of it can reach the report (KB-S2: run_metrics no longer emits sealed-window text).
    for t, text in s.hook_errors:
        if text is None:
            continue
        m = HOOK_RE.search(text)
        out.append(_event(t, me, f"hook:{m.group(1)}", "hook", "hook_block", text))
    for t, text in s.human:
        if text is None:
            continue
        if CORRECTION_RE.search(text) or NICK_CATCH_RE.search(text):
            out.append(_event(t, me, "nick", "nick", "nick_correction", text))
    for t, sender, body in s.received:
        if body is None:
            continue
        if CORRECTION_RE.search(body):
            cls = "org" if sender in org_names else "peer"
            out.append(_event(t, me, sender, cls, "peer_correction", body))
    for t, _, body in s.sent:
        if body is None:
            continue
        if SELF_CORRECTION_RE.search(body):
            out.append(_event(t, me, me, "self", "self_correction", body))
    return sorted(out, key=lambda e: e["ts"])


def _git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _ok(repo: Path, *args) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


def _reflog(repo: Path, ref: str) -> list:
    """(sha, epoch, subject) per reflog entry, oldest first; [] when the ref has no reflog."""
    try:
        out = _git(repo, "reflog", "show", "--date=unix", "--format=%H %gd %gs", ref)
    except subprocess.CalledProcessError:
        return []
    rows = []
    for line in out.splitlines():
        # The fixed --format always matches; a line that does not is a git change worth a crash.
        m = re.match(r"([0-9a-f]{40}) \S*@\{(\d+)\} ?(.*)", line)
        rows.append((m.group(1), int(m.group(2)), m.group(3)))
    return list(reversed(rows))


MADE_ON_BRANCH_RE = re.compile(r"^(commit|rebase|cherry-pick)\b")


def branch_timing(repo: Path, branch: str, main_ref: str = "main",
                  land_ref: str | None = "refs/remotes/origin/main") -> dict:
    """First commit, landing time and minutes between them for one local branch.

    Branch commits are the commits its reflog records it making that are still in its
    history; without a reflog, main..tip. Landing is the earliest
    entry of land_ref's reflog that contains the tip (a push); without one, the commit time
    of the first main first-parent commit containing the tip.
    """
    repo = Path(repo)
    rec = {"branch": branch, "commits": 0, "first_commit": None, "landed": None,
           "land_source": None, "time_to_land_min": None, "reset_entries": 0}
    if not _ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"):
        rec["error"] = "branch not found"
        return rec
    tip = _git(repo, "rev-parse", f"refs/heads/{branch}^{{commit}}")
    if not _ok(repo, "rev-parse", "--verify", "--quiet", f"{main_ref}^{{commit}}"):
        rec["error"] = "main ref not found"
        return rec
    main = _git(repo, "rev-parse", f"{main_ref}^{{commit}}")
    blog = _reflog(repo, f"refs/heads/{branch}")
    rec["reset_entries"] = sum(1 for _, _, subject in blog if subject.startswith("reset:"))
    if blog:
        # The commits this branch made (commit, rebase, cherry-pick entries) that are still in
        # its history. A reset onto a later main adds main's commits to base..tip, so the
        # creation point alone over-counts.
        commits = []
        for sha, _, subject in blog:
            if not MADE_ON_BRANCH_RE.match(subject):
                continue
            onto = re.search(r"\bonto ([0-9a-f]{40})\b", subject)
            # A finished rebase is one reflog entry for every commit it replayed (Codex r2 F1).
            made = (_git(repo, "rev-list", "--reverse", f"{onto.group(1)}..{sha}").split()
                    if subject.startswith("rebase") and onto else [sha])
            for c in made:
                if c not in commits and _ok(repo, "merge-base", "--is-ancestor", c, tip):
                    commits.append(c)
    else:
        commits = _git(repo, "rev-list", "--reverse", f"{main}..{tip}").split()
    rec["commits"] = len(commits)
    if not commits:
        return rec
    first_t = min(int(_git(repo, "show", "-s", "--format=%at", c)) for c in commits)
    rec["first_commit"] = iso(first_t)

    land_t = None
    if land_ref:
        for sha, t, _ in _reflog(repo, land_ref):
            if _ok(repo, "merge-base", "--is-ancestor", tip, sha):
                land_t, rec["land_source"] = t, f"reflog:{land_ref}"
                break
    if land_t is None and _ok(repo, "merge-base", "--is-ancestor", tip, main):
        chain = _git(repo, "rev-list", "--first-parent", "--reverse", main).split()
        # main itself is on the chain and contains the tip, so a containing commit always exists.
        landed_sha = next(c for c in chain if _ok(repo, "merge-base", "--is-ancestor", tip, c))
        land_t = int(_git(repo, "show", "-s", "--format=%ct", landed_sha))
        rec["land_source"] = "commit-time"
    if land_t is not None:
        rec["landed"] = iso(land_t)
        rec["time_to_land_min"] = round((land_t - first_t) / 60, 1)
    return rec


def dedupe_failures(events: list) -> list:
    """One event per (catcher, message). A message that is both the sender's self-correction
    and a correction seen by its receivers is kept once, as self."""
    rank = {"self": 0, "hook": 1, "nick": 1, "org": 2, "peer": 2}
    best = {}
    for e in sorted(events, key=lambda e: (rank.get(e["catcher_class"], 9), e["ts"])):
        fp = e.get("fingerprint") or e["excerpt"]
        # A hook block is one event per firing; a message is one event however many sessions saw it.
        key = (e["catcher"], fp, e["ts"], e["session"]) if e["catcher_class"] == "hook" else (e["catcher"], fp)
        best.setdefault(key, e)
    return sorted(best.values(), key=lambda e: e["ts"])


def worktree_branches(repo: Path) -> dict:
    """Map each worktree's directory name to its checked-out branch (detached ones are left out)."""
    out, path = {}, None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path:
            out[Path(path).name] = line[len("branch refs/heads/"):]
    return out


def build_report(transcripts: Path, repo: Path, start: str, end: str, org_names: set,
                 run_id: str, main_ref: str = "main", idle_cap_min: float = 10,
                 nick_cap_min: float = 10, land_ref: str | None = "refs/remotes/origin/main") -> dict:
    t0, t1 = parse_ts(start), parse_ts(end)
    sessions = []
    markers = sealed_markers(DEFAULT_SEALED_CONFIG)
    for p in sorted(Path(transcripts).glob("*.jsonl")):
        if os.path.getmtime(p) < t0:
            # Speed guard, not an equivalence: a transcript copied or restored with an old
            # mtime would be skipped (Codex r3 F2, parked; 0 of 395 live files on 2026-11-23).
            continue
        s = load_session(p, t0, t1, markers, guard=DEFAULT_SEALED_CONFIG is None)
        if s:
            sessions.append(s)
    names = resolve_names(sessions)
    wt_map = worktree_branches(Path(repo))
    fails, srows, branches = [], [], set()
    dispatches = 0
    for s in sessions:
        name = display_name(s, names)
        fails.extend(failures(s, names, org_names))
        found = s.branches | {wt_map[w] for w in s.worktrees if w in wt_map}
        bs = sorted(b for b in found if b not in (main_ref, "HEAD"))
        branches.update(bs)
        if name in org_names:
            dispatches += len(s.sent)
        srows.append({
            "name": name, "session_id": s.session_id, "first": iso(s.first), "last": iso(s.last),
            "wall_min": round((s.last - s.first) / 60, 1),
            "active_min": round(active_minutes(s.stamps, idle_cap_min), 1),
            "nick_prompts": s.nick_prompts, "nick_minutes": round(s.nick_minutes(nick_cap_min), 1),
            "sent_messages": len(s.sent), "branches": bs, "sealed_excluded": s.sealed_excluded,
        })
    fails = dedupe_failures(fails)
    by_class = {}
    for f in fails:
        by_class[f["catcher_class"]] = by_class.get(f["catcher_class"], 0) + 1
    return {
        "run_id": run_id, "window_start": start, "window_end": end, "org": sorted(org_names),
        "sessions_count": len(srows), "sessions": sorted(srows, key=lambda r: r["first"]),
        "org_messages_sent": dispatches,
        "nick_prompts_total": sum(r["nick_prompts"] for r in srows),
        "nick_minutes_total": round(sum(r["nick_minutes"] for r in srows), 1),
        "sealed_excluded_total": sum(r["sealed_excluded"] for r in srows),
        "branches": [branch_timing(Path(repo), b, main_ref, land_ref) for b in sorted(branches)],
        "failure_candidates": sorted(fails, key=lambda e: e["ts"]),
        "candidates_by_catcher_class": by_class,
        "definitions": {"idle_cap_min": idle_cap_min, "nick_cap_min": nick_cap_min,
                        "doc": "see tools/run_metrics.py module docstring, DEFINITIONS"},
    }


def check_out_path(out: Path, repo: Path) -> None:
    out, repo = Path(out).resolve(), Path(repo).resolve()
    try:
        rel = out.relative_to(repo)
    except ValueError:
        return
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", str(rel)],
                             capture_output=True).returncode == 0
    ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(rel)]).returncode == 0
    if tracked or not ignored:
        sys.exit(f"run_metrics: refusing --out {rel}: inside the repo and not gitignored "
                 "(run metrics are personal data; this repo is public). Use output/.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--transcripts", type=Path, default=None,
                    help="transcript folder; default: Claude Code's folder for --repo")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--org", action="append", default=[], help="org session name; repeatable (handovers)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--main-ref", default="main")
    ap.add_argument("--land-ref", default="refs/remotes/origin/main",
                    help="ref whose reflog records pushes; landing = first entry containing the tip")
    ap.add_argument("--idle-cap-min", type=float, default=10)
    ap.add_argument("--nick-cap-min", type=float, default=10)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.out:
        check_out_path(a.out, a.repo)
    transcripts = a.transcripts or transcripts_dir_for(a.repo)
    if not Path(transcripts).is_dir():
        # A missing folder would read as a run with 0 sessions (Grok r2 F1).
        print(f"run_metrics: no transcript folder at {transcripts}; pass --transcripts", file=sys.stderr)
        return 2
    rep = build_report(transcripts, a.repo, a.start, a.end, set(a.org), a.run_id, a.main_ref,
                       a.idle_cap_min, a.nick_cap_min, a.land_ref)
    text = json.dumps(rep, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
