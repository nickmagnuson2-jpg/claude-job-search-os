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
    monkeypatch.setattr(gad, "vault_therapy_dir",
                        lambda: called.append("vault") or Path("/nonexistent-vault/data/therapy"))
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
    """therapy -> vault. With no vault it must refuse, not write and not crash."""
    monkeypatch.setattr(gad, "vault_therapy_dir",
                        lambda: Path("/nonexistent-vault/data/therapy"))
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "therapy")
    assert result["status"] == "error", "therapy with no vault must refuse"
    assert result["reason"] == "vault-missing"


def test_guard_reports_the_path_it_checked(monkeypatch):
    """The error must name the expected vault, so a wrong path is debuggable."""
    monkeypatch.setattr(gad, "vault_therapy_dir",
                        lambda: Path("/nonexistent-vault/data/therapy"))
    result = gad.persist_via_granola_save(MEETING, "summary text", "", "therapy")
    assert "expected_vault" in result and result["expected_vault"], (
        "guard must report which vault path it checked"
    )
