"""tools/merge_chain.py: the one rule for "a push of merge commits that adds nothing of its
own". Real temp git repos; no stubs. Imported by cross_model_gate, prepush_pii_guard and
land_branch, so every case here is a case all three agree on."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import allowlist_write  # noqa: E402
import merge_chain as mc  # noqa: E402

ALLOW = mc.ALLOWLIST


def sh(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q", "-b", "main")
    sh(root, "config", "user.email", "t@example.com")
    sh(root, "config", "user.name", "T")
    (root / "tools").mkdir()
    (root / "a.txt").write_text("a\n")
    (root / "b.txt").write_text("b\n")
    (root / ALLOW).write_text(dump({"k::base": "base reason"}))
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "base")
    return root


def commit_on(root, start, name, files: dict, deletes=()) -> str:
    """A commit on a new branch `name` from `start` writing `files`; returns its sha."""
    sh(root, "checkout", "-q", "-b", name, start)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for rel in deletes:
        sh(root, "rm", "-q", rel)
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "--allow-empty", "-m", name)
    tip = sh(root, "rev-parse", "HEAD")
    sh(root, "checkout", "-q", "main")
    return tip


def head(root):
    return sh(root, "rev-parse", "HEAD")


def tree(root, sha):
    return sh(root, "rev-parse", f"{sha}^{{tree}}")


def merge_commit(root, p1, p2, tree_sha, msg="m"):
    return sh(root, "commit-tree", tree_sha, "-p", p1, "-p", p2, "-m", msg)


# ---------------------------------------------------------------- serialization
def test_serialize_is_what_allowlist_write_dumps(tmp_path):
    data = {"z::b": "r2", "a::b": "r1 é"}
    (tmp_path / "tools").mkdir()
    allowlist_write._dump(tmp_path, data)
    assert (tmp_path / "tools" / "mutation-allow.json").read_text(encoding="utf-8") \
        == allowlist_write.serialize(data) == dump(data)


# ---------------------------------------------------------------- expected_tree
def test_clean_merge_expected_tree_is_git_merge_tree(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    t2 = commit_on(repo, base, "y", {"b.txt": "b2\n"})
    got, why = mc.expected_tree(repo, t1, t2)
    want = sh(repo, "merge-tree", "--write-tree", t1, t2)
    assert (got, why) == (want, "clean")


def test_conflict_on_another_file_has_no_expected_tree(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "one\n"})
    t2 = commit_on(repo, base, "y", {"a.txt": "two\n"})
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert "a.txt" in why


def test_allowlist_conflict_resolves_to_sorted_key_merge(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"}),
                                     "b.txt": "b2\n"})
    # git's own line merge conflicts: both sides append after the same last key
    rc = subprocess.run(["git", "merge-tree", "--write-tree", t1, t2], cwd=repo,
                        capture_output=True).returncode
    assert rc == 1
    got, why = mc.expected_tree(repo, t1, t2)
    assert got and why == "allowlist resolved"
    blob = sh(repo, "show", f"{got}:{ALLOW}") + "\n"
    assert blob == dump({"k::base": "base reason", "k::x": "rx", "k::y": "ry"})
    assert sh(repo, "show", f"{got}:b.txt") == "b2"


def test_allowlist_key_conflict_has_no_expected_tree(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "changed by x"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "changed by y"})})
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert "k::base" in why


def test_allowlist_plus_other_conflict_has_no_expected_tree(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"}),
                                     "a.txt": "one\n"})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"}),
                                     "a.txt": "two\n"})
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None and "a.txt" in why


def test_allowlist_deleted_on_one_side_has_no_expected_tree(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {}, deletes=[ALLOW])
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert ALLOW in why


def test_bad_ref_has_no_expected_tree(repo):
    got, why = mc.expected_tree(repo, head(repo), "ab" * 20)
    assert got is None and why and why != "clean"


# ---------------------------------------------------------------- walk
def chain_of_two(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    t2 = commit_on(repo, base, "y", {"b.txt": "b2\n", "c.txt": "new\n"})
    m1 = mc.build(repo, base, t1, "merge x")
    m2 = mc.build(repo, m1, t2, "merge y")
    return base, t1, t2, m1, m2


def test_walk_returns_each_merge_with_parents_in_order(repo):
    base, t1, t2, m1, m2 = chain_of_two(repo)
    got = mc.walk(repo, base, m2)
    assert [(m.sha, m.p1, m.p2) for m in got] == [(m1, base, t1), (m2, m1, t2)]
    assert [m.tree for m in got] == [tree(repo, m1), tree(repo, m2)]
    assert [m.resolved_allowlist for m in got] == [False, False]


def test_build_writes_expected_tree_parents_and_message(repo):
    base, t1, _t2, m1, _m2 = chain_of_two(repo)
    assert sh(repo, "rev-list", "--parents", "-n1", m1).split() == [m1, base, t1]
    assert tree(repo, m1) == sh(repo, "merge-tree", "--write-tree", base, t1)
    assert sh(repo, "log", "-1", "--format=%B", m1) == "merge x"


def test_build_refuses_a_real_conflict(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "one\n"})
    t2 = commit_on(repo, base, "y", {"a.txt": "two\n"})
    with pytest.raises(mc.ChainError, match="a.txt"):
        mc.build(repo, t1, t2, "m")


def test_walk_blocks_a_merge_that_adds_content(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    sneak = commit_on(repo, t1, "sneak", {"evil.txt": "added inside the merge\n"})
    bad = merge_commit(repo, base, t1, tree(repo, sneak))
    with pytest.raises(mc.ChainError, match="not git's clean merge"):
        mc.walk(repo, base, bad)


def test_walk_blocks_a_non_merge_commit(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    with pytest.raises(mc.ChainError, match="2 parents"):
        mc.walk(repo, base, t1)


def test_walk_blocks_an_octopus(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    t2 = commit_on(repo, base, "y", {"b.txt": "b2\n"})
    octo = sh(repo, "commit-tree", tree(repo, t1), "-p", base, "-p", t1, "-p", t2, "-m", "o")
    with pytest.raises(mc.ChainError, match="2 parents"):
        mc.walk(repo, base, octo)


def test_walk_blocks_a_first_parent_that_skips_base(repo):
    base = head(repo)
    other = commit_on(repo, base, "o", {"a.txt": "o\n"})
    t1 = commit_on(repo, base, "x", {"b.txt": "b2\n"})
    m = mc.build(repo, other, t1, "m")       # first parent is `other`, not base
    with pytest.raises(mc.ChainError):
        mc.walk(repo, base, m)


def test_walk_blocks_a_chain_built_on_a_stale_base(repo):
    """main moved after the chain was built: the merge is clean and 2-parent, and its first
    parent is an ancestor of the pushed base, so only the first-parent check catches it."""
    old = head(repo)
    t1 = commit_on(repo, old, "x", {"a.txt": "a2\n"})
    m = mc.build(repo, old, t1, "m")
    moved = commit_on(repo, old, "moved", {"b.txt": "moved\n"})
    with pytest.raises(mc.ChainError, match="first parent"):
        mc.walk(repo, moved, m)


def test_walk_blocks_merge_whose_first_parent_is_the_branch(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    swapped = mc.build(repo, t1, base, "swapped")   # branch first, main second
    with pytest.raises(mc.ChainError):
        mc.walk(repo, base, swapped)


def test_walk_blocks_empty_range_and_non_descendant(repo):
    base = head(repo)
    with pytest.raises(mc.ChainError, match="no commits"):
        mc.walk(repo, base, base)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    with pytest.raises(mc.ChainError):
        mc.walk(repo, t1, base)


def test_walk_blocks_unknown_shas(repo):
    with pytest.raises(mc.ChainError, match="not a commit here: " + "ab" * 20):
        mc.walk(repo, "ab" * 20, head(repo))
    with pytest.raises(mc.ChainError, match="not a commit here: " + "cd" * 20):
        mc.walk(repo, head(repo), "cd" * 20)


def test_walk_accepts_correct_allowlist_resolution(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    m1 = mc.build(repo, base, t1, "x")
    m2 = mc.build(repo, m1, t2, "y")
    got = mc.walk(repo, base, m2)
    assert [m.resolved_allowlist for m in got] == [False, True]


@pytest.mark.parametrize("bad", [
    {"k::base": "base reason", "k::x": "rx"},                      # dropped theirs' key
    {"k::base": "base reason", "k::x": "rx", "k::y": "ry", "k::z": "smuggled"},
])
def test_walk_blocks_wrong_allowlist_resolution(repo, bad):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    m1 = mc.build(repo, base, t1, "x")
    wrong = commit_on(repo, m1, "wrong", {ALLOW: dump(bad)})
    m2 = merge_commit(repo, m1, t2, tree(repo, wrong))
    with pytest.raises(mc.ChainError, match="not git's clean merge"):
        mc.walk(repo, base, m2)


def test_walk_blocks_unsorted_allowlist_resolution(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    m1 = mc.build(repo, base, t1, "x")
    same_keys_other_order = json.dumps({"k::y": "ry", "k::base": "base reason", "k::x": "rx"},
                                       indent=2) + "\n"
    wrong = commit_on(repo, m1, "wrong", {ALLOW: same_keys_other_order})
    m2 = merge_commit(repo, m1, t2, tree(repo, wrong))
    with pytest.raises(mc.ChainError):
        mc.walk(repo, base, m2)


# ---------------------------------------------------------------- numstat
def test_numstat_lists_paths_and_counts(repo):
    base, _t1, _t2, m1, m2 = chain_of_two(repo)
    assert mc.numstat(repo, m1, m2) == [("b.txt", 1, 1), ("c.txt", 1, 0)]
    assert mc.numstat(repo, base, m1) == [("a.txt", 1, 1)]


def test_numstat_reports_a_rename_as_two_paths(repo):
    base = head(repo)
    sh(repo, "checkout", "-q", "-b", "r", base)
    sh(repo, "mv", "a.txt", "renamed.txt")
    sh(repo, "commit", "-qm", "r")
    tip = head(repo)
    sh(repo, "checkout", "-q", "main")
    assert sorted(p for p, _a, _r in mc.numstat(repo, base, tip)) == ["a.txt", "renamed.txt"]


def test_numstat_raises_on_git_error(repo):
    with pytest.raises(mc.ChainError):
        mc.numstat(repo, head(repo), "ab" * 20)


def test_parse_numstat_binary_counts_as_zero():
    assert mc.parse_numstat("-\t-\timg.png\n3\t1\tx.py\n\nbad line\n") == \
        [("img.png", 0, 0), ("x.py", 3, 1)]


# ---------------------------------------------------------------- allowlist edge cases
def test_allowlist_key_deleted_on_one_side_stays_deleted(repo):
    """The merge base is read: a key x removed is not brought back by y's side."""
    base = commit_on(repo, head(repo), "b2", {ALLOW: dump({"k::base": "r", "k::old": "o"})})
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "r", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "r", "k::old": "o",
                                                  "k::y": "ry"})})
    got, why = mc.expected_tree(repo, t1, t2)
    assert why == "allowlist resolved"
    assert sh(repo, "show", f"{got}:{ALLOW}") + "\n" == \
        dump({"k::base": "r", "k::x": "rx", "k::y": "ry"})


def test_allowlist_added_on_both_sides_merges_from_an_empty_base(repo):
    base = commit_on(repo, head(repo), "nofile", {}, deletes=[ALLOW])
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::y": "ry"})})
    got, why = mc.expected_tree(repo, t1, t2)
    assert why == "allowlist resolved"
    assert sh(repo, "show", f"{got}:{ALLOW}") + "\n" == dump({"k::x": "rx", "k::y": "ry"})


@pytest.mark.parametrize("ours", ["", "{not json\n"])
def test_allowlist_unreadable_side_has_no_expected_tree(repo, ours):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: ours})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert "unreadable side" in why


def test_allowlist_with_two_merge_bases_is_refused(repo):
    """A criss-cross history has two merge bases; the key-wise merge needs exactly one."""
    base = head(repo)
    a1 = commit_on(repo, base, "a1", {"a.txt": "a1\n"})
    b1 = commit_on(repo, base, "b1", {"b.txt": "b1\n"})
    a2 = mc.build(repo, a1, b1, "a2")
    b2 = mc.build(repo, b1, a1, "b2")
    a3 = commit_on(repo, a2, "a3", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    b3 = commit_on(repo, b2, "b3", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    assert len(sh(repo, "merge-base", "--all", a3, b3).split()) == 2
    got, why = mc.expected_tree(repo, a3, b3)
    assert got is None
    assert "2 merge bases" in why


def allowlist_conflict(repo):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {ALLOW: dump({"k::base": "base reason", "k::x": "rx"})})
    t2 = commit_on(repo, base, "y", {ALLOW: dump({"k::base": "base reason", "k::y": "ry"})})
    return base, t1, t2


def failing_git(monkeypatch, subcommand, stdout="", rc=1):
    real = mc._git

    def fake(root, *args, **kw):
        if subcommand in args:
            return subprocess.CompletedProcess(args, rc, stdout, "forced failure")
        return real(root, *args, **kw)
    monkeypatch.setattr(mc, "_git", fake)


@pytest.mark.parametrize("step", ["ls-tree", "hash-object", "read-tree", "update-index",
                                  "write-tree"])
def test_allowlist_plumbing_failure_has_no_expected_tree(repo, monkeypatch, step):
    _base, t1, t2 = allowlist_conflict(repo)
    failing_git(monkeypatch, step)
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert f"{step} failed" in why and "forced failure" in why


def test_a_failed_merge_base_is_an_error_not_an_empty_base(repo, monkeypatch):
    """rc 1 with no output means unrelated histories; rc 128 is git failing (grok 2026-11-19)."""
    _base, t1, t2 = allowlist_conflict(repo)
    failing_git(monkeypatch, "merge-base", rc=128)
    got, why = mc.expected_tree(repo, t1, t2)
    assert got is None
    assert "merge-base failed" in why and "forced failure" in why


def test_build_raises_when_commit_tree_fails(repo, monkeypatch):
    base = head(repo)
    t1 = commit_on(repo, base, "x", {"a.txt": "a2\n"})
    failing_git(monkeypatch, "commit-tree")
    with pytest.raises(mc.ChainError, match="commit-tree failed"):
        mc.build(repo, base, t1, "m")


def test_walk_raises_when_rev_list_fails(repo, monkeypatch):
    base, t1, _t2, m1, _m2 = chain_of_two(repo)
    failing_git(monkeypatch, "rev-list")
    with pytest.raises(mc.ChainError, match="rev-list failed"):
        mc.walk(repo, base, m1)
