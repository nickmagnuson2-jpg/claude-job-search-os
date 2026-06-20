"""Tests for the `withdraw` subcommand of tools/todo_write.py.

Withdrawing a todo marks it Withdrawn (not Done) so it never counts as a completion.
Two paths:
  - a row in ## Active is moved to ## Completed marked "Withdrawn <today>"
  - a row already in ## Completed (mis-marked "Completed <date>") is corrected in place
"""
import json
import os
import subprocess
import sys

from conftest import run_script, TOOLS_DIR

FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Ship the thing | High | — | Pending | keep the note |
| Apply to Acme | Med | 2026-06-10 | Pending | warm intro |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Old umbrella task | High | 2026-06-08 | Completed 2026-06-08 |
"""


def _write(tmp_path, content=FIXTURE):
    p = tmp_path / "data" / "job-todos.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _run_raw(tmp_path, *args):
    """Run todo_write.py without check=True so error JSON can be inspected."""
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), *args,
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, json.loads(r.stdout)


def test_withdraw_active_row_moves_to_completed_as_withdrawn(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "withdraw", "Ship the thing",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    assert res["action"] == "withdrawn"

    text = p.read_text(encoding="utf-8")
    active, completed = text.split("## Completed")
    assert "Ship the thing" not in active            # left Active
    row = next(l for l in completed.splitlines() if "Ship the thing" in l)
    assert "Withdrawn" in row                         # marked withdrawn
    assert "Completed 2026" not in row                # NOT a completion
    assert "keep the note" in row                     # original note preserved


def test_withdraw_corrects_a_miscompleted_row(tmp_path):
    p = _write(tmp_path)
    res = run_script("todo_write.py", "withdraw", "Old umbrella task",
                     "--repo-root", str(tmp_path))
    assert res["status"] == "ok"
    text = p.read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if "Old umbrella task" in l)
    assert "Withdrawn" in row
    assert "Completed 2026-06-08" not in row          # marker flipped, no longer a completion


def test_withdraw_no_match_errors(tmp_path):
    _write(tmp_path)
    code, out = _run_raw(tmp_path, "withdraw", "no such task zzz")
    assert code != 0
    assert out["status"] == "error"


def test_withdrawn_row_not_counted_by_metrics(tmp_path):
    """End-to-end: a row withdrawn 'today' does not appear in todo_daily_metrics completions."""
    _write(tmp_path)
    run_script("todo_write.py", "withdraw", "Old umbrella task",
               "--repo-root", str(tmp_path))
    today = "2026-06-08"
    metrics = run_script("todo_daily_metrics.py", "--target-date", today,
                         "--repo-root", str(tmp_path))
    assert "Old umbrella task" not in [c["task"] for c in metrics["completed_today"]]


# ---------------------------------------------------------------------------
# Tests: supersede subcommand (dedupe a contact's prior auto follow-up)
# ---------------------------------------------------------------------------

SUPERSEDE_FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Follow up: Jane Doe — await reply | Med | 2026-06-25 | Pending | old |
| Follow up: Jane Doe — newer await | Med | 2026-06-26 | Pending | old2 |
| Follow up: Jane Doe (Acme) — manual curated | Med | 2026-06-27 | Pending | keep |
| Unrelated task | Low | — | Pending | keep |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
"""


def test_supersede_withdraws_all_prefix_matches(tmp_path):
    p = _write(tmp_path, SUPERSEDE_FIXTURE)
    code, res = _run_raw(tmp_path, "supersede", "Follow up: Jane Doe —")
    assert code == 0
    assert res["action"] == "supersede"
    assert res["superseded"] == 2

    active, completed = p.read_text(encoding="utf-8").split("## Completed")
    # Both auto rows left Active and are now Withdrawn
    assert "await reply" not in active
    assert "newer await" not in active
    assert completed.count("Withdrawn") == 2
    # The manually-curated "(Acme)" variant and the unrelated task are untouched
    assert "Jane Doe (Acme) — manual curated" in active
    assert "Unrelated task" in active


def test_supersede_no_match_is_noop(tmp_path):
    p = _write(tmp_path, SUPERSEDE_FIXTURE)
    before = p.read_text(encoding="utf-8")
    code, res = _run_raw(tmp_path, "supersede", "Follow up: Nobody —")
    assert code == 0
    assert res["superseded"] == 0
    assert p.read_text(encoding="utf-8") == before  # file untouched
