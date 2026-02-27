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
        | 2026-02-25 | cold-outreach | linkedin | Alex | Amae | Hi | Drafted |
    """)

    result = run_script("outreach_pending.py",
                        "--target-date", "2026-02-26",
                        "--days-threshold-overdue", "5",
                        "--repo-root", str(tmp_path))

    # Sarah Chen: 16 days, No reply → overdue
    overdue_names = [e["name"] for e in result["awaiting_response_overdue"]]
    assert "Sarah Chen" in overdue_names

    # Alex: 1 day, Drafted → below threshold, not overdue
    overdue_names_all = overdue_names + [e["name"] for e in result["awaiting_response"]]
    # Alex is within threshold so may not appear at all, or appears in awaiting
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
    """Response rate = Replied / (Sent - Replied) * 100 (approx)."""
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
    # Rate = 1 / 3 * 100 = 33%
    assert result["recent_outreach"]["response_rate_percent"] == 33


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
