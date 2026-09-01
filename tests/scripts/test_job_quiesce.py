"""Tests for tools/job_quiesce.py.

WHAT THIS PROTECTS. While mutation_sweep runs, one tools/*.py is mutated on disk at any
instant. tests/conftest.py refuses to run in that state -- that is the loud half. The
silent half is launchd: gmail-fetch fires every 900s and granola-auto-debrief every 3h,
both shelling into tools/*.py, so an overnight sweep would execute mutants against real
Gmail and real data files dozens of times with nothing to say so.

The protection cannot live INSIDE the tools being mutated: mutating a guard's own `if`
is exactly what the sweep does. So the jobs are unloaded for the duration and restored
after, and every test here is about the restore actually happening.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import job_quiesce as jq  # noqa: E402


PREFIX = "com.nickmagnuson.jobsearch."


@pytest.fixture(autouse=True)
def fake_launch_agents(tmp_path, monkeypatch):
    """Point LAUNCH_AGENTS at a tmp dir for every test in this file.

    Without this the restore tests pass ONLY on a machine where the real
    ~/Library/LaunchAgents already holds these plists -- green here, red on a fresh
    clone, and green for a reason that has nothing to do with the code under test. It
    also stops the suite from writing into the user's real LaunchAgents directory.
    """
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    for short in ("gmail-fetch", "granola-auto-debrief", "mutation-sweep"):
        (agents / f"{PREFIX}{short}.plist").write_text("x", encoding="utf-8")
    monkeypatch.setattr(jq, "LAUNCH_AGENTS", agents)
    return agents


@pytest.fixture
def repo(tmp_path):
    """A tmp repo with three plists: two ordinary jobs and the sweep's own."""
    d = tmp_path / "tools" / "launchd"
    d.mkdir(parents=True)
    for short in ("gmail-fetch", "granola-auto-debrief", "mutation-sweep"):
        (d / f"{PREFIX}{short}.plist").write_text(
            f'<plist><dict><key>Label</key><string>{PREFIX}{short}</string>'
            f'</dict></plist>', encoding="utf-8")
    return tmp_path


class FakeRunner:
    """Records launchctl invocations and replays a scripted `launchctl list`."""

    def __init__(self, loaded=(), fail_on=()):
        self.loaded = set(loaded)
        self.fail_on = set(fail_on)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "list"]:
            body = "".join(f"-\t0\t{lbl}\n" for lbl in sorted(self.loaded))
            return subprocess.CompletedProcess(cmd, 0, body, "")
        if cmd[1] == "bootout":
            label = cmd[2].split("/")[-1]
            if label in self.fail_on:
                return subprocess.CompletedProcess(cmd, 3, "", "boom")
            self.loaded.discard(label)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1] == "bootstrap":
            label = PREFIX + Path(cmd[3]).name[len(PREFIX):-len(".plist")]
            if label in self.fail_on:
                return subprocess.CompletedProcess(cmd, 3, "", "boom")
            self.loaded.add(label)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


# --------------------------------------------------------------------------
# label selection
# --------------------------------------------------------------------------

def test_quiescible_labels_come_from_the_plists_not_a_hardcoded_list(repo):
    """Same reason check_automation_health derives expected_jobs from plists: a list
    written here goes stale the first time a job is added and nobody notices."""
    assert jq.quiescible_labels(repo) == [
        PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"]


def test_the_sweeps_own_label_is_never_quiescible(repo):
    """Booting out the sweep's own job kills the sweep mid-tool, which strands a
    .mutation_backup and makes the NEXT run refuse at conftest."""
    assert jq.SWEEP_LABEL not in jq.quiescible_labels(repo)


def test_a_new_plist_is_picked_up_with_no_code_change(repo):
    (repo / "tools" / "launchd" / f"{PREFIX}brand-new.plist").write_text("x", encoding="utf-8")
    assert PREFIX + "brand-new" in jq.quiescible_labels(repo)


# --------------------------------------------------------------------------
# quiesce
# --------------------------------------------------------------------------

def test_quiesce_boots_out_every_loaded_job(repo, tmp_path):
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"])
    marker = tmp_path / "marker.json"
    res = jq.quiesce(repo, marker, runner=r)
    assert sorted(res["quiesced"]) == [PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"]
    assert r.loaded == set()


def test_quiesce_records_ONLY_jobs_that_were_actually_loaded(repo, tmp_path):
    """Restore must not START a job the user had deliberately unloaded. Recording the
    plist list rather than the loaded list would silently re-enable it."""
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    marker = tmp_path / "marker.json"
    jq.quiesce(repo, marker, runner=r)
    assert json.loads(marker.read_text())["labels"] == [PREFIX + "gmail-fetch"]


def test_quiesce_never_boots_out_the_sweep_itself(repo, tmp_path):
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch", jq.SWEEP_LABEL])
    jq.quiesce(repo, tmp_path / "m.json", runner=r)
    assert jq.SWEEP_LABEL in r.loaded
    booted = [c[2] for c in r.calls if c[1] == "bootout"]
    assert all(jq.SWEEP_LABEL not in b for b in booted)


def test_quiesce_writes_the_marker_BEFORE_booting_anything_out(repo, tmp_path):
    """A crash between the first bootout and the marker write would leave jobs off with
    no record that anything turned them off -- unrecoverable by definition."""
    marker = tmp_path / "m.json"
    seen = {}

    class Watch(FakeRunner):
        def __call__(self, cmd, **kw):
            if cmd[1:2] == ["bootout"]:
                seen.setdefault("marker_existed", marker.exists())
            return super().__call__(cmd, **kw)

    jq.quiesce(repo, marker, runner=Watch(loaded=[PREFIX + "gmail-fetch"]))
    assert seen["marker_existed"] is True


def test_quiesce_with_a_stranded_marker_restores_first(repo, tmp_path):
    """A SIGKILLed sweep leaves the marker behind. The next run must put the jobs back
    before taking them down again, or the record of what to restore is overwritten."""
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "granola-auto-debrief"]}), encoding="utf-8")
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, marker, runner=r)
    assert any(c[1] == "bootstrap" for c in r.calls)
    # granola was bootstrapped back, then both were quiesced together
    assert sorted(json.loads(marker.read_text())["labels"]) == [
        PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"]


def test_a_bootout_that_fails_is_reported_not_swallowed(repo, tmp_path):
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"], fail_on=[PREFIX + "gmail-fetch"])
    res = jq.quiesce(repo, tmp_path / "m.json", runner=r)
    assert res["failed"] == [PREFIX + "gmail-fetch"]


def test_a_job_that_failed_to_boot_out_is_not_recorded_for_restore(repo, tmp_path):
    """It never went down, so bootstrapping it later is a no-op at best and an error at
    worst. The marker is a list of debts actually incurred."""
    marker = tmp_path / "m.json"
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"],
                   fail_on=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, marker, runner=r)
    assert json.loads(marker.read_text())["labels"] == [PREFIX + "granola-auto-debrief"]


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------

def test_restore_bootstraps_every_recorded_job_and_clears_the_marker(repo, tmp_path):
    marker = tmp_path / "m.json"
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"])
    jq.quiesce(repo, marker, runner=r)
    res = jq.restore(repo, marker, runner=r)
    assert sorted(res["restored"]) == [PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"]
    assert r.loaded == {PREFIX + "gmail-fetch", PREFIX + "granola-auto-debrief"}
    assert not marker.exists()


def test_restore_with_no_marker_is_a_no_op(repo, tmp_path):
    r = FakeRunner()
    res = jq.restore(repo, tmp_path / "absent.json", runner=r)
    assert res["restored"] == []
    assert r.calls == []


def test_restore_is_idempotent(repo, tmp_path):
    marker = tmp_path / "m.json"
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, marker, runner=r)
    jq.restore(repo, marker, runner=r)
    second = jq.restore(repo, marker, runner=r)
    assert second["restored"] == []


def test_a_failed_restore_KEEPS_the_marker_so_the_next_pass_retries(repo, tmp_path):
    """Deleting the marker on a partial restore is how a job stays off forever with
    nothing left to say it should be on. Loud and stuck beats quiet and wrong."""
    marker = tmp_path / "m.json"
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, marker, runner=r)
    r.fail_on = {PREFIX + "gmail-fetch"}
    res = jq.restore(repo, marker, runner=r)
    assert res["failed"] == [PREFIX + "gmail-fetch"]
    assert marker.exists()


def test_restore_reports_a_recorded_job_whose_plist_has_since_vanished(repo, tmp_path):
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "deleted-job"]}), encoding="utf-8")
    res = jq.restore(repo, marker, runner=FakeRunner())
    assert res["failed"] == [PREFIX + "deleted-job"]
    assert "plist" in res["notes"][0].lower()


def test_restore_survives_a_corrupt_marker_without_raising(repo, tmp_path):
    """The marker is written by a process that may be killed mid-write. A guard that
    raises here would block the sweep from ever restoring anything again."""
    marker = tmp_path / "m.json"
    marker.write_text("{not json", encoding="utf-8")
    res = jq.restore(repo, marker, runner=FakeRunner())
    assert res["restored"] == []
    assert res["notes"]


def test_is_quiesced_reports_whether_a_marker_is_outstanding(repo, tmp_path):
    marker = tmp_path / "m.json"
    assert jq.is_quiesced(marker) is False
    jq.quiesce(repo, marker, runner=FakeRunner(loaded=[PREFIX + "gmail-fetch"]))
    assert jq.is_quiesced(marker) is True


# --- mutation-driven: the assertions above were green while these behaviours were free
#     to change. Each test here killed a specific surviving mutant on 2026-09-01.

def test_the_bootout_target_is_the_users_own_gui_domain(repo, tmp_path):
    """`launchctl bootout <domain>/<label>`. A wrong domain does not raise, it simply
    fails to unload, and the sweep then runs with the jobs still live."""
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, tmp_path / "m.json", runner=r)
    target = [c[2] for c in r.calls if c[1] == "bootout"][0]
    assert target == f"gui/{os.getuid()}/{PREFIX}gmail-fetch"


def test_quiescible_labels_returns_an_empty_LIST_when_there_is_no_plist_dir(tmp_path):
    """Not None. The caller does `set(quiescible_labels(...)) & loaded`, so None is a
    TypeError that aborts the sweep before a single tool is measured."""
    assert jq.quiescible_labels(tmp_path / "nope") == []


def test_loaded_labels_returns_an_empty_SET_when_launchctl_cannot_be_run(repo):
    """Same shape, same consequence: a set intersection against None raises."""
    def broken(*a, **k):
        raise OSError("no launchctl")
    assert jq.loaded_labels(runner=broken) == set()


def test_launchctl_list_lines_for_other_software_are_ignored(repo, tmp_path):
    """`launchctl list` is the whole machine's job table. Treating every row as ours
    would try to boot out unrelated system agents."""
    class Noisy(FakeRunner):
        def __call__(self, cmd, **kw):
            if cmd[:2] == ["launchctl", "list"]:
                return subprocess.CompletedProcess(
                    cmd, 0, f"-\t0\tcom.apple.something\n-\t0\t{PREFIX}gmail-fetch\n", "")
            return super().__call__(cmd, **kw)

    r = Noisy(loaded=[PREFIX + "gmail-fetch"])
    assert jq.loaded_labels(runner=r) == {PREFIX + "gmail-fetch"}


def test_a_clean_start_does_not_claim_to_have_restored_anything(repo, tmp_path):
    """With no marker there is no prior debt. Reporting 'restored 0 jobs' every night
    trains the reader to skim the line that matters when it is not zero."""
    res = jq.quiesce(repo, tmp_path / "m.json",
                     runner=FakeRunner(loaded=[PREFIX + "gmail-fetch"]))
    assert not any("stranded" in n for n in res["notes"])


def test_recovering_a_stranded_marker_says_so_in_the_notes(repo, tmp_path):
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "granola-auto-debrief"]}),
                      encoding="utf-8")
    res = jq.quiesce(repo, marker, runner=FakeRunner(loaded=[PREFIX + "gmail-fetch"]))
    assert any("stranded" in n for n in res["notes"])


def test_notes_from_the_recovery_are_carried_forward_not_dropped(repo, tmp_path):
    """The recovery's own failures are the caller's only evidence that a job it thinks
    is running is actually still down."""
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "deleted-job"]}), encoding="utf-8")
    res = jq.quiesce(repo, marker, runner=FakeRunner())
    assert any("deleted-job" in n for n in res["notes"])


def test_the_marker_directory_is_created_when_it_does_not_exist_yet(repo, tmp_path):
    """A first run against a fresh state dir must not die writing its own marker."""
    marker = tmp_path / "brand" / "new" / "m.json"
    jq.quiesce(repo, marker, runner=FakeRunner(loaded=[PREFIX + "gmail-fetch"]))
    assert marker.exists()


def test_a_failed_bootout_is_named_in_the_notes_not_only_counted(repo, tmp_path):
    """A job that stayed up keeps firing against a mutated tree; the log has to say
    which one so the morning read is actionable."""
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"], fail_on=[PREFIX + "gmail-fetch"])
    res = jq.quiesce(repo, tmp_path / "m.json", runner=r)
    assert any("gmail-fetch" in n for n in res["notes"])


def test_a_fully_successful_quiesce_adds_no_failure_note(repo, tmp_path):
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    res = jq.quiesce(repo, tmp_path / "m.json", runner=r)
    assert not any("could NOT" in n for n in res["notes"])


def test_restore_with_no_marker_says_nothing_at_all(repo, tmp_path):
    """Silence is the signal that nothing was owed. A note here would be indistinguishable
    from a real recovery in the health check's warning list."""
    res = jq.restore(repo, tmp_path / "absent.json", runner=FakeRunner())
    assert res["notes"] == [] and res["failed"] == []


def test_a_job_whose_plist_vanished_is_never_counted_as_restored(repo, tmp_path):
    """Falling through to bootstrap a path that does not exist would report the job as
    both failed and restored, and the 'restored' half is what a reader believes."""
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "deleted-job"]}), encoding="utf-8")
    res = jq.restore(repo, marker, runner=FakeRunner())
    assert res["restored"] == []


def test_a_bootstrap_that_raises_is_recorded_in_the_notes(repo, tmp_path):
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"labels": [PREFIX + "gmail-fetch"]}), encoding="utf-8")

    def raising(cmd, **kw):
        raise OSError("launchctl vanished")

    res = jq.restore(repo, marker, runner=raising)
    assert res["failed"] == [PREFIX + "gmail-fetch"]
    assert any("launchctl vanished" in n for n in res["notes"]), (
        "the exception text is the only evidence of WHY the job is still down")


def test_a_kept_marker_explains_itself(repo, tmp_path):
    """'Still down, marker kept, next pass retries' is the whole recovery contract; a
    silent kept marker looks like a bug to whoever finds it."""
    marker = tmp_path / "m.json"
    r = FakeRunner(loaded=[PREFIX + "gmail-fetch"])
    jq.quiesce(repo, marker, runner=r)
    r.fail_on = {PREFIX + "gmail-fetch"}
    res = jq.restore(repo, marker, runner=r)
    assert any("marker kept" in n for n in res["notes"])
