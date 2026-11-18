#!/usr/bin/env python3
"""morning_page.py - the plain morning page (D5): one static local HTML file for the laptop.

Plan: output/codify-ui-102726/03-plan.md section 4. Nick ruled "One plain page first" (D5):
used for a week on the laptop screen, then variants judged against what it lacked.

WHAT IT DOES. Renders one HTML file from sources that only read:
  * tools/standup_collect.py in READ-ONLY mode (no STATE-BLOCK render, no sent-check state)
  * tools/day_check.py, the same counts /check prints ("Done so far today")
  * tools/calendar_fetch.py, today and the next 3 days (calendar.readonly token)
  * the commonplace entries that have no take yet (one a day, rotating)
  * /standup's synthesis, if it wrote one: output/morning-page/claude-read-<date>.json,
    {"text": "..."}. The two never share a file. Not written by /standup yet, so the block
    says so until it is.

What it writes: the page (default output/morning-page/index.html), and at 19:00 or later
also a dated copy (output/morning-page/<date>.html) as the day's record. calendar_fetch
may refresh its own token file; nothing else is written.

Content, in Nick's order: 3 the funnel FIRST, full width (funnel-intake.md step 1, Nick
2026-11-17: "I haven't been getting shown the roles that are coming in"): this week's
applications against the target, sends, replies, live processes, watched companies, the
roles the latest scan passed with their scores, and EVERY role it cut, each with the key
tools/title_cut_ruling.py takes to record "should have shown". Then 1 must-dos, 2 waiting
on me, "Waiting on them" (the processes whose next move is theirs), 5 done so far today,
6 one commonplace prompt. The machine room (block 4) is off the page (same ruling).
Plus "Claude's read" and the First chair prompt.

Rules the tests hold it to:
  * The page embeds generated_at and shows a STALE banner when it is older than 30 minutes
    (checked in the browser, so a page the job stopped updating says so).
  * Live mode carries a fixed "LIVE, PRIVATE" band. Demo mode (--demo) reads only the
    files in tests/fixtures/morning_page/ and carries a "DEMO DATA" band instead.
  * Numbers come only from script output. "Claude's read" may hold no digit: a read that
    contains one is withheld, not shown.
  * Built for a glance at a laptop screen: large type, at most a few items per block.
    Sections grow to fit their content and the page scrolls; nothing is clipped or overlapped.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/morning_page.py [--repo-root .] [--out PATH] [--demo]
      [--now YYYY-MM-DDTHH:MM]

The launchd job (tools/launchd/com.nickmagnuson.jobsearch.morning-page.plist) runs it at
07:00 and every 15 minutes until 19:00.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import day_check  # noqa: E402
import funnel_state  # noqa: E402
import standup_collect  # noqa: E402
from stage_vocab import is_terminal_stage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "morning_page"
OUT_REL = Path("output") / "morning-page"
STALE_MINUTES = 30
ARCHIVE_HOUR = 19
MAX_ITEMS = 4
TAKE_DIRS = ("data/source-articles", "data/source-emails")
LIVE_BAND = "LIVE, PRIVATE"
DEMO_BAND = "DEMO DATA"


# --------------------------------------------------------------------------- sources

def _data(collector: dict, name: str):
    r = (collector.get("results") or {}).get(name) or {}
    return r.get("data")


def calendar_events(root: Path, days: int = 4) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, "tools/calendar_fetch.py", "--days", str(days)],
                           cwd=str(root), capture_output=True, text=True, timeout=60, env=env)
    except (subprocess.SubprocessError, OSError) as e:
        return {"error": f"calendar_fetch could not run: {e}"}
    if p.returncode != 0:
        return {"error": (p.stderr.strip().splitlines() or ["calendar_fetch failed"])[-1][:200]}
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": "calendar_fetch printed no JSON"}


def empty_take(text: str) -> bool:
    """True when the `## My take` section holds no line of Nick's (a blockquote line)."""
    m = re.search(r"^## My take.*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return False
    return not re.search(r"^>\s*[^\s<]", m.group(1), re.M)


def commonplace(root: Path, today: date) -> dict:
    waiting = []
    for rel in TAKE_DIRS:
        d = root / rel
        for f in sorted(d.glob("*.md")):  # a missing folder globs to nothing
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            if empty_take(text):
                h = re.search(r"^# (.+)$", text, re.M)
                waiting.append({"title": (h.group(1) if h else f.stem)[:140],
                                "path": f"{rel}/{f.name}"})
    if not waiting:
        return {"waiting": 0, "pick": None}
    # One a day, rotating: a skipped day does not pile up, the next day shows the next one.
    return {"waiting": len(waiting), "pick": waiting[today.toordinal() % len(waiting)]}


def claude_read(root: Path, today: date) -> dict:
    p = root / OUT_REL / f"claude-read-{today.isoformat()}.json"
    try:
        text = json.loads(p.read_text(encoding="utf-8")).get("text", "")
    except FileNotFoundError:
        return {"text": None, "note": "No read from /standup today."}
    except (OSError, ValueError, AttributeError):
        return {"text": None, "note": "The /standup read could not be parsed."}
    return {"text": str(text), "note": None}


# The next move is the OTHER side's: an awaited reply or read, a call they booked, a wait
# after an interview. Matched in the Next Action cell, or in Stage when that cell is empty.
# "scheduled" alone is not theirs: "to be scheduled" is Nick's move, and a booked call is on
# the calendar block already (D5 review 838.F3, 842.F1).
# "<Name> will reply" needs a named other person: "Will follow up Monday" and "I will send"
# are Nick's own moves (D5 review 836.F1).
THEIRS_CI = re.compile(
    r"^(await|wait\b|waiting\b|ball (is )?on\b|pending (their|his|her)\b|their move)"
    r"|\bawaiting\b|\bshe calls\b|\bhe calls\b|\bthey call\b",
    re.I)
# Case-SENSITIVE on purpose: under re.I, [A-Z] matched "team", "they" and "Need" as names
# (848.F2, 852.F2). A capitalised name other than I/Nick/We/Need, then the verb.
THEIRS_NAMED = re.compile(
    r"\b(?!(?:I|Nick|We|Need|Must|Should|Then|Also)\b)[A-Z][\w'-]* "
    r"(?:will (?:reply|respond|send|call|follow up|let)|to (?:reply|respond) by|is to reply)\b")


class _Theirs:
    """THEIRS.search(text): the other side's move, by either pattern."""
    @staticmethod
    def search(text):
        return THEIRS_CI.search(text) or THEIRS_NAMED.search(text)


THEIRS = _Theirs()


def waiting_on_them(root: Path) -> dict:
    """9e (feedback_entity_identifier_drift_breaks_reconciliation, 2026-10-29): every open
    pipeline process whose next action belongs to the other side. Before this they showed
    only as a number in the snapshot. Read from data/job-pipeline.md, read-only; terminal
    rows (stage_vocab) are left out. Stalled rows are not left out: a stall where the ball
    is theirs is still theirs, and the row says how long it has sat."""
    text, err = day_check._read(root / "data" / "job-pipeline.md")
    if text is None:
        return {"error": err, "rows": None}
    rows = []
    for cols in day_check.pipeline_rows(text):
        if len(cols) < 5:  # the header and separator rows match nothing below
            continue
        company, stage, updated, nxt = cols[0], cols[2], cols[3], cols[4]
        if is_terminal_stage(stage):
            continue
        has_next = nxt not in ("", "—", "-")
        # The Next Action cell decides when there is one: a row whose stage says "awaiting
        # read" but whose next action is Nick's ("Send deck to ...") is his move.
        if THEIRS.search(nxt if has_next else stage):
            rows.append({"company": company, "next": (nxt if has_next else stage)[:90],
                         "updated": updated})
    return {"error": None, "rows": rows}


def gather_live(root: Path, now: datetime) -> dict:
    today = now.date()
    return {
        "collector": standup_collect.collect(root, today.isoformat(), read_only=True),
        "day": day_check.collect(root, today),
        "calendar": calendar_events(root),
        "commonplace": commonplace(root, today),
        "claude_read": claude_read(root, today),
        "waiting_on_them": waiting_on_them(root),
        "funnel": funnel_state.collect(root, now.astimezone()),
    }


SOURCES = ("collector", "day", "calendar", "commonplace", "claude_read", "waiting_on_them",
           "funnel")


def gather_demo(fixtures: Path = FIXTURES, today: date | None = None) -> dict:
    out = {}
    for name in SOURCES:
        out[name] = json.loads((fixtures / f"{name}.json").read_text(encoding="utf-8"))
    if today is not None:
        # The fixtures are dated to the collector's target_date. Move the calendar by the same
        # number of days so --demo on any day shows the sample events. Only the calendar is
        # compared against today; todo ages are stored as day counts.
        shift = today - date.fromisoformat(out["collector"]["target_date"])
        for ev in out["calendar"].get("events") or []:
            for k in ("start", "end"):
                ev[k] = (date.fromisoformat(ev[k][:10]) + shift).isoformat() + ev[k][10:]
    return out


# --------------------------------------------------------------------------- view

def _event_day(ev: dict) -> date | None:
    # fromisoformat reads both "2026-01-07" (all-day) and "2026-01-07T10:00:00".
    try:
        return datetime.fromisoformat(str(ev.get("start", ""))[:19]).date()
    except ValueError:
        return None


def _event_line(ev: dict) -> str:
    start = str(ev.get("start", ""))
    when = "all day" if ev.get("all_day") else start[11:16]
    return f"{when}  {ev.get('summary') or '(no title)'}"


RULE_CMD = ('PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py mark --key KEY '
            '--reason "why it should have shown"')


def funnel_view(f: dict, day: dict) -> dict:
    """The funnel section's content (funnel-intake.md step 1). Every number is read from
    funnel_state.collect or day_check.collect; nothing is counted here except list lengths."""
    counts = day.get("counts") or {}
    target = (day.get("targets") or {}).get("applications")
    week = {k: (counts.get(k) or {}).get("week") for k in ("applications", "sends", "replies")}

    rulings = f.get("rulings") or {}
    # Only Nick's rulings are the R1 log (funnel-intake.md, Autonomy ladder): only they are
    # counted, listed, and mark a cut. Entries by anyone else are counted apart, never mixed.
    all_rulings = rulings.get("rulings")
    nick = None if all_rulings is None else [r for r in all_rulings if _by(r) == ""]
    # Joined on the full posting identity (Fable F4); a role_key that is not text is
    # skipped, never used as a dict key (Codex/Grok F1, round 2).
    by_key = {r["role_key"]: r for r in (nick or []) if isinstance(r.get("role_key"), str)}
    cuts = f.get("cuts") or {}
    rows = None
    if cuts.get("cuts") is not None:
        rows = [{"key": c.get("key", ""), "company": c.get("company") or "?",
                 "title": c.get("title", ""), "url": c.get("url", ""),
                 "reason": c.get("reason", ""), "location": c.get("location", ""),
                 "rule": c.get("rule", ""), "history": c.get("history") or "",
                 "review": bool(c.get("review")), "new": bool(c.get("new")),
                 "ruling": by_key.get(c.get("role_key"))} for c in cuts["cuts"]]
    groups = None
    if rows is not None:
        # The full list is grouped by company, never by the reason text: the reason strings
        # belong to the title gate and change with it (title-gate-1118). Shown as given.
        g = {}
        for r in rows:
            g.setdefault(r["company"], []).append(r)
        for rs in g.values():  # ruled rows first, so a ruling is seen next to its role
            rs.sort(key=lambda r: r["ruling"] is None)
        groups = sorted(g.items(), key=lambda kv: (-len(kv[1]), kv[0].casefold()))

    scan = f.get("scan") or {}
    age = scan.get("age_hours")
    late = scan.get("late_after_hours")
    return {
        "applications_week": week["applications"],
        "applications_target": target.get("target") if target else None,
        "sends_week": week["sends"], "replies_week": week["replies"],
        "scan_at": scan.get("scanned_at"), "scan_age_hours": age,
        "scan_late": age is not None and late is not None and age > late,
        "cuts_error": cuts.get("error") if cuts else "not read",
        "cut_count": None if rows is None else len(rows),
        # The two-minute list: cuts new in this scan and worth a look.
        "to_look": None if rows is None else [r for r in rows if r["review"] and r["new"]],
        "review_total": None if rows is None else sum(r["review"] for r in rows),
        "scans_error": f.get("scans_error"),
        "fetch_failures": f.get("fetch_failures"),
        "cut_groups": groups,
        "rulings": list(reversed(nick or []))[:MAX_ITEMS],
        "ruling_count": None if nick is None else len(nick),
        "other_entries": 0 if all_rulings is None else len(all_rulings) - len(nick),
        "rulings_error": rulings.get("error"),
        "rulings_bad_lines": rulings.get("bad_lines") or 0,
        "passed": f.get("passed") or {"error": "not read", "roles": None},
        "watched": f.get("watched") or {"error": "not read"},
        "live": f.get("live") or {"error": "not read", "rows": None},
    }


def build_view(src: dict, now: datetime) -> dict:
    today = now.date()
    col = src.get("collector") or {}
    cal = src.get("calendar") or {}

    # 1. Must-dos: calendar first, kept apart from follow-up due dates, then top todos.
    events = cal.get("events") if isinstance(cal, dict) else None
    if events is None:
        cal_today, cal_soon = None, None
    else:
        cal_today = [_event_line(e) for e in events if _event_day(e) == today]
        cal_soon = [f"{_event_day(e).strftime('%a')}  {_event_line(e)}" for e in events
                    if _event_day(e) and today < _event_day(e) <= today + timedelta(days=3)]
    todos = []
    for t in ((_data(col, "todos_summary") or {}).get("top_n") or [])[:MAX_ITEMS]:
        d = t.get("days_until_due")
        age = ("due today" if d == 0 else f"{-d}d overdue" if isinstance(d, int) and d < 0
               else f"in {d}d" if isinstance(d, int) else "no date")
        # The tag leads: long tasks are clamped to two lines and must not lose it.
        todos.append(f"({age}) {t.get('task', '')[:90]}")

    # 2. Waiting on me.
    nf = _data(col, "networking_followup") or {}
    op = _data(col, "outreach_pending") or {}
    sa = _data(col, "sent_artifacts") or {}
    cp = src.get("commonplace") or {}
    waiting = {
        "follow-ups overdue": (nf.get("summary") or {}).get("overdue"),
        "follow-ups due today": (nf.get("summary") or {}).get("due_today"),
        "sent, no reply, past due": len(op["awaiting_response_overdue"])
        if isinstance(op.get("awaiting_response_overdue"), list) else None,
        "sent mail to check": len(sa["flags"]) if isinstance(sa.get("flags"), list) else None,
        "commonplace takes empty": cp.get("waiting"),
    }
    names = [f"{x.get('name', '')}, {x.get('follow_up_action', '')}"[:80]
             for x in (nf.get("followup_overdue") or [])[:MAX_ITEMS - 1]]

    funnel = funnel_view(src.get("funnel") or {}, src.get("day") or {})
    sources_failed = list(col.get("failed") or [])

    read = src.get("claude_read") or {}
    text = read.get("text")
    if text and re.search(r"\d", text):
        read = {"text": None, "note": "Withheld: the read contained a number. Numbers come "
                                      "only from the scripts."}

    return {
        "date": today.isoformat(), "weekday": today.strftime("%A"),
        "calendar_error": cal.get("error") if isinstance(cal, dict) else None,
        "cal_today": cal_today, "cal_soon": cal_soon, "todos": todos,
        "waiting": waiting, "waiting_names": names,
        "funnel": funnel, "sources_failed": sources_failed,
        "day": src.get("day") or {},
        "commonplace": cp.get("pick"),
        "claude_read": read,
        "waiting_on_them": src.get("waiting_on_them") or {"error": "not read", "rows": None},
    }


# --------------------------------------------------------------------------- html

def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _n(v) -> str:
    return "unknown" if v is None else _e(v)


def _list(items, empty: str) -> str:
    if items is None:
        return f'<p class="muted">{_e(empty)}</p>'
    if not items:
        return '<p class="muted">Nothing.</p>'
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items[:MAX_ITEMS]) + "</ul>"


CSS = """
:root{--bg:#f6f4ef;--card:#ffffff;--ink:#1d1d1b;--muted:#6b6862;--line:#dedad2;
--live:#8a1c1c;--demo:#1c4f8a;--stale:#b26a00;--ok:#2f6b3a;--warn:#8a1c1c}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161614;
--card:#20201d;--ink:#eceae4;--muted:#a19d95;--line:#34332f;--live:#e07a7a;--demo:#7aa7e0;
--stale:#e0a84a;--ok:#7ac08a;--warn:#e07a7a}}
:root[data-theme="dark"]{--bg:#161614;--card:#20201d;--ink:#eceae4;--muted:#a19d95;
--line:#34332f;--live:#e07a7a;--demo:#7aa7e0;--stale:#e0a84a;--ok:#7ac08a;--warn:#e07a7a}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font:20px/1.35 -apple-system,"Helvetica Neue",
Helvetica,Arial,sans-serif}
.band{font-weight:700;letter-spacing:.08em;text-align:center;padding:4px;color:#fff}
.band.live{background:var(--live)}.band.demo{background:var(--demo)}
#stale{display:none;background:var(--stale);color:#000;text-align:center;font-weight:700;padding:6px}
header{display:flex;justify-content:space-between;align-items:baseline;padding:14px 32px 6px}
header h1{font-size:30px;margin:0;font-weight:700}header .gen{color:var(--muted);font-size:16px}
.read{margin:0 32px 10px;font-size:19px}
main{display:grid;grid-template-columns:repeat(3,1fr);column-gap:40px;row-gap:18px;
padding:0 32px 20px}
section{border-top:2px solid var(--ink);padding:8px 0 0}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;margin:0 0 8px}
h3{font-size:16px;margin:10px 0 2px;color:var(--muted);font-weight:600}
ul{margin:0}li{margin:2px 0}p{margin:4px 0}
.muted{color:var(--muted)}table{border-collapse:collapse;width:100%}
td,th{padding:3px 0;vertical-align:top}td.n,th.n{text-align:right;white-space:nowrap;padding-left:14px;
font-variant-numeric:tabular-nums}td.n{font-weight:700}
th{font-size:14px;font-weight:600;color:var(--muted);text-align:left;border-bottom:1px solid var(--line)}
td.n .muted{font-weight:400}
li{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
ul{list-style:none;padding-left:0}li{padding-left:14px;text-indent:-14px}li::before{content:"\\2022\\00a0\\00a0"}
.ok{color:var(--ok)}.warn{color:var(--warn)}
.funnel{grid-column:1/-1}.fgrid2{display:grid;grid-template-columns:3fr 2fr;column-gap:40px}
ul.cuts{columns:2;column-gap:40px}ul.cuts li{break-inside:avoid}
code{font:15px/1.3 ui-monospace,Menlo,monospace}summary{cursor:pointer;font-weight:600;margin-top:6px}
.ruled{color:var(--ok);font-weight:700}.funnel li{display:block}
#sources-failed{margin:0 32px 20px}
@media (max-width:900px){main{grid-template-columns:1fr;padding:0 16px 16px}
.fgrid2{grid-template-columns:1fr}ul.cuts{columns:1}#sources-failed{margin:0 16px 16px}
header{padding:10px 16px 4px}.read{margin:0 16px 8px}}
"""

STALE_JS = """
(function(){var g=Date.parse(document.body.dataset.generatedAt);var lim=%d*60*1000;
function chk(){var el=document.getElementById('stale');if(isNaN(g)||Date.now()-g>lim){
el.style.display='block';el.textContent='STALE: this page was generated at '+
document.body.dataset.generatedAt+' and has not updated since.';}}chk();setInterval(chk,60000);})();
""" % STALE_MINUTES


def _when(iso) -> str:
    """'Tue Nov 17, 8:30 AM' in local time, or the raw value when it does not parse."""
    try:
        t = datetime.fromisoformat(str(iso)).astimezone()
    except ValueError:
        return str(iso)
    return f"{t.strftime('%a %b')} {t.day}, {t.strftime('%I:%M %p').lstrip('0')}"


def _link(url, text) -> str:
    u = str(url or "")
    if u.startswith(("https://", "http://")):
        return f'<a href="{_e(u)}" target="_blank" rel="noopener">{_e(text)}</a>'
    return _e(text)


def _by(ruling: dict) -> str:
    """'' for Nick's own ruling; ' (by claude)' otherwise. Only Nick's rulings earn R1
    (funnel-intake.md, Autonomy ladder), so a ruling Claude made must not read as his."""
    by = str(ruling.get("by") or "nick")
    return "" if by == "nick" else f" (by {_e(by)})"


def _cut_li(r: dict, with_company: bool) -> str:
    note = [r["reason"]]
    if r["rule"] == "location" and r["location"]:
        note.append(r["location"])
    note.append(r["history"])  # empty parts are dropped by the join below
    ruled = (f' <span class="ruled">RULED should have shown: {_e(r["ruling"].get("reason"))}</span>'
             if r["ruling"] else "")
    name = f'{r["company"]}: {r["title"]}' if with_company else r["title"]
    return (f'<li><code>{_e(r["key"])}</code> {_link(r["url"], name)} '
            f'<span class="muted">({_e("; ".join(n for n in note if n))})</span>{ruled}</li>')


def render_funnel(f: dict) -> str:
    """The funnel section (funnel-intake.md step 1). First the roles to act on: new cuts
    worth a look and the roles that passed, each linked to its posting. Then the week and
    the live processes. Then the rulings and every cut, one click away."""
    when = _e(_when(f["scan_at"])) if f["scan_at"] else "unknown"
    # 1. New cuts worth a look.
    if f["to_look"] is None:
        look = f'<h3>Cut by the scan</h3><p class="warn">Cuts unavailable: {_e(f["cuts_error"])}</p>'
    else:
        look = (f'<h3 id="cuts-head">Cut by the scan of {when}, new, in a location the scan accepts, '
                f'cut only because the title is not on the list: {len(f["to_look"])}</h3>')
        look += ("<ul>" + "".join(_cut_li(r, True) for r in f["to_look"]) + "</ul>"
                 if f["to_look"] else '<p class="muted">None new in this scan.</p>')
        look += ('<p class="muted">Should one have shown? Run <code>' + _e(RULE_CMD)
                 + '</code> in the repo, KEY from the row.</p>')
    if f["scan_late"]:
        look += (f'<p class="warn">The scan has not run since {when} '
                 f'({f["scan_age_hours"]:g} hours ago); it runs daily at 8:30.</p>')
    if f.get("fetch_failures"):
        look += (f'<p class="warn">{f["fetch_failures"]} boards failed to fetch in this scan; '
                 "their roles are missing from both lists.</p>")
    if f["scans_error"]:
        look += f'<p class="warn">{_e(f["scans_error"])}</p>'

    # 2. Roles that passed.
    ps = f["passed"]
    if ps.get("roles") is None:
        passed = f'<h3>Passed the scan</h3><p class="muted">{_e(ps.get("error"))}</p>'
    else:
        items = "".join(
            f'<li><strong>{_n(r.get("score"))}</strong> '
            + _link(r.get("apply_url") or r.get("url"), f'{r.get("company")}: {r.get("title")}')
            + (f' <span class="muted">({_e(r["history"])})</span>' if r.get("history") else "")
            + "</li>" for r in ps["roles"])
        passed = (f'<h3>Passed the title list, unread or still open, by score: {len(ps["roles"])}</h3>'
                  + (f"<ul>{items}</ul>" if items else '<p class="muted">Nothing passed.</p>')
                  + (f'<p class="warn">plus {ps["unlisted"]} more standing roles the queue does '
                     "not store</p>" if ps.get("unlisted") else "")
                  + ('<p class="warn">goals.md is stale, so the scan scored nothing: scores '
                     "show as unknown.</p>" if ps.get("goals_stale") else ""))

    # 3. The week, watched companies, live processes.
    app, tgt = f["applications_week"], f["applications_target"]
    cls = " warn" if isinstance(app, int) and isinstance(tgt, int) and app < tgt else ""
    app_cell = _n(app) + (f' <span class="muted">/ {_e(tgt)}</span>' if tgt is not None else "")
    w, lv = f["watched"], f["live"]
    rows = [f'<tr><td>Applications this week</td><td class="n{cls}">{app_cell}</td></tr>',
            f'<tr><td>Sends this week</td><td class="n">{_n(f["sends_week"])}</td></tr>',
            f'<tr><td>Replies this week</td><td class="n">{_n(f["replies_week"])}</td></tr>',
            '<tr><td>Live processes</td><td class="n">'
            + ("unknown" if lv.get("rows") is None else str(len(lv["rows"]))) + "</td></tr>"]
    if w.get("error"):
        rows.append('<tr><td>Watched companies</td><td class="n">unknown</td></tr>')
    else:
        rows.append(f'<tr><td>Watched companies</td><td class="n">{_n(w.get("count"))}</td></tr>')
        if w.get("open") is not None:
            rows.append('<tr><td class="muted">open / closed / never engaged</td><td class="n">'
                        f'{len(w["open"])} / {len(w["closed"])} / {len(w["never"])}</td></tr>')
        rows.append(f'<tr><td class="muted">list last grew</td><td class="n">'
                    f'{_n(w.get("last_grew"))}</td></tr>')
    week = "<h3>This week</h3><table>" + "".join(rows) + "</table>"
    if w.get("error"):
        week += f'<p class="muted">Watched list: {_e(w["error"])}</p>'
    if lv.get("rows") is None:
        live = f'<h3>Live processes</h3><p class="muted">Pipeline unavailable: {_e(lv.get("error"))}</p>'
    else:
        items = "".join(f'<li>{_e(r["company"])}: {_e(r["stage"])}</li>' for r in lv["rows"])
        live = ("<h3>Live processes</h3>" + (f"<ul>{items}</ul>" if items else '<p class="muted">None.</p>')
                + (f'<p class="muted">plus {lv["other_open"]} open rows not yet in a process</p>'
                   if lv.get("other_open") else ""))

    out = (f'<div class="fgrid2"><div>{look}</div><div>{passed}</div></div>'
           f'<div class="fgrid2"><div>{week}</div><div>{live}</div></div>')

    # 4. Rulings and every cut.
    if f["rulings_error"]:
        out += f'<p class="warn">Rulings log unreadable: {_e(f["rulings_error"])}</p>'
    else:
        out += f'<h3>Your rulings in the log: {_n(f["ruling_count"])}</h3>'
        if f["other_entries"]:
            out += (f'<p class="muted">{f["other_entries"]} entries in the log not made by you '
                    "are not counted and mark nothing.</p>")
        if f["rulings_bad_lines"]:
            out += f'<p class="warn">{f["rulings_bad_lines"]} unreadable lines in the log</p>'
        if f["rulings"]:
            out += "<ul>" + "".join(
                f'<li>{_e(r.get("company"))}: {_e(r.get("title"))}. {_e(r.get("reason"))} '
                f'<span class="muted">({_e(str(r.get("ruled_at", ""))[:10])})</span></li>'
                for r in f["rulings"]) + "</ul>"
    if f["cut_groups"] is not None:
        body = "".join(
            f'<h3>{_e(company)}: {len(rs)}</h3><ul class="cuts">'
            + "".join(_cut_li(r, False) for r in rs) + "</ul>"
            for company, rs in f["cut_groups"])
        out += (f'<details><summary>Every cut in the scan of {when}: {f["cut_count"]} '
                f'({f["review_total"]} in an accepted location and cut only by the title list)</summary>{body}</details>')
    return out


def render(view: dict, now: datetime, demo: bool) -> str:
    band = (f'<div class="band demo">{DEMO_BAND}</div>' if demo
            else f'<div class="band live">{LIVE_BAND}</div>')
    gen = now.isoformat(timespec="seconds")

    must = "<h3>Calendar today</h3>"
    if view["cal_today"] is None:
        must += f'<p class="muted">Calendar unavailable: {_e(view["calendar_error"])}</p>'
    else:
        must += _list(view["cal_today"], "")
        must += "<h3>Next 3 days</h3>" + _list(view["cal_soon"], "")
    must += "<h3>Top to-dos</h3>" + _list(view["todos"], "")

    rows = "".join(f'<tr><td>{_e(k)}</td><td class="n">{_n(v)}</td></tr>'
                   for k, v in view["waiting"].items())
    waiting = f"<table>{rows}</table>"
    if view["waiting_names"]:
        waiting += "<h3>Oldest follow-ups</h3>" + _list(view["waiting_names"], "")
    waiting += ('<p class="muted">Not wired yet: drafts staged, spines, Applied '
                'confirmations, debrief scores, P0 sign-offs, coaching decisions.</p>')

    funnel = render_funnel(view["funnel"])
    failed = (f'<p class="warn" id="sources-failed">Sources failed: '
              f'{_e(", ".join(view["sources_failed"]))}</p>' if view["sources_failed"] else "")

    day = view["day"]
    counts = day.get("counts") or {}
    targets = day.get("targets") or {}
    drows = ""
    for k in day_check.COUNTS:
        c = counts.get(k) or {}
        t = targets.get(k)
        tail = (f' <span class="muted">/ {_e(t["target"])} a {_e(t["period"])}</span>'
                if t else "")
        drows += (f'<tr><td>{_e(day_check.LABELS[k])}</td><td class="n">{_n(c.get("today"))}'
                  f'</td><td class="n">{_n(c.get("week"))}{tail}</td></tr>')
    done = ('<table><tr><th></th><th class="n">Today</th><th class="n">This week</th></tr>'
            f"{drows}</table>")

    pick = view["commonplace"]
    if pick:
        cp = (f'<p><strong>{_e(pick["title"])}</strong></p><p class="muted">{_e(pick["path"])}</p>'
              '<p>One line: agree, disagree, or so what?</p>')
    else:
        cp = '<p class="muted">No saved article is waiting for a take.</p>'
    cp += '<h3>First chair</h3><p>Practice today? Dictate one line.</p>'

    wt = view["waiting_on_them"]
    if wt.get("rows") is None:
        theirs = f'<p class="muted">Pipeline unavailable: {_e(wt.get("error"))}</p>'
    else:
        items = [f"{r['company']}: {r['next']}" for r in wt["rows"]]
        more = len(items) - MAX_ITEMS
        theirs = (_list(items, "")
                  + (f'<p class="muted">and {more} more</p>' if more > 0 else ""))

    read = view["claude_read"]
    read_html = _e(read["text"]) if read.get("text") else f'<span class="muted">{_e(read.get("note"))}</span>'
    try:
        d = date.fromisoformat(view["date"])
        heading = f"{view['weekday']}, {d.strftime('%B')} {d.day}"
    except (TypeError, ValueError):
        heading = f"{view['weekday']} {view['date']}"
    updated = now.strftime("%I:%M %p").lstrip("0")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Morning Page</title><style>{CSS}</style></head>
<body data-generated-at="{_e(gen)}">
{band}<div id="stale"></div>
<header><h1>{_e(heading)}</h1><span class="gen">updated {_e(updated)}</span></header>
<div class="read" id="claude-read"><strong>Claude's read:</strong> {read_html}</div>
<main>
<section id="b3" class="funnel"><h2>Funnel</h2>{funnel}</section>
<section id="b1"><h2>Today's must-dos</h2>{must}</section>
<section id="b2"><h2>Waiting on me</h2>{waiting}</section>
<section id="theirs"><h2>Waiting on them</h2>{theirs}</section>
<section id="b5"><h2>Done so far</h2>{done}</section>
<section id="b6"><h2>Commonplace</h2>{cp}</section>
</main>
{failed}
<script>{STALE_JS}</script>
</body></html>
"""


def write_page(text: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--out", default=None)
    ap.add_argument("--demo", action="store_true", help="render the fixtures only")
    ap.add_argument("--now", default=None, help="YYYY-MM-DDTHH:MM, for tests")
    a = ap.parse_args(argv)
    root = Path(a.repo_root).resolve()
    try:
        now = datetime.fromisoformat(a.now) if a.now else datetime.now()
    except ValueError:
        print(f"ERROR: --now must be YYYY-MM-DDTHH:MM, got {a.now!r}", file=sys.stderr)
        return 2
    src = gather_demo(today=now.date()) if a.demo else gather_live(root, now)
    page = render(build_view(src, now), now, demo=a.demo)
    out = Path(a.out) if a.out else root / OUT_REL / ("demo.html" if a.demo else "index.html")
    write_page(page, out)
    if not a.demo and now.hour >= ARCHIVE_HOUR:
        write_page(page, out.parent / f"{now.date().isoformat()}.html")
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
