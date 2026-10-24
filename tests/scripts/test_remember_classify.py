"""Tests for tools/remember_classify.py"""
from pathlib import Path

import pytest

from conftest import run_script, write_fixture


def _types(result: dict) -> list[str]:
    """Extract destination types from classify result."""
    return [d["type"] for d in result["destinations"]]


def _setup_networking(tmp_path: Path, contact_name: str = "Jordan Lee",
                      company: str = "Acme AI") -> None:
    write_fixture(tmp_path, "data/networking.md", f"""\
        # Networking

        ## Contacts
        | Name | Company | Role | Relationship | Added | Last Interaction | Email |
        |------|---------|------|-------------|-------|-----------------|-------|
        | {contact_name} | {company} | Director | peer | 2026-02-01 | 2026-02-25 | — |
    """)


def _setup_pipeline(tmp_path: Path, company: str = "Northwind") -> None:
    write_fixture(tmp_path, "data/job-pipeline.md", f"""\
        # Pipeline

        ## Active
        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|-------------|-------------|---------|-------|-----|
        | {company} | PM | Researching | 2026-02-25 | Research | — | — | — |
    """)


def test_known_contact_reply_routes_to_outreach_reply(tmp_path):
    """Note with known contact name + reply keyword → outreach_reply destination."""
    _setup_networking(tmp_path)
    result = run_script("remember_classify.py",
                        "--note", "Jordan Lee replied to my email this morning",
                        "--repo-root", str(tmp_path))
    assert "outreach_reply" in _types(result)
    assert result["ambiguous"] is False


def test_known_contact_routes_to_contact_note(tmp_path):
    """Note mentioning a known contact (no reply keyword) → contact_note destination."""
    _setup_networking(tmp_path)
    result = run_script("remember_classify.py",
                        "--note", "Had a great call with Jordan Lee, lots of insights about the team",
                        "--repo-root", str(tmp_path))
    assert "contact_note" in _types(result)
    assert result["ambiguous"] is False


def test_known_company_routes_to_company_note(tmp_path):
    """Note mentioning a known pipeline company → company_note destination."""
    _setup_pipeline(tmp_path)
    result = run_script("remember_classify.py",
                        "--note", "Northwind looks really interesting for a CoS role",
                        "--repo-root", str(tmp_path))
    assert "company_note" in _types(result)
    dest = next(d for d in result["destinations"] if d["type"] == "company_note")
    assert dest["slug"] == "northwind"
    assert result["ambiguous"] is False


def test_profile_update_detected(tmp_path):
    """Note with compensation keyword (no entity match) → profile_update destination."""
    result = run_script("remember_classify.py",
                        "--note", "compensation floor is $95K, ceiling $120K",
                        "--repo-root", str(tmp_path))
    assert _types(result) == ["profile_update"]
    assert result["ambiguous"] is False


def test_decision_detected(tmp_path):
    """Note with decision keyword (no entity match) → decision destination."""
    result = run_script("remember_classify.py",
                        "--note", "decided not to pursue this track any further",
                        "--repo-root", str(tmp_path))
    assert _types(result) == ["decision"]
    assert result["ambiguous"] is False


def test_unknown_falls_back_to_general_note(tmp_path):
    """Note with no known entities or keywords → general_note, ambiguous=True."""
    result = run_script("remember_classify.py",
                        "--note", "the weather is really nice today",
                        "--repo-root", str(tmp_path))
    assert _types(result) == ["general_note"]
    assert result["ambiguous"] is True


# ---------------------------------------------------------------------------
# Direct pins of the pipeline-table reader (added 2026-10-24, before moving it onto
# pipe_read.parse_all_rows). Only cases where this reader and the shared rule agree.
# ---------------------------------------------------------------------------
_PIPE_HEADER = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
                "|---|---|---|---|---|---|---|---|\n")


def _pipe_doc(*rows, archived=()):
    text = "# Job Pipeline\n\n## Active\n" + _PIPE_HEADER + "".join(r + "\n" for r in rows)
    if archived:
        text += "\n## Archived\n" + _PIPE_HEADER + "".join(r + "\n" for r in archived)
    return text


_SKIPPED_ROWS = (
    "|  | PM | Applied | 2026-02-01 | x | — | — | — |",
    "| Short | PM |",
)

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import remember_classify as _rc  # noqa: E402


def test_load_pipeline_companies_keeps_live_rows_in_file_order():
    content = _pipe_doc("| Acme | PM | Applied | 2026-02-01 | x | — | — | — |", *_SKIPPED_ROWS,
                        "| Beta | CoS | To Evaluate | 2026-02-01 | x | — | — | — |",
                        "| Gone | PM | Withdrawn | 2026-02-01 | — | — | — | — |",
                        "| Took | PM | Accepted | 2026-02-01 | — | — | — | — |",
                        archived=("| Old | PM | Archived | 2026-01-01 | — | — | — | — |",))
    assert _rc.load_pipeline_companies(content) == ["Acme", "Beta"]
