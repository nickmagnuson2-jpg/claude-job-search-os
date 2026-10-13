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

Detection: an ACTUAL `python ... todo_write.py <subcommand> ... --flag` invocation,
judged ONE SIMPLE COMMAND AT A TIME. The command is tokenized by the shared
tools/shell_tokens.py (the tokenizer check_public_pii.py segments with), so:
  1. Each todo_write.py call is judged against ITS OWN subcommand. `withdraw X;
     update Y --notes-append Z` is two calls, and the flag belongs to `update`.
  2. A quoted span is one word, so a `todo_write.py --due` inside a string argument
     (a friction-log nature, a `python3 -c "..."` body, a grep pattern, a commit
     message) can never be read as the script or as a flag. Heredoc bodies are not
     words at all.
  3. todo_write.py must be the SCRIPT python runs (the first non-option word after
     `python`/`python3`), so `echo todo_write.py --priority` and
     `python3 tools/other.py todo_write.py --due` do not fire.
  4. A command inside `$(...)` or backticks runs, so it is judged too.
Explicit allowlist of the four kwargs Claude reaches for, so legitimate flags like
--repo-root are silently passed.

History. 2026-06-02: quote-awareness added after the original substring regex fired
twice in one session on legitimate work. 2026-10-12: per-command judging replaced a
single regex that read the FIRST todo_write.py subcommand on the line for the whole
Bash call. That failed in both directions: 10 of the 18 blocks found in the session
transcripts were `done`/`withdraw` chained before a valid `update --flag`, and
`update Y --notes-append Z; add T --priority High` passed because the exempt `update`
was the subcommand read. See [[feedback_command_hook_match_position_not_substring]]
and [[feedback_todo_hook_blocks_chained_update_notes_append]].

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
from shell_tokens import simple_commands  # noqa: E402
from hook_runtime import read_payload  # noqa: E402

# The four kwargs, matched against ONE argument word. `\b` keeps `--notes-append` and
# `--due=X` inside the match and `--duet` outside it.
KWARG = re.compile(r"--(task|priority|due|notes)\b")

# The interpreter word, by basename: python, python3, python3.12, /usr/bin/python3.
PYTHON = re.compile(r"python(\d+(\.\d+)*)?")

# Interpreter options that take the NEXT word as their value.
_PYTHON_OPTS_WITH_VALUE = {"-X", "-W"}

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


def subcommand_of(tail) -> str:
    """The subcommand token in `todo_write.py [--repo-root P] <cmd> ...`, or ''.

    `tail` is the argument words after the script (a list), or the same as one string.
    """
    toks = tail.split() if isinstance(tail, str) else list(tail)
    i = 0
    while i < len(toks):
        if toks[i] == "--repo-root":
            i += 2
            continue
        if toks[i].startswith("-"):
            return ""          # a flag before any subcommand: not an exempt form
        return toks[i].lower()     # todo_write.py lowercases its subcommand
    return ""


def todo_write_args(words: list[str]):
    """The argument words of a `python ... todo_write.py ...` call, or None.

    `words` is ONE simple command. todo_write.py has to be the script the interpreter
    runs: the first word after it that is not an interpreter option. `-c` and `-m` run
    no script file, so what follows them is never this script.
    """
    for i, word in enumerate(words):
        if not PYTHON.fullmatch(os.path.basename(word)):
            continue
        j = i + 1
        while j < len(words) and words[j].startswith("-") and words[j] != "-":
            if words[j][:2] in ("-c", "-m"):
                j = len(words)
            elif words[j] in _PYTHON_OPTS_WITH_VALUE:
                j += 2
            else:
                j += 1
        if j < len(words) and os.path.basename(words[j]) == "todo_write.py":
            return words[j + 1:]
    return None


def find_violation(command: str):
    """(subcommand, flag) for the first todo_write.py call that passes one of the four
    kwargs to a subcommand that does not take them, or None. Pure.

    Each simple command is judged on its own, so a flag is only ever compared with the
    subcommand of the call that carries it.
    """
    for cmd in simple_commands(command):
        args = todo_write_args(cmd.words)
        if args is not None:
            sub = subcommand_of(args)
            if sub not in FLAG_TAKING_SUBCOMMANDS:
                for arg in args:
                    m = KWARG.match(arg)
                    if m:
                        return sub, m.group(1)
        for inner in cmd.subs:          # `$(...)` and backticks run too
            found = find_violation(inner)
            if found:
                return found
    return None

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
    "take real flags. --task / --priority / --due / --notes trigger this on every other\n"
    "subcommand (`add`, `done`, `withdraw`, ...), judged per command in a chained call.\n"
)


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)
    data = p.data

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")

    if not command:
        sys.exit(0)

    found = find_violation(command)
    if not found:
        sys.exit(0)

    sub, flag = found
    where = f"`{sub}`" if sub else "no subcommand before the flag"
    print(
        f"BLOCKED: todo_write.py invoked with --{flag} (kwarg) on {where}. "
        "Argparse will reject this.\n"
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
