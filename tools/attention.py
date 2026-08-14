#!/usr/bin/env python3
"""attention.py — one read-only view of every review queue that currently dead-ends.

WHY THIS EXISTS. Measured 2026-08-13: four producers write review-gated items and not one is
consumed on a cadence.

  - the weekly promotion scan writes `memory/promotion-backlog.md`, in a directory where 94%
    of files had never been read once, and files a Low-priority todo, where PARKED items from
    May are still sitting;
  - `data/inbox.md` holds 157 live items and grows ~158 lines/day;
  - overdue todos and stale pipeline rows surface only if a skill is invoked.

The queues are fine. The mechanisms that fill them are fine. There is simply no surface that
presents them, so the work lands in three graveyards. This is that surface.

TWO PROPERTIES, both learned the hard way the day this was written:

1. **A skipped queue is loud.** A missing source never silently drops out, because a report
   that quietly omits a queue turns "nothing needs attention" into a lie. Skips are counted,
   named, and reported with `count: null` -- never `0`, which would be a claim.
2. **Every count carries its denominator.** A bare "12" is not a finding. This is the
   CLAUDE.md name-the-scope hard rule applied to this tool's own output.

READ-ONLY BY CONSTRUCTION. It never writes, so it takes no lock and cannot corrupt
`data/inbox.md` while another session is draining it. Do not add a write path here; put it in
a separate tool and let this one stay safe to run at any time.

Exit codes: 0 always (a reporting tool that fails a session is worse than one that reports a
gap). `complete: false` in the payload is the signal to read the skips.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
# The auto-memory dir lives OUTSIDE the repo, so --repo-root alone does not scope this tool.
# Overridable rather than hardcoded, so a caller (or a test) can say which corpus it means --
# a tool whose scope cannot be stated is a tool whose output cannot be trusted.
DEFAULT_MEMORY_DIR = (Path.home()
                      / ".claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory")


def _skip(name, source, reason):
    return {"name": name, "status": "SKIPPED", "source": str(source), "reason": reason,
            "count": None, "denominator": None, "oldest_age_days": None, "detail": None}


def _run_json(argv, cwd):
    """Run a sibling tool and parse its JSON. Returns (payload, error_string)."""
    try:
        p = subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                           cwd=str(cwd), timeout=120)
    except Exception as exc:                      # noqa: BLE001 - report, never raise
        return None, f"could not run {argv[0]}: {exc}"
    if not p.stdout.strip():
        return None, f"{Path(argv[0]).name} produced no output (rc={p.returncode})"
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError:
        return None, f"{Path(argv[0]).name} output was not JSON (rc={p.returncode})"


# --------------------------------------------------------------------------- inbox
def queue_inbox(repo: Path) -> dict:
    src = repo / "data/inbox.md"
    if not src.is_file():
        return _skip("inbox", src, "data/inbox.md not found")
    # inbox_census is the ORACLE. A naive `## ` scan miscounts -- a 372-line HTML comment
    # hides three headers -- so never re-implement the count here.
    payload, err = _run_json([str(TOOLS / "inbox_census.py"), "--repo-root", str(repo)], repo)
    if err:
        return _skip("inbox", src, err)
    if payload.get("status") != "ok":
        return _skip("inbox", src, f"census refused: {payload.get('message', payload)}")
    live = payload.get("live_h2_headers")
    return {
        "name": "inbox", "status": "ok", "source": str(src),
        "count": live,
        "denominator": live + payload.get("commented_h2_headers", 0),
        "oldest_age_days": None,
        "detail": {k: payload.get(k) for k in
                   ("career_scan_live", "call_debrief", "dated_captures", "agent_drips")},
        "reason": None,
    }


# --------------------------------------------------------------------------- todos
_ROW = re.compile(r"^\|(?!\s*-{2,})")


def queue_todos(repo: Path, today: date) -> dict:
    src = repo / "data/job-todos.md"
    if not src.is_file():
        return _skip("todos", src, "data/job-todos.md not found")
    text = src.read_text(encoding="utf-8", errors="replace")
    rows, overdue, oldest = 0, [], None
    for line in text.splitlines():
        if not _ROW.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() == "task":
            continue
        rows += 1
        task, prio, due, status = cells[0], cells[1], cells[2], cells[3]
        if not status.lower().startswith("pending"):
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", due)
        if not m:
            continue
        d = date(*map(int, m.groups()))
        if d <= today:
            age = (today - d).days
            overdue.append({"task": task[:90], "priority": prio, "due": due, "age_days": age})
            oldest = age if oldest is None else max(oldest, age)
    overdue.sort(key=lambda r: -r["age_days"])
    return {
        "name": "todos", "status": "ok", "source": str(src),
        "count": len(overdue), "denominator": rows, "oldest_age_days": oldest,
        "detail": {"top": overdue[:5]}, "reason": None,
    }


# --------------------------------------------------------------------------- promotion
def queue_promotion(repo: Path, memory_dir: Path) -> dict:
    src = memory_dir
    if not src.is_dir():
        return _skip("promotion", src, "auto-memory directory not found")
    payload, err = _run_json(
        [str(TOOLS / "scan_promotion_candidates.py"), "--memory-dir", str(src),
         "--repo-root", str(repo), "--mode", "interactive", "--dry-run"], repo)
    if err:
        return _skip("promotion", src, err)
    cov = payload.get("schema_coverage", {})
    return {
        "name": "promotion", "status": "ok", "source": str(src),
        "count": payload.get("promotion_count"),
        # the denominator that matters is the rules the signal can SEE, not the corpus
        "denominator": cov.get("feedback_visible"),
        "oldest_age_days": None,
        "detail": {
            # `partial` is half-landed, not unstarted -- a different kind of work (finish the
            # enforcement) from an untouched rule (choose a tier). Split, because a single
            # number hides which one you are looking at. 23 of 35 were partial on 2026-08-13.
            "partial": sum(1 for c in (payload.get("promotion_candidates") or [])
                           if c.get("partial")),
            "terminal": payload.get("terminal_count"),
            "needs_review": payload.get("needs_review_count"),
            "coverage_pct": cov.get("feedback_pct"),
            "oversized_context_files": payload.get("oversized_count"),
        },
        "reason": None,
    }


# --------------------------------------------------------------------------- pipeline
def queue_pipeline(repo: Path) -> dict:
    src = repo / "data/job-pipeline.md"
    if not src.is_file():
        return _skip("pipeline", src, "data/job-pipeline.md not found")
    payload, err = _run_json(
        [str(TOOLS / "pipeline_staleness.py"), "--repo-root", str(repo)], repo)
    if err:
        return _skip("pipeline", src, err)
    stalled = payload.get("stalled_entries") or []
    active = payload.get("active_entries")
    if isinstance(active, list):
        active = len(active)
    ages = [e.get("days_stale") for e in stalled if isinstance(e.get("days_stale"), int)]
    return {
        "name": "pipeline", "status": "ok", "source": str(src),
        "count": len(stalled),
        "denominator": active,
        "oldest_age_days": max(ages) if ages else None,
        "detail": {"top": stalled[:5]}, "reason": None,
    }


# --------------------------------------------------------------------------- report
def build(repo: Path, today: date, memory_dir: Path) -> dict:
    queues = [queue_inbox(repo), queue_todos(repo, today),
              queue_promotion(repo, memory_dir), queue_pipeline(repo)]
    ok = [q for q in queues if q["status"] == "ok"]
    skipped = [q for q in queues if q["status"] != "ok"]
    return {
        "status": "ok",
        "date": today.isoformat(),
        "queues": queues,
        "skipped_count": len(skipped),
        "complete": not skipped,
        # None, not 0, when nothing could be read -- a total over an empty set is not a total.
        "total_open": sum(q["count"] for q in ok) if ok else None,
    }


def render(r: dict) -> str:
    out = [f"Attention — {r['date']}"]
    if not r["complete"]:
        out.append(f"  INCOMPLETE: {r['skipped_count']} of {len(r['queues'])} queues unreadable")
    out.append("")
    for q in r["queues"]:
        if q["status"] != "ok":
            out.append(f"  {q['name']:10} SKIPPED — {q['reason']}")
            continue
        den = "" if q["denominator"] is None else f" / {q['denominator']}"
        age = "" if q["oldest_age_days"] is None else f"  oldest {q['oldest_age_days']}d"
        out.append(f"  {q['name']:10} {q['count']}{den}{age}")
        d = q.get("detail") or {}
        for row in (d.get("top") or [])[:3]:
            label = row.get("task") or row.get("company") or str(row)[:70]
            out.append(f"             - {str(label)[:78]}")
        extra = {k: v for k, v in d.items() if k != "top" and v is not None}
        if extra:
            out.append("             " + "  ".join(f"{k}={v}" for k, v in extra.items()))
    tot = r["total_open"]
    out.append("")
    out.append(f"  total open: {tot}" if tot is not None
               else "  total open: UNKNOWN (no queue was readable)")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--memory-dir", default=str(DEFAULT_MEMORY_DIR),
                    help="auto-memory dir; lives outside the repo, so --repo-root does not scope it")
    ap.add_argument("--today", help="YYYY-MM-DD override, for tests")
    args = ap.parse_args()

    today = (datetime.strptime(args.today, "%Y-%m-%d").date() if args.today
             else date.today())
    report = build(Path(args.repo_root).resolve(), today, Path(args.memory_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
