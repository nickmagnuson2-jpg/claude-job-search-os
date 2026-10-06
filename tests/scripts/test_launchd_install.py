"""Tests for tools/launchd/install.sh: the standing set, and arming / disarming a job.

WHAT THIS PROTECTS. A job that is deliberately not running (the mutation sweep, between
baselines) used to keep its plist in tools/launchd/. `install` builds its list from that
folder and the health watchdog builds its expected set from the same folder, so the
installer loaded the job again and the watchdog reported it missing every day, with
advice to run the installer (2026-10-06). The fix is a folder, not a list: a disarmed
plist lives in tools/launchd/disarmed/ and neither reader looks there.

Runs the real script against a throwaway tree with a recording stand-in for launchctl.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "launchd" / "install.sh"
PREFIX = "com.nickmagnuson.jobsearch."

sys.path.insert(0, str(REPO / "tools"))
import check_automation_health as cah  # noqa: E402
import job_quiesce as jq  # noqa: E402


def _tree(tmp_path, standing=("gmail-fetch",), disarmed=("mutation-sweep",)):
    src = tmp_path / "repo" / "tools" / "launchd"
    (src / "disarmed").mkdir(parents=True)
    for short in standing:
        (src / f"{PREFIX}{short}.plist").write_text(f"standing {short}", encoding="utf-8")
    for short in disarmed:
        (src / "disarmed" / f"{PREFIX}{short}.plist").write_text(
            f"disarmed {short}", encoding="utf-8")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    calls = tmp_path / "launchctl.calls"
    fake = tmp_path / "launchctl"
    fake.write_text(f'#!/bin/bash\necho "$@" >> "{calls}"\n', encoding="utf-8")
    fake.chmod(0o755)
    return src, agents, calls, fake


def _sh(tmp_path, *args):
    src, agents, calls, fake = (tmp_path / "repo" / "tools" / "launchd",
                                tmp_path / "LaunchAgents",
                                tmp_path / "launchctl.calls", tmp_path / "launchctl")
    env = {**os.environ, "LAUNCHD_SOURCE_DIR": str(src), "LAUNCHD_TARGET_DIR": str(agents),
           "LAUNCHCTL": str(fake)}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return r, log


def test_install_leaves_a_disarmed_job_alone(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    r, log = _sh(tmp_path, "install")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in agents.iterdir()) == [f"{PREFIX}gmail-fetch.plist"]
    assert not any("mutation-sweep" in line for line in log), log
    assert any(line.startswith("load ") and "gmail-fetch" in line for line in log)


def test_arm_moves_the_plist_into_the_standing_set_and_loads_it(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    r, log = _sh(tmp_path, "arm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    name = f"{PREFIX}mutation-sweep.plist"
    assert (src / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert not (src / "disarmed" / name).exists(), "armed and disarmed copies both exist"
    assert (agents / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert log[-1] == f"load {agents / name}"


def test_disarm_unloads_removes_and_parks_the_plist(tmp_path):
    src, agents, _, _ = _tree(tmp_path, standing=("gmail-fetch", "mutation-sweep"),
                              disarmed=())
    name = f"{PREFIX}mutation-sweep.plist"
    (agents / name).write_text("installed", encoding="utf-8")
    r, log = _sh(tmp_path, "disarm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    assert log == [f"unload {agents / name}"]
    assert not (agents / name).exists(), "the installed copy is what launchd reads"
    assert not (src / name).exists()
    assert (src / "disarmed" / name).read_text(encoding="utf-8") == "standing mutation-sweep"
    # ...and the installer no longer sees it.
    r, log = _sh(tmp_path, "install")
    assert not (agents / name).exists()


def test_arm_then_disarm_round_trips(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    name = f"{PREFIX}mutation-sweep.plist"
    assert _sh(tmp_path, "arm", "mutation-sweep")[0].returncode == 0
    assert _sh(tmp_path, "disarm", "mutation-sweep")[0].returncode == 0
    assert (src / "disarmed" / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert not (src / name).exists() and not (agents / name).exists()


def test_arming_an_unknown_job_fails_and_loads_nothing(tmp_path):
    _tree(tmp_path)
    r, log = _sh(tmp_path, "arm", "no-such-job")
    assert r.returncode == 1
    assert "no-such-job" in r.stderr
    assert log == []


def test_arm_and_disarm_need_a_job_name(tmp_path):
    _tree(tmp_path)
    for verb in ("arm", "disarm"):
        r, log = _sh(tmp_path, verb)
        assert r.returncode == 1 and "Usage" in r.stderr
        assert log == []


def test_disarming_the_last_standing_job_is_allowed(tmp_path):
    """arm / disarm must not go through the empty-standing-set refusal."""
    src, agents, _, _ = _tree(tmp_path, standing=("mutation-sweep",), disarmed=())
    r, _ = _sh(tmp_path, "disarm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    r, _ = _sh(tmp_path, "install")
    assert r.returncode == 1 and "empty list" in r.stderr


def test_the_watchdog_does_not_expect_a_disarmed_job(tmp_path):
    """Same folder rule, second reader: an unloaded disarmed job is not a fault."""
    _tree(tmp_path)
    assert cah.expected_jobs(tmp_path / "repo") == {"gmail-fetch"}


def test_a_sweep_does_not_quiesce_or_restore_a_disarmed_job(tmp_path):
    """Third reader. Restoring a job nobody loaded would arm it behind Nick's back."""
    _tree(tmp_path, standing=("gmail-fetch",), disarmed=("career-scan",))
    assert jq.quiescible_labels(tmp_path / "repo") == [f"{PREFIX}gmail-fetch"]


def test_the_real_tree_has_no_job_in_both_folders():
    live = REPO / "tools" / "launchd"
    both = sorted({p.name for p in live.glob("*.plist")}
                  & {p.name for p in (live / "disarmed").glob("*.plist")})
    assert not both, f"plist(s) both standing and disarmed: {both}"
