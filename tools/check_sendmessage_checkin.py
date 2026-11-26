#!/usr/bin/env python3
"""check_sendmessage_checkin.py -- PreToolUse hook on the SendMessage tool.

BLOCKS (exit 2) a cross-session message that
  (a) reports a "(3) unlogged:" value other than none/nothing. The check-in format is
      "(1) changed (2) uncommitted (3) unlogged (4) blocked on"; rule 12 of the overnight
      briefs is "log first, then message", so an unlogged item means the log is behind;
  (b) carries a guessed clock time: HH:Mx or HH:x ("07:4x") anywhere outside quotes and
      backticks, or ~HH:MM / about HH:MM in the same clause as a "by `date`" label; also,
      anywhere outside quotes, HH:MMish ("16:40ish") and a present-tense claim with an
      approximator ("it's ~3:40", "it is about 11:50", "the time is roughly 10:20"), the
      shapes of feedback_run_date_dont_infer_local_time's fires (added 2026-11-25, S1).
      Times come from running `date` (feedback_log_timestamps_from_date_not_estimates).

HOLD (S3, 2026-11-26): during an org HOLD stage 2 no data/ write is allowed, so "log first"
cannot happen and (a) refused every numbered check-in (5 fires in 5 close-outs; sections sent
a plain-form status instead, which hides the gap). "(3) unlogged: held ..." is accepted ONLY
while a HOLD is on record: the newest ORG heading in data/workstreams/orchestration.md (main
checkout) whose title names HOLD does not also say lifted, eased, released or ended, and is
under 2 hours old by its own heading time. Order is line order in the file. The org writes
that heading before it freezes writes; without it the held form is refused like any other.

WHY (measured 2026-11-23, output/overnight-112226/S1-staging/sendmessage-hook/PLAN.md):
  received corpus (726 messages): 6 check-ins with an unlogged value, all true; each cost an
  org correction round trip. Sent corpus (1,432 SendMessage calls since 2026-11-21): 67 carry
  HH:Mx outside quotes, and a seeded 30-row sample was 30 of 30 typed clock estimates. This
  is the only gate that sees a guessed time in a message; the dated-section writer covers logs.

SCOPE: tool_name SendMessage only; the `message` field, and `content` when it is a string
that differs from `message`. A non-string message (a structured protocol message) is not
judged. Forecasts ("ETA about 11:15") are allowed: ~/about only count beside a date label.

ADVERSARY: the agent writing a check-in. Bypasses it cannot be stopped from: quoting a guessed
time (deliberately allowed, so a correction can cite one), writing "around HH:MM" without a
date label, or sending the update through a file instead. This is a tripwire for the habit,
not a boundary.

Fail policy: an unreadable payload exits 0 (open), like the other hooks here.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from vault_paths import main_checkout  # noqa: E402

NAME = "check_sendmessage_checkin"

# Quoted spans: a mention of a pattern is not a use of it.
_DQUOTE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
_SQUOTE = re.compile(r"(?<![A-Za-z0-9])'[^'\n]{1,60}'(?![A-Za-z0-9])")
_BTICK = re.compile(r"`[^`\n]*`")

_UNLOGGED = re.compile(r"\(3\)\s*unlogged\s*:\s*(.*?)(?=\s*\(4\)|\n|$)", re.I)
_NONE = re.compile(r"^[\s*_-]*(none|nothing)\b", re.I)
_HELD = re.compile(r"^[\s*_-]*held\b", re.I)
_ORG_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) by `date` \| ORG [^:\n]*:(.*)$", re.M)
_HOLD_WORD = re.compile(r"\bHOLD\b")
_HOLD_END = re.compile(r"\b(?:lifted|eased|released|ended)\b", re.I)
HOLD_MAX_AGE = timedelta(hours=2)
HOLD_LOG = Path("data") / "workstreams" / "orchestration.md"
_GUESS = re.compile(r"(?<![\w:])\d{1,2}:(?:\d[xX]|[xX]{1,2})(?!\w)")
_APPROX = re.compile(r"(?:~\s?|\babout\s+)\d{1,2}:\d{2}\b", re.I)
_DATE_LABEL = re.compile(r"\bby\s+`?date`?", re.I)
# The two shapes of the run_date rule's own fires that the patterns above miss (S1,
# 2026-11-25): a clock time with "ish" ("16:40ish", fire 11), and a present-tense claim
# about the time with an approximator ("it's ~3:40", "It's roughly 10:20", "It's about
# 11:50"). A forecast ("ETA about 11:15", "done around 17:00") is not a claim about now
# and stays allowed.
_ISH = re.compile(r"(?<![\w:])\d{1,2}:\d{2}\s?-?ish\b", re.I)
_NOW_CLAIM = re.compile(
    r"\b(?:it[’']?s|it\s+is|the\s+time\s+is)(?:\s+now)?\s+"
    r"(?:~\s?|about\s+|roughly\s+|around\s+|approx(?:imately|\.)?\s+|nearly\s+|almost\s+)"
    r"\d{1,2}:\d{2}\b", re.I)
_CLAUSE = re.compile(r"[.;\n]\s|\n|[.;]$")


def _strip_quotes(text: str, backticks: bool = True) -> str:
    text = _DQUOTE.sub(" ", text)
    text = _SQUOTE.sub(" ", text)
    return _BTICK.sub(" ", text) if backticks else text


def _unlogged_values(text: str) -> list:
    """The value of every "(3) unlogged:" check-in field that is not none/nothing."""
    plain = _strip_quotes(text)
    out = []
    for m in _UNLOGGED.finditer(plain):
        line_start = plain.rfind("\n", 0, m.start()) + 1
        prefix = plain[line_start:m.start()]
        if prefix.strip(" \t-*>") and "(2)" not in prefix:
            continue                       # a mid-sentence mention, not a check-in field
        if not _NONE.match(m.group(1)):
            out.append(m.group(1))
    return out


def _unlogged(text: str) -> bool:
    return bool(_unlogged_values(text))


def hold_on_record(log_text: str, now: datetime) -> bool:
    """Is an org HOLD open on the record? The newest ORG heading naming HOLD decides: an
    end word closes it; otherwise it opens it, for HOLD_MAX_AGE from its heading time."""
    start = None
    for m in _ORG_HEAD.finditer(log_text):
        title = m.group(2)
        if not _HOLD_WORD.search(title):
            continue
        start = None if _HOLD_END.search(title) else datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
    return start is not None and timedelta(0) <= now - start < HOLD_MAX_AGE


def _hold_now() -> bool:
    """Read the main checkout's orchestration log. Unreadable means no HOLD on record."""
    root = main_checkout(Path(__file__).resolve().parents[1])
    try:
        text = (root / HOLD_LOG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return hold_on_record(text, datetime.now())


def _guessed_time(text: str) -> bool:
    plain = _strip_quotes(text)
    if _GUESS.search(plain) or _ISH.search(plain) or _NOW_CLAIM.search(plain):
        return True
    for clause in _CLAUSE.split(_strip_quotes(text, backticks=False)):
        if _APPROX.search(clause) and _DATE_LABEL.search(clause):
            return True
    return False


def judge(text: str, hold=None) -> str | None:
    """'unlogged', 'guessed_time', or None. `hold` is a zero-argument callable saying
    whether an org HOLD is on record (default: read the orchestration log); it is called
    only for a held-form check-in."""
    values = _unlogged_values(text)
    if values and not (all(_HELD.match(v) for v in values) and (hold or _hold_now)()):
        return "unlogged"
    if _guessed_time(text):
        return "guessed_time"
    return None


FIX = {
    "unlogged": "(3) unlogged is not none. Log first (append the entry, time from `date`), "
                "then send with (3) unlogged: none. During an org HOLD on record in "
                "orchestration.md, write (3) unlogged: held.",
    "guessed_time": "a guessed time (HH:Mx, HH:MMish, \"it's about/~ HH:MM\", or ~/about HH:MM "
                    "beside 'by date'). Run `date` and paste its HH:MM; to mention a guessed "
                    "time, quote it.",
}


def main() -> None:
    p = read_payload()
    # Fail open on an unreadable payload; judge only SendMessage. `or` short-circuits, so
    # tool_name is never read off a payload that is not ok (that read raises by design).
    if not p.ok or p.tool_name != "SendMessage":
        sys.exit(0)
    ti = p.tool_input
    texts = [t for t in (ti.get("message"), ti.get("content")) if isinstance(t, str)]
    for text in dict.fromkeys(texts):
        kind = judge(text)
        if kind:
            print(f"BLOCKED {NAME}: {FIX[kind]}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
