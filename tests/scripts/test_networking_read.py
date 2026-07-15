"""Tests for tools/networking_read.py"""
from pathlib import Path

import pytest

from conftest import run_script, write_fixture


def test_missing_file_no_crash(tmp_path):
    """Missing networking file returns empty result without crashing."""
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert result["contacts"] == []
    assert result["metrics"]["total_contacts"] == 0


def test_stale_contact_detected(tmp_path):
    """A contact with last_interaction 20 days ago is flagged stale."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Sarah Chen | Stripe | EM | hiring-manager | 2026-01-01 | 2026-02-08 | — |
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["contacts"]) == 1
    contact = result["contacts"][0]
    assert contact["stale"] is True
    assert contact["days_since_last_interaction"] == 20
    assert len(result["stale_contacts"]) == 1


def test_active_contact_not_stale(tmp_path):
    """A contact with last_interaction 5 days ago is not stale."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Alex Park | Google | PM | peer | 2026-02-01 | 2026-02-23 | — |
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["contacts"]) == 1
    contact = result["contacts"][0]
    assert contact["stale"] is False
    assert contact["days_since_last_interaction"] == 5
    assert result["stale_contacts"] == []


def test_pipeline_link_detected(tmp_path):
    """A contact whose company has an active pipeline entry gets a pipeline_link."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Sarah Chen | Stripe | EM | hiring-manager | 2026-02-01 | 2026-02-25 | — |
    """)
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Stripe | PM | Applied | 2026-02-25 | Follow up | — | — | — |
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    contact = result["contacts"][0]
    assert contact["pipeline_link"] is not None
    assert contact["pipeline_link"]["company"] == "Stripe"
    assert contact["pipeline_link"]["stage"] == "Applied"
    assert len(result["pipeline_connections"]) == 1


def test_closed_stage_not_a_pipeline_connection(tmp_path):
    """A contact whose company is at a freeform terminal stage ('Closed - they
    passed', 'Declined', ...) must NOT surface as a live pipeline_connection.
    Regression for fable-audit Theme 2: the 4-value TERMINAL_STAGES set missed the
    real closed vocabulary, so dead-company contacts showed as active connections."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Sarah Chen | Stripe | EM | hiring-manager | 2026-02-01 | 2026-02-25 | — |
        | Dana Lee | Globex | PM | peer | 2026-02-01 | 2026-02-25 | — |
    """)
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Stripe | PM | Applied | 2026-02-25 | Follow up | — | — | — |
        | Globex | PM | Closed - they passed | 2026-02-20 | — | — | — | — |
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    conn_companies = {c["company"] for c in result["pipeline_connections"]}
    assert conn_companies == {"Stripe"}, conn_companies


def test_midstring_declined_stage_not_a_connection(tmp_path):
    """A closed signal buried mid-string ('Founder intro complete (...) - declined,
    no current fit') must exclude the company from pipeline_connections. This is the
    real-data leak a prefix-only terminal check missed. fable-audit Theme 2."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Dana Lee | Globex | Founder | peer | 2026-02-01 | 2026-02-25 | — |
    """)
    write_fixture(tmp_path, "data/job-pipeline.md", """\
        # Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | Globex | CoS | Founder intro complete (a CEO, 6/29) - declined, no current fit | 2026-02-20 | — | — | — | — |
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert result["pipeline_connections"] == []


def test_interaction_count_matches_log_headers(tmp_path):
    """Interaction count reflects the number of '#### ' date headers in that contact's log section."""
    write_fixture(tmp_path, "data/networking.md", """\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | Sarah Chen | Stripe | EM | hiring-manager | 2026-02-01 | 2026-02-25 | — |

        ## Interaction Log

        ### Sarah Chen — Stripe

        #### 2026-02-25 | email | Follow-up email

        **Follow-up:** —

        #### 2026-02-18 | linkedin | Connected on LinkedIn

        **Follow-up:** Send cold email
    """)
    result = run_script("networking_read.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert result["contacts"][0]["interaction_count"] == 2
