"""2026-10-21: every other project's auto-memory is in the nightly backup.

Peer repos (dailyidea, mag5-advisors, ...) keep their Claude Code memory under
~/.claude/projects/<slug>/memory, outside any repo, and until this loop existed nothing
backed them up. The test runs the loop itself from the script against fake directories.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "backup-data.sh"


def _loop() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index('  for mem in "$GLOBAL_CLAUDE"/projects/*/memory; do')
    end = text.index("\n  done", start) + len("\n  done")
    return text[start:end]


def _run(tmp_path: Path) -> Path:
    home = tmp_path / "claude"
    own = home / "projects" / "own-repo" / "memory"
    peer = home / "projects" / "peer-repo" / "memory"
    empty = home / "projects" / "scratch" / "memory"
    for d in (own, peer, empty):
        d.mkdir(parents=True)
    (own / "a.md").write_text("own")
    (peer / "rule.md").write_text("peer")
    mirror = tmp_path / "mirror"
    env = {"GLOBAL_CLAUDE": str(home), "GLOBAL_MIRROR": str(mirror),
           "CANONICAL_MEMORY": str(own), "PATH": "/usr/bin:/bin"}
    subprocess.run(["bash", "-c", _loop()], env=env, check=True)
    return mirror


def test_peer_project_memory_is_mirrored(tmp_path):
    mirror = _run(tmp_path)
    assert (mirror / "projects" / "peer-repo" / "memory" / "rule.md").read_text() == "peer"


def test_own_memory_is_not_copied_twice_and_empty_ones_are_skipped(tmp_path):
    mirror = _run(tmp_path)
    assert sorted(p.name for p in (mirror / "projects").iterdir()) == ["peer-repo"]


def test_the_mirror_lands_under_the_staged_memory_tree():
    """The loop writes under GLOBAL_MIRROR, which must sit under memory/ (staged, gitignored)."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'GLOBAL_MIRROR="$WORK_TREE/memory/global-claude-mirror"' in text
    assert '"$WORK_TREE/memory/"' in text
