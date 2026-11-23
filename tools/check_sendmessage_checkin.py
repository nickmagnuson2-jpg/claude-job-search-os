#!/usr/bin/env python3
"""check_sendmessage_checkin.py -- PreToolUse hook on the SendMessage tool.

BLOCKS (exit 2) a cross-session message that
  (a) reports a "(3) unlogged:" value other than none/nothing. The check-in format is
      "(1) changed (2) uncommitted (3) unlogged (4) blocked on"; rule 12 of the overnight
      briefs is "log first, then message", so an unlogged item means the log is behind;
  (b) carries a guessed clock time: HH:Mx or HH:x ("07:4x") anywhere outside quotes and
      backticks, or ~HH:MM / about HH:MM in the same clause as a "by `date`" label.
      Times come from running `date` (feedback_log_timestamps_from_date_not_estimates).

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402

NAME = "check_sendmessage_checkin"

# Quoted spans: a mention of a pattern is not a use of it.
_DQUOTE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
_SQUOTE = re.compile(r"(?<![A-Za-z0-9])'[^'\n]{1,60}'(?![A-Za-z0-9])")
_BTICK = re.compile(r"`[^`\n]*`")

_UNLOGGED = re.compile(r"\(3\)\s*unlogged\s*:\s*(.*?)(?=\s*\(4\)|\n|$)", re.I)
_NONE = re.compile(r"^[\s*_-]*(none|nothing)\b", re.I)
_GUESS = re.compile(r"(?<![\w:])\d{1,2}:(?:\d[xX]|[xX]{1,2})(?!\w)")
_APPROX = re.compile(r"(?:~\s?|\babout\s+)\d{1,2}:\d{2}\b", re.I)
_DATE_LABEL = re.compile(r"\bby\s+`?date`?", re.I)
_CLAUSE = re.compile(r"[.;\n]\s|\n|[.;]$")


def _strip_quotes(text: str, backticks: bool = True) -> str:
    text = _DQUOTE.sub(" ", text)
    text = _SQUOTE.sub(" ", text)
    return _BTICK.sub(" ", text) if backticks else text


def _unlogged(text: str) -> bool:
    plain = _strip_quotes(text)
    for m in _UNLOGGED.finditer(plain):
        line_start = plain.rfind("\n", 0, m.start()) + 1
        prefix = plain[line_start:m.start()]
        if prefix.strip(" \t-*>") and "(2)" not in prefix:
            continue                       # a mid-sentence mention, not a check-in field
        if not _NONE.match(m.group(1)):
            return True
    return False


def _guessed_time(text: str) -> bool:
    if _GUESS.search(_strip_quotes(text)):
        return True
    for clause in _CLAUSE.split(_strip_quotes(text, backticks=False)):
        if _APPROX.search(clause) and _DATE_LABEL.search(clause):
            return True
    return False


def judge(text: str) -> str | None:
    """'unlogged', 'guessed_time', or None."""
    if _unlogged(text):
        return "unlogged"
    if _guessed_time(text):
        return "guessed_time"
    return None


FIX = {
    "unlogged": "(3) unlogged is not none. Log first (append the entry, time from `date`), "
                "then send with (3) unlogged: none.",
    "guessed_time": "a guessed time (HH:Mx, or ~/about HH:MM beside 'by date'). Run `date` and "
                    "paste its HH:MM; to mention a guessed time, quote it.",
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
