#!/usr/bin/env python3
"""tools/suite_record.py run: one suite at a time, and a refusal names the holder.

Origin (2026-11-21, orchestration.md 18:55, Fix A; C2 in output/overnight-112126/DECISIONS.md):
two suite_record runs overlapped twice in one night, and the org's pgrep check missed
suite_record runs. The slot was prose held in the org's head. The lock moves it into the run
path: a second `run` refuses with the holder named, or with --wait queues behind it.

Every repo here is a throwaway git repo with a tiny pytest suite; the lock directory is
redirected to tmp_path so no test touches ~/.cache.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
LEDGER = Path("tools") / ".suite-ledger.jsonl"
PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_bad():\n    assert False\n"


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


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _commit(repo, files, msg):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "commit.gpgsign", "false")
    _commit(r, {"tests/test_base.py": PASSING, "README": "base\n"}, "base")
    return r


@pytest.fixture
def tmp_parent(tmp_path, monkeypatch):
    d = tmp_path / "tmpparent"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def _branch(repo, name, files):
    _git(repo, "checkout", "-q", "-b", name, "main")
    sha = _commit(repo, files, f"on {name}")
    _git(repo, "checkout", "-q", "main")
    return sha


def _run(sr, repo, capsys, *extra):
    code = sr.main(["--repo-root", str(repo), "run", *extra])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, out


def _rows(repo):
    p = repo / LEDGER
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _hold(repo, lockdir, seconds, branch="other-branch"):
    """Start a separate process that holds the suite lock for `seconds`, as a real run would."""
    code = (
        "import sys, time; from pathlib import Path\n"
        f"sys.path.insert(0, {str(TOOLS)!r})\n"
        "import suite_record as s\n"
        f"with s.suite_lock(Path({str(repo / LEDGER)!r}), {branch!r}, 0):\n"
        "    print('held', flush=True)\n"
        f"    time.sleep({seconds})\n"
    )
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(lockdir), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                         env=env)
    assert p.stdout.readline().strip() == "held"
    return p


def test_a_second_run_refuses_and_names_the_holder(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 30)
    try:
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    finally:
        holder.kill()
        holder.wait()
    assert code == 1
    assert out["code"] == "suite_locked"
    assert out["recorded"] is False
    assert out["holder"]["pid"] == holder.pid
    assert out["holder"]["branch"] == "other-branch"
    assert out["holder"]["started_at"]
    assert "other-branch" in out["message"] and str(holder.pid) in out["message"]
    assert _rows(repo) == []
    # Refused before any work: no temp worktree was created.
    assert list(tmp_parent.iterdir()) == []


def test_wait_queues_behind_the_holder_then_records(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 1.5)
    try:
        start = time.monotonic()
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main",
                         "--wait", "60")
        waited = time.monotonic() - start
    finally:
        holder.kill()
        holder.wait()
    assert code == 0, out
    assert out["recorded"] is True
    assert waited >= 1.0
    assert len(_rows(repo)) == 1


def test_wait_that_expires_refuses_with_the_holder(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 30)
    try:
        start = time.monotonic()
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main",
                         "--wait", "0.5")
        waited = time.monotonic() - start
    finally:
        holder.kill()
        holder.wait()
    assert code == 1
    assert out["code"] == "suite_locked"
    assert out["holder"]["pid"] == holder.pid
    assert waited >= 0.4
    assert _rows(repo) == []


def test_the_holder_record_names_this_run_while_it_runs(sr, repo, lockdir, tmp_parent,
                                                        capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    seen = {}
    real = sr._run_pytest

    def spy(wt, junit, log, timeout):
        seen["holder"] = sr.read_holder(repo / LEDGER)
        return real(wt, junit, log, timeout)

    monkeypatch.setattr(sr, "_run_pytest", spy)
    code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    assert code == 0, out
    assert seen["holder"]["pid"] == os.getpid()
    assert seen["holder"]["branch"] == "feat"


def test_the_lock_is_released_after_a_failing_run(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "bad", {"tests/test_bad.py": FAILING})
    _branch(repo, "good", {"tests/test_good.py": PASSING})
    code, out = _run(sr, repo, capsys, "--branch", "bad", "--base", "main")
    assert code == 1 and out["code"] == "tests_failed"
    # Another process can take the lock at once, so the failing run did not keep it.
    holder = _hold(repo, lockdir, 0)
    holder.wait()
    code, out = _run(sr, repo, capsys, "--branch", "good", "--base", "main")
    assert code == 0, out


def test_check_does_not_take_the_lock(sr, repo, lockdir, tmp_parent, capsys):
    # check only reads the ledger; a gate must not stall because a suite is running.
    tip = _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 30)
    try:
        code = sr.main(["--repo-root", str(repo), "check", "--tip", tip, "--base", "main"])
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    finally:
        holder.kill()
        holder.wait()
    assert code == 1 and out["recorded"] is False


def test_a_negative_wait_is_a_usage_error(sr, repo, lockdir, capsys):
    with pytest.raises(SystemExit) as e:
        sr.main(["--repo-root", str(repo), "run", "--branch", "x", "--wait", "-1"])
    assert e.value.code == 2

