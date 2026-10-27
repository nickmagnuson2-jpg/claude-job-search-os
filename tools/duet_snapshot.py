#!/usr/bin/env python3
"""
duet_snapshot.py - Daily Markdown snapshot of job-search state for a proactive agent.

The agent (a morning-brief assistant with Gmail/Calendar/Drive connectors) cannot read
local files. This prints a short Markdown page that can be uploaded to a Google Doc so
the brief can combine it with the inbox. Read-only: it never writes a data file.

Sections:
  1. Active pipeline   - roles in process (company, role, stage, next action), plus the
                         pre-application rows still open ("on the radar").
  2. Due soon          - follow-ups and scheduled calls due in the next 7 days, plus
                         overdue ones from the last 14 days. Older overdue items are
                         stale backlog: excluded, and only their count is printed.
  3. Waiting on        - contacts with an open "Follow up: <Name> - wait for..." todo.

Reused, not copied (one source of truth each):
  todo_daily_metrics.parse_pipeline        - active_process bucket (the cross-check count)
  todo_daily_metrics.parse_upcoming_scheduled, SCHEDULED_EVENT_RE, _extract_dates
  todo_daily_metrics.parse_todos, is_closed_status  - todo rows; "Withdrawn <date> (...)"
                                             statuses are closed (prefix match)
  pipe_read.parse_all_rows                 - next action + archived flag per row
  stage_vocab.is_terminal_stage            - terminal-stage guard
  networking_read.parse_contacts           - name -> company (when the todo omits it)
                                             and Last Interaction (the "since" date)

Privacy: sanitize() strips, from every free-text cell: email addresses; phone numbers of
any country (9+ digits, ISO dates kept); money (any Unicode currency sign, with the
amount on either side; a common ISO 4217 code beside any amount, upper case or the seven
commonest in lower case; any other three capitals only beside a figure of four or more
digits, so "PST 2pm" stays; K-shorthand like "9K" or "1.5K"; thousands grouped by comma,
dot, apostrophe or any space, and ranges of any of these; a bare figure grouped in
threes by any of those separators, or of 5+ digits); URLs of any scheme with "://" or a
mailto, tel, sms, data, urn, geo or magnet URI, and domains with a path; and file paths
(a known repo directory, an extension from a fixed list of common document, image,
media, archive and calendar types, three or more segments, Unix absolute with two or
more segments or a bare system root like /tmp, Windows, UNC; an absolute path's inner
segments may hold spaces, "My Drive/notes", but its last segment ends at the first
space, since what follows cannot be told from prose). It is a pattern list, not a proof:
a token in none of those shapes passes. Kept on purpose, because each cannot be told
from a path by its shape: a two-segment word pair ("and/or", "notes/drafts") and a
one-segment slash command ("/follow-up").

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/duet_snapshot.py [--date YYYY-MM-DD]
      [--out PATH] [--repo-root PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from todo_daily_metrics import (  # noqa: E402
    SCHEDULED_EVENT_RE, _extract_dates, is_closed_status, parse_pipeline,
    parse_todos, parse_upcoming_scheduled, read_file,
)
from pipe_read import parse_all_rows  # noqa: E402
from stage_vocab import is_terminal_stage  # noqa: E402
from networking_read import parse_contacts  # noqa: E402

DUE_AHEAD_DAYS = 7
OVERDUE_LOOKBACK_DAYS = 14
MAX_ACTION_CHARS = 140
MAX_STAGE_CHARS = 70

# "Follow up: <who> <sep> <action>", sep being an em/en dash or a spaced hyphen.
# A hyphen inside a name ("Lee-Smith") has no spaces around it, so it never splits.
FOLLOW_UP_RE = re.compile(r"^follow up:\s*(?P<who>.+?)\s*(?:—|–|\s-\s)\s*(?P<action>.*)$",
                          re.IGNORECASE)
FOLLOW_UP_ANY_RE = re.compile(r"^follow up\b", re.IGNORECASE)
WAITING_RE = re.compile(
    r"\b(wait(?:ing)? for|await|ball on|check (?:for|if)|if no reply|if silent|"
    r"if nothing by|if (?:he|she|they) accepts?|watch for|no reply)",
    re.IGNORECASE,
)
LOOP_CLOSED_RE = re.compile(r"\bloop closed\b", re.IGNORECASE)

# Every currency sign Unicode knows (category Sc), not a hand list of four (review 662/663).
_CURRENCY = "".join(chr(c) for c in range(0x110000)
                    if unicodedata.category(chr(c)) == "Sc")
_CUR = "[" + re.escape(_CURRENCY) + "]"
# ONE definition of an amount, shared by every money rule below, so a grouping form fixed
# in one rule cannot be missed by another (rounds 17-19 found it rule by rule). Thousands
# may be grouped by comma, dot, apostrophe, space, no-break or narrow no-break space.
_SEP = "[,.'\u00a0\u202f\u2009\u2007 ]"
_NUM = r"(?:\d{1,3}(?:" + _SEP + r"\d{2,3})+|\d+)(?:[.,]\d+)?"   # 2-digit groups: 12,00,000
_AMT = _NUM + r"\s?[kKmM]?"
_BIG = (r"(?:\d{1,3}(?:" + _SEP + r"\d{2,3})+(?:[.,]\d+)?|\d{4,}(?![-/]\d)|"
        + _NUM + r"\s?[kKmM])")
_RANGE = _AMT + r"(?:\s?-\s?(?:" + _CUR + r")?\s?" + _AMT + r")?"
# A fixed list of common file types; a file named with any other extension passes.
_EXTENSIONS = ("md|py|json|pdf|txt|sh|ya?ml|docx?|odt|rtf|pages|csv|xlsx?|ods|numbers|pptx?"
               "|odp|key|html?|xml|epub|png|jpe?g|gif|heic|webp|svg|tiff?|bmp|mov|mp4|m4v|avi"
               "|mkv|mp3|m4a|wav|aac|flac|ogg|zip|tar(?:\\.gz)?|tgz|gz|7z|rar|dmg|ics|vcf|eml")
# Dates split the phone pass, so a date beside a number neither shields it nor is eaten.
_ISO_DATE = re.compile(r"(\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b)")
# ISO 4217 codes in ordinary use. A code from this list takes any amount ("USD 950"); any
# other three capitals take only a figure that cannot be a time or count ("XYZ 95000").
_CODES = ("USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|CNY|INR|SGD|HKD|SEK|NOK|DKK|MXN|BRL|ZAR|RUB"
          "|KRW|ILS|AED|PLN|CZK|TRY")

_SCRUB = [
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                       # email
    re.compile(r"\b[a-zA-Z][\w+.-]*://[^\s)]+"),                        # url, any scheme
    re.compile(r"\b[\w-]+(?:\.[\w-]+)+/[^\s)]*"),                       # domain with a path
    re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|ai|io|co|org|net|dev|app|so|us|me|ly|gg|tv|fm"
               r"|xyz|info|biz|uk|ca|de|fr|eu)\b", re.IGNORECASE),     # bare domain
    re.compile(r"(?:~|\b(?:output|data|tools|coaching|framework|memory|docs|tests))"
               r"(?:(?: [\w.-]+)*/[\w.-]*)+"),                          # repo or ~ path, spaced segments
    re.compile(r"\b[\w./-]+\.(?:" + _EXTENSIONS + r")\b", re.IGNORECASE),     # file name
    # A segment may hold spaces when a later separator follows ("My Drive/notes").
    re.compile(r"\b[A-Za-z]:\\(?:[^\\\n]*\\)*\S*|\\\\(?:[^\\\n]*\\)*\S*"),     # Windows / UNC path
    re.compile(r"(?<![\w/])/[\w.-]+(?:(?: [\w.-]+)*/[\w.-]*)+"),           # absolute path, 2+ segments
    # One segment is shaped like a slash command ("/follow-up"), so only system roots go.
    re.compile(r"(?<![\w/])/(?:etc|tmp|var|usr|opt|home|Users|private|Volumes|bin|sbin|dev"
               r"|mnt|root|srv|Library|Applications|System)\b/?"),
    re.compile(r"\b[\w.-]+(?:/[\w.-]+){2,}/?"),                          # relative path, 3+ segments
    re.compile(r"\b" + _RANGE + r"\s?" + _CUR),                             # sign after: 950€
    re.compile(_CUR + r"\s?" + _RANGE + r"\b|" + _CUR),                     # sign before, or a lone sign
    re.compile(r"\b(?:" + _CODES + r")\s?" + _RANGE + r"\b"
               r"|\b" + _RANGE + r"\s?(?:" + _CODES + r")\b"),                # "USD 950", "120 000 EUR"
    re.compile(r"\b(?:usd|eur|gbp|cad|aud|chf|inr)\s?" + _RANGE + r"\b"
               r"|\b" + _RANGE + r"\s?(?:usd|eur|gbp|cad|aud|chf|inr)\b"),    # lowercase
    # Unlisted code: a figure of 4+ digits only (grouped by the shared separators, plain, or
    # K/M), so "PST 2pm" stays.
    re.compile(r"\b[A-Z]{3}\s?" + _BIG + r"\b|\b" + _BIG + r"\s?[A-Z]{3}\b"),
                                                                        # "CHF 95000", "150,000 USD"
    re.compile(r"\b" + _NUM + r"[kK]?\s?-\s?" + _NUM + r"\s?[kK]\b"),      # "150-180k" range
    re.compile(r"\b" + _NUM + r"\s?[kK]\b"),                               # "9K", "1.5K", "1000K"
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"),                    # "150,000" grouped figure
    re.compile(r"\b\d{1,3}(?:[.'\u00a0\u202f\u2009\u2007 ]\d{3})+\b"),               # "120 000", "1.000.000", "120'000"
    re.compile(r"\b\d{5,}\b"),                                          # long figure or id
]
# A URI without "//" goes first, before the phone pass can empty "tel:+1415...".
_URI = re.compile(r"\b(?:mailto|tel|sms|data|urn|geo|magnet):[^\s)]+", re.IGNORECASE)
# Any phone shape: 9+ digits joined by spaces, dots, dashes or brackets, optional +.
_PHONE = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")


def _drop_phone(m: re.Match) -> str:
    t = m.group(0)
    return t if sum(ch.isdigit() for ch in t) < 9 else ""


def _strip_phones(s: str) -> str:
    """Phones are judged between ISO dates, so a date beside a number cannot shield it."""
    return "".join(part if _ISO_DATE.fullmatch(part) else _PHONE.sub(_drop_phone, part)
                   for part in _ISO_DATE.split(s))


def sanitize(text: str, limit: int = MAX_ACTION_CHARS) -> str:
    """Strip private tokens, replace dashes, collapse whitespace, truncate on a word."""
    s = (text or "").replace("—", "-").replace("–", "-")
    # Collapse every whitespace run first, so no rule has to allow "USD  950" or a tab
    # (round 24). No-break spaces survive: they are thousands separators, not runs.
    s = re.sub(r"[^\S\u00a0\u202f\u2009\u2007]+", " ", s)
    s = _strip_phones(_URI.sub("", s))
    for rx in _SCRUB:
        s = rx.sub("", s)
    s = re.sub(r"[\s,;-]+\)", ")", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+([,;.)])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -;,")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;-") + "..."
    return s


def _fmt(d: date) -> str:
    return f"{d.isoformat()} ({d.strftime('%a')})"


def _due(item: dict) -> date | None:
    try:
        return datetime.strptime(item.get("due", ""), "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_relevant(task: str, today: date) -> bool:
    """A follow-up, or a call/interview that names a date. Undated chores that merely
    mention a call ("before any call") are not the agent's business."""
    if FOLLOW_UP_ANY_RE.match(task):
        return True
    return bool(SCHEDULED_EVENT_RE.search(task) and _extract_dates(task, today))


def parse_follow_up(task: str) -> dict | None:
    """Split "Follow up: Name @ Company - action" into its parts, or None."""
    m = FOLLOW_UP_RE.match(task.strip())
    if not m:
        return None
    who, action = m.group("who").strip(), m.group("action").strip()
    company = ""
    if " @ " in who:
        who, company = (p.strip() for p in who.split(" @ ", 1))
    else:
        pm = re.search(r"\(([^)]+)\)\s*$", who)
        if pm:
            who, company = who[:pm.start()].strip(), pm.group(1).strip()
    action = "" if action.strip("—- ") == "" else action
    return {"name": who, "company": company, "action": action}


def _last_interaction(contact: dict, today: date) -> date | None:
    """The roster's Last Interaction date, the logged start of the wait. None if absent,
    unparseable, or after the snapshot date."""
    try:
        d = datetime.strptime(contact.get("last_interaction", ""), "%Y-%m-%d").date()
    except ValueError:
        return None
    return d if d <= today else None


def _who(name: str, company: str) -> str:
    c = sanitize(company, 60)
    return sanitize(name, 60) + (f" ({c})" if c else "")


def _list_name(text: str) -> str:
    """Name for the priority list: parenthetical notes dropped ("Acme (former)")."""
    return sanitize(re.sub(r"\s*\([^)]*\)", "", text), 60)


def classify_todos(active: list, today: date, contacts: dict) -> dict:
    """Bucket open, relevant todos into due_soon / waiting, counting stale and closed."""
    start = today - timedelta(days=OVERDUE_LOOKBACK_DAYS)
    end = today + timedelta(days=DUE_AHEAD_DAYS)
    out = {"due_soon": [], "waiting": {}, "stale_count": 0, "closed_skipped": 0}
    for item in active:
        task = item.get("task", "")
        if not _is_relevant(task, today):
            continue
        if is_closed_status(item.get("status", "")) or LOOP_CLOSED_RE.search(task):
            out["closed_skipped"] += 1
            continue
        due = _due(item)
        if due is None:
            continue
        if due < start:
            out["stale_count"] += 1
            continue
        if due > end:
            continue
        fu = parse_follow_up(task)
        contact = contacts.get(fu["name"].lower(), {}) if fu else {}
        if fu and not fu["company"]:
            fu["company"] = contact.get("company", "")
        if fu and WAITING_RE.search(fu["action"]):
            key = fu["name"].lower()
            rec = out["waiting"].setdefault(key, {
                "name": fu["name"], "company": "",
                "since": _last_interaction(contact, today),
                "next_nudge": due, "action": fu["action"]})
            rec["company"] = rec["company"] or fu["company"]
            if due < rec["next_nudge"]:
                rec["next_nudge"], rec["action"] = due, fu["action"]
            continue
        out["due_soon"].append({"due": due, "task": task, "follow_up": fu})
    out["due_soon"].sort(key=lambda e: e["due"])
    return out


def build_pipeline(pipeline_content: str, today: date) -> tuple[list, list]:
    """(in_process, on_radar). in_process is todo_daily_metrics' active_process bucket,
    minus anything stage_vocab calls terminal; on_radar is every other open row."""
    rows = parse_all_rows(pipeline_content, today)
    # An archived duplicate of an active row must not replace its next action (444.F5).
    by_key = {(r["company"], r["role"]): r for r in rows if r["archived"]}
    by_key.update({(r["company"], r["role"]): r for r in rows if not r["archived"]})
    in_process, seen = [], set()
    for e in parse_pipeline(pipeline_content)["active_process"]:
        row = by_key.get((e["company"], e["role"]), {})
        # The row's own stage, because by_key holds one row per key and a closed
        # duplicate would read its live twin (688.F1); and pipe_read's archived flag,
        # because the bucket misses an Archived section when a file has no Active heading
        # (678.F1).
        if is_terminal_stage(e["stage"]) or row.get("archived"):
            continue
        in_process.append({**e, "next_action": row.get("next_action", "")})
        seen.add((e["company"], e["role"]))
    on_radar = [r for r in rows
                if not r["archived"] and (r["company"], r["role"]) not in seen]
    return in_process, on_radar


def _action_or_none(text: str) -> str:
    return sanitize(text) or "none noted"


def render(today: date, in_process: list, on_radar: list, todos: dict,
           upcoming: list) -> str:
    waiting = sorted(todos["waiting"].values(), key=lambda r: r["next_nudge"])
    companies = sorted({_list_name(e["company"]) for e in in_process + on_radar}
                       | {_list_name(r["company"]) for r in waiting})
    names = [r["name"] for r in waiting] + [e["follow_up"]["name"]
                                            for e in todos["due_soon"] if e["follow_up"]]
    people = sorted({_list_name(n) for name in names for n in name.split("/")})
    L = [
        f"# Job search snapshot - {_fmt(today)}",
        "",
        "How to use this page:",
        "- Flag any email or invite from the companies and people below as high priority.",
        "- Draft replies only. Never send, accept, or decline anything.",
        "- All dates are America/Los_Angeles.",
        "- A newer email beats this page; this is a once-a-day snapshot.",
        "",
        f"Priority companies: {', '.join(c for c in companies if c) or 'none'}",
        f"Priority people: {', '.join(p for p in people if p) or 'none'}",
        "",
        f"## 1. Active pipeline ({len(in_process)} in process)",
        "",
    ]
    for e in in_process:
        L.append(f"- {sanitize(e['company'], 60)} - {sanitize(e['role'], 70)} | "
                 f"stage: {sanitize(e['stage'], MAX_STAGE_CHARS)} | "
                 f"next: {_action_or_none(e['next_action'])}")
    if not in_process:
        L.append("- none")
    if on_radar:
        L += ["", f"On the radar, not yet in process ({len(on_radar)}):"]
        for r in on_radar:
            L.append(f"- {sanitize(r['company'], 60)} - {sanitize(r['role'], 70)} | "
                     f"stage: {sanitize(r['stage'], MAX_STAGE_CHARS)} | "
                     f"next: {_action_or_none(r['next_action'])}")

    L += ["", f"## 2. Due in the next {DUE_AHEAD_DAYS} days, and overdue "
              f"from the last {OVERDUE_LOOKBACK_DAYS}", ""]
    for u in upcoming:
        d = datetime.strptime(u["date"], "%Y-%m-%d").date()
        L.append(f"- {_fmt(d)} [pipeline] {sanitize(u['company'], 60)}: "
                 f"{_action_or_none(u['next_action'])}")
    for e in todos["due_soon"]:
        flag = " OVERDUE" if e["due"] < today else ""
        fu = e["follow_up"]
        if fu:
            body = f"follow up with {_who(fu['name'], fu['company'])}: " \
                   f"{_action_or_none(fu['action'])}"
        else:
            body = sanitize(e["task"])
        L.append(f"- {_fmt(e['due'])}{flag} {body}")
    if not upcoming and not todos["due_soon"]:
        L.append("- nothing due")
    L.append(f"- Not shown: {todos['stale_count']} older overdue follow-ups "
             f"(over {OVERDUE_LOOKBACK_DAYS} days late, treated as stale backlog).")

    L += ["", f"## 3. Waiting on replies ({len(waiting)})", ""]
    for r in waiting:
        who = _who(r["name"], r["company"])
        since = r["since"].isoformat() if r["since"] else "unknown"
        late = " OVERDUE" if r["next_nudge"] < today else ""
        L.append(f"- {who} | since {since} | next nudge {_fmt(r['next_nudge'])}{late} | "
                 f"{_action_or_none(r['action'])}")
    if not waiting:
        L.append("- none")
    return "\n".join(L) + "\n"


def build_snapshot(repo_root: Path, today: date) -> str:
    data = repo_root / "data"
    pipeline_content = read_file(data / "job-pipeline.md")
    todos_content = read_file(data / "job-todos.md")
    contacts = {c["name"].lower(): c
                for c in parse_contacts(read_file(data / "networking.md"))}
    in_process, on_radar = build_pipeline(pipeline_content, today)
    _, active, _ = parse_todos(todos_content, today)
    todos = classify_todos(active, today, contacts)
    # parse_upcoming_scheduled gates only the Stage column on terminal stages, so a
    # closed row's dated Next-Action would still come back; keep open rows only (444.F1).
    # The row's own stage decides, so a closed duplicate of a live key stays out (662.F2).
    open_rows = {(e["company"], e["role"]) for e in in_process + on_radar}
    upcoming = [u for u in parse_upcoming_scheduled(pipeline_content, today,
                                                    window_days=DUE_AHEAD_DAYS)
                if (u["company"], u["role"]) in open_rows
                and not is_terminal_stage(u["stage"])]
    return render(today, in_process, on_radar, todos, upcoming)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Print a job-search snapshot for a morning-brief agent.")
    p.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today).")
    p.add_argument("--out", default=None, help="Also write the snapshot to this path.")
    p.add_argument("--repo-root", default=None, help="Repository root (default: cwd).")
    args = p.parse_args(argv)
    today = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    text = build_snapshot(repo_root, today)
    if args.out:
        out = Path(args.out)
        # Read-only toward data/: the snapshot never replaces a source file (444.F3).
        data_dir = (repo_root / "data").resolve()
        if out.resolve() == data_dir or data_dir in out.resolve().parents:
            print(f"duet_snapshot: refusing --out inside {data_dir}; write the snapshot "
                  "elsewhere (output/duet/).", file=sys.stderr)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
