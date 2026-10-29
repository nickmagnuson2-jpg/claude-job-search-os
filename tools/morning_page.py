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

Content, in Nick's order: 1 must-dos, 2 waiting on me, 3 people funnel (placeholder until
the funnel codify run; holds "Waiting on them", the processes whose next move is theirs), 4 machine room, 5 done so far today, 6 one commonplace prompt.
Plus "Claude's read" and the First chair prompt.

Rules the tests hold it to:
  * The page embeds generated_at and shows a STALE banner when it is older than 30 minutes
    (checked in the browser, so a page the job stopped updating says so).
  * Live mode carries a fixed "LIVE, PRIVATE" band. Demo mode (--demo) reads only the
    files in tests/fixtures/morning_page/ and carries a "DEMO DATA" band instead.
  * Numbers come only from script output. "Claude's read" may hold no digit: a read that
    contains one is withheld, not shown.
  * Built for a glance at a laptop screen: large type, at most a few items per block,
    everything visible without scrolling.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/morning_page.py [--repo-root .] [--out PATH] [--demo]
      [--now YYYY-MM-DDTHH:MM]

The launchd job (tools/launchd/com.nickmagnuson.jobsearch.morning-page.plist) runs it at
07:00 and every 15 minutes until 19:00. It is written, not installed.
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
# "<Name> will reply" needs a named other person: "Will follow up Monday" and "I will send"
# are Nick's own moves (D5 review 836.F1).
THEIRS = re.compile(
    r"^(await|wait\b|waiting\b|ball (is )?on\b|pending (their|his|her)\b|their move)"
    r"|\b(?!(?:I|Nick|We)\b)[A-Z][\w'-]* (will (reply|respond|send|call|follow up|let)"
    r"|to (reply|respond) by|is to reply)\b"
    r"|\bawaiting\b|\bscheduled\b|\bshe calls\b|\bhe calls\b|\bthey call\b",
    re.I)


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


def open_findings(root: Path) -> int | None:
    try:
        import finding_write
        return len(finding_write.collect(root, only_open=True))
    except Exception:
        return None


def gather_live(root: Path, now: datetime) -> dict:
    today = now.date()
    return {
        "collector": standup_collect.collect(root, today.isoformat(), read_only=True),
        "day": day_check.collect(root, today),
        "calendar": calendar_events(root),
        "commonplace": commonplace(root, today),
        "claude_read": claude_read(root, today),
        "open_findings": open_findings(root),
        "waiting_on_them": waiting_on_them(root),
    }


def gather_demo(fixtures: Path = FIXTURES) -> dict:
    out = {}
    for name in ("collector", "day", "calendar", "commonplace", "claude_read", "open_findings",
                 "waiting_on_them"):
        out[name] = json.loads((fixtures / f"{name}.json").read_text(encoding="utf-8"))
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
        todos.append(f"{t.get('task', '')[:90]}  ({age})")

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

    # 4. Machine room.
    cm = _data(col, "conversations_metric") or {}
    sent = (cm.get("outreach_sent") or {}).get("counts") or {}
    m4 = None
    if "7d" in sent and "30d" in sent:
        floor = round(sent["30d"] / 30 * 7, 1)
        m4 = {"last_7": sent["7d"], "floor": floor, "ok": sent["7d"] >= floor}
    ws = (_data(col, "workstream_state") or {}).get("summary") or {}
    ah = _data(col, "automation_health") or {}
    machine = {
        "m4": m4,
        "workstreams_changed": ws.get("changed"),
        "workstream_errors": ws.get("error"),
        "open_findings": src.get("open_findings"),
        "launchd_warnings": len(ah["warnings"]) if isinstance(ah.get("warnings"), list) else None,
        "learning_due": (_data(col, "learning_ledger") or {}).get("due_total"),
        "sources_failed": list(col.get("failed") or []),
    }

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
        "machine": machine,
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
Helvetica,Arial,sans-serif;overflow:hidden;display:flex;flex-direction:column}
/* A short window must not clip content (D5 review 830.F1): below 760px of height the page
   scrolls and the cards size to their content instead of to the screen. */
@media (max-height:760px){body{overflow:auto}main{grid-template-rows:none}section{overflow:visible}}
.band{font-weight:700;letter-spacing:.08em;text-align:center;padding:4px;color:#fff}
.band.live{background:var(--live)}.band.demo{background:var(--demo)}
#stale{display:none;background:var(--stale);color:#000;text-align:center;font-weight:700;padding:6px}
header{display:flex;justify-content:space-between;align-items:baseline;padding:10px 24px 4px}
header h1{font-size:30px;margin:0}header .gen{color:var(--muted);font-size:16px}
.read{margin:0 24px 8px;padding:8px 14px;border-left:4px solid var(--line);font-size:19px}
main{flex:1;display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:1fr 1fr;
gap:14px;padding:0 24px 18px;min-height:0}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px 16px;overflow:hidden;min-height:0}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 6px}
h3{font-size:16px;margin:8px 0 2px;color:var(--muted);font-weight:600}
ul{margin:0;padding-left:20px}li{margin:2px 0}p{margin:4px 0}
.muted{color:var(--muted)}table{border-collapse:collapse;width:100%}
td{padding:3px 0;vertical-align:top}td.n{text-align:right;font-weight:700;white-space:nowrap;padding-left:10px}
.ok{color:var(--ok)}.warn{color:var(--warn)}
@media (max-width:900px){body{overflow:auto}main{grid-template-columns:1fr;grid-template-rows:none;padding:0 16px 16px}
header{padding:10px 16px 4px}.read{margin:0 16px 8px}}
"""

STALE_JS = """
(function(){var g=Date.parse(document.body.dataset.generatedAt);var lim=%d*60*1000;
function chk(){var el=document.getElementById('stale');if(isNaN(g)||Date.now()-g>lim){
el.style.display='block';el.textContent='STALE: this page was generated at '+
document.body.dataset.generatedAt+' and has not updated since.';}}chk();setInterval(chk,60000);})();
""" % STALE_MINUTES


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

    m = view["machine"]
    if m["m4"]:
        cls = "ok" if m["m4"]["ok"] else "warn"
        m4 = (f'<tr><td>M4 sends, last 7 days vs floor</td><td class="n {cls}">'
              f'{_e(m["m4"]["last_7"])} / {_e(m["m4"]["floor"])}</td></tr>')
    else:
        m4 = '<tr><td>M4 sends vs floor</td><td class="n">unknown</td></tr>'
    machine = ("<table>" + m4
               + '<tr><td>M1 weekly flow</td><td class="n">not built</td></tr>'
               + f'<tr><td>Workstreams changed</td><td class="n">{_n(m["workstreams_changed"])}</td></tr>'
               + f'<tr><td>Workstream probe errors</td><td class="n">{_n(m["workstream_errors"])}</td></tr>'
               + f'<tr><td>Open cross-model findings</td><td class="n">{_n(m["open_findings"])}</td></tr>'
               + f'<tr><td>launchd warnings</td><td class="n">{_n(m["launchd_warnings"])}</td></tr>'
               + f'<tr><td>Learning lanes due</td><td class="n">{_n(m["learning_due"])}</td></tr>'
               + "</table>")
    if m["sources_failed"]:
        machine += f'<p class="warn">Sources failed: {_e(", ".join(m["sources_failed"]))}</p>'

    day = view["day"]
    counts = day.get("counts") or {}
    targets = day.get("targets") or {}
    drows = ""
    for k in day_check.COUNTS:
        c = counts.get(k) or {}
        t = targets.get(k)
        tail = f" of {_e(t['target'])} a {_e(t['period'])}" if t else ""
        drows += (f'<tr><td>{_e(day_check.LABELS[k])}</td><td class="n">{_n(c.get("today"))}'
                  f'</td><td class="n muted">week {_n(c.get("week"))}{tail}</td></tr>')
    done = f"<table>{drows}</table>"

    pick = view["commonplace"]
    if pick:
        cp = (f'<p><strong>{_e(pick["title"])}</strong></p><p class="muted">{_e(pick["path"])}</p>'
              '<p>One line: agree, disagree, or so what?</p>')
    else:
        cp = '<p class="muted">No saved article is waiting for a take.</p>'
    cp += '<h3>First chair</h3><p>Practice today? Dictate one line.</p>'

    wt = view["waiting_on_them"]
    if wt.get("rows") is None:
        theirs = f'<h3>Waiting on them</h3><p class="muted">Pipeline unavailable: {_e(wt.get("error"))}</p>'
    else:
        items = [f"{r['company']}: {r['next']}" for r in wt["rows"]]
        more = len(items) - MAX_ITEMS
        theirs = ("<h3>Waiting on them</h3>" + _list(items, "")
                  + (f'<p class="muted">and {more} more</p>' if more > 0 else ""))

    read = view["claude_read"]
    read_html = _e(read["text"]) if read.get("text") else f'<span class="muted">{_e(read.get("note"))}</span>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Morning Page</title><style>{CSS}</style></head>
<body data-generated-at="{_e(gen)}">
{band}<div id="stale"></div>
<header><h1>{_e(view["weekday"])} {_e(view["date"])}</h1><span class="gen">generated {_e(gen)}</span></header>
<div class="read" id="claude-read"><strong>Claude's read:</strong> {read_html}</div>
<main>
<section id="b1"><h2>1. Today's must-dos</h2>{must}</section>
<section id="b2"><h2>2. Waiting on me</h2>{waiting}</section>
<section id="b3"><h2>3. People funnel</h2><p class="muted">Placeholder until the funnel codify run.</p>{theirs}</section>
<section id="b4"><h2>4. Machine room</h2>{machine}</section>
<section id="b5"><h2>5. Done so far today</h2>{done}</section>
<section id="b6"><h2>6. Commonplace</h2>{cp}</section>
</main>
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
    src = gather_demo() if a.demo else gather_live(root, now)
    page = render(build_view(src, now), now, demo=a.demo)
    out = Path(a.out) if a.out else root / OUT_REL / ("demo.html" if a.demo else "index.html")
    write_page(page, out)
    if not a.demo and now.hour >= ARCHIVE_HOUR:
        write_page(page, out.parent / f"{now.date().isoformat()}.html")
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
