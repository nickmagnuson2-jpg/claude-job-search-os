#!/usr/bin/env python3
"""job_quiesce.py - take the scheduled launchd jobs down for the duration of a
mutation sweep, and put back exactly the ones that were up.

WHY THIS EXISTS
---------------
While `mutation_sweep.py` runs, one `tools/*.py` is mutated on disk at any instant.
`tests/conftest.py` refuses to run in that state, which is the LOUD half of the problem.
The SILENT half is launchd. Measured 2026-09-01 against the installed plists, an
18:00 -> ~04:00 sweep overlaps:

    gmail-fetch           every 900s   ~40 fires inside the window
    gmail-fetch-personal  every 900s   ~40 fires inside the window
    granola-auto-debrief  every 3h     18:20, 21:20, 00:20, 03:20
    detector-scan         daily 03:20  inside the window

Every one of them shells into a `tools/*.py` that the sweep mutates. `gmail_fetch.py`
alone carries 377 mutants, so during its own measurement window a fetch job would run
mutated mail-handling code against real Gmail, hundreds of times, and nothing anywhere
would report it. The previous sweep handled this with an ad-hoc shell wrapper that no
longer exists in the tree; its absence is invisible until it costs real data.

WHY NOT A GUARD INSIDE THE TOOLS
--------------------------------
The obvious alternative is `refuse_if_a_mutation_backup_exists()` at the top of each
scheduled tool. It cannot work here: mutating that guard's own `if` is precisely what
the sweep does. A protection that lives in the file under mutation is not a protection.
Unloading the jobs puts the mechanism outside the blast radius.

THE ONE PROPERTY THAT MATTERS IS RESTORE
----------------------------------------
Turning jobs off is trivial and turning them back on is the whole risk: a sweep that
dies between the two leaves Nick's mail fetch silently off. Four things defend that:

  1. The marker file is written BEFORE the first bootout, so a crash at any point after
     leaves a record of what is owed. A crash before it means nothing went down.
  2. Only jobs that were ACTUALLY loaded and ACTUALLY booted out are recorded. Restore
     never starts a job the user had deliberately unloaded.
  3. A failed restore KEEPS the marker. Stuck and loud beats clean and wrong.
  4. Recovery does not depend on the sweep ever running again:
     `mutation_sweep` restores a stranded marker at startup, and
     `check_automation_health` (daily 08:00) restores it when no sweep is in flight.

This module never calls launchctl through a shell and takes its runner by injection, so
the tests exercise the real call construction rather than a mock of it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from pathlib import Path

JOB_PREFIX = "com.nickmagnuson.jobsearch."
SWEEP_LABEL = JOB_PREFIX + "mutation-sweep"

# Where launchd reads plists from. Quiescing boots a job out of the running domain; the
# restore has to hand launchctl a file path again, and this is the installed copy, not
# the tracked one in tools/launchd/ (which launchd has never been pointed at).
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# Default marker location: beside the sweep's own state, so a human looking at why the
# baseline stopped finds the outstanding debt in the same directory.
DEFAULT_MARKER = Path("output/analysis/082626-mutation-baseline/.quiesced-jobs.json")


def _domain() -> str:
    return f"gui/{os.getuid()}"


def quiescible_labels(repo_root: Path) -> list[str]:
    """Every jobsearch launchd label except the sweep's own, derived from the plists.

    Derived, not hardcoded, for the same reason `check_automation_health.expected_jobs`
    is: a list written here goes stale the first time a job is added, and the failure is
    silent -- the new job simply keeps firing through the sweep.

    SWEEP_LABEL is excluded because booting out the sweep's own job kills the sweep
    mid-tool, which strands a `.mutation_backup` and makes the next run refuse at
    conftest.
    """
    plist_dir = repo_root / "tools" / "launchd"
    if not plist_dir.is_dir():
        return []
    labels = set()
    for p in plist_dir.glob(f"{JOB_PREFIX}*.plist"):
        label = p.name[:-len(".plist")]
        if label != SWEEP_LABEL:
            labels.add(label)
    return sorted(labels)


def loaded_labels(runner=subprocess.run) -> set[str]:
    """Labels currently loaded, from `launchctl list`.

    A launchctl that cannot be run returns the empty set, which makes quiesce a no-op
    rather than an exception: failing to take jobs down costs a contaminated
    measurement, while raising here would abort a 10-hour sweep before it started.
    """
    try:
        out = runner(["launchctl", "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return set()
    found = set()
    for line in (out.stdout or "").splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2].strip().startswith(JOB_PREFIX):
            found.add(cols[2].strip())
    return found


def is_quiesced(marker: Path) -> bool:
    """True while a quiesce is outstanding. Used by check_automation_health to tell a
    deliberate takedown apart from the 2026-06 die-off it also watches for."""
    return Path(marker).exists()


def _read_marker(marker: Path) -> tuple[list[str], list[str]]:
    """(labels, notes). A corrupt marker yields no labels and a note.

    The marker is written by a process that can be SIGKILLed mid-write. Raising here
    would mean a truncated file permanently blocks every future restore.
    """
    try:
        data = json.loads(Path(marker).read_text(encoding="utf-8"))
        return list(data.get("labels") or []), []
    except (OSError, ValueError, AttributeError) as exc:
        return [], [f"marker at {marker} is unreadable ({exc}); "
                    f"restore jobs by hand with: bash tools/launchd/install.sh install"]


def quiesce(repo_root: Path, marker: Path, runner=subprocess.run) -> dict:
    """Unload every loaded jobsearch job (except the sweep) and record what went down.

    Restores a stranded marker FIRST. A previous sweep killed with SIGKILL leaves its
    marker behind; quiescing on top of it would overwrite the only record of what that
    run owed, stranding those jobs permanently.
    """
    repo_root, marker = Path(repo_root), Path(marker)
    notes: list[str] = []
    if is_quiesced(marker):
        prior = restore(repo_root, marker, runner=runner)
        notes.append(f"restored {len(prior['restored'])} job(s) stranded by a previous run")
        notes.extend(prior["notes"])

    candidates = set(quiescible_labels(repo_root)) & loaded_labels(runner=runner)

    # Marker BEFORE the first bootout. A crash between the two must still leave a record
    # of the debt; the reverse order can turn jobs off with nothing that says so.
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "labels": sorted(candidates),
        "quiesced_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "why": "mutation sweep in flight; these jobs shell into tools/*.py that are "
               "being mutated on disk",
    }, indent=2), encoding="utf-8")

    quiesced, failed = [], []
    for label in sorted(candidates):
        try:
            r = runner(["launchctl", "bootout", f"{_domain()}/{label}"],
                       capture_output=True, text=True, timeout=30)
            rc = r.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            rc, notes = 1, notes + [f"bootout {label}: {exc}"]
        (quiesced if rc == 0 else failed).append(label)

    # Record only the debts actually incurred. A job that never went down must not be
    # bootstrapped later: at best a no-op, at worst an error that keeps the marker stuck.
    marker.write_text(json.dumps({
        "labels": sorted(quiesced),
        "quiesced_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "failed_to_quiesce": sorted(failed),
        "why": "mutation sweep in flight; these jobs shell into tools/*.py that are "
               "being mutated on disk",
    }, indent=2), encoding="utf-8")

    if failed:
        notes.append(f"{len(failed)} job(s) could NOT be unloaded and will keep firing "
                     f"against a mutated tree: {', '.join(failed)}")
    return {"quiesced": quiesced, "failed": failed, "notes": notes}


def restore(repo_root: Path, marker: Path, runner=subprocess.run) -> dict:
    """Re-bootstrap every job recorded in the marker; clear it only on full success.

    Idempotent, and a missing marker is a no-op -- this is called from a `finally`, from
    the next sweep's startup, and from the daily health check, and any of them may run
    when there is nothing owed.
    """
    repo_root, marker = Path(repo_root), Path(marker)
    if not marker.exists():
        return {"restored": [], "failed": [], "notes": []}

    labels, notes = _read_marker(marker)
    restored, failed = [], []
    for label in labels:
        plist = LAUNCH_AGENTS / f"{label}.plist"
        if not plist.exists():
            failed.append(label)
            notes.append(f"{label}: no plist at {plist}; cannot restore it. Reinstall "
                         f"with: bash tools/launchd/install.sh install")
            continue
        try:
            r = runner(["launchctl", "bootstrap", _domain(), str(plist)],
                       capture_output=True, text=True, timeout=30)
            rc = r.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            rc = 1
            notes.append(f"bootstrap {label}: {exc}")
        (restored if rc == 0 else failed).append(label)

    if failed:
        # Keep the marker. A partial restore that deletes its own record is how a job
        # stays off forever with nothing left to say it should be on.
        notes.append(f"{len(failed)} job(s) still down; marker kept at {marker} so the "
                     f"next sweep or health check retries.")
    else:
        marker.unlink(missing_ok=True)
    return {"restored": restored, "failed": failed, "notes": notes}
