#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger_diff.py — cross-call commitment ledger for /meeting call notes.

Answers "what is still owed, by whom, and for how long" by diffing the
`OWES(...)` lines across a project's call notes. This is the deterministic half
of /meeting Step 4: the LLM should not be asked to *remember* what was promised
three calls ago, it should be handed a parsed list and asked to judge it.

THE LINE FORMAT (single source of truth; /meeting Step 5 emits it):

    - [ ] OWES(<who>): <what> — "<verbatim quote>" (YYYY-MM-DD) [#<thread>]

  - `- [ ]` open, `- [x]` closed. Both are parsed; only open ones carry forward.
  - `OWES(unassigned)` is the sanctioned owner for an intention nobody claimed.
  - the quote, the date, and the [#thread] tag are all optional — a note written
    before this format existed still yields the owner and the text.

MATCHING ACROSS NOTES:
    The same commitment is restated in successive notes with slightly different
    wording, so identity is (owner, fuzzy text match) rather than string equality.
    Threshold is deliberately conservative (0.82): under-merging shows a duplicate,
    which a human spots instantly; over-merging silently fuses two obligations into
    one and the second disappears. Prefer the visible failure.

CLI:
  python3 tools/ledger_diff.py open --dir <project-dir> [--counterpart NAME]
  python3 tools/ledger_diff.py since --dir <project-dir> --note <newest-note.md>
  python3 tools/ledger_diff.py parse --file <one-note.md>

Output: JSON to stdout (--format json, default) or a readable table (--format text).

CAVEAT: the line format is young — it was designed 2026-08-07 and has not yet met
many real notes. If the format changes, this parser changes with it; the guard test
in tests/scripts/test_ledger_diff.py pins the contract.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

# - [ ] OWES(who): what — "quote" (2026-08-06) [#thread]
OWES_RE = re.compile(
    r"^\s*[-*]\s*\[(?P<done>[ xX])\]\s*OWES\(\s*(?P<owner>[^)]*?)\s*\)\s*:\s*(?P<rest>.+?)\s*$"
)
QUOTE_RE = re.compile(r'[—–-]\s*"(?P<quote>[^"]*)"')
DATE_RE = re.compile(r"\((?P<date>\d{4}-\d{2}-\d{2})\)")
THREAD_RE = re.compile(r"\[#(?P<thread>[A-Za-z0-9._\-]+)\]")
NOTE_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")

SIMILARITY_THRESHOLD = 0.82
# Minimum length of the shorter string before containment counts as identity.
# Guards against a short fragment matching every commitment that contains it.
CONTAINMENT_MIN_CHARS = 14


def _norm(text: str) -> str:
    """Normalize commitment text for fuzzy identity. Strips the decorations that
    legitimately vary between restatements (quote, date, thread, punctuation)."""
    t = QUOTE_RE.sub(" ", text)
    t = DATE_RE.sub(" ", t)
    t = THREAD_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def parse_note(path: Path) -> list:
    """Extract commitments from one call note. Returns a list of dicts."""
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    m = NOTE_DATE_RE.search(path.name)
    note_date = m.group("date") if m else ""
    for i, line in enumerate(text.splitlines(), 1):
        mt = OWES_RE.match(line)
        if not mt:
            continue
        rest = mt.group("rest")
        q = QUOTE_RE.search(rest)
        d = DATE_RE.search(rest)
        th = THREAD_RE.search(rest)
        what = QUOTE_RE.sub("", rest)
        what = DATE_RE.sub("", what)
        what = THREAD_RE.sub("", what)
        what = what.strip(" —–-\t")
        out.append({
            "owner": mt.group("owner") or "unassigned",
            "what": what,
            "quote": q.group("quote") if q else "",
            "stated": d.group("date") if d else note_date,
            "thread": th.group("thread") if th else "",
            "done": mt.group("done").lower() == "x",
            "note": path.name,
            "note_date": note_date,
            "line": i,
            "_key": _norm(rest),
        })
    return out


def _same(a: dict, b: dict) -> bool:
    if a["owner"].lower() != b["owner"].lower():
        return False
    if a["thread"] and b["thread"]:
        return a["thread"] == b["thread"]
    ka, kb = a["_key"], b["_key"]
    if not ka or not kb:
        return False
    # Containment first. The commonest real restatement is the same commitment
    # with detail bolted on ("design the signup flyer" -> "...for the storefront"),
    # which scores only ~0.79 on a pure ratio and would split into two items.
    # Require the shorter side to be substantial so a short fragment like "call"
    # cannot swallow every commitment containing that word.
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    if len(short) >= CONTAINMENT_MIN_CHARS and short in long_:
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= SIMILARITY_THRESHOLD


def collect(directory: Path, counterpart: str = None) -> list:
    """Parse every call note in a project dir, oldest first."""
    notes = sorted(p for p in directory.glob("*.md")
                   if NOTE_DATE_RE.search(p.name) and not p.name.startswith("_"))
    if counterpart:
        c = counterpart.lower()
        notes = [p for p in notes if c in p.name.lower()]
    rows = []
    for p in notes:
        rows.extend(parse_note(p))
    return rows


def build_ledger(rows: list) -> list:
    """Fold repeated statements of one commitment into a single tracked item."""
    ledger = []
    for r in rows:
        for item in ledger:
            if _same(item, r):
                item["mentions"].append({"note": r["note"], "date": r["note_date"]})
                item["last_seen"] = r["note_date"] or item["last_seen"]
                item["what"] = r["what"] or item["what"]
                if r["done"]:
                    item["done"] = True
                    item["closed_in"] = r["note"]
                if r["thread"] and not item["thread"]:
                    item["thread"] = r["thread"]
                break
        else:
            ledger.append({
                "owner": r["owner"], "what": r["what"], "quote": r["quote"],
                "thread": r["thread"], "done": r["done"],
                "first_seen": r["note_date"] or r["stated"],
                "last_seen": r["note_date"] or r["stated"],
                "closed_in": r["note"] if r["done"] else "",
                "mentions": [{"note": r["note"], "date": r["note_date"]}],
                "_key": r["_key"],
            })
    return ledger


def _age_days(iso: str, today: str = None) -> int:
    if not iso:
        return 0
    try:
        d0 = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return 0
    d1 = datetime.strptime(today, "%Y-%m-%d").date() if today else date.today()
    return max(0, (d1 - d0).days)


def open_items(ledger: list, today: str = None) -> list:
    out = []
    for it in ledger:
        if it["done"]:
            continue
        row = {k: v for k, v in it.items() if k != "_key"}
        row["age_days"] = _age_days(it["first_seen"], today)
        row["calls_unmentioned"] = 0   # filled by since()
        out.append(row)
    out.sort(key=lambda r: (-r["age_days"], r["owner"]))
    return out


def since(directory: Path, newest: Path, counterpart: str = None,
          today: str = None) -> dict:
    """The /meeting 'Since last call' input.

    Classifies every commitment open BEFORE `newest` as Carried (restated in the
    newest note), Done (checked off), or Unmentioned (silence). Unmentioned is the
    one that matters: it is what the earlier notes were quietly dropping.
    """
    prior_rows = [r for r in collect(directory, counterpart) if r["note"] != newest.name]
    prior = build_ledger(prior_rows)
    new_rows = parse_note(newest)

    carried, done, unmentioned = [], [], []
    for it in prior:
        if it["done"]:
            continue
        match = next((n for n in new_rows if _same(it, n)), None)
        entry = {k: v for k, v in it.items() if k != "_key"}
        entry["age_days"] = _age_days(it["first_seen"], today)
        if match is None:
            entry["calls_unmentioned"] = 1
            unmentioned.append(entry)
        elif match["done"]:
            done.append(entry)
        else:
            carried.append(entry)
    fresh = [{k: v for k, v in n.items() if k != "_key"} for n in new_rows
             if not any(_same(p, n) for p in prior)]
    return {
        "newest_note": newest.name,
        "done": done, "carried": carried,
        "unmentioned": unmentioned, "new_this_call": fresh,
    }


def _fmt(rows: list, title: str) -> str:
    if not rows:
        return f"{title}: none\n"
    lines = [f"{title} ({len(rows)}):"]
    for r in rows:
        age = f"{r.get('age_days', 0)}d" if r.get("age_days") else ""
        th = f" [#{r['thread']}]" if r.get("thread") else ""
        lines.append(f"  - OWES({r['owner']}): {r['what']}{th}"
                     + (f"  (open {age}, since {r.get('first_seen','?')})" if age else ""))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Cross-call commitment ledger for /meeting notes.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("open", help="All open commitments in a project dir")
    po.add_argument("--dir", required=True)
    po.add_argument("--counterpart", default=None)
    po.add_argument("--today", default=None, help="YYYY-MM-DD, for deterministic ages")

    ps = sub.add_parser("since", help="Diff prior notes against the newest one")
    ps.add_argument("--dir", required=True)
    ps.add_argument("--note", required=True)
    ps.add_argument("--counterpart", default=None)
    ps.add_argument("--today", default=None)

    pp = sub.add_parser("parse", help="Parse a single note (debugging the format)")
    pp.add_argument("--file", required=True)

    for p in (po, ps, pp):
        p.add_argument("--format", choices=("json", "text"), default="json")

    a = ap.parse_args()

    if a.cmd == "parse":
        rows = parse_note(Path(a.file))
        print(json.dumps([{k: v for k, v in r.items() if k != "_key"} for r in rows],
                         ensure_ascii=False, indent=2) if a.format == "json"
              else _fmt(rows, "Commitments"))
        return 0

    d = Path(a.dir)
    if not d.is_dir():
        print(json.dumps({"status": "error", "message": f"not a directory: {d}"}))
        return 1

    if a.cmd == "open":
        items = open_items(build_ledger(collect(d, a.counterpart)), a.today)
        if a.format == "json":
            print(json.dumps({"status": "ok", "open": items}, ensure_ascii=False, indent=2))
        else:
            sys.stdout.write(_fmt(items, "Open commitments"))
        return 0

    res = since(d, Path(a.note), a.counterpart, a.today)
    if a.format == "json":
        print(json.dumps({"status": "ok", **res}, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(_fmt(res["done"], "Done since last call"))
        sys.stdout.write(_fmt(res["carried"], "Carried"))
        sys.stdout.write(_fmt(res["unmentioned"], "UNMENTIONED (silently dropped)"))
        sys.stdout.write(_fmt(res["new_this_call"], "New this call"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
