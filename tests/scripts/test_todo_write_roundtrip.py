"""Every write must be verified by the CONSUMER, not by the producer.

The failure this closes, 2026-08-17: a recovery note written into a todo's Notes
cell contained the literal string "## Completed". `todo_write.py` was fine -- it
finds sections by walking lines, so its own `audit`/`done`/`withdraw` all reported
success. `todo_daily_metrics.py` found sections with an unanchored regex, so it
matched the phantom header inside the row and saw a 2-row Completed section instead
of 744. `completed_today` reported 0 while five rows literally read
"Completed 2026-08-17".

The bug was invisible to every check the writer had, because the writer's checks and
the writer's bug lived on the same side of the boundary. That is the general class:

    verifying a mutation with the tool that made it proves nothing about the tool
    that reads it.

Same shape as two other failures the same day: a green fixture suite while `withdraw`
corrupted real rows, and a `sync` matcher "fixed" while 18 rows it had already damaged
sat unswept.

So `save_lines` -- the single choke point all 11 mutation paths funnel through -- now
reads the file back with the ACTUAL downstream parser and asserts the reader sees what
the writer wrote. On divergence it ROLLS BACK and errors, so a mutation that would
corrupt the reader's view never lands.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_script, TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import todo_write as tw  # noqa: E402

FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Alpha task | High | 2026-08-01 | Pending | note one |
| Beta task | Med | — | Pending | note two |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Old thing | Low | 2026-08-01 | Completed 2026-08-01 |
"""


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _run_raw(tmp_path, *args):
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


# --------------------------------------------------------------------------
# the counting primitives
# --------------------------------------------------------------------------

def test_writer_counts_match_reader_counts_on_clean_file(tmp_path):
    p = _write(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert tw.writer_section_counts(lines) == tw.reader_section_counts(p) == (2, 1)


def test_reader_counts_survive_a_phantom_header_in_a_cell(tmp_path):
    """The exact 2026-08-17 payload. Regression-locks the parser fix from the other side."""
    poisoned = FIXTURE.replace("| note two |", "| misfiled into ## Completed by the bad sync |")
    p = _write(tmp_path, poisoned)
    assert tw.reader_section_counts(p) == (2, 1)


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

def test_guard_rolls_back_when_reader_disagrees(tmp_path, monkeypatch):
    """Divergence must abort the write and restore the original bytes."""
    p = _write(tmp_path)
    original = p.read_text(encoding="utf-8")
    lines = original.splitlines()

    monkeypatch.setattr(tw, "reader_section_counts", lambda _p: (99, 99))
    with pytest.raises(SystemExit):
        tw.save_lines(p, lines, original)

    assert p.read_text(encoding="utf-8") == original, "file was left corrupted"


def test_guard_reports_both_views_on_divergence(tmp_path, monkeypatch, capsys):
    """The error has to name writer-vs-reader, or the next person debugs blind."""
    p = _write(tmp_path)
    original = p.read_text(encoding="utf-8")
    monkeypatch.setattr(tw, "reader_section_counts", lambda _p: (99, 99))
    with pytest.raises(SystemExit):
        tw.save_lines(p, original.splitlines(), original)
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert out.get("reason") == "roundtrip_mismatch"
    assert out.get("writer_counts") == [2, 1]
    assert out.get("reader_counts") == [99, 99]


def test_guard_is_silent_on_a_normal_write(tmp_path):
    """A passing invariant must not add noise to ordinary output."""
    _write(tmp_path)
    res = run_script("todo_write.py", "add", "Gamma task", "High", "2026-09-01", "n",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert "roundtrip" not in json.dumps(res)


# --------------------------------------------------------------------------
# every mutation path still works end to end
# --------------------------------------------------------------------------

@pytest.mark.parametrize("args,expect", [
    (("add", "New one", "Med", "—", "x"), "added"),
    (("done", "Alpha task"), "done"),
    (("withdraw", "Beta task"), "withdrawn"),
    (("update", "Alpha task", "--notes-append", " more"), "updated"),
])
def test_mutations_pass_the_guard(tmp_path, args, expect):
    _write(tmp_path)
    code, res = _run_raw(tmp_path, *args)
    assert res["status"] == "ok", f"{args} -> {res}"


def test_guard_catches_a_real_section_destroying_write(tmp_path):
    """End-to-end: a write that genuinely destroys a section must not land.

    Simulated by making the writer's own view disagree -- the point is that SOME
    check now stands between a corrupting mutation and the file, which is exactly
    what was missing on 2026-08-17.
    """
    p = _write(tmp_path)
    original = p.read_text(encoding="utf-8")
    broken = [l for l in original.splitlines() if not l.startswith("## Completed")]
    with pytest.raises(SystemExit):
        tw.save_lines(p, broken, original)
    assert p.read_text(encoding="utf-8") == original
