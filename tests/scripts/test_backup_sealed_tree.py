"""The nightly backup still carries the sealed tree after it moved out of the work tree.

2026-10-13: the sealed directory moved to a sibling of the repo so that a recursive
read of the repo cannot reach it. The backup's overlay git uses the repo as its work
tree, so without a second staging step the next backup would have committed every
sealed file as deleted. These tests run the real script against a throwaway work tree,
overlay git dir and bare remote.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "backup-data.sh"
SEALED = "data/project-background"


def _git(git_dir, *args):
    r = subprocess.run(["git", f"--git-dir={git_dir}", *args],
                       capture_output=True, text=True)
    return r.stdout.splitlines()


@pytest.fixture
def world(tmp_path):
    """A work tree named `repo`, its overlay git dir, and a bare remote."""
    work, overlay, remote = tmp_path / "repo", tmp_path / "overlay.git", tmp_path / "remote.git"
    (work / "tools").mkdir(parents=True)
    (work / "data").mkdir()
    (work / "data" / "notes.md").write_text("ordinary\n", encoding="utf-8")
    block = SCRIPT.read_text(encoding="utf-8").split("STAGE_PATHS=(", 1)[1].split("\n)", 1)[0]
    for rel in re.findall(r'"\$WORK_TREE/([^"]+)"', block):
        target = work / rel
        if rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            (target / ".keep").write_text("", encoding="utf-8")
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
    shutil.copy(SCRIPT, work / "tools" / "backup-data.sh")
    (work / "tools" / ".private-backup.conf").write_text(
        f'PRIVATE_GIT_DIR="{overlay}"\nPRIVATE_REPO_URL="local test remote"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(overlay)], check=True)
    _git(overlay, "remote", "add", "origin", str(remote))
    _git(overlay, "config", "user.email", "t@example.invalid")
    _git(overlay, "config", "user.name", "Test")
    return work, overlay, tmp_path


def _backup(world, **env):
    work, _, tmp = world
    home = tmp / "home"          # an empty HOME: no ~/.claude to mirror
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(work / "tools" / "backup-data.sh")], capture_output=True, text=True,
        cwd=str(tmp), env={**os.environ, "HOME": str(home), **env})


def _sealed_in_head(overlay):
    return _git(overlay, "ls-tree", "-r", "--full-tree", "--name-only", "HEAD", "--", SEALED)


def _put_sealed(base):
    d = base / SEALED / "sub"
    d.mkdir(parents=True)
    (base / SEALED / "a.md").write_text("one\n", encoding="utf-8")
    (d / "b.md").write_text("two\n", encoding="utf-8")


def test_a_sealed_tree_in_the_sibling_is_backed_up_under_its_old_paths(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    r = _backup(world)
    assert r.returncode == 0, r.stderr
    assert _sealed_in_head(overlay) == [f"{SEALED}/a.md", f"{SEALED}/sub/b.md"]
    assert "Sealed tree: 2 files staged, 0 staged for deletion." in r.stdout
    assert "data/notes.md" in _git(overlay, "ls-tree", "-r", "--name-only", "HEAD")


def test_moving_the_tree_out_changes_nothing_in_the_backup(world):
    """Backed up in-tree, then moved to the sibling: same paths, same content, no
    deletion and no second copy."""
    work, overlay, tmp = world
    _put_sealed(work)
    assert _backup(world).returncode == 0
    before = _git(overlay, "ls-tree", "-r", "HEAD", "--", SEALED)
    (tmp / "repo-sealed" / "data").mkdir(parents=True)
    shutil.move(str(work / SEALED), str(tmp / "repo-sealed" / SEALED))
    assert _backup(world).returncode == 0
    assert _git(overlay, "ls-tree", "-r", "HEAD", "--", SEALED) == before
    assert _git(overlay, "diff", "--stat", "HEAD~1", "HEAD") == []


def test_an_edit_in_the_sibling_reaches_the_backup(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    assert _backup(world).returncode == 0
    (tmp / "repo-sealed" / SEALED / "a.md").write_text("changed\n", encoding="utf-8")
    assert _backup(world).returncode == 0
    assert _git(overlay, "show", f"HEAD:{SEALED}/a.md") == ["changed"]


def test_a_missing_sibling_stops_the_backup_before_it_commits_the_deletion(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    assert _backup(world).returncode == 0
    head = _git(overlay, "rev-parse", "HEAD")
    shutil.rmtree(tmp / "repo-sealed")
    r = _backup(world)
    assert r.returncode == 1
    assert "2 sealed file(s) in the last backup are missing now" in r.stderr
    assert _git(overlay, "rev-parse", "HEAD") == head, "nothing may be committed"
    assert len(_sealed_in_head(overlay)) == 2
    # and the index is left holding the files, so the next good run is not a re-add
    assert len(_git(overlay, "ls-files", "--cached", "--", f":/{SEALED}")) == 2


def test_sealed_home_can_be_pointed_elsewhere(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "elsewhere")
    r = _backup(world, SEALED_HOME=str(tmp / "elsewhere"))
    assert r.returncode == 0, r.stderr
    assert len(_sealed_in_head(overlay)) == 2


def test_a_tree_with_no_sealed_files_at_all_still_backs_up(world):
    work, overlay, tmp = world
    r = _backup(world)
    assert r.returncode == 0, r.stderr
    assert _sealed_in_head(overlay) == []


# --- One missing path used to empty the whole backup (2026-10-13) -------------
# All paths went to a single `git add` with its errors discarded, and git stages nothing
# when one pathspec matches no file.

def test_one_missing_path_is_named_and_the_rest_is_still_backed_up(world):
    work, overlay, tmp = world
    shutil.rmtree(work / "inbox")
    (work / "tools" / ".owner-identity.txt").unlink()
    r = _backup(world)
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("WARNING: not staged") == 2
    assert "/inbox/" in r.stderr and ".owner-identity.txt" in r.stderr
    tree = _git(overlay, "ls-tree", "-r", "--name-only", "HEAD")
    assert "data/notes.md" in tree and "output/.keep" in tree


def test_a_complete_tree_backs_up_without_a_warning(world):
    work, overlay, tmp = world
    r = _backup(world)
    assert r.returncode == 0 and "WARNING: not staged" not in r.stderr
    assert "data/notes.md" in _git(overlay, "ls-tree", "-r", "--name-only", "HEAD")


def test_a_tree_where_nothing_can_be_staged_commits_nothing(world):
    work, overlay, tmp = world
    for child in work.iterdir():
        if child.name == "tools":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for f in (work / "tools").iterdir():
        if f.name not in ("backup-data.sh", ".private-backup.conf"):
            f.unlink()
    (work / "tools" / ".private-backup.conf").rename(tmp / "conf")
    conf = (tmp / "conf").read_text(encoding="utf-8")
    script = (work / "tools" / "backup-data.sh").read_text(encoding="utf-8")
    (work / "tools" / "backup-data.sh").write_text(
        script.replace('CONF="$SCRIPT_DIR/.private-backup.conf"', f'CONF="{tmp / "conf"}"'),
        encoding="utf-8")
    r = _backup(world)
    assert r.returncode == 1 and "none of the" in r.stderr
    assert _git(overlay, "rev-parse", "--verify", "-q", "HEAD") == []



# --- Cross-model review 305, 2026-10-13 ---------------------------------------

def test_a_partly_missing_sibling_stops_the_backup_too(world):
    """One of two sealed files gone: the count is still above zero, and committing
    would delete the other from the backup."""
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    assert _backup(world).returncode == 0
    head = _git(overlay, "rev-parse", "HEAD")
    (tmp / "repo-sealed" / SEALED / "sub" / "b.md").unlink()
    r = _backup(world)
    assert r.returncode == 1
    assert "1 sealed file(s) in the last backup are missing now" in r.stderr
    assert _git(overlay, "rev-parse", "HEAD") == head
    assert len(_git(overlay, "ls-files", "--cached", "--", f":/{SEALED}")) == 2


def test_a_deliberate_sealed_deletion_needs_the_named_override(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    assert _backup(world).returncode == 0
    (tmp / "repo-sealed" / SEALED / "sub" / "b.md").unlink()
    r = _backup(world, SEALED_ALLOW_DELETE="1")
    assert r.returncode == 0, r.stderr
    assert _sealed_in_head(overlay) == [f"{SEALED}/a.md"]
    assert _backup(world, SEALED_ALLOW_DELETE="yes").returncode == 0, "nothing left to delete"


def test_only_the_exact_override_value_allows_a_deletion(world):
    work, overlay, tmp = world
    _put_sealed(tmp / "repo-sealed")
    assert _backup(world).returncode == 0
    (tmp / "repo-sealed" / SEALED / "a.md").unlink()
    assert _backup(world, SEALED_ALLOW_DELETE="yes").returncode == 1


def test_a_failed_push_is_exit_1_and_never_says_done(world):
    work, overlay, tmp = world
    assert _backup(world).returncode == 0
    shutil.rmtree(tmp / "remote.git")
    (work / "data" / "notes.md").write_text("changed\n", encoding="utf-8")
    r = _backup(world)
    assert r.returncode == 1
    assert "the push failed" in r.stderr and "NOT on the remote" in r.stderr
    assert "Done." not in r.stdout


def test_a_failed_commit_is_exit_1_and_nothing_is_pushed(world):
    work, overlay, tmp = world
    assert _backup(world).returncode == 0
    remote_head = _git(tmp / "remote.git", "rev-parse", "master")
    hook = overlay / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    r = _backup(world)
    assert r.returncode == 1 and "the backup commit failed" in r.stderr
    assert "Pushing" not in r.stdout and "Done." not in r.stdout
    assert _git(tmp / "remote.git", "rev-parse", "master") == remote_head
