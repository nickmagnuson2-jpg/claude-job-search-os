#!/usr/bin/env python3
"""Shared definitions for the mutation guard in tests/conftest.py.

ONE home for the two things `tests/conftest.py` (the producer of the refusal) and
`tools/mutation_check.py` (the consumer that reads its exit code) must agree on. They are
in different trees and neither naturally imports the other, so the value used to be
hardcoded twice. Drift there is silent and inverts a signal: refusals read as ordinary
failures, or ordinary failures as refusals.

Stdlib-only and side-effect-free on import, because `tests/conftest.py` imports it on
EVERY pytest invocation in the repo.
"""
import os
from pathlib import Path

# The exit code tests/conftest.py uses when it refuses to run because a mutation owns the
# tree, and that tools/mutation_check.py matches on to tell "the guard refused" apart from
# "the tests failed".
#
# NOT 3. pytest reserves 0-5 (0 pass, 1 failed, 2 interrupted, 3 INTERNALERROR, 4 usage,
# 5 no-tests-collected), and this was 3 until 2026-09-01 -- indistinguishable from pytest's
# own internal error. A test file that genuinely blew up got filed as a benign
# `isolation_unmeasured` instead of being surfaced as broken. Anything outside 0-5 works;
# stay under 126, where the shell's own signal encoding starts.
CONFTEST_REFUSAL = 86


SUFFIX = ".mutation_backup"

# Backups live OUTSIDE the working tree, deliberately.
#
# Origin 2026-09-01. `~/Documents` is inside iCloud Drive (Desktop & Documents sync is on),
# and mutation_check rewrites its target dozens of times per second. iCloud responded by
# creating conflict copies -- `todo_write.py 2.mutation_backup` -- inside `tools/`. One of
# those cost a 108-tool sweep its entire isolation signal before the orphan rule below
# existed. Writing backups to a cache directory removes the rapidly-rewritten file from the
# synced tree entirely, so there is nothing for iCloud to duplicate.
#
# `~/Library/Caches` rather than `~/.cache`: it is the macOS-native location and is not
# synced by iCloud, Dropbox, or Obsidian.
#
# MUTATION_BACKUP_DIR overrides it, so a test can isolate its own backups completely.
def backup_dir() -> Path:
    d = os.environ.get("MUTATION_BACKUP_DIR")
    if d:
        return Path(d).expanduser()
    return Path.home() / "Library" / "Caches" / "claude-mutation-backups"


def backup_path(target: Path) -> Path:
    """Where the backup for `target` lives.

    The absolute source path is encoded INTO the filename (slashes as %2F) rather than
    hashed, because the guard must be able to recover the source from the backup alone --
    that is what makes "does the source still exist?" answerable, and that question is the
    whole orphan rule.
    """
    enc = str(Path(target).resolve()).replace("%", "%25").replace("/", "%2F")
    return backup_dir() / (enc + SUFFIX)


def source_of(backup: Path) -> Path:
    """Inverse of backup_path: the file this backup claims to be protecting."""
    name = Path(backup).name
    if name.endswith(SUFFIX):
        name = name[: -len(SUFFIX)]
    return Path(name.replace("%2F", "/").replace("%25", "%"))


def all_backups(root: Path | None = None) -> list[Path]:
    """Every backup in the store, optionally only those whose source is under `root`.

    Scoping by decoded SOURCE, not by where the backup file sits, is what keeps one shared
    store from leaking between trees: a fixture repo in tmp_path and the real repo write to
    the same directory, and each must see only its own.
    """
    d = backup_dir()
    if not d.exists():
        return []
    found = sorted(d.glob("*" + SUFFIX))
    if root is None:
        return found
    r = str(Path(root).resolve())
    return [b for b in found if str(source_of(b)).startswith(r)]


def _implied_source(backup: Path) -> Path:
    return source_of(backup)


def stranded_backups(root: Path | None = None) -> list[Path]:
    """Backups that mean a mutation run really does own the tree.

    A backup counts ONLY if its implied source exists. That is the whole narrowing, and
    it is narrow on purpose -- see orphan_backups for why, and note that a live backup
    sitting next to an orphan must still be returned here, or the fix would invert into a
    silent pass while the tree is genuinely mutated.
    """
    return [b for b in all_backups(root) if _implied_source(b).exists()]


def orphan_backups(root: Path | None = None) -> list[Path]:
    """Backups whose implied source does NOT exist: junk, not an in-flight mutation.

    Origin 2026-08-31. A stray `tools/todo_write.py 2.mutation_backup` -- a macOS/sync
    style " 2" duplicate of a real backup, whose source `tools/todo_write.py 2` never
    existed -- sat in the tree. The guard globbed every `*.mutation_backup` and refused
    the isolation subprocess of all 108 tools in a sweep. Every one came back
    `isolation_unmeasured`: nothing failed, nothing was flagged, and the isolation signal
    was simply gone. One unowned file, silently, for a whole run.

    An orphan cannot be an active mutation, because mutation_check only ever writes a
    backup beside a file it is rewriting. So it must not halt anything -- but it is still
    junk that should be deleted, so it is reported rather than ignored.
    """
    return [b for b in all_backups(root) if not _implied_source(b).exists()]


def prune_orphans(root: Path | None = None) -> list[Path]:
    """Delete backups whose source no longer exists, returning what was removed.

    Necessary because the store is now OUTSIDE the trees it serves. Before 2026-09-01 a
    backup sat beside its target, so a tmp fixture tree took its backups with it when it
    was deleted; a shared cache dir keeps them forever instead. Measured the day of the
    move: 27 orphans accumulated from a single afternoon of test runs.

    Safe by construction, and that is the only reason this deletes anything: an orphan's
    source is gone, so the backup cannot be restored to anywhere. A backup whose source
    still exists is never touched, so a concurrent run's live backup is never at risk.
    """
    removed = []
    for b in all_backups(root):
        if not source_of(b).exists():
            try:
                b.unlink()
                removed.append(b)
            except OSError:
                pass
    return removed
