"""Tests for tools/networking_followup.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


CONTACTS_HEADER = """\
    # Networking Contacts

    | Name | Company | Role | Relationship | Last Interaction | Follow-Up Action |
    |------|---------|------|--------------|-----------------|-----------------|
"""


def test_empty_networking(tmp_path):
    """Script handles missing networking.md gracefully."""
    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["followup_due"] == []
    assert result["followup_overdue"] == []
    assert result["summary"]["total_contacts"] == 0


def test_overdue_contact_detected(tmp_path):
    """Contact with 'next week' follow-up from 14 days ago is overdue."""
    write_fixture(tmp_path, "data/networking.md",
                  CONTACTS_HEADER + "    | Alex Mullin | Amae Health | PM | peer | 2026-02-12 | Follow up next week |\n")

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Follow-up was due 2026-02-19 (2026-02-12 + 7 days), today is 2026-02-26 → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Alex Mullin" in overdue_names


def test_explicit_date_in_notes(tmp_path):
    """Explicit date in follow-up action notes is parsed correctly."""
    write_fixture(tmp_path, "data/networking.md",
                  CONTACTS_HEADER + "    | Sarah Chen | Stripe | CoS | peer | 2026-02-24 | Follow up ~2026-03-01 |\n")

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due 2026-03-01 — 3 days from now — should appear in followup_due
    due_names = [e["name"] for e in result["followup_due"]]
    assert "Sarah Chen" in due_names


def test_no_followup_action_skipped(tmp_path):
    """Contacts with '—' follow-up action are not surfaced."""
    write_fixture(tmp_path, "data/networking.md",
                  CONTACTS_HEADER + "    | Jane Doe | Acme | VP | peer | 2026-02-20 | — |\n")

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Jane Doe" not in all_names


def test_3_5_business_days_inference(tmp_path):
    """'3–5 business days' maps to last_date + 5d."""
    write_fixture(tmp_path, "data/networking.md",
                  CONTACTS_HEADER + "    | Bob Smith | TechCo | Eng | peer | 2026-02-20 | Follow up in 3-5 business days |\n")

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due: 2026-02-20 + 5d = 2026-02-25, which is 1 day ago → overdue
    overdue_names = [e["name"] for e in result["followup_overdue"]]
    assert "Bob Smith" in overdue_names


def test_default_14_day_inference(tmp_path):
    """Contacts with no timing signal default to last_date + 14d."""
    write_fixture(tmp_path, "data/networking.md",
                  CONTACTS_HEADER + "    | Sam Jones | StartupX | PM | peer | 2026-02-24 | Check in later |\n")

    result = run_script("networking_followup.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Due: 2026-02-24 + 14d = 2026-03-09 — not yet due, not within 7 days either
    all_names = (
        [e["name"] for e in result["followup_due"]] +
        [e["name"] for e in result["followup_overdue"]]
    )
    assert "Sam Jones" not in all_names
