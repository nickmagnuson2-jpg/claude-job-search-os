"""When did each memory rule file's text last enter a session? (nightly, from transcripts)

Spec: output/memory-hygiene/112226-state-matrix.md, rows "Kind" and "Freshness", and design
v4 section 5 (output/memory-hygiene/112226-learning-loop-design-v4.md). Method and measured
gaps: output/analysis/112226-memory-transcript-analysis.md.

The signal is CONTEXT ENTRY, not use: a transcript shows that a rule's text entered a
session, never that the rule was applied. Four channels count:
- read: a Read tool call on the file whose result is not an error;
- bash_print: a Bash segment whose command word prints a file (cat, head, tail, sed, less,
  more, bat, awk, nl, grep, rg) naming the file, with non-empty output;
- grep_line: a grep output line in a non-Read tool result, `<path>/<stem>.md:<line>:<text>`
  (or `-` separators), whose path opens the line and leads to a memory file;
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
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from count_report import report

LOCAL_TZ = ZoneInfo("America/Los_Angeles")
RULE_FILE = re.compile(r"^(?:feedback|reference|project|user)_.+\.md$")
NAME = re.compile(r"(?:feedback|reference|project|user)_[A-Za-z0-9_\-]+")
# Grep output: the matched file's path opens the line (Grok r1 F1).
GREP_LINE = re.compile(r"^\s*([^\s:]*?)((?:feedback|reference|project|user)_[A-Za-z0-9_\-]+)\.md[:\-](\d+[:\-])?(.*)")
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


def _memory_prefix(prefix: str) -> bool:
    """A path prefix that leads to a memory file: none (a bare name, e.g. after a cd), ./,
    a memory/ folder (its backup copies included), or a shell variable."""
    prefix = prefix.replace('"', "").replace("'", "")
    if prefix in ("", "./"):
        return True
    # A folder path ends at the rule name: anything after its last "/" would be another
    # file's name, e.g. `memory/notes.md:12:` before a cited rule (Codex r2 F1).
    return prefix.endswith("/") and ("memory/" in prefix or "$" in prefix)


def _names_in_memory(segment: str, stems: set[str]) -> set[str]:
    """Stems named in a segment through a memory path (see _memory_prefix). A path through
    any other folder is a different file with the same name (Codex r1 F2)."""
    found = set()
    for stem in _names(segment, stems):
        for m in re.finditer(rf"(\S*?){re.escape(stem)}", segment):
            if _memory_prefix(m.group(1)):
                found.add(stem)
                break
    return found


SEARCH_VERBS = {"grep", "rg"}
# Each verb's option grammar (Codex r3 F2: one shared table shifted operands, and grep -r is
# recursive while rg -r takes a replacement). Any option not listed here fails safe: the
# segment counts nothing, which can only lower the count, never freshen a rule wrongly.
_GRAMMAR = {
    "grep": {
        "no_content_short": set("lLcqo"),
        "value_short": set("efABCmdD"),
        "flag_short": set("abhHiInrRsuUvVwxyzZEFGPT0123456789"),
        "no_content_long": {"--files-with-matches", "--files-without-match", "--count", "--quiet",
                            "--silent", "--only-matching"},
        "value_long": {"--regexp", "--file", "--after-context", "--before-context", "--context",
                       "--max-count", "--directories", "--devices", "--binary-files", "--exclude",
                       "--exclude-from", "--exclude-dir", "--include", "--label", "--group-separator"},
        "flag_long": {"--recursive", "--dereference-recursive", "--line-number", "--ignore-case",
                      "--no-ignore-case", "--no-filename", "--with-filename", "--color", "--colour",
                      "--fixed-strings", "--extended-regexp", "--basic-regexp", "--perl-regexp",
                      "--word-regexp", "--line-regexp", "--invert-match", "--null", "--null-data",
                      "--no-messages", "--text", "--byte-offset", "--line-buffered", "--initial-tab",
                      "--no-group-separator"},
    },
    "rg": {
        "no_content_short": set("lcqo"),
        "value_short": set("efABCmgtTEjrMd"),
        "flag_short": set("aFHhiIInNpsSuUvVwxzPb0123456789"),
        "no_content_long": {"--files-with-matches", "--files-without-match", "--count",
                            "--count-matches", "--quiet", "--files", "--only-matching"},
        "value_long": {"--regexp", "--file", "--after-context", "--before-context", "--context",
                       "--max-count", "--glob", "--iglob", "--type", "--type-not", "--type-add",
                       "--type-clear", "--encoding", "--threads", "--replace", "--engine", "--sort",
                       "--sortr", "--max-depth", "--max-filesize", "--max-columns", "--path-separator",
                       "--pre", "--pre-glob", "--colors", "--context-separator",
                       "--field-match-separator", "--field-context-separator", "--ignore-file",
                       "--dfa-size-limit", "--regex-size-limit", "--hyperlink-format", "--color"},
        "flag_long": {"--line-number", "--no-line-number", "--ignore-case", "--smart-case",
                      "--case-sensitive", "--no-filename", "--with-filename", "--no-heading",
                      "--heading", "--hidden", "--no-ignore", "--follow", "--fixed-strings",
                      "--word-regexp", "--line-regexp", "--invert-match", "--multiline", "--pcre2",
                      "--null", "--null-data", "--text", "--binary", "--vimgrep", "--json",
                      "--no-messages", "--trim", "--column", "--byte-offset", "--search-zip",
                      "--crlf", "--unrestricted"},
    },
}
PATTERN_SHORT = set("ef")
PATTERN_LONG = {"--regexp", "--file"}


def _search_file_args(segment: str) -> list[str] | None:
    """The file arguments of a grep/rg segment, or None when it prints no file text.

    The pattern is a search for text, not a file that was read (Grok r2 F1): it is the
    first operand unless -e/-f gave it. -l, -c, -q, -o and their long forms print names,
    counts or matches only. An option outside the verb's grammar returns None (fail safe)."""
    try:
        toks = shlex.split(segment)
    except ValueError:
        toks = segment.split()
    if not toks:
        return None
    g = _GRAMMAR.get(toks[0].split("/")[-1])
    if g is None:
        return None
    args, explicit_pattern, i = [], False, 1
    while i < len(toks):
        t = toks[i]
        if t == "--":
            args += toks[i + 1:]
            break
        if t.startswith("--"):
            name, has_value = t.split("=", 1)[0], "=" in t
            if name in g["no_content_long"]:
                return None
            explicit_pattern |= name in PATTERN_LONG
            if name in g["value_long"]:
                i += 0 if has_value else 1
            elif name not in g["flag_long"] and not has_value:
                return None
        elif t.startswith("-") and len(t) > 1:
            for j, ch in enumerate(t[1:], 1):
                if ch in g["no_content_short"]:
                    return None
                if ch in g["value_short"]:
                    explicit_pattern |= ch in PATTERN_SHORT
                    if j == len(t) - 1:
                        i += 1
                    break
                if ch not in g["flag_short"]:
                    return None
        else:
            args.append(t)
        i += 1
    return args if explicit_pattern else args[1:]


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
            if toks[0] == "env":   # env's own options come before the command (Codex r3 F3)
                toks = toks[1:]
                while toks and toks[0].startswith("-"):
                    toks = toks[2:] if toks[0] in ("-u", "--unset", "-C", "--chdir") else toks[1:]
                continue
            toks = toks[2:] if toks[0] == "cd" else toks[1:]
        if not toks:
            continue
        verb = toks[0].split("/")[-1]
        if verb in SEARCH_VERBS:
            files = _search_file_args(" ".join(toks))
            found = _names_in_memory(" ".join(files), stems) if files else set()
        else:
            found = _names_in_memory(seg, stems)
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
        if m and m.group(2) in stems and _memory_prefix(m.group(1)):
            rest = m.group(4).strip()
            # Without a `<n>:` line number, text starting with a digit is a citation
            # (`x.md:89,269`), not grep output.
            if rest and not (m.group(3) is None and rest[0].isdigit()):
                hits.add(m.group(2))
    return hits


def _transcripts(project_dir: Path) -> list[tuple[Path, str]]:
    """(path, session) for every top-level and subagent transcript in the project folder."""
    out = [(Path(p), Path(p).stem) for p in sorted(glob.glob(str(project_dir / "*.jsonl")))]
    for p in sorted(glob.glob(str(project_dir / "*" / "subagents" / "**" / "*.jsonl"), recursive=True)):
        out.append((Path(p), Path(p).relative_to(project_dir).parts[0]))
    return out


def sibling_dirs(project_dir: Path) -> list[Path]:
    """Other project folders whose name contains this one's (worktrees, scratchpads)."""
    project_dir = Path(project_dir)
    key = project_dir.name.strip("-")
    return sorted(d for d in project_dir.parent.iterdir()
                  if d.is_dir() and d != project_dir and key in d.name)


def collect(project_dir: Path, stems: set[str], exclude_sessions: Iterable[str] = (),
            sibling_dirs: Iterable[Path] = ()) -> ScanResult:
    """Scan every transcript under project_dir for content entries of the given stems."""
    project_dir = Path(project_dir)
    excluded = set(exclude_sessions)
    events: list[Event] = []
    seen: set[tuple] = set()
    first: date | None = None
    last: date | None = None
    heredoc_refs = 0
    files = _transcripts(project_dir)
    # A resumed transcript copies earlier tool calls into a new file. Files that share a
    # tool call are one logical session, so breadth is counted over the join (Codex r1 F1).
    parent: dict[str, str] = {}
    tool_owner: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

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
                            tid = b.get("id")
                            tools[tid] = (b.get("name"), b.get("input") or {})
                            if tid in tool_owner:
                                parent[root(session)] = root(tool_owner[tid])
                            elif tid:
                                tool_owner[tid] = session
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
                                if text.strip():   # an empty result put no text in context
                                    add(session, stem, "read", day, (stem, "read", tid))
                            # Another file's text is not grep output: on real data its
                            # `x.md:<n>` lines were citations, not rule text.
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
                else:  # attachment
                    att = rec.get("attachment") or {}
                    if att.get("type") == "file":
                        stem = _memory_stem(att.get("filename", ""), stems)
                        if stem:
                            add(session, stem, "file_attachment", day,
                                (session, stem, "file_attachment", rec.get("uuid") or line))
    events = [Event(root(e.session), e.stem, e.channel, e.day) for e in events]
    gaps = {"heredoc_refs": heredoc_refs,
            "sibling_transcripts_not_scanned": sum(len(list(Path(d).rglob("*.jsonl"))) for d in sibling_dirs)}
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


# ---------------------------------------------------------------------------- output
# Every rule below (kind, half-life, breadth, state) is learning_state's; this layer only
# applies it to the observed entries and writes the result.

LIVE_MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-mag-Documents-Obsidian-30-projects-job-search" / "memory"
USAGE_FILE = "memory-usage.jsonl"
CLASS_MAP_FILE = "memory-classes.jsonl"
KIND_ORDER = ("MACHINERY", "FACT", "LESSON")
NO_KIND = "no kind"
# Gaps between uses measure reuse, not how long a rule stays valid, and the window cuts off
# long gaps. The distribution is recorded; no half-life is proposed until Nick rules on an
# estimator (org 68, 2026-11-22).
NO_PROPOSAL = "no proposal: estimator under review"
GAPS_NOTE = ("Observation has known gaps (Python heredocs that open a file, sibling-folder "
             "transcripts), so 'unverified' means not re-confirmed and never claims a rule is unused.")
KIND_RELIABILITY_NOTE = ("Two independent full reads agree on a file's kind about 70% of the time "
                         "(21/31 MACHINERY re-read overlap; 26/40 against 6/12 before the merge).")


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def load_class_rows(path: Path) -> list[dict] | None:
    """memory-classes.jsonl rows, or None when the file does not exist."""
    if not Path(path).exists():
        return None
    rows = []
    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("file"):
            rows.append(row)
    return rows


def build_rows(summary: dict[str, dict], stems: Iterable[str], class_rows: list[dict] | None,
               window_start: date, window_end: date) -> list[dict]:
    """One row per rule file, entered or not, with learning_state's kind and state."""
    import learning_state as ls
    by_file = {r["file"]: r for r in class_rows or []}
    rows = []
    for stem in sorted(stems):
        cls = by_file.get(stem)
        kind = ls.kind_of(cls)
        s = summary.get(stem) or {}
        last = s.get("last_context_entry")
        rows.append({
            "file": stem,
            "kind": kind,
            "kind_source": cls.get("source") if cls else None,
            "state": ls.freshness_state(kind, last, window_start, window_end),
            "last_context_entry": _iso(last),
            "last_entry_any_session": _iso(s.get("last_entry_any_session")),
            "channels": s.get("channels", {}),
            "sessions": s.get("sessions", 0),
            "broad_session_entries": s.get("broad_session_entries", 0),
        })
    return rows


def _p75(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[-(-3 * len(ordered) // 4) - 1]


def build_meta(rows: Iterable[dict], scan: ScanResult, summary: dict[str, dict],
               previous_counts: dict | None, class_map: str | None = None) -> dict:
    """The meta row: counts by kind and state, the window, the rules used, known gaps, and
    each kind's distribution of gaps between uses with its sample size (no proposal)."""
    import learning_state as ls
    rows = list(rows)
    counts: dict[str, Counter] = defaultdict(Counter)
    gaps: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        counts[r["kind"] or NO_KIND][r["state"]] += 1
        gaps[r["kind"]] += use_gaps(list((summary.get(r["file"]) or {}).get("use_days", [])))
    window_days = (scan.window_end - scan.window_start).days
    by_kind = {}
    for kind, half_life in ls.HALF_LIFE_DAYS.items():
        g = gaps.get(kind, [])
        by_kind[kind] = {"n": len(g), "p75": _p75(g), "half_life_days": half_life,
                         "proposal": NO_PROPOSAL}
    return {
        "type": "meta",
        "generated_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "window_start": _iso(scan.window_start),
        "window_end": _iso(scan.window_end),
        "window_days": window_days,
        "transcripts": scan.transcripts,
        "files": len(rows),
        "audit_breadth": ls.AUDIT_BREADTH,
        "half_life_days": dict(ls.HALF_LIFE_DAYS),
        "class_map": class_map,
        "kind_reliability": KIND_RELIABILITY_NOTE,
        "counts": {k: dict(v) for k, v in counts.items()},
        "previous_counts": previous_counts,
        "gaps_by_kind": by_kind,
        "known_gaps": {**scan.gaps, "note": GAPS_NOTE},
    }


def _is_live(path: Path) -> bool:
    return Path(path).resolve().is_relative_to(LIVE_MEMORY_DIR.resolve())


def write_usage(path: Path, rows: Iterable[dict], meta: dict) -> None:
    """Write the meta row then one row per file, atomically. A test never writes live data."""
    path = Path(path)
    if os.environ.get("PYTEST_CURRENT_TEST") and _is_live(path):
        raise RuntimeError(f"refusing to write {path}: the live memory folder, from a test")
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(json.dumps(meta) + "\n")
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, path)


def _read_meta(path: Path) -> dict | None:
    try:
        with open(path) as fh:
            meta = json.loads(fh.readline())
    except (OSError, json.JSONDecodeError):
        return None
    return meta if isinstance(meta, dict) and meta.get("type") == "meta" else None


def read_previous_counts(path: Path) -> dict | None:
    meta = _read_meta(path)
    return meta.get("counts") if meta else None


def standup_line(path: Path) -> str | None:
    """One /standup line: current / unverified / unknown by kind, and the change since the
    previous run. None when the file does not exist (the job has not run)."""
    meta = _read_meta(path)
    if meta is None:
        return None
    counts = meta.get("counts") or {}
    parts = []
    for kind in KIND_ORDER:
        c = counts.get(kind, {})
        parts.append(f"{kind} {c.get('current', 0)} current / {c.get('unverified', 0)} unverified / "
                     f"{c.get('unknown_not_observed', 0)} unknown")
    parts.append(f"{NO_KIND} {sum(counts.get(NO_KIND, {}).values())}")
    prev = meta.get("previous_counts")
    if prev is None:
        change = "This is the first run"
    else:
        deltas = []
        for kind in KIND_ORDER + (NO_KIND,):
            now_c, old_c = counts.get(kind, {}), prev.get(kind, {})
            d = [f"{st} {now_c.get(st, 0) - old_c.get(st, 0):+d}"
                 for st in ("current", "unverified", "unknown_not_observed", "unknown_no_kind")
                 if now_c.get(st, 0) != old_c.get(st, 0)]
            if d:
                deltas.append(f"{kind} " + ", ".join(d))
        change = ("Change since last run: " + "; ".join(deltas)) if deltas else "No change since last run"
    return (f"Memory freshness (context entry, {meta.get('window_start')} to {meta.get('window_end')}): "
            + " · ".join(parts) + f" (unverified = not re-confirmed, not unused). {change}.")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    import learning_state as ls
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project-dir", type=Path, default=LIVE_MEMORY_DIR.parent,
                    help="Claude Code project folder holding the session transcripts")
    ap.add_argument("--memory-dir", type=Path, help="default: <project-dir>/memory")
    ap.add_argument("--class-map", type=Path, help=f"default: <memory-dir>/{CLASS_MAP_FILE}")
    ap.add_argument("--out", type=Path, help=f"default: <memory-dir>/{USAGE_FILE}")
    ap.add_argument("--exclude-session", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true", help="print the meta row; write nothing")
    ap.add_argument("--standup", action="store_true", help="print the /standup line from the last run")
    a = ap.parse_args(argv)
    memory_dir = a.memory_dir or a.project_dir / "memory"
    out = a.out or memory_dir / USAGE_FILE
    if a.standup:
        line = standup_line(out)
        print(line or f"Memory freshness: no run yet ({out} not found).")
        return 0
    class_map = a.class_map or memory_dir / CLASS_MAP_FILE
    stems = memory_stems(memory_dir)
    scan = collect(a.project_dir, stems, a.exclude_session,
                   sibling_dirs=sibling_dirs(a.project_dir))
    if scan.transcripts == 0 or scan.window_start is None:
        print(f"memory_usage: no transcripts with timestamps under {a.project_dir}", file=sys.stderr)
        return 2
    summary = summarize(scan.events, ls.reconfirms)
    class_rows = load_class_rows(class_map)
    rows = build_rows(summary, stems, class_rows, scan.window_start, scan.window_end)
    meta = build_meta(rows, scan, summary, read_previous_counts(out),
                      class_map=str(class_map) if class_rows is not None else None)
    if a.dry_run:
        print(json.dumps(meta, indent=1))
        return 0
    write_usage(out, rows, meta)
    entered = sum(1 for r in rows if r["last_context_entry"])
    report(f"memory rule files in {memory_dir}", population=len(rows), included=entered,
           excluded={"no re-confirming context entry in the window": len(rows) - entered})
    print(standup_line(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
