"""Tests for tools/outreach_pending.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


def test_empty_outreach_log(tmp_path):
    """Script handles missing outreach log gracefully."""
    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["awaiting_response"] == []
    assert result["awaiting_response_overdue"] == []
    assert result["recent_outreach"]["sent"] == 0


def test_no_reply_overdue_detected(tmp_path):
    """Entry with 'No reply' status older than threshold is flagged overdue."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-10 | cold-outreach | email | Sarah Chen | Stripe | Hello | No reply |
        | 2026-02-25 | cold-outreach | linkedin | Jordan Lee | Acme AI | Hi | Drafted |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    # Sarah Chen: 16 days, No reply → overdue
    overdue_names = [e["name"] for e in result["awaiting_response_overdue"]]
    assert "Sarah Chen" in overdue_names

    # Jordan Lee: 1 day, Drafted → below threshold, not overdue
    assert "Sarah Chen" not in [e["name"] for e in result["awaiting_response"]]


def test_replied_not_awaiting(tmp_path):
    """Entries with 'Replied' status are not in awaiting lists."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-15 | cold-outreach | email | Jane Doe | Acme | Hello | Replied |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    assert result["awaiting_response"] == []
    assert result["awaiting_response_overdue"] == []
    assert result["recent_outreach"]["replied"] == 1


def test_response_rate_calculation(tmp_path):
    """Response rate = Replied / Sent * 100 (replies per message sent)."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-20 | cold-outreach | email | A | Co1 | S1 | Replied |
        | 2026-02-20 | cold-outreach | email | B | Co2 | S2 | No reply |
        | 2026-02-20 | cold-outreach | email | C | Co3 | S3 | No reply |
        | 2026-02-20 | cold-outreach | email | D | Co4 | S4 | No reply |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    assert result["recent_outreach"]["sent"] == 4
    assert result["recent_outreach"]["replied"] == 1
    # Rate = 1 replied / 4 sent * 100 = 25%
    assert result["recent_outreach"]["response_rate_percent"] == 25
    # Plausibility guard: no internal inconsistency on well-formed data.
    assert result["recent_outreach"]["sanity_warnings"] == []


def test_outside_lookback_excluded(tmp_path):
    """Entries older than lookback window are excluded."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2025-12-01 | cold-outreach | email | Old Contact | OldCo | Old msg | No reply |
        | 2026-02-25 | cold-outreach | email | New Contact | NewCo | New msg | Drafted |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--lookback-days", "30",
                        "--repo-root", str(tmp_path))

    assert result["recent_outreach"]["sent"] == 1  # Only new contact within 30 days


def test_cross_reference_networking_reply(tmp_path):
    """Outreach-log 'Sent' entry is treated as replied when networking.md has a later interaction."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-03-03 | cold-outreach | text | Sam Carter | Tessera Cheese | Coffee ask | Sent |
    """)

    write_fixture(tmp_path, "data/networking.md", """\
        ## Contacts

        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        | --- | --- | --- | --- | --- | --- | --- |
        | Sam Carter | Tessera Cheese | Owner | personal | 2026-03-03 | 2026-03-10 | — |

        ## Interaction Log

        ### Sam Carter — Tessera Cheese

        #### 2026-03-10 | text | Sam replied. Call scheduled Friday 2026-03-13 at 11am.

        **Follow-up:** 2026-03-13

        #### 2026-03-03 | text | Sent cold text via a mutual contact's intro

        **Follow-up:** —
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-03-10",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    # Sam should NOT appear in awaiting lists — networking.md shows he replied on 2026-03-10
    all_awaiting = (
        [e["name"] for e in result["awaiting_response"]] +
        [e["name"] for e in result["awaiting_response_overdue"]]
    )
    assert "Sam Carter" not in all_awaiting
    # Should be counted as replied
    assert result["recent_outreach"]["replied"] == 1


def test_cross_reference_no_later_interaction(tmp_path):
    """Outreach-log 'Sent' is NOT treated as replied when networking.md interaction is same day or earlier."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-03-03 | cold-outreach | text | Sam Carter | Tessera Cheese | Coffee ask | Sent |
    """)

    write_fixture(tmp_path, "data/networking.md", """\
        ## Contacts

        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        | --- | --- | --- | --- | --- | --- | --- |
        | Sam Carter | Tessera Cheese | Owner | personal | 2026-03-03 | 2026-03-03 | — |

        ## Interaction Log

        ### Sam Carter — Tessera Cheese

        #### 2026-03-03 | text | Sent cold text via a mutual contact's intro

        **Follow-up:** —
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-03-10",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    # Sam SHOULD still appear as overdue — no interaction after the outreach date
    overdue_names = [e["name"] for e in result["awaiting_response_overdue"]]
    assert "Sam Carter" in overdue_names


def test_closed_pipeline_company_suppressed(tmp_path):
    """Outreach to a company that is Closed in the pipeline is not 'awaiting' (but still counted)."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-10 | follow-up | email | Casey Doe | ClosedCo | Quick one before Monday | No reply |
        | 2026-02-10 | cold-outreach | email | Active Person | LiveCo | Real ask | No reply |
    """)

    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Job Application Pipeline

        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        | --- | --- | --- | --- | --- | --- | --- | --- |
        | ClosedCo | Lead | Closed - they passed (door open) | 2026-02-05 | none | - | - | - |
        | LiveCo | Ops | Applied | 2026-02-10 | follow up | - | - | - |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    all_awaiting = (
        [e["name"] for e in result["awaiting_response"]] +
        [e["name"] for e in result["awaiting_response_overdue"]]
    )
    # ClosedCo is closed → suppressed from awaiting
    assert "Casey Doe" not in all_awaiting
    # LiveCo is active → still surfaces
    assert "Active Person" in all_awaiting
    # Both still counted as sent outreach (stats unchanged)
    assert result["recent_outreach"]["sent"] == 2


def test_thank_you_not_awaiting(tmp_path):
    """A thank-you note (no reply expected) is not flagged as awaiting, even for an active company."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-10 | follow-up | email | Jordan Sample | ActiveCo | Thanks for the time today — post-call thank-you | Sent |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    all_awaiting = (
        [e["name"] for e in result["awaiting_response"]] +
        [e["name"] for e in result["awaiting_response_overdue"]]
    )
    assert "Jordan Sample" not in all_awaiting


def test_graceful_close_not_awaiting(tmp_path):
    """A graceful-close message is not flagged as awaiting."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-10 | follow-up | email | Alex Example | PassedCo | Re: the launch — graceful close after soft pass | Sent |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    all_awaiting = (
        [e["name"] for e in result["awaiting_response"]] +
        [e["name"] for e in result["awaiting_response_overdue"]]
    )
    assert "Alex Example" not in all_awaiting


def test_active_company_real_ask_still_awaiting(tmp_path):
    """Backward compat: a real ask to an active (or untracked) company still surfaces as awaiting."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-02-10 | cold-outreach | email | Real Contact | SomeCo | Intro request — would love 20 min | No reply |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    overdue_names = [e["name"] for e in result["awaiting_response_overdue"]]
    assert "Real Contact" in overdue_names


def test_cross_reference_no_networking_file(tmp_path):
    """Script works normally when networking.md is missing (no cross-reference)."""
    write_fixture(tmp_path, "data/outreach-log.md", """\
        # Outreach Log

        | Date | Type | Channel | Name | Company | Subject | Status |
        |------|------|---------|------|---------|---------|--------|
        | 2026-03-03 | cold-outreach | text | Sam Carter | Tessera Cheese | Coffee ask | Sent |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-03-10",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    # Without networking.md, Sam stays as Sent → overdue (7 days > 5 threshold)
    overdue_names = [e["name"] for e in result["awaiting_response_overdue"]]
    assert "Sam Carter" in overdue_names
