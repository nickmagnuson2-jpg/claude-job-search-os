#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the vault-existence guard in granola_auto_debrief.

The guard exists so an unmounted or renamed personal vault cannot silently grow
a shadow tree that Obsidian and every skill ignore. It must FAIL CLOSED for
vault-bound destinations.

It must also not fire for repo-bound destinations. Before 2026-08-20 it keyed on
`type_ != "networking"`, which sent every recruiter transcript down the vault
branch; with the vault unconfigured `vault_root` is None (vault_paths fails loud
by design) and the guard raised AttributeError, so `granola_cli.py pull` crashed
on every recruiter drill. The fix keys on the resolved destination path instead,
which is also immune to `resolve_destination` remapping `personal` -> `general`.

Both directions are tested. A test that only proved recruiter works would have
passed on a guard deleted outright.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import granola_auto_debrief as gad  # noqa: E402


# The personal-vault root comes from the gitignored tools/.personal-vault.conf, so these
# tests failed in any fresh worktree (plan A1, output/mutation-parallel-102826/plan.md).
# PERSONAL_VAULT_ROOT outranks that file, and a temp vault keeps the tests off the real one.
@pytest.fixture(autouse=True)
def _temp_vault(monkeypatch, tmp_path):
    vault = tmp_path / "personal-vault"
    vault.mkdir()
    monkeypatch.setenv("PERSONAL_VAULT_ROOT", str(vault))
    return vault

MEETING = {"id": "not_TEST", "title": "A Drill", "created_at": "2026-08-20T22:06:00Z"}


def test_repo_bound_destination_resolves_into_the_repo(monkeypatch):
    dest = gad.resolve_destination(MEETING, "recruiter", None, None)
    assert str(dest["output_path"]).startswith(str(gad.VOICE_CORPUS_DIR)), (
        "recruiter transcripts must resolve into the repo corpus dir"
    )


def test_recruiter_persist_does_not_consult_the_vault(monkeypatch):
    """The regression test for the actual 2026-08-20 bug.

    Must exercise persist_via_granola_save, not just resolve_destination. An
    earlier version of this test only checked path resolution and therefore
    PASSED against the broken `type_ != "networking"` guard, which is the very
    thing it was written to prevent. Verified by mutation: reverting the guard
    must make this test fail.
    """
    called = []
    monkeypatch.setattr(gad, "vault_sealed_dir",
                        lambda: called.append("vault") or Path("/nonexistent-vault/data/sealed"))
    monkeypatch.setattr(gad, "vault_root", None, raising=False)
    # granola_auto_debrief does `import subprocess` INSIDE the function, so it is a
    # local name, not a module attribute. Patch the real module.
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": '{"status":"ok"}', "stderr": ""})())
    result = gad.persist_via_granola_save(MEETING, "summary", "", "recruiter")
    assert result.get("reason") != "vault-missing", (
        "recruiter is repo-bound; the vault guard must not fire for it"
    )
    assert not called, "recruiter persist must not consult the vault at all"


def test_vault_bound_destination_fails_closed_when_vault_missing(monkeypatch):
    """sealed -> vault. With no vault it must refuse, not write and not crash."""
    monkeypatch.setattr(gad, "vault_sealed_dir",
                        lambda: Path("/nonexistent-vault/data/sealed"))
    monkeypatch.setattr(gad, "personal_vault", lambda: Path("/nonexistent-vault"))
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "sealed")
    assert result["status"] == "error", "sealed with no vault must refuse"
    assert result["reason"] == "vault-missing"


def test_guard_reports_the_path_it_checked(monkeypatch):
    """The error must name the expected vault, so a wrong path is debuggable."""
    monkeypatch.setattr(gad, "vault_sealed_dir",
                        lambda: Path("/nonexistent-vault/data/sealed"))
    monkeypatch.setattr(gad, "personal_vault", lambda: Path("/nonexistent-vault"))
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "sealed")
    assert "expected_vault" in result and result["expected_vault"], (
        "guard must report which vault path it checked"
    )


import pytest  # noqa: E402


@pytest.mark.parametrize("missing", ["VaultRootMissing", "SealedFolderMissing"])
def test_sealed_persist_refuses_when_the_vault_or_its_sealed_folder_is_unconfigured(
        monkeypatch, missing):
    """2026-11-01: the sealed folder comes from config. An unconfigured vault root or
    sealed folder must refuse as vault-missing, not raise out of the persist call (which
    would end the launchd run on the first sealed meeting)."""
    def unconfigured():
        raise getattr(gad.vault_paths, missing)()
    monkeypatch.setattr(gad, "vault_sealed_dir", unconfigured)
    monkeypatch.setattr(gad, "personal_vault", unconfigured)
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "sealed")
    assert result == {"status": "error", "reason": "vault-missing",
                      "expected_vault": "unconfigured"}


def test_a_sealed_filename_names_the_contact_without_the_configured_keyword(monkeypatch):
    """The title keyword comes from config (2026-11-01), so the slug strips the configured
    keywords, not a word written in this public file."""
    monkeypatch.setattr(gad, "vault_sealed_dir", lambda: Path("/v/sealed"))
    monkeypatch.setattr(gad, "load_sealed_meetings_config",
                        lambda: {"attendees": set(), "names": [], "keywords": ["private"]})
    meeting = {"title": "Private session with Jordan", "created_at": "2026-11-01T17:00:00Z"}
    dest = gad.resolve_destination(meeting, "sealed", None, Path("/v"))
    assert dest["output_path"].name.endswith("-sealed-jordan-transcript.md"), dest["output_path"]
    assert dest["output_path"].parent == Path("/v/sealed")


def test_vault_sealed_dir_is_the_configured_sealed_folder(monkeypatch):
    monkeypatch.setattr(gad.vault_paths, "sealed_dir", lambda: Path("/v/configured"))
    assert gad.vault_sealed_dir() == Path("/v/configured")


def test_a_title_that_is_only_a_keyword_still_names_the_file(monkeypatch):
    monkeypatch.setattr(gad, "vault_sealed_dir", lambda: Path("/v/sealed"))
    monkeypatch.setattr(gad, "load_sealed_meetings_config",
                        lambda: {"attendees": set(), "names": [], "keywords": ["private"]})
    dest = gad.resolve_destination({"title": "Private", "created_at": "2026-11-01T17:00:00Z"},
                                   "sealed", None, Path("/v"))
    assert dest["output_path"].name.endswith("-sealed-private-transcript.md"), dest["output_path"]


def test_a_sealed_persist_into_an_existing_vault_goes_ahead(monkeypatch, tmp_path):
    """The vault-missing refusal must fire only when the vault is missing."""
    monkeypatch.setattr(gad, "vault_sealed_dir", lambda: tmp_path / "sealed")
    monkeypatch.setattr(gad, "personal_vault", lambda: tmp_path)
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": '{"status":"ok"}', "stderr": ""})())
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "sealed")
    assert result.get("reason") != "vault-missing" and result.get("status") == "ok", result


def test_the_unconfigured_refusal_says_why(monkeypatch, capsys):
    def unconfigured():
        raise gad.vault_paths.SealedFolderMissing()
    monkeypatch.setattr(gad, "vault_sealed_dir", unconfigured)
    gad.persist_via_granola_save(MEETING, "summary text", "", "sealed")
    err = capsys.readouterr().err
    assert "no sealed vault folder is configured" in err and "NOT persisted" in err


# --- review round 1 (Fable 8.F1/9.F1, 8.F6, 8.F2/10.F4), 2026-11-02 ----------------------

def test_a_repo_bound_destination_never_needs_the_personal_vault(monkeypatch):
    """Recruiter, networking and general land in the repo. Resolving them must not ask for
    the personal vault, so an unconfigured vault cannot turn them into a quiet refusal."""
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    monkeypatch.setattr(gad.vault_paths, "vault_root", lambda config_path=None: None)
    for type_ in ("recruiter", "networking", "general"):
        dest = gad.resolve_destination(MEETING, type_)
        assert str(dest["output_path"]).startswith(str(gad.VOICE_CORPUS_DIR)), type_


def test_a_multiword_keyword_holding_session_still_leaves_the_contact(monkeypatch):
    monkeypatch.setattr(gad, "vault_sealed_dir", lambda: Path("/v/sealed"))
    monkeypatch.setattr(gad, "load_sealed_meetings_config",
                        lambda: {"attendees": set(), "names": [], "keywords": ["private session"]})
    meeting = {"title": "Private session with Jordan", "created_at": "2026-11-01T17:00:00Z"}
    dest = gad.resolve_destination(meeting, "sealed", None, Path("/v"))
    assert dest["output_path"].name.endswith("-sealed-jordan-transcript.md"), dest["output_path"]


def test_an_unclassified_meeting_names_a_missing_sealed_config(monkeypatch, tmp_path):
    import meeting_vocab as mv
    monkeypatch.setattr(mv, "DEFAULT_SEALED_CONFIG", tmp_path / "absent.txt")
    assert ".sealed-meetings.txt" in mv.unclassified_reason()
    present = tmp_path / "present.txt"
    present.write_text("keyword: private\n", encoding="utf-8")
    monkeypatch.setattr(mv, "DEFAULT_SEALED_CONFIG", present)
    assert ".sealed-meetings.txt" not in mv.unclassified_reason()
    assert "no sealed signal" in mv.unclassified_reason()


def test_a_forced_sealed_meeting_with_no_config_still_names_its_file(monkeypatch):
    """--force-type sealed with the config missing (Fable r1 8.F5): no keywords to strip."""
    monkeypatch.setattr(gad, "vault_sealed_dir", lambda: Path("/v/sealed"))
    monkeypatch.setattr(gad, "load_sealed_meetings_config", lambda: None)
    dest = gad.resolve_destination({"title": "Session with Jordan",
                                    "created_at": "2026-11-01T17:00:00Z"}, "sealed", None, Path("/v"))
    assert dest["output_path"].name.endswith("-sealed-jordan-transcript.md"), dest["output_path"]
