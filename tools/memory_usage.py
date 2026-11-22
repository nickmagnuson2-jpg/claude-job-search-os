"""When did each memory rule file's text last enter a session? (nightly, from transcripts)

Spec: output/memory-hygiene/112226-state-matrix.md, rows "Kind" and "Freshness", and design
v4 section 5 (output/memory-hygiene/112226-learning-loop-design-v4.md). Method and measured
gaps: output/analysis/112226-memory-transcript-analysis.md.

The signal is CONTEXT ENTRY, not use: a transcript shows that a rule's text entered a
session, never that the rule was applied. Four channels count:
- read: a Read tool call on the file whose result is not an error;
- bash_print: a Bash segment whose command word prints a file (cat, head, tail, sed, less,
  more, bat, awk, nl, grep, rg) naming the file, with non-empty output;
- grep_line: a `<stem>.md:<line>:<text>` or `<stem>.md-<line>-<text>` line in any tool output;
- file_attachment: a `file` attachment carrying the file.
Excluded, because no text entered or the reference is ambiguous: writes (Edit, Write,
MultiEdit, redirects, sed -i, cp/mv/rm/touch/tee), names in listings, prompts, system
reminders, hook messages, edit notices and always-loaded files (MEMORY.md / CLAUDE.md), and
Python heredocs (counted as a known gap, `heredoc_refs`).

Each event keeps its session; a session's breadth is its count of distinct files entered.
Breadth is provenance, not deletion: broad (audit) sessions keep their events, but whether
an event re-confirms a rule is decided by learning_state.reconfirms(breadth).

The kind, half-life and freshness state come from tools/learning_state.py (B1). This module
only observes. Output: `memory-usage.jsonl` in the memory folder, one row per rule file plus
one meta row. Frontmatter is never written.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")
RULE_FILE = re.compile(r"^(?:feedback|reference|project|user)_.+\.md$")
NAME = re.compile(r"(?:feedback|reference|project|user)_[A-Za-z0-9_\-]+")
GREP_LINE = re.compile(r"((?:feedback|reference|project|user)_[A-Za-z0-9_\-]+)\.md[:\-](?:\d+[:\-])?(.*)")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\1\b", re.S)
PRINT_VERBS = {"cat", "head", "tail", "sed", "less", "more", "bat", "awk", "nl", "grep", "rg"}
WRITE_VERBS = {"mv", "cp", "rm", "touch", "tee"}
SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|\n]")
REDIRECT_TO_RULE = re.compile(r">\s*\S*(?:feedback|reference|project|user)_")
CONTENT_RECORD_TYPES = ("user", "assistant", "attachment")


@dataclass(frozen=True)
class Event:
    session: str
    stem: str
    channel: str
    day: date


@dataclass
class ScanResult:
    events: list[Event]
    window_start: date | None
    window_end: date | None
    gaps: dict[str, int] = field(default_factory=dict)
    transcripts: int = 0


def memory_stems(memory_dir: Path) -> set[str]:
    """Rule files in the memory folder: feedback_/reference_/project_/user_*.md."""
    return {f[:-3] for f in os.listdir(memory_dir) if RULE_FILE.match(f)}


def _names(text: str, stems: set[str]) -> set[str]:
    return {m.rstrip("-_") for m in NAME.findall(text)} & stems


def _local_day(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
    except ValueError:
        return None


def _memory_stem(path: str, stems: set[str]) -> str | None:
    if "/memory/" not in path or not path.endswith(".md"):
        return None
    stem = os.path.basename(path)[:-3]
    return stem if stem in stems else None


def classify_bash(command: str, stems: set[str]) -> tuple[set[str], set[str], int]:
    """(printed stems, written stems, heredoc references) for one Bash command.

    A stem counts as printed only as an argument of a segment whose command word is a print
    verb. Heredoc bodies are removed first and their rule-name references counted, because a
    Python script that opens a file may or may not print it.
    """
    heredoc_refs = sum(len(_names(m.group(2), stems)) for m in HEREDOC.finditer(command))
    text = HEREDOC.sub(" ", command)
    printed: set[str] = set()
    written: set[str] = set()
    for seg in SEGMENT_SPLIT.split(text):
        toks = seg.strip().split()
        while toks and (re.match(r"^\w+=", toks[0]) or toks[0] in ("cd", "time", "env")):
            toks = toks[2:] if toks[0] == "cd" else toks[1:]
        if not toks:
            continue
        found = _names(seg, stems)
        if not found:
            continue
        verb = toks[0].split("/")[-1]
        if REDIRECT_TO_RULE.search(seg) or (verb == "sed" and "-i" in toks) or verb in WRITE_VERBS:
            written |= found
        elif verb in PRINT_VERBS:
            printed |= found
    return printed, written, heredoc_refs


def _result_text(content) -> str:
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def _grep_lines(text: str, stems: set[str]) -> set[str]:
    hits = set()
    for line in text.splitlines():
        m = GREP_LINE.search(line)
        if m and m.group(1) in stems:
            rest = m.group(2).strip()
            if rest and not rest.isdigit():
                hits.add(m.group(1))
    return hits


def _transcripts(project_dir: Path) -> list[tuple[Path, str]]:
    """(path, session) for every top-level and subagent transcript in the project folder."""
    out = [(Path(p), Path(p).stem) for p in sorted(glob.glob(str(project_dir / "*.jsonl")))]
    for p in sorted(glob.glob(str(project_dir / "*" / "subagents" / "**" / "*.jsonl"), recursive=True)):
        out.append((Path(p), Path(p).relative_to(project_dir).parts[0]))
    return out


def collect(project_dir: Path, stems: set[str], exclude_sessions: Iterable[str] = (),
            sibling_glob: str | None = None) -> ScanResult:
    """Scan every transcript under project_dir for content entries of the given stems."""
    project_dir = Path(project_dir)
    excluded = set(exclude_sessions)
    events: list[Event] = []
    seen: set[tuple] = set()
    first: date | None = None
    last: date | None = None
    heredoc_refs = 0
    files = _transcripts(project_dir)

    def add(session, stem, channel, day, key):
        if day is None or key in seen:
            return
        seen.add(key)
        events.append(Event(session, stem, channel, day))

    for path, session in files:
        if session in excluded:
            continue
        tools: dict[str, tuple[str, dict]] = {}
        with open(path, errors="ignore") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict) or rec.get("type") not in CONTENT_RECORD_TYPES:
                    continue
                day = _local_day(rec.get("timestamp"))
                if day:
                    first = day if first is None or day < first else first
                    last = day if last is None or day > last else last
                kind = rec["type"]
                if kind == "assistant":
                    content = (rec.get("message") or {}).get("content")
                    for b in content if isinstance(content, list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tools[b.get("id")] = (b.get("name"), b.get("input") or {})
                elif kind == "user":
                    content = (rec.get("message") or {}).get("content")
                    for b in content if isinstance(content, list) else []:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        tid = b.get("tool_use_id")
                        name, inp = tools.get(tid, (None, {}))
                        failed = bool(b.get("is_error"))
                        if name in ("Edit", "Write", "MultiEdit") or (name == "Read" and failed):
                            continue
                        text = _result_text(b.get("content"))
                        if name == "Read":
                            stem = _memory_stem(inp.get("file_path", ""), stems)
                            if stem:
                                add(session, stem, "read", day, (stem, "read", tid))
                            continue
                        if name == "Bash" and text.strip():
                            printed, _written, refs = classify_bash(inp.get("command", ""), stems)
                            heredoc_refs += refs
                            if failed:
                                # A failed compound command may still have printed the file:
                                # count it only when the output carries the file's own name: line.
                                printed = {s for s in printed
                                           if re.search(rf"(?m)^\s*name:\s*['\"]?{re.escape(s)}\b", text)}
                            for stem in printed:
                                add(session, stem, "bash_print", day, (stem, "bash_print", tid))
                        for stem in _grep_lines(text, stems):
                            add(session, stem, "grep_line", day, (stem, "grep_line", tid))
                elif kind == "attachment":
                    att = rec.get("attachment") or {}
                    if att.get("type") == "file":
                        stem = _memory_stem(att.get("filename", ""), stems)
                        if stem:
                            add(session, stem, "file_attachment", day,
                                (session, stem, "file_attachment", rec.get("uuid") or line))
    gaps = {"heredoc_refs": heredoc_refs,
            "sibling_transcripts_not_scanned": len(glob.glob(os.path.join(sibling_glob, "*.jsonl")))
            if sibling_glob else 0}
    return ScanResult(events, first, last, gaps, transcripts=len(files))


def session_breadth(events: Iterable[Event]) -> dict[str, int]:
    """Distinct files entered per session."""
    per: dict[str, set[str]] = defaultdict(set)
    for e in events:
        per[e.session].add(e.stem)
    return {s: len(v) for s, v in per.items()}


def use_gaps(days: list[date]) -> list[int]:
    """Days between consecutive distinct use days."""
    ds = sorted(set(days))
    return [(b - a).days for a, b in zip(ds, ds[1:])]


def summarize(events: list[Event], reconfirms: Callable[[int], bool]) -> dict[str, dict]:
    """Per stem: last re-confirming entry, last entry from any session, channels, sessions.

    `reconfirms(breadth)` decides whether an event's session may re-confirm a rule
    (learning_state.reconfirms in production). Events from other sessions are kept and
    counted under `broad_session_entries`, never used as the re-confirmation date.
    """
    breadth = session_breadth(events)
    out: dict[str, dict] = {}
    for e in events:
        row = out.setdefault(e.stem, {"last_context_entry": None, "last_entry_any_session": None,
                                      "channels": Counter(), "_sessions": set(), "use_days": set(),
                                      "broad_session_entries": 0})
        row["channels"][e.channel] += 1
        any_last = row["last_entry_any_session"]
        row["last_entry_any_session"] = e.day if any_last is None or e.day > any_last else any_last
        if reconfirms(breadth[e.session]):
            row["_sessions"].add(e.session)
            row["use_days"].add(e.day)
            last = row["last_context_entry"]
            row["last_context_entry"] = e.day if last is None or e.day > last else last
        else:
            row["broad_session_entries"] += 1
    for row in out.values():
        row["channels"] = dict(row["channels"])
        row["sessions"] = len(row.pop("_sessions"))
        row["use_days"] = sorted(row["use_days"])
    return out
