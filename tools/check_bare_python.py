#!/usr/bin/env python3
"""
check_bare_python.py — PreToolUse hook for Bash.

Fires when a Bash command invokes the bare `python` interpreter instead of
`python3`. macOS ships no `python` binary, so every bare invocation fails with
`zsh: command not found: python` (exit 127) — burning a turn for a recoverable
typo with a single deterministic fix: add the `3`.

This is the structural promotion of memory rule [[project_macos_python3]]. The
friction ledger recorded this error at 5 fires across 5/27-6/02 (it had been
fragmenting into per-surface rows at count 1 until the dedup fix on 2026-06-02
collapsed them, surfacing the `script-patch (mandatory)` promotion). Memory-tier
capture did not stop recurrence; this hook is the hook-tier enforcement.

BLOCK tier (exit 2). Per memory/feedback_warn_vs_block_hook_design.md: BLOCK is
correct for an unambiguous structural violation with a known single correction.
Note also (from check_todo_write_kwargs.py's T8 finding) that WARN-tier exit 0 +
stderr is NOT surfaced by Claude Code's PreToolUse handling — a literal "warn"
would be invisible. Exit 2 with a corrective message is the effective warning.
Since bare `python` always fails on this machine anyway, blocking pre-emptively
is strictly better than letting it hit command-not-found.

Detection: `python` in COMMAND POSITION only — at the start of the command or
right after a command separator (`|`, `;`, `&`, newline, `(`, backtick), with
optional leading `VAR=val` env assignments, and not followed by a word char,
`.`, or `-` (so `python3`, `python-dateutil`, `python.md` stay clean). Crucially
it does NOT fire on `python` inside a quoted string, a grep pattern, a commit
message, or an argument (`git commit -m "fix python bug"`, `grep python f`) —
those are preceded by a space/quote, not a command boundary. This command-
position constraint was added after a live smoke test caught the naive
"`python` anywhere" regex blocking its own echo'd test payload (2026-06-02).

Hook input: JSON via stdin from Claude Code.  {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (uses python3, no bare python, or parse failure → fail-open)
  2 — bare `python` detected, tool call BLOCKED with the python3 correction shown

Origin: 2026-06-02 friction-log dedup fix surfaced the hidden mandatory-patch
promotion. See memory/MEMORY.md [[project_macos_python3]].
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# Command boundary = start, newline, or a separator (`|`, `;`, `&`, `(`). Backtick
# is deliberately EXCLUDED: `` `python` `` in a commit message or doc is inline code,
# not command substitution (caught blocking its own commit, 2026-06-02), and modern
# `$(...)` substitution is already covered by `(`.
BARE_PYTHON = re.compile(r"(?:^|[\n;&|(])\s*(?:\w+=\S+\s+)*python(?![\w.\-])")

# Literal spans (quoted strings + heredoc bodies) are stripped before matching so a
# `(` or the `python` token *inside* a string argument, grep pattern, commit message,
# JSON payload, or heredoc body is not mistaken for a real invocation. Shared with
# check_todo_write_kwargs.py via tools/hook_command_lint.py — single source of truth so
# the strip logic cannot drift (the 4th/5th fires of this family came from the fix
# living in one hook's copy and not the other's). Heredoc-body stripping added
# 2026-06-05 when the parked "2nd heredoc fire" gate tripped.
# [[feedback_command_hook_match_position_not_substring]].


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    if not BARE_PYTHON.search(strip_literals(command)):
        sys.exit(0)

    print(
        "BLOCKED: bare `python` invocation detected. macOS has no `python` "
        "binary — this fails with `command not found` (exit 127).\n"
        "\n"
        "Use `python3` instead:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/<script>.py ...\n"
        "\n"
        "(All tools/*.py scripts also require the PYTHONIOENCODING=utf-8 prefix.)\n"
        "Reference: memory rule [[project_macos_python3]].\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
