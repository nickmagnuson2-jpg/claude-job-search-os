"""Tests for tools/remember_apply.py"""
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

def run_apply(*args, tmp_path=None):
    script_path = TOOLS_DIR / "remember_apply.py"
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


def dest_json(*dests):
    return json.dumps(list(dests))


# ---------------------------------------------------------------------------
# Destination-count guard (fable-audit Theme 2 doc-drift)
# Locks HANDLERS at 11 so a handler added/removed without updating the docstring,
# CLAUDE.md, and docs/methodology.md (all of which state the count) fails loudly.
# ---------------------------------------------------------------------------

def test_handler_count_is_eleven():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "remember_apply", str(TOOLS_DIR / "remember_apply.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.HANDLERS) == 11, sorted(mod.HANDLERS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NETWORKING_MD = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jane Doe — Acme Corp

"""

PIPELINE_MD = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Director | Researching | 2026-01-01 | Research | — | — | — |
"""

OUTREACH_MD = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-01-10 | cold-outreach | email | Jane Doe | Acme Corp | Hi Jane | Sent |
"""

PROFILE_MD = """\
# Profile

## Background

Some info here.
"""

NOTES_MD = """\
# Job Search Notes

## Decisions

## Notes
"""


# ---------------------------------------------------------------------------
# Tests: contact_note
# ---------------------------------------------------------------------------

def test_contact_note_appends_to_section(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane mentioned she'll be at the conference",
        "--destinations", dest_json(
            {"type": "contact_note", "entity": "Jane Doe", "file": "data/networking.md"}
        ),
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "contact_note"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "Jane mentioned she'll be at the conference" in content
    # Should be inside Jane's section
    assert "### Jane Doe" in content


def test_contact_note_is_parseable_interaction_entry(tmp_path):
    """A /remember contact_note must write a `#### DATE | ...` interaction header,
    not a bare `[DATE] note` line, so the reconcilers that scan the Interaction Log
    (outreach_pending, networking_read) can see it. Regression for fable-audit
    Theme 2: bare `[date]` lines left /remember-captured replies stuck 'awaiting'."""
    import re
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied confirming the intro",
        "--destinations", dest_json(
            {"type": "contact_note", "entity": "Jane Doe"}
        ),
    )
    assert code == 0
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    headers = [l for l in content.splitlines()
               if re.match(r"^####\s+\d{4}-\d{2}-\d{2}\s*\|", l)]
    assert len(headers) == 1, f"expected a parseable #### date header, got: {headers}"
    assert "Jane replied confirming the intro" in headers[0]
    # And it must NOT be the old bare-bracket form.
    assert not re.search(r"^\[\d{4}-\d{2}-\d{2}\]", content, re.MULTILINE)


def test_contact_note_flips_reply_out_of_awaiting_end_to_end(tmp_path):
    """The real purpose of the #### format (not just its shape): a /remember-captured
    reply must let outreach_pending reconcile the thread OUT of 'awaiting response'.
    A bare `[date]` line was invisible to that reconciler. fable-audit Theme 2.

    Uses real today() because apply_contact_note stamps the entry with now(); the
    outreach sent date is set a few days earlier so the logged reply post-dates it."""
    from datetime import date, timedelta
    today = date.today()
    # 10 days back: past the 5-day overdue threshold (so it's 'awaiting' before the
    # note) but within the 30-day lookback window.
    sent = (today - timedelta(days=10)).strftime("%Y-%m-%d")

    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    write_fixture(tmp_path, "data/outreach-log.md",
                  "# Outreach Log\n\n"
                  "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |\n"
                  "| --- | --- | --- | --- | --- | --- | --- |\n"
                  f"| {sent} | follow-up | email | Jane Doe | Acme Corp | Re: intro | Sent |\n")

    # Sanity: before the note, Jane's thread is awaiting.
    before = run_script("outreach_pending.py",
                        "--repo-root", str(tmp_path),
                        "--target-date", today.strftime("%Y-%m-%d"),
                        "--lookback-days", "30")
    awaiting_before = [e["name"] for e in before["awaiting_response"] + before["awaiting_response_overdue"]]
    assert "Jane Doe" in awaiting_before

    # Capture the reply via /remember contact_note.
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied — happy to intro me",
        "--destinations", dest_json({"type": "contact_note", "entity": "Jane Doe"}),
    )
    assert code == 0

    # After the note, the reconciler sees the interaction and drops Jane from awaiting.
    after = run_script("outreach_pending.py",
                       "--repo-root", str(tmp_path),
                       "--target-date", today.strftime("%Y-%m-%d"),
                       "--lookback-days", "30")
    awaiting_after = [e["name"] for e in after["awaiting_response"] + after["awaiting_response_overdue"]]
    assert "Jane Doe" not in awaiting_after


# ---------------------------------------------------------------------------
# Tests: outreach_reply
# ---------------------------------------------------------------------------

def test_outreach_reply_updates_status(tmp_path):
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied to my email",
        "--destinations", dest_json(
            {"type": "outreach_reply", "entity": "Jane Doe", "file": "data/outreach-log.md"}
        ),
    )
    assert code == 0
    content = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if "Jane Doe" in l and l.startswith("|")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    # Real schema: Status is cols[6]; the Subject cell (cols[5]) must stay intact.
    assert cols[6] == "Replied"
    assert cols[5] == "Hi Jane"


def test_outreach_reply_fallback_to_networking(tmp_path):
    """No outreach-log.md → falls back to writing a contact note."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied",
        "--destinations", dest_json(
            {"type": "outreach_reply", "entity": "Jane Doe"}
        ),
    )
    assert code == 0
    assert "warning" in result or result.get("type") in ("outreach_reply", "contact_note")
    # Note written somewhere (no crash)


# ---------------------------------------------------------------------------
# Tests: pipeline_note
# ---------------------------------------------------------------------------

def test_pipeline_note_appends_to_notes_cell(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Series B confirmed",
        "--destinations", dest_json(
            {"type": "pipeline_note", "entity": "Acme Corp", "file": "data/job-pipeline.md"}
        ),
    )
    assert code == 0
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "Series B confirmed" in content
    # Should be in the Notes cell, not a new row
    rows = [l for l in content.splitlines() if l.startswith("| Acme Corp |")]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Tests: company_note
# ---------------------------------------------------------------------------

def test_company_note_creates_file_if_absent(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Office in SF Dogpatch area",
        "--destinations", dest_json(
            {"type": "company_note", "entity": "Acme Corp", "slug": "acme-corp"}
        ),
    )
    assert code == 0
    note_path = tmp_path / "data/company-notes/acme-corp.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Office in SF Dogpatch area" in content


def test_company_note_prepends_to_existing(tmp_path):
    write_fixture(tmp_path, "data/company-notes/acme-corp.md", """\
# Acme Corp — Notes

> Running log.

---

## 2026-01-01 | General
Old note here.
""")
    run_apply(
        "--repo-root", str(tmp_path),
        "--note", "New observation",
        "--destinations", dest_json(
            {"type": "company_note", "entity": "Acme Corp", "slug": "acme-corp"}
        ),
    )
    content = (tmp_path / "data/company-notes/acme-corp.md").read_text(encoding="utf-8")
    pos_new = content.index("New observation")
    pos_old = content.index("Old note here")
    assert pos_new < pos_old


# ---------------------------------------------------------------------------
# Tests: profile_update
# ---------------------------------------------------------------------------

def test_profile_update_appends_to_session_notes(tmp_path):
    write_fixture(tmp_path, "data/profile.md", PROFILE_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Comp floor is $130K not $140K",
        "--destinations", dest_json(
            {"type": "profile_update", "file": "data/profile.md"}
        ),
    )
    assert code == 0
    content = (tmp_path / "data/profile.md").read_text(encoding="utf-8")
    assert "Comp floor is $130K not $140K" in content
    assert "## Session Notes" in content


# ---------------------------------------------------------------------------
# Tests: decision / general_note
# ---------------------------------------------------------------------------

def test_decision_appends_under_decisions(tmp_path):
    # decision now routes to the dated data/decisions.md log (newest-first),
    # not to a ## Decisions section in notes.md (routing changed; see
    # remember_apply LOG_HEADERS + CLAUDE.md decisions-log boundary).
    run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Decided not to pursue Lumen",
        "--destinations", dest_json({"type": "decision"}),
    )
    content = (tmp_path / "data/decisions.md").read_text(encoding="utf-8")
    assert "# Job-Search Decisions Log" in content      # log created with header
    assert "Decided not to pursue Lumen" in content
    # entry sits under a dated header below the NEW-ENTRIES marker
    assert content.index("Decided not to pursue Lumen") > content.index("NEW ENTRIES BELOW")


def test_general_note_appends_under_notes(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Random thought about interviews",
        "--destinations", dest_json({"type": "general_note"}),
    )
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    notes_pos = content.index("## Notes")
    note_pos = content.index("Random thought about interviews")
    assert note_pos > notes_pos


# ---------------------------------------------------------------------------
# Tests: raw_capture
# ---------------------------------------------------------------------------

def test_raw_capture_creates_inbox_file(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Check out Northwind careers",
        "--destinations", dest_json({"type": "raw_capture"}),
    )
    assert code == 0
    inbox_files = list((tmp_path / "inbox").glob("*.md"))
    assert len(inbox_files) == 1
    content = inbox_files[0].read_text(encoding="utf-8")
    assert "Check out Northwind careers" in content


# ---------------------------------------------------------------------------
# Tests: multi-destination
# ---------------------------------------------------------------------------

def test_multi_destination_write(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane at Acme is interested",
        "--destinations", dest_json(
            {"type": "contact_note", "entity": "Jane Doe"},
            {"type": "general_note"},
        ),
    )
    assert code == 0
    assert result["action"] == "multi_write"
    assert len(result["results"]) == 2
    assert all(r["status"] == "ok" for r in result["results"])


# ---------------------------------------------------------------------------
# Tests: error cases
# ---------------------------------------------------------------------------

def test_malformed_json_returns_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "test note",
        "--destinations", "not valid json",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "invalid_json"


def test_dry_run_returns_no_file_change(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    original = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "--note", "would be written",
        "--destinations", dest_json({"type": "general_note"}),
    )
    assert code == 0
    assert result["dry_run"] is True
    after = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert after == original


def test_destinations_file_arg(tmp_path):
    """--destinations-file path reads JSON from file."""
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    dests_file = tmp_path / "dests.json"
    dests_file.write_text(dest_json({"type": "general_note"}), encoding="utf-8")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Written via dests file",
        "--destinations-file", str(dests_file),
    )
    assert code == 0
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "Written via dests file" in content
