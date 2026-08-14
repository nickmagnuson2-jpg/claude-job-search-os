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


def _sync(tmp_path, apply=True):
    """Run sync. Defaults to apply=True here because most tests assert on the write.

    The COMMAND defaults the other way (preview) on purpose — see cmd_sync's docstring
    and test_bare_sync_writes_nothing below.
    """
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), "sync",
           "--repo-root", str(tmp_path)]
    if apply:
        cmd.append("--apply")
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


# ---------------------------------------------------------------------------
# Freeform terminal stages (2026-08-14)
#
# sync used a local exact-match set {"Withdrawn","Rejected","Accepted"} while every
# other pipeline consumer used stage_vocab.is_terminal_stage(), which matches terminal
# KEYWORDS anywhere in a freeform stage. On the live pipeline that day, 30 companies
# were closed with stages like "Closed - they passed" / "Declined" / "Considered -
# passed (self, 7/7)" and sat in ## Active Pipeline rather than ## Archived. Every one
# was invisible to sync, and each also landed in sync's "still live" set — so those
# companies were permanently blocked from syncing even once archived.
# ---------------------------------------------------------------------------

FREEFORM_PIPELINE = """\
# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Initech | Deployment Strategist | Closed - they passed | 2025-03-01 | — | - | - | - |
| Umbrella | BizOps | Considered - passed (self, 7/7) | 2025-03-02 | — | - | - | - |
| Hooli | Ops Manager | Onsite loop scheduled (founder screen PASSED) | 2025-03-03 | Prep | - | - | - |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Soylent | Chief of Staff | Closed - no response | 2025-02-01 | - | - | - | - |
"""

FREEFORM_TODOS = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Apply to Initech Deployment Strategist | High | — | Pending | — |
| Research Umbrella BizOps team | Med | — | Pending | — |
| Prep Hooli onsite | High | — | Pending | — |
| Research Soylent leadership | Med | — | Pending | — |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Old task | Low | 2025-01-05 | Completed 2025-01-05 |
"""


def test_freeform_terminal_stage_in_active_pipeline_is_terminal(tmp_path):
    """The 30-row case: closed with a descriptive stage, never moved to ## Archived."""
    p = _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    _sync(tmp_path)
    assert "Apply to Initech Deployment Strategist" not in _active(p)


def test_considered_passed_prefix_is_terminal(tmp_path):
    """'Considered - passed (self, ...)' is a self-pass, i.e. the opportunity is over."""
    p = _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    _sync(tmp_path)
    assert "Research Umbrella BizOps team" not in _active(p)


def test_freeform_terminal_stage_in_archived_is_terminal(tmp_path):
    """Archived rows must accept freeform stages too, not only the exact three."""
    p = _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    _sync(tmp_path)
    assert "Research Soylent leadership" not in _active(p)


def test_live_row_with_passed_in_prose_is_not_terminal(tmp_path):
    """SAFETY. A live loop whose stage narrates 'founder screen PASSED' must survive.

    This is the direction that costs the most if it breaks: over-eager terminal
    matching would withdraw the prep todos for an interview that has not happened.
    """
    p = _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    _sync(tmp_path)
    assert "Prep Hooli onsite" in _active(p)


def test_freeform_blast_radius(tmp_path):
    """Exactly the three terminal companies, never the live one."""
    _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    res = _sync(tmp_path)
    assert res["withdrawn"] == 3, f"expected 3 withdrawals, got {res.get('withdrawn')}"


def test_sync_runs_with_no_archived_section(tmp_path):
    """Terminal rows can now come from ## Active Pipeline, so a missing ## Archived
    section must no longer short-circuit the whole command."""
    pipeline = FREEFORM_PIPELINE.split("## Archived")[0]
    p = _setup(tmp_path, pipeline=pipeline, todos=FREEFORM_TODOS)
    res = _sync(tmp_path)
    assert res["status"] == "ok"
    assert "Apply to Initech Deployment Strategist" not in _active(p)
    assert "Prep Hooli onsite" in _active(p)


def test_bare_sync_writes_nothing(tmp_path):
    """Preview is the default. The 2026-08-14 real-data run produced 31 candidates of
    which ~4 were genuine, so an unattended write is the wrong default no matter how
    good the terminal-stage classification gets."""
    p = _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    before = p.read_text(encoding="utf-8")
    res = _sync(tmp_path, apply=False)
    assert res["status"] == "ok"
    assert res["withdrawn"] == 0
    assert res["applied"] is False
    assert p.read_text(encoding="utf-8") == before, "bare sync must not modify the file"


def test_preview_reports_the_same_rows_apply_would_withdraw(tmp_path):
    """Preview must not lie about what --apply would do, or confirming it is theatre."""
    _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    preview = _sync(tmp_path, apply=False)
    previewed = sorted(c["task"] for c in preview["candidates"])

    p2 = _setup(tmp_path / "second", pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    applied = _sync(tmp_path / "second", apply=True)
    completed = _completed(p2)
    assert applied["withdrawn"] == len(previewed)
    for task in previewed:
        assert task in completed


def test_preview_candidates_name_the_triggering_company(tmp_path):
    """The caller has to judge each row, so it needs to see WHY the row matched."""
    _setup(tmp_path, pipeline=FREEFORM_PIPELINE, todos=FREEFORM_TODOS)
    res = _sync(tmp_path, apply=False)
    row = next(c for c in res["candidates"]
               if c["task"] == "Apply to Initech Deployment Strategist")
    assert row["company"] == "Initech"
    assert "Closed" in row["stage"]


def test_terminal_in_archived_but_live_in_active_still_wins(tmp_path):
    """The 2026-08-08 rule must survive the freeform change: a live Active row beats a
    terminal Archived row, even when the live stage is freeform prose."""
    pipeline = FREEFORM_PIPELINE.replace(
        "| Soylent | Chief of Staff | Closed - no response | 2025-02-01 | - | - | - | - |",
        "| Soylent | Chief of Staff | Closed - no response | 2025-02-01 | - | - | - | - |\n"
        "| Hooli | Ops Manager | Closed - stale duplicate | 2025-01-01 | - | - | - | - |",
    )
    p = _setup(tmp_path, pipeline=pipeline, todos=FREEFORM_TODOS)
    _sync(tmp_path)
    assert "Prep Hooli onsite" in _active(p)
