#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/vault_paths.py — the single source of truth for the
personal-vault root.

The property under test is not "does it return a path." It is: **an
unconfigured vault must fail loudly, never fall back to a guess.** The root is
private (this repo is public) and it hosts sealed therapy material, so a
fallback would have to be either the real location — putting it back in public
code, defeating the point — or a guess, which risks writing sealed content
somewhere unintended. Both are worse than stopping.

So the negative tests below are the load-bearing ones.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import vault_paths as vp  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Never let the developer's real env leak into a test."""
    monkeypatch.delenv(vp.ENV_VAR, raising=False)


def _conf(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".personal-vault.conf"
    p.write_text(body, encoding="utf-8")
    return p


# --- resolution ------------------------------------------------------------

def test_reads_root_from_config_file(tmp_path):
    cfg = _conf(tmp_path, "/srv/vault\n")
    assert vp.vault_root(cfg) == Path("/srv/vault")


def test_expands_leading_tilde(tmp_path):
    cfg = _conf(tmp_path, "~/somewhere/vault\n")
    root = vp.vault_root(cfg)
    assert "~" not in str(root)
    assert root == Path.home() / "somewhere/vault"


def test_skips_comments_and_blank_lines(tmp_path):
    cfg = _conf(tmp_path, "# a comment\n\n   \n/srv/vault\n# trailing\n")
    assert vp.vault_root(cfg) == Path("/srv/vault")


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    cfg = _conf(tmp_path, "/from/file\n")
    monkeypatch.setenv(vp.ENV_VAR, "/from/env")
    assert vp.vault_root(cfg) == Path("/from/env")


def test_env_var_works_with_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv(vp.ENV_VAR, "/only/env")
    assert vp.vault_root(tmp_path / "absent.conf") == Path("/only/env")


def test_blank_env_var_falls_through_to_config(tmp_path, monkeypatch):
    cfg = _conf(tmp_path, "/from/file\n")
    monkeypatch.setenv(vp.ENV_VAR, "   ")
    assert vp.vault_root(cfg) == Path("/from/file")


def test_root_need_not_exist_on_disk(tmp_path):
    """Configuration is our concern; existence is the caller's."""
    cfg = _conf(tmp_path, "/definitely/not/here\n")
    assert vp.vault_root(cfg) == Path("/definitely/not/here")


# --- the load-bearing negatives --------------------------------------------

@pytest.mark.parametrize("body", ["", "\n\n", "# only a comment\n", "   \n\t\n"])
def test_empty_or_commentonly_config_is_unconfigured(tmp_path, body):
    assert vp.vault_root(_conf(tmp_path, body)) is None


def test_missing_config_is_unconfigured_not_a_guess(tmp_path):
    assert vp.vault_root(tmp_path / "absent.conf") is None


def test_config_path_that_is_a_directory_is_unconfigured(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    assert vp.vault_root(d) is None


def test_require_raises_when_unconfigured(tmp_path):
    with pytest.raises(vp.VaultRootMissing):
        vp.require_vault_root(tmp_path / "absent.conf")


def test_missing_root_error_tells_you_how_to_fix_it(tmp_path):
    """An error that does not say what to do gets worked around, not fixed."""
    with pytest.raises(vp.VaultRootMissing) as exc:
        vp.require_vault_root(tmp_path / "absent.conf")
    msg = str(exc.value)
    assert vp.ENV_VAR in msg
    assert ".personal-vault.conf" in msg
    assert "gitignored" in msg


@pytest.mark.parametrize("fn", ["therapy_dir", "personal_inbox",
                                "personal_voice_corpus_dir", "personal_todos"])
def test_every_named_location_refuses_when_unconfigured(tmp_path, fn):
    """No named location may silently resolve against a guessed root."""
    with pytest.raises(vp.VaultRootMissing):
        getattr(vp, fn)(tmp_path / "absent.conf")


def test_living_log_refuses_when_unconfigured(tmp_path):
    with pytest.raises(vp.VaultRootMissing):
        vp.living_log("garden", tmp_path / "absent.conf")


# --- named locations -------------------------------------------------------

def test_named_locations_derive_from_the_configured_root(tmp_path):
    cfg = _conf(tmp_path, "/srv/vault\n")
    assert vp.therapy_dir(cfg) == Path("/srv/vault/data/therapy")
    assert vp.personal_inbox(cfg) == Path("/srv/vault/data/inbox.md")
    assert vp.personal_voice_corpus_dir(cfg) == Path("/srv/vault/data/voice-corpus/granola")
    assert vp.personal_todos(cfg) == Path("/srv/vault/data/personal-todos.md")
    assert vp.living_log("garden", cfg) == Path("/srv/vault/data/garden-log.md")


def test_no_public_file_hardcodes_the_vault_root():
    """Regression guard for the sweep itself.

    The root was hardcoded in 17 places across 7 public files; scrubbing any one
    of them was cosmetic while the others remained. This fails if one comes back.
    Scoped to tracked public artifacts — data/, output/ and memory/ are
    gitignored and may legitimately name real paths.
    """
    import subprocess
    # Built from parts on purpose: spelling the path literally here would put it
    # in a tracked public file, and this guard would then match ITSELF and fail on
    # every run. A self-flagging guard gets deleted rather than fixed.
    needle = "Obsidian/30-projects/" + "personal"
    out = subprocess.run(
        ["git", "grep", "-l", "-I", needle, "--", "*.py", "*.md", "*.sh"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.split()
    offenders = [f for f in out if not f.startswith(("data/", "output/", "memory/",
                                                     "tests/fixtures/"))]
    assert offenders == [], (
        "the personal-vault root is hardcoded again in: " + ", ".join(offenders)
        + " — derive it from tools/vault_paths.py instead"
    )


# --- personal_mail_dir (added 2026-08-18 for gmail_fetch --personal) ---------

def test_personal_mail_dir_is_a_directory_under_data(tmp_path):
    """A DIRECTORY, not inbox.md: gmail_fetch writes one markdown file per message,
    so pointing it at a file would try to write files into a file."""
    cfg = _conf(tmp_path, "/srv/vault\n")
    # <root>/inbox, matching the destination the live gmail-fetch-personal job has
    # always used. An earlier draft invented <root>/data/mail; see the docstring.
    assert vp.personal_mail_dir(cfg) == Path("/srv/vault/inbox")


def test_personal_mail_dir_is_distinct_from_personal_inbox(tmp_path):
    cfg = _conf(tmp_path, "/srv/vault\n")
    assert vp.personal_mail_dir(cfg) != vp.personal_inbox(cfg)


def test_personal_mail_dir_raises_when_unconfigured(tmp_path):
    """Loud failure, never a silent fallback: guessing could write personal mail
    into the PUBLIC repo. (_clear_env is autouse, so the real env is already out.)"""
    missing = tmp_path / "absent.conf"
    with pytest.raises(vp.VaultRootMissing):
        vp.personal_mail_dir(missing)
