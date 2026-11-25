#!/usr/bin/env python3
"""tools/suite_record.py status: is a suite running, read from the suite lock itself.

Origin (2026-11-25, overnight run, S6d; Nick: "always fix at the source"): the org decided the
suite slot by parsing `ps`, and that reading was wrong twice in one night. The one-suite lock
(commit 642ef597) already knows. `status` asks the lock: exit 0 when free, exit 1 when held,
with the holder named from its own record.

Every repo here is a throwaway git repo; the lock directory is redirected to tmp_path so no
test touches ~/.cache.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
LEDGER = Path("tools") / ".suite-ledger.jsonl"


@pytest.fixture
def lockdir(tmp_path, monkeypatch):
    d = tmp_path / "locks"
    d.mkdir()
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(d))
    return d


@pytest.fixture
def sr(lockdir, monkeypatch):
    sys.path.insert(0, str(TOOLS))
    try:
        mod = importlib.import_module("suite_record")
        il = importlib.import_module("inbox_lock")
    finally:
        sys.path.remove(str(TOOLS))
    monkeypatch.setattr(il, "ACQUISITION_LOG", lockdir / "acquisitions.jsonl")
    return mod


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    return r


def _status(sr, repo, capsys):
    code = sr.main(["--repo-root", str(repo), "status"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _hold(repo, lockdir, holder=None, branch="other-branch"):
    """A separate process holding the suite lock. holder=None: the real suite_lock, which
    names itself. A dict: hold the bare lock and write that dict as the holder record.
    "none": hold the bare lock with no holder record (a run that has not named itself)."""
    if holder is None:
        body = (f"with s.suite_lock(Path({str(repo / LEDGER)!r}), {branch!r}, 0):\n"
                "    print('held', flush=True)\n    time.sleep(60)\n")
    else:
        write = ("" if holder == "none" else
                 f"    s.holder_path(Path({str(repo / LEDGER)!r})).write_text("
                 f"{json.dumps(holder)!r}, encoding='utf-8')\n")
        body = (f"with il.file_lock(Path({str(repo / LEDGER)!r}), timeout=0):\n"
                + write + "    print('held', flush=True)\n    time.sleep(60)\n")
    code = ("import sys, time; from pathlib import Path\n"
            f"sys.path.insert(0, {str(TOOLS)!r})\n"
            "import suite_record as s, inbox_lock as il\n" + body)
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(lockdir), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                         env=env)
    assert p.stdout.readline().strip() == "held"
    return p


def _stop(p):
    p.kill()
    p.wait()


def test_free_lock_exits_0(sr, repo, capsys):
    code, out = _status(sr, repo, capsys)
    assert code == 0
    assert out["status"] == "free"


def test_held_lock_exits_1_and_names_the_holder(sr, repo, lockdir, capsys):
    holder = _hold(repo, lockdir)
    try:
        code, out = _status(sr, repo, capsys)
    finally:
        _stop(holder)
    assert code == 1
    assert out["status"] == "held"
    assert out["branch"] == "other-branch"
    assert out["pid"] == holder.pid and out["pid_alive"] is True
    assert out["started_at"]


def test_a_holder_whose_pid_has_exited_still_reads_held(sr, repo, lockdir, capsys):
    """The lock decides, not the record: a run killed while its pytest holds the lock."""
    dead = {"pid": 2 ** 22 + 12345, "branch": "gone-branch",
            "started_at": "2026-11-25T20:00:00+00:00", "session": ""}
    holder = _hold(repo, lockdir, holder=dead)
    try:
        code, out = _status(sr, repo, capsys)
    finally:
        _stop(holder)
    assert code == 1 and out["status"] == "held"
    assert out["branch"] == "gone-branch" and out["pid_alive"] is False


def test_a_held_lock_with_no_holder_record_reads_held(sr, repo, lockdir, capsys):
    holder = _hold(repo, lockdir, holder="none")
    try:
        code, out = _status(sr, repo, capsys)
    finally:
        _stop(holder)
    assert code == 1 and out["status"] == "held"
    assert out["branch"] is None and out["pid"] is None
    assert out["pid_alive"] is None, "no recorded pid: liveness is unknown, not false"


def test_a_stale_holder_record_on_a_free_lock_reads_free(sr, repo, capsys):
    """A record left behind does not make the slot held: only the lock does."""
    sr.holder_path(repo / LEDGER).write_text(json.dumps(
        {"pid": os.getpid(), "branch": "stale", "started_at": "x"}), encoding="utf-8")
    code, out = _status(sr, repo, capsys)
    assert code == 0 and out["status"] == "free"


def test_status_writes_no_record(sr, repo, lockdir, capsys):
    hp = sr.holder_path(repo / LEDGER)
    _status(sr, repo, capsys)
    assert not hp.exists()
    assert not (repo / LEDGER).exists()
    holder = _hold(repo, lockdir)
    try:
        before = hp.read_bytes()
        _status(sr, repo, capsys)
        assert hp.read_bytes() == before
    finally:
        _stop(holder)


def test_status_releases_the_lock_it_probed(sr, repo, lockdir, capsys):
    """After a free status, a run can still take the lock at once."""
    _status(sr, repo, capsys)
    holder = _hold(repo, lockdir)
    _stop(holder)


def test_cli_exit_codes(repo, lockdir):
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(lockdir), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, str(TOOLS / "suite_record.py"), "--repo-root", str(repo), "status"]
    free = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert free.returncode == 0 and json.loads(free.stdout)["status"] == "free"
    holder = _hold(repo, lockdir)
    try:
        held = subprocess.run(cmd, capture_output=True, text=True, env=env)
    finally:
        _stop(holder)
    assert held.returncode == 1 and json.loads(held.stdout)["status"] == "held"


def test_not_a_git_repository_exits_2_not_held(sr, tmp_path, capsys):
    """An error is neither free (0) nor held (1): the grant reads exit 1 as held."""
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out = _status(sr, plain, capsys)
    assert code == 2
    assert out["status"] == "error" and out["code"] == "git_error"


def test_an_unusable_lock_location_exits_2_not_held(sr, repo, tmp_path, monkeypatch, capsys):
    """Codex S6d r3 F1: an error creating or opening the lock sidecar escaped as a traceback,
    exit 1, which reads as held. The lock directory here is a regular file."""
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x", encoding="utf-8")
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(not_a_dir))
    code, out = _status(sr, repo, capsys)
    assert code == 2
    assert out["status"] == "error" and out["code"] == "lock_error"


def test_status_inside_this_process_s_own_hold_reads_held(sr, repo, capsys):
    """Codex S6d r4 F1 (P0): file_lock is re-entrant within a thread, so a probe made while
    this same thread holds the suite lock entered without a kernel attempt and read free."""
    with sr.suite_lock(repo / LEDGER, "this-branch", 0):
        code, out = _status(sr, repo, capsys)
    assert code == 1 and out["status"] == "held"
    assert out["branch"] == "this-branch" and out["pid"] == os.getpid()
    code, out = _status(sr, repo, capsys)           # and free again once it is released
    assert code == 0 and out["status"] == "free"
