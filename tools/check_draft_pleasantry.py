#!/usr/bin/env python3
"""
check_draft_pleasantry.py — PreToolUse hook for Claude Code.

Blocks open_draft.py when a job-search draft is being sent ON A WEEKEND or a
holiday-adjacent day AND the body carries no warm pleasantry (open or close).

Why this exists (tier escalation):
  The "add a personalized pleasantry when you know something about the recipient's
  week" rule (framework/voice-reference.md line 40) was promoted to a mechanical
  pre-send check inside /follow-up Step 7 on 2026-07-09 after 3 misses. It was
  STILL skipped a 4th time (a contact at a target company, 2026-07-24, a Friday
  send). A checklist buried in skill prose relies on the LLM remembering to run
  it, and it doesn't (per memory/feedback_llm_self_policing_fails.md). This hook
  moves the check to the enforcement tier for the one case that is cleanly
  detectable and high-signal: a weekend / holiday-adjacent send.

Why BLOCK, not WARN:
  Per tools/HOOK_AUTHORING.md, a PreToolUse exit-0+stderr "warn" is NOT surfaced
  by Claude Code — it is invisible. A real signal must be exit 2. To keep the
  false-positive rate low (per feedback_warn_vs_block_hook_design.md, BLOCK is
  reserved for near-always-wrong cases), the block is SCOPED to fire only when
  BOTH (a) no pleasantry token is present AND (b) it is a weekend (Fri-Sun) or
  within +/-1 day of a fixed major US holiday. On an ordinary weekday it stays
  silent — a pleasantry is optional there. The rare legit weekend send with no
  pleasantry (a break-up nudge, or a pleasantry the regex missed) uses the
  documented DRAFT_OVERRIDE escape hatch.

Known scope limit (documented, not a bug): floating Monday holidays sent ON the
Monday itself (Memorial Day, Labor Day) are not in the fixed list; the long
weekend before them still triggers via the Fri-Sun weekend rule. Multi-day-ahead
holiday references (e.g. "great 4th of July" sent 4 days early) are not caught —
those stay behavioral in the drafting skills.

Exit codes:
  0 — clean / not applicable / fail-open (never blocks on infra issues)
  2 — weekend-or-holiday send with no pleasantry (blocks; corrective message)

Override: DRAFT_OVERRIDE=1 python3 tools/open_draft.py  (must be in the hook
process env; inline prefix on the blocked Bash command does NOT inherit).
"""
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Reuse the single-source command-detection + body-extraction from the sibling
# hook — do NOT re-implement the open_draft.py command-position regex here (that
# kind of per-hook drift is exactly what tools/HOOK_AUTHORING.md warns against).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_draft_voice import (  # noqa: E402
    read_hook_command,
    is_open_draft_invocation,
    extract_body,
)

DRAFT_FILE = Path(__file__).parent / ".pending-draft.txt"

# Any one of these in the body counts as a warm pleasantry (open OR close).
# Derived from Nick's corpus: "Hope you have a great weekend!", "Hope you had a
# great long weekend!", "Hope your week is going well!", "Happy 4th of July!",
# "enjoy your vacation", "take care", "safe travels", "wishing you".
PLEASANTRY_RE = re.compile(
    r"""
      hope\s+(you|your|the|everything|things|you're|you\s+had|you\s+have|you'?ll)
    | have\s+a\s+(great|good|wonderful|nice|lovely|relaxing|restful)
    | enjoy\s+(your|the|this)
    | happy\s+(friday|monday|weekend|new\s+year|thanksgiving|holidays?|fourth|4th|birthday|labor|memorial)
    | (great|long|nice|lovely)\s+weekend
    | wishing\s+you
    | take\s+care
    | safe\s+travels
    | congrat
    | great\s+week\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Fixed-date major US holidays: (month, day) -> display name.
FIXED_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (6, 19): "Juneteenth",
    (7, 4): "the Fourth of July",
    (11, 11): "Veterans Day",
    (12, 24): "Christmas Eve",
    (12, 25): "Christmas",
    (12, 31): "New Year's Eve",
}


def timing_context(today: date) -> "str | None":
    """
    Return a human phrase for why a pleasantry is expected today, or None if
    today is an ordinary weekday with no holiday adjacency (pleasantry optional).
    """
    wd = today.weekday()  # Mon=0 .. Sun=6
    if wd == 4:
        return "heading into the weekend (Friday)"
    if wd in (5, 6):
        return "over the weekend"
    for delta in (0, 1, -1):  # today, tomorrow (eve), yesterday (just-passed)
        d = today + timedelta(days=delta)
        name = FIXED_HOLIDAYS.get((d.month, d.day))
        if name:
            when = {0: "today", 1: "tomorrow", -1: "just now"}[delta]
            return f"near {name} ({when})"
    return None


def evaluate(body: str, today: date) -> "str | None":
    """
    Core decision, pure + testable. Returns the timing-context reason string
    when the draft SHOULD be blocked (weekend/holiday send, no pleasantry),
    else None (allow).
    """
    if not body.strip():
        return None
    if PLEASANTRY_RE.search(body):
        return None
    return timing_context(today)


def main():
    if os.environ.get("DRAFT_OVERRIDE") == "1":
        sys.exit(0)

    command = read_hook_command()
    if not is_open_draft_invocation(command):
        sys.exit(0)

    if not DRAFT_FILE.exists():
        sys.exit(0)  # fail-open: no draft to check

    try:
        content = DRAFT_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        sys.exit(0)  # fail-open on read errors

    reason = evaluate(extract_body(content), date.today())
    if reason is None:
        sys.exit(0)

    msg = (
        f"BLOCKED: this draft has no warm pleasantry and you're sending {reason}.\n\n"
        '  Nick reliably adds a warm line here (e.g. "Hope you have a great weekend!"\n'
        '  or "Hope you had a great long weekend!"). It can be the opener or the close.\n\n'
        "  This is the enforcement tier for a rule that kept getting skipped at the\n"
        "  skill tier (voice-reference.md line 40; 4th recurrence 2026-07-24).\n\n"
        "  Fix: add a one-line pleasantry to tools/.pending-draft.txt, then re-run open_draft.py.\n"
        "  Legit exception (break-up nudge, or a pleasantry the check missed):\n"
        "    DRAFT_OVERRIDE=1 python3 tools/open_draft.py\n"
        "  If this is a false positive, log it:\n"
        '    python3 tools/friction_log.py append check_draft_pleasantry.py "FP: <what got wrongly blocked>"'
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
