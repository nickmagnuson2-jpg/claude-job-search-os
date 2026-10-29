"""Tests for tools/day_check.py, the read-only "where am I today" counts (/check, the page).

Placeholder people and companies only: this file is public.
"""
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import day_check as dc  # noqa: E402

D = date(2026, 1, 7)  # a Wednesday; the week starts Monday 2026-01-05

OUTREACH = """# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status | Artifact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-01-07 | cold-outreach | email | Alex Example | Acme Corp | hello | Sent | |
| 2026-01-07 | follow-up | email | Blair Sample | Beta Inc | bump | Drafted | |
| 2026-01-07 | follow-up | email | Casey Person | Gamma LLC | bump | Replied | |
| 2026-01-06 | cold-outreach | email | Dana Placeholder | Delta Co | hi | Sent | |
| 2026-01-04 | cold-outreach | email | Old Sunday | Delta Co | last week | Sent | |
"""

NETWORKING = """# Networking

### Alex — Acme Corp

#### 2026-01-07 | email | Follow-up #1 SENT 9:00 AM on the intro thread.
#### 2026-01-07 | email | REPLY from Alex 10:00 AM: happy to talk.

### Erin Example — Epsilon

#### 2026-01-07 | email (inbound) | She wrote first about a role.
#### 2026-01-07 | email | Nick replied 11:00 AM with times.
#### 2026-01-07 | call | Intro call, 30 minutes.
#### 2026-01-07 | email | Erin replied 1:00 PM: Thursday works.
#### 2026-01-07 | email | she sent the deck over, nothing owed.
#### 2026-01-06 | video | Screen with the team.
"""

PIPELINE = """# Job Application Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Ops Lead | Applied | 2026-01-07 | Follow-up sent | cv.pdf | Applied 2026-01-02 via the site | |
| Beta Inc | Strategist | Applied | 2026-01-07 | — | cv.pdf | Applied 2026-01-07 via Ashby | |
| Gamma LLC | BizOps | Researching | 2026-01-06 | — | — | applied on 2026-01-06 per recruiter | |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Delta Co | Analyst | Rejected | 2026-01-07 | — | — | — | |
"""

TODOS = """# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|------|----------|-----|--------|-------|
| Open task | High | 2026-01-09 | Pending | |

## Completed

| Task | Priority | Completed | Notes |
|------|----------|-----------|-------|
| Done today | High | 2026-01-07 | |
| Also today | Low | 2026-01-07 | |
| Pulled | Low | 2026-01-07 (withdrawn) | |
| Done Monday | Low | 2026-01-05 | |
"""

GOALS = """# Goals

## Success Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Applications / week | 5 (owner, 2026-01-01) | — |
| New contacts / week | 10 | — |
"""


def _repo(tmp_path, **override):
    files = {"outreach-log.md": OUTREACH, "networking.md": NETWORKING,
             "job-pipeline.md": PIPELINE, "job-todos.md": TODOS, "goals.md": GOALS}
    files.update(override)
    (tmp_path / "data").mkdir()
    for name, body in files.items():
        if body is not None:
            (tmp_path / "data" / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_counts_for_the_day_and_the_week(tmp_path):
    out = dc.collect(_repo(tmp_path), D)
    c = out["counts"]
    assert out["week_start"] == "2026-01-05" and out["complete"] is True
    # Alex (both logs, "Alex" and "Alex Example" are one person), Casey (Replied is sent),
    # Erin ("Nick replied"). Blair is only Drafted.
    assert c["sends"] == {"today": 3, "week": 4}
    # REPLY from, email (inbound), "Erin replied". Not "Nick replied", not "she sent".
    assert c["replies"] == {"today": 3, "week": 3}
    assert c["calls"] == {"today": 1, "week": 2}
    # Beta says "Applied 2026-01-07". Acme's Stage "Applied" next to Date Updated
    # 2026-01-07 is not an application today. Gamma's "applied on" counts on its date.
    assert c["applications"] == {"today": 1, "week": 2}
    assert c["pipeline_moves"] == {"today": 3, "week": 4}
    assert c["todos_closed"] == {"today": 2, "week": 3}


def test_the_weekly_target_comes_from_goals(tmp_path):
    out = dc.collect(_repo(tmp_path), D)
    assert out["targets"] == {"applications": {
        "target": 5, "period": "week", "label": "Applications / week",
        "source": "data/goals.md Success Metrics"}}


def test_monday_is_its_own_week(tmp_path):
    out = dc.collect(_repo(tmp_path), date(2026, 1, 5))
    assert out["week_start"] == "2026-01-05"
    assert out["counts"]["todos_closed"] == {"today": 1, "week": 1}


@pytest.mark.parametrize("missing,nulls", [
    ("networking.md", ("sends", "replies", "calls")),
    ("outreach-log.md", ("sends",)),
    ("job-pipeline.md", ("applications", "pipeline_moves")),
    ("job-todos.md", ("todos_closed",)),
])
def test_an_unreadable_source_is_null_never_zero(tmp_path, missing, nulls):
    out = dc.collect(_repo(tmp_path, **{missing: None}), D)
    assert out["complete"] is False
    for k in dc.COUNTS:
        if k in nulls:
            assert out["counts"][k] == {"today": None, "week": None}, k
        else:
            assert out["counts"][k]["today"] is not None, k


def test_todo_schema_drift_is_reported_not_zeroed(tmp_path):
    drift = TODOS.replace("| Task | Priority | Completed | Notes |", "| Task | Done on | Notes |")
    out = dc.collect(_repo(tmp_path, **{"job-todos.md": drift}), D)
    assert out["counts"]["todos_closed"]["today"] is None
    assert out["sources"]["job-todos"]["readable"] is False


@pytest.mark.parametrize("kind,summary,want", [
    ("email", "REPLY from Sam 9:19 AM: confirmed.", "reply"),
    ("email", "Inbound 09:12: asked for an intro call.", "reply"),
    ("email (inbound)", "anything", "reply"),
    ("email", "Sam replied 2:00 PM.", "reply"),
    ("email", "Nick replied 2:00 PM.", "send"),
    ("email", "Follow-up #2 SENT unchanged.", "send"),
    ("text", "OUTBOUND text: a nudge.", "send"),
    ("email", "Cold outreach after applying.", "send"),
    ("email", "Final note SENT 1:43 PM.", "send"),
    ("email", "she sent the deck.", None),
    ("note", "context only", None),
])
def test_classify_summary(kind, summary, want):
    assert dc.classify_summary(kind, summary) == want


def test_same_person_needs_a_word_prefix():
    assert dc.same_person("Sam", "Sam Example") and dc.same_person("Sam Example", "sam")
    assert not dc.same_person("Sam", "Samantha Example")
    assert not dc.same_person("", "Sam")
    assert dc.count_people(["Sam", "Sam Example", "Lee"]) == 2


def test_the_cli_writes_nothing_and_prints_json(tmp_path):
    root = _repo(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "day_check.py"),
                        "--repo-root", str(root), "--target-date", "2026-01-07", "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["counts"]["sends"]["today"] == 3
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_the_cli_text_and_exit_codes(tmp_path):
    root = _repo(tmp_path, **{"goals.md": None})
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "day_check.py"),
                        "--repo-root", str(root), "--target-date", "2026-01-07"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "Sends" in r.stdout and "goals: file not found" in r.stdout
    bad = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "day_check.py"),
                          "--target-date", "01/07/2026"], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2


def test_a_bad_date_in_an_interaction_header_is_skipped(tmp_path):
    net = NETWORKING + "\n### Fran Example\n\n#### 2026-13-45 | call | not a date\n#### 2026-01-07 | call | real\n"
    out = dc.collect(_repo(tmp_path, **{"networking.md": net}), D)
    assert out["counts"]["calls"]["today"] == 2


def test_a_file_that_cannot_be_read_names_the_error(tmp_path):
    root = _repo(tmp_path, **{"networking.md": None})
    (root / "data" / "networking.md").mkdir()
    out = dc.collect(root, D)
    assert out["sources"]["networking"]["error"].startswith("unreadable:")
    assert out["counts"]["calls"]["today"] is None


def test_a_missing_todo_file_says_not_found(tmp_path):
    out = dc.collect(_repo(tmp_path, **{"job-todos.md": None}), D)
    assert out["sources"]["job-todos"] == {"readable": False, "error": "file not found"}


def test_prose_outside_the_pipeline_table_is_not_a_row(tmp_path):
    pipe = PIPELINE + "\nApplied 2026-01-07 to a place, noted in prose | x | y | 2026-01-07\n" \
                      "| Applied 2026-01-07 | short |\n"
    out = dc.collect(_repo(tmp_path, **{"job-pipeline.md": pipe}), D)
    assert out["counts"]["applications"]["today"] == 1
    assert out["counts"]["pipeline_moves"]["today"] == 3


def test_target_rows_outside_the_table_or_without_a_target_are_ignored(tmp_path):
    goals = "Applications / week | 9 is prose\n| Applications / week |\n" + GOALS
    out = dc.collect(_repo(tmp_path, **{"goals.md": goals}), D)
    assert out["targets"]["applications"]["target"] == 5
    assert dc.parse_targets("| Applications / week |\n") == {}
    assert dc.parse_targets("Applications / week | 9\n") == {}


def test_same_person_is_strictly_boolean():
    assert dc.same_person("", "Sam") is False


def test_text_shows_unknown_for_a_null_and_no_error_line_when_complete(tmp_path):
    full = dc.render_text(dc.collect(_repo(tmp_path), D))
    assert "unknown" not in full and "Unreadable" not in full
    assert "(target 5 a week)" in full
    (tmp_path / "data" / "job-todos.md").unlink()
    part = dc.render_text(dc.collect(tmp_path, D))
    line = [l for l in part.splitlines() if "To-dos closed" in l][0]
    assert line.count("unknown") == 2
    assert "Unreadable sources: job-todos: file not found" in part


def test_a_bad_target_date_says_why_on_stderr():
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "day_check.py"),
                        "--target-date", "01/07/2026"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "must be YYYY-MM-DD" in r.stderr


# --- D8 review round 1 -------------------------------------------------------------------

@pytest.mark.parametrize("summary", [
    "IMMEDIATE RESPONSE: Sam replied minutes after the cold email.",
    "Heard back from Sam: not hiring.",
    "Shannon responded to the comment.",
    "Backfilled. INBOUND 2:06 PM from the thread.",
])
def test_more_reply_shapes(summary):
    assert dc.classify_summary("email", summary) == "reply"


@pytest.mark.parametrize("summary", [
    "Nick replied 2:00 PM.", "I replied with times.", "No reply yet.",
    "Follow-up #2 SENT, reply in the old thread.",
])
def test_not_replies(summary):
    assert dc.classify_summary("email", summary) != "reply"


def test_a_non_utf8_file_is_unreadable_not_a_crash(tmp_path):
    root = _repo(tmp_path)
    (root / "data" / "networking.md").write_bytes(b"\xff\xfe\x00bad")
    out = dc.collect(root, D)
    assert out["sources"]["networking"]["error"].startswith("unreadable:")
    assert out["counts"]["replies"]["today"] is None


def test_a_drifted_pipeline_header_is_unreadable(tmp_path):
    pipe = PIPELINE.replace("| Company | Role | Stage | Date Updated |", "| Company | Stage | Role | Date Updated |")
    out = dc.collect(_repo(tmp_path, **{"job-pipeline.md": pipe}), D)
    assert out["sources"]["pipeline"]["readable"] is False
    assert "schema drift" in out["sources"]["pipeline"]["error"]
    assert out["counts"]["pipeline_moves"]["today"] is None


def test_a_stage_move_to_applied_without_a_date_is_unconfirmed(tmp_path):
    pipe = PIPELINE.replace("## Archived",
        "| Zeta Co | Ops | Applied | 2026-01-07 | — | cv.pdf | via the tool | |\n\n## Archived")
    out = dc.collect(_repo(tmp_path, **{"job-pipeline.md": pipe}), D)
    # Zeta counts as unconfirmed; Acme (Applied, but "Applied 2026-01-02" in Notes) and
    # Beta (confirmed by its own text) do not.
    assert out["counts"]["applications"]["today"] == 1
    assert out["counts"]["applications_unconfirmed"] == {"today": 1, "week": 1}
    text = dc.render_text(out).splitlines()
    plus = [i for i, l in enumerate(text) if "plus 1 row(s) moved to Applied today" in l]
    assert len(plus) == 1 and "Applications" in text[plus[0] - 1]
    (tmp_path / "b").mkdir()
    assert "plus" not in dc.render_text(dc.collect(_repo(tmp_path / "b"), D))


def test_running_writes_no_bytecode(tmp_path):
    import shutil
    tools = tmp_path / "tools"
    shutil.copytree(REPO_ROOT / "tools", tools, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "r").mkdir()
    root = _repo(tmp_path / "r")
    r = subprocess.run([sys.executable, str(tools / "day_check.py"), "--repo-root", str(root),
                        "--target-date", "2026-01-07"], capture_output=True, text=True,
                       timeout=60, env={"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert not list(tools.rglob("__pycache__")), "day_check wrote bytecode"


@pytest.mark.parametrize("summary", [
    "Sam never replied after the intro.", "No reply from Sam yet.", "Nobody responded to the post.",
    "Still have not heard back from the team.", "Sam has yet to respond.",
])
def test_negated_notes_are_not_replies(summary):
    assert dc.classify_summary("email", summary) is None


def test_a_negated_note_that_records_a_send_is_a_send():
    assert dc.classify_summary("email", "Follow-up #2 SENT; no reply from Sam to #1.") == "send"
    assert dc.classify_summary("email (inbound)", "No reply needed, she wrote first") == "reply"


def test_a_pipeline_table_without_its_header_is_unreadable(tmp_path):
    pipe = PIPELINE.replace("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |",
                            "| Firm | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |")
    out = dc.collect(_repo(tmp_path, **{"job-pipeline.md": pipe}), D)
    assert out["sources"]["pipeline"]["readable"] is False
    assert "no '| Company |' header" in out["sources"]["pipeline"]["error"]
    assert out["counts"]["applications"]["today"] is None


def test_the_check_skill_prints_unconfirmed_applications():
    text = (REPO_ROOT / ".claude" / "skills" / "check" / "SKILL.md").read_text()
    assert "applications_unconfirmed" in text


@pytest.mark.parametrize("summary", [
    "I haven't heard back", "haven't heard back from her", "I havent heard back",
    "I haven’t heard back", "she hadn't responded", "still waiting to hear back",
])
def test_a_note_recording_no_reply_is_never_counted_as_a_reply(summary):
    """Review 2026-10-29: NEGATED knew "hasn't" but not "haven't"."""
    assert dc.classify_summary("email", summary) != "reply"
