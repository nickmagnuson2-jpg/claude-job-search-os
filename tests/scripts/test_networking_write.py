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
        "log", "Jane Doe", "--direction", "none",
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
        "log", "Jane Doe", "--direction", "none",
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
        "log", "Jane Doe", "--direction", "none",
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
        "log", "Jane Doe", "--direction", "none",
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
        "log", "Jane Doe", "--direction", "none",
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
        "log", "Ghost Person", "--direction", "none",
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


def test_dry_run_reports_fuzzy_duplicate(tmp_path):
    """--dry-run must surface the SAME possible_duplicate the real run would block on,
    not a blind 'Would add'. Regression for fable-audit Theme 2: the dry-run branch
    returned before the exact/fuzzy dedup checks ran."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    # "Jane Doe" already exists; "Jane Doh" is a fuzzy variant.
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "add", "Jane Doh", "--company", "Acme Corp",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "possible_duplicate"


def test_dry_run_reports_exact_duplicate(tmp_path):
    """--dry-run on an exact-name match reports duplicate_warning, not 'Would add'."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "add", "Jane Doe", "--company", "Acme Corp",
    )
    assert code == 0
    assert result["action"] == "duplicate_warning"


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


def _run_raw(*args):
    """For argparse failures: stdout is empty, so run_nw_write's json.loads cannot run."""
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))


def _log(tmp_path, direction, kind="email", summary="Heard back from Jane"):
    return run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe", "--date", "2026-06-19",
        "--type", kind, "--summary", summary, "--direction", direction, "--no-todo")


def _header(tmp_path):
    text = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("#### ")]


def test_log_without_direction_is_refused_and_writes_nothing(tmp_path):
    """Nick, 2026-10-30: the direction is required, never guessed from the summary."""
    _setup_outreach(tmp_path)
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    r = _run_raw("--repo-root", tmp_path, "log", "Jane Doe", "--date", "2026-06-19",
                 "--type", "email", "--summary", "Heard back from Jane", "--no-todo")
    assert r.returncode != 0
    assert "--direction" in r.stderr
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before
    assert "| Sent |" in (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("flag", ["--reply-received", "--no-reply-flip"])
def test_the_old_override_flags_are_gone(tmp_path, flag):
    _setup_outreach(tmp_path)
    r = _run_raw("--repo-root", tmp_path, "log", "Jane Doe", "--summary", "hi",
                 "--direction", "none", "--no-todo", flag)
    assert r.returncode != 0 and "unrecognized arguments" in r.stderr


def test_an_unknown_direction_is_refused(tmp_path):
    _setup_outreach(tmp_path)
    r = _run_raw("--repo-root", tmp_path, "log", "Jane Doe", "--summary", "hi",
                 "--direction", "inbound", "--no-todo")
    assert r.returncode != 0 and "invalid choice" in r.stderr


def test_direction_in_marks_the_type_and_flips_the_outreach_log(tmp_path):
    _setup_outreach(tmp_path)
    result, code = _log(tmp_path, "in", summary="Asked for a resume")
    assert code == 0 and result["outreach_log_updated"] is True
    assert _header(tmp_path) == ["#### 2026-06-19 | email (inbound) | Asked for a resume"]
    assert "| Replied |" in (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")


def test_direction_out_marks_the_type_and_never_flips(tmp_path):
    """Even a summary that reads like a reply: the caller said it went out."""
    _setup_outreach(tmp_path)
    result, code = _log(tmp_path, "out", summary="Heard back from Jane, replied with times")
    assert code == 0 and result["outreach_log_updated"] is False
    assert _header(tmp_path) == [
        "#### 2026-06-19 | email (outbound) | Heard back from Jane, replied with times"]
    assert "| Sent |" in (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")


def test_direction_none_keeps_the_type_plain_and_never_flips(tmp_path):
    _setup_outreach(tmp_path)
    result, code = _log(tmp_path, "none", summary="Heard back from Jane")
    assert code == 0 and result["outreach_log_updated"] is False
    assert _header(tmp_path) == ["#### 2026-06-19 | email | Heard back from Jane"]


def test_direction_both_marks_two_way_and_flips(tmp_path):
    """Nick, 2026-10-30: a note can record a message each way; the contact replied, so
    the outreach row flips."""
    _setup_outreach(tmp_path)
    result, code = _log(tmp_path, "both", summary="Sent times, Jane replied")
    assert code == 0 and result["outreach_log_updated"] is True
    assert _header(tmp_path) == ["#### 2026-06-19 | email (two-way) | Sent times, Jane replied"]


def test_a_type_that_already_says_inbound_gets_no_second_suffix(tmp_path):
    _setup_outreach(tmp_path)
    result, code = _log(tmp_path, "in", kind="email (inbound)", summary="Asked for a call")
    assert code == 0 and result["outreach_log_updated"] is True
    assert _header(tmp_path) == ["#### 2026-06-19 | email (inbound) | Asked for a call"]


@pytest.mark.parametrize("kind,direction", [
    ("email (inbound)", "out"), ("email (inbound)", "none"), ("inbound", "none"),
    ("email (outbound)", "in"), ("email (outbound)", "none"),
])
def test_a_type_that_contradicts_the_direction_is_refused(tmp_path, kind, direction):
    _setup_outreach(tmp_path)
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    result, code = _log(tmp_path, direction, kind=kind)
    assert code != 0 and result["status"] == "error"
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before


def test_dry_run_shows_the_marked_type(tmp_path):
    _setup_outreach(tmp_path)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "--dry-run", "log", "Jane Doe", "--date", "2026-06-19",
        "--type", "text", "--summary", "Asked for a call", "--direction", "in")
    assert code == 0
    assert result["would_mutate"][0]["entry"] == "#### 2026-06-19 | text (inbound) | Asked for a call"


# ---------------------------------------------------------------------------
# --email on `add`
# ---------------------------------------------------------------------------

def test_add_records_email_when_given(tmp_path):
    """The Email column already exists in the schema and fmt_contact_row already
    accepts it; before this flag there was no way to populate it from the CLI, so
    every contact added programmatically had a permanently empty Email cell."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path),
        "add", "Alice Smith",
        "--company", "Beta Inc",
        "--email", "alice@example.com",
    )
    assert code == 0 and result["status"] == "ok"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    row = next(l for l in content.splitlines() if l.startswith("| Alice Smith |"))
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[6] == "alice@example.com"


def test_add_without_email_keeps_placeholder(tmp_path):
    """Existing callers must be unaffected."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    run_nw_write("--repo-root", str(tmp_path), "add", "Bob Jones", "--company", "Gamma")
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    row = next(l for l in content.splitlines() if l.startswith("| Bob Jones |"))
    cols = [c.strip() for c in row.strip("|").split("|")]
    assert cols[6] == "—"


def test_add_email_survives_a_dry_run_without_writing(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "--dry-run",
        "add", "Carol Lee", "--email", "carol@example.com",
    )
    assert code == 0
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before
    assert "carol@example.com" in json.dumps(result)


def test_add_rejects_a_malformed_email(tmp_path):
    """A pipe would break the table row; a missing @ is almost always a typo."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "add", "Dave Kim", "--email", "not-an-email",
    )
    assert code != 0
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# `update` subcommand
# ---------------------------------------------------------------------------

NETWORKING_FOR_UPDATE = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Alice Smith | Beta Inc | Director | peer | 2026-01-05 | 2026-02-01 | a@beta.com |
| Bob Jones | Gamma LLC | VP | recruiter | 2026-01-06 | — | — |

## Interaction Log

### Alice Smith — Beta Inc

- 2026-02-01 | email | said hello

### Bob Jones — Gamma LLC
"""


def _row(tmp_path, name):
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    line = next(l for l in content.splitlines() if l.startswith(f"| {name} |"))
    return [c.strip() for c in line.strip("|").split("|")]


def test_update_changes_only_the_named_fields(tmp_path):
    """Before this existed, backfilling one field meant remove + re-add, which
    left an [ARCHIVED] stub in the Interaction Log to clean up by hand."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    result, code = run_nw_write("--repo-root", str(tmp_path),
                                "update", "Bob Jones", "--email", "bob@gamma.com")
    assert code == 0 and result["status"] == "ok"
    cols = _row(tmp_path, "Bob Jones")
    assert cols[6] == "bob@gamma.com"
    assert cols[1] == "Gamma LLC"      # untouched
    assert cols[2] == "VP"             # untouched
    assert cols[3] == "recruiter"      # untouched
    assert cols[4] == "2026-01-06"     # Added never changes


def test_update_leaves_other_contacts_alone(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    run_nw_write("--repo-root", str(tmp_path), "update", "Bob Jones", "--role", "SVP")
    assert _row(tmp_path, "Alice Smith") == [
        "Alice Smith", "Beta Inc", "Director", "peer",
        "2026-01-05", "2026-02-01", "a@beta.com"]


def test_update_company_also_fixes_the_interaction_log_header(tmp_path):
    """The log header embeds the company; leaving it stale silently desyncs the
    two halves of the file."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    run_nw_write("--repo-root", str(tmp_path),
                 "update", "Alice Smith", "--company", "Delta Co")
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "### Alice Smith — Delta Co" in content
    assert "### Alice Smith — Beta Inc" not in content
    assert "- 2026-02-01 | email | said hello" in content   # history preserved


def test_update_unknown_contact_errors(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    result, code = run_nw_write("--repo-root", str(tmp_path),
                                "update", "Nobody Here", "--role", "X")
    assert code != 0
    assert result["status"] == "error"


def test_update_with_no_fields_errors(tmp_path):
    """A no-op update that reports ok is a silent-success bug."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    result, code = run_nw_write("--repo-root", str(tmp_path), "update", "Bob Jones")
    assert code != 0
    assert result["status"] == "error"


def test_update_validates_email(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    result, code = run_nw_write("--repo-root", str(tmp_path),
                                "update", "Bob Jones", "--email", "nope")
    assert code != 0 and result["status"] == "error"


def test_update_rejects_a_pipe_in_any_field(tmp_path):
    """A pipe splits the row and shifts every later column."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    result, code = run_nw_write("--repo-root", str(tmp_path),
                                "update", "Bob Jones", "--role", "VP | Ops")
    assert code != 0 and result["status"] == "error"


def test_update_dry_run_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_FOR_UPDATE)
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    result, code = run_nw_write("--repo-root", str(tmp_path), "--dry-run",
                                "update", "Bob Jones", "--email", "bob@gamma.com")
    assert code == 0 and result.get("dry_run") is True
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before


def test_reply_flip_without_an_outreach_log_reports_false(tmp_path):
    """No outreach log: the log entry still lands, and the flip reports False, not None."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "Heard back from Jane", "--direction", "in",
    )
    assert code == 0
    assert result["outreach_log_updated"] is False


def test_reply_flip_on_a_same_date_tie_marks_the_later_row(tmp_path):
    """Changed 2026-10-21 by decision: the log is chronological, so the later row of two
    open rows on one date is the later message. (The old copy kept the first.)"""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "data/outreach-log.md",
                  "# Outreach Log\n\n"
                  "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |\n"
                  "| --- | --- | --- | --- | --- | --- | --- |\n"
                  "| 2026-06-18 | cold-outreach | linkedin | Jane Doe | Acme | Morning DM | Sent |\n"
                  "| 2026-06-18 | follow-up | email | Jane Doe | Acme | Afternoon email | Sent |\n")
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe",
        "--date", "2026-06-19", "--type", "email",
        "--summary", "Heard back from Jane", "--direction", "in",
    )
    assert code == 0 and result["outreach_log_updated"] is True
    rows = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8").splitlines()[-2:]
    assert rows[0].endswith("| Sent |") and rows[1].endswith("| Replied |")



# The reply-phrasing cases that lived here moved to
# tests/scripts/test_reply_classification_consistency.py with the shared classifier.


# ---------------------------------------------------------------------------
# The follow-up to-do is a side effect on a SECOND file. It must be visible in
# the output, and a caller barred from that file must be able to switch it off.
# Origin 2026-10-28: four `log --followup` calls wrote data/job-todos.md during a
# session whose brief put that file off-limits, and the output never said so.
# ---------------------------------------------------------------------------

TODOS_FIXTURE = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Alpha task | High | 2026-08-01 | Pending | note one |

## Completed

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
"""


def _log_with_followup(tmp_path, *extra):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "data/job-todos.md", TODOS_FIXTURE)
    return run_nw_write(
        "--repo-root", str(tmp_path),
        "log", "Jane Doe", "--direction", "none",
        "--date", "2026-02-10",
        "--type", "email",
        "--summary", "Sent a note",
        "--followup", "Wait for her reply",
        *extra,
    )


def test_log_followup_reports_the_todo_it_wrote(tmp_path):
    result, code = _log_with_followup(tmp_path)
    assert code == 0
    todos = (tmp_path / "data/job-todos.md").read_text(encoding="utf-8")
    assert "Follow up: Jane Doe — Wait for her reply" in todos
    assert result["todo_added"] is True
    assert result["todos_file"] == "data/job-todos.md"


def test_log_no_todo_leaves_the_todos_file_untouched(tmp_path):
    result, code = _log_with_followup(tmp_path, "--no-todo")
    assert code == 0
    assert (tmp_path / "data/job-todos.md").read_text(encoding="utf-8") == TODOS_FIXTURE
    assert result["todo_added"] is False
    # the networking entry itself is still written
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "**Follow-up:** Wait for her reply" in content


def test_log_without_followup_reports_no_todo(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "data/job-todos.md", TODOS_FIXTURE)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe", "--direction", "none",
        "--date", "2026-02-10", "--type", "email", "--summary", "Sent a note",
    )
    assert code == 0 and result["todo_added"] is False
    assert (tmp_path / "data/job-todos.md").read_text(encoding="utf-8") == TODOS_FIXTURE


def test_log_followup_with_no_todos_file_reports_nothing_written(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_WITH_CONTACT)
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe", "--direction", "none",
        "--date", "2026-02-10", "--type", "email", "--summary", "Sent a note",
        "--followup", "Wait for her reply",
    )
    assert code == 0 and result["todo_added"] is False
    assert not (tmp_path / "data/job-todos.md").exists()


def test_a_second_followup_replaces_the_first_auto_row(tmp_path):
    _log_with_followup(tmp_path)
    run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe", "--direction", "none",
        "--date", "2026-02-12", "--type", "email", "--summary", "Sent another",
        "--followup", "Call her Monday instead",
    )
    todos = (tmp_path / "data/job-todos.md").read_text(encoding="utf-8")
    active = todos.split("## Completed")[0]
    assert "Follow up: Jane Doe — Call her Monday instead" in active
    assert "Follow up: Jane Doe — Wait for her reply" not in active


# ---------------------------------------------------------------------------
# A reply logged twice (2026-11-02): reply_classify.open_reply
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second,refused", [
    ("in", "in", True),      # the same reply again, e.g. /follow-up then /networking
    ("in", "both", True),
    ("both", "in", True),    # a two-way entry holds a reply, so this is usually the same one
    ("out", "in", False),    # Nick wrote after nothing; a reply now is the first one
    ("none", "in", False),   # a plain entry records no direction, so there is nothing open
])
def test_a_second_reply_with_no_send_between_is_refused(tmp_path, first, second, refused):
    _setup_outreach(tmp_path)
    r1, c1 = _log(tmp_path, first, summary="First")
    assert c1 == 0, r1
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    log_before = (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8")
    r2, c2 = _log(tmp_path, second, summary="Second")
    if refused:
        assert c2 != 0 and r2["code"] == "possible_duplicate_reply", r2
        assert r2["existing"] == {"date": "2026-06-19", "summary": "First",
                                  "type": {"in": "email (inbound)", "both": "email (two-way)"}[first]}
        assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before
        assert (tmp_path / "data/outreach-log.md").read_text(encoding="utf-8") == log_before
    else:
        assert c2 == 0, r2
        assert _header(tmp_path)[0].endswith("| Second")


def test_new_reply_logs_a_genuinely_new_message(tmp_path):
    _setup_outreach(tmp_path)
    _log(tmp_path, "in", summary="First")
    result, code = run_nw_write(
        "--repo-root", str(tmp_path), "log", "Jane Doe", "--date", "2026-06-20", "--type", "email",
        "--summary", "Second", "--direction", "in", "--new-reply", "--no-todo")
    assert code == 0, result
    assert _header(tmp_path)[:2] == ["#### 2026-06-20 | email (inbound) | Second",
                                     "#### 2026-06-19 | email (inbound) | First"]


def test_a_send_after_the_reply_reopens_logging(tmp_path):
    _setup_outreach(tmp_path)
    _log(tmp_path, "in", summary="Their reply")
    _log(tmp_path, "out", summary="My answer")
    result, code = _log(tmp_path, "in", summary="Their second reply")
    assert code == 0, result


def test_an_outbound_log_is_never_refused_after_a_reply(tmp_path):
    _setup_outreach(tmp_path)
    _log(tmp_path, "in", summary="Their reply")
    result, code = _log(tmp_path, "out", summary="My answer")
    assert code == 0, result


def test_the_check_reads_only_this_contacts_section(tmp_path):
    text = NETWORKING_WITH_CONTACT.replace(
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |",
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |\n"
        "| Sam Example | Beta Inc | VP | peer | 2026-01-01 | — | — |")
    text += "### Sam Example — Beta Inc\n\n#### 2026-06-18 | email (inbound) | Sam replied\n\n"
    write_fixture(tmp_path, "data/networking.md", text)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_LOG_SENT)
    result, code = _log(tmp_path, "in", summary="Jane replied")
    assert code == 0, result


def test_an_archived_section_still_counts_for_the_check(tmp_path):
    text = NETWORKING_WITH_CONTACT + "### [ARCHIVED] Jane Doe — Acme Corp\n\n#### 2026-06-01 | email (inbound) | Old reply\n\n"
    write_fixture(tmp_path, "data/networking.md", text)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_LOG_SENT)
    result, code = _log(tmp_path, "in", summary="Again")
    assert code != 0 and result["code"] == "possible_duplicate_reply"
    assert result["existing"]["summary"] == "Old reply"


# One cell format for the contacts table (table-writers, 2026-11-02)
# ---------------------------------------------------------------------------

def test_fmt_contact_row_escapes_a_pipe_in_every_cell():
    import importlib, sys as _s
    _s.path.insert(0, str(TOOLS_DIR)); nw = importlib.import_module("networking_write")
    row = nw.fmt_contact_row("A|B", "C|o", "R", "peer", "2026-01-01", "—", "a@b.test")
    assert row == "| A/B | C/o | R | peer | 2026-01-01 | — | a@b.test |"


def test_a_name_with_a_pipe_is_stored_and_found_the_same_way(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_EMPTY)
    r1, c1 = run_nw_write("--repo-root", str(tmp_path), "add", "Sam|Example", "--company", "Beta|Inc")
    assert c1 == 0, r1
    r2, c2 = run_nw_write("--repo-root", str(tmp_path), "log", "Sam|Example", "--date", "2026-06-19",
                          "--type", "email", "--summary", "Hi", "--direction", "none", "--no-todo")
    assert c2 == 0, r2
    text = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Sam/Example | Beta/Inc |" in text and "### Sam/Example — Beta/Inc" in text
