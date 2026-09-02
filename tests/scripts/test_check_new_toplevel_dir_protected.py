"""Clean/block tests for check_new_toplevel_dir_protected.py."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(ROOT, "tools", "check_new_toplevel_dir_protected.py")


def run(file_path, cwd=ROOT):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_PROJECT_DIR=cwd)
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_input": {"file_path": file_path}}),
        capture_output=True, text=True, env=env, timeout=20,
    )
    return p.returncode, p.stderr


# --- CLEAN: must not block -------------------------------------------------
@pytest.mark.parametrize("path", [
    "data/goals.md",                      # existing dir, gitignored + backed up
    "output/analysis/x.md",               # existing dir, gitignored + backed up
    "tools/some_new_tool.py",             # existing dir, tracked publicly
    "framework/whatever.md",              # existing dir
    "README.md",                          # top-level FILE, not a new dir
    ".claude/skills/foo/SKILL.md",        # exempt harness dir
    "/etc/passwd",                        # outside the repo
])
def test_clean_paths_do_not_block(path):
    rc, err = run(path if path.startswith("/") else os.path.join(ROOT, path))
    assert rc == 0, "false positive on %s: %s" % (path, err)


def test_bad_json_fails_open():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_PROJECT_DIR=ROOT)
    p = subprocess.run([sys.executable, HOOK], input="not json",
                       capture_output=True, text=True, env=env, timeout=20)
    assert p.returncode == 0


def test_empty_payload_fails_open():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_PROJECT_DIR=ROOT)
    p = subprocess.run([sys.executable, HOOK], input="{}",
                       capture_output=True, text=True, env=env, timeout=20)
    assert p.returncode == 0


# --- BLOCK: must catch the real failure ------------------------------------
def test_new_unprotected_toplevel_dir_blocks():
    rc, err = run(os.path.join(ROOT, "totally-new-dir-xyz", "note.md"))
    assert rc == 2
    assert "BLOCKED check_new_toplevel_dir_protected" in err
    assert "totally-new-dir-xyz" in err
    assert "backup-data.sh" in err


def test_block_message_names_both_axes():
    _, err = run(os.path.join(ROOT, "another-new-dir-abc", "f.txt"))
    assert "gitignore" in err.lower()
    assert "git check-ignore -v" in err


def test_the_8_16_regression_frames_dir_is_now_clean():
    """frames/ was the origin fire; it is now gitignored AND in backup-data.sh."""
    rc, err = run(os.path.join(ROOT, "frames", "x.yaml"))
    assert rc == 0, "frames/ should be protected now: %s" % err
