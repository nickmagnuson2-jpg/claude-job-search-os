"""MERGED item 8: a manual `granola_cli pull` of a personal-project call.

granola_cli called classify_meeting(meeting, config) without the personal-projects
config, so a personal call classified `networking` and was saved into the job-search
voice corpus. The auto-debrief passes pconfig; the CLI must too, and must route a
personal call to its project's vault the same way the auto-debrief does.

Placeholders only: this is a public file.
"""
import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import tools.granola_cli as gc  # noqa: E402

PARTNER_CONFIG = {"rules": [{"project": "side-project", "attendees": {"robin@example.com"},
                             "names": ["Robin Placeholder"], "titles": ["side project"]}]}
EMPTY_SEALED = {"attendees": set(), "names": []}


@pytest.fixture
def pull(monkeypatch, tmp_path):
    vault = tmp_path / "personal-vault"
    vault.mkdir()
    monkeypatch.setenv("PERSONAL_VAULT_ROOT", str(vault))
    calls = []

    def run(attendees, title="Weekly sync", pconfig=PARTNER_CONFIG):
        note = {"title": title, "created_at": "2026-11-20T17:00:00Z",
                "transcript": "Them: hello\nMe: hi", "attendees": attendees}
        monkeypatch.setattr(gc, "resolve_ref", lambda ref: "not_placeholder")
        monkeypatch.setattr(gc, "fetch_note_full", lambda mid: note)
        monkeypatch.setattr(gc, "extract_summary_and_notes", lambda n: ("summary", ""))
        monkeypatch.setattr(gc, "load_sealed_meetings_config", lambda: EMPTY_SEALED)
        # raising=False: the pre-fix module has no such name, and the test must then fail
        # on the routing, not on the patch.
        monkeypatch.setattr(gc, "load_personal_projects_config", lambda: pconfig, raising=False)

        def fake_persist(meeting, summary, notes, type_, project=None, vault_root=None):
            calls.append({"type": type_, "project": project, "vault_root": vault_root})
            return {"status": "ok"}

        monkeypatch.setattr(gc, "persist_via_granola_save", fake_persist)
        rc = gc.cmd_pull(argparse.Namespace(ref="1", type=None, no_persist=False))
        return rc, calls[-1] if calls else None

    run.vault = vault
    return run


def test_cli_pull_routes_a_personal_call_to_its_project_vault(pull):
    rc, call = pull([{"name": "Robin", "email": "robin@example.com"}])
    assert rc == 0
    assert call == {"type": "personal", "project": "side-project", "vault_root": pull.vault}


def test_cli_pull_still_files_a_job_search_call_as_networking(pull):
    """Preservation test, not a regression test: it passes before the fix too. It pins
    that the personal config does not pull a job-search call away from networking
    (Codex r1 F3)."""
    rc, call = pull([{"name": "Pat Recruiter", "email": "pat@northwind.example"}])
    assert rc == 0
    assert call == {"type": "networking", "project": None, "vault_root": None}
