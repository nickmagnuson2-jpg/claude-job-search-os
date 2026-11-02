#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_paths.py — the one place the personal-vault root is resolved.

This repo is PUBLIC. The personal Obsidian vault is not, and its location is a
private fact: naming it in committed code discloses where sealed material
(sealed transcripts, family logistics, side-business notes) lives on disk.
Before 2026-08-11 the absolute root was hardcoded in 17 places across 7 public
files, so scrubbing any one of them was cosmetic.

Every consumer now derives its paths from `vault_root()`, which reads, in order:

  1. the PERSONAL_VAULT_ROOT environment variable
  2. the gitignored `tools/.personal-vault.conf` (first non-comment, non-blank
     line; `~` expanded)

MISSING CONFIG IS A LOUD FAILURE, NEVER A SILENT FALLBACK.
There is deliberately no default path. A fallback would have to be either the
real location (which puts it back in public code, defeating the point) or a
guess (which risks writing sealed content somewhere unintended). Both
are worse than stopping. This matches `backup-data.sh` + `.private-backup.conf`,
which also exits non-zero rather than silently skipping.

Callers choose their failure mode:
  vault_root()          -> Path | None   for code that can degrade
  require_vault_root()  -> Path          raises VaultRootMissing, for code that cannot

Config format (`tools/.personal-vault.conf`):

    # Absolute path to the personal Obsidian vault. Gitignored: this repo is public.
    ~/path/to/personal-vault

SEALED FOLDERS (added 2026-11-01). Which vault folders are sealed is private too: a folder's
name says what it holds. They come from the gitignored `tools/.sealed-folders.conf`, one
vault-relative folder per line, and from nowhere else: no env var can change what the guard
protects. sealed_rels() returns None when the file is missing or lists nothing. check_sealed_read treats None as "every folder under the
vault's data/ is sealed" (fail closed), and sealed_dir() refuses instead of guessing.
"""
import os
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / ".personal-vault.conf"
ENV_VAR = "PERSONAL_VAULT_ROOT"
SEALED_CONFIG = Path(__file__).resolve().parent / ".sealed-folders.conf"

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

class SealedFolderMissing(RuntimeError):
    """Raised when no sealed vault folder is configured."""

    def __init__(self):
        super().__init__(
            f"no sealed vault folder is configured.\nCreate {SEALED_CONFIG} with one "
            f"vault-relative folder per line (e.g. data/<name>).\n"
            f"That file is gitignored because this repo is public.")


def sealed_rels(config_path: Path | None = None) -> tuple[str, ...] | None:
    """The vault-relative sealed folders, or None if none is configured.

    Raises ValueError for a folder that is absolute or leaves the vault: the guard and the
    writer would then both point somewhere other than the folder meant.
    """
    try:
        # utf-8-sig drops a BOM an editor may add; a BOM kept would name a folder that never
        # matches (Fable r1 F2).
        raw = (config_path or SEALED_CONFIG).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    rels = []
    for line in raw:
        line = line.split("#", 1)[0].strip()        # a trailing comment is not the name
        if not line:
            continue
        rel = os.path.normpath(line)
        if os.path.isabs(rel) or rel in (".", "..") or rel.startswith("../"):
            raise ValueError(f"sealed folder {line!r} is not inside the vault")
        rels.append(rel)
    return tuple(rels) or None


def sealed_dir(config_path: Path | None = None, sealed_config: Path | None = None) -> Path:
    """The folder sealed transcripts are written to: the first configured one.
    Never appears in any external-facing artifact."""
    root = require_vault_root(config_path)
    try:
        rels = sealed_rels(sealed_config)
    except ValueError:                  # a malformed config is no configured folder (Fable r1 F2)
        rels = None
    # A folder that is not there is a typo or a rename: writing would create a shadow folder
    # the guard does not protect (Fable r1 F5).
    if not rels or not (root / rels[0]).is_dir():
        raise SealedFolderMissing()
    return root / rels[0]


def main_checkout(root: Path) -> Path:
    """The main checkout a linked git worktree belongs to, or `root` itself.

    A linked worktree's `.git` is a FILE reading `gitdir: <main>/.git/worktrees/<name>`.
    Files that sit beside the repo (the personal vault, the -sealed tree) and gitignored
    config exist only beside and inside the main checkout, so a worktree must look there
    (Fable r1 7.F1). A plain file read: no git subprocess, safe inside a hook."""
    marker = Path(root) / ".git"
    try:
        if not marker.is_file():
            return Path(root)
        line = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return Path(root)
    if not line.startswith("gitdir:"):
        return Path(root)
    gitdir = Path(line[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = (Path(root) / gitdir).resolve()
    parts = gitdir.parts
    if ".git" not in parts:
        return Path(root)
    main = Path(*parts[:len(parts) - 1 - parts[::-1].index(".git")])
    return main if (main / ".git").is_dir() else Path(root)


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
