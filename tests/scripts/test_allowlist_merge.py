"""Invariants for tools/allowlist_merge.py, the key-wise three-way merge for
tools/mutation-allow.json.

The bug class: every branch that allowlists a survivor appends keys at the END of one
JSON object, so two branches with disjoint keys produce overlapping hunks and git
reports a conflict on every landing (2026-11-16: glob-rewrite x friction-capture, and
q11-shell-tokens x both). The keys never collided; the lines did.

The two failure modes the merge must never have: (1) a key silently dropped, which turns
an explained survivor back into an unexplained one; (2) two different reasons for one
key merged silently, which erases one session's argument without anyone seeing it.

Every test is written to FAIL if the behaviour breaks, not merely to exercise it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import allowlist_merge as am  # noqa: E402

K1 = "tools/foo.py::main::RETURN_NONE::0a1b2c3d"
K2 = "tools/bar.py::helper::IF_FALSE::9f8e7d6c"
K3 = "tools/baz.py::run::DROP_CALL::11112222"
K4 = "tools/qux.py::go::IF_TRUE::33334444"
SCRIPT = TOOLS_DIR / "allowlist_merge.py"


# --- the function API -------------------------------------------------------------

@pytest.mark.parametrize("base, ours, theirs, want", [
    # both sides append a different key: both kept (the 11-16 case)
    ({K1: "a"}, {K1: "a", K2: "b"}, {K1: "a", K3: "c"}, {K1: "a", K2: "b", K3: "c"}),
    # only one side changes anything
    ({K1: "a"}, {K1: "a", K2: "b"}, {K1: "a"}, {K1: "a", K2: "b"}),
    ({K1: "a"}, {K1: "a"}, {K1: "a", K3: "c"}, {K1: "a", K3: "c"}),
    # removed on one side, unchanged on the other: removed
    ({K1: "a", K2: "b"}, {K1: "a"}, {K1: "a", K2: "b"}, {K1: "a"}),
    ({K1: "a", K2: "b"}, {K1: "a", K2: "b"}, {K1: "a"}, {K1: "a"}),
    # removed on both sides
    ({K1: "a", K2: "b"}, {K1: "a"}, {K1: "a"}, {K1: "a"}),
    # one side rewrites a reason, the other leaves it: the rewrite wins
    ({K1: "a"}, {K1: "a2"}, {K1: "a"}, {K1: "a2"}),
    ({K1: "a"}, {K1: "a"}, {K1: "a3"}, {K1: "a3"}),
    # both sides make the identical change
    ({K1: "a"}, {K1: "same"}, {K1: "same"}, {K1: "same"}),
    ({}, {K2: "b"}, {K2: "b"}, {K2: "b"}),
    # empty base (file created on both sides)
    ({}, {K1: "a"}, {K2: "b"}, {K1: "a", K2: "b"}),
])
def test_merge_keeps_every_side_change(base, ours, theirs, want):
    got = am.merge(base, ours, theirs)
    assert got == want
    assert list(got) == list(want)  # order is part of the contract


@pytest.mark.parametrize("base, ours, theirs, key", [
    # the same new key with two reasons
    ({}, {K1: "mine"}, {K1: "yours"}, K1),
    # both sides rewrite one reason differently
    ({K1: "a"}, {K1: "mine"}, {K1: "yours"}, K1),
    # modify/delete in both directions: dropping it would erase the rewrite
    ({K1: "a"}, {}, {K1: "rewritten"}, K1),
    ({K1: "a"}, {K1: "rewritten"}, {}, K1),
])
def test_two_reasons_for_one_key_raise(base, ours, theirs, key):
    with pytest.raises(am.AllowlistConflict) as exc:
        am.merge(base, ours, theirs)
    assert [c[0] for c in exc.value.conflicts] == [key]


def test_every_conflict_is_reported_not_just_the_first():
    with pytest.raises(am.AllowlistConflict) as exc:
        am.merge({}, {K1: "x", K2: "x"}, {K1: "y", K2: "y"})
    assert sorted(c[0] for c in exc.value.conflicts) == sorted([K1, K2])


def test_ours_order_is_kept_and_theirs_additions_follow_in_theirs_order():
    """The file is append-ordered, not sorted (20 order breaks at f58251d6). A merge
    that re-sorted would rewrite hundreds of lines and make the merge commit
    unreviewable, so ours keeps its order and theirs' new keys go after it."""
    base = {K2: "b", K1: "a"}
    ours = {K2: "b", K1: "a", K4: "d"}
    theirs = {K2: "b", K1: "a", K3: "c", K4: "d"}
    assert list(am.merge(base, ours, theirs)) == [K2, K1, K4, K3]


def test_a_rewrite_from_theirs_keeps_the_key_in_its_ours_position():
    base = {K1: "a", K2: "b"}
    assert list(am.merge(base, base, {K1: "a", K2: "b2"}).items()) == [(K1, "a"), (K2, "b2")]


@pytest.mark.parametrize("ours", [{K1: ""}, {K1: "   "}, {K1: None}, {K1: 3}])
def test_an_empty_or_non_string_reason_never_reaches_the_output(ours):
    with pytest.raises(ValueError):
        am.merge({}, ours, {})


def test_a_non_object_side_is_refused():
    with pytest.raises(ValueError):
        am.merge({}, [K1], {})


def test_a_conflict_is_a_value_error_for_callers_that_catch_broadly():
    assert issubclass(am.AllowlistConflict, ValueError)


# --- the CLI / git merge driver ---------------------------------------------------

def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture()
def sides(tmp_path: Path):
    return (_write(tmp_path / "O", {K1: "a"}),
            _write(tmp_path / "A", {K1: "a", K2: "b — non-ascii kept"}),
            _write(tmp_path / "B", {K1: "a", K3: "c"}))


def test_driver_form_writes_the_merge_into_ours(sides):
    """git runs `driver %O %A %B` and reads the result back from %A."""
    o, a, b = sides
    r = _run(o, a, b)
    assert r.returncode == 0, r.stderr
    text = a.read_text(encoding="utf-8")
    assert json.loads(text) == {K1: "a", K2: "b — non-ascii kept", K3: "c"}
    # same on-disk format as the file today: indent 2, raw UTF-8, trailing newline
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"


def test_dash_o_writes_elsewhere_and_leaves_ours_alone(sides, tmp_path):
    o, a, b = sides
    before = a.read_text(encoding="utf-8")
    out = tmp_path / "out.json"
    r = _run(o, a, b, "-o", out)
    assert r.returncode == 0, r.stderr
    assert a.read_text(encoding="utf-8") == before
    assert set(json.loads(out.read_text(encoding="utf-8"))) == {K1, K2, K3}


def test_a_conflict_exits_1_prints_both_reasons_and_writes_nothing(tmp_path):
    o = _write(tmp_path / "O", {})
    a = _write(tmp_path / "A", {K1: "reason from ours"})
    b = _write(tmp_path / "B", {K1: "reason from theirs"})
    before = a.read_text(encoding="utf-8")
    r = _run(o, a, b)
    assert r.returncode == 1
    assert K1 in r.stderr
    assert "reason from ours" in r.stderr and "reason from theirs" in r.stderr
    assert a.read_text(encoding="utf-8") == before


def test_a_duplicate_key_in_an_input_is_refused(tmp_path):
    """json.load keeps the last of two duplicate keys and says nothing, which is a
    silent drop of the first reason."""
    o = _write(tmp_path / "O", {})
    a = tmp_path / "A"
    a.write_text('{"%s": "one", "%s": "two"}\n' % (K1, K1), encoding="utf-8")
    b = _write(tmp_path / "B", {})
    r = _run(o, a, b)
    assert r.returncode == 2
    assert "duplicate" in r.stderr.lower()


def test_unparseable_input_exits_2(tmp_path):
    o = _write(tmp_path / "O", {})
    a = tmp_path / "A"
    a.write_text("{not json", encoding="utf-8")
    b = _write(tmp_path / "B", {})
    assert _run(o, a, b).returncode == 2


def test_an_empty_base_file_means_no_common_ancestor(tmp_path):
    """git passes an empty %O when both sides created the file independently."""
    o = tmp_path / "O"
    o.write_text("", encoding="utf-8")
    a = _write(tmp_path / "A", {K1: "a"})
    b = _write(tmp_path / "B", {K2: "b"})
    r = _run(o, a, b)
    assert r.returncode == 0, r.stderr
    assert set(json.loads(a.read_text(encoding="utf-8"))) == {K1, K2}


@pytest.mark.parametrize("which", ["A", "B"])
def test_an_empty_ours_or_theirs_is_refused_not_read_as_no_keys(tmp_path, which):
    """Reading an empty side as {} would remove every key it still holds."""
    o = _write(tmp_path / "O", {K1: "a"})
    a = _write(tmp_path / "A", {K1: "a", K2: "b"})
    b = _write(tmp_path / "B", {K1: "a"})
    (tmp_path / which).write_text("", encoding="utf-8")
    assert _run(o, a, b).returncode == 2


def test_it_works_as_a_registered_git_merge_driver(tmp_path):
    """End to end in a throwaway repo: two branches append different keys, git's line
    merge would conflict, and with the driver registered the merge completes."""
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True,
                              text=True, encoding="utf-8")
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    allow = tmp_path / "allow.json"
    _write(allow, {K1: "a"})
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "side")
    _write(allow, {K1: "a", K3: "c"})
    git("commit", "-qam", "side")
    git("checkout", "-q", "main")
    _write(allow, {K1: "a", K2: "b"})
    git("commit", "-qam", "main")
    (tmp_path / ".git" / "info" / "attributes").write_text("allow.json merge=allowlist\n")
    git("config", "merge.allowlist.driver",
        f"{sys.executable} {SCRIPT} %O %A %B")
    r = git("merge", "-q", "--no-edit", "side")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(allow.read_text(encoding="utf-8")) == {K1: "a", K2: "b", K3: "c"}
