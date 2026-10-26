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
any country (9+ digits, ISO dates kept); money (any Unicode currency sign, a three-letter
code beside a figure, "95K", comma-grouped or 5+ digit figures); URLs of any scheme and
domains with a path; and file paths (a known repo directory, a file extension, three or
more segments, Unix absolute, Windows, UNC). It is a pattern list, not a proof: a token in
none of those shapes passes. A two-segment word pair ("and/or", "notes/drafts") is kept
on purpose, because it cannot be told from a path by its shape.

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
_ISO_DATE = re.compile(r"(\b\d{4}-\d{2}-\d{2}\b)")
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
    re.compile(r"~/\S*|\b(?:output|data|tools|coaching|framework|memory|docs|tests)/\S*"),
    re.compile(r"\b[\w./-]+\.(?:md|py|json|pdf|txt|sh|ya?ml|docx|csv)\b"),  # file / path
    re.compile(r"\b[A-Za-z]:\\\S*|\\\\\S+"),                             # Windows / UNC path
    re.compile(r"(?<![\w/])/[\w.-]+(?:/[\w.-]*)+"),                      # absolute path, 2+ segments
    re.compile(r"\b[\w.-]+(?:/[\w.-]+){2,}/?"),                          # relative path, 3+ segments
    re.compile(_CUR + r"\s?\d[\d,.']*\s?[kKmM]?(?:\s?-\s?" + _CUR
               + r"?\d[\d,.']*\s?[kKmM]?)?\b|" + _CUR),                 # any currency sign, or a range
    re.compile(r"\b(?:" + _CODES + r")\s?\d[\d,.']*\s?[kKmM]?\b"
               r"|\b\d[\d,.']*\s?[kKmM]?\s?(?:" + _CODES + r")\b"),       # "USD 950", "950 EUR"
    re.compile(r"\b[A-Z]{3}\s?(?:\d{1,3}(?:[,.']\d{3})+|\d{4,}(?![-/]\d)|\d+(?:\.\d+)?\s?[kKmM])\b"
               r"|\b(?:\d{1,3}(?:[,.']\d{3})+|\d{4,}|\d+(?:\.\d+)?\s?[kKmM])\s?[A-Z]{3}\b"),
                                                                        # "CHF 95000", "150,000 USD"
    re.compile(r"\b\d{2,3}(?:\.\d)?[kK]?\s?-\s?\d{2,3}(?:\.\d)?[kK]\b"),   # "150-180k" range
    re.compile(r"\b\d{2,3}(?:\.\d)?[kK]\b"),                            # "95K" comp shorthand
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"),                    # "150,000" grouped figure
    re.compile(r"\b\d{5,}\b"),                                          # long figure or id
]
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
    s = _strip_phones(s)
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
        if is_terminal_stage(e["stage"]):
            continue
        row = by_key.get((e["company"], e["role"]), {})
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
