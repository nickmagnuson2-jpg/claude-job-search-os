#!/usr/bin/env python3
"""check_automation_health.py — independent watchdog for the launchd automation.

Surfaced by /standup so a scheduler failure is caught within a day. The whole
point is that this check lives OUTSIDE the jobs it watches: the in-job Gmail
staleness alert cannot fire when the job itself is not running.

Origin: 2026-06-15 — all 8 launchd jobs silently died ~June 2. After a macOS
TCC update, a stale `com.apple.macl` xattr on each job's existing log file made
launchd unable to open the file, so every job failed at setup with EX_CONFIG (78)
before executing a single line. Nick's Gmail-staleness alert (which lives inside
gmail_fetch.py) never fired, because gmail_fetch.py was never run. The fix for a
poisoned log is to delete it so launchd recreates a fresh one; the structural
lesson is that a watchdog must run somewhere independent of what it watches.

Checks (both degrade gracefully — never raises, never fails standup):
  1. Gmail fetch freshness — last_refresh age in tools/.gmail_state*.json.
  2. launchd job health — last exit code of every com.nickmagnuson.jobsearch.*
     user agent (non-zero == broken).

Output: JSON to stdout. {"warnings": [...], "gmail": {...}, "jobs": [...]}.
Usage: PYTHONIOENCODING=utf-8 python3 tools/check_automation_health.py [--repo-root .] [--gmail-stale-hours 24]
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

JOB_PREFIX = "com.nickmagnuson.jobsearch."


def _age_hours(iso_str: str):
    """Hours since an ISO-ish timestamp, or None if unparseable."""
    if not iso_str:
        return None
    normalized = re.sub(r"\.\d+", "", str(iso_str)).rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return (datetime.now() - datetime.strptime(normalized, fmt)).total_seconds() / 3600.0
        except ValueError:
            continue
    return None


def check_gmail(repo_root: Path, stale_hours: float) -> tuple[list, list]:
    """Return (gmail_status_entries, warnings)."""
    entries, warnings = [], []
    for label, fname in (("work", ".gmail_state.json"), ("personal", ".gmail_state_personal.json")):
        path = repo_root / "tools" / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            warnings.append(f"Gmail ({label}) state file unreadable: {fname}")
            continue
        age = _age_hours(data.get("last_refresh"))
        entry = {"account": label, "last_refresh": data.get("last_refresh"),
                 "age_hours": round(age, 1) if age is not None else None}
        entries.append(entry)
        if age is None:
            warnings.append(f"Gmail ({label}) last_refresh missing/unparseable — fetch may be stalled.")
        elif age > stale_hours:
            warnings.append(
                f"Gmail ({label}) last fetched {age/24:.1f} days ago "
                f"(threshold {stale_hours/24:.0f}d) — fetch is stalled. "
                f"Check launchd: bash tools/launchd/install.sh status"
            )
    return entries, warnings


def check_jobs() -> tuple[list, list]:
    """Return (job_status_entries, warnings) from `launchctl list`."""
    entries, warnings = [], []
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return entries, ["Could not run `launchctl list` to check automation health."]
    for line in out.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) < 3 or not cols[2].startswith(JOB_PREFIX):
            continue
        label = cols[2].strip()
        short = label[len(JOB_PREFIX):]
        try:
            exit_code = int(cols[1])
        except ValueError:
            exit_code = None
        entries.append({"job": short, "last_exit": exit_code})
        if exit_code not in (0, None):
            warnings.append(f"launchd job '{short}' last exited {exit_code} (non-zero = broken).")
    if not entries:
        warnings.append("No com.nickmagnuson.jobsearch.* launchd jobs are loaded — automation is off.")
    return entries, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--gmail-stale-hours", type=float, default=24.0)
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()

    gmail, gw = check_gmail(repo_root, args.gmail_stale_hours)
    jobs, jw = check_jobs()
    print(json.dumps({"warnings": gw + jw, "gmail": gmail, "jobs": jobs}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
