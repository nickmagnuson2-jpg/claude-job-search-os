#!/usr/bin/env python3
"""
check_sealed_read.py — PreToolUse hook (Read|Grep|Glob|Bash).

Defense-in-depth for the sealed vault. `data/project-background/` is SEALED: its
contents must never appear in any external-facing artifact (CVs, cover letters,
recruiter prep, voice exports, networking notes). The seal is already enforced by
a CLAUDE.md hard rule, by .gitignore (it can't reach the public repo), and by a
`sealed: true` header on every file. This hook adds a fourth layer: it BLOCKS a
tool call that reads under the sealed directory, so sealed content cannot be
pulled into context (and from there into output) without a deliberate override.

Why BLOCK, not WARN: per tools/HOOK_AUTHORING.md, a PreToolUse exit-0+stderr "warn"
is invisible to Claude Code. A visible signal must be exit 2. To keep intentional
maintenance workable there is an explicit override:
  - session-wide:   set SEAL_READ_OK=1 in the environment, or
  - one Bash call:  prefix the command with SEAL_READ_OK=1

Exit 2 = block. Any other path = allow. Fails OPEN on any error.
Wired via .claude/settings.json PreToolUse on Read|Grep|Glob|Bash.
"""
import json
import os
import sys
from pathlib import Path

SEALED_REL = "data/project-background"


def _root() -> Path:
    env = os.environ.get("SEAL_REPO_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parent.parent


def _under_sealed(root: Path, p: str) -> bool:
    if not p:
        return False
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(str(root), p)
    rel = os.path.relpath(os.path.normpath(p), str(root)).replace(os.sep, "/")
    return rel == SEALED_REL or rel.startswith(SEALED_REL + "/")


def _overridden(command: str = "") -> bool:
    if os.environ.get("SEAL_READ_OK") == "1":
        return True
    # Inline override for a single Bash call: `SEAL_READ_OK=1 <cmd>`.
    return "SEAL_READ_OK=1" in command


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    root = _root()

    targets = []
    command = ""
    if tool == "Read":
        targets = [ti.get("file_path", "")]
    elif tool in ("Grep", "Glob"):
        targets = [ti.get("path", "")]
    elif tool == "Bash":
        command = ti.get("command", "") or ""
        # Substring match: any explicit reference to the sealed path in the command.
        if SEALED_REL in command:
            targets = [SEALED_REL]
    else:
        return

    hit = None
    if tool == "Bash":
        hit = SEALED_REL if targets else None
    else:
        for t in targets:
            if _under_sealed(root, t):
                hit = t
                break

    if not hit or _overridden(command):
        return

    msg = [
        f"BLOCKED: this {tool} targets the SEALED directory {SEALED_REL}/.",
        "",
        "Sealed content (Zuora/McKinsey raw materials, personal captures) must never",
        "enter context that could reach an external-facing artifact (CV, cover letter,",
        "recruiter prep, voice export, networking note).",
        "",
        "If this is intentional sealed-file maintenance, override:",
        "  - one Bash call:  prefix with  SEAL_READ_OK=1",
        "  - this session:   export SEAL_READ_OK=1",
        "",
        "If this block is wrong, log it:",
        '  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append check_sealed_read.py "FP: <what>"',
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
