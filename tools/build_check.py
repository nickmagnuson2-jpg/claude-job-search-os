#!/usr/bin/env python3
"""build_check.py -- run the SCRIPT checks over every BUILD-LOG.md and report the drain flags.

WHY THIS EXISTS
---------------
The artifact workflow (framework/artifact-workflow.md) labels
more than fifteen checks SCRIPT and named no command for any of them, so none ran. This is
that command. It reads each `output/**/BUILD-LOG.md` written on the section 5 template and
reports, as data, what a person would otherwise have to remember to look at:

  waived                     a `waived: nick YYYY-MM-DD` line: Nick says this is not a build for
                             a reader, so no other check runs on it
  incomplete                 a Phase 0 field this variant's tier requires is blank, or the
                             log predates the v2 header and carries no tier fields at all
  expired                    past need_expires and nothing sent
  check analytics and Gmail  sent 3 days or more ago, no `opened` and no `reply` recorded
  drain due                  an `outcome:` pointer, or send_date 14 days or more ago, and no
                             `drained:` line
  workflow edit due          the Drain block incremented a ledger row that now has
                             Occurrences >= 2 and Promoted = No, and no workflow_edit_todo

Failed checks (`failed_checks`) are the per-log SCRIPT checks of sections 2, 3 and 5.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/build_check.py --repo-root . --json
  PYTHONIOENCODING=utf-8 python3 tools/build_check.py --repo-root . --open output/<slug>/BUILD-LOG.md

--json always exits 0 (it is a /standup probe; the flags are the data). --open exits 1 when
the log has a flag or a failed check, 0 when it is clean.

The JSON carries a `reach` line from tools/count_report.py: logs found, open, drained, and
the folders the walk pruned.

ZERO LOGS is reported as a warning, never as "0 builds": no log is not evidence of no build,
because a build started without a log is invisible to this check.

parse_log() is the one parser for the template. tools/reader_review.py --gate imports it, and
tools/check_build_log_present.py imports ask_state(), so the hook and this report read
`reader_ask` and `waived:` the same way.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from tools.count_report import format_line  # noqa: E402
from tools.md_table import find_section, is_sep_row, parse_cols, read_file  # noqa: E402

SKIP_DIRS = {"node_modules", ".next", ".vercel", ".git"}

# Filled means a quoted value that is not the template placeholder. Read anywhere in the
# file, not only under Phase 0, so a pre-template log can carry it.
READER_ASK = re.compile(r'^\s*reader_ask:\s*"(?!<verbatim>)[^"\s][^"]*"', re.M)
WAIVED = re.compile(r"^\s*waived:\s*nick\s+\d{4}-\d{2}-\d{2}", re.M)
# The reach line (tools/count_report.py): os.walk, so the gitignored output/ tree IS read.
REACH_SCOPE = ("BUILD-LOG.md files under output/, recursive by os.walk;"
               " node_modules, .next, .vercel and .git pruned")
DEFAULT_IDS = {f"D{i}" for i in range(1, 7)}
VARIANTS = {"V1", "V2", "V3", "new"}

# Phase 0 fields each tier requires (section 2, "Tiers"). V1 fills every row; V2 every row
# except not_a_copy; V3 the seven core rows. `new` has no tier yet, so it fills every row.
ALL_FIELDS = ("filled_by", "route", "reader", "reader_ask", "external_stake", "time_box",
              "value_item", "proof_purpose", "not_a_copy", "prior_outcomes", "prediction")
TIER_FIELDS = {
    "V1": ALL_FIELDS,
    "new": ALL_FIELDS,
    "V2": tuple(f for f in ALL_FIELDS if f != "not_a_copy"),
    "V3": tuple(f for f in ALL_FIELDS if f not in ("not_a_copy", "prior_outcomes")),
}

# Steps whose Entries feed a budget or an order check (section 3).
STEP_BRIEF, STEP_RESEARCH, STEP_DRAFT, STEP_DESIGN, STEP_REVIEW = 1, 2, 4, 5, 6
EDIT_STEPS = (STEP_DRAFT, STEP_DESIGN)

SKIPPED_CHECKS = [
    "brief file's saved: timestamp precedes the first draft Entry (the template has no saved: field;"
    " checked instead as the Step 1 Entry preceding the first Step 4 Entry)",
    "timed V2 under two hours fills four fields only (the template does not say a V2 is timed)",
    "per-default outcomes joined to the outreach-log row (not joined yet; run counts per "
    "default are reported)",
]

# A key starts a line or follows two or more spaces: `reader: ...   channel: ...`.
_KEY_RE = re.compile(r"(?:^|(?<=\s{2}))([A-Za-z][A-Za-z_-]*(?: [a-z][a-z_-]*)?(?: \(V\d\))?):(?=\s|$)")
# `yes/no` is a placeholder only as the unanswered end of a line (`...shows it: yes/no`);
# inside prose ("advance, yes/no, by <date>") it is a filled value.
_PLACEHOLDER_RE = re.compile(r"<[^>]*>|YYYY|\byes/no\s*$")
_CHOICE_RE = re.compile(r"\w+(?:\s*[|/]\s*\w+)+")
_ENTRY_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?\S*\s*\|\s*(.*)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_blank(value) -> bool:
    """True for an empty value or one still holding the template's placeholder text."""
    v = (value or "").strip()
    return (not v or bool(_PLACEHOLDER_RE.search(v)) or bool(_CHOICE_RE.fullmatch(v))
            or v.lower() == "pending")


def parse_kv(line: str) -> dict[str, str]:
    """`a: x   b: y` -> {"a": "x", "b": "y"}. Keys are lower-cased; `(V1)` is dropped."""
    out = {}
    matches = list(_KEY_RE.finditer(line))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        key = re.sub(r" \(V\d\)$", "", m.group(1)).lower()
        out[key] = line[m.end():end].strip()
    return out


def parse_time_box(value: str) -> dict[str, str]:
    """`signal "x" | need_expires 2026-01-02 | research 3 | ...` -> {name: value}."""
    out = {}
    for seg in (value or "").split("|"):
        seg = seg.strip()
        for name in ("need_expires", "research", "design", "review loops", "send_date", "signal"):
            if seg.startswith(name):
                out[name] = seg[len(name):].strip()
    return out


def _date(value):
    m = _DATE_RE.search(value or "")
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(0))
    except ValueError:
        return None


def _hours(value):
    """`1:30` -> 1.5, `2` or `2h` -> 2.0, anything else (`not timed`, a date) -> None."""
    v = (value or "").strip()
    m = re.fullmatch(r"(\d+):(\d{2})", v)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*h?", v)
    return float(m.group(1)) if m else None


def parse_entry(heading: str):
    """`### 2026-01-02 10:00 | Step 4: draft | duration 1:30 | who: claude` -> dict, or None."""
    m = _ENTRY_RE.match(heading)
    if not m:
        return None
    parts = [p.strip() for p in m.group(3).split("|")]
    step = re.match(r"Step\s+(\d+)", parts[0])
    entry = {"date": m.group(1), "time": m.group(2) or "", "step": int(step.group(1)) if step else None,
             "what": parts[0], "duration": None, "who": None}
    for p in parts[1:]:
        if p.startswith("duration"):
            entry["duration"] = _hours(p[len("duration"):])
        elif p.startswith("who:"):
            entry["who"] = p[len("who:"):].strip() or None
    return entry


def _section_lines(lines, title):
    start, end = find_section(lines, rf"^## {re.escape(title)}\b")
    return lines[start + 1:end] if start != -1 else None


def _kv_block(block) -> dict[str, str]:
    out = {}
    for ln in block or []:
        out.update(parse_kv(ln))
    return out


def ask_state(text: str) -> str:
    """`waived`, `filled` or `missing` for one BUILD-LOG's text. A waiver wins.

    A log on the template is read from its Phase 0 block only, so a filled-looking line
    elsewhere (an Entry quoting an ask) cannot stand in for a placeholder in Phase 0
    (round-12 review, 703.F1). A pre-template log has no Phase 0 block and is read whole."""
    if WAIVED.search(text):
        return "waived"
    m = re.search(r"^## Phase 0\b.*?(?=^## |\Z)", text, re.M | re.S)
    return "filled" if READER_ASK.search(m.group(0) if m else text) else "missing"


def parse_log(text: str) -> dict:
    """The one parser for the BUILD-LOG template (section 5). Never raises on old logs."""
    lines = text.splitlines()
    header = []
    for ln in lines:
        if ln.startswith("## "):
            break
        header.append(ln)
    head = _kv_block(header)
    phase0 = _section_lines(lines, "Phase 0")
    entries_block = _section_lines(lines, "Entries") or []
    log = {
        "format": "v2" if ("variant" in head and phase0 is not None) else "pre-v2",
        "variant": (head.get("variant") or "").split()[0] if head.get("variant") else "",
        "adapted_from": head.get("adapted_from", ""),
        "fields": _kv_block(phase0),
        "entries": [e for e in (parse_entry(ln) for ln in entries_block) if e],
        "ship": _kv_block(_section_lines(lines, "Ship")),
        "after_send": _kv_block(_section_lines(lines, "After send")),
        "drain": _kv_block(_section_lines(lines, "Drain")),
    }
    log["time_box"] = parse_time_box(log["fields"].get("time_box", ""))
    log["ask_state"] = ask_state(text)
    return log


def read_log(path: Path) -> dict:
    return parse_log(read_file(Path(path)))


def find_logs(repo: Path) -> list[Path]:
    """Every output/**/BUILD-LOG.md, skipping build and dependency folders."""
    out = []
    for root, dirs, files in os.walk(repo / "output"):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if "BUILD-LOG.md" in files:
            out.append(Path(root) / "BUILD-LOG.md")
    return sorted(out)


def ledger_rows(repo: Path) -> list[dict]:
    """`[artifact]` rows of memory/lessons.md Section 2 with their Occurrences and Promoted."""
    lines = read_file(repo / "memory" / "lessons.md").splitlines()
    start, end = find_section(lines, r"^## Section 2\b")
    if start == -1:
        return []
    header, rows = None, []
    for ln in lines[start + 1:end]:
        if not ln.startswith("|") or is_sep_row(ln):
            continue
        cols = parse_cols(ln)
        if header is None:
            header = [c.lower() for c in cols]
        elif "[artifact]" in ln:
            rows.append({**dict(zip(header, cols)), "_text": ln})
    return rows


def _pointer_ok(repo: Path, value: str, rel: str) -> bool:
    m = re.fullmatch(re.escape(rel) + r":(\d+)", (value or "").strip())
    if not m:
        return False
    n = int(m.group(1))
    return 1 <= n <= len(read_file(repo / rel).splitlines())


def _row_names_reader(repo: Path, value: str, reader: str) -> bool:
    """The pointed-to line is an outreach-log data row that carries the reader's first name."""
    n = int(re.search(r":(\d+)$", value.strip()).group(1))
    row = read_file(repo / "data" / "outreach-log.md").splitlines()[n - 1]
    first = re.split(r"[\s,;(]+", reader.strip())[0] if reader.strip() else ""
    return bool(re.match(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|", row)) and (
        not first or re.search(rf"(?<![a-z]){re.escape(first.lower())}(?![a-z])", row.lower())
        is not None)


def check_log(log: dict, today: dt.date, repo: Path) -> dict:
    """Flags, failed checks and missing fields for one parsed log."""
    flags, failed, missing = [], [], []
    if log.get("ask_state") == "waived":
        return {"flags": ["waived"], "failed_checks": [], "missing_fields": []}
    if log["format"] != "v2":
        return {"flags": ["incomplete"], "failed_checks": [],
                "missing_fields": ["v2 header (Variant: line and Phase 0 block)"]}
    f, tb, ship, after, drain = (log["fields"], log["time_box"], log["ship"],
                                 log["after_send"], log["drain"])
    variant = log["variant"]
    if variant not in VARIANTS:
        missing.append("Variant (V1, V2, V3 or new)")
    missing += [k for k in TIER_FIELDS.get(variant, ALL_FIELDS) if is_blank(f.get(k))]
    if missing:
        flags.append("incomplete")

    need, send = _date(tb.get("need_expires")), _date(tb.get("send_date"))
    sent = not is_blank(ship.get("sent"))
    if need and send and send > need:
        failed.append(f"send_date {send} is after need_expires {need}")
    if need and today > need and not sent:
        flags.append("expired")

    entries = log["entries"]
    for e in entries:
        where = f"Entry {' '.join(filter(None, (e['date'], e['time'])))} '{e['what']}'"
        if e["duration"] is None:
            failed.append(f"{where}: duration missing or not h:mm")
        if not e["who"]:
            failed.append(f"{where}: who missing")
    for name, step in (("research", STEP_RESEARCH), ("design", STEP_DESIGN)):
        budget = _hours(tb.get(name))
        used = sum(e["duration"] or 0 for e in entries if e["step"] == step)
        if budget is not None and used > budget:
            failed.append(f"{name} hours {used:g} over the budget of {budget:g}")
    loops = _hours(tb.get("review loops"))
    reviews = [i for i, e in enumerate(entries) if e["step"] == STEP_REVIEW]
    if loops is not None and len(reviews) > loops:
        failed.append(f"review loops {len(reviews)} over the budget of {loops:g}")
    briefs = [i for i, e in enumerate(entries) if e["step"] == STEP_BRIEF]
    drafts = [i for i, e in enumerate(entries) if e["step"] == STEP_DRAFT]
    if drafts and (not briefs or briefs[0] > drafts[0]):
        failed.append("no Step 1 (brief) Entry before the first Step 4 (draft) Entry")
    edits = [i for i, e in enumerate(entries) if e["step"] in EDIT_STEPS]
    if edits and (sent or reviews) and (not reviews or reviews[-1] < edits[-1]):
        failed.append("no Step 6 (review) Entry after the last edit Entry")

    disp = ship.get("review_dispositions", "")
    if not is_blank(disp):
        open_p0 = re.search(r"(\d+)\s+open", disp)
        if open_p0 and int(open_p0.group(1)) > 0:
            failed.append(f"{open_p0.group(1)} P0 still open")
        signed = re.search(r"(\d+)\s+signed off", disp)
        if signed and int(signed.group(1)) > 0 and not re.search(r"nick \d{4}-\d{2}-\d{2}", disp):
            failed.append("a signed-off P0 has no 'nick YYYY-MM-DD' line")
    if sent:
        if (ship.get("recipient_equals_reader") or "").strip().lower() != "yes":
            failed.append("sent, but recipient_equals_reader is not yes")
        if not _pointer_ok(repo, ship.get("sent"), "data/outreach-log.md"):
            failed.append(f"sent: '{ship.get('sent')}' is not a line in data/outreach-log.md")
        elif not _row_names_reader(repo, ship.get("sent"), f.get("reader", "")):
            failed.append(f"sent: '{ship.get('sent')}' points at a row that does not name the "
                          "reader (a backdated row shifts later line numbers; re-point it)")
        rr = ship.get("reader_review", "")
        if is_blank(rr) or "no report written" in rr:
            failed.append("sent with no reader_review report in Ship (run tools/reader_review.py "
                          "--gate)")
    applied = defaults_applied(log)
    unknown = [d for d in applied if d not in DEFAULT_IDS]
    if unknown:
        failed.append(f"defaults_applied names unknown IDs: {', '.join(unknown)}")

    days = (today - send).days if send else None
    if (sent and days is not None and days >= 3
            and is_blank(after.get("opened")) and is_blank(after.get("reply"))):
        flags.append("check analytics and Gmail")
    outcome = bool(re.match(r"data/job-pipeline\.md:\d+", (after.get("outcome") or "").strip()))
    if (outcome or (days is not None and days >= 14)) and is_blank(drain.get("drained")):
        flags.append("drain due")
    if workflow_edit_due(log, repo):
        flags.append("workflow edit due")
    return {"flags": flags, "failed_checks": failed, "missing_fields": missing}


def defaults_applied(log: dict) -> list[str]:
    v = log["ship"].get("defaults_applied", "")
    if is_blank(v) or v.strip().lower() == "none":
        return []
    return [t for t in re.split(r"[,\s]+", v.strip()) if t]


def workflow_edit_due(log: dict, repo: Path) -> bool:
    drain = log["drain"]
    todo = drain.get("workflow_edit_todo", "")
    ids = re.findall(r"\bAR\d+\b", drain.get("ledger_rows", ""))
    if not ids or (not is_blank(todo) and todo.strip().lower() != "none"):
        return False
    for row in ledger_rows(repo):
        if not any(re.search(rf"\b{i}\b", row["_text"]) for i in ids):
            continue
        occ = re.match(r"\s*(\d+)", row.get("occurrences") or "")
        if occ and int(occ.group(1)) >= 2 and (row.get("promoted") or "").strip().lower().startswith("no"):
            return True
    return False


def summarize(path: Path, repo: Path, today: dt.date) -> dict:
    log = read_log(path)
    result = check_log(log, today, repo)
    send = _date(log["time_box"].get("send_date"))
    sent = not is_blank(log["ship"].get("sent"))
    steps = [e["step"] for e in log["entries"] if e["step"] is not None]
    try:
        rel = path.relative_to(repo).as_posix()
        slug = path.parent.relative_to(repo / "output").as_posix()
    except ValueError:      # an --open log outside output/
        rel, slug = str(path), path.parent.name
    return {
        "path": rel,
        "slug": slug,
        "format": log["format"],
        "variant": log["variant"],
        "step": steps[-1] if steps else None,
        "sent": sent,
        "sent_days_ago": (today - send).days if (sent and send) else None,
        "opened": "" if is_blank(log["after_send"].get("opened")) else log["after_send"]["opened"],
        "drained": not is_blank(log["drain"].get("drained")),
        "defaults_applied": defaults_applied(log),
        **result,
    }


def brief_line(builds: list[dict], warning: str) -> str:
    if warning:
        return warning
    open_ = [b for b in builds if not b["drained"]]
    parts = []
    for b in open_:
        sent = f"sent {b['sent_days_ago']}d ago" if b["sent_days_ago"] is not None else "not sent"
        parts.append(f"{b['slug']} ({b['variant'] or 'no variant'}, step {b['step']}, {sent}, "
                     f"opened: {b['opened'] or 'unknown'}, flags: {', '.join(b['flags']) or 'none'}, "
                     f"failed checks: {len(b.get('failed_checks') or [])})")
    return f"Builds: {len(open_)} open" + "".join(f" · {p}" for p in parts)


def report(repo: Path, today: dt.date) -> dict:
    builds = [summarize(p, repo, today) for p in find_logs(repo)]
    warning = ("" if builds else
               "WARNING: no output/**/BUILD-LOG.md found. This is not '0 builds': a build"
               " started without a log is invisible to this check.")
    runs = {}
    for b in builds:
        for d in b["defaults_applied"]:
            runs.setdefault(d, []).append(b["path"])
    open_n = sum(1 for b in builds if not b["drained"])
    reach = format_line(REACH_SCOPE, population=len(builds), included=open_n,
                        excluded={"drained": len(builds) - open_n})
    return {"today": today.isoformat(), "reach": reach, "logs": len(builds),
            "open": open_n, "warning": warning,
            "line": brief_line(builds, warning), "builds": builds,
            "default_runs": {d: {"runs": len(p), "logs": p} for d, p in sorted(runs.items())},
            "skipped_checks": SKIPPED_CHECKS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--today", default="", help="YYYY-MM-DD; defaults to today")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--json", action="store_true", help="every log, as one JSON object")
    mode.add_argument("--open", metavar="LOG", help="one BUILD-LOG.md, as text")
    a = ap.parse_args(argv)
    repo = Path(a.repo_root).resolve()
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    if a.json:
        print(json.dumps(report(repo, today), indent=1, ensure_ascii=False))
        return 0
    path = Path(a.open)
    path = path if path.is_absolute() else repo / path
    if not path.is_file():
        print(f"build_check: no such log: {path}", file=sys.stderr)
        return 2
    b = summarize(path.resolve(), repo, today)
    print(f"{b['path']}: {b['format']}, variant {b['variant'] or '(none)'}, step {b['step']}")
    print(f"flags: {', '.join(b['flags']) or 'none'}")
    for k in ("missing_fields", "failed_checks"):
        for item in b[k]:
            print(f"{k.replace('_', ' ')}: {item}")
    return 1 if (b["flags"] or b["failed_checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
