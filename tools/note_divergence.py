#!/usr/bin/env python3
"""
note_divergence.py — find pipeline rows whose artifacts are NEWER than the row.

THE QUESTION THIS ANSWERS, and why it is not pipeline_staleness.py.

`pipeline_staleness.py` asks "has nothing happened on this row for a while" and nudges.
This asks the inverse: **has something happened that the row does not know about.** A row
whose `Date Updated` is 2026-09-16 sitting beside an `output/<slug>/` directory last
written 2026-09-20 is a row describing a superseded state.

Those two questions fail in opposite directions and only this one can send Nick back to
work he already finished.

ORIGIN (2026-09-20). Asked "what should I do", Claude answered from a client engagement's todo note,
which read `NOTHING IS BUILT - no slide exists`, written the night of 2026-09-16. By then the
frame had gone v29 -> v46, three cross-model verification rounds had run, both deliverables
had been rebuilt at 07:36, and the work had been emailed to the client at 07:55 — twelve hours
before its deadline. The note was never wrong. It aged, and it aged fastest *because* the work
was active: logging competes with doing and doing wins.

So the rows worked hardest carry the stalest notes, which inverts how anyone reads a tracker.
See memory/feedback_dont_assert_stale_annotation_as_fact.md (fire 2).

DELIBERATELY NOT WIRED. This reports; it gates nothing. Wiring it into /standup is a separate
decision, and /standup is a daily surface that should not gain an untested dependency.

SCOPE AND ITS LIMIT. This compares row dates against FILE MTIMES in `output/<slug>/`. It cannot
see whether something was delivered — that lives in sent mail, and "built" and "sent" are
different questions. When a row diverges, check the sent folder too. That check is not
automated here on purpose: it needs network and credentials, and a reporter that can fail for
network reasons is a reporter people stop trusting.

IT DOES NOT COVER ITS OWN ORIGIN CASE, AND THAT IS STATED RATHER THAN HIDDEN. The 2026-09-20
divergence lived in `data/job-todos.md`, not in the pipeline: that engagement's pipeline row was
current (`2026-09-20 | Take-home submitted (9/20)`) while the todo note still read
`NOTHING IS BUILT`. Verified against live data the day this was written — this scanner reports
2 diverged rows and that row is correctly not one of them.

Pipeline rows are checkable because each row carries a `Date Updated` column, which gives the
comparison a baseline. **Todo rows carry no note-written date**: only a due date, which is a
deadline rather than a timestamp, plus intermittent `[rev YYYY-MM-DD: ...]` markers that appear
only when a field changed.

**RESOLVED 2026-09-20, by Nick: the baseline IS recoverable, from the session transcripts.**
`~/.claude/projects/<project-slug>/*.jsonl` records every tool call with an ISO timestamp, so
the last `todo_write.py add|update` naming a task is that note's write-date. Verified on the
case that produced this tool: the todo was added 2026-09-15T00:33:56Z and last updated
2026-09-17T05:36:25Z, which is 2026-09-16 22:36 PDT and matches the note's own text ("2026-09-16
NIGHT, SESSION END") to the evening. That is the missing baseline.

NOT BUILT HERE, and the reason is cost rather than design: the transcript tree is ~1.0 GB across
124 files, so a naive scan per todo is not something to put on a daily surface. The shape that
works is one pass building a {task-fragment -> last-write-timestamp} map, cached, refreshed when
new transcripts appear. Left for a session that can measure that scan rather than assume it.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/note_divergence.py            # human-readable
  PYTHONIOENCODING=utf-8 python3 tools/note_divergence.py --json     # machine-readable
  PYTHONIOENCODING=utf-8 python3 tools/note_divergence.py --min-lag-days 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_staleness import parse_pipeline, read_file  # noqa: E402

# Files that are never evidence of work. .DS_Store is written by Finder merely for
# *looking* at a directory, so counting it would report divergence on any folder Nick
# happened to open — the exact false positive that makes a reporter ignorable.
IGNORED_NAMES = {".DS_Store", ".gitkeep", "Thumbs.db"}
IGNORED_SUFFIXES = {".tmp", ".swp", ".part"}


def slugify(name: str) -> str:
    """Company display name -> output/<slug>/ directory name.

    Same rule as act_classify._slug. Kept as one line rather than imported because
    act_classify does heavy work at import time (it reads the inbox and the pipeline),
    and a reporter should not pay that to lowercase a string.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def newest_artifact(directory: Path) -> tuple[Path | None, date | None]:
    """Newest real file under `directory`, recursively. (path, mtime_date)."""
    newest_path: Path | None = None
    newest_mtime: float = -1.0
    if not directory.is_dir():
        return None, None
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if path.name in IGNORED_NAMES or path.suffix in IGNORED_SUFFIXES:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime, newest_path = mtime, path
    if newest_path is None:
        return None, None
    return newest_path, datetime.fromtimestamp(newest_mtime).date()


def scan(repo_root: Path, today: date, min_lag_days: int) -> dict:
    """Compare every active pipeline row's Date Updated against its artifacts."""
    content = read_file(repo_root / "data" / "job-pipeline.md")
    parsed = parse_pipeline(content, today, None)

    diverged: list[dict] = []
    checked = 0
    no_artifacts = 0

    for entry in parsed.get("active_entries", []):
        company = entry.get("company", "")
        slug = slugify(company)
        if not slug:
            continue
        out_dir = repo_root / "output" / slug
        artifact, artifact_date = newest_artifact(out_dir)
        if artifact is None or artifact_date is None:
            no_artifacts += 1
            continue
        checked += 1

        raw_row_date = entry.get("date_updated") or ""
        try:
            row_date = datetime.strptime(raw_row_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            # An unparseable date is itself a divergence: the row cannot be compared,
            # so it cannot be trusted. Report rather than silently skip — a skipped row
            # is indistinguishable from a clean one in the output.
            diverged.append({
                "company": company,
                "role": entry.get("role", ""),
                "stage": entry.get("stage", ""),
                "row_date": raw_row_date or "(missing)",
                "newest_artifact": str(artifact.relative_to(repo_root)),
                "artifact_date": artifact_date.isoformat(),
                "lag_days": None,
                "reason": "row date is missing or unparseable",
            })
            continue

        lag = (artifact_date - row_date).days
        if lag >= min_lag_days:
            diverged.append({
                "company": company,
                "role": entry.get("role", ""),
                "stage": entry.get("stage", ""),
                "row_date": row_date.isoformat(),
                "newest_artifact": str(artifact.relative_to(repo_root)),
                "artifact_date": artifact_date.isoformat(),
                "lag_days": lag,
                "reason": f"artifacts are {lag} day(s) newer than the row",
            })

    diverged.sort(key=lambda d: (d["lag_days"] is None, -(d["lag_days"] or 0)))
    return {
        "target_date": today.isoformat(),
        "min_lag_days": min_lag_days,
        "diverged": diverged,
        "metrics": {
            "active_rows": len(parsed.get("active_entries", [])),
            "rows_with_artifacts": checked,
            "rows_without_artifacts": no_artifacts,
            "diverged_count": len(diverged),
        },
    }


def render(result: dict) -> str:
    rows = result["diverged"]
    m = result["metrics"]
    if not rows:
        return (f"No divergence. {m['rows_with_artifacts']} of {m['active_rows']} active rows "
                f"have artifacts and all are older than their row date.")
    out = [f"{len(rows)} row(s) where the artifacts are newer than the note:", ""]
    for r in rows:
        lag = "unknown" if r["lag_days"] is None else f"{r['lag_days']}d"
        out.append(f"  {r['company']} — row says {r['row_date']}, "
                   f"newest artifact {r['artifact_date']} ({lag} behind)")
        out.append(f"      {r['newest_artifact']}")
        out.append(f"      stage: {r['stage']}")
    out += ["", "Check the sent folder before acting on any of these: built and delivered",
            "are different questions and this only answers the first."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of prose")
    ap.add_argument("--target-date", default=None, help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--min-lag-days", type=int, default=1,
                    help="report a row only when artifacts are at least this many days "
                         "newer (default 1; same-day edits are normal and not signal)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    result = scan(repo_root, today, args.min_lag_days)
    print(json.dumps(result, indent=2) if args.json else render(result))
    # Always 0. This is a reporter, not a gate; a non-zero exit would make it a
    # blocking check nobody agreed to, and a reporter that can fail a pipeline
    # gets removed from the pipeline.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
