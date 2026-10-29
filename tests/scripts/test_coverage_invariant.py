"""9c: every working-tree file is git-tracked, backed up by tools/backup-data.sh, or declared
ephemeral. Fixture repos only, except the one live test at the end."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import coverage_invariant as ci  # noqa: E402

BACKUP = '''#!/bin/bash
STAGE_PATHS=(
  "$WORK_TREE/data/"
  "$WORK_TREE/tools/.secret.conf"
)
for p in "${STAGE_PATHS[@]}"; do :; done
'''


def _git(root, *a):
    subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)


def _repo(tmp_path):
    root = tmp_path / "r"
    (root / "tools").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("data/\nlost/\ntools/.secret.conf\ntools/.other.json\n__pycache__/\n")
    (root / "tools" / "backup-data.sh").write_text(BACKUP)
    (root / "tools" / "x.py").write_text("x = 1\n")
    _git(root, "add", ".")
    _git(root, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "i")
    (root / "data").mkdir()
    (root / "data" / "notes.md").write_text("n")
    (root / "tools" / ".secret.conf").write_text("s")
    (root / "tools" / "__pycache__").mkdir()
    (root / "tools" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\0")
    return root


def test_stage_paths_are_read_from_the_script():
    assert ci.stage_paths(BACKUP) == ["data/", "tools/.secret.conf"]
    real = ci.stage_paths((REPO / "tools" / "backup-data.sh").read_text())
    assert "data/" in real and "output/" in real and "tools/.cross-model-ledger.jsonl" in real
    with pytest.raises(ValueError):
        ci.stage_paths("no array here")
    with pytest.raises(ValueError):
        ci.stage_paths("STAGE_PATHS=(\n)\n")


def test_backed_up_matches_dirs_by_prefix_and_files_exactly():
    stage = ["data/", "tools/.secret.conf"]
    assert ci.backed_up("data/a/b.md", stage) and ci.backed_up("tools/.secret.conf", stage)
    assert not ci.backed_up("database.md", stage) and not ci.backed_up("tools/.secret.conf.bak", stage)


def test_ephemeral_needs_a_listed_pattern():
    assert ci.ephemeral("tools/__pycache__/x.pyc") and ci.ephemeral("tools/launchd/logs/a.log")
    assert ci.ephemeral("tools/gmail_token.json").startswith("credential")
    assert ci.ephemeral("scratchpad/notes.md") is None
    assert all(why for _, why in ci.EPHEMERAL), "every ephemeral entry states its reason"


def test_a_covered_tree_passes(tmp_path):
    out = ci.uncovered(_repo(tmp_path))
    assert out["uncovered"] == [] and out["untracked_files"] == 3 and out["stage_paths"] == 2
    assert (out["backed_up"], out["ephemeral"]) == (2, 1)


def test_a_file_in_neither_copy_is_reported(tmp_path):
    root = _repo(tmp_path)
    (root / "lost").mkdir()
    (root / "lost" / "only-copy.md").write_text("x")
    (root / "tools" / ".other.json").write_text("{}")
    (root / "new-untracked.md").write_text("y")
    out = ci.uncovered(root)
    assert out["uncovered"] == ["lost/only-copy.md", "new-untracked.md", "tools/.other.json"]
    assert out["uncovered_top_level"] == ["lost/", "new-untracked.md", "tools/"]
    r = subprocess.run([sys.executable, str(REPO / "tools" / "coverage_invariant.py"), "--repo-root",
                        str(root)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "population 6 | included 3" in r.stdout and "lost/only-copy.md" in r.stdout
    assert "backed up 2, declared ephemeral 1" in r.stdout


def test_cli_setup_errors_exit_2(tmp_path):
    r = subprocess.run([sys.executable, str(REPO / "tools" / "coverage_invariant.py"), "--repo-root",
                        str(tmp_path), "--json"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "ERROR" in r.stderr
    root = _repo(tmp_path / "ok")
    r = subprocess.run([sys.executable, str(REPO / "tools" / "coverage_invariant.py"), "--repo-root",
                        str(root), "--json"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and '"uncovered_count": 0' in r.stdout


def test_untracked_files_reports_a_git_failure(tmp_path):
    with pytest.raises(RuntimeError):
        ci.untracked_files(tmp_path)


@pytest.mark.live_data
@pytest.mark.xfail(strict=False, reason="2026-10-29: 551 files in neither copy on the live tree "
                   "(.claude/.venv, .claude/settings.local.json, scratchpad/, tools state files, "
                   ".privacy-backup/). Each needs Nick's call: back it up or declare it ephemeral. "
                   "Make this strict once the list is empty.")
def test_the_live_tree_is_fully_covered():
    assert ci.uncovered(REPO)["uncovered"] == []
