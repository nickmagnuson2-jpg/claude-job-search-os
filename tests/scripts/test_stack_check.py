"""Tests for tools/stack_check.py: refuse a branch stacked on another unlanded branch whose
current tip it does not contain.

Origin (codify #3, 2 fires on 2026-11-22): B2 and B7 were built on B1 commits that B1 then
moved past. B7's suite on 856a7442 ran against the stale base and failed. A replay of the
rule over that night's reflogs refuses 856a7442 at suite start, naming learning-state-1122
with 5 commits missing.

Every test builds a throwaway repo under tmp_path. Branch names are generic placeholders.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import stack_check as sc  # noqa: E402

TOOL = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "stack_check.py")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


class Repo:
    def __init__(self, path):
        os.makedirs(path, exist_ok=True)
        self.path = str(path)
        self.n = 0
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.logAllRefUpdates", "true")
        self.commit("m0")

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        r = subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def commit(self, name):
        self.n += 1
        with open(os.path.join(self.path, f"{name}.txt"), "w") as f:
            f.write(f"{name} {self.n}\n")
        self.git("add", f"{name}.txt")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def branch(self, name, start="HEAD"):
        self.git("checkout", "-q", "-b", name, start)

    def checkout(self, name):
        self.git("checkout", "-q", name)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "r")


def _parents(refusals):
    return sorted(r.parent for r in refusals)


# --- the recorded fire: built on a parent commit the parent then moved past ----------
def test_refuses_branch_built_on_a_moved_parent(repo):
    repo.branch("parent")
    p1 = repo.commit("p1")
    repo.branch("child")            # child starts from parent's p1
    repo.commit("c1")
    repo.checkout("parent")
    p2 = repo.commit("p2")          # parent moves on; child never picks it up
    out = sc.check(repo.path, "child")
    assert _parents(out) == ["parent"]
    r = out[0]
    assert r.built_on == p1
    assert r.parent_tip == p2
    assert r.missing == [p2]


def test_allows_branch_that_contains_the_parent_tip(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    assert sc.check(repo.path, "child") == []


def test_allows_after_rebasing_onto_the_new_parent_tip(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    repo.commit("p2")
    repo.checkout("child")
    repo.git("rebase", "-q", "parent")
    assert sc.check(repo.path, "child") == []


def test_allows_when_the_parent_has_landed(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    repo.commit("p2")
    repo.checkout("main")
    repo.git("merge", "-q", "--no-ff", "-m", "land parent", "parent")
    # parent's tip is now in main: landing child merges against main, which has p2.
    assert sc.check(repo.path, "child", main="main") == []


def test_siblings_from_main_do_not_refuse_each_other(repo):
    repo.branch("a")
    repo.commit("a1")
    repo.checkout("main")
    repo.branch("b")
    repo.commit("b1")
    assert sc.check(repo.path, "a") == []
    assert sc.check(repo.path, "b") == []


def test_a_branch_stacked_on_us_is_not_our_parent(repo):
    repo.branch("base")
    repo.commit("b1")
    repo.branch("top")              # top is stacked on base, not the other way round
    repo.commit("t1")
    assert sc.check(repo.path, "base") == []


def test_a_branch_with_no_commits_of_its_own_is_never_a_parent(repo):
    repo.branch("work")
    repo.commit("w1")
    repo.git("branch", "backup/work")   # reflog says only "Created from"
    repo.commit("w2")
    assert sc.check(repo.path, "work") == []


def test_built_on_names_the_newest_parent_commit_the_branch_has(repo):
    repo.branch("parent")
    repo.commit("p1")
    p2 = repo.commit("p2")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    p3 = repo.commit("p3")
    out = sc.check(repo.path, "child")
    assert out[0].built_on == p2
    assert out[0].missing == [p3]


def test_a_rewritten_parent_still_refuses(repo):
    repo.branch("parent")
    p1 = repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    repo.git("commit", "-q", "--amend", "-m", "p1 amended")
    new_tip = repo.git("rev-parse", "HEAD")
    out = sc.check(repo.path, "child")
    assert _parents(out) == ["parent"]
    assert out[0].built_on == p1
    assert out[0].missing == [new_tip]


def test_accepts_a_sha_as_the_tip(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    c1 = repo.commit("c1")
    repo.checkout("parent")
    repo.commit("p2")
    assert _parents(sc.check(repo.path, c1)) == ["parent"]


def test_the_branch_itself_is_never_its_own_parent(repo):
    repo.branch("solo")
    repo.commit("s1")
    repo.commit("s2")
    repo.git("reset", "-q", "--hard", "HEAD~1")   # its old tip s2 is now outside it
    assert sc.check(repo.path, "solo") == []


# --- message and CLI -------------------------------------------------------------------
def test_message_names_parent_and_missing_commits(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    p2 = repo.commit("p2")
    msg = sc.format_refusals("child", sc.check(repo.path, "child"))
    assert "parent" in msg and p2[:8] in msg and "1 commit" in msg


def _cli(repo, *args):
    return subprocess.run([sys.executable, TOOL, "--repo", repo.path, *args],
                          capture_output=True, text=True, env={**os.environ, **GIT_ENV})


def test_cli_exit_1_and_json_on_refusal(repo):
    repo.branch("parent")
    repo.commit("p1")
    repo.branch("child")
    repo.commit("c1")
    repo.checkout("parent")
    repo.commit("p2")
    r = _cli(repo, "child", "--json")
    assert r.returncode == 1, r.stderr
    d = json.loads(r.stdout)
    assert d["refused"] is True and d["refusals"][0]["parent"] == "parent"


def test_cli_exit_0_when_clean(repo):
    repo.branch("a")
    repo.commit("a1")
    r = _cli(repo, "a")
    assert r.returncode == 0, r.stderr


def test_cli_exit_2_on_an_unknown_tip(repo):
    r = _cli(repo, "no-such-branch")
    assert r.returncode == 2
