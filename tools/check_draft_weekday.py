#!/usr/bin/env python3
"""
check_draft_weekday.py — PreToolUse hook for Claude Code.

Blocks open_draft.py when the pending draft pairs a weekday with a date that
falls on a different weekday ("Monday, Mar 16" when Mar 16 is a Tuesday).

Why this exists (tier escalation):
  memory/feedback_run_date_dont_infer_local_time.md says to compute dates,
  never infer them. It fired a 6th time when an outbound draft paired
  weekdays with dates that did not match. The
  rule's earlier promotion target (/todo, /checkout) never sees an outbound
  draft, and the existing draft hooks check voice and holidays, not dates.

Why BLOCK, not WARN:
  Per tools/HOOK_AUTHORING.md, a PreToolUse exit-0 warn is invisible. A
  weekday that contradicts its own date is wrong in every case, and the fix
  is a single correction, which is the BLOCK bar.

What counts as a pair (case-insensitive):
  - weekday then date: "Monday, Mar 16", "Mon Mar 16th", "Tuesday, March 17, 2027",
    "Wed 3/17", "Wednesday, 3/17/2027"
  - date then weekday in parentheses: "Mar 16 (Mon)", "3/16 (Tuesday)"
A bare weekday with no date ("talk Thursday") or a bare date is not checked.
A pair must sit on one line, a full weekday name followed by a period ends a
sentence and never pairs with the next date, and subject and body are checked
separately.

Year: an explicit year (1900-2099 only) is used as written. Otherwise the year is the one,
among last year / this year / next year, that puts the date closest to the
run date, so "Jan 5" written in late December resolves to next January.
Impossible dates (Feb 30) are skipped, not blocked. A four-digit number right
after a named date is read as a year: outside 1900-2099 the whole date is
skipped, and so is a bare 24-hour time like "Mar 16, 1030" (known limit; it
skips rather than blocks).

Exit codes:
  0 — clean / not applicable / fail-open (never blocks on infra issues)
  2 — a weekday contradicts its date (blocks; message lists each with the fix)

Override: DRAFT_OVERRIDE=1 python3 tools/open_draft.py  (must be in the hook
process env; an inline prefix on the blocked Bash command does NOT inherit).
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

# Reuse the single-source command detection + body extraction from the sibling
# hook (tools/HOOK_AUTHORING.md: never re-implement the open_draft.py matcher).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_draft_voice import (  # noqa: E402
    read_hook_command,
    is_open_draft_invocation,
    extract_body,
)

DRAFT_FILE = Path(__file__).parent / ".pending-draft.txt"

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD_ALT = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?"
    r"|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON_ALT = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# "Mar 16", "March 16th", "Mar. 16, 2027"
_NAMED = rf"(?P<mon>{_MON_ALT})\.?[ \t]+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?[ \t]+(?P<year>(?:19|20)\d{{2}}))?(?(year)|(?!,?[ \t]+\d{{4}}\b))"
# The conditional: with no captured year, the date may not be followed by a
# four-digit number, so an out-of-range year ("Mar 1, 8000") rejects the whole
# token instead of being dropped and re-read as this year.
# "3/16", "3/16/2027", "3/16/27"
_NUMERIC = r"(?P<nmon>\d{1,2})/(?P<nday>\d{1,2})(?:/(?P<nyear>(?:19|20)\d{2}|\d{2}))?"
_DATE = rf"(?:{_NAMED}|{_NUMERIC})"

WD_THEN_DATE = re.compile(
    rf"\b(?P<wd>{_WD_ALT})\b(?P<dot>\.)?,?[ \t]+(?:the[ \t]+)?{_DATE}(?![\w/])", re.IGNORECASE
)
DATE_THEN_WD = re.compile(
    rf"\b{_DATE}(?![\w/])[ \t]*\([ \t]*(?P<wd>{_WD_ALT})\.?[ \t]*\)", re.IGNORECASE
)


def _weekday_index(token: str) -> int:
    return WEEKDAYS.index(next(w for w in WEEKDAYS if w.startswith(token.lower()[:3])))


def _resolve(month: int, day: int, year: "int | None", today: date) -> "date | None":
    """Return the concrete date, or None if it does not exist."""
    if year is not None:
        return _safe_date(year, month, day)
    candidates = [d for d in (_safe_date(y, month, day) for y in
                              (today.year - 1, today.year, today.year + 1)) if d]
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - today).days))


def _safe_date(year: int, month: int, day: int) -> "date | None":
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_from_match(m: "re.Match[str]", today: date) -> "date | None":
    if m.group("mon"):
        month = MONTHS[m.group("mon").lower()[:3]]
        day = int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else None
    else:
        month = int(m.group("nmon"))
        day = int(m.group("nday"))
        ny = m.group("nyear")
        year = (2000 + int(ny) if len(ny) == 2 else int(ny)) if ny else None
    return _resolve(month, day, year, today)


def _ends_sentence(m: "re.Match[str]") -> bool:
    """'Monday. March 16' is two sentences; 'Mon. Mar 16' is an abbreviation."""
    groups = m.groupdict()
    return bool(groups.get("dot")) and m.group("wd").lower() in WEEKDAYS


def find_mismatches(text: str, today: date) -> "list[tuple[str, str]]":
    """
    Pure, testable core. Returns (phrase as written, correct weekday) for every
    weekday/date pair whose weekday is wrong. Empty list means clean.
    """
    found = []
    for pattern in (WD_THEN_DATE, DATE_THEN_WD):
        for m in pattern.finditer(text):
            if _ends_sentence(m):
                continue
            actual = _date_from_match(m, today)
            if actual is None:
                continue
            claimed = _weekday_index(m.group("wd"))
            if claimed != actual.weekday():
                found.append((m.group(0).strip(), WEEKDAYS[actual.weekday()].capitalize()
                              + f" {actual.isoformat()}"))
    return found


def checked_fields(content: str) -> "list[str]":
    """Subject and body as separate fields, so no pair is formed across them."""
    subject = ""
    for line in content.splitlines():
        if line.upper().startswith("SUBJECT:"):
            subject = line.split(":", 1)[1]
            break
    return [subject, extract_body(content)]


def main():
    if os.environ.get("DRAFT_OVERRIDE") == "1":
        sys.exit(0)

    command = read_hook_command()
    if not is_open_draft_invocation(command):
        sys.exit(0)

    try:
        content = DRAFT_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        sys.exit(0)  # fail-open: no draft, or unreadable

    today = date.today()
    mismatches = [hit for field in checked_fields(content)
                  for hit in find_mismatches(field, today)]
    if not mismatches:
        sys.exit(0)

    lines = "\n".join(f'    "{phrase}"  ->  that date is a {fix}' for phrase, fix in mismatches)
    msg = (
        "BLOCKED: weekday/date mismatch in tools/.pending-draft.txt.\n\n"
        f"{lines}\n\n"
        "  Compute every weekday from its date (`date -j -f %Y-%m-%d YYYY-MM-DD +%A`\n"
        "  or a datetime call); never infer it. Fix the draft, then re-run\n"
        "  open_draft.py. Origin: wrong weekdays sent in a real outbound draft.\n"
        "  If this is a false positive, log it:\n"
        '    python3 tools/friction_log.py append check_draft_weekday.py "FP: <what got wrongly blocked>"\n'
        "  and override once with DRAFT_OVERRIDE=1 in the hook environment."
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
