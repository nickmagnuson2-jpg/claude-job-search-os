"""Tests for tools/precommit_pii_guard.py, run through the real tracked pre-commit hook.

Each test builds a throwaway repo with the hook installed and a fake denylist, then
commits. The tokens are invented (Zorpcorp, Harbor); none is a real entity.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
HOOK = TOOLS / "hooks" / "pre-commit"
COPIED = ("precommit_pii_guard.py", "check_public_pii.py", "shell_tokens.py", "hook_runtime.py")


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
    (root / "docs").mkdir()
    _git(root, "init", "-q", "-b", "main")
    for name in COPIED:
        (root / "tools" / name).write_bytes((TOOLS / name).read_bytes())
    (root / ".gitignore").write_text("tools/.pii-denylist*.txt\n")
    (root / "tools" / ".pii-denylist.txt").write_text("Zorpcorp\n")
    (root / "tools" / ".pii-denylist-ambiguous.txt").write_text("Harbor\n")
    (root / "docs" / "a.md").write_text("The Harbor line was here first.\nsecond line\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed", "--no-verify")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(HOOK.read_bytes())
    hook.chmod(0o755)
    return root


def _commit(repo, rel, text, env=None, args=()):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(repo, "add", "--", rel)
    return _git(repo, "commit", "-q", "-m", "x", *args, env=env, check=False)


def test_an_added_real_name_is_refused(repo):
    r = _commit(repo, "docs/b.md", "We met with Zorpcorp today.\n")
    assert r.returncode != 0
    assert "Zorpcorp" in r.stderr and "precommit_pii_guard" in r.stderr


def test_clean_added_text_commits(repo):
    assert _commit(repo, "docs/b.md", "We met with Acme today.\n").returncode == 0


def test_an_ordinary_word_on_an_untouched_line_does_not_block(repo):
    """The friction this design exists to avoid: old text needs no new verdict."""
    r = _commit(repo, "docs/a.md", "The Harbor line was here first.\nsecond line, edited\n")
    assert r.returncode == 0, r.stderr


def test_an_ordinary_word_on_an_added_line_needs_a_per_file_accept(repo):
    text = "The Harbor line was here first.\nsecond line\nWe met Harbor again.\n"
    r = _commit(repo, "docs/a.md", text)
    assert r.returncode != 0 and "docs/a.md:Harbor" in r.stderr
    wrong = _git(repo, "commit", "-q", "-m", "x", check=False,
                 env={"PII_COMMIT_ACCEPT": "docs/other.md:Harbor"})
    assert wrong.returncode != 0
    right = _git(repo, "commit", "-q", "-m", "x", check=False,
                 env={"PII_COMMIT_ACCEPT": "docs/a.md:Harbor"})
    assert right.returncode == 0, right.stderr


def test_the_pathspec_commit_form_is_still_checked(repo):
    """`git commit -- <paths>` runs the hook on a temporary index; it must see the change."""
    (repo / "docs" / "b.md").write_text("Zorpcorp\n")
    _git(repo, "add", "--", "docs/b.md")
    r = _git(repo, "commit", "-q", "-m", "x", "--", "docs/b.md", check=False)
    assert r.returncode != 0 and "Zorpcorp" in r.stderr


def test_the_index_is_read_not_the_working_tree(repo):
    """A name removed from the working tree after staging still ships, so it still blocks."""
    (repo / "docs" / "b.md").write_text("Zorpcorp\n")
    _git(repo, "add", "--", "docs/b.md")
    (repo / "docs" / "b.md").write_text("Acme\n")
    assert _git(repo, "commit", "-q", "-m", "x", check=False).returncode != 0


def test_deleting_a_file_that_names_someone_commits(repo):
    (repo / "docs" / "b.md").write_text("Zorpcorp\n")
    _git(repo, "add", "--", "docs/b.md")
    _git(repo, "commit", "-q", "-m", "seed2", "--no-verify")
    _git(repo, "rm", "-q", "--", "docs/b.md")
    assert _git(repo, "commit", "-q", "-m", "x", check=False).returncode == 0


def test_an_empty_denylist_refuses(repo):
    (repo / "tools" / ".pii-denylist.txt").write_text("")
    r = _commit(repo, "docs/b.md", "anything\n")
    assert r.returncode != 0 and "empty" in r.stderr


def test_no_denylist_file_passes_and_says_so(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    r = _commit(repo, "docs/b.md", "Zorpcorp\n")
    assert r.returncode == 0 and "gen_pii_denylist" in r.stderr


def test_an_override_commits_and_is_recorded(repo):
    r = _commit(repo, "docs/b.md", "Zorpcorp\n", env={"PII_COMMIT_OVERRIDE": "test reason"})
    assert r.returncode == 0, r.stderr
    log = repo / ".git" / "pii-commit-overrides.jsonl"
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["reason"] == "test reason"
    assert row["blocked"] == [{"file": "docs/b.md", "tokens": ["Zorpcorp"]}]


def test_a_force_added_ignored_path_is_judged(repo):
    """git stops treating a force-added path as ignored, so it ships and must be judged."""
    (repo / ".gitignore").write_text("tools/.pii-denylist*.txt\nprivate/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ig", "--no-verify")
    (repo / "private").mkdir()
    (repo / "private" / "x.md").write_text("Zorpcorp\n")
    _git(repo, "add", "-f", "--", "private/x.md")
    r = _git(repo, "commit", "-q", "-m", "x", check=False)
    # A force-added file is TRACKED, so git no longer treats it as ignored: it ships and
    # is judged. That is the safe direction.
    assert r.returncode != 0 and "private/x.md" in r.stderr


def test_an_ambiguous_word_that_opens_a_sentence_or_is_lowercase_only_warns(repo):
    (repo / "tools" / ".pii-denylist-ambiguous.txt").write_text("Harbor\nvoice\n")
    r = _commit(repo, "docs/b.md", "Harbor views are nice.\nThe voice is clear.\n")
    assert r.returncode == 0, r.stderr
    assert "docs/b.md:Harbor" in r.stderr and "docs/b.md:voice" in r.stderr
