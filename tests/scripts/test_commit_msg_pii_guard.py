"""Tests for tools/commit_msg_pii_guard.py, run through the real tracked commit-msg hook.

Each test builds a throwaway repo with the hook installed and a fake denylist, then
commits. The tokens are invented (Zorpcorp, Harbor, Quillfen); none is a real entity.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
HOOK = TOOLS / "hooks" / "commit-msg"
COPIED = ("commit_msg_pii_guard.py", "precommit_pii_guard.py", "check_public_pii.py",
          "shell_tokens.py", "hook_runtime.py")

sys.path.insert(0, str(TOOLS))
import commit_msg_pii_guard as guard  # noqa: E402


def _git(repo, *args, env=None, check=True):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("GIT_") and not k.startswith("PII_")}
    e.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    e.update(env or {})
    r = subprocess.run(["git", *args], cwd=repo, env=e, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tools" / "hooks").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    for name in COPIED:
        (root / "tools" / name).write_bytes((TOOLS / name).read_bytes())
    (root / ".gitignore").write_text("tools/.pii-denylist*.txt\n")
    (root / "tools" / ".pii-denylist.txt").write_text("Zorpcorp\n")
    (root / "tools" / ".pii-denylist-ambiguous.txt").write_text("Harbor\n")
    (root / "tools" / ".pii-denylist-targetwords.txt").write_text("Quillfen\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed", "--no-verify")
    hook = root / ".git" / "hooks" / "commit-msg"
    hook.write_bytes(HOOK.read_bytes())
    hook.chmod(0o755)
    return root


def _commit(repo, message, env=None, args=()):
    p = repo / "note.md"
    p.write_text(p.read_text() + "x\n" if p.exists() else "x\n")
    _git(repo, "add", "--", "note.md")
    return _git(repo, "commit", "-q", "-m", message, *args, env=env, check=False)


def _subjects(repo):
    return _git(repo, "log", "--format=%s").stdout.split("\n")


def test_a_real_name_in_the_message_is_refused(repo):
    r = _commit(repo, "fix: a case Zorpcorp hit")
    assert r.returncode != 0
    assert "Zorpcorp" in r.stderr and "commit_msg_pii_guard" in r.stderr
    assert "fix: a case Zorpcorp hit" not in _subjects(repo)


def test_a_real_name_in_the_body_is_refused(repo):
    r = _commit(repo, "fix: a case\n\nThe replay matched Zorpcorp twice.")
    assert r.returncode != 0


def test_a_clean_message_commits(repo):
    r = _commit(repo, "fix: describe the case by kind")
    assert r.returncode == 0, r.stderr
    assert "fix: describe the case by kind" in _subjects(repo)


def test_an_ambiguous_word_warns_and_commits(repo):
    r = _commit(repo, "docs: the Harbor of the old plan")
    assert r.returncode == 0, r.stderr
    assert "Harbor" in r.stderr


def test_a_target_word_warns_and_commits(repo):
    r = _commit(repo, "docs: we met Quillfen")
    assert r.returncode == 0, r.stderr
    assert "Quillfen" in r.stderr


def test_an_override_commits_and_is_recorded(repo):
    r = _commit(repo, "fix: Zorpcorp case", env={"PII_COMMIT_OVERRIDE": "fixture name"})
    assert r.returncode == 0, r.stderr
    log = repo / ".git" / "pii-commit-overrides.jsonl"
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["reason"] == "fixture name"
    assert row["blocked"] == [{"file": "<commit message>", "tokens": ["Zorpcorp"]}]


def test_an_empty_denylist_refuses(repo):
    (repo / "tools" / ".pii-denylist.txt").write_text("")
    r = _commit(repo, "fix: anything")
    assert r.returncode != 0
    assert "empty" in r.stderr


def test_no_denylist_file_passes_and_says_so(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    r = _commit(repo, "fix: Zorpcorp case")
    assert r.returncode == 0, r.stderr
    assert "nothing to match against" in r.stderr


def test_no_verify_is_git_s_own_escape(repo):
    """Documents the boundary: --no-verify skips every client hook. The push gate remains."""
    assert _commit(repo, "fix: Zorpcorp case", args=("--no-verify",)).returncode == 0


# --- in-process: exact text handling and return codes ---

def test_comment_lines_are_dropped():
    assert guard.message_text("keep\n# Zorpcorp in a comment\nalso keep") == "keep\nalso keep"


def test_everything_below_the_scissors_line_is_dropped():
    raw = ("subject\n# " + guard.SCISSORS + "\n# Do not modify the line above.\n"
           "diff --git a/x b/x\n+Zorpcorp\n")
    assert guard.message_text(raw) == "subject"


def test_a_custom_comment_char_is_honoured():
    assert guard.message_text("keep\n; Zorpcorp\n# kept", ";") == "keep\n# kept"


def test_comment_char_reads_git_config(repo):
    assert guard.comment_char(repo) == "#"
    _git(repo, "config", "core.commentChar", ";")
    assert guard.comment_char(repo) == ";"
    _git(repo, "config", "core.commentChar", "auto")
    assert guard.comment_char(repo) == "#"


def test_judge_separates_block_and_warn(repo):
    got = guard.judge(repo, "Zorpcorp and Harbor and Quillfen")
    assert got == {"blocked": ["Zorpcorp"], "warned": ["Harbor", "Quillfen"]}


def test_main_returns_1_on_an_unreadable_message_file(repo, capsys):
    assert guard.main([str(repo / "missing.txt"), "--repo-root", str(repo)]) == 1
    assert "could not read the message" in capsys.readouterr().err


def test_main_returns_0_on_a_clean_message_and_prints_nothing(repo, tmp_path, capsys,
                                                              monkeypatch):
    monkeypatch.delenv("PII_COMMIT_OVERRIDE", raising=False)
    msg = tmp_path / "MSG"
    msg.write_text("fix: clean\n")
    assert guard.main([str(msg), "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().err == ""


def test_main_returns_1_and_names_the_token_and_the_override(repo, tmp_path, capsys,
                                                             monkeypatch):
    monkeypatch.delenv("PII_COMMIT_OVERRIDE", raising=False)
    msg = tmp_path / "MSG"
    msg.write_text("fix: Zorpcorp\n")
    assert guard.main([str(msg), "--repo-root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "Zorpcorp" in err and "PII_COMMIT_OVERRIDE" in err


def test_the_hook_is_tracked_executable_and_calls_the_guard():
    assert os.access(HOOK, os.X_OK)
    assert "commit_msg_pii_guard.py" in HOOK.read_text()


def test_main_returns_0_with_no_denylist_file(repo, tmp_path, capsys):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    msg = tmp_path / "MSG"
    msg.write_text("fix: Zorpcorp\n")
    assert guard.main([str(msg), "--repo-root", str(repo)]) == 0
    assert "nothing to match against" in capsys.readouterr().err


def test_main_override_returns_0_and_names_the_log(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PII_COMMIT_OVERRIDE", "fixture name")
    msg = tmp_path / "MSG"
    msg.write_text("fix: Zorpcorp\n")
    assert guard.main([str(msg), "--repo-root", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "OVERRIDDEN: fixture name" in err and "pii-commit-overrides.jsonl" in err
