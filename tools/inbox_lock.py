#!/usr/bin/env python3
"""Advisory file locking + guarded read-modify-write for concurrently-edited markdown.

WHY THIS EXISTS
---------------
`data/inbox.md` is written by a launchd cron job every few hours
(`granola_auto_debrief.prepend_inbox_entries`) using a read-modify-write:

    text = path.read_text()          # (1) read
    new  = splice_entries(text)      # (2) modify
    tmp.write_text(new); os.replace(tmp, path)   # (3) write

Step (3) is atomic against a *crash* -- a reader never sees a half-written file.
It is NOT atomic against *concurrency*. If an interactive session routes items
out of the inbox between (1) and (3), the cron's `os.replace` drops a stale
snapshot over the top: the session's edits vanish, and entries the session just
routed away resurrect as ghosts.

This module provides the two pieces that close most of that window.

WHAT IS AND IS NOT GUARANTEED
-----------------------------
`file_lock` is an **advisory** lock (`fcntl.flock` on a sidecar file). It only
excludes other processes that call `file_lock` on the same target. It does
nothing about a writer that never takes the lock -- Obsidian, `vim`, a `Write`
tool call, `cat > inbox.md`, Obsidian Sync, or Dropbox will all happily clobber
a locked file.

`atomic_update` therefore adds a second, weaker guard: it records
`(st_mtime_ns, st_size)` at read time and re-stats immediately before
`os.replace`. If the file moved underneath us, the transform is discarded and
retried. This **narrows** the race with lock-unaware writers; it does not
eliminate it. There is still an unavoidable window of a few microseconds
between the final stat and the rename, and an external writer that happens to
produce the identical size at the identical nanosecond is undetectable.

Honest summary: lock-aware writers are serialized correctly; lock-unaware
writers are *usually* detected and retried against, never prevented. Do not
describe this module as making `inbox.md` safe against arbitrary editors.

WHERE THE LOCK FILE LIVES
-------------------------
Not next to the target. `data/` is an Obsidian vault directory that is also
synced and backed up; dropping `inbox.md.lock` in there means a stray
zero-byte file in the vault tree, in Obsidian's file watcher, in the sync set,
and in the nightly backup. Instead the sidecar lives in a single cache
directory outside the vault:

    ~/.cache/jobsearch-locks/<sha256(realpath(target))[:16]>-<basename>.lock

The hash of the resolved absolute path makes the mapping deterministic (any
process locking the same file picks the same sidecar) and collision-safe across
same-named files in different vaults (`data/inbox.md` exists in more than one
vault here). The basename suffix is purely for human legibility when debugging.
Override the directory with the `JOBSEARCH_LOCK_DIR` environment variable.

These sidecars are never deleted -- deleting a lock file is itself a race (a
second process can be mid-`open` on the inode you just unlinked). They are
empty, bounded in number by the number of distinct files ever locked, and live
in a cache dir, so they cost nothing. `flock` is released by the kernel when
the holding process exits or crashes, so there is no such thing as a stale lock
here and no PID-file cleanup logic is needed.

RE-ENTRANCY
-----------
`flock` is per open-file-description: a process that opens the same lock file
twice and flocks both fds deadlocks against itself. `file_lock` avoids that
with a process-wide registry -- the same *thread* may nest `file_lock` calls on
the same path freely (only the outermost takes/releases the kernel lock), and
other *threads* in the same process serialize on a `threading.RLock` before
ever reaching the kernel lock. So: re-entrant within a thread, mutually
exclusive across threads, mutually exclusive across processes.

USAGE
-----
Wrapping an existing read-modify-write:

    from inbox_lock import atomic_update

    def splice(text: str) -> str:
        ...                      # pure: text in, text out, no file I/O
        return new_text

    result = atomic_update(inbox_path, splice)   # {"status": "ok", ...}

Or, when the critical section is not a single whole-file rewrite:

    from inbox_lock import file_lock

    with file_lock(inbox_path):
        do_whatever(inbox_path)

Requires POSIX `fcntl` (macOS/Linux). Standard library only. Python 3.10+.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

__all__ = [
    "LockTimeout",
    "ConcurrentModification",
    "lock_path_for",
    "file_lock",
    "atomic_update",
]

DEFAULT_TIMEOUT = 30.0
_POLL_INTERVAL = 0.01  # seconds between non-blocking flock attempts


class LockTimeout(TimeoutError):
    """Raised when the exclusive lock could not be acquired within `timeout`."""


class ConcurrentModification(RuntimeError):
    """Raised when the target file kept changing under us across every retry.

    Signals a writer that does not participate in this lock protocol (an
    editor, a sync client, a hand-rolled script). The caller's transform was
    never applied; nothing was written.
    """


# ---------------------------------------------------------------------------
# Lock-file location
# ---------------------------------------------------------------------------

def _lock_dir() -> Path:
    override = os.environ.get("JOBSEARCH_LOCK_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".cache" / "jobsearch-locks"


def lock_path_for(path: str | os.PathLike) -> Path:
    """Return the sidecar lock-file path used to guard `path`.

    Deterministic for a given resolved target path, so independent processes
    agree. Exposed so callers/tests can inspect it; you do not need to create
    or clean it up yourself.
    """
    target = Path(path)
    # resolve() so `data/inbox.md` and `/abs/.../data/inbox.md` and a path via
    # a symlinked vault all hash to the same sidecar. strict=False: the target
    # need not exist yet.
    resolved = str(target.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in target.name)
    return _lock_dir() / f"{digest}-{safe_name}.lock"


# ---------------------------------------------------------------------------
# Re-entrancy registry (process-wide)
# ---------------------------------------------------------------------------

_registry_guard = threading.Lock()
# resolved-lock-path -> {"rlock": RLock, "fd": int | None, "depth": int}
_registry: dict[str, dict] = {}


def _entry_for(lock_file: Path) -> dict:
    key = str(lock_file)
    with _registry_guard:
        entry = _registry.get(key)
        if entry is None:
            entry = {"rlock": threading.RLock(), "fd": None, "depth": 0}
            _registry[key] = entry
        return entry


# ---------------------------------------------------------------------------
# file_lock
# ---------------------------------------------------------------------------

@contextmanager
def file_lock(
    path: str | os.PathLike,
    timeout: float = DEFAULT_TIMEOUT,
) -> Iterator[Path]:
    """Hold an exclusive advisory lock on `path` for the duration of the block.

    Args:
        path: The file being protected (NOT the lock file -- the sidecar path
            is derived from it, see `lock_path_for`). The file itself need not
            exist.
        timeout: Seconds to keep polling for the lock. `0` means a single
            non-blocking attempt. Must be >= 0.

    Yields:
        The sidecar lock-file `Path`, for logging/debugging.

    Raises:
        LockTimeout: the lock was still held elsewhere when `timeout` elapsed.

    The lock is released on normal exit AND on exception. Re-entrant within a
    single thread; see the module docstring.
    """
    if timeout < 0:
        raise ValueError("timeout must be >= 0")

    lock_file = lock_path_for(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    entry = _entry_for(lock_file)
    deadline = time.monotonic() + timeout

    # Stage 1: serialize threads inside this process. Doing this before the
    # kernel lock is what makes nesting on one thread safe -- an RLock is
    # re-entrant per thread, flock is not re-entrant per process.
    remaining = max(0.0, deadline - time.monotonic())
    if not entry["rlock"].acquire(timeout=remaining if timeout > 0 else 0):
        raise LockTimeout(
            f"could not acquire in-process lock for {path} within {timeout}s "
            f"(another thread holds it)"
        )

    took_kernel_lock = False
    fd = None
    try:
        # Stage 2: the outermost holder on this thread takes the kernel lock.
        if entry["depth"] == 0:
            fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                            raise
                        if time.monotonic() >= deadline:
                            raise LockTimeout(
                                f"could not acquire lock on {path} within {timeout}s "
                                f"(held by another process; lock file: {lock_file})"
                            ) from None
                        time.sleep(min(_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
            except BaseException:
                os.close(fd)
                raise
            entry["fd"] = fd
            took_kernel_lock = True

        entry["depth"] += 1
        try:
            yield lock_file
        finally:
            entry["depth"] -= 1
            if took_kernel_lock:
                held_fd = entry["fd"]
                entry["fd"] = None
                try:
                    fcntl.flock(held_fd, fcntl.LOCK_UN)
                finally:
                    os.close(held_fd)
    finally:
        entry["rlock"].release()


# ---------------------------------------------------------------------------
# atomic_update
# ---------------------------------------------------------------------------

def _stamp(path: Path) -> tuple[int, int] | None:
    """Return (st_mtime_ns, st_size), or None if the file does not exist."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` via temp file + os.replace in the same directory.

    Same-directory temp is required: `os.replace` is only atomic within a
    filesystem, and a cross-device rename raises.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    mode = None
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass

    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode if mode is not None else 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_update(
    path: str | os.PathLike,
    transform: Callable[[str], str],
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
) -> dict:
    """Lock-guarded, change-detecting read-modify-write of a text file.

    Under the exclusive lock, per attempt:
      1. read the file's text (`""` if it does not exist) and stamp it
         with (st_mtime_ns, st_size);
      2. call `transform(text) -> new_text`;
      3. re-stat immediately before committing. If the stamp changed, someone
         who does not honour the lock wrote to the file during the transform:
         discard the result and start over;
      4. otherwise write via temp file + `os.replace` in the same directory.

    Args:
        path: File to update. May not exist; it will be created.
        transform: Pure `str -> str`. It MUST NOT write to `path` itself (that
            is indistinguishable from an external writer and will burn every
            retry), and it may be called more than once, so it must not have
            side effects you cannot repeat.
        timeout: Seconds to wait for the lock. See `file_lock`.
        retries: Maximum number of *additional* attempts after the first, so
            total attempts are at most `retries + 1`. `retries=0` means try
            once and raise on the first detected collision.

    Returns:
        {"status": "ok", "attempts": <int>, "path": <str>,
         "created": <bool>, "changed": <bool>}
        `created` is True if the file did not exist at read time; `changed` is
        True if the transform actually produced different text.

    Raises:
        LockTimeout: could not acquire the lock.
        ConcurrentModification: every attempt saw the file change mid-flight;
            nothing was written.
        ValueError: `transform` returned a non-str, or `retries` < 0.

    Note: the write is skipped when the transform is a no-op, so a no-op update
    does not touch mtime and does not perturb Obsidian's file watcher.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")

    target = Path(path)
    max_attempts = retries + 1
    last_seen: tuple | None = None

    with file_lock(target, timeout=timeout):
        for attempt in range(1, max_attempts + 1):
            before = _stamp(target)
            try:
                text = target.read_text(encoding="utf-8")
            except FileNotFoundError:
                text = ""

            # A file that appeared/vanished between stat and read is itself a
            # collision; fall through to the recheck, which will catch it.
            new_text = transform(text)
            if not isinstance(new_text, str):
                raise ValueError(
                    f"transform must return str, got {type(new_text).__name__}"
                )

            after = _stamp(target)
            if after != before:
                last_seen = (before, after)
                continue  # someone wrote underneath us; redo read+transform

            if new_text == text and before is not None:
                return {
                    "status": "ok",
                    "attempts": attempt,
                    "path": str(target),
                    "created": False,
                    "changed": False,
                }

            _write_atomic(target, new_text)
            return {
                "status": "ok",
                "attempts": attempt,
                "path": str(target),
                "created": before is None,
                "changed": new_text != text,
            }

    raise ConcurrentModification(
        f"{target} changed underneath every one of {max_attempts} attempt(s); "
        f"last observed stamp transition {last_seen}. A writer that does not "
        f"take the lock is active -- nothing was written."
    )


# ---------------------------------------------------------------------------
# The canonical inbox prepend.
#
# Four writers independently reimplemented "find the header, splice after it,
# rewrite the file": granola_auto_debrief, career_scanner, agent_collect, and
# remember_apply. Two of them did it non-atomically (a crash mid-write truncates
# the user's inbox), one appended at EOF instead of prepending (silently breaking
# the newest-first convention), and the two atomic ones shared a fixed temp-file
# name, so concurrent "atomic" writers could tear each other's temp file before
# either rename. This is the single source of truth for all of them.
# ---------------------------------------------------------------------------

DEFAULT_INBOX_HEADER = (
    "# Inbox\n\n"
    "<!-- Items captured via /remember. Review and route to appropriate files periodically. -->\n"
)


def splice_after_header(content: str, block: str) -> str:
    """Insert `block` directly after the '# Inbox' header and its comment preamble.

    Pure string transform, so it is safe to re-run under `atomic_update` retries.
    An empty or headerless file gets the default header first, which keeps the
    insertion point well-defined instead of silently prepending to nothing.
    """
    if not content.strip():
        content = DEFAULT_INBOX_HEADER
    lines = content.split("\n")
    insert_after = 0
    for i, line in enumerate(lines):
        if line.startswith("# Inbox"):
            insert_after = i + 1
            while insert_after < len(lines):
                nxt = lines[insert_after].strip()
                if nxt == "" or nxt.startswith("<!--") or nxt.endswith("-->"):
                    insert_after += 1
                else:
                    break
            break
    head = "\n".join(lines[:insert_after])
    tail = "\n".join(lines[insert_after:])
    return head.rstrip("\n") + "\n\n" + block.strip("\n") + "\n\n" + tail.lstrip("\n")


def prepend_entries(inbox_path, entries, timeout: float = 30.0) -> dict:
    """Prepend entries to an inbox, under the lock, atomically. THE way to write an inbox.

    `entries` may be a list of blocks or a single string. Returns the
    `atomic_update` result dict. Raises LockTimeout / ConcurrentModification —
    callers should catch and report rather than letting a cron run die.
    """
    if isinstance(entries, str):
        entries = [entries]
    block = "\n".join(e.strip("\n") for e in entries if e and e.strip())
    if not block:
        return {"status": "noop", "attempts": 0, "path": str(inbox_path)}
    return atomic_update(inbox_path, lambda c: splice_after_header(c, block), timeout=timeout)


def _main(argv=None) -> int:
    """CLI so SKILLS can prepend to an inbox under the lock.

    A Claude Write/Edit tool call cannot hold an flock across its read-think-write
    cycle, so any skill that says "re-read data/inbox.md then Write it" bypasses
    every guarantee in this module. Those skills call this instead: they hand over
    just the new block, and the read-splice-write happens here, inside the lock.

      python3 tools/inbox_lock.py prepend --inbox <path> --block "## Heading\n\nbody"
      echo "## Heading" | python3 tools/inbox_lock.py prepend --inbox <path> --stdin
    """
    import argparse
    ap = argparse.ArgumentParser(description="Prepend a block to an inbox, locked and atomically.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepend")
    p.add_argument("--inbox", required=True)
    p.add_argument("--block", default=None)
    p.add_argument("--stdin", action="store_true", help="read the block from stdin")
    p.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args(argv)

    block = sys.stdin.read() if a.stdin else (a.block or "")
    if not block.strip():
        print(json.dumps({"status": "error", "message": "empty block; nothing written"}))
        return 1
    inbox = Path(a.inbox).expanduser()
    if not inbox.parent.is_dir():
        print(json.dumps({"status": "error",
                          "message": f"inbox directory does not exist: {inbox.parent}"}))
        return 1
    try:
        res = prepend_entries(inbox, block, timeout=a.timeout)
    except (LockTimeout, ConcurrentModification) as exc:
        print(json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"status": res.get("status", "ok"), "inbox": str(inbox),
                      "attempts": res.get("attempts", 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
