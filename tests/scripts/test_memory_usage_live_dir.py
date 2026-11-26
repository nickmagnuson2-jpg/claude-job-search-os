"""memory_usage.LIVE_MEMORY_DIR is the MAIN checkout's live memory tier, from any checkout.

The other memory_usage tests read LIVE_MEMORY_DIR relative to itself, so a wrong derivation
passed all 747 of them: a hand check on 2026-11-26 (S3) changed line 739 three ways
(parents[0]; main_checkout dropped; "memory" -> "memories") and every test stayed green.
This test loads a copy of the module from a fake linked worktree and compares the result
with the folder Claude Code would use, derived here without claude_project_dir: the main
checkout's absolute path with every "/" turned into "-", under CLAUDE_CONFIG_DIR/projects.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _fake_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout (with a .git folder) and a linked worktree of it holding tools/."""
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / "tools").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n", encoding="utf-8")
    for src in TOOLS.glob("*.py"):
        dst = wt / "tools" / src.name
        if src.name == "memory_usage.py":
            dst.write_bytes(src.read_bytes())
        else:
            dst.symlink_to(src)
    return main, wt


def _load(path: Path):
    """Execute a copy of the module, then undo everything the execution added: its own
    sys.path insert and any module it imported from the temp tree (Codex s1c 10.F2)."""
    spec = importlib.util.spec_from_file_location("memory_usage_live_dir_probe", path)
    mod = importlib.util.module_from_spec(spec)
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = path_before
        for name in set(sys.modules) - modules_before:
            del sys.modules[name]
    return mod


def test_live_memory_dir_is_the_main_checkouts_tier_from_a_worktree(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()          # the module resolves its own path
    main, wt = _fake_worktree(tmp_path)
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    mod = _load(wt / "tools" / "memory_usage.py")
    expected = config / "projects" / str(main).replace("/", "-") / "memory"
    assert mod.LIVE_MEMORY_DIR == expected


def test_loading_the_copy_leaves_no_trace(tmp_path, monkeypatch):
    """Codex s1c 10.F2: the copy's own sys.path insert and the siblings it imports from the
    temp tree must not outlive the test, or a later test reads a deleted folder."""
    tmp_path = tmp_path.resolve()
    _main, wt = _fake_worktree(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    path_before = list(sys.path)
    _load(wt / "tools" / "memory_usage.py")
    assert sys.path == path_before
    from_tmp = sorted(name for name, m in list(sys.modules.items())
                      if str(getattr(m, "__file__", None) or "").startswith(str(tmp_path)))
    assert from_tmp == []
