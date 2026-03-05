"""Tests for tools/act_apply.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_script, write_fixture, TOOLS_DIR, REPO_ROOT

# ---------------------------------------------------------------------------
# Helper: run without check=True
# ---------------------------------------------------------------------------

def run_act_apply(*args, tmp_path=None):
    script_path = TOOLS_DIR / "act_apply.py"
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(REPO_ROOT),
    )
    out = result.stdout.strip()
    if out:
        return json.loads(out), result.returncode
    return {"_stderr": result.stderr}, result.returncode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PIPELINE_MD = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""

NETWORKING_MD = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |

## Interaction Log
"""

NOTES_MD = """\
# Notes

> General notes.

## Decisions

## Notes

## From Inbox
"""


# ---------------------------------------------------------------------------
# Tests: pipeline-add
# ---------------------------------------------------------------------------

def test_pipeline_add_correct_row(tmp_path):
    """Adds an 8-column Researching row to the Active section."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "OpenAI",
        "--role", "CoS",
        "--url", "https://openai.com/careers/cos",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "pipeline_add"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| OpenAI |")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[0] == "OpenAI"
    assert cols[2] == "Researching"
    assert cols[7] == "https://openai.com/careers/cos"


def test_pipeline_add_source_file_in_notes(tmp_path):
    """--source-file value appears in Notes column."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "Stripe",
        "--source-file", "stripe-job.md",
    )
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "stripe-job.md" in content


def test_pipeline_add_missing_file_returns_error(tmp_path):
    """Missing pipeline file returns JSON error, not crash."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "Ghost Co",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


# ---------------------------------------------------------------------------
# Tests: contact-add
# ---------------------------------------------------------------------------

def test_contact_add_row_and_log_section(tmp_path):
    """Creates a Contacts row and an Interaction Log section."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Sarah Chen",
        "--company", "Headway",
        "--role", "Head of Ops",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "contact_add"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Sarah Chen |" in content
    assert "### Sarah Chen — Headway" in content


def test_contact_add_content_in_blockquote(tmp_path):
    """--content value is stored as blockquote in the interaction log entry."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Alex Park",
        "--company", "Acme",
        "--content", "Met Alex at SF health event. Head of ops at Acme.",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "> Met Alex at SF health event." in content


def test_contact_add_source_file_in_log(tmp_path):
    """--source-file value appears in interaction log entry."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Tom Brown",
        "--source-file", "tom-capture.md",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "tom-capture.md" in content


def test_contact_add_missing_file_returns_error(tmp_path):
    """Missing networking file returns JSON error."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Nobody",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


# ---------------------------------------------------------------------------
# Tests: notes-add
# ---------------------------------------------------------------------------

def test_notes_add_with_slug_goes_to_company_notes(tmp_path):
    """notes-add with --company-slug writes to data/company-notes/<slug>.md."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "Office is in the Dogpatch neighborhood",
        "--company-slug", "headway",
    )
    assert code == 0
    note_path = tmp_path / "data/company-notes/headway.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Office is in the Dogpatch neighborhood" in content


def test_notes_add_without_slug_goes_to_notes_md(tmp_path):
    """notes-add without --company-slug writes to data/notes.md under ## From Inbox."""
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "Unclassifiable snippet from inbox",
    )
    assert code == 0
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "Unclassifiable snippet from inbox" in content
    # Should be under ## From Inbox
    from_inbox_pos = content.index("## From Inbox")
    note_pos       = content.index("Unclassifiable snippet from inbox")
    assert note_pos > from_inbox_pos


def test_notes_add_creates_notes_md_if_missing(tmp_path):
    """notes-add creates data/notes.md with ## From Inbox if file is absent."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "New note",
    )
    assert code == 0
    assert (tmp_path / "data/notes.md").exists()
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "## From Inbox" in content
    assert "New note" in content


# ---------------------------------------------------------------------------
# Tests: company-note-add
# ---------------------------------------------------------------------------

COMPANY_NOTES_EXISTING = """\
# amae-health — Notes

> Running log of raw notes, call prep, and observations.
> Newest entries at the top.

---

## 2026-02-20 | recruiter call

Notes from earlier call.
"""


def test_company_note_add_creates_new_file(tmp_path):
    """Creates data/company-notes/<slug>.md with header and entry when file is absent."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "amae-health",
        "--content", "Alex replied with a calendar invite",
        "--context", "inbound email",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "company_note_add"
    assert result["slug"] == "amae-health"

    note_path = tmp_path / "data/company-notes/amae-health.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Alex replied with a calendar invite" in content
    assert "inbound email" in content


def test_company_note_add_prepends_to_existing(tmp_path):
    """New entry is prepended before the first ## YYYY-* heading (newest first)."""
    write_fixture(tmp_path, "data/company-notes/amae-health.md", COMPANY_NOTES_EXISTING)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "amae-health",
        "--content", "Follow-up email received",
        "--context", "inbound email",
    )
    content = (tmp_path / "data/company-notes/amae-health.md").read_text(encoding="utf-8")
    new_pos  = content.index("Follow-up email received")
    old_pos  = content.index("Notes from earlier call.")
    assert new_pos < old_pos, "New entry should appear before existing entries"


def test_company_note_add_context_in_header(tmp_path):
    """--context value appears in the ## YYYY-MM-DD | <context> header."""
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "sofar-ocean",
        "--content", "Sarah confirmed interest in CoS role",
        "--context", "inbound email",
    )
    content = (tmp_path / "data/company-notes/sofar-ocean.md").read_text(encoding="utf-8")
    assert "| inbound email" in content


def test_company_note_add_source_file_in_header(tmp_path):
    """--source-file value appears in the header label."""
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "openai",
        "--content", "Recruiter outreach for eng role",
        "--source-file", "openai-recruiter.md",
    )
    content = (tmp_path / "data/company-notes/openai.md").read_text(encoding="utf-8")
    assert "openai-recruiter.md" in content


def test_company_note_add_dry_run(tmp_path):
    """--dry-run returns dry_run:true and does not create the file."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "company-note-add", "ghost-company",
        "--content", "Should not be written",
    )
    assert code == 0
    assert result["dry_run"] is True
    assert not (tmp_path / "data/company-notes/ghost-company.md").exists()


# ---------------------------------------------------------------------------
# Tests: dry-run
# ---------------------------------------------------------------------------

def test_dry_run_returns_no_file_change(tmp_path):
    """--dry-run returns dry_run:true and does not write."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "pipeline-add", "DryRun Corp",
    )
    assert code == 0
    assert result["dry_run"] is True
    after = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert after == original
