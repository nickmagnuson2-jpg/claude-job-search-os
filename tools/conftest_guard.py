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


def _implied_source(backup: Path) -> Path:
    """The file a backup claims to be protecting.

    mutation_check writes `<target>.mutation_backup` beside a real `<target>`, so stripping
    the suffix recovers the target it belongs to.
    """
    return backup.with_name(backup.name[: -len(".mutation_backup")])


def all_backups(root: Path) -> list[Path]:
    return sorted(Path(root).glob("**/*.mutation_backup"))


def stranded_backups(root: Path) -> list[Path]:
    """Backups that mean a mutation run really does own the tree.

    A backup counts ONLY if its implied source exists. That is the whole narrowing, and
    it is narrow on purpose -- see orphan_backups for why, and note that a live backup
    sitting next to an orphan must still be returned here, or the fix would invert into a
    silent pass while the tree is genuinely mutated.
    """
    return [b for b in all_backups(root) if _implied_source(b).exists()]


def orphan_backups(root: Path) -> list[Path]:
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
