"""todo_write.py rotate: close a recurring row and recreate it in ONE verified write.

Spec: output/analysis/090826-source-check-upstream-rotate-spec.md, section 5. The tests
that matter most:
  - a no-op writer must FAIL verification (37.F4: the old check accepted the
    pre-existing row, so it could never fail);
  - a Pending row stranded under ## Completed is never treated as live (37.F1).
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import todo_write as tw  # noqa: E402

TODOS = """\
# Job To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|---|---|---|---|---|
| Check ACME board for new job postings | High | 2026-09-05 | Pending | Recurring. |
| Unrelated task | Low | — | Pending | noise |

## Completed

| Task | Priority | Completed | Notes |
|---|---|---|---|
| Check ACME board for new job postings | High | 2026-09-02 | old instance |
"""

STRANDED = """\
# Job To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|---|---|---|---|---|
| Unrelated task | Low | — | Pending | noise |

## Completed

| Task | Priority | Completed | Notes |
|---|---|---|---|
| Check ACME board for new job postings | High | 2026-09-05 | Pending | stranded |
"""


def _run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "todo_write.py"), "--repo-root",
                        str(tmp_path), *args], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode


def _todos(tmp_path, text=TODOS):
    return write_fixture(tmp_path, "data/job-todos.md", text)


def _live(path, fragment="Check ACME"):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [c for _, c in tw.active_rows(lines) if fragment in c[0]]


# 1. Happy path against the real file -----------------------------------------------

def test_rotate_closes_and_recreates_in_one_write(tmp_path):
    p = _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME board", "--due", "2026-09-11",
                     "--closed-on", "2026-09-08", "--op-id", "acme:2026-09-08:abc123")
    assert code == 0 and out["status"] == "ok", out
    assert out["new_due"] == "2026-09-11" and out["closed_on"] == "2026-09-08"
    assert out["op_id"] == "acme:2026-09-08:abc123"
    live = _live(p)
    assert len(live) == 1
    assert live[0][1] == "High" and live[0][2] == "2026-09-11" and live[0][3] == "Pending"
    assert "acme:2026-09-08:abc123" in live[0][4] and "Recurring." in live[0][4]
    lines = p.read_text(encoding="utf-8").splitlines()
    assert tw.writer_section_counts(lines) == (2, 2)
    closed = [l for l in lines if "| 2026-09-08 |" in l]
    assert len(closed) == 1 and "rotated, acme:2026-09-08:abc123" in closed[0]


def test_rotate_takes_new_priority_and_notes(tmp_path):
    p = _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11",
                     "--priority", "Med", "--notes", "fresh notes")
    assert code == 0, out
    live = _live(p)[0]
    assert live[1] == "Med" and "fresh notes" in live[4] and "Recurring." not in live[4]


def test_rotate_generates_an_op_id_and_closes_today(tmp_path):
    from datetime import date
    p = _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11")
    assert code == 0, out
    today = date.today().isoformat()
    assert out["closed_on"] == today and out["op_id"].startswith(f"rotate:{today}:")
    assert out["op_id"] in _live(p)[0][4]


# 2. A no-op writer must FAIL --------------------------------------------------------

def test_a_writer_that_changes_nothing_fails_verification(tmp_path, monkeypatch, capsys):
    p = _todos(tmp_path)
    monkeypatch.setattr(tw, "save_lines", lambda *a, **k: None)  # reports nothing, writes nothing
    with pytest.raises(SystemExit) as e:
        tw.cmd_rotate(["Check ACME", "--due", "2026-09-11", "--closed-on", "2026-09-08"], p)
    assert e.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["reason"] == "rotate_unverified"
    assert p.read_text(encoding="utf-8") == TODOS


def _rotated(due="2026-09-11", marker="[op1] x", closed=True, extra_live=False):
    lines = TODOS.splitlines()
    out = list(lines)
    i = next(n for n, l in enumerate(out) if l.startswith("| Check ACME"))
    out[i] = f"| Check ACME board for new job postings | High | {due} | Pending | {marker} |"
    if extra_live:
        out.insert(i, "| Check ACME board for new job postings | High | 2026-09-11 | Pending | dup |")
    if closed:
        out.append("| Check ACME board for new job postings | High | 2026-09-08 | done |")
    return lines, out


def test_verify_rotation_names_a_wrong_due():
    lines, out = _rotated(due="2026-09-12")
    probs = tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert len(probs) == 1 and "Due" in probs[0]


def test_verify_rotation_accepts_a_real_rotation():
    lines, out = _rotated()
    assert tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                              "2026-09-11", "op1", "2026-09-08") == []


def test_verify_rotation_rejects_the_untouched_file():
    lines = TODOS.splitlines()
    probs = tw.verify_rotation(lines, list(lines), "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert any("Due" in p for p in probs)
    assert any("marker" in p for p in probs)
    assert any("Completed" in p for p in probs)
    assert any("section counts" in p for p in probs)


def test_verify_rotation_rejects_a_missing_marker():
    lines, out = _rotated(marker="no marker")
    probs = tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert len(probs) == 1 and "marker" in probs[0]


def test_verify_rotation_rejects_a_missing_close():
    lines, out = _rotated(closed=False)
    probs = tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert any("Completed did not gain" in p for p in probs)
    assert any("section counts" in p for p in probs)


def test_verify_rotation_rejects_two_live_rows():
    lines, out = _rotated(extra_live=True)
    probs = tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert any("exactly 1 live" in p for p in probs)


def test_verify_rotation_ignores_a_close_on_another_date():
    lines, out = _rotated()
    out[-1] = out[-1].replace("2026-09-08", "2026-09-07")
    probs = tw.verify_rotation(lines, out, "Check ACME board for new job postings",
                               "2026-09-11", "op1", "2026-09-08")
    assert any("Completed did not gain" in p for p in probs)


def test_an_unverified_rotation_restores_the_file(tmp_path, monkeypatch, capsys):
    p = _todos(tmp_path)
    monkeypatch.setattr(tw, "verify_rotation", lambda *a, **k: ["forced"])
    with pytest.raises(SystemExit):
        tw.cmd_rotate(["Check ACME", "--due", "2026-09-11"], p)
    assert p.read_text(encoding="utf-8") == TODOS
    assert json.loads(capsys.readouterr().out)["problems"] == ["forced"]


# 3-5. Matching ------------------------------------------------------------------------

def test_zero_matches_is_rejected_without_a_write(tmp_path):
    p = _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "No such task", "--due", "2026-09-11")
    assert code == 1 and out["reason"] == "no_live_row"
    assert p.read_text(encoding="utf-8") == TODOS


def test_two_matches_are_rejected_and_both_named(tmp_path):
    text = TODOS.replace("| Unrelated task | Low |", "| Check ACME careers page | Low |")
    p = _todos(tmp_path, text)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11")
    assert code == 1 and out["reason"] == "ambiguous"
    assert sorted(out["matches"]) == ["Check ACME board for new job postings",
                                      "Check ACME careers page"]
    assert p.read_text(encoding="utf-8") == text


def test_a_stranded_row_under_completed_is_not_live(tmp_path):
    p = _todos(tmp_path, STRANDED)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11")
    assert code == 1 and out["reason"] == "no_live_row"
    assert "sits under ## Completed" in out["message"]
    assert p.read_text(encoding="utf-8") == STRANDED


def test_active_rows_reads_only_the_active_section():
    rows = tw.active_rows(STRANDED.splitlines())
    assert [c[0] for _, c in rows] == ["Unrelated task"]
    assert tw.active_rows(["# no sections"]) == []


# 6. Compare-and-swap ------------------------------------------------------------------

def test_expect_due_mismatch_aborts_without_writing(tmp_path):
    p = _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11",
                     "--expect-due", "2026-09-04")
    assert code == 1 and out["reason"] == "expect_due_mismatch"
    assert p.read_text(encoding="utf-8") == TODOS


def test_expect_due_match_proceeds(tmp_path):
    _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11",
                     "--expect-due", "2026-09-05")
    assert code == 0, out


# 8. Crash between transform and write ------------------------------------------------

def test_a_crash_inside_the_write_leaves_the_file_unchanged(tmp_path, monkeypatch):
    p = _todos(tmp_path)

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        tw.cmd_rotate(["Check ACME", "--due", "2026-09-11"], p)
    assert p.read_text(encoding="utf-8") == TODOS


# Argument handling -------------------------------------------------------------------

@pytest.mark.parametrize("args,needle", [
    (["Check ACME"], "--due"),
    (["Check ACME", "--due", "9/11"], "YYYY-MM-DD"),
    (["Check ACME", "--due", "2026-09-11", "--expect-due", "soon"], "YYYY-MM-DD"),
    (["Check ACME", "--due", "2026-09-11", "--closed-on", "today"], "YYYY-MM-DD"),
    (["Check ACME", "--due", "2026-09-11", "--priority", "Urgent"], "High, Med or Low"),
    (["Check ACME", "--due", "2026-02-30"], "not a real calendar date"),
    (["Check ACME", "--due", "2026-09-11", "--closed-on", "2026-13-40"], "not a real calendar date"),
    (["Check ACME", "--due", "2026-09-11", "--expect-due", "2026-04-31"], "not a real calendar date"),
    (["Check ACME", "--due", "2026-09-11", "--op-id", "a|b"], "--op-id"),
    (["Check ACME", "--due", "2026-09-11", "--op-id", "a]b"], "--op-id"),
    (["--due", "2026-09-11"], "Usage"),
    (["Check ACME", "extra", "--due", "2026-09-11"], "one task fragment"),
    (["Check ACME", "--due"], "needs a value"),
])
def test_bad_arguments_are_refused_before_any_read(tmp_path, args, needle, capsys):
    p = _todos(tmp_path)
    with pytest.raises(SystemExit) as e:
        tw.cmd_rotate(args, p)
    assert e.value.code == 1
    assert needle in json.loads(capsys.readouterr().out)["message"]
    assert p.read_text(encoding="utf-8") == TODOS


def test_the_cli_accepts_rotate_flags(tmp_path):
    _todos(tmp_path)
    out, code = _run(tmp_path, "rotate", "Check ACME", "--due", "2026-09-11", "--bogus", "x")
    assert code == 1 and "Unknown flag" in out["message"]
