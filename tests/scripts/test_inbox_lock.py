"""Tests for tools/inbox_lock.py -- advisory locking + guarded read-modify-write.

Design notes:
  * The cross-process exclusivity tests use real `subprocess` children, not two
    handles in one process. Two handles in one process would only exercise the
    in-process re-entrancy registry and would prove nothing about `flock`.
  * They are deterministic rather than sleep-raced: the parent holds the lock
    for the child's entire lifetime (child must time out), or has fully
    released it before the child starts (child must succeed). No test depends
    on two processes interleaving at the right moment.
  * `JOBSEARCH_LOCK_DIR` is redirected into `tmp_path` so runs never touch the
    real `~/.cache` and parallel test runs cannot collide.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from inbox_lock import (  # noqa: E402
    ConcurrentModification,
    LockTimeout,
    atomic_update,
    file_lock,
    lock_path_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Redirect the sidecar lock directory into tmp_path."""
    d = tmp_path / "locks"
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(d))
    return d


@pytest.fixture
def inbox(tmp_path):
    """A stand-in for data/inbox.md with the repo's header shape."""
    p = tmp_path / "vault" / "data" / "inbox.md"
    p.parent.mkdir(parents=True)
    p.write_text("# Inbox\n\n- existing item\n", encoding="utf-8")
    return p


def _child(code: str, lock_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `code` in a separate OS process sharing the same lock directory."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(TOOLS_DIR)!r})
        from inbox_lock import file_lock, atomic_update, LockTimeout, ConcurrentModification
        """
    ) + textwrap.dedent(code)
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "JOBSEARCH_LOCK_DIR": str(lock_dir),
        },
        timeout=60,
    )


TRY_LOCK = """
    target, timeout = sys.argv[1], float(sys.argv[2])
    try:
        with file_lock(target, timeout=timeout):
            print("ACQUIRED")
    except LockTimeout:
        print("TIMEOUT")
"""


# ---------------------------------------------------------------------------
# file_lock -- basics
# ---------------------------------------------------------------------------

def test_acquire_and_release_creates_sidecar_outside_the_vault(inbox, lock_dir):
    with file_lock(inbox) as lock_file:
        assert lock_file.exists()
        assert lock_file == lock_path_for(inbox)
        # The sidecar must NOT land next to the markdown file in the vault.
        assert lock_dir in lock_file.parents
        assert inbox.parent not in lock_file.parents
    # Vault directory still contains only the markdown file.
    assert [p.name for p in inbox.parent.iterdir()] == ["inbox.md"]


def test_lock_path_is_deterministic_across_equivalent_paths(inbox, lock_dir):
    relative = os.path.relpath(inbox, os.getcwd())
    assert lock_path_for(inbox) == lock_path_for(relative)
    assert lock_path_for(inbox) == lock_path_for(str(inbox) + "")


def test_lock_path_differs_for_same_name_in_different_vaults(tmp_path, lock_dir):
    a = tmp_path / "vault_a" / "inbox.md"
    b = tmp_path / "vault_b" / "inbox.md"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.touch()
    b.touch()
    assert lock_path_for(a) != lock_path_for(b)


def test_lock_works_when_target_does_not_exist(tmp_path, lock_dir):
    missing = tmp_path / "vault" / "data" / "not-yet.md"
    missing.parent.mkdir(parents=True)
    with file_lock(missing):
        pass
    assert not missing.exists()


def test_negative_timeout_rejected(inbox, lock_dir):
    with pytest.raises(ValueError):
        with file_lock(inbox, timeout=-1):
            pass


# ---------------------------------------------------------------------------
# file_lock -- genuine cross-process exclusivity
# ---------------------------------------------------------------------------

def test_lock_is_exclusive_across_two_os_processes(inbox, lock_dir):
    """Parent holds the lock for the child's whole lifetime -> child must time out."""
    with file_lock(inbox):
        result = _child(TRY_LOCK, lock_dir, str(inbox), "0.5")
    assert result.stdout.strip() == "TIMEOUT", result.stderr


def test_other_process_acquires_after_release(inbox, lock_dir):
    """Same child, run after the parent has fully released -> must succeed."""
    with file_lock(inbox):
        pass
    result = _child(TRY_LOCK, lock_dir, str(inbox), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


def test_lock_on_a_different_file_does_not_block(inbox, tmp_path, lock_dir):
    other = tmp_path / "vault" / "data" / "other.md"
    other.write_text("x", encoding="utf-8")
    with file_lock(inbox):
        result = _child(TRY_LOCK, lock_dir, str(other), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


def test_lock_released_when_holding_process_dies(inbox, lock_dir):
    """flock is released by the kernel on exit -- so there are no stale locks."""
    crash = """
        from inbox_lock import file_lock
        import os
        cm = file_lock(sys.argv[1])
        cm.__enter__()
        os._exit(1)   # die hard, holding the lock, no cleanup runs
    """
    dead = _child(crash, lock_dir, str(inbox))
    assert dead.returncode == 1
    result = _child(TRY_LOCK, lock_dir, str(inbox), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


# ---------------------------------------------------------------------------
# file_lock -- timeout and exception safety
# ---------------------------------------------------------------------------

def test_timeout_raises_lock_timeout(inbox, lock_dir):
    """Child holds the lock; the parent's short-timeout attempt must raise."""
    holder_ready = inbox.parent / "ready.flag"
    holder_release = inbox.parent / "release.flag"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time, pathlib
                sys.path.insert(0, {str(TOOLS_DIR)!r})
                from inbox_lock import file_lock
                with file_lock({str(inbox)!r}):
                    pathlib.Path({str(holder_ready)!r}).write_text("1")
                    for _ in range(600):
                        if pathlib.Path({str(holder_release)!r}).exists():
                            break
                        time.sleep(0.05)
                """
            ),
        ],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "JOBSEARCH_LOCK_DIR": str(lock_dir)},
    )
    try:
        # Deterministic handshake: do not attempt until the child truly holds it.
        for _ in range(600):
            if holder_ready.exists():
                break
            import time as _t
            _t.sleep(0.01)
        assert holder_ready.exists(), "holder process never acquired the lock"

        with pytest.raises(LockTimeout) as exc:
            with file_lock(inbox, timeout=0.3):
                pytest.fail("acquired a lock held by another process")
        assert str(inbox) in str(exc.value)
    finally:
        holder_release.write_text("1")
        holder.wait(timeout=30)


def test_zero_timeout_is_a_single_nonblocking_attempt(inbox, lock_dir):
    result = _child(TRY_LOCK, lock_dir, str(inbox), "0")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


def test_lock_released_when_body_raises(inbox, lock_dir):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with file_lock(inbox):
            raise Boom()

    # Prove release from a *separate process* -- an in-process re-acquire would
    # pass via the re-entrancy registry even if the kernel lock leaked.
    result = _child(TRY_LOCK, lock_dir, str(inbox), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


def test_lock_released_when_atomic_update_transform_raises(inbox, lock_dir):
    def boom(_text):
        raise ValueError("transform blew up")

    with pytest.raises(ValueError):
        atomic_update(inbox, boom)

    result = _child(TRY_LOCK, lock_dir, str(inbox), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr
    assert inbox.read_text(encoding="utf-8") == "# Inbox\n\n- existing item\n"


# ---------------------------------------------------------------------------
# file_lock -- re-entrancy
# ---------------------------------------------------------------------------

def test_reentrant_within_one_thread_does_not_deadlock(inbox, lock_dir):
    with file_lock(inbox, timeout=2):
        with file_lock(inbox, timeout=2):
            with file_lock(inbox, timeout=2):
                pass
    result = _child(TRY_LOCK, lock_dir, str(inbox), "5")
    assert result.stdout.strip() == "ACQUIRED", result.stderr


def test_nested_atomic_update_inside_file_lock(inbox, lock_dir):
    with file_lock(inbox):
        atomic_update(inbox, lambda t: t + "- nested\n")
    assert "- nested" in inbox.read_text(encoding="utf-8")


def test_inner_exit_does_not_release_the_kernel_lock(inbox, lock_dir):
    with file_lock(inbox):
        with file_lock(inbox):
            pass
        # Outer scope still holds it: another process must still be excluded.
        result = _child(TRY_LOCK, lock_dir, str(inbox), "0.3")
    assert result.stdout.strip() == "TIMEOUT", result.stderr


def test_second_thread_is_excluded_while_first_holds(inbox, lock_dir):
    import threading

    holding = threading.Event()
    may_release = threading.Event()
    outcome = []

    def holder():
        with file_lock(inbox, timeout=5):
            holding.set()
            may_release.wait(30)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert holding.wait(10)
        try:
            with file_lock(inbox, timeout=0.3):
                outcome.append("ACQUIRED")
        except LockTimeout:
            outcome.append("TIMEOUT")
    finally:
        may_release.set()
        t.join(timeout=30)
    assert outcome == ["TIMEOUT"]


# ---------------------------------------------------------------------------
# atomic_update -- happy path
# ---------------------------------------------------------------------------

def test_atomic_update_applies_transform(inbox, lock_dir):
    result = atomic_update(inbox, lambda t: t.replace("# Inbox\n", "# Inbox\n\n- new item\n"))
    assert result == {
        "status": "ok",
        "attempts": 1,
        "path": str(inbox),
        "created": False,
        "changed": True,
    }
    assert inbox.read_text(encoding="utf-8") == "# Inbox\n\n- new item\n\n- existing item\n"


def test_atomic_update_prepend_shape_matches_the_cron_use_case(inbox, lock_dir):
    """The granola_auto_debrief pattern: splice entries under the header."""
    def prepend(text: str) -> str:
        lines = text.split("\n")
        i = next(n for n, line in enumerate(lines) if line.startswith("# Inbox")) + 1
        return "\n".join(lines[:i] + ["", "- debrief entry"] + lines[i:])

    atomic_update(inbox, prepend)
    body = inbox.read_text(encoding="utf-8")
    assert body.index("- debrief entry") < body.index("- existing item")


def test_atomic_update_creates_missing_file(tmp_path, lock_dir):
    missing = tmp_path / "vault" / "data" / "inbox.md"
    missing.parent.mkdir(parents=True)
    result = atomic_update(missing, lambda t: t + "# Inbox\n")
    assert result["created"] is True
    assert result["changed"] is True
    assert missing.read_text(encoding="utf-8") == "# Inbox\n"


def test_atomic_update_noop_transform_does_not_touch_the_file(inbox, lock_dir):
    before = inbox.stat().st_mtime_ns
    result = atomic_update(inbox, lambda t: t)
    assert result["changed"] is False
    assert inbox.stat().st_mtime_ns == before


def test_atomic_update_leaves_no_temp_files(inbox, lock_dir):
    atomic_update(inbox, lambda t: t + "- x\n")
    assert [p.name for p in inbox.parent.iterdir()] == ["inbox.md"]


def test_atomic_update_preserves_unicode(inbox, lock_dir):
    atomic_update(inbox, lambda t: t + "- café ✓ \U0001f9ed\n")
    assert "café ✓ \U0001f9ed" in inbox.read_text(encoding="utf-8")


def test_atomic_update_rejects_non_string_transform_result(inbox, lock_dir):
    with pytest.raises(ValueError):
        atomic_update(inbox, lambda t: None)
    assert inbox.read_text(encoding="utf-8") == "# Inbox\n\n- existing item\n"


def test_atomic_update_rejects_negative_retries(inbox, lock_dir):
    with pytest.raises(ValueError):
        atomic_update(inbox, lambda t: t + "x", retries=-1)


def test_atomic_update_propagates_lock_timeout(inbox, lock_dir):
    """A lock held by another process must surface as LockTimeout, not silent loss."""
    ready = inbox.parent / "ready.flag"
    release = inbox.parent / "release.flag"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time, pathlib
                sys.path.insert(0, {str(TOOLS_DIR)!r})
                from inbox_lock import file_lock
                with file_lock({str(inbox)!r}):
                    pathlib.Path({str(ready)!r}).write_text("1")
                    for _ in range(600):
                        if pathlib.Path({str(release)!r}).exists():
                            break
                        time.sleep(0.05)
                """
            ),
        ],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "JOBSEARCH_LOCK_DIR": str(lock_dir)},
    )
    try:
        import time as _t
        for _ in range(600):
            if ready.exists():
                break
            _t.sleep(0.01)
        assert ready.exists()
        with pytest.raises(LockTimeout):
            atomic_update(inbox, lambda t: t + "- should not land\n", timeout=0.3)
        assert "should not land" not in inbox.read_text(encoding="utf-8")
    finally:
        release.write_text("1")
        holder.wait(timeout=30)


# ---------------------------------------------------------------------------
# atomic_update -- mtime recheck / retry against lock-unaware writers
# ---------------------------------------------------------------------------

def test_atomic_update_retries_then_succeeds_when_file_changes_mid_flight(inbox, lock_dir):
    """Simulate an editor that writes without taking the lock, once.

    Deterministic: the interference happens *inside* the transform, so it is
    always inside the read->write window, with no timing dependency.
    """
    calls = []

    def transform(text: str) -> str:
        calls.append(text)
        if len(calls) == 1:
            # Lock-unaware writer clobbers the file during our transform.
            inbox.write_text("# Inbox\n\n- edited by hand\n", encoding="utf-8")
        return text.replace("# Inbox\n", "# Inbox\n\n- cron entry\n")

    result = atomic_update(inbox, transform)
    assert result["status"] == "ok"
    assert result["attempts"] == 2
    assert len(calls) == 2

    body = inbox.read_text(encoding="utf-8")
    # The retry re-read the hand edit, so nothing was lost and nothing resurrected.
    assert "- edited by hand" in body
    assert "- cron entry" in body
    assert "- existing item" not in body


def test_atomic_update_uses_all_allowed_attempts(inbox, lock_dir):
    calls = []

    def transform(text: str) -> str:
        calls.append(text)
        if len(calls) <= 3:
            inbox.write_text(f"# Inbox\n\n- external {len(calls)}\n", encoding="utf-8")
        return text + "- cron\n"

    result = atomic_update(inbox, transform, retries=3)
    assert result["attempts"] == 4
    body = inbox.read_text(encoding="utf-8")
    assert "- external 3" in body
    assert "- cron" in body


def test_atomic_update_raises_after_exhausting_retries(inbox, lock_dir):
    calls = []

    def transform(text: str) -> str:
        calls.append(text)
        # Never stops interfering.
        inbox.write_text(f"# Inbox\n\n- external {len(calls)}\n", encoding="utf-8")
        return text + "- cron entry\n"

    with pytest.raises(ConcurrentModification) as exc:
        atomic_update(inbox, transform, retries=3)

    assert len(calls) == 4  # first attempt + 3 retries
    assert "nothing was written" in str(exc.value)
    # The cron's content never landed; the external writer's last write survives.
    body = inbox.read_text(encoding="utf-8")
    assert "- cron entry" not in body
    assert "- external 4" in body


def test_retries_zero_means_one_attempt(inbox, lock_dir):
    calls = []

    def transform(text: str) -> str:
        calls.append(text)
        inbox.write_text("# Inbox\n\n- external\n", encoding="utf-8")
        return text + "- cron\n"

    with pytest.raises(ConcurrentModification):
        atomic_update(inbox, transform, retries=0)
    assert len(calls) == 1


def test_size_identical_but_content_changed_is_still_detected(inbox, lock_dir):
    """mtime_ns catches a same-size overwrite that a size-only check would miss."""
    calls = []
    original = inbox.read_text(encoding="utf-8")
    same_size = "# Inbox\n\n- EXISTING ITEM\n"
    assert len(same_size) == len(original)

    def transform(text: str) -> str:
        calls.append(text)
        if len(calls) == 1:
            inbox.write_text(same_size, encoding="utf-8")
        return text + "- cron\n"

    result = atomic_update(inbox, transform)
    assert result["attempts"] == 2
    assert "- EXISTING ITEM" in inbox.read_text(encoding="utf-8")


def test_deleted_mid_flight_is_detected_and_retried(inbox, lock_dir):
    calls = []

    def transform(text: str) -> str:
        calls.append(text)
        if len(calls) == 1:
            inbox.unlink()
        return text + "- cron\n"

    result = atomic_update(inbox, transform)
    assert result["attempts"] == 2
    assert result["created"] is True
    assert inbox.read_text(encoding="utf-8") == "- cron\n"


# ---------------------------------------------------------------------------
# Two real processes contending through atomic_update
# ---------------------------------------------------------------------------

def test_two_processes_serialize_and_neither_write_is_lost(inbox, lock_dir):
    """Both children append under the lock; both lines must survive.

    Without the lock this is exactly the read-modify-write race that loses one
    of the two appends.
    """
    appender = """
        target, marker = sys.argv[1], sys.argv[2]
        import time
        def transform(text):
            time.sleep(0.2)   # widen the read->write window on purpose
            return text + "- " + marker + "\\n"
        atomic_update(target, transform, timeout=30)
        print("DONE")
    """
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(TOOLS_DIR)!r})
        from inbox_lock import atomic_update
        """
    ) + textwrap.dedent(appender)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "JOBSEARCH_LOCK_DIR": str(lock_dir)}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(inbox), f"writer{i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        for i in range(2)
    ]
    outs = [p.communicate(timeout=60) for p in procs]
    for (out, err), p in zip(outs, procs):
        assert p.returncode == 0, err
        assert out.strip() == "DONE"

    body = inbox.read_text(encoding="utf-8")
    assert "- writer0" in body
    assert "- writer1" in body
    assert "- existing item" in body
