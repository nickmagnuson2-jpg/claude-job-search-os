#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_paths.py — the one place the personal-vault root is resolved.

This repo is PUBLIC. The personal Obsidian vault is not, and its location is a
private fact: naming it in committed code discloses where sealed material
(therapy transcripts, family logistics, side-business notes) lives on disk.
Before 2026-08-11 the absolute root was hardcoded in 17 places across 7 public
files, so scrubbing any one of them was cosmetic.

Every consumer now derives its paths from `vault_root()`, which reads, in order:

  1. the PERSONAL_VAULT_ROOT environment variable
  2. the gitignored `tools/.personal-vault.conf` (first non-comment, non-blank
     line; `~` expanded)

MISSING CONFIG IS A LOUD FAILURE, NEVER A SILENT FALLBACK.
There is deliberately no default path. A fallback would have to be either the
real location (which puts it back in public code, defeating the point) or a
guess (which risks writing sealed therapy content somewhere unintended). Both
are worse than stopping. This matches `backup-data.sh` + `.private-backup.conf`,
which also exits non-zero rather than silently skipping.

Callers choose their failure mode:
  vault_root()          -> Path | None   for code that can degrade
  require_vault_root()  -> Path          raises VaultRootMissing, for code that cannot

Config format (`tools/.personal-vault.conf`):

    # Absolute path to the personal Obsidian vault. Gitignored: this repo is public.
    ~/path/to/personal-vault
"""
import os
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / ".personal-vault.conf"
ENV_VAR = "PERSONAL_VAULT_ROOT"

SETUP_HINT = (
    f"Set {ENV_VAR}, or create {DEFAULT_CONFIG} containing one line:\n"
    f"  the absolute path to your personal Obsidian vault (a leading ~ is expanded).\n"
    f"That file is gitignored because this repo is public."
)


class VaultRootMissing(RuntimeError):
    """Raised when the personal-vault root is not configured."""

    def __init__(self, detail: str = "personal-vault root is not configured"):
        super().__init__(f"{detail}\n{SETUP_HINT}")


def _read_config(config_path: Path) -> str | None:
    """First non-comment, non-blank line of the config file, or None."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def vault_root(config_path: Path | None = None) -> Path | None:
    """Resolve the personal-vault root, or None if unconfigured.

    Returns the path whether or not it exists on disk: existence is the
    caller's business, configuration is ours. Env var beats config file so a
    test or a one-off run can redirect without touching the real file.
    """
    env = os.environ.get(ENV_VAR, "").strip()
    raw = env or _read_config(config_path or DEFAULT_CONFIG)
    if not raw:
        return None
    return Path(raw).expanduser()


def require_vault_root(config_path: Path | None = None) -> Path:
    """Like vault_root(), but raises VaultRootMissing instead of returning None."""
    root = vault_root(config_path)
    if root is None:
        raise VaultRootMissing()
    return root


# ── Named locations (single source of truth; add here, never inline elsewhere) ──

def therapy_dir(config_path: Path | None = None) -> Path:
    """Sealed therapy transcripts. Never appears in any external-facing artifact."""
    return require_vault_root(config_path) / "data" / "therapy"


def personal_inbox(config_path: Path | None = None) -> Path:
    return require_vault_root(config_path) / "data" / "inbox.md"


def personal_mail_dir(config_path: Path | None = None) -> Path:
    """Fetched Personal-label email lands here, one markdown file per message.

    A directory, not the inbox.md file: gmail_fetch.py writes one file per message
    (mirroring the job-search `inbox/`), so pointing it at inbox.md would try to
    write files INTO a markdown file.

    CORRECTED 2026-08-19: the first version returned `<root>/data/mail`, which was
    INVENTED. The live gmail-fetch-personal launchd job had been writing to
    `<root>/inbox` for months, and that directory exists. Verified against the
    running job, not assumed.
    """
    return require_vault_root(config_path) / "inbox"


def personal_voice_corpus_dir(config_path: Path | None = None) -> Path:
    return require_vault_root(config_path) / "data" / "voice-corpus" / "granola"


def personal_todos(config_path: Path | None = None) -> Path:
    return require_vault_root(config_path) / "data" / "personal-todos.md"


def living_log(name: str, config_path: Path | None = None) -> Path:
    """Path for a living log, e.g. 'garden' -> <vault>/data/garden-log.md."""
    return require_vault_root(config_path) / "data" / f"{name}-log.md"
