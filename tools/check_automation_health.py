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

Checks (all degrade gracefully — never raises, never fails standup):
  1. Gmail fetch freshness — last_refresh age in tools/.gmail_state*.json.
  2. launchd job health — last exit code of every com.nickmagnuson.jobsearch.*
     user agent (non-zero == broken).
  3. Stalled long runs — a declared multi-hour job (a state dir holding targets.json
     + baseline.jsonl, i.e. the mutation sweep) that is INCOMPLETE with no process
     running and no progress for hours.

Check 3 exists because of 2026-08-26/27. The corpus sweep was stopped by hand at 22:45
and never restarted: it sat dead all night and the morning found 3 of 109 tools banked.
Nothing noticed, because nothing was watching. That is the same shape as the 2026-06
die-off in the origin note above — an unattended job that stops, with the only observer
being the thing that stopped. The sweep is resumable and now expected to span several
nights, so "incomplete and idle" is a normal overnight state and a stalled state at
once; what makes it reportable is that it is idle when nobody intended it to be. This
check reports the counts and the age and lets the reader decide, rather than guessing
at intent.

Output: JSON to stdout. {"warnings": [...], "gmail": {...}, "jobs": [...]}.
Usage: PYTHONIOENCODING=utf-8 python3 tools/check_automation_health.py [--repo-root .] [--gmail-stale-hours 24]
"""
import argparse
import json
import os
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


def expected_jobs(repo_root: Path) -> set:
    """Job short-names we expect loaded, derived from the installed plists.

    The plists in tools/launchd/ are the source of truth for what SHOULD run;
    deriving from them keeps this watchdog in sync as jobs are added/removed.
    """
    plist_dir = repo_root / "tools" / "launchd"
    jobs = set()
    if plist_dir.is_dir():
        for p in plist_dir.glob(f"{JOB_PREFIX}*.plist"):
            jobs.add(p.name[len(JOB_PREFIX):-len(".plist")])
    return jobs


def check_jobs(repo_root: Path) -> tuple[list, list]:
    """Return (job_status_entries, warnings) from `launchctl list`.

    Two failure modes are caught: (1) a loaded job whose last exit was non-zero,
    and (2) a job that has a plist but is NOT loaded at all — the exact shape of
    the 2026-06 die-off, where jobs vanished from `launchctl list` and the old
    check (which only warned on ZERO loaded jobs) reported healthy for 3 weeks.
    """
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
    loaded = {e["job"] for e in entries}
    for missing in sorted(expected_jobs(repo_root) - loaded):
        warnings.append(
            f"launchd job '{missing}' has a plist but is not loaded — automation is off "
            f"for it. Run: bash tools/launchd/install.sh install"
        )
    return entries, warnings


def _process_running(pattern: str) -> bool:
    """Is a process matching `pattern` alive? False on any failure.

    Injectable (and defaulted to False on error) because the watchdog must never raise:
    a watchdog that dies on a broken pgrep is exactly as useful as no watchdog.
    """
    try:
        return subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def check_long_runs(repo_root: Path, stall_hours: float = 4.0,
                    running=_process_running) -> tuple[list, list]:
    """Return (long_run_entries, warnings) for declared multi-hour runs.

    A run declares itself by leaving a `targets.json` in a state dir; it records progress
    by appending one line per finished unit to `baseline.jsonl`. Both facts are on disk,
    which is what lets an outside observer judge it without cooperation from the job.
    """
    entries, warnings = [], []
    state_dirs = sorted((repo_root / "output" / "analysis").glob("*/targets.json"))
    for targets_file in state_dirs:
        state = targets_file.parent
        try:
            targets = json.loads(targets_file.read_text(encoding="utf-8"))
            total = sum(1 for t in targets if (t.get("mutants") or 0) > 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue                      # unreadable target list is not a stall claim
        if not total:
            continue

        results = state / "baseline.jsonl"
        try:
            banked = sum(1 for ln in results.read_text(encoding="utf-8").splitlines()
                         if ln.strip())
            progress_at = results.stat().st_mtime
        except OSError:
            banked, progress_at = 0, targets_file.stat().st_mtime

        idle_hours = (datetime.now().timestamp() - progress_at) / 3600.0
        alive = running("tools/mutation_sweep.py")
        entry = {"run": state.name, "banked": banked, "total": total,
                 "running": alive, "idle_hours": round(idle_hours, 1)}
        entries.append(entry)

        # Complete, or actively working: nothing to say.
        if banked >= total or alive:
            continue
        if idle_hours < stall_hours:
            continue                      # just stopped; someone is probably still here
        warnings.append(
            f"Long run '{state.name}' is STALLED: {banked} of {total} banked, no "
            f"mutation_sweep process, no progress for {idle_hours:.1f}h. It is resumable "
            f"— re-run to continue from where it stopped.")
    return entries, warnings


def write_inbox_alert(repo_root: Path, warnings: list) -> Path | None:
    """When run headless (launchd), surface warnings by writing a dated alert to
    inbox/ so /standup or /act catches an outage even if the watchdog is never
    invoked interactively. Overwrites the same-day file (idempotent per day);
    removes a stale same-day alert once things are healthy again. Atomic write.
    """
    inbox = repo_root / "inbox"
    # Date derived locally; this is a CLI tool, not a resumable workflow.
    stamp = datetime.now().strftime("%Y%m%d")
    alert = inbox / f"{stamp}-automation-health-alert.md"
    if not warnings:
        if alert.exists():
            try:
                alert.unlink()
            except OSError:
                pass
        return None
    inbox.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Automation health alert ({stamp})\n\n"
        "The launchd automation watchdog (`check_automation_health.py`) found problems:\n\n"
        + "".join(f"- {w}\n" for w in warnings)
        + "\nRun `bash tools/launchd/install.sh status` to inspect, "
        "then `bash tools/launchd/install.sh install` to (re)load jobs.\n"
    )
    tmp = alert.with_suffix(alert.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, alert)
    return alert


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--gmail-stale-hours", type=float, default=24.0)
    ap.add_argument("--stall-hours", type=float, default=4.0,
                    help="How long a declared long run may sit idle and not running "
                         "before it is reported as stalled.")
    ap.add_argument("--inbox-on-warn", action="store_true",
                    help="Write a dated alert to inbox/ when warnings exist (for headless/launchd runs).")
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()

    gmail, gw = check_gmail(repo_root, args.gmail_stale_hours)
    jobs, jw = check_jobs(repo_root)
    runs, rw = check_long_runs(repo_root, args.stall_hours)
    warnings = gw + jw + rw
    if args.inbox_on_warn:
        write_inbox_alert(repo_root, warnings)
    print(json.dumps({"warnings": warnings, "gmail": gmail, "jobs": jobs,
                      "long_runs": runs}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
