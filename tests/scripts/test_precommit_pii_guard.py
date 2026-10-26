"""Tests for tools/precommit_pii_guard.py, run through the real tracked pre-commit hook.

Each test builds a throwaway repo with the hook installed and a fake denylist, then
commits. The tokens are invented (Zorpcorp, Harbor); none is a real entity.
"""
import json
import os
import subprocess
import sys
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
    assert "Harbor" not in r.stderr


def test_an_ambiguous_word_on_an_added_line_warns_and_commits(repo):
    """The ambiguous tier warns: blocking it refused 12 ordinary words for no catch the
    denylist did not already make (400-commit replay, 2026-10-25)."""
    r = _commit(repo, "docs/a.md", "The Harbor line was here first.\nsecond line\nWe met Harbor again.\n")
    assert r.returncode == 0, r.stderr
    assert "docs/a.md:Harbor" in r.stderr


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


def _seed_private(repo, rel, text):
    """A tracked file outside the public tree, committed without the hook."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(repo, "add", "-f", "--", rel)
    _git(repo, "commit", "-q", "-m", "seed private", "--no-verify")


def test_an_unchanged_rename_from_a_private_path_is_judged(repo):
    """644.F1/648.F1/650.F1: a 100% rename adds no lines, so the guard read nothing."""
    _seed_private(repo, "data/x.md", "We met with Zorpcorp today.\n")
    _git(repo, "mv", "data/x.md", "docs/x.md")
    r = _git(repo, "commit", "-q", "-m", "x", check=False)
    assert r.returncode != 0 and "docs/x.md" in r.stderr and "Zorpcorp" in r.stderr


def test_a_copy_from_a_private_path_is_judged(repo):
    _seed_private(repo, "data/x.md", "We met with Zorpcorp today.\nmore text here\n")
    (repo / "docs" / "x.md").write_text("We met with Zorpcorp today.\nmore text here\n")
    _git(repo, "add", "--", "docs/x.md")
    r = _git(repo, "commit", "-q", "-m", "x", check=False)
    assert r.returncode != 0 and "Zorpcorp" in r.stderr


def test_a_rename_between_public_paths_is_read_whole(repo):
    """The pathspec in added_text hides a rename's source, so git shows the whole file
    as added. That is what catches the private-path rename above; this pins it."""
    (repo / "docs" / "old.md").write_text("We met with Zorpcorp today.\nline two\n")
    _git(repo, "add", "--", "docs/old.md")
    _git(repo, "commit", "-q", "-m", "seed", "--no-verify")
    _git(repo, "mv", "docs/old.md", "docs/new.md")
    r = _git(repo, "commit", "-q", "-m", "x", check=False)
    assert r.returncode != 0 and "docs/new.md" in r.stderr


def test_a_target_word_used_as_a_name_warns_and_commits(repo):
    """Blocking target words at commit refused 4 commits in the replay, none a leak."""
    (repo / "tools" / ".pii-denylist-targetwords.txt").write_text("Lantern\n")
    r = _commit(repo, "docs/b.md", "We spoke to Lantern about the role.\n")
    assert r.returncode == 0, r.stderr
    assert "docs/b.md:Lantern" in r.stderr


# --- in process: return codes, messages and error paths the hook run cannot tell apart

sys.path.insert(0, str(TOOLS))
import precommit_pii_guard as guard  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("GIT_") or k.startswith("PII_"):
            monkeypatch.delenv(k, raising=False)


def _stage(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(repo, "add", "-f", "--", rel)


def test_main_returns_0_on_clean_staged_text_and_prints_nothing(repo, clean_env, capsys):
    _stage(repo, "docs/b.md", "Acme only.\n")
    assert guard.main(["--repo-root", str(repo)]) == 0
    assert capsys.readouterr().err == ""


def test_main_returns_1_and_names_the_token_and_the_override(repo, clean_env, capsys):
    _stage(repo, "docs/b.md", "Zorpcorp\n")
    assert guard.main(["--repo-root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "docs/b.md: real name(s) Zorpcorp" in err and "PII_COMMIT_OVERRIDE" in err


def test_main_returns_0_with_no_denylist_file(repo, clean_env, capsys):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    _stage(repo, "docs/b.md", "Zorpcorp\n")
    assert guard.main(["--repo-root", str(repo)]) == 0


def test_an_override_returns_0_and_names_the_log(repo, clean_env, capsys, monkeypatch):
    monkeypatch.setenv("PII_COMMIT_OVERRIDE", "why")
    _stage(repo, "docs/b.md", "Zorpcorp\n")
    assert guard.main(["--repo-root", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "OVERRIDDEN: why" in err and "pii-commit-overrides.jsonl" in err


def test_a_git_failure_listing_the_index_refuses(repo, clean_env, capsys, monkeypatch):
    def broken(_root):
        raise RuntimeError("git diff: fatal")
    monkeypatch.setattr(guard, "staged_paths", broken)
    assert guard.main(["--repo-root", str(repo)]) == 1
    assert "could not list the staged paths" in capsys.readouterr().err


def test_a_staged_change_git_cannot_show_refuses_and_says_so(repo, clean_env, capsys, monkeypatch):
    _stage(repo, "docs/b.md", "Acme\n")
    def broken(_root, _rel):
        raise RuntimeError("git diff: bad object")
    monkeypatch.setattr(guard, "added_text", broken)
    assert guard.main(["--repo-root", str(repo)]) == 1
    assert "could not read the staged change, so it was not checked: docs/b.md" in capsys.readouterr().err


def test_git_errors_raise_rather_than_return_empty_text(repo):
    with pytest.raises(RuntimeError):
        guard._git(repo, "show", "no-such-ref-xyz")


def test_a_private_path_is_not_judged(repo, clean_env):
    """data/ is never public, even force-added; only public files are judged."""
    _stage(repo, "data/notes.md", "Zorpcorp\n")
    assert guard.main(["--repo-root", str(repo)]) == 0
