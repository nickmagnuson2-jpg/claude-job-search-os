"""Tests for the `sync` subcommand of tools/todo_write.py.

`sync` auto-withdraws Active todos whose company has reached a terminal stage in
the pipeline's ## Archived section.

Regression origin (2026-08-08): a single stale Archived row mass-withdrew 18
todos, 3 of them unrelated. Two independent defects produced that blast radius:

  1. The company name was substring-matched against ALL columns joined together,
     so a todo that merely *mentioned* the company in its Notes was withdrawn.
  2. No check for whether that same company also had a LIVE (non-terminal) row in
     ## Active Pipeline. A company with both an old Withdrawn row and a current
     active row was treated as terminal.

The fixtures below replay both. `Northwind` is the incident shape (duplicate rows,
plus an unrelated todo name-dropping it); `Globex` is the genuinely-terminal
control that must still be withdrawn so the guard cannot pass by disabling sync.
"""
import json
import os
import subprocess
import sys

from conftest import TOOLS_DIR

PIPELINE = """\
# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Northwind | Engagement Lead | Onsite | 2025-01-15 | Await read | - | live loop | - |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Northwind | Solutions Architect | Withdrawn | 2025-01-10 | - | - | stale duplicate row | - |
| Globex | Chief of Staff | Rejected | 2025-02-01 | - | - | closed out | - |
"""

TODOS = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Follow up with Northwind recruiter | High | — | Pending | live loop |
| Prep Acme onsite | High | — | Pending | reuse the Northwind case format |
| Apply to Globex | Med | — | Pending | — |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Old task | Low | 2025-01-05 | Completed 2025-01-05 |
"""


def _setup(tmp_path, pipeline=PIPELINE, todos=TODOS):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "job-pipeline.md").write_text(pipeline, encoding="utf-8")
    todos_path = data / "job-todos.md"
    todos_path.write_text(todos, encoding="utf-8")
    return todos_path


def _sync(tmp_path):
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), "sync",
           "--repo-root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return json.loads(r.stdout)


def _active(todos_path):
    return todos_path.read_text(encoding="utf-8").split("## Completed")[0]


def _completed(todos_path):
    return todos_path.read_text(encoding="utf-8").split("## Completed")[1]


def test_company_with_a_live_pipeline_row_is_not_treated_as_terminal(tmp_path):
    """Defect 2: Northwind is Withdrawn in Archived but still Onsite in Active Pipeline."""
    p = _setup(tmp_path)
    _sync(tmp_path)
    assert "Follow up with Northwind recruiter" in _active(p)


def test_company_mentioned_only_in_notes_is_not_withdrawn(tmp_path):
    """Defect 1: 'Prep Acme onsite' merely name-drops Northwind in its Notes column."""
    p = _setup(tmp_path)
    _sync(tmp_path)
    assert "Prep Acme onsite" in _active(p)


def test_notes_mention_of_a_terminal_company_does_not_withdraw(tmp_path):
    """Defect 1, isolated from defect 2: Globex is terminal with no live row, but
    this todo is about Acme and only mentions Globex in Notes."""
    todos = TODOS.replace("| reuse the Northwind case format |",
                          "| reuse the Globex case format |")
    p = _setup(tmp_path, todos=todos)
    _sync(tmp_path)
    assert "Prep Acme onsite" in _active(p)


def test_genuinely_terminal_company_is_still_withdrawn(tmp_path):
    """Control: the guard must not work by disabling sync outright."""
    p = _setup(tmp_path)
    res = _sync(tmp_path)
    assert res["status"] == "ok"
    assert "Apply to Globex" not in _active(p)
    row = next(l for l in _completed(p).splitlines() if "Apply to Globex" in l)
    assert "Withdrawn" in row
    assert "Globex" in row and "Rejected" in row


def test_incident_blast_radius_is_exactly_one_row(tmp_path):
    """The whole point: three candidate todos, exactly one legitimate withdrawal."""
    _setup(tmp_path)
    res = _sync(tmp_path)
    assert res["withdrawn"] == 1, f"expected 1 withdrawal, got {res.get('withdrawn')}"
