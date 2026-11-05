"""Tests for tools/act_apply.py"""
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

def run_act_apply(*args, tmp_path=None):
    script_path = TOOLS_DIR / "act_apply.py"
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PIPELINE_MD = """\
# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""

NETWORKING_MD = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |

## Interaction Log
"""

NOTES_MD = """\
# Notes

> General notes.

## Decisions

## Notes

## From Inbox
"""


# ---------------------------------------------------------------------------
# Tests: pipeline-add
# ---------------------------------------------------------------------------

def test_pipeline_add_correct_row(tmp_path):
    """Adds an 8-column Researching row to the Active section."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "Northwind",
        "--role", "CoS",
        "--url", "https://northwind.example.com/careers/cos",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "pipeline_add"

    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    rows = [l for l in content.splitlines() if l.startswith("| Northwind |")]
    assert len(rows) == 1
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cols[0] == "Northwind"
    assert cols[2] == "Researching"
    assert cols[7] == "https://northwind.example.com/careers/cos"


def test_pipeline_add_source_file_in_notes(tmp_path):
    """--source-file value appears in Notes column."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "Northwind",
        "--source-file", "northwind-job.md",
    )
    content = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert "northwind-job.md" in content


def test_pipeline_add_missing_file_returns_error(tmp_path):
    """Missing pipeline file returns JSON error, not crash."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "pipeline-add", "Ghost Co",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


# ---------------------------------------------------------------------------
# Tests: contact-add
# ---------------------------------------------------------------------------

def test_contact_add_row_and_log_section(tmp_path):
    """Creates a Contacts row and an Interaction Log section."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Priya Anand",
        "--company", "Northwind",
        "--role", "Head of Ops",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "contact_add"

    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Priya Anand |" in content
    assert "### Priya Anand — Northwind" in content


def test_contact_add_content_in_blockquote(tmp_path):
    """--content value is stored as blockquote in the interaction log entry."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Alex Park",
        "--company", "Acme",
        "--content", "Met Alex at SF health event. Head of ops at Acme.",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "> Met Alex at SF health event." in content


def test_contact_add_source_file_in_log(tmp_path):
    """--source-file value appears in interaction log entry."""
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Casey Doe",
        "--source-file", "casey-capture.md",
    )
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "casey-capture.md" in content


def test_contact_add_missing_file_returns_error(tmp_path):
    """Missing networking file returns JSON error."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Nobody",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


# ---------------------------------------------------------------------------
# Tests: contact-add duplicate guard (fable-audit Theme 2 — name_dedup bypass)
# ---------------------------------------------------------------------------

CONTACTS_WITH_JORDAN = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jordan Sample | Acme | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jordan Sample — Acme
"""


def test_contact_add_blocks_exact_duplicate(tmp_path):
    """An exact-name re-add returns duplicate_warning and does not add a 2nd row."""
    write_fixture(tmp_path, "data/networking.md", CONTACTS_WITH_JORDAN)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Jordan Sample", "--company", "Acme",
    )
    assert code == 0
    assert result["action"] == "duplicate_warning"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert content.count("| Jordan Sample |") == 1


def test_contact_add_blocks_fuzzy_duplicate(tmp_path):
    """A spelling-variant of an existing contact is blocked with possible_duplicate
    (parity with networking_write.py add). Regression: act_apply had NO dup check."""
    write_fixture(tmp_path, "data/networking.md", CONTACTS_WITH_JORDAN)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Jordan Samples", "--company", "Acme",
    )
    assert code != 0
    assert result["status"] == "error"
    assert result["code"] == "possible_duplicate"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Jordan Samples |" not in content


def test_contact_add_force_overrides_fuzzy_duplicate(tmp_path):
    """--force adds the near-duplicate anyway (genuinely different person)."""
    write_fixture(tmp_path, "data/networking.md", CONTACTS_WITH_JORDAN)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "contact-add", "Jordan Samples", "--company", "Acme", "--force",
    )
    assert code == 0
    assert result["action"] == "contact_add"
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "| Jordan Samples |" in content


def test_contact_add_dry_run_reports_duplicate(tmp_path):
    """--dry-run must surface the SAME duplicate the real run would block on, not a
    blind 'Would add' (parity with networking_write; fable-audit Theme 2 reviewer
    finding — the dedup guard was dead on the dry-run path)."""
    write_fixture(tmp_path, "data/networking.md", CONTACTS_WITH_JORDAN)
    # exact dup under dry-run
    result, code = run_act_apply(
        "--repo-root", str(tmp_path), "--dry-run",
        "contact-add", "Jordan Sample", "--company", "Acme",
    )
    assert result["action"] == "duplicate_warning"
    # fuzzy dup under dry-run
    result, code = run_act_apply(
        "--repo-root", str(tmp_path), "--dry-run",
        "contact-add", "Jordan Samples", "--company", "Acme",
    )
    assert code != 0
    assert result["code"] == "possible_duplicate"
    # file untouched by the dry run
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert content.count("| Jordan Sample") == 1


# ---------------------------------------------------------------------------
# Tests: notes-add
# ---------------------------------------------------------------------------

def test_notes_add_with_slug_goes_to_company_notes(tmp_path):
    """notes-add with --company-slug writes to data/company-notes/<slug>.md."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "Office is in the SoMa neighborhood",
        "--company-slug", "northwind",
    )
    assert code == 0
    note_path = tmp_path / "data/company-notes/northwind.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Office is in the SoMa neighborhood" in content


def test_notes_add_without_slug_goes_to_notes_md(tmp_path):
    """notes-add without --company-slug writes to data/notes.md under ## From Inbox."""
    write_fixture(tmp_path, "data/notes.md", NOTES_MD)
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "Unclassifiable snippet from inbox",
    )
    assert code == 0
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "Unclassifiable snippet from inbox" in content
    # Should be under ## From Inbox
    from_inbox_pos = content.index("## From Inbox")
    note_pos       = content.index("Unclassifiable snippet from inbox")
    assert note_pos > from_inbox_pos


def test_notes_add_creates_notes_md_if_missing(tmp_path):
    """notes-add creates data/notes.md with ## From Inbox if file is absent."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "notes-add",
        "--content", "New note",
    )
    assert code == 0
    assert (tmp_path / "data/notes.md").exists()
    content = (tmp_path / "data/notes.md").read_text(encoding="utf-8")
    assert "## From Inbox" in content
    assert "New note" in content


# ---------------------------------------------------------------------------
# Tests: company-note-add
# ---------------------------------------------------------------------------

COMPANY_NOTES_EXISTING = """\
# acme-ai — Notes

> Running log of raw notes, call prep, and observations.
> Newest entries at the top.

---

## 2026-02-20 | recruiter call

Notes from earlier call.
"""


def test_company_note_add_creates_new_file(tmp_path):
    """Creates data/company-notes/<slug>.md with header and entry when file is absent."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "acme-ai",
        "--content", "Alex replied with a calendar invite",
        "--context", "inbound email",
    )
    assert code == 0
    assert result["status"] == "ok"
    assert result["action"] == "company_note_add"
    assert result["slug"] == "acme-ai"

    note_path = tmp_path / "data/company-notes/acme-ai.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Alex replied with a calendar invite" in content
    assert "inbound email" in content


def test_company_note_add_prepends_to_existing(tmp_path):
    """New entry is prepended before the first ## YYYY-* heading (newest first)."""
    write_fixture(tmp_path, "data/company-notes/acme-ai.md", COMPANY_NOTES_EXISTING)
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "acme-ai",
        "--content", "Follow-up email received",
        "--context", "inbound email",
    )
    content = (tmp_path / "data/company-notes/acme-ai.md").read_text(encoding="utf-8")
    new_pos  = content.index("Follow-up email received")
    old_pos  = content.index("Notes from earlier call.")
    assert new_pos < old_pos, "New entry should appear before existing entries"


def test_company_note_add_context_in_header(tmp_path):
    """--context value appears in the ## YYYY-MM-DD | <context> header."""
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "contoso",
        "--content", "Contact confirmed interest in CoS role",
        "--context", "inbound email",
    )
    content = (tmp_path / "data/company-notes/contoso.md").read_text(encoding="utf-8")
    assert "| inbound email" in content


def test_company_note_add_source_file_in_header(tmp_path):
    """--source-file value appears in the header label."""
    run_act_apply(
        "--repo-root", str(tmp_path),
        "company-note-add", "acme-corp",
        "--content", "Recruiter outreach for eng role",
        "--source-file", "acme-recruiter.md",
    )
    content = (tmp_path / "data/company-notes/acme-corp.md").read_text(encoding="utf-8")
    assert "acme-recruiter.md" in content


def test_company_note_add_dry_run(tmp_path):
    """--dry-run returns dry_run:true and does not create the file."""
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "company-note-add", "ghost-company",
        "--content", "Should not be written",
    )
    assert code == 0
    assert result["dry_run"] is True
    assert not (tmp_path / "data/company-notes/ghost-company.md").exists()


# ---------------------------------------------------------------------------
# Tests: dry-run
# ---------------------------------------------------------------------------

def test_dry_run_returns_no_file_change(tmp_path):
    """--dry-run returns dry_run:true and does not write."""
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    original = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_act_apply(
        "--repo-root", str(tmp_path),
        "--dry-run",
        "pipeline-add", "DryRun Corp",
    )
    assert code == 0
    assert result["dry_run"] is True
    after = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    assert after == original


# ---------------------------------------------------------------------------
# act_apply writes the pipeline and networking tables only through their owners
# (table-writers, 2026-11-02)
# ---------------------------------------------------------------------------

import ast  # noqa: E402


def test_act_apply_no_longer_writes_the_owned_tables_itself():
    """The adoption gate: neither command body calls write_atomic; pipe_write.py and
    networking_write.py own those tables."""
    tree = ast.parse((TOOLS_DIR / "act_apply.py").read_text(encoding="utf-8"))
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name in ("cmd_pipeline_add", "cmd_contact_add")):
        calls = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "write_atomic" not in calls, fn.name
        assert "_owner" in calls, fn.name


def test_pipeline_add_writes_notes_and_next_action_through_pipe_write(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "North|wind",
                                 "--role", "CoS", "--notes", "via | referral", "--source-file", "a|b.md")
    assert code == 0 and result["action"] == "pipeline_add", result
    row = [l for l in (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
           if l.startswith("| North/wind |")][0]
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert len(cells) == 8
    assert cells[4] == "Run /research-company, then /generate-cv"
    assert cells[6] == "via / referral - Added from inbox/a/b.md"


def test_pipeline_add_without_a_role_writes_a_dash(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "Northwind")
    assert code == 0 and result["role"] == "—"


def test_pipeline_add_reports_the_owners_duplicate_and_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "Northwind", "--role", "CoS")
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "northwind", "--role", "cos")
    assert code == 0 and result["action"] == "duplicate_warning", result
    assert result["existing_roles"] == ["CoS"]
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_pipeline_add_dry_run_previews_the_owners_row(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    before = (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8")
    result, code = run_act_apply("--repo-root", str(tmp_path), "--dry-run", "pipeline-add", "Northwind")
    assert code == 0 and result["dry_run"] is True
    assert result["would_mutate"][0]["row"].startswith("| Northwind | — | Researching |")
    assert (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8") == before


def test_pipeline_add_relays_the_owners_error(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", "# Job Pipeline\n\nno sections\n")
    result, code = run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "Northwind")
    assert code != 0 and result["code"] == "missing_section"


def test_contact_add_goes_through_networking_write_add_and_log(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_act_apply("--repo-root", str(tmp_path), "contact-add", "Sam|Example",
                                 "--company", "Beta|Inc", "--content", "Hello there",
                                 "--source-file", "s.md")
    assert code == 0 and result["action"] == "contact_add", result
    text = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    row = [l for l in text.splitlines() if l.startswith("| Sam/Example |")][0]
    assert len([c for c in row.strip("|").split("|")]) == 7
    assert "| other |" in row
    assert "#### " in text and "| other | Captured from inbox/s.md" in text
    assert "> Hello there" in text
    assert "**Follow-up:** Review and update relationship type, add outreach if appropriate." in text


def test_contact_add_reports_a_failed_log_as_a_partial_write(tmp_path, monkeypatch):
    import importlib, sys as _s
    _s.path.insert(0, str(TOOLS_DIR)); aa = importlib.import_module("act_apply")
    seq = iter([{"status": "ok", "action": "add"}, {"status": "error", "message": "boom"}])
    monkeypatch.setattr(aa, "_owner", lambda *a, **k: next(seq))
    out = []
    monkeypatch.setattr(aa, "out_error", lambda msg, code="error", **kw: (_ for _ in ()).throw(SystemExit((msg, code, kw))))
    args = type("A", (), {"name": "Sam", "company": None, "role": None, "source_file": None,
                          "content": None, "force": False})()
    with pytest.raises(SystemExit) as e:
        aa.cmd_contact_add(args, tmp_path / "data" / "networking.md", False)
    msg, code, kw = e.value.code
    assert code == "partial_write" and kw == {"contact_added": True} and "boom" in msg


def test_owner_without_json_is_an_owner_failed_error(tmp_path, monkeypatch):
    import importlib, sys as _s
    _s.path.insert(0, str(TOOLS_DIR)); aa = importlib.import_module("act_apply")

    class P:
        stdout, stderr, returncode = "", "Traceback: boom", 1
    monkeypatch.setattr(aa.subprocess, "run", lambda *a, **k: P())
    r = aa._owner("pipe_write.py", ["add", "X", "Y"], tmp_path, False)
    assert r["status"] == "error" and r["code"] == "owner_failed" and "boom" in r["message"]


def test_pipeline_add_without_a_url_writes_a_dash(tmp_path):
    write_fixture(tmp_path, "data/job-pipeline.md", PIPELINE_MD)
    result, code = run_act_apply("--repo-root", str(tmp_path), "pipeline-add", "Northwind")
    assert code == 0, result
    row = [l for l in (tmp_path / "data/job-pipeline.md").read_text(encoding="utf-8").splitlines()
           if l.startswith("| Northwind |")][0]
    assert [c.strip() for c in row.strip("|").split("|")][7] == "—"


def test_contact_add_dry_run_previews_and_writes_nothing(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    result, code = run_act_apply("--repo-root", str(tmp_path), "--dry-run", "contact-add", "Sam Example",
                                 "--content", "Hello there")
    assert code == 0 and result["action"] == "contact_add" and result["dry_run"] is True, result
    assert result["would_mutate"]
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before


def test_contact_add_without_content_writes_no_quote(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING_MD)
    result, code = run_act_apply("--repo-root", str(tmp_path), "contact-add", "Sam Example")
    assert code == 0, result
    text = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    assert "Captured from inbox" in text and not [l for l in text.splitlines() if l.startswith(">")]
