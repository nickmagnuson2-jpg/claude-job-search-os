"""Exact-output tests for `todo_write.py sync`: the JSON it prints and the file it writes.

The older sync tests (test_todo_write_sync.py) assert membership: a row is or is not
in a section, a count is right. These pin the whole result for crafted fixtures, so a
change to row formatting, note merging, the summary text, or which lines count as table
rows shows up as a diff instead of passing unnoticed.

Placeholder company names are distinctive non-words on purpose: `sync` refuses to match
an ordinary English single-token name, and these tests are not about that filter.
"""
import json
import os
import subprocess
import sys
from datetime import date

from conftest import TOOLS_DIR

PIPE_HEAD = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
             "| --- | --- | --- | --- | --- | --- | --- | --- |\n")

PIPELINE = (
    "# Job Pipeline\n\n## Active Pipeline\n\n" + PIPE_HEAD
    + "| Hooli | Ops Manager | Onsite | 2025-03-03 | Prep | - | - | - |\n"
    "\n## Archived\n\n" + PIPE_HEAD
    + "| Globex | Chief of Staff | Rejected | 2025-02-01 | - | - | - | - |\n"
    "| Initech | Strategist | Closed - they passed | 2025-03-01 | - | - | - | - |\n"
)

ACTIVE_HEAD = ("| Task | Priority | Due | Status | Notes |\n"
               "| --- | --- | --- | --- | --- |\n")
COMPLETED_HEAD = ("| Task | Priority | Completed | Notes |\n"
                  "| --- | --- | --- | --- |\n"
                  "| Old task | Low | 2025-01-05 | Completed 2025-01-05 |\n")

TODOS = (
    "# Job Search To-Dos\n\n## Active\n\n" + ACTIVE_HEAD
    + "| Apply to Globex | High | — | Pending | met the team at a meetup |\n"
    "| Research Initech leadership | Low | — | Pending | — |\n"
    "| Prep Hooli onsite | High | — | Pending | — |\n"
    "\n## Completed\n\n" + COMPLETED_HEAD
)


def _write(tmp_path, pipeline, todos):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "job-pipeline.md").write_text(pipeline, encoding="utf-8")
    path = data / "job-todos.md"
    path.write_text(todos, encoding="utf-8")
    return path


def _sync(tmp_path, apply):
    cmd = [sys.executable, str(TOOLS_DIR / "todo_write.py"), "sync", "--repo-root", str(tmp_path)]
    if apply:
        cmd.append("--apply")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_apply_writes_exact_completed_rows_and_summary(tmp_path):
    """Each withdrawn row keeps its own priority, keeps real notes ahead of the auto
    note, gets the bare auto note when its notes were empty, and the summary names
    every company with its stage, in row order."""
    path = _write(tmp_path, PIPELINE, TODOS)
    today = date.today().isoformat()

    out = _sync(tmp_path, apply=True)

    assert out == {
        "status": "ok",
        "action": "sync",
        "summary": "2 to-do(s) auto-withdrawn — Globex (Rejected); Initech (Closed - they passed)",
        "withdrawn": 2,
        "companies": ["Globex", "Initech"],
    }
    assert path.read_text(encoding="utf-8") == (
        "# Job Search To-Dos\n\n## Active\n\n" + ACTIVE_HEAD
        + "| Prep Hooli onsite | High | — | Pending | — |\n"
        "\n## Completed\n\n" + COMPLETED_HEAD
        + f"| Apply to Globex | High | Withdrawn {today} | met the team at a meetup / "
          f"Auto-withdrawn {today} — Globex pipeline entry marked Rejected |\n"
        f"| Research Initech leadership | Low | Withdrawn {today} | "
        f"Auto-withdrawn {today} — Initech pipeline entry marked Closed - they passed |\n"
    )


def test_two_todos_for_one_company_are_named_once_in_the_summary(tmp_path):
    """The summary lists each (company, stage) once even when it withdrew several rows."""
    todos = TODOS.replace(
        "| Research Initech leadership | Low | — | Pending | — |\n",
        "| Email Globex recruiter | Med | — | Pending | — |\n")
    _write(tmp_path, PIPELINE, todos)

    out = _sync(tmp_path, apply=True)

    assert out["summary"] == "2 to-do(s) auto-withdrawn — Globex (Rejected)"
    assert out["companies"] == ["Globex"]


def test_apply_creates_the_completed_section_when_the_file_has_none(tmp_path):
    """A to-do file with no ## Completed section gets one appended, set off from the
    Active table by a blank line and with its own header and separator."""
    todos = ("# Job Search To-Dos\n\n## Active\n\n" + ACTIVE_HEAD
             + "| Apply to Globex | High | — | Pending | — |\n")
    path = _write(tmp_path, PIPELINE, todos)
    today = date.today().isoformat()

    out = _sync(tmp_path, apply=True)

    assert out["withdrawn"] == 1
    assert path.read_text(encoding="utf-8") == (
        "# Job Search To-Dos\n\n## Active\n\n" + ACTIVE_HEAD
        + "\n## Completed\n\n"
        "| Task | Priority | Completed | Notes |\n"
        "| --- | --- | --- | --- |\n"
        f"| Apply to Globex | High | Withdrawn {today} | "
        f"Auto-withdrawn {today} — Globex pipeline entry marked Rejected |\n"
    )


NO_MATCH_TODOS = TODOS.replace(
    "| Apply to Globex | High | — | Pending | met the team at a meetup |\n"
    "| Research Initech leadership | Low | — | Pending | — |\n", "")


def test_preview_with_no_matching_todo_says_so(tmp_path):
    _write(tmp_path, PIPELINE, NO_MATCH_TODOS)

    out = _sync(tmp_path, apply=False)

    assert out == {
        "status": "ok",
        "action": "sync",
        "summary": "No active todos matched terminal pipeline companies",
        "withdrawn": 0,
        "candidates": [],
        "skipped_companies": [],
    }


def test_apply_with_no_matching_todo_says_so_and_leaves_the_file_alone(tmp_path):
    path = _write(tmp_path, PIPELINE, NO_MATCH_TODOS)

    out = _sync(tmp_path, apply=True)

    assert out == {
        "status": "ok",
        "action": "sync",
        "summary": "No active todos matched terminal pipeline companies",
        "withdrawn": 0,
        "candidates": [],
        "skipped_companies": [],
    }
    assert path.read_text(encoding="utf-8") == NO_MATCH_TODOS


def test_a_prose_line_with_pipes_in_the_pipeline_is_not_a_row(tmp_path):
    """A stage legend written under ## Archived contains pipes and a terminal word,
    but it is not a table row and must not be read as a closed company."""
    pipeline = (
        "# Job Pipeline\n\n## Active Pipeline\n\n" + PIPE_HEAD
        + "| Hooli | Ops Manager | Onsite | 2025-03-03 | Prep | - | - | - |\n"
        "\n## Archived\n\n"
        "Terminal stages: Withdrawn | Rejected | Closed\n\n" + PIPE_HEAD
    )
    path = _write(tmp_path, pipeline, TODOS)

    out = _sync(tmp_path, apply=False)

    assert out == {
        "status": "ok",
        "action": "sync",
        "summary": "No terminal-stage companies in the pipeline",
        "withdrawn": 0,
    }
    assert path.read_text(encoding="utf-8") == TODOS


def test_a_prose_line_in_the_active_section_is_not_a_candidate(tmp_path):
    """Only table rows are todos. A note above the Active table that names a closed
    company is not a candidate, and the real row for that company still is."""
    todos = TODOS.replace(
        "## Active\n\n",
        "## Active\n\nGlobex follow-ups are tracked in the pipeline, not here.\n\n")
    _write(tmp_path, PIPELINE, todos)

    out = _sync(tmp_path, apply=False)

    assert out["candidates"] == [
        {"task": "Apply to Globex", "priority": "High", "company": "Globex", "stage": "Rejected"},
        {"task": "Research Initech leadership", "priority": "Low", "company": "Initech",
         "stage": "Closed - they passed"},
    ]


def test_a_company_closed_in_both_sections_is_skipped_once(tmp_path):
    """A company with a terminal row in Active Pipeline and another in Archived is one
    company: the skipped list names it once, not once per row."""
    pipeline = (
        "# Job Pipeline\n\n## Active Pipeline\n\n" + PIPE_HEAD
        + "| Anchor | Ops Lead | Closed - no response | 2025-03-01 | - | - | - | - |\n"
        "\n## Archived\n\n" + PIPE_HEAD
        + "| Anchor | Ops Lead | Withdrawn | 2025-01-10 | - | - | - | - |\n"
    )
    _write(tmp_path, pipeline, TODOS)

    out = _sync(tmp_path, apply=False)

    assert out == {
        "status": "ok",
        "action": "sync",
        "summary": "No terminal-stage companies with a distinctive name",
        "withdrawn": 0,
        "candidates": [],
        "skipped_companies": ["Anchor"],
    }
