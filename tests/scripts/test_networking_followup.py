"""Tests for tools/networking_followup.py"""
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def make_fixture(contacts_rows: str, interaction_log: str = "") -> str:
    """Build a full networking.md fixture with contacts table + interaction log."""
    base = (
        "## Contacts\n\n"
        "| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    )
    base += contacts_rows
    if interaction_log:
        base += "\n## Interaction Log\n\n" + interaction_log
    return base


def test_empty_networking(tmp_path):
    """Script handles missing networking.md gracefully."""
    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["followup_due"] == []
    assert result["followup_overdue"] == []
    assert result["summary"]["total_contacts"] == 0


def test_overdue_contact_from_interaction_log(tmp_path):
    """Contact with 'next week' follow-up from 14 days ago is overdue."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Alex Mullin | Amae Health | PM | peer | 2026-02-10 | 2026-02-12 | alex@amae.com |\n",
        "### Alex Mullin — Amae Health\n\n"
        "#### 2026-02-12 | email | Coffee chat follow-up\n\n"
        "**Follow-up:** Follow up next week\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Follow-up was due 2026-02-19 (2026-02-12 + 7 days), today is 2026-02-26 → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Alex Mullin" in overdue_names


def test_explicit_date_in_interaction_log(tmp_path):
    """Explicit date in follow-up line is parsed correctly."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Sarah Chen | Stripe | CoS | peer | 2026-02-20 | 2026-02-24 | — |\n",
        "### Sarah Chen — Stripe\n\n"
        "#### 2026-02-24 | email | Sent intro request\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-01\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due 2026-03-01 — 3 days from now — should appear in followup_due
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Sarah Chen" in due_names


def test_no_interaction_log_skipped(tmp_path):
    """Contacts with no Interaction Log section are not surfaced."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jane Doe | Acme | VP | peer | 2026-02-15 | 2026-02-20 | jane@acme.com |\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Jane Doe" not in all_names


def test_dash_followup_skipped(tmp_path):
    """Contacts with dash follow-up in interaction log are not surfaced."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Jane Doe | Acme | VP | peer | 2026-02-15 | 2026-02-20 | jane@acme.com |\n",
        "### Jane Doe — Acme\n\n"
        "#### 2026-02-20 | email | Sent intro request\n\n"
        "**Follow-up:** —\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Jane Doe" not in all_names


def test_3_5_business_days_inference(tmp_path):
    """'3-5 business days' maps to entry_date + 5d."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Bob Smith | TechCo | Eng | peer | 2026-02-15 | 2026-02-20 | — |\n",
        "### Bob Smith — TechCo\n\n"
        "#### 2026-02-20 | email | Sent cold email\n\n"
        "**Follow-up:** Follow up in 3-5 business days\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due: 2026-02-20 + 5d = 2026-02-25, which is 1 day ago → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Bob Smith" in overdue_names


def test_most_recent_followup_used(tmp_path):
    """When multiple interaction entries exist, the most recent (topmost) follow-up is used."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Sam Jones | StartupX | PM | peer | 2026-02-10 | 2026-02-24 | — |\n",
        "### Sam Jones — StartupX\n\n"
        "#### 2026-02-24 | email | Follow-up #2 sent\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-05\n\n"
        "#### 2026-02-15 | email | Initial cold outreach\n\n"
        "**Follow-up:** Follow up next week\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Should use the most recent follow-up (~2026-03-05), not the older "next week"
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Sam Jones" in due_names
    entry = [e for e in result["followup_due"] if e["name"] == "Sam Jones"][0]
    assert entry["followup_date"] == "2026-03-05"


def test_none_required_followup_skipped(tmp_path):
    """Follow-up starting with 'None' is skipped."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Lisa Park | YogaCo | Instructor | other | 2026-02-10 | 2026-02-20 | — |\n",
        "### Lisa Park — YogaCo\n\n"
        "#### 2026-02-20 | call | Decided to step back from yoga biz\n\n"
        "**Follow-up:** None required. Lisa intro pending.\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Lisa Park" not in all_names


def test_marisa_bug_regression(tmp_path):
    """Regression test: Marisa emailed 2026-03-09 with follow-up ~2026-03-16 should NOT be overdue on 2026-03-10."""
    write_fixture(tmp_path, "data/networking.md", make_fixture(
        "| Marisa Patel-O'Connor | Headway | — | cold-outreach | 2026-03-09 | 2026-03-09 | — |\n",
        "### Marisa Patel-O'Connor — Headway\n\n"
        "#### 2026-03-09 | email | Re: Manager, Business Operations application\n\n"
        "> Hi Marisa, ...\n\n"
        "**Follow-up:** Wait for response — check back ~2026-03-16 if no reply\n\n",
    ))

    result = run_script("networking_followup.py",
                        "--target-date", "2026-03-10",
                        "--repo-root", str(tmp_path))

    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Marisa Patel-O'Connor" not in overdue_names

    # Should appear in followup_due (6 days until due)
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Marisa Patel-O'Connor" in due_names
    entry = [e for e in result["followup_due"] if e["name"] == "Marisa Patel-O'Connor"][0]
    assert entry["followup_date"] == "2026-03-16"
    assert entry["days_until"] == 6
