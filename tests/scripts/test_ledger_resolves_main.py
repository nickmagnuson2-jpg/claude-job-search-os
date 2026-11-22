#!/usr/bin/env python3
"""Run from a linked worktree, the two ledger tools use the MAIN checkout's ledger.

WHY (2026-11-22, friction-log rows for audit_pii_record.py and codex_verify.py, 3 fires).
The push gate reads the main checkout's gitignored ledgers. From a worktree,
`audit_pii_record.py record` wrote the worktree's own copy, which the gate never sees, and
codex_verify's open-P0 line counted the worktree's copy of the review ledger, where the
dispositions finding_write writes to main never appear (it said 6 open P0s, the gate said 3).

What deliberately does NOT move: the library functions prepush_pii_guard imports
(read_rows, has_clean_record) keep reading the root they are given. land_branch's merge-push
preview runs the hook inside a throwaway linked worktree holding main's ledger PLUS backfill
rows not yet written to main; resolving main there would hide those rows.

Fixtures are invented; this file is a public artifact.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import audit_pii_record as rec  # noqa: E402
import codex_verify as cv  # noqa: E402

RECORD = ROOT / "tools" / "audit_pii_record.py"
AUDIT = Path("tools") / ".audit-pii-ledger.jsonl"
CROSS = Path("tools") / ".cross-model-ledger.jsonl"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com",
                           "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def main_and_worktree(tmp_path):
    main = tmp_path / "main"
    (main / "tools").mkdir(parents=True)
    (main / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    return main.resolve(), wt.resolve()


def _cli(root, *args):
    return subprocess.run([sys.executable, str(RECORD), "--repo-root", str(root), *args],
                          capture_output=True, text=True, cwd=str(root))


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _clean_row(tree):
    return {"kind": "audit", "verdict": "clean", "tree": tree, "tip": "x",
            "recorded": "2026-11-22T00:00:00+00:00"}


# ------------------------------------------------------------------ audit_pii_record CLI

def test_record_from_a_worktree_lands_in_the_main_ledger(main_and_worktree):
    main, wt = main_and_worktree
    tip = _git(wt, "rev-parse", "HEAD")
    r = _cli(wt, "record", "--tip", tip, "--files", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    got = _rows(main / AUDIT)
    assert [(g["kind"], g["tree"]) for g in got] == [("audit", _git(wt, "rev-parse", "HEAD^{tree}"))]
    assert not (wt / AUDIT).exists()
    assert json.loads(r.stdout)["ledger"] == str(main / AUDIT)


def test_check_from_a_worktree_sees_a_record_held_only_by_main(main_and_worktree):
    main, wt = main_and_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    (main / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    r = _cli(wt, "check", "--tip", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["recorded"] is True


def test_check_from_a_worktree_fails_a_record_held_only_by_the_worktree(main_and_worktree):
    """A clean record only in the worktree copy is exactly what the gate cannot see, so
    it does not pass (org 72 ruling, 2026-11-22); the message says how to fix it."""
    main, wt = main_and_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    (wt / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    r = _cli(wt, "check", "--tip", "HEAD")
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["recorded"] is False
    assert out["code"] == "recorded_only_in_worktree"
    assert "re-record from the main checkout" in out["message"]


def test_check_from_a_worktree_with_no_record_anywhere_fails(main_and_worktree):
    main, wt = main_and_worktree
    r = _cli(wt, "check", "--tip", "HEAD")
    assert r.returncode == 1 and json.loads(r.stdout)["recorded"] is False


def test_an_unreadable_main_ledger_is_an_error_not_no_record(main_and_worktree):
    main, wt = main_and_worktree
    (main / AUDIT).write_text("not json\n", encoding="utf-8")
    r = _cli(wt, "check", "--tip", "HEAD")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "ledger_unreadable"


def test_record_in_the_main_checkout_still_writes_main(main_and_worktree):
    main, _ = main_and_worktree
    assert _cli(main, "record", "--tip", "HEAD").returncode == 0
    assert len(_rows(main / AUDIT)) == 1


def test_the_library_read_the_push_guard_imports_stays_on_its_root(main_and_worktree):
    """land_branch.preview_gate runs the hook in a linked worktree whose ledger is main's
    plus backfill rows; the guard must read that copy, not the real main."""
    main, wt = main_and_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    (main / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    assert rec.has_clean_record(wt, tree) is False
    assert rec.has_clean_record(main, tree) is True


# ------------------------------------------------------------------ codex_verify preamble

def _review_row(disposition=None):
    f = {"id": "F1", "severity": "P0", "summary": "s", "location": "tools/x.py:1"}
    if disposition:
        f["disposition"] = disposition
    return {"recorded": "2026-11-22T00:00:00", "model": "codex", "report": "r.md",
            "paths": ["tools/x.py"], "findings": [f]}


def test_the_open_p0_line_from_a_worktree_counts_main_dispositions(main_and_worktree):
    """The worktree copy still shows the P0 open; main, where finding_write recorded the
    fix, shows it closed. The line must agree with the gate, which reads main."""
    main, wt = main_and_worktree
    (wt / CROSS).write_text(json.dumps(_review_row()) + "\n", encoding="utf-8")
    (main / CROSS).write_text(json.dumps(_review_row("fixed")) + "\n", encoding="utf-8")
    assert cv.open_p0_line(wt, ["tools/x.py"]).startswith("open P0s on these paths: 0;")


def test_the_open_p0_line_from_a_worktree_sees_a_p0_open_only_in_main(main_and_worktree):
    main, wt = main_and_worktree
    (main / CROSS).write_text(json.dumps(_review_row()) + "\n", encoding="utf-8")
    assert cv.open_p0_line(wt, ["tools/x.py"]).startswith("open P0s on these paths: 1;")


def test_the_open_p0_line_in_the_main_checkout_reads_main(main_and_worktree):
    main, _ = main_and_worktree
    (main / CROSS).write_text(json.dumps(_review_row()) + "\n", encoding="utf-8")
    assert cv.open_p0_line(main, ["tools/x.py"]).startswith("open P0s on these paths: 1;")


def test_a_worktree_whose_main_cannot_be_found_reports_unknown(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /nowhere/.git/worktrees/wt\n", encoding="utf-8")
    assert "unknown" in cv.open_p0_line(wt, ["tools/x.py"])
