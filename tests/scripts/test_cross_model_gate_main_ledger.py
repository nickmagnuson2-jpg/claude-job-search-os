"""The push gate, run from a linked worktree, also reads the main checkout's ledger (S2d).

Origin (overnight 2026-11-25): S5's Codex and Grok rows at tree 734f1e8f were written
from a review worktree. They reached that worktree's ledger and main's mirror, never the
branch worktree whose pre-push gate reads its own copy, so land_branch refused and both
reviews were repeated. Org ruling (b7): read side. main's ledger is the copy every review
is mirrored into and finding_write keeps in step; on the same run the worktree's row wins;
main's ledger missing or unreadable fails closed with a stated reason.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from tools import cross_model_gate as g  # noqa: E402

PATH = "tools/career_scanner/scanner.py"
CHANGE = [(PATH, 200, 40)]


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def repos(tmp_path):
    main, wt = tmp_path / "main", tmp_path / "wt"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base")
    _git(main, "worktree", "add", "-q", "-b", "branch", str(wt))
    return main, wt


def _row(root, **kw):
    row = {"recorded": "2026-11-25T10:00:00+00:00", "model": "codex",
           "report": "output/analysis/112526-codex-x.md", "paths": [PATH],
           "findings": [], "waived": False, "verified": True}
    row.update(kw)
    p = g.ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _open_p0():
    return [{"id": "F1", "severity": "P0", "location": f"{PATH}:10",
             "summary": "a real defect", "disposition": None}]


def test_a_row_only_in_main_covers_the_worktree_push(repos):
    main, wt = repos
    _row(wt, report="output/analysis/unrelated.md", paths=["docs/x.md"])   # wt ledger exists
    assert g.check(wt, CHANGE, since=0).blocked is True                    # red before main's row
    _row(main, worktree="/elsewhere/review-wt")
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is False, v.message


def test_an_open_p0_only_in_main_on_the_pushed_paths_blocks(repos):
    main, wt = repos
    _row(wt)                                                               # covers on its own
    _row(main, report="output/analysis/unrelated.md", paths=["docs/x.md"])  # main ledger exists
    assert g.check(wt, CHANGE, since=0).blocked is False
    _row(main, report="output/analysis/112526-grok-x.md", model="grok", findings=_open_p0())
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_the_worktree_copy_wins_over_main_on_the_same_run(repos):
    main, wt = repos
    fixed = _open_p0()
    fixed[0]["disposition"] = "fixed"
    _row(wt, findings=fixed)                                    # dispositioned here
    _row(main, findings=_open_p0(), worktree=str(wt))           # stale mirror: still open
    rows, corrupt, note = g.read_gate_ledger(wt)
    same = [r for r in rows if r["report"] == "output/analysis/112526-codex-x.md"]
    assert len(same) == 1 and same[0]["findings"][0]["disposition"] == "fixed"
    assert corrupt == 0 and note == ""
    assert g.check(wt, CHANGE, since=0).blocked is False


def test_main_ledger_missing_fails_closed_with_a_reason(repos):
    main, wt = repos
    _row(wt)
    rows, corrupt, note = g.read_gate_ledger(wt)
    assert corrupt == 1 and "missing" in note
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is True and "main checkout's ledger is missing" in v.message


def test_main_ledger_unreadable_fails_closed(repos):
    main, wt = repos
    _row(wt)
    p = g.ledger_path(main)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe not json\n")
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is True
    rows, corrupt, note = g.read_gate_ledger(wt)
    assert corrupt >= 1 and note


def test_main_located_from_git_common_dir_not_a_fixed_path(repos):
    main, wt = repos
    assert g.main_checkout_of(wt) == main.resolve()
    assert g.main_checkout_of(main) is None                # the main checkout reads only its own


def test_outside_a_linked_worktree_nothing_changes(tmp_path):
    _row(tmp_path)
    rows, corrupt, note = g.read_gate_ledger(tmp_path)
    assert len(rows) == 1 and corrupt == 0 and note == ""
