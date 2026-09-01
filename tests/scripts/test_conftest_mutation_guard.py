#!/usr/bin/env python3
"""Tests for tests/conftest.py's mutation guard.

The guard had no tests, and on 2026-08-31 both of its weaknesses fired in one run:

  1. ONE ORPHAN FILE SILENTLY VOIDED EVERY ISOLATION RESULT. A stray
     `tools/todo_write.py 2.mutation_backup` -- a macOS/sync-style " 2" duplicate whose
     source `tools/todo_write.py 2` never existed -- sat in the tree. `_stranded_backups`
     globbed `**/*.mutation_backup` and matched it, so the guard refused every isolation
     subprocess in a 108-tool sweep. All 108 came back `isolation_unmeasured`. Nothing
     was broken, nothing was flagged, and the isolation signal was simply gone.

  2. THE REFUSAL EXIT CODE WAS AMBIGUOUS. The guard exited 3, which is also pytest's
     INTERNALERROR. `mutation_check` reads exit 3 as "the guard refused", so a genuine
     internal error in a test file was indistinguishable from a refusal -- one gets
     recorded as `isolation_unmeasured` (benign) when it is really a broken test file.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))


class TestOrphanBackupsDoNotHaltTheSuite:
    """An orphan is a backup whose SOURCE does not exist. It cannot be an in-flight
    mutation, because mutation_check only ever writes `<target>.mutation_backup` beside a
    real `<target>`. Halting on one is a false accusation that costs the whole signal."""

    def _mk(self, root, name, with_source):
        root.mkdir(parents=True, exist_ok=True)
        bak = root / f"{name}.mutation_backup"
        bak.write_text("x = 1\n", encoding="utf-8")
        if with_source:
            (root / name).write_text("x = 1\n", encoding="utf-8")
        return bak

    def test_a_backup_whose_source_exists_is_stranded(self, tmp_path):
        from conftest_guard import stranded_backups
        self._mk(tmp_path / "tools", "real.py", with_source=True)
        assert [p.name for p in stranded_backups(tmp_path)] == ["real.py.mutation_backup"]

    def test_a_backup_whose_source_is_MISSING_is_not_stranded(self, tmp_path):
        """The exact 2026-08-31 file: `todo_write.py 2.mutation_backup` with no
        `todo_write.py 2` beside it."""
        from conftest_guard import stranded_backups
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        assert stranded_backups(tmp_path) == []

    def test_a_live_backup_is_still_caught_alongside_an_orphan(self, tmp_path):
        """The orphan must not mask a real one -- that would invert the bug into a
        silent pass while the tree really is mid-mutation."""
        from conftest_guard import stranded_backups
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        self._mk(tmp_path / "tools", "real.py", with_source=True)
        assert [p.name for p in stranded_backups(tmp_path)] == ["real.py.mutation_backup"]

    def test_orphans_are_reported_separately_not_swallowed(self, tmp_path):
        """Not refusing is not the same as ignoring: an orphan is still junk that voids
        nothing but should be cleaned, so it must remain visible."""
        from conftest_guard import orphan_backups
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        assert [p.name for p in orphan_backups(tmp_path)] == \
            ["todo_write.py 2.mutation_backup"]


class TestRefusalExitCodeIsUnambiguous:
    """pytest reserves 0-5 (0 pass, 1 failed, 2 interrupted, 3 INTERNALERROR, 4 usage,
    5 no-tests-collected). The refusal code must sit outside that range or it cannot be
    told apart from pytest's own outcomes."""

    def test_refusal_code_is_outside_pytests_reserved_range(self):
        from conftest_guard import CONFTEST_REFUSAL
        assert CONFTEST_REFUSAL not in range(0, 6), (
            "refusal code collides with a reserved pytest exit code; a real failure "
            "would be misread as a refusal"
        )

    def test_mutation_check_reads_the_same_constant_not_a_copy(self):
        """Single source of truth. Both the producer (tests/conftest.py) and the consumer
        (tools/mutation_check.py) must IMPORT this value from tools/conftest_guard.py.
        A re-hardcoded literal in either place would drift silently: refusals would read
        as ordinary failures, or ordinary failures as refusals."""
        import mutation_check
        from conftest_guard import CONFTEST_REFUSAL
        assert mutation_check.CONFTEST_REFUSAL == CONFTEST_REFUSAL
        for f in ("tools/mutation_check.py", "tests/conftest.py"):
            text = (REPO_ROOT / f).read_text(encoding="utf-8")
            assert "conftest_guard" in text, f"{f} does not import the shared constant"
            assert f"CONFTEST_REFUSAL = {CONFTEST_REFUSAL}" not in text, \
                f"{f} re-hardcodes the refusal code instead of importing it"


class TestGuardStillRefusesForReal:
    """The point of the guard must survive the narrowing."""

    def _run(self, tmp, env_extra=None):
        import os
        tests = tmp / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "test_t.py").write_text("def test_ok():\n    assert True\n",
                                         encoding="utf-8")
        (tests / "conftest.py").write_text(
            (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"),
            encoding="utf-8")
        tools = tmp / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "real.py").write_text("x = 1\n", encoding="utf-8")
        # conftest.py imports the shared guard module from tools/, so the fixture tree
        # needs it too. Copied rather than stubbed: a stub could drift from the real
        # refusal code and make this test pass against a value nothing else uses.
        (tools / "conftest_guard.py").write_text(
            (REPO_ROOT / "tools" / "conftest_guard.py").read_text(encoding="utf-8"),
            encoding="utf-8")
        (tools / "real.py.mutation_backup").write_text("x = 1\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("MUTATION_CHECK_ACTIVE", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-m", "pytest", "-q", str(tests)],
                              capture_output=True, text=True, cwd=str(tmp), env=env)

    def test_the_orphan_note_is_actually_VISIBLE_in_default_output(self, tmp_path):
        """A warning nobody sees is not a warning. pytest CAPTURES stdout from a session
        fixture, so the first version of this note -- a plain print() -- produced exactly
        zero visible characters in a default run. Asserted end-to-end through a real
        pytest subprocess with no -s, because that is the only way to catch it."""
        import os
        tests = tmp_path / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "test_t.py").write_text("def test_ok():\n    assert True\n",
                                         encoding="utf-8")
        (tests / "conftest.py").write_text(
            (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"),
            encoding="utf-8")
        tools = tmp_path / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "conftest_guard.py").write_text(
            (REPO_ROOT / "tools" / "conftest_guard.py").read_text(encoding="utf-8"),
            encoding="utf-8")
        # orphan: no source file beside it
        (tools / "gone.py 2.mutation_backup").write_text("x = 1\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("MUTATION_CHECK_ACTIVE", None)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(tests)],
                           capture_output=True, text=True, cwd=str(tmp_path), env=env)
        assert r.returncode == 0, "an orphan must not block the run"
        assert "orphaned mutation backup" in r.stdout, (
            "the orphan note is invisible in default output; pytest captured it"
        )
        assert "gone.py 2.mutation_backup" in r.stdout, "the note must name the file"

    def test_a_genuine_live_backup_still_refuses_with_the_new_code(self, tmp_path):
        from conftest_guard import CONFTEST_REFUSAL
        r = self._run(tmp_path)
        assert r.returncode == CONFTEST_REFUSAL, r.stdout[-500:]
        assert "REFUSING TO RUN" in r.stdout

    def test_mutation_check_active_still_bypasses_the_guard(self, tmp_path):
        """mutation_check's own subprocesses are SUPPOSED to see a mutated tree."""
        r = self._run(tmp_path, {"MUTATION_CHECK_ACTIVE": "1"})
        assert r.returncode == 0, r.stdout[-500:]
