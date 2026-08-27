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
    r = subprocess.run(
        [sys.executable, str(MOD_PATH), "--repo-root", str(repo), "--stall-hours", "4"],
        capture_output=True, text=True)
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
