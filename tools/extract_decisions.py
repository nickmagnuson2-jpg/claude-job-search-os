#!/usr/bin/env python3
"""Extract an operator's verbatim decisions from a frame, a process log and session
transcripts, with the query that surfaced each one.

A LEDGER, NOT AN ARGUMENT. This does no interpretation, no grouping into "what worked", no
assessment. It locates decision records that ALREADY EXIST and reprints them with their
provenance, so the operator reacts to their own words rather than re-deriving them. The
moment it starts summarising, the thing it produces stops being evidence.

EVERY ROW CARRIES ITS QUERY. A ledger whose rows do not say how they were found cannot have a
denominator, and any ratio computed downstream is then a number with no scope. That is a
standing rule in this repo and it is enforced here by the Entry shape: `query` is a field, not
a habit.

THREE SOURCES, each answering something the others cannot:

  frame       the governed decision record: compression_ledger, declines, exclusions,
              unknowns. Schema-driven, so these are found by structure, not by search.
  processlog  a `| D## |` decision table, plus prose passages naming the operator.
  transcript  ~/.claude/projects/<slug>/*.jsonl -- the operator's own typed or dictated
              words, which is the only source that is not already a summary.

WHAT IS POLICY AND STAYS WITH THE CALLER: which engagement, the date window, the needle that
identifies a relevant session, and the operator's name. All are arguments; none are baked in.

THE WINDOW IS ON FILE MTIME AND THAT IS A KNOWN WEAKNESS. A session file touched after the
engagement closed still falls in range, and one edited later moves. It is a cheap filter over
a large directory, not an authority on when something was said; the `when` on each transcript
row comes from the record's own timestamp where it has one.

Promoted 2026-09-21 from an engagement's `scripts/extract_decisions.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from json_leaves import leaves, strings

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML required: python3 -m pip install pyyaml")


# A turn shorter than this is an acknowledgement, not a decision. Machinery is excluded by
# prefix rather than by length, because some machinery is long and some decisions are short.
MIN_UTTERANCE_CHARS = 25

NOISE_PREFIXES = (
    "<system-reminder>",
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "[Request interrupted",
    "Caveat: The messages below",
    "<user-prompt-submit-hook>",
    "<task-notification>",
)

D_ROW = re.compile(r"^\|\s*(D\d+)\s*\|(.*)$")


@dataclass
class Entry:
    """One extracted decision record. `text` is verbatim; nothing is paraphrased."""

    eid: str
    source: str            # frame | processlog | transcript
    locator: str           # where in that source
    text: str
    when: str = ""         # date or frame version, when the source carries one
    governs: str = ""      # the artifact the decision governed, when stated
    overrules: str = ""    # what it overruled, when stated
    query: str = ""        # the query that surfaced this row
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Source 1: the frame
# --------------------------------------------------------------------------

# Sections whose every entry IS a decision, and are therefore read by SCHEMA rather than
# searched for by name. Keeping them out of the name-search below stops each one being
# reported twice.
STRUCTURED_SECTIONS = ("compression_ledger", "declines", "exclusions", "unknowns")


def extract_frame(frame_path: Path, operator: str) -> list[Entry]:
    data = yaml.safe_load(frame_path.read_text(encoding="utf-8"))
    out: list[Entry] = []
    ver = data.get("version", "?")

    for i, row in enumerate(data.get("compression_ledger") or []):
        if not isinstance(row, dict):
            continue
        out.append(Entry(
            eid=f"CL{i + 1}", source="frame", locator=f"compression_ledger[{i}]",
            text=f"CUT: {row.get('cut', '')}\nREASON: {row.get('reason', '')}",
            when=f"frame v{row.get('at_version', '?')}", governs="the deliverable",
            query="frame compression_ledger, all entries", tags=["cut"]))

    for i, row in enumerate(data.get("declines") or []):
        if not isinstance(row, dict):
            continue
        body = "\n".join(f"{k.upper()}: {v}" for k, v in row.items()
                         if isinstance(v, str) and k not in {"date", "decided_by"})
        out.append(Entry(
            eid=f"DEC{i + 1}", source="frame", locator=f"declines[{i}]", text=body,
            when=str(row.get("date", "")), governs="the deliverable",
            overrules=str(row.get("raised_by", "")),
            query="frame declines, all entries", tags=["decline", "overrule"]))

    for i, row in enumerate(data.get("exclusions") or []):
        if not isinstance(row, dict):
            continue
        out.append(Entry(
            eid=f"EX{i + 1}", source="frame", locator=f"exclusions[{i}]",
            text=f"EXCLUDED: {row.get('element', '')}\nREASON: {row.get('reason', '')}",
            when=f"frame v{ver}", governs="scope",
            query="frame exclusions, all entries", tags=["exclusion"]))

    for key, row in (data.get("unknowns") or {}).items():
        if not isinstance(row, dict):
            continue
        out.append(Entry(
            eid=f"UNK-{key}", source="frame", locator=f"unknowns.{key}",
            text=(f"UNKNOWN: {row.get('text', '')}"
                  f"\nDISPOSITION: {row.get('disposition', '')}"
                  f"\nSENSITIVITY: {row.get('sensitivity', '')}"),
            when=f"frame v{ver}", governs="scope",
            query="frame unknowns, all entries", tags=["unknown"]))

    # Everything else naming the operator. These are the fact-level and design decision
    # contexts; they live in free text, so they are surfaced by NAME rather than by schema,
    # and the query says so.
    for path, text in leaves(data, keep=strings):
        if operator not in text:
            continue
        if any(path.startswith(p) for p in STRUCTURED_SECTIONS):
            continue
        out.append(Entry(
            eid=f"FR-{path}", source="frame", locator=path, text=text,
            when=f"frame v{ver}", governs=path.split(".")[0],
            query=f'frame, string leaves containing "{operator}"', tags=["named"]))
    return out


# --------------------------------------------------------------------------
# Source 2: the process log
# --------------------------------------------------------------------------


def extract_process_log(path: Path, operator: str) -> list[Entry]:
    out: list[Entry] = []
    if not path.exists():
        return out
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for n, line in enumerate(lines, 1):
        m = D_ROW.match(line)
        if m:
            did, rest = m.group(1), m.group(2)
            cells = [c.strip() for c in rest.split("|")]
            decision = cells[0] if cells else ""
            because = " | ".join(c for c in cells[1:] if c)
            out.append(Entry(
                eid=did, source="processlog", locator=f"{path.name}:{n}",
                text=f"{decision}\n{because}".strip(), governs="the deliverable",
                query="process log, rows matching ^| D## |",
                tags=["D-row"] + (["overrule"] if "verrule" in because else [])))
            continue

        # Quoted operator passages outside the decision table.
        if f"{operator}:" in line or f"{operator}, " in line:
            stripped = line.strip().lstrip("|").strip()
            if len(stripped) >= MIN_UTTERANCE_CHARS:
                out.append(Entry(
                    eid=f"PL{n}", source="processlog", locator=f"{path.name}:{n}",
                    text=stripped,
                    query=f'process log, lines containing "{operator}:"', tags=["quote"]))
    return out


# --------------------------------------------------------------------------
# Source 3: the session transcripts
# --------------------------------------------------------------------------


def message_text(msg) -> str:
    """A turn's text content, ignoring tool results and images."""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list):
        return "\n".join(b.get("text", "") for b in msg
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_operator_turn(text: str) -> bool:
    """A real utterance, not harness machinery and not an acknowledgement."""
    t = text.strip()
    if len(t) < MIN_UTTERANCE_CHARS:
        return False
    return not any(t.startswith(p) for p in NOISE_PREFIXES)


def extract_transcripts(tdir: Path, needle: str, start: date, end: date) -> list[Entry]:
    out: list[Entry] = []
    if not tdir.exists():
        return out
    seen: set[str] = set()

    files = []
    for f in sorted(tdir.glob("*.jsonl")):
        mtime = datetime.fromtimestamp(f.stat().st_mtime).date()
        if start <= mtime <= end:
            files.append((mtime, f))

    for mtime, f in sorted(files):
        raw = f.read_text(encoding="utf-8", errors="replace")
        if needle.lower() not in raw.lower():
            continue
        for ln, line in enumerate(raw.splitlines(), 1):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user":
                continue
            msg = rec.get("message") or {}
            if msg.get("role") != "user":
                continue
            text = message_text(msg.get("content")).strip()
            if not is_operator_turn(text):
                continue
            # Deduped on a PREFIX, not the whole turn: the same instruction is often
            # re-sent with a trailing edit, and both copies are one decision.
            key = text[:300]
            if key in seen:
                continue
            seen.add(key)
            out.append(Entry(
                eid=f"T-{f.stem[:8]}-{ln}", source="transcript",
                locator=f"{f.name}:{ln}", text=text,
                when=rec.get("timestamp", "")[:10] or str(mtime),
                query=(f'transcripts with mtime in [{start}..{end}] containing "{needle}", '
                       f"type=user role=user, non-machinery, >={MIN_UTTERANCE_CHARS} chars, "
                       "deduped on first 300 chars"),
                tags=["utterance"]))
    return out


# --------------------------------------------------------------------------


SOURCE_TITLES = {
    "frame": "The frame (governed decision record)",
    "processlog": "The process log (D## decision table and quoted passages)",
    "transcript": "The session transcripts (the operator's own words)",
}


def render_markdown(entries: list[Entry], counts: dict, title: str) -> str:
    lines = [f"# {title}", "",
             f"Generated {date.today().isoformat()} by `tools/extract_decisions.py`.", "",
             "**This is a ledger, not an argument.** Nothing here is assessed, grouped into "
             '"what worked", or interpreted. Every row is verbatim from a source that '
             "already existed, with the query that surfaced it.", "",
             "## Coverage", "", "| Source | Rows | Query |", "|---|---:|---|"]
    for src, (n, q) in counts.items():
        lines.append(f"| {src} | {n} | {q} |")
    lines.append("")

    by_source: dict[str, list[Entry]] = {}
    for e in entries:
        by_source.setdefault(e.source, []).append(e)

    for src in ("frame", "processlog", "transcript"):
        rows = by_source.get(src, [])
        if not rows:
            continue
        lines += [f"## {SOURCE_TITLES[src]} -- {len(rows)} rows", ""]
        for e in rows:
            meta = " · ".join(x for x in (e.when, e.locator) if x)
            lines.append(f"### {e.eid}")
            if meta:
                lines.append(f"`{meta}`")
            lines += ["", "> " + e.text.replace("\n", "\n> "), ""]
            if e.overrules and len(e.overrules) < 400:
                lines += [f"**Overruled:** {e.overrules}", ""]
    return "\n".join(lines) + "\n"


def _day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", required=True, type=Path, help="the engagement's frame.yaml")
    ap.add_argument("--process-log", type=Path, help="markdown log carrying a | D## | table")
    ap.add_argument("--transcript-dir", type=Path,
                    help="~/.claude/projects/<slug>/ holding session .jsonl files")
    # NO DEFAULT. A generic tool that defaults to one person's name will, when the caller
    # forgets the flag, return a NON-EMPTY ledger that is silently missing every record
    # naming the actual operator -- which reads as a successful run. Found by cross-model
    # review 2026-09-21 (117.F4). Baking a real name into a public tool's default is also
    # how a name reaches a public repo without anyone writing it there.
    ap.add_argument("--operator", required=True,
                    help="the name whose words and decisions are being collected")
    ap.add_argument("--needle", required=True,
                    help="a session mentioning this is part of the engagement")
    ap.add_argument("--window-start", type=_day, help="YYYY-MM-DD, on file mtime")
    ap.add_argument("--window-end", type=_day, help="YYYY-MM-DD, on file mtime")
    ap.add_argument("--title", default="Decision ledger")
    ap.add_argument("--out", type=Path, help="markdown output path")
    ap.add_argument("--json", dest="json_out", type=Path, help="JSON output path")
    a = ap.parse_args(argv)

    if a.transcript_dir and not (a.window_start and a.window_end):
        ap.error("--transcript-dir needs --window-start and --window-end: an unbounded "
                 "sweep of every session collects other engagements' decisions")
    if a.window_start and a.window_end and a.window_start > a.window_end:
        ap.error(f"empty window: {a.window_start} is after {a.window_end}")

    entries: list[Entry] = []
    counts: dict[str, tuple[int, str]] = {}

    fr = extract_frame(a.frame, a.operator)
    entries += fr
    counts["frame"] = (len(fr), "compression_ledger + declines + exclusions + unknowns + "
                                f'leaves naming "{a.operator}"')

    if a.process_log:
        pl = extract_process_log(a.process_log, a.operator)
        entries += pl
        counts["processlog"] = (len(pl), f'rows ^| D## | plus lines containing "{a.operator}:"')

    if a.transcript_dir:
        tr = extract_transcripts(a.transcript_dir, a.needle, a.window_start, a.window_end)
        entries += tr
        counts["transcript"] = (len(tr), f"user turns, mtime {a.window_start}.."
                                         f'{a.window_end}, file mentions "{a.needle}"')

    if a.out:
        a.out.write_text(render_markdown(entries, counts, a.title), encoding="utf-8")
    if a.json_out:
        a.json_out.write_text(
            json.dumps([asdict(e) for e in entries], indent=1, ensure_ascii=False),
            encoding="utf-8")

    for src, (n, q) in counts.items():
        print(f"{src:12} {n:5}  ({q})")
    print(f"{'TOTAL':12} {len(entries):5}")
    for p in (a.out, a.json_out):
        if p:
            print(f"wrote {p}")
    # AN EMPTY LEDGER IS AN ERROR, NOT A RESULT. Zero rows means the needle missed, the
    # window was wrong, or the paths were, and a closeout that reads "no decisions found"
    # as "no decisions made" is the failure this whole family is named for.
    if not entries:
        print("NO ROWS EXTRACTED: check --needle, the window and the paths. This is not "
              "evidence that no decisions were made.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
