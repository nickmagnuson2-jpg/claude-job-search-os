"""Tests for tools/check_automation_health.py — the launchd watchdog.

Focus: the missing/unloaded-job detection added after the fable-audit found the
watchdog could not detect a specific job vanishing from `launchctl list` (only a
total ZERO-jobs wipeout). That blind spot let the 2026-06 die-off run 3 weeks.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "tools" / "check_automation_health.py"

spec = importlib.util.spec_from_file_location("check_automation_health", MOD_PATH)
cah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cah)

PREFIX = "com.nickmagnuson.jobsearch."


def _mk_repo(tmp_path, job_shorts):
    """Build a fake repo_root with tools/launchd/<prefix><short>.plist files."""
    plist_dir = tmp_path / "tools" / "launchd"
    plist_dir.mkdir(parents=True)
    for short in job_shorts:
        (plist_dir / f"{PREFIX}{short}.plist").write_text("<plist/>", encoding="utf-8")
    return tmp_path


def _fake_launchctl(loaded):
    """Return a fake subprocess.run producing `launchctl list` output.

    loaded: list of (exit_code, short) for jobs that ARE loaded.
    """
    lines = ["PID\tStatus\tLabel"]  # header-ish noise, filtered out
    for exit_code, short in loaded:
        lines.append(f"-\t{exit_code}\t{PREFIX}{short}")

    class _R:
        stdout = "\n".join(lines)

    def _run(*a, **k):
        return _R()

    return _run


def test_missing_expected_job_warns(tmp_path, monkeypatch):
    """A job with a plist but absent from launchctl list must produce a warning.

    This is the regression: without the expected-vs-loaded diff, a vanished job
    is invisible while the others report exit 0.
    """
    repo = _mk_repo(tmp_path, ["gmail-fetch", "career-scan"])
    # career-scan is NOT in the loaded set — simulating the die-off.
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([(0, "gmail-fetch")]))
    entries, warnings = cah.check_jobs(repo)
    assert any("career-scan" in w and "not loaded" in w for w in warnings), warnings
    # gmail-fetch is healthy and loaded — no warning about it.
    assert not any("gmail-fetch" in w for w in warnings), warnings


def test_all_expected_loaded_no_warning(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path, ["gmail-fetch", "career-scan"])
    monkeypatch.setattr(
        subprocess, "run", _fake_launchctl([(0, "gmail-fetch"), (0, "career-scan")])
    )
    entries, warnings = cah.check_jobs(repo)
    assert warnings == [], warnings
    assert {e["job"] for e in entries} == {"gmail-fetch", "career-scan"}


def test_nonzero_exit_still_warns(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([(78, "gmail-fetch")]))
    entries, warnings = cah.check_jobs(repo)
    assert any("78" in w and "gmail-fetch" in w for w in warnings), warnings


def test_zero_jobs_loaded_warns_once(tmp_path, monkeypatch):
    """Total wipeout: keep the original 'automation is off' warning and do NOT
    also emit a per-job missing warning for every plist (avoid double-counting)."""
    repo = _mk_repo(tmp_path, ["gmail-fetch", "career-scan"])
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([]))
    entries, warnings = cah.check_jobs(repo)
    assert len(warnings) == 1
    assert "automation is off" in warnings[0]


def test_expected_jobs_derives_from_plists(tmp_path):
    repo = _mk_repo(tmp_path, ["a", "b", "c"])
    assert cah.expected_jobs(repo) == {"a", "b", "c"}


def test_expected_jobs_missing_dir_returns_empty(tmp_path):
    assert cah.expected_jobs(tmp_path) == set()


def test_inbox_alert_written_when_warnings(tmp_path):
    alert = cah.write_inbox_alert(tmp_path, ["job X is down", "gmail stalled"])
    assert alert is not None and alert.exists()
    text = alert.read_text(encoding="utf-8")
    assert "job X is down" in text and "gmail stalled" in text
    assert "automation-health-alert" in alert.name
    # Atomic: no temp orphan.
    assert list((tmp_path / "inbox").glob("*.tmp")) == []


def test_inbox_alert_none_and_cleanup_when_healthy(tmp_path):
    # Pre-seed a stale same-day alert, then a healthy run should remove it.
    first = cah.write_inbox_alert(tmp_path, ["transient outage"])
    assert first.exists()
    result = cah.write_inbox_alert(tmp_path, [])
    assert result is None
    assert not first.exists()


def test_inbox_alert_no_dir_created_when_healthy_and_nothing_pending(tmp_path):
    result = cah.write_inbox_alert(tmp_path, [])
    assert result is None
    # A healthy first run should not spuriously create inbox/.
    assert not (tmp_path / "inbox").exists()


# --- stalled long runs -------------------------------------------------------
#
# THE INCIDENT (2026-08-26/27). The corpus mutation sweep was stopped by hand at 22:45
# and never restarted. It sat dead all night; morning found 3 of 109 tools banked and
# nothing had noticed, because the only thing watching the sweep was the sweep.
#
# That is the same shape as the 2026-06 launchd die-off this whole tool exists for: an
# unattended job stops, and the observer is inside the thing that stopped. The sweep is
# resumable and now spans several nights, so "incomplete" is normal and "incomplete AND
# idle AND not running" is the reportable state.

import json as _json
import os as _os
import time as _time


def _mk_run(tmp_path, name="082626-mutation-baseline", total=10, banked=0,
            idle_hours=0.0, with_results=True):
    """A state dir shaped like a real sweep: targets.json + baseline.jsonl."""
    state = tmp_path / "output" / "analysis" / name
    state.mkdir(parents=True)
    (state / "targets.json").write_text(_json.dumps(
        [{"tool": f"tools/t{i}.py", "mutants": 5} for i in range(total)]), encoding="utf-8")
    if with_results:
        results = state / "baseline.jsonl"
        results.write_text("".join(
            _json.dumps({"tool": f"tools/t{i}.py"}) + "\n" for i in range(banked)),
            encoding="utf-8")
        stamp = _time.time() - idle_hours * 3600
        _os.utime(results, (stamp, stamp))
    else:
        stamp = _time.time() - idle_hours * 3600
        _os.utime(state / "targets.json", (stamp, stamp))
    return tmp_path


def test_an_idle_incomplete_run_with_no_process_is_reported_stalled(tmp_path):
    """The 2026-08-27 case, exactly: banked far short of total, nothing running, hours
    of silence. This is the warning that would have caught it at 08:00."""
    repo = _mk_run(tmp_path, total=109, banked=3, idle_hours=10.7)
    entries, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert len(warnings) == 1
    assert "STALLED" in warnings[0]
    assert "3 of 109" in warnings[0], "the counts are the whole point of the alert"
    assert "10.7h" in warnings[0]
    assert entries[0] == {"run": "082626-mutation-baseline", "banked": 3, "total": 109,
                          "running": False, "idle_hours": 10.7}


def test_the_stall_warning_says_it_is_resumable(tmp_path):
    """Without that, the obvious reading is 'start over', which throws away the work
    already banked and is why the resume feature exists."""
    repo = _mk_run(tmp_path, total=109, banked=3, idle_hours=10.0)
    _, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert "resumable" in warnings[0]


def test_a_run_in_progress_is_never_reported(tmp_path):
    """A long sweep legitimately banks nothing for over an hour on a slow tool. Warning
    while it works is how a watchdog trains you to ignore it."""
    repo = _mk_run(tmp_path, total=109, banked=3, idle_hours=48.0)
    entries, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: True)
    assert warnings == []
    assert entries[0]["running"] is True


def test_a_completed_run_is_never_reported(tmp_path):
    repo = _mk_run(tmp_path, total=10, banked=10, idle_hours=100.0)
    entries, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert warnings == []
    assert entries[0]["banked"] == 10


def test_a_just_stopped_run_is_inside_the_grace_window(tmp_path):
    """Somebody is probably still at the keyboard. Nagging instantly is noise."""
    repo = _mk_run(tmp_path, total=10, banked=2, idle_hours=0.5)
    _, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert warnings == []


def test_a_run_that_never_banked_anything_still_stalls(tmp_path):
    """The worst case and the easiest to miss: it died before writing a results file,
    so there is no progress timestamp at all. Fall back to the target list's own age."""
    repo = _mk_run(tmp_path, total=10, banked=0, idle_hours=9.0, with_results=False)
    entries, warnings = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert len(warnings) == 1 and "0 of 10" in warnings[0]
    assert entries[0]["banked"] == 0


def test_blank_lines_are_not_counted_as_progress(tmp_path):
    """A run killed mid-write leaves ragged output; counting a blank line as a banked
    tool would inflate progress and could mark an incomplete run complete."""
    repo = _mk_run(tmp_path, total=3, banked=1, idle_hours=9.0)
    results = repo / "output" / "analysis" / "082626-mutation-baseline" / "baseline.jsonl"
    results.write_text(results.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    stamp = _time.time() - 9 * 3600
    _os.utime(results, (stamp, stamp))
    entries, _ = cah.check_long_runs(repo, stall_hours=4.0, running=lambda p: False)
    assert entries[0]["banked"] == 1


def test_only_auditable_targets_count_toward_the_total(tmp_path):
    """Self-excluded rows carry mutants -1. Counting them makes the denominator wrong and
    a finished run can never reach it."""
    state = tmp_path / "output" / "analysis" / "run"
    state.mkdir(parents=True)
    (state / "targets.json").write_text(_json.dumps([
        {"tool": "tools/a.py", "mutants": 5},
        {"tool": "tools/self.py", "mutants": -1},
        {"tool": "tools/empty.py", "mutants": 0}]), encoding="utf-8")
    (state / "baseline.jsonl").write_text(_json.dumps({"tool": "tools/a.py"}) + "\n",
                                          encoding="utf-8")
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=0.0,
                                            running=lambda p: False)
    assert entries[0]["total"] == 1
    assert warnings == [], "1 of 1 is complete"


def test_no_declared_runs_is_silence_not_an_error(tmp_path):
    (tmp_path / "output" / "analysis").mkdir(parents=True)
    assert cah.check_long_runs(tmp_path, running=lambda p: False) == ([], [])


def test_a_missing_analysis_tree_is_silence(tmp_path):
    assert cah.check_long_runs(tmp_path, running=lambda p: False) == ([], [])


def test_an_unreadable_target_list_makes_no_stall_claim(tmp_path):
    """Never assert a state you could not read. A watchdog that cries stall on a corrupt
    file gets muted, and then it is not a watchdog."""
    state = tmp_path / "output" / "analysis" / "run"
    state.mkdir(parents=True)
    (state / "targets.json").write_text("{not json", encoding="utf-8")
    assert cah.check_long_runs(tmp_path, running=lambda p: False) == ([], [])


def test_process_detection_never_raises(monkeypatch):
    """The watchdog must survive a broken pgrep. One that dies on a subprocess error is
    exactly as useful as no watchdog."""
    def boom(*a, **k):
        raise OSError("pgrep is gone")
    monkeypatch.setattr(cah.subprocess, "run", boom)
    assert cah._process_running("anything") is False


def test_main_surfaces_long_runs_in_its_output(tmp_path):
    """Wiring guard: the check can be correct and still never reach the reader."""
    repo = _mk_run(tmp_path, total=109, banked=3, idle_hours=10.0)
    (repo / "tools" / "launchd").mkdir(parents=True, exist_ok=True)
    # A stub pgrep that finds nothing. The real one matched the nightly sweep that was
    # RUNNING this test, so the fake run read as alive, no STALLED warning was emitted,
    # and three tools went baseline_red every night at 22:00 while passing all day
    # (diagnosed 2026-09-24). The subprocess must not depend on what else is running.
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    (stub_bin / "pgrep").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (stub_bin / "pgrep").chmod(0o755)
    env = {**_os.environ, "PATH": f"{stub_bin}{_os.pathsep}{_os.environ.get('PATH', '')}"}
    r = subprocess.run(
        [sys.executable, str(MOD_PATH), "--repo-root", str(repo), "--stall-hours", "4"],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)
    assert "long_runs" in out
    assert any("STALLED" in w for w in out["warnings"])


def test_a_target_list_with_nothing_auditable_is_not_a_tracked_run(tmp_path):
    """All rows self-excluded or empty means there is no run to report. Emitting an entry
    anyway puts a phantom '0 of 0' in the output, which reads as a real run at a glance
    and is the kind of noise that gets a watchdog ignored."""
    state = tmp_path / "output" / "analysis" / "run"
    state.mkdir(parents=True)
    (state / "targets.json").write_text(_json.dumps([
        {"tool": "tools/self.py", "mutants": -1},
        {"tool": "tools/empty.py", "mutants": 0}]), encoding="utf-8")
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=0.0,
                                            running=lambda p: False)
    assert entries == [] and warnings == []


# --- stranded quiesce markers -------------------------------------------------
#
# mutation_sweep unloads the scheduled launchd jobs for the duration of a run (they shell
# into the tools/*.py it mutates) and restores them in a `finally`. A SIGKILL has no
# `finally`, so the jobs stay down and the only record is a marker file. This watchdog is
# the recovery path that does not depend on the sweep ever running again.

import json as _qjson  # noqa: E402


def _marker(repo, labels):
    d = repo / "output" / "analysis" / "run-1"
    d.mkdir(parents=True, exist_ok=True)
    m = d / ".quiesced-jobs.json"
    m.write_text(_qjson.dumps({"labels": labels}), encoding="utf-8")
    return m


def test_no_marker_means_nothing_to_say(tmp_path):
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    entries, warnings = cah.check_quiesced_jobs(repo, running=lambda _p: False)
    assert entries == [] and warnings == []


def test_a_marker_while_the_sweep_runs_is_expected_not_a_fault(tmp_path):
    """The jobs are down ON PURPOSE for the length of the run. Warning here would train
    Nick to ignore the one alert that matters when the sweep is NOT running."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, ["com.nickmagnuson.jobsearch.gmail-fetch"])
    called = []
    entries, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: True,
        restorer=lambda *a, **k: called.append(1) or {"restored": [], "failed": [],
                                                      "notes": []})
    assert warnings == []
    assert called == [], "a live sweep's jobs must not be restored underneath it"
    assert entries[0]["sweep_running"] is True


def test_a_marker_with_no_sweep_running_restores_the_jobs(tmp_path):
    """The SIGKILL case. Nothing else puts these back: the `finally` never ran, and the
    next sweep is 24h away."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, ["com.nickmagnuson.jobsearch.gmail-fetch"])
    entries, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: False,
        restorer=lambda *a, **k: {"restored": ["com.nickmagnuson.jobsearch.gmail-fetch"],
                                  "failed": [], "notes": []})
    assert entries[0]["restored"] == ["com.nickmagnuson.jobsearch.gmail-fetch"]
    assert any("gmail-fetch" in w for w in warnings)


def test_a_restore_that_fails_warns_loudly_and_names_the_jobs(tmp_path):
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, ["com.nickmagnuson.jobsearch.gmail-fetch"])
    _, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: False,
        restorer=lambda *a, **k: {"restored": [],
                                  "failed": ["com.nickmagnuson.jobsearch.gmail-fetch"],
                                  "notes": []})
    assert any("STILL DOWN" in w.upper() and "gmail-fetch" in w for w in warnings)


def test_a_watchdog_never_raises_on_a_restorer_that_blows_up(tmp_path):
    """A watchdog that dies on a broken dependency is exactly as useful as no watchdog -
    the same rule _process_running already follows."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, ["com.nickmagnuson.jobsearch.gmail-fetch"])

    def boom(*a, **k):
        raise RuntimeError("launchctl gone")

    _, warnings = cah.check_quiesced_jobs(repo, running=lambda _p: False, restorer=boom)
    assert any("launchctl gone" in w for w in warnings)


def test_stranded_markers_are_reported_by_main(tmp_path, monkeypatch):
    """Guards the wiring, not just the function: an unwired check is a check that never
    fires, which is the failure this whole file exists to catch."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, ["com.nickmagnuson.jobsearch.gmail-fetch"])
    monkeypatch.setattr(cah, "_process_running", lambda _p: False)
    monkeypatch.setattr(cah.job_quiesce, "restore",
                        lambda *a, **k: {"restored": [], "failed": [],
                                         "notes": ["marker cleared"]})
    monkeypatch.setattr(sys, "argv", ["x", "--repo-root", str(repo)])
    monkeypatch.setattr(cah, "check_jobs", lambda r: ([], []))
    monkeypatch.setattr(cah, "check_gmail", lambda r, h: ([], []))
    cah.main()


def test_the_restorer_error_is_kept_in_the_entry_not_only_the_warning(tmp_path):
    """The JSON is what the launchd log preserves; a warning string alone leaves nothing
    machine-readable to explain why a job is still down."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, [PREFIX + "gmail-fetch"])

    def boom(*a, **k):
        raise RuntimeError("launchctl gone")

    entries, _ = cah.check_quiesced_jobs(repo, running=lambda _p: False, restorer=boom)
    assert "launchctl gone" in entries[0]["error"]


def test_a_restore_that_put_nothing_back_does_not_claim_a_sweep_died(tmp_path):
    """An empty marker is not evidence of a crashed sweep. Warning on it every morning
    is how a real 'the sweep died' alert stops being read."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, [])
    _, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: False,
        restorer=lambda *a, **k: {"restored": [], "failed": [], "notes": []})
    assert warnings == []


def test_a_fully_successful_restore_raises_no_still_down_alarm(tmp_path):
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, [PREFIX + "gmail-fetch"])
    _, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: False,
        restorer=lambda *a, **k: {"restored": [PREFIX + "gmail-fetch"], "failed": [],
                                  "notes": []})
    assert not any("STILL DOWN" in w.upper() for w in warnings)


def test_notes_from_the_restorer_reach_the_warning_list(tmp_path):
    """A kept marker or an unreadable one is reported only through notes. Dropping them
    turns a stuck recovery into a silent one."""
    repo = _mk_repo(tmp_path, ["gmail-fetch"])
    _marker(repo, [PREFIX + "gmail-fetch"])
    _, warnings = cah.check_quiesced_jobs(
        repo, running=lambda _p: False,
        restorer=lambda *a, **k: {"restored": [], "failed": [],
                                  "notes": ["marker kept; next pass retries"]})
    assert any("next pass retries" in w for w in warnings)


# =============================================================================
# 2026-10-06: the watchdog was looking in a directory the sweep left a month ago.
#
# Sweep state moved to output/mutation-state/ and both checks below still globbed
# output/analysis/*/. Every test above plants the OLD path, so the suite stayed green
# while the watchdog could not see a stalled sweep or jobs a killed sweep left unloaded.
# Second defect, same function: "banked" was a count of LINES in an append-only file, so
# a sweep that retried four failing tools every night for weeks read as more than complete.
# =============================================================================

def _mk_canonical(tmp_path, targets, rows, idle_hours=10.0):
    state = tmp_path / "output" / "mutation-state"
    state.mkdir(parents=True)
    (state / "targets.json").write_text(_json.dumps(
        [{"tool": t, "mutants": 5} for t in targets]), encoding="utf-8")
    results = state / "baseline.jsonl"
    results.write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    stamp = _time.time() - idle_hours * 3600
    _os.utime(results, (stamp, stamp))
    return state


def test_a_stalled_run_in_the_canonical_state_directory_is_seen(tmp_path):
    tools = [f"tools/t{i}.py" for i in range(10)]
    _mk_canonical(tmp_path, tools, [{"tool": t, "status": "ok"} for t in tools[:3]])
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=4.0, running=lambda p: False)
    assert len(warnings) == 1 and "STALLED" in warnings[0]
    assert "3 of 10" in warnings[0]
    assert entries[0]["run"] == "mutation-state"


def test_repeated_error_rows_do_not_make_a_run_look_complete(tmp_path):
    """One tool measured, one tool failed six nights running: 1 of 2, not 7 of 2."""
    rows = ([{"tool": "tools/a.py", "status": "survivors"}]
            + [{"tool": "tools/b.py", "status": "error"}] * 6)
    _mk_canonical(tmp_path, ["tools/a.py", "tools/b.py"], rows)
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=4.0, running=lambda p: False)
    assert entries[0]["banked"] == 1 and entries[0]["total"] == 2
    assert len(warnings) == 1 and "1 of 2" in warnings[0]


def test_a_tool_that_failed_after_an_earlier_clean_row_is_not_banked(tmp_path):
    """Last row wins, in both directions: a fresh failure is unmeasured again."""
    rows = [{"tool": "tools/a.py", "status": "ok"},
            {"tool": "tools/a.py", "status": "UNAUDITED_TIMEOUT"}]
    _mk_canonical(tmp_path, ["tools/a.py"], rows)
    entries, _ = cah.check_long_runs(tmp_path, stall_hours=4.0, running=lambda p: False)
    assert entries[0]["banked"] == 0


def test_jobs_left_down_by_a_sweep_in_the_canonical_directory_are_restored(tmp_path):
    state = tmp_path / "output" / "mutation-state"
    state.mkdir(parents=True)
    marker = state / ".quiesced-jobs.json"
    marker.write_text(_json.dumps({"jobs": ["gmail-fetch"]}), encoding="utf-8")
    asked = []

    def restorer(root, m):
        asked.append(m)
        return {"restored": ["gmail-fetch"], "failed": []}

    entries, warnings = cah.check_quiesced_jobs(tmp_path, running=lambda p: False,
                                                restorer=restorer)
    assert asked == [marker], "the marker in the live state directory was never looked at"
    assert entries and entries[0]["run"] == "mutation-state"



# =============================================================================
# 2026-10-06: a job with no recorded exit that is overdue; a sweep parked on purpose.
# =============================================================================

@pytest.fixture(autouse=True)
def _no_real_boot_time(monkeypatch):
    """The last boot counts as evidence a job has not had its turn yet. Pinned to
    "unknown" so no test depends on when the machine running it was restarted."""
    monkeypatch.setattr(cah, "boot_time", lambda: None)


def _plist(tmp_path, short, body):
    d = tmp_path / "tools" / "launchd"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{PREFIX}{short}.plist"
    f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                 f'<key>Label</key><string>{PREFIX}{short}</string>{body}</dict></plist>',
                 encoding="utf-8")
    return f


def _aged(path, hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = _time.time() - hours * 3600
    _os.utime(path, (stamp, stamp))
    return path


EVERY_15_MIN = "<key>StartInterval</key><integer>900</integer>"
DAILY = ("<key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer>"
         "<key>Minute</key><integer>0</integer></dict>")
WEEKLY = ("<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer>"
          "<key>Hour</key><integer>7</integer></dict>")


@pytest.mark.parametrize("body, seconds", [
    (EVERY_15_MIN, 900), (DAILY, 86400), (WEEKLY, 7 * 86400),
    ("<key>StartCalendarInterval</key><dict><key>Minute</key><integer>5</integer></dict>", 3600),
    ("<key>StartCalendarInterval</key><dict><key>Day</key><integer>1</integer></dict>", 31 * 86400),
    ("<key>StartCalendarInterval</key><array><dict><key>Weekday</key><integer>1</integer></dict>"
     "<dict><key>Hour</key><integer>3</integer></dict></array>", 86400),
    ("<key>StartCalendarInterval</key><array>"
     + "".join(f"<dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>0</integer></dict>"
               for h in range(0, 24, 3)) + "</array>", 3 * 3600),
    ("<key>StartCalendarInterval</key><array><dict><key>Hour</key><integer>9</integer></dict>"
     "<dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>"
     "</array>", 11 * 3600 + 1800),
    ("<key>StartCalendarInterval</key><array><dict><key>Hour</key><integer>9</integer></dict>"
     "<dict><key>Hour</key><integer>9</integer></dict></array>", 86400),
    ("", None),
    ("<key>StartInterval</key><integer>0</integer>", None),
    ("<key>StartCalendarInterval</key><dict></dict>", None),
    ("<key>StartCalendarInterval</key><dict><key>Second</key><integer>5</integer></dict>", None),
])
def test_a_schedule_is_reduced_to_its_period(tmp_path, body, seconds):
    assert cah.schedule_seconds(_plist(tmp_path, "j", body)) == seconds


def test_a_plist_that_cannot_be_read_has_no_schedule(tmp_path):
    assert cah.schedule_seconds(tmp_path / "absent.plist") is None


def _no_exit(monkeypatch, short):
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([("-", short)]))


def test_a_job_with_no_recorded_exit_that_is_overdue_warns(tmp_path, monkeypatch):
    """After a reboot every job shows no exit, so "no exit" alone was read as healthy and
    a job that never ran again stayed healthy indefinitely."""
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    _aged(tmp_path / "tools" / "launchd" / "logs" / "gmail-fetch.log", hours=3)
    _no_exit(monkeypatch, "gmail-fetch")
    entries, warnings = cah.check_jobs(tmp_path, launch_agents=tmp_path / "LaunchAgents")
    assert len(warnings) == 1
    assert "gmail-fetch" in warnings[0] and "no recorded exit" in warnings[0]
    assert "3.0h" in warnings[0]
    assert entries[0]["overdue_hours"] == 3.0


def test_a_job_with_no_recorded_exit_that_ran_recently_is_quiet(tmp_path, monkeypatch):
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    _aged(tmp_path / "tools" / "launchd" / "logs" / "gmail-fetch.log", hours=0.2)
    _no_exit(monkeypatch, "gmail-fetch")
    entries, warnings = cah.check_jobs(tmp_path, launch_agents=tmp_path / "LaunchAgents")
    assert warnings == [] and "overdue_hours" not in entries[0]


def test_the_threshold_is_twice_the_schedule(tmp_path, monkeypatch):
    plist = _plist(tmp_path, "daily", DAILY)
    log = tmp_path / "tools" / "launchd" / "logs" / "daily.log"
    _aged(log, hours=47)
    assert cah.overdue_hours(tmp_path, "daily", launch_agents=tmp_path / "none") is None
    _aged(log, hours=49)
    assert cah.overdue_hours(tmp_path, "daily", launch_agents=tmp_path / "none") == 49.0


def test_the_newest_evidence_counts_whichever_file_it_is(tmp_path, monkeypatch):
    """An old log beside a fresh .err, or a plist installed an hour ago, is not overdue."""
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    logs = tmp_path / "tools" / "launchd" / "logs"
    _aged(logs / "gmail-fetch.log", hours=30)
    agents = tmp_path / "LaunchAgents"
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=agents) == 30.0
    _aged(logs / "gmail-fetch.err", hours=0.1)
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=agents) is None
    _aged(logs / "gmail-fetch.err", hours=30)
    _aged(agents / f"{PREFIX}gmail-fetch.plist", hours=0.1)
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=agents) is None


def test_no_evidence_and_no_schedule_are_not_alarms(tmp_path, monkeypatch):
    _plist(tmp_path, "fresh", EVERY_15_MIN)
    assert cah.overdue_hours(tmp_path, "fresh", launch_agents=tmp_path / "none") is None
    _plist(tmp_path, "unscheduled", "")
    _aged(tmp_path / "tools" / "launchd" / "logs" / "unscheduled.log", hours=900)
    assert cah.overdue_hours(tmp_path, "unscheduled", launch_agents=tmp_path / "none") is None


def test_a_job_that_exited_zero_is_not_checked_for_lateness(tmp_path, monkeypatch):
    """A recorded exit of 0 is its own evidence; an old log beside it is a quiet job."""
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    _aged(tmp_path / "tools" / "launchd" / "logs" / "gmail-fetch.log", hours=300)
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([(0, "gmail-fetch")]))
    entries, warnings = cah.check_jobs(tmp_path, launch_agents=tmp_path / "LaunchAgents")
    assert warnings == []


def test_a_nonzero_exit_still_warns_as_before(tmp_path, monkeypatch):
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    monkeypatch.setattr(subprocess, "run", _fake_launchctl([(78, "gmail-fetch")]))
    entries, warnings = cah.check_jobs(tmp_path, launch_agents=tmp_path / "LaunchAgents")
    assert len(warnings) == 1 and "last exited 78" in warnings[0]


def test_a_sweep_parked_on_purpose_is_not_reported_as_stalled(tmp_path):
    tools = [f"tools/t{i}.py" for i in range(10)]
    _mk_canonical(tmp_path, tools, [{"tool": t, "status": "ok"} for t in tools[:3]])
    parked = tmp_path / "tools" / "launchd" / "disarmed"
    parked.mkdir(parents=True)
    (parked / f"{PREFIX}mutation-sweep.plist").write_text("x", encoding="utf-8")
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=4.0, running=lambda p: False)
    assert warnings == []
    assert entries[0]["disarmed"] is True and entries[0]["banked"] == 3


def test_another_job_being_parked_does_not_hide_a_stalled_sweep(tmp_path):
    tools = [f"tools/t{i}.py" for i in range(10)]
    _mk_canonical(tmp_path, tools, [{"tool": t, "status": "ok"} for t in tools[:3]])
    parked = tmp_path / "tools" / "launchd" / "disarmed"
    parked.mkdir(parents=True)
    (parked / f"{PREFIX}career-scan.plist").write_text("x", encoding="utf-8")
    entries, warnings = cah.check_long_runs(tmp_path, stall_hours=4.0, running=lambda p: False)
    assert len(warnings) == 1 and "STALLED" in warnings[0]
    assert "disarmed" not in entries[0]


def test_a_reboot_gives_every_job_a_fresh_turn(tmp_path, monkeypatch):
    """A restart touches neither the logs nor the installed plist, so without the boot
    time a job with week-old logs warned at the first health check after a reboot."""
    _plist(tmp_path, "gmail-fetch", EVERY_15_MIN)
    _aged(tmp_path / "tools" / "launchd" / "logs" / "gmail-fetch.log", hours=200)
    none = tmp_path / "none"
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=none) == 200.0
    just_now = _time.time() - 300
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=none,
                             booted=lambda: just_now) is None
    long_ago = _time.time() - 5 * 3600
    assert cah.overdue_hours(tmp_path, "gmail-fetch", launch_agents=none,
                             booted=lambda: long_ago) == 5.0
    # ...and the check the watchdog actually runs uses it.
    monkeypatch.setattr(cah, "boot_time", lambda: just_now)
    _no_exit(monkeypatch, "gmail-fetch")
    assert cah.check_jobs(tmp_path, launch_agents=none)[1] == []


def test_the_boot_time_alone_never_dates_a_job(tmp_path):
    """No log and no installed plist is still no evidence, however long the uptime."""
    _plist(tmp_path, "fresh", EVERY_15_MIN)
    assert cah.overdue_hours(tmp_path, "fresh", launch_agents=tmp_path / "none",
                             booted=lambda: _time.time() - 900 * 3600) is None


@pytest.mark.parametrize("text, expected", [
    ("{ sec = 1759700000, usec = 123456 } Mon Oct  5 14:33:20 2026", 1759700000.0),
    ("nothing useful", None),
])
def test_the_boot_time_is_parsed_from_sysctl(monkeypatch, text, expected):
    import importlib.util as _u
    spec = _u.spec_from_file_location("cah_boot", cah.__file__)
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _R:
        stdout = text

    asked = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **k: asked.append(cmd) or _R())
    assert mod.boot_time() == expected
    assert asked == [["sysctl", "-n", "kern.boottime"]]


# =============================================================================
# 2026-10-06: paths the mutation run found unprotected. check_gmail had no test at all:
# 20 of its mutants survived, including every one of its warnings.
# =============================================================================

def _gmail_state(tmp_path, fname, payload):
    d = tmp_path / "tools"
    d.mkdir(exist_ok=True)
    (d / fname).write_text(payload if isinstance(payload, str) else _json.dumps(payload),
                           encoding="utf-8")


def _stamp(hours_ago, fmt="%Y-%m-%dT%H:%M:%S"):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(hours=hours_ago)).strftime(fmt)


def test_a_fresh_fetch_is_reported_with_its_age_and_no_warning(tmp_path):
    _gmail_state(tmp_path, ".gmail_state.json", {"last_refresh": _stamp(2)})
    entries, warnings = cah.check_gmail(tmp_path, stale_hours=24.0)
    assert warnings == []
    assert len(entries) == 1 and entries[0]["account"] == "work"
    assert entries[0]["age_hours"] == 2.0
    assert entries[0]["last_refresh"].startswith("20")


def test_a_stale_fetch_warns_with_the_age_and_the_threshold(tmp_path):
    _gmail_state(tmp_path, ".gmail_state_personal.json", {"last_refresh": _stamp(72)})
    entries, warnings = cah.check_gmail(tmp_path, stale_hours=24.0)
    assert [e["account"] for e in entries] == ["personal"]
    assert len(warnings) == 1
    assert "Gmail (personal) last fetched 3.0 days ago (threshold 1d)" in warnings[0]


def test_the_staleness_threshold_is_the_one_passed_in(tmp_path):
    _gmail_state(tmp_path, ".gmail_state.json", {"last_refresh": _stamp(30)})
    assert cah.check_gmail(tmp_path, stale_hours=24.0)[1] != []
    assert cah.check_gmail(tmp_path, stale_hours=48.0)[1] == []


def test_a_missing_or_unparseable_refresh_time_warns_and_is_still_listed(tmp_path):
    _gmail_state(tmp_path, ".gmail_state.json", {"last_refresh": "last tuesday"})
    _gmail_state(tmp_path, ".gmail_state_personal.json", {})
    entries, warnings = cah.check_gmail(tmp_path, stale_hours=24.0)
    assert [(e["account"], e["age_hours"]) for e in entries] == [("work", None), ("personal", None)]
    assert len(warnings) == 2 and all("missing/unparseable" in w for w in warnings)
    assert "(work)" in warnings[0] and "(personal)" in warnings[1]


def test_an_unreadable_state_file_warns_and_the_other_account_is_still_checked(tmp_path):
    _gmail_state(tmp_path, ".gmail_state.json", "{not json")
    _gmail_state(tmp_path, ".gmail_state_personal.json", {"last_refresh": _stamp(1)})
    entries, warnings = cah.check_gmail(tmp_path, stale_hours=24.0)
    assert warnings == ["Gmail (work) state file unreadable: .gmail_state.json"]
    assert [e["account"] for e in entries] == ["personal"]


def test_an_account_with_no_state_file_is_absent_not_a_warning(tmp_path):
    (tmp_path / "tools").mkdir()
    assert cah.check_gmail(tmp_path, stale_hours=24.0) == ([], [])


@pytest.mark.parametrize("text, hours", [
    (None, None), ("", None), ("not a time", None),
])
def test_an_empty_or_unparseable_timestamp_has_no_age(text, hours):
    assert cah._age_hours(text) is hours


@pytest.mark.parametrize("fmt, suffix", [
    ("%Y-%m-%dT%H:%M:%S", ""), ("%Y-%m-%dT%H:%M:%S", ".417149"),
    ("%Y-%m-%dT%H:%M:%S", ".123Z"), ("%Y-%m-%dT%H:%M", ""),
])
def test_every_timestamp_shape_the_fetchers_write_is_aged(fmt, suffix):
    age = cah._age_hours(_stamp(5, fmt) + suffix)
    assert age is not None and 4.9 < age < 5.1


def test_a_date_with_no_time_is_aged_from_midnight():
    from datetime import datetime
    age = cah._age_hours(datetime.now().strftime("%Y-%m-%d"))
    assert age is not None and 0 <= age < 24.1


def test_launchctl_being_unavailable_is_a_warning_with_no_jobs(tmp_path, monkeypatch):
    def gone(*a, **k):
        raise FileNotFoundError("launchctl")

    monkeypatch.setattr(subprocess, "run", gone)
    assert cah.check_jobs(tmp_path) == (
        [], ["Could not run `launchctl list` to check automation health."])


def _quiet_main(tmp_path, monkeypatch, capsys, *flags, warnings=()):
    monkeypatch.setattr(sys, "argv", ["x", "--repo-root", str(tmp_path), *flags])
    monkeypatch.setattr(cah, "check_jobs", lambda r: ([], list(warnings)))
    monkeypatch.setattr(cah, "check_gmail", lambda r, h: ([], []))
    rc = cah.main()
    return rc, _json.loads(capsys.readouterr().out)


def test_main_exits_zero_and_prints_every_section(tmp_path, monkeypatch, capsys):
    rc, out = _quiet_main(tmp_path, monkeypatch, capsys, warnings=["job X is down"])
    assert rc == 0
    assert out["warnings"] == ["job X is down"]
    assert set(out) == {"warnings", "gmail", "jobs", "long_runs", "quiesced_jobs"}


def test_the_inbox_alert_is_written_only_when_asked_for(tmp_path, monkeypatch, capsys):
    _quiet_main(tmp_path, monkeypatch, capsys, warnings=["job X is down"])
    assert not (tmp_path / "inbox").exists(), "wrote an alert without --inbox-on-warn"
    _quiet_main(tmp_path, monkeypatch, capsys, "--inbox-on-warn", warnings=["job X is down"])
    alerts = list((tmp_path / "inbox").glob("*-automation-health-alert.md"))
    assert len(alerts) == 1 and "job X is down" in alerts[0].read_text(encoding="utf-8")


def test_the_watchdog_imports_by_path_from_a_process_that_knows_nothing_about_tools():
    """It is run by launchd and loaded by path in its tests; its sibling imports only work
    because it puts its own directory on sys.path first."""
    import os as _o
    code = ("import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('w', %r)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print(mod.JOB_PREFIX)\n" % cah.__file__)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/",
                       env={k: v for k, v in _o.environ.items() if k != "PYTHONPATH"})
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.strip() == "com.nickmagnuson.jobsearch."

