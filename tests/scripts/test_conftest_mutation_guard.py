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


@pytest.fixture(autouse=True)
def _isolated_backup_store(tmp_path, monkeypatch):
    """Point the shared backup store at a per-test directory.

    The store is now a single cache dir shared by every tree, so without this a test's
    backups would be visible to the real repo's guard (and vice versa). Scoping in the
    code is by decoded SOURCE path, but isolating the store too keeps these tests from
    depending on that for correctness."""
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "_bakstore"))


class TestOrphanBackupsDoNotHaltTheSuite:
    """An orphan is a backup whose SOURCE does not exist. It cannot be an in-flight
    mutation, because mutation_check only ever writes `<target>.mutation_backup` beside a
    real `<target>`. Halting on one is a false accusation that costs the whole signal."""

    def _mk(self, root, name, with_source, monkeypatch=None, store=None):
        """Create a backup for `root/name` through the SHARED backup_path.

        Backups no longer sit beside their target -- they live in a cache dir outside the
        working tree (iCloud was making conflict copies of them). The test writes through
        the same function the tool does, so it cannot drift from the real layout."""
        import conftest_guard as g
        root.mkdir(parents=True, exist_ok=True)
        src = root / name
        if with_source:
            src.write_text("x = 1\n", encoding="utf-8")
        bak = g.backup_path(src)
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("x = 1\n", encoding="utf-8")
        return bak

    def test_a_backup_whose_source_exists_is_stranded(self, tmp_path):
        from conftest_guard import source_of, stranded_backups
        self._mk(tmp_path / "tools", "real.py", with_source=True)
        # identity is the SOURCE the backup protects; the backup's own filename is now a
        # percent-encoded absolute path in a shared store
        assert [source_of(p).name for p in stranded_backups(tmp_path)] == ["real.py"]

    def test_a_backup_whose_source_is_MISSING_is_not_stranded(self, tmp_path):
        """The exact 2026-08-31 file: `todo_write.py 2.mutation_backup` with no
        `todo_write.py 2` beside it."""
        from conftest_guard import stranded_backups
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        assert stranded_backups(tmp_path) == []

    def test_a_live_backup_is_still_caught_alongside_an_orphan(self, tmp_path):
        """The orphan must not mask a real one -- that would invert the bug into a
        silent pass while the tree really is mid-mutation."""
        from conftest_guard import source_of, stranded_backups
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        self._mk(tmp_path / "tools", "real.py", with_source=True)
        assert [source_of(p).name for p in stranded_backups(tmp_path)] == ["real.py"]

    def test_orphans_are_reported_separately_not_swallowed(self, tmp_path):
        """Not refusing is not the same as ignoring: an orphan is still junk that voids
        nothing but should be cleaned, so it must remain visible."""
        from conftest_guard import orphan_backups, source_of
        self._mk(tmp_path / "tools", "todo_write.py 2", with_source=False)
        assert [source_of(p).name for p in orphan_backups(tmp_path)] == ["todo_write.py 2"]


class TestOneSharedStoreDoesNotLeakBetweenTrees:
    """Every tree writes to ONE store now, so scoping has to happen on the decoded source.

    Found by mutation 2026-09-01: dropping the root filter left all tests green, because
    each test had its own isolated store and never exercised the sharing. In real use the
    repo and every tmp fixture tree share `~/Library/Caches/claude-mutation-backups`, and
    without the filter a fixture's leftover backup would make the REAL repo's suite refuse
    to run -- reintroducing the outage this whole change exists to prevent."""

    def test_a_tree_sees_only_its_own_backups(self, tmp_path, monkeypatch):
        import conftest_guard as g
        store = tmp_path / "shared_store"
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(store))
        a, b = tmp_path / "treeA", tmp_path / "treeB"
        for t in (a, b):
            (t / "tools").mkdir(parents=True)
            (t / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
            bak = g.backup_path(t / "tools" / "x.py")
            bak.parent.mkdir(parents=True, exist_ok=True)
            bak.write_text("x = 1\n", encoding="utf-8")

        assert len(g.all_backups()) == 2, "both trees share one store"
        for tree in (a, b):
            got = g.stranded_backups(tree)
            assert len(got) == 1, f"{tree.name} saw another tree's backup"
            assert str(g.source_of(got[0])).startswith(str(tree.resolve()))

    def test_an_unrelated_trees_wreckage_does_not_make_this_tree_refuse(
            self, tmp_path, monkeypatch):
        """The consequence, stated as behaviour: a leftover from somewhere else must not
        halt a suite that is perfectly clean."""
        import conftest_guard as g
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "shared_store"))
        other = tmp_path / "somewhere_else"
        (other / "tools").mkdir(parents=True)
        (other / "tools" / "y.py").write_text("y = 1\n", encoding="utf-8")
        bak = g.backup_path(other / "tools" / "y.py")
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("y = 1\n", encoding="utf-8")

        mine = tmp_path / "mine"
        (mine / "tools").mkdir(parents=True)
        assert g.stranded_backups(mine) == []
        assert g.orphan_backups(mine) == []


class TestBackupDirIsRedirectable:
    """The override is what keeps tests out of the real store. Mutation showed it could be
    ignored entirely with every test still green -- which would mean the suite had been
    quietly writing into `~/Library/Caches/claude-mutation-backups` all along."""

    def test_the_env_var_actually_moves_the_store(self, tmp_path, monkeypatch):
        import conftest_guard as g
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "elsewhere"))
        assert g.backup_dir() == tmp_path / "elsewhere"
        assert g.backup_path(tmp_path / "tools" / "z.py").parent == tmp_path / "elsewhere"

    def test_without_the_override_the_store_is_outside_the_synced_tree(self, monkeypatch):
        """The whole point: not under ~/Documents, which is inside iCloud Drive."""
        from pathlib import Path
        import conftest_guard as g
        monkeypatch.delenv("MUTATION_BACKUP_DIR", raising=False)
        d = g.backup_dir()
        assert "Documents" not in d.parts, \
            "backup store is inside the iCloud-synced tree; conflict copies will return"
        assert d.is_relative_to(Path.home())


class TestOrphanPruning:
    """The store is shared and outside every tree it serves, so nothing else clears it.
    Measured 2026-09-01: 27 orphans accumulated in one afternoon of test runs, each one a
    tmp fixture tree that was deleted while its backup lived on in the cache."""

    def test_orphans_are_deleted(self, tmp_path, monkeypatch):
        import conftest_guard as g
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "store"))
        gone = tmp_path / "tools" / "gone.py"
        gone.parent.mkdir(parents=True)
        bak = g.backup_path(gone)          # source never created
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("x = 1\n", encoding="utf-8")
        assert g.prune_orphans() == [bak]
        assert not bak.exists()

    def test_a_LIVE_backup_is_never_pruned(self, tmp_path, monkeypatch):
        """The safety property. Pruning a backup whose source still exists would destroy
        the only copy of an unmutated file while a run is in flight."""
        import conftest_guard as g
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "store"))
        live = tmp_path / "tools" / "live.py"
        live.parent.mkdir(parents=True)
        live.write_text("x = 1\n", encoding="utf-8")
        bak = g.backup_path(live)
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("x = 1\n", encoding="utf-8")
        assert g.prune_orphans() == []
        assert bak.exists(), "pruned a live backup - the unmutated source would be lost"

    def test_pruning_an_empty_store_is_not_an_error(self, tmp_path, monkeypatch):
        import conftest_guard as g
        monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "never_created"))
        assert g.prune_orphans() == []


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
        # Write the backup through the SHARED backup_path into an isolated store, not
        # beside the target: backups moved out of the working tree on 2026-09-01.
        import conftest_guard as g
        os.environ["MUTATION_BACKUP_DIR"] = str(tmp / "_bakstore")
        bak = g.backup_path(tools / "real.py")
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("x = 1\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "MUTATION_BACKUP_DIR": str(tmp / "_bakstore")}
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
        # orphan: a backup whose source was never created
        import conftest_guard as g
        os.environ["MUTATION_BACKUP_DIR"] = str(tmp_path / "_bakstore")
        bak = g.backup_path(tools / "gone.py 2")
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text("x = 1\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "MUTATION_BACKUP_DIR": str(tmp_path / "_bakstore")}
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
