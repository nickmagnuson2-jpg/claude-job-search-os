"""Tests for prepush_pii_guard.py — fable-audit 2026-07-07 #9.

The bug: the guard scanned the on-disk working tree, so PII committed in a pushed
commit slipped through whenever the working tree had since been cleaned. The fix
scans the committed blobs at the pushed sha (read from the pre-push hook's stdin),
falling back to a working-tree scan only for manual invocations.
"""
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "tools" / "prepush_pii_guard.py"
ZERO_SHA = "0" * 40
FIXTURE_DENYLIST = "# fixture\nZorptech\nPat Zorp\n"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def _init_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    dl = tmp_path / "tools" / ".pii-denylist.txt"
    dl.parent.mkdir(parents=True, exist_ok=True)
    dl.write_text(FIXTURE_DENYLIST, encoding="utf-8")
    return tmp_path


def _commit(repo, rel, content):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"add {rel}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run(repo, stdin):
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=stdin, capture_output=True, text=True, cwd=str(repo),
    )


def _push_line(sha):
    return f"refs/heads/main {sha} refs/heads/main {ZERO_SHA}\n"


def test_blocks_pii_in_pushed_commit(tmp_path):
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "framework/leak.md", "Target company: Zorptech\n")
    r = _run(repo, _push_line(sha))
    assert r.returncode == 1, r.stderr
    assert "Zorptech" in r.stderr


def test_blocks_committed_pii_even_when_worktree_cleaned(tmp_path):
    """The regression: PII is in the pushed commit but the working tree has been
    scrubbed. The old working-tree scan passed; the sha scan must still block."""
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "framework/leak.md", "Target company: Zorptech\n")
    # Scrub the working tree (do NOT commit) — on-disk file is now clean.
    (repo / "framework" / "leak.md").write_text("Target company: Acme\n", encoding="utf-8")
    r = _run(repo, _push_line(sha))
    assert r.returncode == 1, "sha scan must catch committed PII despite a clean worktree"
    assert "Zorptech" in r.stderr


def test_manual_run_uses_worktree_fallback(tmp_path):
    """No pre-push stdin => working-tree scan. With the tree scrubbed, it passes —
    the counterpart proving the two code paths diverge as intended."""
    repo = _init_repo(tmp_path)
    _commit(repo, "framework/leak.md", "Target company: Zorptech\n")
    (repo / "framework" / "leak.md").write_text("Target company: Acme\n", encoding="utf-8")
    r = _run(repo, "")  # empty stdin -> no pushed shas -> worktree fallback
    assert r.returncode == 0, r.stderr


def test_clean_pushed_commit_passes(tmp_path):
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "framework/ok.md", "Target company: Acme (placeholder)\n")
    r = _run(repo, _push_line(sha))
    assert r.returncode == 0, r.stderr


def test_branch_deletion_sha_skipped(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "framework/leak.md", "Target company: Zorptech\n")
    # A deletion pushes an all-zero local sha; nothing to scan -> allow.
    r = _run(repo, _push_line(ZERO_SHA))
    assert r.returncode == 0, r.stderr
