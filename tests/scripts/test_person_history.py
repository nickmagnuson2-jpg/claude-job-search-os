#!/usr/bin/env python3
"""Tests for tools/person_history.py — the pre-recommendation record check.

The tool exists because a confident recommendation was made without reading a
relationship's history, and was wrong twice in one answer. So the property under test is
not "does it retrieve rows" but **does it put the thing that gets missed where it cannot
be missed**:

  1. Interactions come back OLDEST FIRST. Newest-first is the default reading and the
     default reading is what failed: recent entries showed a loop closing and hid what the
     relationship had originally been for.
  2. `origin` is surfaced as its own field, and it is the EARLIEST entry. Burying it in a
     list is the same failure with extra steps.
  3. Absence is a reported result, never an empty success. "No record" is precisely the
     finding that should stop a recommendation, so it must be loud and must exit nonzero.

Driven against a synthetic repo via PERSON_HISTORY_REPO_ROOT. All names are placeholders:
this file is a public artifact.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "person_history.py"


def load(root, monkeypatch):
    """Fresh module bound to `root`; REPO_ROOT is read at import time."""
    monkeypatch.setenv("PERSON_HISTORY_REPO_ROOT", str(root))
    spec = importlib.util.spec_from_file_location("person_history_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "networking.md").write_text(
        "# Networking\n\n"
        "| Name | Company | Role | Relationship | First | Last | Email |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Casey Doe | Acme | Talent Partner | recruiter | 2026-01-05 | 2026-03-20 | "
        "casey@example.com |\n"
        "| Jordan Sample | Beacon | Founder | peer | 2026-02-01 | 2026-02-01 | - |\n"
        "\n"
        "### Casey Doe — Acme\n\n"
        "#### 2026-03-20 | email | LATEST. The loop closed, they passed.\n\n"
        "**Follow-up:** none\n\n"
        "#### 2026-02-10 | call | MIDDLE. Screening call happened.\n\n"
        "#### 2026-01-05 | linkedin | ORIGINAL. Introduced for a chief of staff role, "
        "which Casey redirected to a strategist seat.\n\n"
        "### Jordan Sample — Beacon\n\n"
        "#### 2026-02-01 | email | Only interaction.\n",
        encoding="utf-8")
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Company | Role | Stage | Updated |\n|---|---|---|---|\n"
        "| Acme | Strategist | Rejected | 2026-03-20 |\n", encoding="utf-8")
    (tmp_path / "data" / "job-todos.md").write_text(
        "| Task | Priority | Due | Status |\n|---|---|---|---|\n"
        "| Follow up: Casey Doe re next steps | Med | 2026-03-25 | Pending |\n",
        encoding="utf-8")
    (tmp_path / "data" / "outreach-log.md").write_text(
        "| Date | Type | Channel | Recipient | Company | Subject | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 2026-03-01 | follow-up | email | Casey Doe | Acme | Checking in | Replied |\n",
        encoding="utf-8")
    return tmp_path


# --- 1. origin-first is the whole design ------------------------------------

def test_interactions_come_back_oldest_first(repo, monkeypatch):
    """Newest-first is how the file stores them and how a human skims them. That reading
    is what produced the error this tool exists to prevent."""
    mod = load(repo, monkeypatch)
    dates = [r["date"] for r in mod.interactions("Casey Doe")]
    assert dates == ["2026-01-05", "2026-02-10", "2026-03-20"]


def test_origin_is_the_earliest_interaction_not_the_latest(repo, monkeypatch):
    """The single most-missed fact. If this ever returns the newest entry, the tool is
    actively reinforcing the failure it was built to stop."""
    mod = load(repo, monkeypatch)
    origin = mod.build("Casey Doe")["origin"]
    assert origin["date"] == "2026-01-05"
    assert "ORIGINAL" in origin["summary"]
    assert "chief of staff" in origin["summary"]


def test_origin_is_rendered_before_the_interaction_list(repo, monkeypatch):
    """Position is the point. A correct value buried below a long list is the same miss."""
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Casey Doe"))
    assert "## ORIGIN" in body
    assert body.index("## ORIGIN") < body.index("## Interactions")


def test_the_origin_section_says_why_it_is_there(repo, monkeypatch):
    """Without the reason it reads as trivia and gets skipped."""
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Casey Doe"))
    assert "before recommending anything" in body
    assert "hides what it was FOR" in body


def test_a_single_interaction_is_still_its_own_origin(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    d = mod.build("Jordan Sample")
    assert d["origin"]["date"] == "2026-02-01"
    assert d["interaction_count"] == 1


# --- 2. absence is a result, never an empty success -------------------------

def test_an_unknown_person_exits_nonzero_and_says_so(repo, monkeypatch, capsys):
    """"No record" is the finding that should STOP a recommendation. Exiting 0 with an
    empty body renders identically to a clean lookup."""
    load(repo, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(TOOL), "Nobody Here"], capture_output=True, text=True,
        env={**os.environ, "PERSON_HISTORY_REPO_ROOT": str(repo),
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["found"] is False
    assert "unverified" in out["note"]


def test_a_known_person_exits_zero(repo, monkeypatch):
    r = subprocess.run(
        [sys.executable, str(TOOL), "Casey Doe"], capture_output=True, text=True,
        env={**os.environ, "PERSON_HISTORY_REPO_ROOT": str(repo),
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert "ORIGIN" in r.stdout


def test_missing_data_files_do_not_crash(tmp_path, monkeypatch):
    """A partial checkout or a fresh clone must degrade, not explode."""
    mod = load(tmp_path, monkeypatch)
    d = mod.build("Casey Doe")
    assert d["interactions_oldest_first"] == [] and d["contact"] is None


# --- 3. the record itself ---------------------------------------------------

def test_the_contact_row_is_matched_exactly_not_by_substring(repo, monkeypatch):
    """A substring match would resolve a first name to the wrong person, which is worse
    than no match because it is silent."""
    mod = load(repo, monkeypatch)
    assert mod.contact_row("Casey Doe")["company"] == "Acme"
    assert mod.contact_row("Casey") is None


def test_pipeline_todos_and_outreach_are_all_pulled(repo, monkeypatch):
    """Six greps collapsed into one command is the entire value proposition; dropping any
    source silently narrows the check while still looking complete."""
    mod = load(repo, monkeypatch)
    d = mod.build("Casey Doe")
    assert d["pipeline"][0]["stage"] == "Rejected"
    assert d["todos"][0]["status"] == "Pending"
    assert d["outreach"][0]["status"] == "Replied"


def test_company_mode_skips_person_interactions(repo, monkeypatch):
    """A company is not a person; a networking section lookup would return noise."""
    mod = load(repo, monkeypatch)
    d = mod.build("Acme", is_company=True)
    assert d["kind"] == "company"
    assert d["interactions_oldest_first"] == [] and d["origin"] is None
    assert d["pipeline"][0]["company"] == "Acme"


def test_artifacts_name_the_files_that_mention_them(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    (repo / "coaching" / "progress").mkdir(parents=True)
    (repo / "coaching" / "progress" / "2026-03-20-acme-debrief.md").write_text(
        "Debrief of the Casey Doe call.", encoding="utf-8")
    hits = mod.artifacts("Casey Doe")
    assert "coaching/progress/2026-03-20-acme-debrief.md" in hits


def test_a_section_heading_with_no_trailing_dash_still_resolves(repo, monkeypatch):
    """`### Name` and `### Name — Company` both occur in the live file."""
    mod = load(repo, monkeypatch)
    p = repo / "data" / "networking.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "### Jordan Sample — Beacon", "### Jordan Sample"), encoding="utf-8")
    assert len(mod.interactions("Jordan Sample")) == 1


def test_one_persons_entries_do_not_leak_into_another(repo, monkeypatch):
    """Sections are delimited by the next `### `; a greedy read would merge them and
    hand back an origin belonging to someone else."""
    mod = load(repo, monkeypatch)
    assert len(mod.interactions("Casey Doe")) == 3
    assert all("Only interaction" not in r["summary"]
               for r in mod.interactions("Casey Doe"))


# --- 4. the rendered document is the product --------------------------------
#
# Every assertion below was a surviving mutant. The whole render layer could be gutted
# with the suite green: contact block, interaction lines, every section, and the guards
# that decide whether a section appears at all. A retrieval tool whose output silently
# loses half the record is worse than no tool, because the caller believes they checked.

def test_the_contact_block_is_rendered(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Casey Doe"))
    assert "**Casey Doe** | Acme | Talent Partner" in body
    assert "Relationship: recruiter" in body
    assert "First contact: 2026-01-05" in body and "Last: 2026-03-20" in body
    assert "casey@example.com" in body


def test_every_interaction_appears_in_the_rendered_list(repo, monkeypatch):
    """A truncated list reintroduces exactly the partial reading this tool prevents."""
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Casey Doe"))
    section = body.split("## Interactions")[1]
    for d in ("2026-01-05", "2026-02-10", "2026-03-20"):
        assert d in section
    assert "(3, oldest first)" in body


def test_pipeline_todos_and_outreach_each_render_their_own_section(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Casey Doe"))
    assert "## Pipeline" in body and "stage=Rejected" in body
    assert "## To-dos" in body and "status=Pending" in body
    assert "## Outreach log" in body and "status=Replied" in body


def test_artifacts_render_with_a_count(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    (repo / "output" / "acme").mkdir(parents=True)
    (repo / "output" / "acme" / "brief.md").write_text("Casey Doe brief", encoding="utf-8")
    body = mod.render(mod.build("Casey Doe"))
    assert "## Artifacts (1)" in body
    assert "`output/acme/brief.md`" in body


def test_empty_sections_are_omitted_entirely(repo, monkeypatch):
    """A heading over an empty list reads as 'checked, nothing there' when nothing was
    checked. Jordan has no pipeline, no todos, no outreach, no artifacts."""
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Jordan Sample"))
    for heading in ("## Pipeline", "## To-dos", "## Outreach log", "## Artifacts"):
        assert heading not in body


def test_no_origin_means_no_origin_section(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    body = mod.render(mod.build("Acme", is_company=True))
    assert "## ORIGIN" not in body and "## Interactions" not in body


# --- 5. parser guards -------------------------------------------------------

def test_malformed_rows_are_skipped_not_half_parsed(repo, monkeypatch):
    """A short row would IndexError or, worse, shift every column left and report a
    stage that is really a date."""
    mod = load(repo, monkeypatch)
    for rel, extra in (("data/job-pipeline.md", "| Acme |\n"),
                       ("data/job-todos.md", "| Casey Doe |\n"),
                       ("data/outreach-log.md", "| Casey Doe |\n")):
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + extra, encoding="utf-8")
    d = mod.build("Casey Doe")
    assert len(d["pipeline"]) == 1 and len(d["todos"]) == 1 and len(d["outreach"]) == 1


def test_a_separator_row_is_not_read_as_data(repo, monkeypatch):
    """`|---|---|` starts with '| ' in some files and would otherwise become a record."""
    mod = load(repo, monkeypatch)
    p = repo / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8") + "| --- | Acme | --- | --- |\n",
                 encoding="utf-8")
    assert all(r["company"] != "---" for r in mod.pipeline_rows("Acme"))


def test_outreach_requires_a_real_date_in_the_first_column(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    p = repo / "data" / "outreach-log.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "| notadate | follow-up | email | Casey Doe | Acme | x | Sent |\n",
                 encoding="utf-8")
    assert [r["date"] for r in mod.outreach_rows("Casey Doe")] == ["2026-03-01"]


def test_non_table_lines_never_become_contacts(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    p = repo / "data" / "networking.md"
    p.write_text("Casey Doe wrote this prose line\n" + p.read_text(encoding="utf-8"),
                 encoding="utf-8")
    assert mod.contact_row("Casey Doe")["company"] == "Acme"


def test_a_missing_artifacts_tree_is_not_an_error(tmp_path, monkeypatch):
    mod = load(tmp_path, monkeypatch)
    assert mod.artifacts("Casey Doe") == []


def test_artifacts_excludes_files_that_do_not_mention_the_target(repo, monkeypatch):
    mod = load(repo, monkeypatch)
    (repo / "output" / "other").mkdir(parents=True)
    (repo / "output" / "other" / "unrelated.md").write_text("nothing here", encoding="utf-8")
    assert mod.artifacts("Casey Doe") == []


# --- 6. the company cross-reference must not duplicate ----------------------

def test_a_company_row_already_matched_by_name_is_not_added_twice(repo, monkeypatch):
    """The widening is additive; without the dedupe a row naming both the person and the
    company appears twice and inflates what the reader thinks they are looking at."""
    mod = load(repo, monkeypatch)
    p = repo / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "| Acme | Ops role for Casey Doe | Active | 2026-03-01 |\n",
                 encoding="utf-8")
    pipe = mod.build("Casey Doe")["pipeline"]
    keys = [(r["company"], r["role"]) for r in pipe]
    assert len(keys) == len(set(keys)), f"duplicate pipeline rows: {keys}"


def test_the_company_widened_row_says_how_it_got_there(repo, monkeypatch):
    """Provenance: a row the caller did not ask for by name must announce why it is here."""
    mod = load(repo, monkeypatch)
    pipe = mod.build("Casey Doe")["pipeline"]
    assert any(r.get("via") == "company of Casey Doe" for r in pipe)


# --- 7. the CLI contract ----------------------------------------------------

def test_no_argument_exits_two_with_a_message(repo):
    """Exit 0 on a usage error would let a caller believe an empty check succeeded."""
    r = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True,
                       env={**os.environ, "PERSON_HISTORY_REPO_ROOT": str(repo),
                            "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 2
    assert "name or --company" in r.stderr


def test_a_prose_line_containing_pipes_is_not_read_as_a_contact_row(repo, monkeypatch):
    """The `startswith("| ")` guard is the only thing separating a table row from prose
    that happens to contain pipes. Without it this line resolves and returns the WRONG
    company, silently, which is worse than not resolving at all."""
    mod = load(repo, monkeypatch)
    p = repo / "data" / "networking.md"
    p.write_text("Casey Doe | Impostor Corp | not a table row at all\n"
                 + p.read_text(encoding="utf-8"), encoding="utf-8")
    assert mod.contact_row("Casey Doe")["company"] == "Acme"


def test_a_three_column_pipeline_row_yields_an_empty_updated_not_a_crash(repo, monkeypatch):
    """Older rows predate the Updated column. Indexing them unguarded raises IndexError
    and takes the whole lookup down with it."""
    mod = load(repo, monkeypatch)
    p = repo / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8") + "| Acme | Legacy role | Withdrawn |\n",
                 encoding="utf-8")
    rows = mod.pipeline_rows("Acme")
    legacy = [r for r in rows if r["role"] == "Legacy role"]
    assert legacy and legacy[0]["updated"] == ""
