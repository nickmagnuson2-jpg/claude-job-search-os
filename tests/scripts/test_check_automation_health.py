"""Tests for tools/check_automation_health.py — the launchd watchdog.

Focus: the missing/unloaded-job detection added after the fable-audit found the
watchdog could not detect a specific job vanishing from `launchctl list` (only a
total ZERO-jobs wipeout). That blind spot let the 2026-06 die-off run 3 weeks.
"""
import importlib.util
import subprocess
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
