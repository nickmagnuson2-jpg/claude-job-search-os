#!/usr/bin/env python3
"""PreToolUse (Write): BLOCK creating a file under a NEW top-level directory that is
neither gitignored nor covered by tools/backup-data.sh.

A new top-level dir inherits NO protections. If it is not gitignored it lands in the
PUBLIC repo on the next `git add -A`; if it is gitignored but absent from
backup-data.sh's tracked list it is never backed up anywhere. Both happened.

Deterministic: two greps and a set test. No judgment, no content inspection.
Origin: feedback_a_new_toplevel_dir_inherits_no_protections (2026-08-16 fire 1,
2026-08-17 fire 2). Scaffolded per tools/HOOK_AUTHORING.md.
"""
import json
import os
import re
import subprocess
import sys

BACKUP = "tools/backup-data.sh"

# Dirs that exist for the harness, not for Nick's data. Never Nick-authored content.
EXEMPT_TOP = {".git", ".claude", "node_modules", "__pycache__", ".pytest_cache", ".venv"}


def repo_root():
    r = os.environ.get("CLAUDE_PROJECT_DIR")
    if r and os.path.isdir(r):
        return r
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:
        return None


def backup_covered_tops(root):
    """Top-level names appearing as $WORK_TREE/<name> in backup-data.sh."""
    tops = set()
    p = os.path.join(root, BACKUP)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                for m in re.finditer(r'\$WORK_TREE/([^/"\s]+)', line):
                    tops.add(m.group(1))
    except OSError:
        return None  # unreadable -> fail open
    return tops


def is_gitignored(root, rel):
    try:
        return subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=root, capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open on bad JSON

    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path") or ""
    if not fp:
        return 0

    root = repo_root()
    if not root:
        return 0

    try:
        rel = os.path.relpath(os.path.abspath(fp), root)
    except Exception:
        return 0
    if rel.startswith(".."):
        return 0  # outside the repo, not our business

    parts = rel.split(os.sep)
    if len(parts) < 2:
        return 0  # a top-level FILE, not a new top-level dir
    top = parts[0]
    if top in EXEMPT_TOP:
        return 0

    # Only fire when the top-level dir does not exist yet: this is a CREATION guard.
    if os.path.isdir(os.path.join(root, top)):
        return 0

    ignored = is_gitignored(root, rel)
    covered_tops = backup_covered_tops(root)
    if ignored is None or covered_tops is None:
        return 0  # fail open

    covered = top in covered_tops
    if ignored and covered:
        return 0            # gitignored + backed up: protected
    if (not ignored) and covered:
        return 0            # tracked publicly AND backed up
    if not ignored and not covered:
        problem = ("NOT gitignored (it will hit the PUBLIC repo on the next `git add -A`) "
                   "and NOT in backup-data.sh")
    else:
        problem = ("gitignored (so git will never carry it) but NOT in backup-data.sh, "
                   "so it is backed up NOWHERE")

    sys.stderr.write(
        "BLOCKED check_new_toplevel_dir_protected: creating `%s/` as a new top-level "
        "directory.\n\nIt is %s.\n\n"
        "A new top-level dir inherits NO protections. Decide both axes before writing:\n"
        "  1. Public or private?  -> add `%s/` to .gitignore if it holds personal data\n"
        "  2. Backed up?          -> add \"$WORK_TREE/%s/\" to the path list in %s\n\n"
        "Verify:  git check-ignore -v %s   &&   grep -n '%s' %s\n\n"
        "If this path is deliberately ephemeral, write it under an existing scratch dir "
        "instead of a new top-level one.\n"
        % (top, problem, top, top, BACKUP, rel, top, BACKUP)
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
