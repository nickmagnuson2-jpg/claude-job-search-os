"""tools/conftest_guard.py all_backups: a root sees only backups under it, by path component.

Plan A2, 2026-10-28 (output/mutation-parallel-102826/plan.md). A bare string prefix let
`.../wt1` claim the backups of `.../wt10`; a parallel run puts one worktree per worker side
by side, so every worker would have seen its neighbors' backups and refused its own suite.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import conftest_guard as g


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(d))
    return d


def _backup(source: Path) -> Path:
    b = g.backup_path(source)
    b.write_text("x")
    return b


def test_a_root_does_not_claim_a_sibling_whose_name_it_prefixes(tmp_path, store):
    wt1, wt10 = tmp_path / "wt1", tmp_path / "wt10"
    for d in (wt1, wt10):
        (d / "tools").mkdir(parents=True)
        (d / "tools" / "x.py").write_text("")
    mine, theirs = _backup(wt1 / "tools" / "x.py"), _backup(wt10 / "tools" / "x.py")
    assert g.all_backups(wt1) == [mine]
    assert g.all_backups(wt10) == [theirs]
    assert g.stranded_backups(wt1) == [mine]


def test_a_root_sees_backups_at_any_depth_under_it(tmp_path, store):
    root = tmp_path / "repo"
    (root / "a" / "b").mkdir(parents=True)
    deep = _backup(root / "a" / "b" / "c.py")
    assert g.all_backups(root) == [deep]
    assert g.all_backups(tmp_path) == [deep]


def test_a_backup_of_the_root_path_itself_is_in_scope(tmp_path, store):
    f = tmp_path / "single.py"
    b = _backup(f)
    assert g.all_backups(f) == [b]


def test_no_root_returns_every_backup(tmp_path, store):
    a, b = _backup(tmp_path / "p" / "x.py"), _backup(tmp_path / "q" / "y.py")
    assert sorted(g.all_backups()) == sorted([a, b])


def test_an_empty_or_missing_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MUTATION_BACKUP_DIR", str(tmp_path / "absent"))
    assert g.all_backups(tmp_path) == []
