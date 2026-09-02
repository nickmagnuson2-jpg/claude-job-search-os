#!/usr/bin/env python3
"""Count the OUTCOME metric Nick actually cares about: conversations and interviews HAD.

Origin: 2026-09-01. Nick: "My metric that I first is outreach: number of outreach sent.
I need to start thinking about interviews and conversations had. I need to actually get
those outcomes up and then determine how I schedule my day based off of how I can get
those things."

Outreach-sent is an INPUT. This counts the OUTPUT, plus the conversion between them, so
/standup can lead with the outcome instead of the activity.

Three sources, each reported with its own status. A source that cannot be read yields
`null`, NEVER 0 - a silently-zeroed source turns "no conversations" into a lie, which is
the exact failure this metric exists to avoid.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/conversations_metric.py [--target-date YYYY-MM-DD]
                                                              [--repo-root PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

WINDOWS = (7, 30, 90)

# --- interaction-type vocabulary -------------------------------------------------
# A LIVE conversation is a real-time human exchange. That is the thing being counted.
# Everything else is either an async touch (an input, like outreach) or bookkeeping.
# Normalization is casefold + strip; unmapped types are REPORTED, never dropped.
LIVE = {
    "call", "video", "in-person", "coffee", "visit", "recruiter call",
    "call + text", "meeting", "onsite", "interview", "recruiter screen",
}
ASYNC = {
    "email", "linkedin", "text", "imessage", "sms", "email-sent", "email reply",
    "email (inbound)", "linkedin message", "inbound", "claude-chat",
}
META = {
    "other", "closeout", "profile-import", "captured", "status", "note", "context",
    "decision", "research", "logistics", "calendar-invite", "linkedin-observed",
    "recruiter screen scheduled", "call scheduled", "—", "-", "",
}

INTERACTION_RE = re.compile(r"^####\s+(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|", re.M)
STAGE_RE = re.compile(r"^stage:\s*(.+?)\s*$", re.M)
DATED_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# stages in coaching/progress that are practice, not a real conversation with a company.
# Substring match, because the field is free text: "pre-call prep drill" is a drill and an
# exact-match set silently counted it as a real interview on the 2026-09-01 first run.
DRILL_MARKERS = ("drill", "sim", "practice", "rehears")
DRILL_STAGES_EXACT = {"rep", "n/a", ""}
# An OUTCOME record is news about a loop (offer/rejection), not a conversation that happened.
# Counting these as conversations inflated the 30d number by 2 on the first real-data run.
OUTCOME_MARKERS = ("outcome",)


def _classify_stage(stage: str) -> str:
    """-> 'drill' | 'outcome' | 'conversation'"""
    st = stage.strip().casefold()
    if st in DRILL_STAGES_EXACT or any(m in st for m in DRILL_MARKERS):
        return "drill"
    if any(m in st for m in OUTCOME_MARKERS):
        return "outcome"
    return "conversation"


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _bucket(d: date, today: date) -> int | None:
    """Smallest window this date falls into, or None if older than the largest."""
    age = (today - d).days
    if age < 0:
        return None
    for w in WINDOWS:
        if age < w:
            return w
    return None


def _empty_windows() -> dict:
    return {f"{w}d": 0 for w in WINDOWS}


def _add(counts: dict, d: date, today: date) -> None:
    age = (today - d).days
    if age < 0:
        return
    for w in WINDOWS:
        if age < w:
            counts[f"{w}d"] += 1


def count_conversations(path: Path, today: date) -> dict:
    """Live conversations from the networking interaction log."""
    out = {
        "source": str(path), "readable": False, "counts": None,
        "async_counts": None, "unknown_types": [], "total_entries": 0,
    }
    if not path.exists():
        out["error"] = "file not found"
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        out["error"] = f"unreadable: {e}"
        return out
    live, asyn = _empty_windows(), _empty_windows()
    unknown: dict[str, int] = {}
    entries = INTERACTION_RE.findall(text)
    for raw_date, raw_type in entries:
        d = _parse_date(raw_date)
        if d is None:
            continue
        t = raw_type.strip().casefold()
        if t in LIVE:
            _add(live, d, today)
        elif t in ASYNC:
            _add(asyn, d, today)
        elif t in META:
            pass
        else:
            unknown[t] = unknown.get(t, 0) + 1
    out.update(
        readable=True, counts=live, async_counts=asyn,
        total_entries=len(entries),
        unknown_types=sorted(unknown.items(), key=lambda kv: -kv[1]),
    )
    return out


def count_interviews(progress_dir: Path, today: date) -> dict:
    """Real (non-drill) interview sessions from coaching/progress session-metadata."""
    out = {"source": str(progress_dir), "readable": False, "counts": None,
           "outcome_counts": None, "recent": [], "untagged": []}
    if not progress_dir.is_dir():
        out["error"] = "directory not found"
        return out
    counts, outcomes = _empty_windows(), _empty_windows()
    recent, untagged = [], []
    for f in sorted(progress_dir.glob("*.md")):
        m = DATED_FILE_RE.match(f.name)
        if not m:
            continue
        d = _parse_date(m.group(1))
        if d is None:
            continue
        try:
            head = f.read_text(encoding="utf-8")[:1200]
        except OSError:
            continue
        sm = STAGE_RE.search(head)
        if sm is None:
            if _bucket(d, today):
                untagged.append(f.name)
            continue
        stage = sm.group(1).strip().casefold()
        kind = _classify_stage(stage)
        if kind == "drill":
            continue
        if kind == "outcome":
            _add(outcomes, d, today)
            continue
        _add(counts, d, today)
        if _bucket(d, today) is not None:
            recent.append({"date": d.isoformat(), "file": f.name, "stage": stage})
    out.update(readable=True, counts=counts, outcome_counts=outcomes,
               recent=sorted(recent, key=lambda r: r["date"], reverse=True)[:8],
               untagged=untagged)
    return out


def count_outreach(path: Path, today: date) -> dict:
    """Outreach actually sent - the INPUT side of the conversion."""
    out = {"source": str(path), "readable": False, "counts": None}
    if not path.exists():
        out["error"] = "file not found"
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        out["error"] = f"unreadable: {e}"
        return out
    counts = _empty_windows()
    sent_states = {"sent", "replied", "delivered", "no reply", "delivery unknown", "bounced"}
    for line in lines:
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        d = _parse_date(cells[0])
        if d is None:
            continue
        if cells[-1].casefold() in sent_states:
            _add(counts, d, today)
    out.update(readable=True, counts=counts)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target-date", default=None)
    ap.add_argument("--repo-root", default=".")
    a = ap.parse_args(argv)

    root = Path(a.repo_root).resolve()
    today = _parse_date(a.target_date) if a.target_date else date.today()
    if today is None:
        print(json.dumps({"status": "error",
                          "message": f"bad --target-date: {a.target_date!r}"}))
        return 2

    convo = count_conversations(root / "data" / "networking.md", today)
    ivs = count_interviews(root / "coaching" / "progress", today)
    reach = count_outreach(root / "data" / "outreach-log.md", today)

    complete = all(s["readable"] for s in (convo, ivs, reach))
    unreadable = [s["source"] for s in (convo, ivs, reach) if not s["readable"]]

    combined = None
    if convo["readable"] and ivs["readable"]:
        combined = {f"{w}d": convo["counts"][f"{w}d"] + ivs["counts"][f"{w}d"]
                    for w in WINDOWS}

    # Deliberately NOT a ratio. Conversations in a window are not caused by that window's
    # outreach (there is a lag, and interviews also come from applications), so a computed
    # "conversion rate" would read as causal and be wrong. Counts side by side only.
    outcome_vs_input = {}
    if combined is not None and reach["readable"]:
        for w in WINDOWS:
            k = f"{w}d"
            outcome_vs_input[k] = {
                "conversations_and_interviews": combined[k],
                "outreach_sent": reach["counts"][k],
            }
    outcome_vs_input["_note"] = (
        "Counts only. No ratio is computed: conversations in a window are not caused by "
        "that window's outreach (lag + interviews also arrive via applications)."
    )

    print(json.dumps({
        "status": "ok",
        "target_date": today.isoformat(),
        "windows_days": list(WINDOWS),
        "complete": complete,
        "unreadable_sources": unreadable,
        "conversations_and_interviews": combined,
        "live_conversations": convo,
        "interviews": ivs,
        "outreach_sent": reach,
        "outcome_vs_input": outcome_vs_input,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
