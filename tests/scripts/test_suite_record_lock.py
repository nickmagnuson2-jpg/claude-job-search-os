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
import signal
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

    def spy(wt, junit, log, timeout, keep_fds=(), **kw):
        seen["holder"] = sr.read_holder(repo / LEDGER)
        return real(wt, junit, log, timeout, keep_fds, **kw)

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



def test_the_holder_record_is_removed_when_the_run_ends(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    assert code == 0, out
    assert not sr.holder_path(repo / LEDGER).exists()


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]", '"a string"'])
def test_an_absent_or_unusable_holder_record_reads_as_empty(sr, repo, lockdir, content):
    hp = sr.holder_path(repo / LEDGER)
    hp.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        hp.unlink(missing_ok=True)
    else:
        hp.write_text(content, encoding="utf-8")
    assert sr.read_holder(repo / LEDGER) == {}


# ---- Grok 585ac8c5 F3: killing the named holder must not free the slot while its pytest runs

def test_killing_the_holder_keeps_the_lock_until_its_pytest_exits(sr, repo, lockdir, tmp_path,
                                                                  tmp_parent, capsys):
    started, done = tmp_path / "started", tmp_path / "done"
    slow = ("import time, pathlib\n"
            "def test_slow():\n"
            f"    pathlib.Path({str(started)!r}).write_text('x')\n"
            "    time.sleep(4)\n"
            f"    pathlib.Path({str(done)!r}).write_text('x')\n")
    _branch(repo, "slow", {"tests/test_slow.py": slow})
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(lockdir), "PYTHONIOENCODING": "utf-8"}
    parent = subprocess.Popen([sys.executable, str(TOOLS / "suite_record.py"), "--repo-root",
                               str(repo), "run", "--branch", "slow", "--base", "main"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert time.monotonic() < deadline, "slow test never started"
            time.sleep(0.05)
        parent.kill()
        parent.wait()
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
        assert code == 1 and out["code"] == "suite_locked"
        assert out["holder"]["pid"] == parent.pid
        assert "has exited; its pytest is still running" in out["message"]
        deadline = time.monotonic() + 60
        while not done.exists():
            assert time.monotonic() < deadline, "slow test never finished"
            time.sleep(0.05)
        time.sleep(0.5)  # let the orphaned pytest exit and drop its copy of the fd
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main", "--wait", "30")
        assert code == 0, out
    finally:
        if parent.poll() is None:
            parent.kill()


def test_a_live_holder_is_not_described_as_exited(sr, repo, lockdir, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 30)
    try:
        _, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    finally:
        holder.kill()
        holder.wait()
    assert "has exited" not in out["message"]


@pytest.mark.parametrize("pid,alive", [(None, False), (0, False), (-5, False), ("12", False),
                                       (os.getpid(), True), (2 ** 22 + 12345, False)])
def test_alive(sr, pid, alive):
    assert sr._alive(pid) is alive


def test_alive_treats_a_process_we_may_not_signal_as_alive(sr, monkeypatch):
    def denied(pid, sig):
        raise PermissionError
    monkeypatch.setattr(sr.os, "kill", denied)
    assert sr._alive(1) is True


def test_pytest_receives_the_held_lock_fd(sr, repo, lockdir, tmp_parent, capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    seen = {}
    real = sr._run_pytest

    def spy(wt, junit, log, timeout, keep_fds=(), **kw):
        seen["fds"] = keep_fds
        seen["held"] = sr.held_fd(repo / LEDGER)
        return real(wt, junit, log, timeout, keep_fds, **kw)

    monkeypatch.setattr(sr, "_run_pytest", spy)
    code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    assert code == 0, out
    assert isinstance(seen["held"], int) and seen["fds"] == (seen["held"],)


# ---- parked 1306.F2 / 1322.F3: --wait accepted NaN and infinity

@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "Infinity", "+inf"])
def test_a_non_finite_wait_is_a_usage_error(sr, repo, lockdir, capsys, value):
    with pytest.raises(SystemExit) as e:
        sr.main(["--repo-root", str(repo), "run", "--branch", "x", "--wait", value])
    assert e.value.code == 2


# ---- parked 1306.F1 / 1322.F4: a stale record named a dead run as the current holder

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_stale_holder_record_is_not_named_as_the_holder(sr, repo, lockdir, tmp_parent,
                                                          capsys):
    """A run killed after its pytest also exited leaves its record behind. The next run takes
    the lock before it rewrites the record, so a refusal in that window must not name the
    dead run (and must not say its pytest is still running)."""
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    dead = _dead_pid()
    holder = _hold(repo, lockdir, 30)        # the new holder, which has written its record
    try:
        sr.holder_path(repo / LEDGER).write_text(json.dumps(   # ...as seen inside the window
            {"pid": dead, "pytest_pid": _dead_pid(), "branch": "gone-branch",
             "started_at": "2026-11-25T00:00:00+00:00"}), encoding="utf-8")
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    finally:
        holder.kill()
        holder.wait()
    assert code == 1 and out["code"] == "suite_locked"
    assert "gone-branch" not in out["message"] and str(dead) not in out["message"]
    assert "has exited" not in out["message"]
    assert "has not named itself yet" in out["message"]
    assert out["holder"] == {}


def test_a_killed_run_whose_pytest_still_runs_is_still_named(sr, repo, lockdir, tmp_parent,
                                                             capsys):
    """The stale rule must not hide the case Grok 585ac8c5 F3 named: the run is dead but its
    pytest is alive and holds the lock."""
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    holder = _hold(repo, lockdir, 30)
    try:
        sr.holder_path(repo / LEDGER).write_text(json.dumps(
            {"pid": _dead_pid(), "pytest_pid": holder.pid, "branch": "orphan-branch",
             "started_at": "2026-11-25T00:00:00+00:00"}), encoding="utf-8")
        code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    finally:
        holder.kill()
        holder.wait()
    assert code == 1 and "orphan-branch" in out["message"]
    assert "has exited; its pytest is still running" in out["message"]


def test_the_holder_record_carries_pytest_pid_while_pytest_runs(sr, repo, lockdir, tmp_parent,
                                                                capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    seen = {}
    real_wait = subprocess.Popen.wait

    def spy(self, timeout=None):
        # pytest's wait, picked by its argv: the gate's git calls also wait with a timeout
        if timeout is not None and "pytest" in " ".join(map(str, self.args)):
            seen.setdefault("holder", sr.read_holder(repo / LEDGER))
            seen.setdefault("child", self.pid)
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", spy)
    code, out = _run(sr, repo, capsys, "--branch", "feat", "--base", "main")
    assert code == 0, out
    assert seen["holder"]["pytest_pid"] == seen["child"]
    assert seen["holder"]["pid"] == os.getpid()


# ---- parked 1294.F1: Ctrl-C unwound the lock while pytest (own session) kept running

def test_an_interrupt_during_the_suite_kills_pytest(sr, tmp_path):
    import _thread
    import threading
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    pidfile = tmp_path / "pytest.pid"
    (wt / "tests" / "test_slow.py").write_text(
        "import os, time, pathlib\n"
        "def test_slow():\n"
        f"    pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n", encoding="utf-8")

    def interrupt_when_started():
        deadline = time.monotonic() + 60
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        _thread.interrupt_main()

    # Python's own SIGINT handler, so interrupt_main() raises KeyboardInterrupt here: an
    # earlier test module (test_mutation_check) leaves mutation_check._on_signal installed
    # in this process, which turns SIGINT into SystemExit(130). Reported to the org.
    prev = signal.signal(signal.SIGINT, signal.default_int_handler)
    threading.Thread(target=interrupt_when_started, daemon=True).start()
    try:
        with pytest.raises(KeyboardInterrupt):
            sr._run_pytest(wt, tmp_path / "j.xml", tmp_path / "log", timeout=120)
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 10
        while sr._alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not sr._alive(pid), "pytest kept running after the interrupt"
    finally:
        signal.signal(signal.SIGINT, prev)
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 9)
            except (ProcessLookupError, ValueError):
                pass


# ---------------------------------------------------------------- mutation survivors, 11-27
# Three changed-lines mutants of dc936278 survived (output/analysis/
# 112626-S5-mutation-suite-dc936278-changed-lines.json); each test below kills one.

def test_an_interrupt_kills_pytests_process_group_before_it_propagates(sr, tmp_path,
                                                                        monkeypatch):
    """suite_record.py:271. The existing interrupt test cannot see this line: with the
    killpg dropped, proc.wait() still returns once its 60 s sleep ends, and the pid is dead
    by then. Here the interrupt arrives on the first wait and killpg is recorded."""
    killed = []

    class FakeProc:
        pid = 424242

        def __init__(self, *a, **k):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0

    monkeypatch.setattr(sr.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(sr.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(KeyboardInterrupt):
        sr._run_pytest(tmp_path, tmp_path / "j.xml", tmp_path / "log", timeout=5)
    assert killed == [(424242, signal.SIGKILL)]


def test_note_pytest_pid_leaves_another_runs_holder_record_alone(sr, tmp_path):
    """suite_record.py:330. Only the run named in the holder record may add pytest's pid;
    a record naming another process stays exactly as it was (control: our own record
    does get the pid)."""
    ledger = tmp_path / LEDGER
    hp = sr.holder_path(ledger)
    hp.parent.mkdir(parents=True, exist_ok=True)
    other = {"pid": os.getpid() + 1, "branch": "someone-else", "pytest_pid": None}
    hp.write_text(json.dumps(other), encoding="utf-8")
    sr._note_pytest_pid(ledger, 777)
    assert sr.read_holder(ledger) == other

    own = {"pid": os.getpid(), "branch": "mine", "pytest_pid": None}
    hp.write_text(json.dumps(own), encoding="utf-8")
    sr._note_pytest_pid(ledger, 777)
    assert sr.read_holder(ledger) == {**own, "pytest_pid": 777}


def test_no_git_env_puts_the_git_variables_back(sr, monkeypatch):
    """suite_record.py:477. GIT_* is dropped only while the gate runs, then restored."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_INDEX_FILE", "/elsewhere/index")
    with sr._no_git_env():
        assert not [k for k in os.environ if k.startswith("GIT_")]
    assert os.environ["GIT_DIR"] == "/elsewhere/.git"
    assert os.environ["GIT_INDEX_FILE"] == "/elsewhere/index"
