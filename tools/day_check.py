#!/usr/bin/env python3
"""day_check.py - "where am I today": the day's activity, counted from the logs. Read-only.

Why it exists. /checkout is end-of-day only and writes the daily log. Nick wanted a midday
read he can take any time (2026-10-28: "I want to be able to, during the midday, figure out
where I'm at"). This script is that read. The /check skill prints it in chat, and the
morning page renders the same JSON, so the two can never disagree. It never writes a file.

Six counts, each for the target date and for the week so far (Monday to the target date):

  sends           people written to: outreach-log rows whose status says the message went
                  out (the shared conversations_metric.SENT_STATES), plus networking.md
                  interactions that tools/reply_classify.py calls a send. A person counts
                  once per day across both logs ("First" and "First Last" are one person).
  replies         networking.md interactions that tools/reply_classify.py calls a reply
                  (the one rule networking_write.py also uses; what counts is written in
                  that module's docstring).
  calls           networking.md interactions of a live type (conversations_metric.LIVE:
                  call, video, in-person, interview, and so on).
  applications    pipeline rows whose text says "Applied <date>" (or "applied on <date>").
                  Stage alone is not counted: a follow-up also moves Date Updated on an
                  Applied row, which would count an old application again. Rows moved to
                  Stage "Applied" that day with no applied date anywhere (what
                  `pipe_write.py update <co> Applied` writes) are reported apart as
                  applications_unconfirmed, for Nick to confirm.
  pipeline_moves  pipeline rows whose Date Updated is the date.
  todos_closed    job-todos.md Completed rows for the date (todo_read.completed_on, withdrawn
                  rows excluded).

Targets come from the Success Metrics table in data/goals.md. Only weekly targets are
written there today, so each target is shown with its period, against the week so far.
No per-day target is invented.

A source that cannot be read reports null for its counts, never 0, and names the error. A
pipeline whose header drifted from the expected columns counts as unreadable.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/day_check.py [--repo-root .] [--target-date YYYY-MM-DD]
      [--json]

Exit codes: 0 every source read; 1 at least one source unreadable (counts from the rest are
still printed); 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Read-only means no __pycache__ either: the sibling imports below would write bytecode.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conversations_metric import LIVE, SENT_STATES  # noqa: E402
# The one "did they reply" rule, shared with networking_write.py (no local copy here).
from reply_classify import classify as classify_summary  # noqa: E402
from schema_guard import SchemaDriftError, assert_schema, find_header_line  # noqa: E402
from outreach_read import parse_rows as parse_outreach_rows  # noqa: E402
import todo_read  # noqa: E402

COUNTS = ("sends", "replies", "calls", "applications", "pipeline_moves", "todos_closed")
# Shown beside applications, never added to it (see unconfirmed_applied).
EXTRA = ("applications_unconfirmed",)

CONTACT_RE = re.compile(r"^###\s+(.+?)(?:\s+[—–-]\s+(.+))?\s*$")
INTERACTION_RE = re.compile(r"^####\s+(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*(.*)$")
PIPELINE_COLUMNS = ["Company", "Role", "Stage", "Date Updated", "Next Action", "CV Used",
                    "Notes", "URL"]

TARGET_ROWS = {
    # goals.md Success Metrics label -> (count it is compared with, period)
    "applications / week": ("applications", "week"),
}


def _date(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, "file not found"
    except (OSError, ValueError) as e:  # ValueError: UnicodeDecodeError on a non-UTF-8 file
        return None, f"unreadable: {e}"


def networking_events(content: str) -> list[dict]:
    """Every dated interaction, with the contact it sits under."""
    events, name = [], None
    for line in content.splitlines():
        c = CONTACT_RE.match(line)
        if c:
            name = c.group(1).strip()
        m = INTERACTION_RE.match(line)
        if not m or name is None:
            continue
        # A bad date ("2026-13-45") parses to None and so never equals a day counted.
        kind, summary = m.group(2), m.group(3)
        events.append({"date": _date(m.group(1)), "name": name, "kind": kind.strip().casefold(),
                       "direction": classify_summary(kind, summary)})
    return events


def pipeline_rows(content: str) -> list[list[str]]:
    """Table rows only. The header and separator rows need no filter: neither has a date
    in Date Updated nor an "Applied <date>" cell."""
    rows = []
    for line in content.splitlines():
        cols = [c.strip() for c in line.strip("|").split("|")]
        if line.startswith("|") and len(cols) >= 4:
            rows.append(cols)
    return rows


def applied_on(cols: list[str], d: date) -> bool:
    """Cell by cell: joining the cells would read the Stage cell "Applied" followed by the
    Date Updated cell as "Applied <date>", which is the false positive this rule avoids."""
    pat = re.compile(rf"\bapplied (on )?{d.isoformat()}\b", re.I)
    return any(pat.search(c) for c in cols)


def unconfirmed_applied(cols: list[str], d: date) -> bool:
    """A row moved to Stage "Applied" on this date with no "Applied <date>" cell anywhere.

    `pipe_write.py update <co> Applied` sets the Stage and Date Updated and writes no
    date text, so it is invisible to applied_on. But a follow-up on an older Applied row
    also moves Date Updated, so this cannot be counted as an application: it is shown
    apart, as rows to confirm."""
    return (cols[2].strip().casefold() == "applied" and _date(cols[3]) == d
            and not any(re.search(r"\bapplied (on )?\d{4}-\d{2}-\d{2}\b", c, re.I)
                        for c in cols))


def same_person(a: str, b: str) -> bool:
    """The two logs spell a person differently ("First" in one, "First Last" in the
    other). Same person when one name is the other plus more words."""
    a, b = a.casefold().split(), b.casefold().split()
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_[:len(short)] == short


def count_people(names: list[str]) -> int:
    kept: list[str] = []
    for n in names:
        if not any(same_person(n, k) for k in kept):
            kept.append(n)
    return len(kept)


def parse_targets(content: str) -> dict:
    out = {}
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2:
            continue
        key = TARGET_ROWS.get(cols[0].casefold())
        m = re.match(r"\s*(\d+)\b", cols[1])
        if key and m:
            out[key[0]] = {"target": int(m.group(1)), "period": key[1],
                           "label": cols[0], "source": "data/goals.md Success Metrics"}
    return out


def _todos_closed(content: str, d: date) -> int:
    return len(todo_read.completed_on(content, d))


def collect(root: Path, target: date) -> dict:
    week_start = target - timedelta(days=target.weekday())
    days = [week_start + timedelta(days=i) for i in range((target - week_start).days + 1)]
    per_day = {d: {k: None for k in COUNTS + EXTRA} for d in days}
    sources = {}

    outreach, err_o = _read(root / "data" / "outreach-log.md")
    networking, err_n = _read(root / "data" / "networking.md")
    pipeline, err_p = _read(root / "data" / "job-pipeline.md")
    todos, err_t = _read(root / "data" / "job-todos.md")
    goals, err_g = _read(root / "data" / "goals.md")
    for name, err in (("outreach-log", err_o), ("networking", err_n), ("pipeline", err_p),
                      ("job-todos", err_t), ("goals", err_g)):
        sources[name] = {"readable": err is None, **({"error": err} if err else {})}

    if pipeline is not None:
        try:
            header = find_header_line(pipeline, "| Company |")
            if header is None and pipeline_rows(pipeline):
                # A table with no recognisable header is drift too; assert_schema passes a
                # missing header, so it is caught here (D8 r2).
                raise SchemaDriftError("no '| Company |' header line above the table rows")
            assert_schema(header, PIPELINE_COLUMNS)
        except SchemaDriftError as e:
            sources["pipeline"] = {"readable": False, "error": f"schema drift: {e}"}
            pipeline = None
    events = networking_events(networking) if networking is not None else []
    o_rows = parse_outreach_rows(outreach) if outreach is not None else []
    p_rows = pipeline_rows(pipeline) if pipeline is not None else []

    for d in days:
        c = per_day[d]
        # Both logs are needed to say how many went out; with one missing the number is a
        # floor, so it is reported as null rather than as a confident count.
        if outreach is not None and networking is not None:
            people = [r["recipient"] for r in o_rows
                      if _date(r["date"]) == d and r["status"].casefold() in SENT_STATES]
            people += [e["name"] for e in events if e["date"] == d and e["direction"] == "send"]
            c["sends"] = count_people(people)
        if networking is not None:
            c["replies"] = sum(1 for e in events if e["date"] == d and e["direction"] == "reply")
            c["calls"] = sum(1 for e in events if e["date"] == d and e["kind"] in LIVE)
        if pipeline is not None:
            c["applications"] = sum(1 for cols in p_rows if applied_on(cols, d))
            c["applications_unconfirmed"] = sum(1 for cols in p_rows
                                                if unconfirmed_applied(cols, d))
            c["pipeline_moves"] = sum(1 for cols in p_rows if _date(cols[3]) == d)
        if todos is not None:
            try:
                c["todos_closed"] = _todos_closed(todos, d)
            except Exception as e:  # schema drift in the todo file: say so, do not zero it
                sources["job-todos"] = {"readable": False, "error": f"{type(e).__name__}: {e}"}
                c["todos_closed"] = None

    def week(k):
        vals = [per_day[d][k] for d in days]
        return None if any(v is None for v in vals) else sum(vals)

    counts = {k: {"today": per_day[target][k], "week": week(k)} for k in COUNTS + EXTRA}
    return {
        "target_date": target.isoformat(),
        "week_start": week_start.isoformat(),
        "counts": counts,
        "targets": parse_targets(goals) if goals is not None else {},
        "sources": sources,
        "complete": all(s["readable"] for s in sources.values()),
    }


LABELS = {"sends": "Sends", "replies": "Replies logged", "calls": "Calls",
          "applications": "Applications", "pipeline_moves": "Pipeline moves",
          "todos_closed": "To-dos closed"}


def render_text(out: dict) -> str:
    lines = [f"Today, {out['target_date']} (week from {out['week_start']})", ""]
    for k in COUNTS:
        c = out["counts"][k]
        t = out["targets"].get(k)
        today = "unknown" if c["today"] is None else str(c["today"])
        week = "unknown" if c["week"] is None else str(c["week"])
        tail = f"  (target {t['target']} a {t['period']})" if t else ""
        lines.append(f"  {LABELS[k]:16} today {today:>7}   week {week:>7}{tail}")
        if k == "applications":
            u = out["counts"]["applications_unconfirmed"]["today"]
            if u:
                lines.append(f"  {'':16} plus {u} row(s) moved to Applied today with no "
                             "applied date: confirm")
    bad = [f"{n}: {s['error']}" for n, s in out["sources"].items() if not s["readable"]]
    if bad:
        lines += ["", "Unreadable sources: " + "; ".join(bad)]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--target-date", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    target = _date(a.target_date) if a.target_date else todo_read.logical_today()
    if target is None:
        print(f"ERROR: --target-date must be YYYY-MM-DD, got {a.target_date!r}", file=sys.stderr)
        return 2
    out = collect(Path(a.repo_root).resolve(), target)
    print(json.dumps(out, indent=1, ensure_ascii=False) if a.json else render_text(out))
    return 0 if out["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
