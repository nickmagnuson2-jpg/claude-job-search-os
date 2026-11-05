"""Tests for shipped_today in tools/todo_daily_metrics.py (what landed on main today)."""
import json
import os
import re
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
    assert out == {"available": True, "items": [], "ref": "main", "reason": None}


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
    _branch(repo, "feature-review", "2026-11-03T11:26:00", n=3)
    _branch(repo, "table-routing", "2026-11-03T12:11:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert out["available"] is True
    assert {(i["branch"], i["ahead"]) for i in out["items"]} == {
        ("feature-review", 3), ("table-routing", 1)}


def test_waiting_excludes_merged_and_counts_old(repo):
    _merge_pr(repo, 5, "done", "merged already", "2026-11-03T10:00:00")
    _branch(repo, "old-snapshot", "2026-10-20T10:00:00")
    _branch(repo, "fresh", "2026-11-02T10:00:00")
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert [i["branch"] for i in out["items"]] == ["fresh"]
    assert out["older_count"] == 1


def test_waiting_window_is_seven_calendar_dates(repo):
    """7 days ending 11-03 is 10-28..11-03; 10-27 is the eighth date and is older."""
    _branch(repo, "edge", "2026-10-28T10:00:00")
    _branch(repo, "past-edge", "2026-10-27T10:00:00")
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


def _commit(repo: Path, msg: str, when: str, author_when: str | None = None):
    name = msg.replace(" ", "-")
    (repo / f"{name}.txt").write_text(msg)
    _git(repo, "add", f"{name}.txt")
    env = {}
    if author_when:
        env["GIT_AUTHOR_DATE"] = author_when
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True,
                   capture_output=True, text=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com",
                        "GIT_AUTHOR_DATE": author_when or when, "GIT_COMMITTER_DATE": when})


def test_after_midnight_items_follow_the_evening(repo):
    """The logical day spans two dates; landing order, not the HH:MM string, decides."""
    _commit(repo, "evening", "2026-11-03T22:40:00")
    _commit(repo, "after midnight", "2026-11-04T01:10:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [i["time"] for i in out["items"]] == ["22:40", "01:10"]


def test_commit_at_0400_counts_once(repo):
    _commit(repo, "on the boundary", "2026-11-04T04:00:00")
    assert m.shipped_today(repo, date(2026, 11, 3))["items"] == []
    assert [i["title"] for i in m.shipped_today(repo, date(2026, 11, 4))["items"]] == [
        "on the boundary"]


def test_time_is_committer_date_not_author_date(repo):
    """A rebased direct push: authored the day before, landed today."""
    _commit(repo, "rebased", "2026-11-03T10:00:00", author_when="2026-11-02T18:00:00")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [i["time"] for i in out["items"]] == ["10:00"]


def test_time_is_rendered_in_local_zone(repo, monkeypatch):
    """A commit stored at +0000 shows in Nick's zone, not its own offset."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _commit(repo, "utc merge", "2026-11-03T20:00:00+0000")
    out = m.shipped_today(repo, date(2026, 11, 3))
    assert [i["time"] for i in out["items"]] == ["12:00"]


def test_undecodable_git_output_does_not_crash(repo):
    """2026-11-04 real run: git emitted a non-UTF-8 byte and killed the script.

    Ref names are raw bytes (commit messages are transcoded, and APFS refuses the loose
    ref file), so a latin-1 name written into packed-refs reaches the for-each-ref path.
    """
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side work", "2026-11-03T10:00:00")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "pack-refs", "--all")
    packed = repo / ".git" / "packed-refs"
    lines = packed.read_bytes().replace(b"refs/heads/side", b"refs/heads/caf\xe9").splitlines()
    header = [ln for ln in lines if ln.startswith(b"#")]
    refs = sorted((ln for ln in lines if not ln.startswith(b"#")), key=lambda ln: ln.split()[1])
    packed.write_bytes(b"\n".join(header + refs) + b"\n")  # packed-refs must stay sorted
    out = m.waiting_to_merge(repo, date(2026, 11, 3))
    assert out["available"] is True
    assert [i["branch"] for i in out["items"]] == ["caf\ufffd"]


def test_ahead_timeout_does_not_crash(monkeypatch, tmp_path):
    class R:
        returncode, stdout, stderr = 0, "slow\t2026-11-03\n", ""

    def fake(cmd, **k):
        if "rev-list" in cmd:
            raise subprocess.TimeoutExpired(cmd, 20)
        return R()
    monkeypatch.setattr(m.subprocess, "run", fake)
    out = m.waiting_to_merge(tmp_path, date(2026, 11, 3))
    assert out["available"] is True
    assert out["items"] == [{"branch": "slow", "last_commit": "2026-11-03", "ahead": None}]


@pytest.fixture
def remote_setup(repo, tmp_path):
    """repo tracks a bare 'GitHub'; a second clone merges a PR there and pushes."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    _branch(repo, "feat", "2026-11-03T09:00:00")
    _git(repo, "push", "-q", "origin", "feat")
    gh = tmp_path / "gh"
    subprocess.run(["git", "clone", "-q", str(bare), str(gh)], check=True)
    _git(gh, "merge", "-q", "--no-ff", "origin/feat", "-m",
         "Merge pull request #21 from someone/feat\n\nFeature", when="2026-11-03T11:00:00")
    _git(gh, "push", "-q", "origin", "main")
    return repo


def test_merge_on_remote_ships_before_local_pull(remote_setup):
    repo = remote_setup
    base = m.resolve_main(repo, fetch=True)
    assert (base["ref"], base["fresh"], base["fetch_error"]) == ("origin/main", True, None)
    shipped = m.shipped_today(repo, date(2026, 11, 3), ref=base["ref"])
    assert [i["pr"] for i in shipped["items"]] == [21]
    waiting = m.waiting_to_merge(repo, date(2026, 11, 3), ref=base["ref"])
    assert waiting["items"] == []
    # Control: stale local main still lists the merged branch, which is the bug fixed.
    assert [i["branch"] for i in m.waiting_to_merge(repo, date(2026, 11, 3))["items"]] == [
        "feat"]


def test_failed_fetch_keeps_last_fetched_origin_and_says_so(remote_setup, tmp_path):
    repo = remote_setup
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    base = m.resolve_main(repo, fetch=True)
    assert base["ref"] == "origin/main" and base["fresh"] is False
    assert base["fetch_error"] != "fetch skipped" and base["as_of"]


def test_no_remote_falls_back_to_local_main(repo):
    base = m.resolve_main(repo, fetch=True)
    assert base["ref"] == "main" and base["fresh"] is False and base["as_of"] is None


def test_default_does_not_fetch_and_reports_age(remote_setup):
    """No fetch by default: origin/main as last fetched, so the remote merge is unseen."""
    base = m.resolve_main(remote_setup)
    assert (base["ref"], base["fresh"], base["fetch_error"]) == (
        "origin/main", False, "fetch skipped")
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", base["as_of"])
    assert m.shipped_today(remote_setup, date(2026, 11, 3), ref=base["ref"])["items"] == []


def test_run_reports_the_ref_and_its_age(remote_setup, capsys):
    """End to end through _run: the freshness fields reach the JSON /checkout reads."""
    (remote_setup / "output").mkdir()
    m._run("", "", "", "", "", "", remote_setup / "output", remote_setup, date(2026, 11, 3))
    out = json.loads(capsys.readouterr().out)
    shipped, waiting = out["shipped_today"], out["waiting_to_merge"]
    assert (shipped["ref"], shipped["fresh"], shipped["fetch_error"]) == (
        "origin/main", False, "fetch skipped")
    assert shipped["as_of"] and waiting["ref"] == "origin/main"
