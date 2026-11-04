"""Tests for shipped_today in tools/todo_daily_metrics.py (what landed on main today)."""
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

import todo_daily_metrics as m


def _git(repo: Path, *args, when: str | None = None):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"})
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "seed", when="2026-11-02T10:00:00")
    return r


def _merge_pr(repo: Path, n: int, branch: str, title: str, when: str):
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / f"{branch}.txt").write_text(branch)
    _git(repo, "add", f"{branch}.txt")
    _git(repo, "commit", "-q", "-m", "work on branch", when=when)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", branch, "-m",
         f"Merge pull request #{n} from someone/{branch}\n\n{title}", when=when)


def test_merged_pr_reports_number_branch_and_title(repo):
    _merge_pr(repo, 14, "mutation-cases", "Mutation cases", "2026-11-03T13:19:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert out["available"] is True
    assert [(i["pr"], i["branch"], i["title"]) for i in out["items"]] == [
        (14, "mutation-cases", "Mutation cases")]


def test_branch_commits_are_not_listed_separately(repo):
    """--first-parent: the PR shows once, not once per branch commit."""
    _merge_pr(repo, 10, "review-jail", "fix: one rule per parent", "2026-11-03T11:14:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert len(out["items"]) == 1


def test_direct_commit_to_main_is_listed_without_pr(repo):
    (repo / "b.txt").write_text("b")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "refactor: direct push", when="2026-11-03T06:48:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [(i["pr"], i["title"]) for i in out["items"]] == [(None, "refactor: direct push")]


def test_window_is_the_logical_day(repo):
    """Before 04:00 belongs to the previous day; 03:30 on 11/4 counts for 11/3."""
    _merge_pr(repo, 1, "early", "too early", "2026-11-03T03:30:00")
    _merge_pr(repo, 2, "late-night", "after midnight", "2026-11-04T03:30:00")
    _merge_pr(repo, 3, "next-day", "next day", "2026-11-04T05:00:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [i["pr"] for i in out["items"]] == [2]


def test_items_sorted_by_time(repo):
    _merge_pr(repo, 2, "second", "second", "2026-11-03T13:00:00")
    (repo / "c.txt").write_text("c")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "direct", when="2026-11-03T14:00:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [i["time"] for i in out["items"]] == ["13:00", "14:00"]


def test_unreadable_git_is_unavailable_not_empty(tmp_path):
    """A directory that is not a repo must not read as 'nothing shipped'."""
    out = m.shipped_today(tmp_path, date(2026, 11, 3))
    assert out["available"] is False
    assert out["reason"]
    assert out["items"] == []


def test_quiet_day_is_available_and_empty(repo):
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert out == {"available": True, "items": [], "reason": None}


def test_parse_merge_without_body_falls_back_to_subject():
    raw = "abc12345\x1f10:00\x1fMerge pull request #7 from someone/x\x1f\x1e"
    items = m.parse_shipped_log(raw)
    assert items[0]["pr"] == 7
    assert items[0]["title"] == "Merge pull request #7 from someone/x"


def test_parse_skips_blank_and_truncated_records():
    raw = "\x1e\nabc12345\x1f10:00\x1e" + "def67890\x1f11:00\x1fdirect\x1f\x1e"
    items = m.parse_shipped_log(raw)
    assert [(i["sha"], i["title"]) for i in items] == [("def67890", "direct")]


def test_git_missing_is_unavailable(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(m.subprocess, "run", boom)
    out = m.shipped_today(tmp_path, date(2026, 11, 3))
    assert out["available"] is False
    assert "git not runnable" in out["reason"]


def _branch(repo: Path, name: str, when: str, n: int = 1):
    _git(repo, "checkout", "-q", "-b", name)
    for k in range(n):
        (repo / f"{name}-{k}.txt").write_text(name)
        _git(repo, "add", f"{name}-{k}.txt")
        _git(repo, "commit", "-q", "-m", f"{name} {k}", when=when)
    _git(repo, "checkout", "-q", "main")


def test_waiting_lists_recent_unmerged_branches_with_ahead_count(repo):
    _branch(repo, "eic-review", "2026-11-03T11:26:00", n=3)
    _branch(repo, "table-routing", "2026-11-03T12:11:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert out["available"] is True
    assert {(i["branch"], i["ahead"]) for i in out["items"]} == {
        ("eic-review", 3), ("table-routing", 1)}


def test_waiting_excludes_merged_and_counts_old(repo):
    _merge_pr(repo, 5, "done", "merged already", "2026-11-03T10:00:00")
    _branch(repo, "old-snapshot", "2026-10-20T10:00:00")
    _branch(repo, "fresh", "2026-11-02T10:00:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert [i["branch"] for i in out["items"]] == ["fresh"]
    assert out["older_count"] == 1


def test_waiting_window_edge_is_inclusive(repo):
    _branch(repo, "edge", "2026-10-27T10:00:00")
    _branch(repo, "past-edge", "2026-10-26T10:00:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert [i["branch"] for i in out["items"]] == ["edge"]
    assert out["older_count"] == 1


def test_waiting_sorted_newest_first(repo):
    _branch(repo, "a-older", "2026-11-01T10:00:00")
    _branch(repo, "b-newer", "2026-11-03T10:00:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert [i["branch"] for i in out["items"]] == ["b-newer", "a-older"]


def test_waiting_unreadable_git_is_unavailable(tmp_path):
    out = m.waiting_to_merge(tmp_path, date(2026, 11, 3))
    assert out["available"] is False and out["reason"]


def test_waiting_git_missing_is_unavailable(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(m.subprocess, "run", boom)
    out = m.waiting_to_merge(tmp_path, date(2026, 11, 3))
    assert out["available"] is False and "git not runnable" in out["reason"]


def test_waiting_skips_a_line_with_no_readable_date(monkeypatch, tmp_path):
    class R:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, ""
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        if "for-each-ref" in cmd:
            return R("weird\tnot-a-date\ngood\t2026-11-03\n")
        return R("2\n")
    monkeypatch.setattr(m.subprocess, "run", fake)
    out = m.waiting_to_merge(tmp_path, date(2026, 11, 3))
    assert [(i["branch"], i["ahead"]) for i in out["items"]] == [("good", 2)]
    assert out["older_count"] == 0
