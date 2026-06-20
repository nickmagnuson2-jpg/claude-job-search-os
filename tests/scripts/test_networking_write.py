"""Tests for tools/networking_write.py"""
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

def run_nw_write(*args, tmp_path=None):
    script_path = TOOLS_DIR / "networking_write.py"
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(REPO_ROOT),
    )
    return json.loads(result.stdout), result.returncode


# ---------------------------------------------------------------------------
# Minimal networking fixtures
# ---------------------------------------------------------------------------

NETWORKING_EMPTY = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |

## Interaction Log
"""

NETWORKING_WITH_CONTACT = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jane Doe — Acme Corp

"""

NETWORKING_WITH_LOG = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | 2026-01-15 | — |

## Interaction Log

### Jane Doe — Acme Corp

#### 2026-01-15 | email | First outreach

**Follow-up:** Wait for response

"""

NETWORKING_NO_LOG_SECTION = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |
"""


# ---------------------------------------------------------------------------
# Tests: add
# ---------------------------------------------------------------------------

def test_add_new_contact(tmp_path):
    """Adds a row to Contacts table and a stub to Interaction Log."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "add", "Alice Smith",
        "--company", "Beta Inc",
        "--role", "Director",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "add"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Alice Smith |" in content
    assert "Beta Inc" in content
    assert "### Alice Smith" in content


def test_add_duplicate_returns_warning(tmp_path):
    """Second add for same name returns warning, no duplicate row."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "add", "Jane Doe",
        "--company", "Rival Co",
    )
    assert code == 0
    assert result["action"] == "duplicate_warning"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Jane Doe |")]
    assert len(rows) == 1


def test_add_creates_interaction_log_section_if_absent(tmp_path):
    """If ## Interaction Log is missing, add creates it."""
    write_fixture(tmp_path, "data/networking.md", """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
""")
    run_nw_write(
        "--repo-root", str(tmp_path),
        "add", "Bob", "--company", "StartupX"
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "## Interaction Log" in content
    assert "### Bob" in content


# ---------------------------------------------------------------------------
# Tests: log
# ---------------------------------------------------------------------------

def test_log_appends_entry(tmp_path):
    """Log command creates an interaction entry in the contact's section."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe",
        "--date", "2026-02-01",
        "--type", "email",
        "--summary", "Replied to coffee invite",
    )
    assert code == 0
    assert result["status"] == "ok"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "#### 2026-02-01 | email | Replied to coffee invite" in content


def test_log_newest_entry_at_top(tmp_path):
    """Two log calls — the newer entry appears above the older one."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_LOG)
    run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe",
        "--date", "2026-02-20",
        "--type", "coffee",
        "--summary", "Had coffee",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    pos_new = content.index("2026-02-20")
    pos_old = content.index("2026-01-15")
    assert pos_new < pos_old


def test_log_updates_last_interaction_date(tmp_path):
    """Logging updates the Last Interaction column in the Contacts table."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe",
        "--date", "2026-03-01",
        "--type", "call",
        "--summary", "Quick catch-up",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Jane Doe |")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[5] == "2026-03-01"  # Last Interaction


def test_log_with_content_uses_blockquote(tmp_path):
    """Content passed via --content is written as a blockquote."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe",
        "--date", "2026-02-10",
        "--type", "email",
        "--summary", "Cold outreach",
        "--content", "Hi Jane, I'd love to connect.",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "> Hi Jane, I'd love to connect." in content


def test_log_with_followup_line(tmp_path):
    """Follow-up text appears in the entry."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe",
        "--date", "2026-02-10",
        "--type", "linkedin",
        "--summary", "Connected",
        "--followup", "Send cold outreach email next week",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "**Follow-up:** Send cold outreach email next week" in content


def test_log_unknown_contact_returns_error(tmp_path):
    """Logging for a contact not in Contacts table returns not_found error."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Ghost Person",
        "--date", "2026-02-01",
        "--type", "call",
        "--summary", "Phantom call",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "not_found"


# ---------------------------------------------------------------------------
# Tests: remove
# ---------------------------------------------------------------------------

def test_remove_archives_contact(tmp_path):
    """Remove deletes the Contacts row and prefixes section heading with [ARCHIVED]."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "remove", "Jane Doe",
    )
    assert code == 0
    assert result["status"] == "ok"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    # Row removed from Contacts table
    assert "| Jane Doe |" not in content
    # Section heading archived
    assert "### [ARCHIVED] Jane Doe" in content


def test_missing_file_returns_error(tmp_path):
    """Missing networking file returns JSON error, not a crash."""
    result, code = run_nw_write("--repo-root", str(tmp_path), "add", "Nobody")
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


def test_dry_run_returns_no_file_change(tmp_path):
    """--dry-run returns dry_run:true and does not write."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    original = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "add", "Test Person", "--company", "DryRunCo",
    )
    assert code == 0
    assert result["dry_run"] is True
    after = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert after == original


# ---------------------------------------------------------------------------
# Tests: outreach-log reply-flip (bug fix 2026-06-18 — who replied?)
# ---------------------------------------------------------------------------

OUTREACH_LOG_SENT = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-18 | follow-up | email | Jane Doe | Acme Corp | Re: intro | Sent |
"""


def _setup_outreach(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_LOG_SENT)


def test_outbound_reply_does_not_flip_outreach_log(tmp_path):
    """Logging Nick's OWN reply ('Replied to ...') must NOT flip the row to Replied."""
    _setup_outreach(tmp_path)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-18", "--type", "email reply",
        "--summary", "Replied to her intro - yes to a chat",
    )
    assert code == 0
    assert result["outreach_log_updated"] is False
    log = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    assert "| Sent |" in log
    assert "| Replied |" not in log


def test_inbound_reply_flips_outreach_log(tmp_path):
    """A genuine received reply ('Heard back ...') flips the row to Replied."""
    _setup_outreach(tmp_path)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "Heard back from Jane - wants to schedule",
    )
    assert code == 0
    assert result["outreach_log_updated"] is True
    log = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    assert "| Replied |" in log


def test_inbound_subject_wins_over_outbound_marker(tmp_path):
    """'She replied to my email' is inbound even though it contains 'replied to'."""
    _setup_outreach(tmp_path)
    result, _ = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "She replied to my email with availability",
    )
    assert result["outreach_log_updated"] is True


def test_reply_received_flag_forces_flip(tmp_path):
    """--reply-received forces the flip even with outbound-looking phrasing."""
    _setup_outreach(tmp_path)
    result, _ = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "Replied to her note", "--reply-received",
    )
    assert result["outreach_log_updated"] is True


def test_no_reply_flip_flag_suppresses(tmp_path):
    """--no-reply-flip suppresses the flip even with inbound-looking phrasing."""
    _setup_outreach(tmp_path)
    result, _ = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "Heard back from Jane", "--no-reply-flip",
    )
    assert result["outreach_log_updated"] is False
    log = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    assert "| Sent |" in log
