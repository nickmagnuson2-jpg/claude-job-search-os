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


def _sync(tmp_path, apply=True, pipeline=None, todos=None):
    """Run sync. Defaults to apply=True here because most tests assert on the write.

    The COMMAND defaults the other way (preview) on purpose — see cmd_sync's docstring
    and test_bare_sync_writes_nothing below.
    """
    if pipeline is not None or todos is not None:
        _setup(tmp_path,
               pipeline=PIPELINE if pipeline is None else pipeline,
               todos=TODOS if todos is None else todos)
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

# Placeholder company names here must be DISTINCTIVE (not ordinary English words):
# `sync` deliberately refuses to match on a single-token name that is a dictionary word,
# so a fixture named after an everyday noun would exercise the distinctiveness filter
# instead of the freeform-stage parsing these tests are about.
FREEFORM_PIPELINE = """\
# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Initech | Deployment Strategist | Closed - they passed | 2025-03-01 | — | - | - | - |
| Vandelay | BizOps | Considered - passed (self, 7/7) | 2025-03-02 | — | - | - | - |
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
| Research Vandelay BizOps team | Med | — | Pending | — |
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
    assert "Research Vandelay BizOps team" not in _active(p)


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


# ---------------------------------------------------------------------------
# Weak-evidence matching (2026-09-14)
# ---------------------------------------------------------------------------
# Defect 3, measured on the owner's live files: 27 candidates, ~6 genuine. Two
# distinct mechanisms produced the other 21, and they fail independently:
#
#   3a. A single-token company name that is an ordinary English word (or too short
#       to be a brand token at all) matched every todo that used the word. A todo
#       about patching a script matched a company whose name is that verb; a
#       spreadsheet cell reference matched a two-character company name.
#   3b. The company was named anywhere in the TASK column, including deep in a
#       parenthetical or a REOPEN-gate clause, so infra/learning/reflection todos
#       that merely cite a company were treated as opportunity todos.
#
# The fixtures below use generic placeholders. "Anchor" is the ordinary-English
# shape, "Q3" the too-short shape, "Globex"/"Initech" the distinctive controls
# that must keep matching.

WEAK_PIPELINE = """\
# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Anchor | Ops Lead | Withdrawn | 2025-01-10 | - | - | - | - |
| Q3 | Analyst | Withdrawn | 2025-01-10 | - | - | - | - |
| Globex | Chief of Staff | Rejected | 2025-02-01 | - | - | - | - |
"""

WEAK_TODOS = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Anchor the voice examples in the style guide | High | — | Pending | — |
| Follow up: Casey Doe — He may hit the Q3 column-position check | Med | — | Pending | — |
| Build out the inbound profile preferences — Globex inbound is proof the source works | High | — | Pending | — |
| PARKED — build the extract pipeline as a skill. Working example and full prompt anatomy live in the Globex retro | Low | — | Pending | — |
| Reach the ops lead via the alumni path re Globex Business Operations — warmer than the cold queue | Med | — | Pending | — |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Old task | Low | 2025-01-05 | Completed 2025-01-05 |
"""


def _tasks(res):
    return [c["task"] for c in res.get("candidates", [])]


def test_ordinary_english_company_name_does_not_match_a_verb_in_the_task(tmp_path):
    """3a: a company named after an everyday word must not match ordinary prose."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert "Anchor the voice examples in the style guide" not in _tasks(res)


def test_company_name_too_short_to_be_a_brand_token_does_not_match(tmp_path):
    """3a: a two-character company name matched a spreadsheet cell reference."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert not any("column-position check" in t for t in _tasks(res))


def test_company_named_only_in_the_body_does_not_match(tmp_path):
    """3b: the todo's subject is the inbound profile, not the company it cites."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert not any(t.startswith("Build out the inbound profile") for t in _tasks(res))


def test_parked_marker_does_not_promote_the_body_into_the_subject(tmp_path):
    """3b: a leading PARKED/status marker is a prefix, not the subject. The subject is
    the clause after it, and a company cited further down the body still does not count."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert not any(t.startswith("PARKED") for t in _tasks(res))


def test_company_in_the_subject_clause_still_matches(tmp_path):
    """Control for 3b: a genuine opportunity todo names the company in its subject,
    before the em-dash body, and must still be caught."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert any(t.startswith("Reach the ops lead") for t in _tasks(res))


def test_weak_evidence_blast_radius_is_exactly_one_row(tmp_path):
    """Five candidate todos on the weak-evidence fixture, exactly one genuine."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert len(res["candidates"]) == 1, _tasks(res)


def test_preview_names_the_companies_it_refused_to_match_on(tmp_path):
    """Silently dropping a company is how a user concludes sync is broken. The
    non-distinctive names are reported, so the exclusion is visible."""
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=WEAK_TODOS, apply=False)
    assert sorted(res["skipped_companies"]) == ["Anchor", "Q3"]


def test_distinctive_multi_token_company_still_matches(tmp_path):
    """A multi-token name is distinctive as a phrase even when every component is an
    ordinary word — it must not be filtered out with the single-token ones."""
    pipeline = WEAK_PIPELINE.replace(
        "| Globex | Chief of Staff | Rejected",
        "| Anchor Point | Chief of Staff | Rejected | 2025-02-01 | - | - | - | - |\n"
        "| Globex | Chief of Staff | Rejected")
    todos = WEAK_TODOS.replace(
        "| Anchor the voice examples in the style guide | High | — | Pending | — |",
        "| Apply to Anchor Point | High | — | Pending | — |")
    res = _sync(tmp_path, pipeline=pipeline, todos=todos, apply=False)
    assert "Apply to Anchor Point" in _tasks(res)


def test_status_marker_prefix_is_skipped_so_the_real_subject_still_matches(tmp_path):
    """The marker-skip has to work in BOTH directions: dropping a leading 'PARKED' must
    not make every parked todo permanently unmatchable, or the filter hides real ones."""
    todos = WEAK_TODOS.replace(
        "| Anchor the voice examples in the style guide | High | — | Pending | — |",
        "| PARKED — Globex outreach follow-through: rebuild the sequence | Low | — | Pending | — |")
    res = _sync(tmp_path, pipeline=WEAK_PIPELINE, todos=todos, apply=False)
    assert any(t.startswith("PARKED — Globex") for t in _tasks(res)), _tasks(res)


def test_company_name_with_no_word_characters_is_skipped_not_crashed(tmp_path):
    """A punctuation-only company cell must be refused, not indexed into."""
    pipeline = WEAK_PIPELINE.replace(
        "| Anchor | Ops Lead | Withdrawn | 2025-01-10 | - | - | - | - |",
        "| *** | Ops Lead | Withdrawn | 2025-01-10 | - | - | - | - |")
    res = _sync(tmp_path, pipeline=pipeline, todos=WEAK_TODOS, apply=False)
    assert res["status"] == "ok"
    assert "***" in res["skipped_companies"]
