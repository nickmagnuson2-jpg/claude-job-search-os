"""Tests for tools/check_changelog_currency.py — the changelog-drift Stop hook.

Detection logic is exercised against real temporary git repos (the behavior is
inherently git-shaped, so a temp-repo fixture is the honest test, not mocks).
Origin: [[feedback_changelog_drifts_without_structural_update]] REOPEN gate.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import check_changelog_currency as cc  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _commit(repo, relpath, content, msg):
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _commit(tmp_path, "docs/CHANGELOG.md", "# Changelog\n", "docs: seed changelog")
    return tmp_path


def test_clean_when_changelog_is_latest(repo):
    assert cc.unlogged_worthy_commits(repo) == []


def test_warns_when_tool_commit_after_changelog(repo):
    _commit(repo, "tools/foo.py", "print(1)\n", "feat: add foo tool")
    unlogged = cc.unlogged_worthy_commits(repo)
    assert len(unlogged) == 1
    assert "add foo tool" in unlogged[0][1]


def test_clear_after_changelog_updated(repo):
    _commit(repo, "tools/foo.py", "print(1)\n", "feat: add foo tool")
    assert len(cc.unlogged_worthy_commits(repo)) == 1
    _commit(repo, "docs/CHANGELOG.md", "# Changelog\n\n## 2026-06-05\n- foo\n",
            "docs: log foo")
    assert cc.unlogged_worthy_commits(repo) == []


def test_non_worthy_commit_does_not_warn(repo):
    _commit(repo, "data/note.md", "personal note\n", "data: a note")
    assert cc.unlogged_worthy_commits(repo) == []


def test_skill_and_framework_and_settings_are_worthy(repo):
    _commit(repo, ".claude/skills/x/SKILL.md", "skill\n", "feat: skill x")
    _commit(repo, "framework/y.md", "fw\n", "docs: framework y")
    _commit(repo, ".claude/settings.json", "{}\n", "chore: settings")
    assert len(cc.unlogged_worthy_commits(repo)) == 3


def test_combined_code_and_changelog_commit_clears(repo):
    # Doing it right — code + changelog in ONE commit — leaves nothing un-logged.
    p_tool = repo / "tools" / "bar.py"
    p_tool.parent.mkdir(parents=True, exist_ok=True)
    p_tool.write_text("print(2)\n", encoding="utf-8")
    (repo / "docs" / "CHANGELOG.md").write_text("# Changelog\n\n## x\n- bar\n",
                                                encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: bar + changelog")
    assert cc.unlogged_worthy_commits(repo) == []


def test_last_changelog_commit_found(repo):
    assert cc.last_changelog_commit(repo) != ""
