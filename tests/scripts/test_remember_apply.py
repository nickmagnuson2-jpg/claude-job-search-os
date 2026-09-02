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
# Locks HANDLERS at 12 so a handler added/removed without updating the docstring,
# CLAUDE.md, and docs/methodology.md (all of which state the count) fails loudly.
# ---------------------------------------------------------------------------

def test_handler_count_is_twelve():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "remember_apply", str(TOOLS_DIR / "remember_apply.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.HANDLERS) == 12, sorted(mod.HANDLERS)


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


# ---------------------------------------------------------------------------
# Mutation-hardening additions (2026-08-26)
# Every assert carries a message: the instrument misclassifies bare asserts.
# ---------------------------------------------------------------------------

import importlib.util
import re as _re
from datetime import datetime as _dt


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "remember_apply_under_test", str(TOOLS_DIR / "remember_apply.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _today():
    return _dt.now().strftime("%Y-%m-%d")


def run_apply_env(*args, env_extra=None):
    """run_apply with extra environment variables (for vault-root routing)."""
    script_path = TOOLS_DIR / "remember_apply.py"
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(env_extra or {})
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            env=env, cwd=str(REPO_ROOT))
    out = result.stdout.strip()
    if out:
        return json.loads(out), result.returncode
    return {"_stderr": result.stderr}, result.returncode


# --- contact_note -----------------------------------------------------------

def test_contact_note_creates_section_when_contact_absent(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Met Rex Random at a meetup",
        "--destinations", dest_json({"type": "contact_note", "entity": "Rex Random"}),
    )
    assert code == 0, f"unknown contact should still succeed, got {result}"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "### Rex Random" in content, "a new ### section must be appended for an unknown contact"
    assert f"#### {_today()} | remember | Met Rex Random at a meetup" in content, \
        "the entry must be a canonical #### interaction header"


def test_contact_note_missing_networking_file_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied",
        "--destinations", dest_json({"type": "contact_note", "entity": "Jane Doe"}),
    )
    assert code == 1, f"missing networking.md must exit 1, got {code}: {result}"
    assert result["status"] == "error", "status must be error"
    assert "networking.md not found or empty" in result["message"], \
        f"message must name the missing file, got {result['message']}"
    assert result["code"] == "handler_error", \
        f"handler errors without an explicit code map to handler_error, got {result['code']}"


def test_contact_note_collapses_multiline_note_into_one_header(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "line one\nline two   line three",
        "--destinations", dest_json({"type": "contact_note", "entity": "Jane Doe"}),
    )
    assert code == 0, "multi-line note must be accepted"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert f"#### {_today()} | remember | line one line two line three" in content, \
        "whitespace/newlines must be collapsed into a single header line"


def test_contact_note_matches_archived_heading_and_preserves_trailing_newline(tmp_path):
    write_fixture(tmp_path, "data/networking.md",
                  "# Networking\n\n## Interaction Log\n\n### [ARCHIVED] Jane Doe — Acme\n\n")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "archived contact resurfaced",
        "--destinations", dest_json({"type": "contact_note", "entity": "Jane Doe"}),
    )
    assert code == 0, "archived heading must still match"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert content.endswith("\n"), "a file that ended with a newline must still end with one"
    idx_head = content.index("### [ARCHIVED] Jane Doe")
    idx_entry = content.index("archived contact resurfaced")
    assert idx_entry > idx_head, "entry must land inside the matched section"
    assert "### Jane Doe\n" not in content, "must not append a duplicate section"


# --- outreach_reply ---------------------------------------------------------

OUTREACH_TWO_ROWS = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-01-10 | cold-outreach | email | Jane Doe | Acme Corp | First ping | Sent |
| 2026-02-10 | follow-up | email | Jane Doe | Acme Corp | Second ping | Drafted |
"""


def test_outreach_reply_flips_the_last_matching_row_only(tmp_path):
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_TWO_ROWS)
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied",
        "--destinations", dest_json({"type": "outreach_reply", "entity": "Jane Doe"}),
    )
    assert code == 0, "outreach reply must succeed"
    content = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    statuses = [[c.strip() for c in l.strip("|").split("|")][6]
                for l in content.splitlines() if "Jane Doe" in l]
    assert statuses == ["Sent", "Replied"], \
        f"only the most recent Sent/Drafted row flips, got {statuses}"
    assert content.endswith("\n"), "trailing newline must be preserved"


def test_outreach_reply_ignores_replied_rows_and_falls_back(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    write_fixture(tmp_path, "data/outreach-log.md",
                  "# Outreach Log\n\n"
                  "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |\n"
                  "| --- | --- | --- | --- | --- | --- | --- |\n"
                  "| 2026-01-10 | cold-outreach | email | Jane Doe | Acme | Hi | Replied |\n")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied again",
        "--destinations", dest_json({"type": "outreach_reply", "entity": "Jane Doe"}),
    )
    assert code == 0, "fallback path must succeed"
    assert result["warning"] == "No Sent/Drafted outreach row found for Jane Doe — logged to networking.md", \
        f"unexpected warning: {result.get('warning')}"
    assert result["action"] == "contact_note", \
        f"fallback writes a contact_note, got {result['action']}"
    net = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "Jane replied again" in net, "note must land in networking.md"


def test_outreach_reply_missing_log_falls_back_with_its_own_warning(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied",
        "--destinations", dest_json({"type": "outreach_reply", "entity": "Jane Doe"}),
    )
    assert code == 0, "missing outreach log must fall back, not fail"
    assert result["warning"] == "No outreach-log.md found — logged to networking.md", \
        f"unexpected warning: {result.get('warning')}"


def test_outreach_reply_skips_rows_with_too_few_columns(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    write_fixture(tmp_path, "data/outreach-log.md",
                  "# Outreach Log\n\n"
                  "| Date | Recipient | Status |\n"
                  "| --- | --- | --- |\n"
                  "| 2026-01-10 | Jane Doe | Sent |\n")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Jane replied",
        "--destinations", dest_json({"type": "outreach_reply", "entity": "Jane Doe"}),
    )
    assert code == 0, "short rows must not crash"
    assert "warning" in result, "a 3-column row is not a valid outreach row, so it must fall back"
    log = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    assert "Replied" not in log, "a malformed row must never be rewritten"


# --- pipeline_note ----------------------------------------------------------

def test_pipeline_note_writes_dated_prefix_into_notes_column(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Series B confirmed",
        "--destinations", dest_json({"type": "pipeline_note", "entity": "Acme Corp"}),
    )
    assert code == 0, "pipeline note must succeed"
    row = [l for l in (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
           if l.startswith("| Acme Corp |")][0]
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[6] == f"[{_today()}]: Series B confirmed", \
        f"Notes is column 6 and gets a dated prefix, got {cols[6]!r}"
    assert cols[2] == "Researching", "the Stage cell must be untouched"


@pytest.mark.xfail(strict=True, reason=(
    "OPEN TOOL DEFECT (found 2026-08-26, remember_apply.py:288). apply_pipeline_note "
    "appends with a literal ' | ' INSIDE a markdown table cell, which markdown reads as "
    "a new column. A second note on the same company therefore emits a 9-cell row under "
    "an 8-cell header and pushes the URL out of column 7 into column 8. Reproduced "
    "end-to-end on a scratch pipeline; latent on the live file (0 rows currently carry "
    "two dated notes). This test asserts the CORRECT behaviour and is expected to fail "
    "until the tool escapes the separator (\\| or a non-pipe delimiter). strict=True, so "
    "it turns RED the moment the tool is fixed and this marker must then be removed."
))
def test_pipeline_note_appends_to_existing_notes_with_separator(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD.replace(
        "| Research | — | — | — |", "| Research | — | prior note | — |"))
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "new info",
        "--destinations", dest_json({"type": "pipeline_note", "entity": "Acme Corp"}),
    )
    assert code == 0, "append path must succeed"
    row = [l for l in (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
           if l.startswith("| Acme Corp |")][0]
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[6] == f"prior note | [{_today()}]: new info", \
        f"existing note must be preserved and separated, got {cols[6]!r}"


def test_pipeline_note_unknown_company_is_error(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "nope",
        "--destinations", dest_json({"type": "pipeline_note", "entity": "Nowhere Inc"}),
    )
    assert code == 1, f"unknown company must exit 1, got {code}"
    assert result["message"] == "No pipeline entry found for: Nowhere Inc", \
        f"unexpected message: {result['message']}"
    assert "nope" not in (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8"), \
        "nothing may be written when the company is not found"


def test_pipeline_note_missing_file_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "nope",
        "--destinations", dest_json({"type": "pipeline_note", "entity": "Acme Corp"}),
    )
    assert code == 1, "missing pipeline file must exit 1"
    assert result["message"] == "job-pipeline.md not found or empty", \
        f"unexpected message: {result['message']}"


# --- company_note -----------------------------------------------------------

def test_company_note_missing_slug_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "something",
        "--destinations", dest_json({"type": "company_note", "entity": "Acme Corp"}),
    )
    assert code == 1, "a company_note without a slug must exit 1"
    assert result["message"] == "No slug in destination", \
        f"unexpected message: {result['message']}"


def test_company_note_new_file_header_and_entry_shape(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Office in SF",
        "--destinations", dest_json(
            {"type": "company_note", "entity": "Acme Corp", "slug": "acme-corp"}),
    )
    assert code == 0, "creating a new company note must succeed"
    content = (tmp_path / "data/company-notes/acme-corp.md").read_text(encoding="utf-8")
    assert content.startswith("# Acme Corp — Notes\n"), \
        f"header must name the entity, got {content[:40]!r}"
    assert f"## {_today()} | General\nOffice in SF\n" in content, \
        "entry must be a dated '| General' section followed by the note"
    assert "---" in content, "the new-file scaffold keeps its horizontal rule"


def test_company_note_falls_back_to_entity_when_slug_only(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "slug only",
        "--destinations", dest_json({"type": "company_note", "slug": "acme-corp"}),
    )
    assert code == 0, "slug-only destination must work"
    content = (tmp_path / "data/company-notes/acme-corp.md").read_text(encoding="utf-8")
    assert content.startswith("# acme-corp — Notes"), \
        f"entity defaults to slug, got {content[:30]!r}"


def test_company_note_appends_when_no_dated_section_exists(tmp_path):
    write_fixture(tmp_path, "data/company-notes/acme-corp.md",
                  "# Acme Corp — Notes\n\n> Running log.\n\n---\n")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "first dated entry",
        "--destinations", dest_json({"type": "company_note", "slug": "acme-corp"}),
    )
    assert code == 0, "append path must succeed"
    content = (tmp_path / "data/company-notes/acme-corp.md").read_text(encoding="utf-8")
    assert content.index("first dated entry") > content.index("Running log."), \
        "with no existing dated section the entry goes at the end"
    assert content.endswith("\n"), "file must end with a newline"


# --- profile_update ---------------------------------------------------------

def test_profile_update_creates_profile_when_absent(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "New fact",
        "--destinations", dest_json({"type": "profile_update"}),
    )
    assert code == 0, "missing profile.md must be created, not error"
    content = (tmp_path / "data/profile.md").read_text(encoding="utf-8")
    assert content.startswith("# Profile"), f"scaffold header missing: {content[:20]!r}"
    assert f"**{_today()}:** New fact" in content, "entry must be bold-dated"
    assert "## Session Notes" in content, "Session Notes section must be created"


def test_profile_update_inserts_at_end_of_session_notes_section(tmp_path):
    write_fixture(tmp_path, "data/profile.md",
                  "# Profile\n\n## Session Notes\n\n**2026-01-01:** older\n\n## Background\n\nstuff\n")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "newer fact",
        "--destinations", dest_json({"type": "profile_update"}),
    )
    assert code == 0, "profile update must succeed"
    content = (tmp_path / "data/profile.md").read_text(encoding="utf-8")
    assert content.index("older") < content.index("newer fact") < content.index("## Background"), \
        "the new entry belongs at the END of Session Notes, before the next ## section"


# --- notes.md / dated logs --------------------------------------------------

def test_general_note_creates_notes_scaffold_when_absent(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "a thought",
        "--destinations", dest_json({"type": "general_note"}),
    )
    assert code == 0, "missing notes.md must be created"
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert content.startswith("# Job Search Notes"), f"scaffold header missing: {content[:20]!r}"
    assert "## Decisions" in content and "## Notes" in content, \
        "both scaffold sections must exist"
    assert content.index("a thought") > content.index("## Notes"), \
        "a general_note goes under ## Notes, not ## Decisions"


def test_accomplishment_creates_its_own_log_with_header(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Sent 5 outreach emails",
        "--destinations", dest_json({"type": "accomplishment"}),
    )
    assert code == 0, "accomplishment must succeed"
    content = (tmp_path / "data/accomplishments.md").read_text(encoding="utf-8")
    assert content.startswith("# Job-Search Accomplishments Log"), \
        f"wrong log header: {content[:40]!r}"
    assert f"## {_today()}" in content, "entry must carry a dated ## header"
    assert content.index("Sent 5 outreach emails") > content.index("NEW ENTRIES BELOW"), \
        "entry must sit below the marker"
    assert not (tmp_path / "data/decisions.md").exists(), \
        "an accomplishment must not touch the decisions log"


def test_dated_log_is_newest_first(tmp_path):
    write_fixture(tmp_path, "data/decisions.md",
                  "# Job-Search Decisions Log\n\n"
                  "<!-- NEW ENTRIES BELOW (newest first) -->\n\n"
                  "## 2026-01-01\n\nolder decision\n")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "newer decision",
        "--destinations", dest_json({"type": "decision"}),
    )
    assert code == 0, "decision must succeed"
    content = (tmp_path / "data/decisions.md").read_text(encoding="utf-8")
    assert content.index("newer decision") < content.index("older decision"), \
        "new entries are prepended directly after the marker"
    assert content.endswith("newer decision\n") is False, \
        "the older entry must still be present at the end"
    assert content.endswith("\n"), "file ends with exactly one newline"
    assert not content.endswith("\n\n"), "trailing blank lines must be stripped"


def test_dated_log_without_marker_appends_at_end(tmp_path):
    write_fixture(tmp_path, "data/decisions.md", "# Decisions\n\n## 2026-01-01\n\nolder\n")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "newer",
        "--destinations", dest_json({"type": "decision"}),
    )
    assert code == 0, "marker-less log must still be written"
    content = (tmp_path / "data/decisions.md").read_text(encoding="utf-8")
    assert content.index("older") < content.index("newer"), \
        "with no marker the entry is appended at the end"


def test_dated_log_preserves_multiline_note_verbatim(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Decided: drop Lumen\n\nWhy: comp floor.\n",
        "--destinations", dest_json({"type": "decision"}),
    )
    assert code == 0, "multi-line decision must succeed"
    content = (tmp_path / "data/decisions.md").read_text(encoding="utf-8")
    assert "Decided: drop Lumen\n\nWhy: comp floor." in content, \
        "the structured body must be written verbatim, not collapsed"


# --- raw_capture ------------------------------------------------------------

def test_raw_capture_filename_and_body_shape(tmp_path):
    note = "Check out Northwind careers page soon, it looks promising"
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", note,
        "--destinations", dest_json({"type": "raw_capture"}),
    )
    assert code == 0, "raw capture must succeed"
    files = list((tmp_path / "inbox").glob("*.md"))
    assert len(files) == 1, f"exactly one inbox file, got {files}"
    name = files[0].name
    assert _re.match(r"^\d{8}-\d{6}-check-out-northwind-careers\.md$", name), \
        f"filename must be YYYYMMDD-HHMMSS + first 4 words slug, got {name}"
    content = files[0].read_text(encoding="utf-8")
    assert content.startswith(f"# {note[:80]}\n"), "title is the first 80 chars of the note"
    assert "*Route with `/act` when ready.*" in content, "routing footer must be present"
    # The title is `note[:80]`, so for a note under 80 chars the title IS the note and it
    # legitimately appears twice in the file. Count below the title line only -- that is
    # the property this asserts: the body is written once, not duplicated.
    below_title = content.split("\n", 1)[1]
    assert below_title.count(note) == 1, \
        f"the full note body must appear exactly once below the title, got {below_title.count(note)}"


def test_raw_capture_punctuation_only_note_uses_note_slug(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "!!! ???",
        "--destinations", dest_json({"type": "raw_capture"}),
    )
    assert code == 0, "punctuation-only note must not crash"
    files = list((tmp_path / "inbox").glob("*.md"))
    assert files[0].name.endswith("-note.md"), \
        f"slug falls back to 'note' when no words survive, got {files[0].name}"


# --- source_article ---------------------------------------------------------

def test_source_article_creates_voice_pure_entry(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "Great piece on pricing power https://example.com/a/b",
        "--destinations", dest_json({"type": "source_article"}),
    )
    assert code == 0, "source_article must succeed"
    files = list((tmp_path / "data/source-articles").glob("*.md"))
    assert len(files) == 1, f"exactly one article file, got {files}"
    assert files[0].name == f"{_dt.now().strftime('%Y%m%d')}-great-piece-on-pricing-power.md", \
        f"URL must be dropped from the slug, got {files[0].name}"
    content = files[0].read_text(encoding="utf-8")
    assert "> **URL:** https://example.com/a/b" in content, "the URL must be extracted into the header"
    assert "## My take (voice-pure)" in content, "voice-pure section must exist"
    assert "<!-- voice: pure-voice, my words only, not Claude's -->" in content, \
        "the voice marker must be present"
    assert "## Claude's read\n\n_(pending)_" in content, "Claude's read starts pending"
    assert content.rstrip().endswith("## Connections"), "Connections stub is last"


def test_source_article_url_defaults_to_todo(tmp_path):
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "no link here at all",
        "--destinations", dest_json({"type": "source_article"}),
    )
    assert code == 0, "note without URL must still work"
    content = list((tmp_path / "data/source-articles").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "> **URL:** TODO" in content, "missing URL becomes TODO"


def test_source_article_does_not_clobber_same_day_duplicate(tmp_path):
    for _ in range(2):
        _r, code = run_apply(
            "--repo-root", str(tmp_path),
            "--note", "same headline twice",
            "--destinations", dest_json({"type": "source_article"}),
        )
        assert code == 0, "both captures must succeed"
    names = sorted(p.name for p in (tmp_path / "data/source-articles").glob("*.md"))
    stamp = _dt.now().strftime("%Y%m%d")
    assert names == [f"{stamp}-same-headline-twice-2.md", f"{stamp}-same-headline-twice.md"], \
        f"the second capture must get a -2 suffix, got {names}"


def test_source_article_strips_leading_label_and_truncates_title(tmp_path):
    long_note = "interesting: alpha beta gamma delta epsilon zeta eta theta iota kappa"
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", long_note,
        "--destinations", dest_json({"type": "source_article"}),
    )
    assert code == 0, "labelled note must succeed"
    f = list((tmp_path / "data/source-articles").glob("*.md"))[0]
    assert f.name.endswith("-alpha-beta-gamma-delta-epsilon-zeta.md"), \
        f"the 'interesting:' label must be stripped and slug capped at 6 words, got {f.name}"
    title = f.read_text(encoding="utf-8").splitlines()[0]
    assert title == "# alpha beta gamma delta epsilon zeta eta theta...", \
        f"title is 8 words with an ellipsis, got {title!r}"


def test_title_helper_truncates_on_char_budget_and_handles_empty():
    mod = _load_module()
    assert mod._title_from_note("") == "Untitled", "an empty note gets the Untitled fallback"
    long_words = "abcdefghij " * 8
    title = mod._title_from_note(long_words)
    assert title.endswith("..."), "over-budget titles get an ellipsis"
    assert len(title) <= 73, f"title must respect the 70-char budget + ellipsis, got {len(title)}"
    assert mod._title_from_note("park this - short one") == "short one", \
        "a 'park this -' label is stripped"
    assert mod._slug_from_note("") == "note", "an empty note slugs to 'note'"


# --- deferred_idea ----------------------------------------------------------

def test_deferred_idea_prepends_dated_block_to_inbox(tmp_path):
    write_fixture(tmp_path, "data/inbox.md",
                  "# Inbox\n\n<!-- Items captured via /remember. -->\n\n"
                  "## 2026-01-01 | older item\n\nold body\n")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "later: look into Northwind's pricing model",
        "--destinations", dest_json({"type": "deferred_idea"}),
    )
    assert code == 0, f"deferred idea must succeed, got {result}"
    assert result["action"] == "deferred_idea", f"wrong action: {result['action']}"
    content = (tmp_path / "data/inbox.md").read_text(encoding="utf-8")
    assert f"## {_today()} | look into Northwind's pricing model" in content, \
        "block header must be dated and use the label-stripped title"
    assert content.index("later: look into") < content.index("older item"), \
        "new entries are prepended above existing ones"
    assert not (tmp_path / "data/notes.md").exists(), \
        "a deferred idea must NOT fall back to notes.md"


def test_deferred_idea_reports_error_when_inbox_is_locked_out(tmp_path, monkeypatch):
    """A lock timeout must return a 'nothing written' error, never a partial write."""
    mod = _load_module()
    calls = {}

    def boom(path, block):
        calls["path"] = path
        raise mod.inbox_lock.LockTimeout("held by another process")

    monkeypatch.setattr(mod.inbox_lock, "prepend_entries", boom)
    res = mod.apply_deferred_idea("some idea", {}, tmp_path)
    assert res["status"] == "error", f"lock timeout must be an error, got {res}"
    assert res["type"] == "deferred_idea", "type must be preserved on the error"
    assert "nothing written" in res["message"], f"message must promise no partial write: {res}"
    assert "retry" in res["message"], "message must tell the caller to retry"
    assert calls["path"] == tmp_path / "data" / "inbox.md", \
        f"the inbox path must be data/inbox.md, got {calls.get('path')}"


# --- personal_capture -------------------------------------------------------

def test_personal_capture_writes_to_vault_and_never_to_repo(tmp_path):
    vault = tmp_path / "vault"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    result, code = run_apply_env(
        "--repo-root", str(repo),
        "--note", "  buy new running shoes  ",
        "--destinations", dest_json({"type": "personal_capture"}),
        env_extra={"PERSONAL_VAULT_ROOT": str(vault)},
    )
    assert code == 0, f"personal capture must succeed, got {result}"
    assert result["file"] == "<personal-vault>/data/inbox.md", \
        f"the absolute vault path must never be echoed, got {result['file']}"
    target = vault / "data" / "inbox.md"
    assert target.exists(), "the vault inbox must be created"
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# Inbox\n"), f"scaffold header missing: {content[:20]!r}"
    assert content.rstrip().endswith("— buy new running shoes"), \
        f"entry must be a stripped dated bullet, got {content.rstrip()[-60:]!r}"
    assert content.endswith("\n"), "file must end with a newline"
    assert list(repo.rglob("*.md")) == [], \
        f"NOTHING may be written into the job-search repo, found {list(repo.rglob('*.md'))}"


def test_personal_capture_appends_to_existing_vault_inbox(tmp_path):
    vault = tmp_path / "vault"
    (vault / "data").mkdir(parents=True)
    (vault / "data" / "inbox.md").write_text("# Inbox\n\n- earlier item\n", encoding="utf-8")
    _r, code = run_apply_env(
        "--repo-root", str(tmp_path / "repo"),
        "--note", "second item",
        "--destinations", dest_json({"type": "personal_capture"}),
        env_extra={"PERSONAL_VAULT_ROOT": str(vault)},
    )
    assert code == 0, "append must succeed"
    content = (vault / "data" / "inbox.md").read_text(encoding="utf-8")
    assert content.index("earlier item") < content.index("second item"), \
        "existing content must be preserved and the new entry appended"


def test_personal_capture_unconfigured_vault_is_a_loud_error(tmp_path, monkeypatch):
    """Unconfigured vault must fail, never silently write personal content into this repo."""
    mod = _load_module()
    import vault_paths
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    monkeypatch.setattr(vault_paths, "DEFAULT_CONFIG", tmp_path / "nope.conf")
    res = mod.apply_personal_capture("a personal note", {}, tmp_path)
    assert res["status"] == "error", f"must be an error, got {res}"
    assert res["code"] == "vault_unconfigured", f"wrong code: {res.get('code')}"
    assert "#personal capture not written" in res["message"], \
        f"message must say nothing was written, got {res['message']}"
    assert list(tmp_path.rglob("*.md")) == [], "no file may be created in the repo tree"


# --- dispatch / CLI ---------------------------------------------------------

def test_unknown_destination_type_is_reported(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "x",
        "--destinations", dest_json({"type": "bogus_type"}),
    )
    assert code == 1, "an unknown type is a single-result error and exits 1"
    assert result["message"] == "Unknown destination type: bogus_type", \
        f"unexpected message: {result['message']}"


def test_destination_without_type_defaults_to_general_note(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "untyped note",
        "--destinations", dest_json({"file": "whatever"}),
    )
    assert code == 0, f"a typeless destination defaults to general_note, got {result}"
    assert "untyped note" in (tmp_path / "data/notes.md").read_text(encoding="utf-8"), \
        "it must land in data/notes.md"


def test_multi_write_reports_partial_failure(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "half works",
        "--destinations", dest_json(
            {"type": "general_note"},
            {"type": "pipeline_note", "entity": "Nowhere Inc"},
        ),
    )
    assert code == 0, "multi_write reports partial failure via JSON, not exit code"
    assert result["action"] == "multi_write", f"wrong action: {result['action']}"
    assert result["has_errors"] is True, "has_errors must be True when one destination failed"
    assert result["summary"] == "Written to 1 of 2 destinations", \
        f"summary must count successes, got {result['summary']!r}"
    statuses = [r["status"] for r in result["results"]]
    assert statuses == ["ok", "error"], f"results keep per-destination order, got {statuses}"


def test_multi_write_all_ok_sets_has_errors_false(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "both work",
        "--destinations", dest_json({"type": "general_note"}, {"type": "accomplishment"}),
    )
    assert code == 0, "both destinations must succeed"
    assert result["has_errors"] is False, "has_errors must be False when nothing failed"
    assert result["summary"] == "Written to 2 of 2 destinations", \
        f"unexpected summary: {result['summary']!r}"


def test_handler_exception_is_caught_and_reported(tmp_path):
    """A raising handler must not crash the run; it becomes a per-destination error."""
    mod = _load_module()
    captured = {}
    mod.out_error = lambda msg, code="error", **kw: captured.update(message=msg, code=code)
    mod.HANDLERS["general_note"] = lambda n, d, r: (_ for _ in ()).throw(ValueError("kaboom"))
    mod.apply_destinations("x", [{"type": "general_note"}], tmp_path, False)
    assert captured["message"] == "kaboom", f"the exception text must surface, got {captured}"
    assert captured["code"] == "handler_error", f"wrong code: {captured.get('code')}"


def test_empty_destinations_list_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "x",
        "--destinations", "[]",
    )
    assert code == 1, "an empty destinations array must exit 1"
    assert result["code"] == "no_destinations", f"wrong code: {result['code']}"
    assert result["message"] == "No destinations provided", f"wrong message: {result['message']}"


def test_non_array_destinations_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "x",
        "--destinations", '{"type": "general_note"}',
    )
    assert code == 1, "a JSON object is not a destinations array"
    assert result["code"] == "invalid_destinations", f"wrong code: {result['code']}"
    assert result["message"] == "Destinations must be a JSON array", \
        f"wrong message: {result['message']}"


def test_missing_note_is_error(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--destinations", dest_json({"type": "general_note"}),
    )
    assert code == 1, "no note must exit 1"
    assert result["code"] == "missing_note", f"wrong code: {result['code']}"
    assert result["message"] == "Provide --note or --note-file", f"wrong message: {result['message']}"


def test_missing_destinations_is_error(tmp_path):
    result, code = run_apply("--repo-root", str(tmp_path), "--note", "x")
    assert code == 1, "no destinations must exit 1"
    assert result["code"] == "missing_destinations", f"wrong code: {result['code']}"
    assert result["message"] == "Provide --destinations or --destinations-file", \
        f"wrong message: {result['message']}"


def test_note_file_arg_is_read_and_stripped(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    note_file = tmp_path / "note.txt"
    note_file.write_text("  note from a file  \n", encoding="utf-8")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note-file", str(note_file),
        "--destinations", dest_json({"type": "general_note"}),
    )
    assert code == 0, "--note-file must be accepted"
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert f"**{_today()}:** note from a file" in content, \
        "the file's text must be stripped before writing"


def test_note_file_beats_inline_note(tmp_path):
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    note_file = tmp_path / "note.txt"
    note_file.write_text("from file", encoding="utf-8")
    _r, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "from flag",
        "--note-file", str(note_file),
        "--destinations", dest_json({"type": "general_note"}),
    )
    assert code == 0, "both flags together must work"
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "from file" in content and "from flag" not in content, \
        "--note-file takes precedence over --note"


def test_destinations_file_beats_inline_destinations(tmp_path):
    dfile = tmp_path / "d.json"
    dfile.write_text(dest_json({"type": "accomplishment"}), encoding="utf-8")
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--note", "won something",
        "--destinations", dest_json({"type": "general_note"}),
        "--destinations-file", str(dfile),
    )
    assert code == 0, "both destination flags together must work"
    assert result["action"] == "accomplishment", \
        f"--destinations-file takes precedence, got {result['action']}"
    assert not (tmp_path / "data/notes.md").exists(), "the inline destination must be ignored"


def test_dry_run_lists_would_mutate_targets(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "--note", "x",
        "--destinations", dest_json(
            {"type": "general_note", "file": "data/notes.md"},
            {"type": "accomplishment"},
        ),
    )
    assert code == 0, "dry run must exit 0"
    assert result["action"] == "dry_run", f"wrong action: {result['action']}"
    assert result["summary"] == "Would write to 2 destination(s)", \
        f"wrong summary: {result['summary']!r}"
    assert result["would_mutate"] == ["data/notes.md", "accomplishment"], \
        f"would_mutate falls back to the type when no file is given, got {result['would_mutate']}"


def test_dry_run_with_empty_destinations_still_reports_zero(tmp_path):
    result, code = run_apply(
        "--repo-root", str(tmp_path), "--dry-run", "--note", "x", "--destinations", "[]")
    assert code == 0, "dry run short-circuits before the empty-destinations check"
    assert result["summary"] == "Would write to 0 destination(s)", \
        f"wrong summary: {result['summary']!r}"


def test_repo_root_defaults_to_cwd(tmp_path):
    """Without --repo-root the tool writes relative to the process cwd."""
    script_path = TOOLS_DIR / "remember_apply.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--note", "cwd routed",
         "--destinations", dest_json({"type": "accomplishment"})],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(tmp_path))
    assert proc.returncode == 0, f"run failed: {proc.stderr}"
    assert (tmp_path / "data/accomplishments.md").exists(), \
        "with no --repo-root the write must land under cwd"


# --- low-level helpers ------------------------------------------------------

def test_row_helpers_classify_table_lines():
    mod = _load_module()
    assert mod.is_sep_row("| --- | --- |") is True, "a dashed row is a separator"
    assert mod.is_sep_row("| :-: | ---: |") is True, "aligned separators count too"
    assert mod.is_sep_row("| Acme | Corp |") is False, "a data row is not a separator"
    assert mod.is_data_row_generic("| Acme | Corp |") is True, "a leading pipe means data row"
    assert mod.is_data_row_generic("Acme | Corp") is False, "no leading pipe means not a row"
    assert mod.is_data_row_generic("| --- | --- |") is False, "separators are excluded"
    assert mod.parse_cols("| a | b |  c |") == ["a", "b", "c"], "cells are split and stripped"


def test_find_and_ensure_section_helpers():
    mod = _load_module()
    lines = ["# T", "", "## Alpha", "a", "## Beta", "b"]
    assert mod.find_section(lines, r"^##\s+Alpha") == (2, 4), \
        "find_section returns [start, next-##) bounds"
    assert mod.find_section(lines, r"^##\s+Missing") == (-1, -1), \
        "a missing section returns (-1, -1)"
    assert mod.find_section(lines, r"^##\s+beta") == (4, 6), \
        "matching is case-insensitive and runs to end of doc"
    assert mod.section_end(lines, 2) == 4, "section_end stops at the next ## heading"
    assert mod.section_end(lines, 4) == 6, "the last section ends at the end of the doc"
    grow = list(lines)
    assert mod.ensure_section(grow, "Alpha") == 2, "an existing section is returned as-is"
    assert len(grow) == len(lines), "no lines are added when the section exists"


@pytest.mark.xfail(strict=True, reason=(
    "OPEN TOOL DEFECT (found 2026-08-26, remember_apply.py:130-139). ensure_section's "
    "docstring promises 'Returns its start index', and the existing-section path returns "
    "the heading's index -- but the create path appends ['', '## H', ''] and returns "
    "len-3, which points at the BLANK LINE before the heading. The two paths disagree by "
    "one. Harmless today only because ensure_section has ZERO callers (`grep -n "
    "ensure_section tools/*.py` finds only the definition): it is dead code, and is "
    "listed for removal rather than removed here. This test asserts the documented "
    "contract. strict=True, so it turns RED if the tool is fixed OR the helper deleted."
))
def test_ensure_section_create_path_returns_the_heading_index(tmp_path):
    mod = _load_module()
    grow = ["# T", "", "## Alpha", "a", "## Beta", "b"]
    idx = mod.ensure_section(grow, "Gamma")
    assert grow[idx] == "## Gamma", f"a new heading is appended, got {grow[idx]!r}"
    assert idx == len(grow) - 2, "the returned index points at the heading, with a blank after"


def test_read_file_returns_empty_string_for_missing_path(tmp_path):
    mod = _load_module()
    assert mod.read_file(tmp_path / "nope.md") == "", "a missing file reads as empty, not an exception"
    assert mod.read_file(tmp_path) == "", "a directory reads as empty too"


def test_write_atomic_creates_parents_and_leaves_no_tmp(tmp_path):
    mod = _load_module()
    target = tmp_path / "a" / "b" / "c.md"
    mod.write_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello", "content must be written"
    assert list(tmp_path.rglob("*.tmp")) == [], "the temp file must be renamed away"
