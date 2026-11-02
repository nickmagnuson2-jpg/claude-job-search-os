#!/usr/bin/env python3
"""reply_classify.py - the ONE rule for "did the contact reply?" in a networking entry.

The rule (Nick, 2026-10-30): the direction is recorded when an interaction is logged, never
read from the summary. `networking_write.py log --direction in|out|both|none` and the
/remember ask store it in the interaction type:

    "<type> (inbound)"   the contact reached Nick    -> 'reply'
    "<type> (outbound)"  Nick reached the contact    -> 'send'
    "<type> (two-way)"   both, in one note           -> 'both' (a reply AND a send)
    "<type>"             neither, or not recorded    -> None

Two-way (Nick, 2026-10-30 13:42): one note can record a message each way ("Sent a nudge;
Sam replied"). A single label would drop one of them for good, so it counts as both.

The bare legacy type "inbound" also reads as inbound. Nothing else counts, and the summary
is not an argument, so no caller can make it count.

Used by:
  - tools/networking_write.py `log`: writes the caller's --direction with with_direction().
    It flips the outreach-log row to Replied on --direction in or both.
  - tools/remember_apply.py `contact_note`: the same, from the direction /remember asked for.
  - tools/day_check.py: counts replies and sends from data/networking.md.
  - tools/conversations_metric.py, tools/todo_daily_metrics.py: base_type() for the channel.

No other tool may keep its own copy of these rules
(tests/scripts/test_reply_classification_consistency.py enforces that).

History. Until 2026-10-30 this module read the summary: five rules of structured English
("Sam replied", "INBOUND 9:12", "Follow-up #1 SENT", ...). Every review round found one more
phrasing those rules got wrong, so the rules never converged. They were replaced by the
recorded field, after tools/networking_direction_backfill.py labelled every existing entry
with the verdict the old rules gave it. No reply or send verdict was lost; replies that also
recorded a message from Nick became two-way, so they count as a send too. The old rules are in git at
dfccc00:tools/reply_classify.py, which is also the classifier the backfill reads.
"""
from __future__ import annotations

import re

DIRECTION_SUFFIX = {"in": " (inbound)", "out": " (outbound)", "both": " (two-way)"}
_SUFFIX_RE = re.compile(r"\s*\((inbound|outbound|two-way)\)\s*$", re.IGNORECASE)
_FROM_SUFFIX = {"inbound": "in", "outbound": "out", "two-way": "both"}
_VERDICT = {"in": "reply", "out": "send", "both": "both"}
# What day_check counts: a two-way entry is one of each.
REPLY_VERDICTS = frozenset({"reply", "both"})
SEND_VERDICTS = frozenset({"send", "both"})


def direction(kind: str | None) -> str | None:
    """-> 'in' | 'out' | 'both' | None, from the type's suffix (or the bare legacy type
    "inbound")."""
    k = (kind or "").strip()
    if k.casefold() == "inbound":
        return "in"
    m = _SUFFIX_RE.search(k)
    if not m:
        return None
    return _FROM_SUFFIX[m.group(1).casefold()]


def base_type(kind: str | None) -> str:
    """The type without its direction suffix: "email (inbound)" -> "email"."""
    return _SUFFIX_RE.sub("", (kind or "").strip())


def with_direction(kind: str, d: str) -> str:
    """The type to write for direction d ('in' | 'out' | 'both' | 'none'). Raises ValueError
    when the type already says a different direction."""
    if d not in ("in", "out", "both", "none"):
        raise ValueError(f"direction must be in, out, both or none, not {d!r}")
    have = direction(kind)
    if have is not None:
        if have != d:
            raise ValueError(f"type {kind!r} is already marked {have!r}, so it cannot be {d!r}")
        return kind
    return kind if d == "none" else kind + DIRECTION_SUFFIX[d]


def classify(kind: str | None) -> str | None:
    """-> 'reply' | 'send' | 'both' | None for one interaction, from its type alone."""
    return _VERDICT.get(direction(kind))


def is_reply(kind: str | None) -> bool:
    """True only when the type records that the contact reached Nick (inbound or two-way)."""
    return classify(kind) in REPLY_VERDICTS


def is_send(kind: str | None) -> bool:
    """True only when the type records that Nick reached the contact (outbound or two-way)."""
    return classify(kind) in SEND_VERDICTS
