#!/usr/bin/env python3
"""
check_todo_write_kwargs.py — PreToolUse hook for Bash.

Warns when a Bash command invokes tools/todo_write.py with kwargs
(--task, --priority, --due, --notes). todo_write.py is positional-only
for its `add` subcommand; kwarg invocations are rejected at argparse
with "Unknown flag(s)" — burning a turn for a recoverable pattern.

This hook fires BEFORE the tool call so Claude can correct the
invocation without paying the argparse-failure cost.

BLOCK tier (exit 2) — interrupts the invocation. The pattern this
hook fires on (todo_write.py + --task|--priority|--due|--notes) is
100% wrong with a deterministic single-correct replacement (positional
args). Per memory/feedback_warn_vs_block_hook_design.md: BLOCK is
appropriate for unambiguous structural violations with a known
correction path. The original WARN-tier design (exit 0 + stderr) was
not surfaced by Claude Code's PreToolUse stderr handling — discovered
during T8 e2e testing 2026-05-28; switched to BLOCK exit 2 to ensure
visibility.

Detection: an ACTUAL `python … todo_write.py … --(task|priority|due|notes)`
invocation. Two guards against the command-position-not-substring blind spot:
  1. Quoted literal spans are stripped first (single-quoted always; double-quoted
     only when they contain no `$`/backtick command-substitution), so a
     `todo_write.py --due` *inside* a string argument — a friction-log nature, a
     `python3 -c "..."` body, a grep pattern, a commit message — is not mistaken
     for a real invocation.
  2. The match requires `python`/`python3` before `todo_write.py`, so a bare
     `echo todo_write.py --priority` reference does not fire.
Explicit allowlist of the four kwargs Claude reaches for (not a negative
lookahead) so legitimate flags like --repo-root are silently passed.

This quote-awareness was added 2026-06-02 after the original substring regex
fired twice in one session on legitimate work: a `friction_log.py append
"todo_write.py" "...--due..."` call and a `python3 -c` smoke harness whose
payload string contained the pattern. Same fix check_bare_python.py got. See
[[feedback_command_hook_match_position_not_substring]].

Hook input: JSON via stdin from Claude Code.
  {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (no kwargs detected, or parse failure → fail-open)
  2 — kwargs detected, tool call BLOCKED with positional usage shown

Origin: Phase E NEW-D2 (2026-05-28 lessons audit).
Friction-log: 10+ fires of "Unknown flag(s): --task, --priority, --due"
across 5/27-5/28 after the in-script argparse patch failed to stop
pre-invocation pattern recognition. See:
  - memory/archive-2026-04-orphans.md (superseded, rule still enforced by this hook)
  - friction-log row 40, 44, 50-52
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# Require an actual `python … todo_write.py … --flag` invocation (not a bare
# substring). `python` anchor + kwarg allowlist; applied AFTER literal-stripping.
KWARGS_PATTERN = re.compile(
    r"python3?\b[^\n]*?\btodo_write\.py\b(?P<tail>[^\n]*?)--(task|priority|due|notes)\b"
)

# `update` (added 2026-08-14) is the one MUTATING subcommand that legitimately takes
# these flags: it edits a single field in place, so naming the field IS the interface.
# The alternative was positional sentinels for every column you are NOT changing, which
# reintroduces the retyping the verb exists to delete.
#
# This exemption is SUBCOMMAND-POSITION, never substring: the token is read from the
# argument slot after todo_write.py (skipping `--repo-root <path>` pairs), so a stray
# the word "update" elsewhere in the command line cannot unlock the guard.
# [[feedback_command_hook_match_position_not_substring]] — the blind spot this whole
# hook family keeps re-learning.
FLAG_TAKING_SUBCOMMANDS = {"update", "list", "sync"}


def subcommand_of(tail: str) -> str:
    """The subcommand token in `todo_write.py [--repo-root P] <cmd> ...`, or ''."""
    toks = tail.split()
    i = 0
    while i < len(toks):
        if toks[i] == "--repo-root":
            i += 2
            continue
        if toks[i].startswith("-"):
            return ""          # a flag before any subcommand: not an exempt form
        return toks[i]
    return ""

# Literal spans (quoted strings + heredoc bodies) are stripped before matching via the
# shared tools/hook_command_lint.py so the pattern inside a string argument
# (friction-log nature, `-c` body, grep pattern, commit message, heredoc body) is not
# mistaken for a real invocation. Shared with check_bare_python.py — single source of
# truth so the strip logic cannot drift (the divergence is what caused this family's
# 4th/5th fires). [[feedback_command_hook_match_position_not_substring]].

USAGE_REMINDER = (
    "todo_write.py is POSITIONAL-ONLY for the `add` subcommand.\n"
    "Pass bare values, NOT --flag names. Examples:\n"
    "\n"
    '  python3 tools/todo_write.py add "<task>" "<priority>" "<due>" "<notes>"\n'
    '  python3 tools/todo_write.py add "Apply to Blueprints AI" "High" "2026-06-10" "warm intro via the case-runner"\n'
    '  python3 tools/todo_write.py add "Quick task" "Low"   # priority only\n'
    '  python3 tools/todo_write.py add "Med task" "Med" "" "notes only, no due"\n'
    "\n"
    "Other subcommands:\n"
    "  python3 tools/todo_write.py done <fragment>      # fuzzy substring match (a completion)\n"
    "  python3 tools/todo_write.py withdraw <fragment>  # mark Withdrawn, not a completion\n"
    "  python3 tools/todo_write.py clear                # archive completed\n"
    "  python3 tools/todo_write.py sync                 # rebuild from data files\n"
    "\n"
    "  python3 tools/todo_write.py update <fragment> --notes-append \"text\"\n"
    "                                                   # edit ONE field in place;\n"
    "                                                   # `update` DOES take these flags\n"
    "\n"
    "The --repo-root flag IS accepted (positional anywhere), and `update`/`list`/`sync`\n"
    "take real flags. Only --task / --priority / --due / --notes on `add` trigger this.\n"
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")

    if not command:
        sys.exit(0)

    match = KWARGS_PATTERN.search(strip_literals(command))
    if not match:
        sys.exit(0)

    # Subcommands that legitimately take these flags are not the failure this hook
    # guards. Checked by argument position, not by substring presence.
    if subcommand_of(match.group("tail")) in FLAG_TAKING_SUBCOMMANDS:
        sys.exit(0)

    flag = match.group(2)
    print(
        f"BLOCKED: todo_write.py invoked with --{flag} (kwarg). Argparse will reject this.\n"
        "\n"
        + USAGE_REMINDER
        + "\n"
        "Reference: memory/archive-2026-04-orphans.md (superseded, rule still enforced by this hook)\n"
        "Friction-log: 10+ fires of this exact pattern across 5/27-5/28.\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
