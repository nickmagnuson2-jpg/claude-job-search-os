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
import plistlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Sibling import: the quiesce/restore primitive mutation_sweep uses. Same path-insert
# reason as elsewhere in tools/ -- this file is loaded both as a script (tools/ on
# sys.path[0]) and by path from its own tests (where it is not).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_quiesce  # noqa: E402
import mutation_state  # noqa: E402


def _state_dirs(repo_root: Path) -> list:
    """Every directory a sweep may have left state in: the live store, then the old ones.

    Derived from `repo_root`, not from mutation_state.STATE_DIR, because this tool takes
    --repo-root and its tests run it against a throwaway tree; only the store's LOCATION
    WITHIN a repo is taken from mutation_state, so there is one definition of it.

    Until 2026-10-06 both checks below looked only in output/analysis/*/, which the sweep
    left on 2026-09-08. For a month this watchdog could not see a stalled sweep or jobs a
    killed sweep had left unloaded, and its tests planted the old path, so they passed.
    """
    live = repo_root / mutation_state.STATE_DIR.relative_to(mutation_state.REPO_ROOT)
    legacy = sorted(p for p in (repo_root / "output" / "analysis").glob("*") if p.is_dir())
    return [live, *legacy]

JOB_PREFIX = "com.nickmagnuson.jobsearch."


def _age_hours(iso_str: str):
    """Hours since an ISO timestamp, or None if it is missing or unparseable.

    A trailing Z is dropped and the time read as local, as the fetchers write it.
    """
    try:
        then = datetime.fromisoformat(str(iso_str or "").rstrip("Z"))
    except ValueError:
        return None
    return (datetime.now() - then.replace(tzinfo=None)).total_seconds() / 3600.0


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
    # A missing folder globs to nothing, so there is no separate "does it exist" branch.
    return {p.name[len(JOB_PREFIX):-len(".plist")]
            for p in plist_dir.glob(f"{JOB_PREFIX}*.plist")}


SWEEP_JOB = "mutation-sweep"


def schedule_seconds(plist: Path):
    """How often this job is meant to run, in seconds, or None if that cannot be read.

    StartInterval is taken as written. A calendar schedule is reduced to its period:
    weekly when it names a Weekday, monthly when it names a Day, daily when it names an
    Hour, hourly when it names only a Minute. A list of calendar entries takes the
    shortest.
    """
    try:
        data = plistlib.loads(plist.read_bytes())
    except Exception:
        return None
    if isinstance(data.get("StartInterval"), int) and data["StartInterval"] > 0:
        return data["StartInterval"]
    cal = data.get("StartCalendarInterval")
    entries = cal if isinstance(cal, list) else [cal]
    # Several times of day (granola-auto-debrief fires every 3 hours as a list of Hour
    # entries): the period is the shortest gap between consecutive entries around the
    # clock, not a day. Reading each entry as "daily" gave that job a 48-hour threshold
    # where 6 hours was meant (cross-model finding, 2026-10-06).
    if (len(entries) > 1 and all(isinstance(e, dict) and "Hour" in e
                                 and not ({"Weekday", "Day", "Month"} & set(e))
                                 for e in entries)):
        minutes = sorted({int(e["Hour"]) * 60 + int(e.get("Minute", 0)) for e in entries})
        # The wrap-around gap is always present, so entries that all name the same time
        # come out as one day with no special case.
        gaps = [b - a for a, b in zip(minutes, minutes[1:])]
        gaps.append(minutes[0] + 24 * 60 - minutes[-1])
        return min(gaps) * 60
    periods = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if "Weekday" in e:
            periods.append(7 * 86400)
        elif "Day" in e or "Month" in e:
            periods.append(31 * 86400)
        elif "Hour" in e:
            periods.append(86400)
        elif "Minute" in e:
            periods.append(3600)
    return min(periods) if periods else None


def boot_time():
    """When this machine last booted, as a timestamp, or None if it cannot be read."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\bsec\s*=\s*(\d+)", out)
    return float(m.group(1)) if m else None


def overdue_hours(repo_root: Path, short: str, now: float | None = None,
                  launch_agents: Path | None = None, booted=None):
    """Hours since this job last showed any sign of running, if that is more than twice
    its schedule. None when it is not overdue OR when nothing can date it.

    The evidence is the newest of: its two log files, the installed plist (a job loaded
    an hour ago has not had its turn yet), and the last boot. The boot counts because
    neither file is touched by a restart: without it, a job with week-old logs warned at
    the first health check after a reboot, before it had been given a chance to run.
    No evidence is not an alarm.
    """
    period = schedule_seconds(repo_root / "tools" / "launchd" / f"{JOB_PREFIX}{short}.plist")
    if not period:
        return None
    agents = launch_agents or job_quiesce.LAUNCH_AGENTS
    seen = []
    for f in (repo_root / "tools" / "launchd" / "logs" / f"{short}.log",
              repo_root / "tools" / "launchd" / "logs" / f"{short}.err",
              agents / f"{JOB_PREFIX}{short}.plist"):
        try:
            seen.append(f.stat().st_mtime)
        except OSError:
            pass
    if not seen:
        return None
    since_boot = (booted or boot_time)()
    if since_boot is not None:
        seen.append(since_boot)
    idle = (now if now is not None else datetime.now().timestamp()) - max(seen)
    return round(idle / 3600.0, 1) if idle > 2 * period else None


def check_jobs(repo_root: Path, launch_agents: Path | None = None) -> tuple[list, list]:
    """Return (job_status_entries, warnings) from `launchctl list`.

    Three failure modes are caught: (1) a loaded job whose last exit was non-zero,
    (2) a job that has a plist but is NOT loaded at all — the exact shape of
    the 2026-06 die-off, where jobs vanished from `launchctl list` and the old
    check (which only warned on ZERO loaded jobs) reported healthy for 3 weeks, and
    (3) since 2026-10-06, a loaded job with NO recorded exit that is overdue. launchd
    shows no exit for a job that has not run since it was loaded, which after a reboot
    is every job, so "no exit" alone was treated as healthy and a job that never ran
    again read as healthy indefinitely. Warning on every such job would alert on every
    reboot; it warns only when the job's logs and installed plist are all older than
    twice its schedule.
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
        elif exit_code is None:
            late = overdue_hours(repo_root, short, launch_agents=launch_agents)
            if late is not None:
                entries[-1]["overdue_hours"] = late
                warnings.append(
                    f"launchd job '{short}' is loaded with no recorded exit and has shown "
                    f"no sign of running for {late}h, more than twice its schedule. "
                    f"Check: launchctl print gui/$(id -u)/{label}")
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
    state_dirs = [d / mutation_state.TARGETS_NAME for d in _state_dirs(repo_root)
                  if (d / mutation_state.TARGETS_NAME).is_file()]
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
            # MEASURED TOOLS, not lines. The file is append-only and a failing tool is
            # retried every night, so a line count grows without anything being banked.
            rows = [json.loads(ln) for ln in results.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]
            banked = sum(1 for r in mutation_state.latest_per_tool(rows)
                         if not mutation_state.is_engine_failure(r))
            progress_at = results.stat().st_mtime
        except (OSError, ValueError):
            banked, progress_at = 0, targets_file.stat().st_mtime

        idle_hours = (datetime.now().timestamp() - progress_at) / 3600.0
        alive = running("tools/mutation_sweep.py")
        entry = {"run": state.name, "banked": banked, "total": total,
                 "running": alive, "idle_hours": round(idle_hours, 1)}
        entries.append(entry)

        # Complete, or actively working: nothing to say.
        if banked >= total or alive:
            continue
        # Deliberately parked (2026-10-06). A sweep job whose plist sits in
        # tools/launchd/disarmed/ was stopped on purpose with work remaining; reporting
        # that as a stall every morning is an alert nobody can act on. Armed and idle
        # still warns.
        if (repo_root / "tools" / "launchd" / "disarmed"
                / f"{JOB_PREFIX}{SWEEP_JOB}.plist").is_file():
            entry["disarmed"] = True
            continue
        if idle_hours < stall_hours:
            continue                      # just stopped; someone is probably still here
        warnings.append(
            f"Long run '{state.name}' is STALLED: {banked} of {total} banked, no "
            f"mutation_sweep process, no progress for {idle_hours:.1f}h. It is resumable "
            f"— re-run to continue from where it stopped.")
    return entries, warnings


def check_quiesced_jobs(repo_root: Path, running=_process_running,
                        restorer=None) -> tuple[list, list]:
    """Find launchd jobs left unloaded by a mutation sweep, and put them back.

    mutation_sweep unloads the scheduled jobs for the length of a run -- they shell into
    the tools/*.py it rewrites, and gmail-fetch alone would fire ~40 times against a
    mutated tree overnight. It restores them in a `finally` and on SIGTERM/SIGINT.
    SIGKILL has neither, and then the jobs stay down with a marker file as the only
    record. This is the recovery path that does not require the sweep to run again.

    THE DISTINCTION THAT MATTERS: a marker while the sweep is alive is the system working
    as designed, and warning about it would train the reader to ignore the one case that
    is a real fault. Only a marker with no sweep process behind it is a stranded job.

    Never raises, for the same reason `_process_running` returns False on error: this
    runs unattended at 08:00 and a watchdog that dies on a broken dependency is exactly
    as useful as no watchdog.
    """
    restorer = restorer or job_quiesce.restore
    entries, warnings = [], []
    markers = [d / mutation_state.QUIESCE_MARKER_NAME for d in _state_dirs(repo_root)
               if (d / mutation_state.QUIESCE_MARKER_NAME).is_file()]
    for marker in markers:
        alive = running("tools/mutation_sweep.py")
        entry = {"run": marker.parent.name, "sweep_running": alive}
        if alive:
            entries.append(entry)
            continue                      # down on purpose, for now
        try:
            res = restorer(repo_root, marker)
        except Exception as exc:          # noqa: BLE001 - a watchdog must not die
            entries.append({**entry, "error": str(exc)})
            warnings.append(f"Could not restore launchd jobs quiesced by "
                            f"'{marker.parent.name}': {exc}. Restore by hand with: "
                            f"bash tools/launchd/install.sh install")
            continue
        entry |= {"restored": res.get("restored") or [],
                  "failed": res.get("failed") or []}
        entries.append(entry)
        if entry["restored"]:
            warnings.append(
                f"A mutation sweep died without restoring launchd jobs; put back: "
                f"{', '.join(entry['restored'])}.")
        if entry["failed"]:
            warnings.append(
                f"launchd job(s) STILL DOWN after a failed restore: "
                f"{', '.join(entry['failed'])}. Automation is off for them. Run: "
                f"bash tools/launchd/install.sh install")
        for note in res.get("notes") or []:
            warnings.append(f"quiesce marker '{marker.parent.name}': {note}")
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
        try:
            alert.unlink(missing_ok=True)
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
    quiesced, qw = check_quiesced_jobs(repo_root)
    warnings = gw + jw + rw + qw
    if args.inbox_on_warn:
        write_inbox_alert(repo_root, warnings)
    print(json.dumps({"warnings": warnings, "gmail": gmail, "jobs": jobs,
                      "long_runs": runs, "quiesced_jobs": quiesced}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
