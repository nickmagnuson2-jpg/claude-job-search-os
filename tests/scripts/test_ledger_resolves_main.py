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
import shlex
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
    assert json.loads(r.stdout)["ledger"] == str(main / AUDIT)


def test_record_from_a_worktree_also_keeps_the_worktree_copy(main_and_worktree):
    """Grok r1 F1: a push run FROM the worktree, and land_branch step 5's dry run, read
    the worktree's copy (the hook's root is --show-toplevel). Writing main only made a
    fresh record pass step 6 and fail step 5."""
    main, wt = main_and_worktree
    tip = _git(wt, "rev-parse", "HEAD")
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    assert _cli(wt, "record", "--tip", tip).returncode == 0
    assert rec.has_clean_record(wt, tree) and rec.has_clean_record(main, tree)
    assert _rows(wt / AUDIT) == _rows(main / AUDIT)


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
    # The message names a runnable command, with the commit SHA resolved (HEAD in the
    # main checkout is a different commit), and running it clears the check.
    sha = _git(wt, "rev-parse", "HEAD")
    want = f"--repo-root {main} record --tip {sha}"
    assert want in out["message"]
    assert _cli(main, "record", "--tip", sha).returncode == 0
    assert _cli(wt, "check", "--tip", "HEAD").returncode == 0


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


def test_the_push_guard_read_from_a_worktree_sees_a_record_held_only_by_main(main_and_worktree):
    """S2d (2026-11-25), replacing the stays-on-its-root contract: the guard run in a
    linked worktree reads that worktree's copy AND main's (git's common dir), so a record
    made from main covers a worktree push with nothing copied. land_branch.preview_gate's
    throwaway worktree gets main's rows the same way plus its own backfill rows."""
    main, wt = main_and_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    (main / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    assert rec.has_clean_record(wt, tree) is True
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


# ------------------------------------------------------------------ Codex r1 F1, F2

@pytest.fixture
def separate_gitdir_worktree(tmp_path):
    """--separate-git-dir: the worktree's gitdir has no `.git` component, so the
    file-read resolver cannot find main (Codex r1 F1)."""
    main = tmp_path / "main"
    gd = tmp_path / "gitdir"
    subprocess.run(["git", "init", "-q", "-b", "main", f"--separate-git-dir={gd}", str(main)],
                   check=True)
    (main / "tools").mkdir()
    (main / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    return main.resolve(), wt.resolve()


@pytest.mark.parametrize("cmd", ["record", "check"])
def test_a_worktree_whose_main_cannot_be_resolved_refuses_loudly(separate_gitdir_worktree, cmd):
    main, wt = separate_gitdir_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    if cmd == "check":     # a worktree-only record must not pass by default
        (wt / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    before = (wt / AUDIT).read_text(encoding="utf-8") if (wt / AUDIT).exists() else None
    r = _cli(wt, cmd, "--tip", "HEAD")
    out = json.loads(r.stdout)
    assert r.returncode == 2 and out["code"] == "main_checkout_unresolved", r.stdout
    after = (wt / AUDIT).read_text(encoding="utf-8") if (wt / AUDIT).exists() else None
    assert after == before and not (main / AUDIT).exists()


def test_the_fix_command_is_runnable_when_paths_hold_spaces_and_metacharacters(tmp_path):
    """Codex r1 F2: the paths were interpolated unquoted."""
    base = tmp_path / "a dir; $(x)"
    main = base / "main"
    (main / "tools").mkdir(parents=True)
    (main / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = base / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    (wt / AUDIT).write_text(json.dumps(_clean_row(tree)) + "\n", encoding="utf-8")
    msg = json.loads(_cli(wt, "check", "--tip", "HEAD").stdout)["message"]
    fix = msg.split("re-record from the main checkout: ", 1)[1]
    # The command names main's own copy of the tool; point it at this branch's copy,
    # which the fixture's main checkout does not hold.
    fix = fix.replace(shlex.quote(str(main.resolve() / "tools" / "audit_pii_record.py")),
                      shlex.quote(str(RECORD)))
    r = subprocess.run(fix, shell=True, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _cli(wt, "check", "--tip", "HEAD").returncode == 0


def test_an_unresolvable_worktree_with_spaces_in_its_git_dir_still_refuses(tmp_path):
    """Codex r2 F1: git's two paths were split on whitespace, so a git dir with a space
    read as 'not a linked worktree' and the worktree's copy was used."""
    main = tmp_path / "main"
    gd = tmp_path / "git dir"
    subprocess.run(["git", "init", "-q", "-b", "main", f"--separate-git-dir={gd}", str(main)],
                   check=True)
    (main / "x.txt").write_text("x\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    r = _cli(wt, "record", "--tip", "HEAD")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "main_checkout_unresolved"
    assert not (wt / AUDIT).exists()


def test_a_separate_git_dir_holder_is_not_taken_for_the_main_checkout(tmp_path):
    """Grok r1 F2: with --separate-git-dir=<holder>/.git the file-read resolver named
    <holder>, which holds no checkout, and the record landed in <holder>/tools."""
    main = tmp_path / "main"
    holder = tmp_path / "holder"
    holder.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", f"--separate-git-dir={holder / '.git'}",
                    str(main)], check=True)
    (main / "x.txt").write_text("x\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    r = _cli(wt, "record", "--tip", "HEAD")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "main_checkout_unresolved"
    assert not (holder / AUDIT).exists() and not (wt / AUDIT).exists()


# ------------------------------------------- read side replaces the reach (S2d, 2026-11-25)
# L1 (2026-11-24) was fixed on the write side by 045ca9bc: a record made from main was also
# appended to every branch worktree at that tree. The guard now reads main's copy itself,
# so the copy is gone; these pin the read side and that nothing is copied any more.

def test_record_from_main_writes_main_only_and_the_worktree_push_sees_it(main_and_worktree):
    main, wt = main_and_worktree
    tree = _git(wt, "rev-parse", "HEAD^{tree}")
    r = _cli(main, "record", "--tip", _git(wt, "rev-parse", "HEAD"), "--files", "3")
    assert r.returncode == 0, r.stderr
    assert "worktrees" not in json.loads(r.stdout)
    assert not (wt / AUDIT).exists()                      # nothing copied into the worktree
    assert rec.has_clean_record(wt, tree) is True          # the guard's call, from the worktree


def test_a_record_for_another_tree_does_not_cover(main_and_worktree):
    main, wt = main_and_worktree
    (main / AUDIT).write_text(json.dumps(_clean_row("0" * 40)) + "\n", encoding="utf-8")
    assert rec.has_clean_record(wt, _git(wt, "rev-parse", "HEAD^{tree}")) is False


def test_an_unreadable_main_ledger_fails_closed_from_a_worktree(main_and_worktree):
    main, wt = main_and_worktree
    (main / AUDIT).parent.mkdir(parents=True, exist_ok=True)
    (main / AUDIT).write_text("not json\n", encoding="utf-8")
    with pytest.raises(rec.LedgerError):
        rec.has_clean_record(wt, _git(wt, "rev-parse", "HEAD^{tree}"))


def test_an_unplaceable_main_fails_closed_from_a_worktree(main_and_worktree, monkeypatch):
    import cross_model_gate as gate
    main, wt = main_and_worktree

    def boom(root):
        raise RuntimeError("git did not name a .git common dir")
    monkeypatch.setattr(gate, "main_checkout_of", boom)
    with pytest.raises(rec.LedgerError):
        rec.has_clean_record(wt, _git(wt, "rev-parse", "HEAD^{tree}"))
