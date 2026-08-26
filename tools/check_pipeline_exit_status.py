#!/usr/bin/env python3
"""
check_pipeline_exit_status.py — PreToolUse hook for Bash.

WHY THIS EXISTS
---------------
A shell verdict taken from a pipeline tests the LAST command in the pipeline,
not the command under test. When that last stage is a pure formatter (`sed`,
`tail`, `head`, `tr`, `cut`, `wc`, ...), its exit status is essentially always
0 — so the failure branch can never fire and the check reports "pass"
unconditionally. That is a check with NO failing mode, which is worse than no
check at all: it is indistinguishable from a working one, and it is trusted
precisely at the moment it matters.

Measured, twice, in this repo (memory/feedback_pipeline_masks_the_exit_status_
you_are_testing.md, occurrences: 2):

  1. 2026-08-13, verifying that a proposed backup mirror could not leak into the
     PUBLIC repo:

         git check-ignore -v claude-global/settings.json 2>&1 | sed 's/^/  /' \\
           || echo "  NOT IGNORED — a new top-level mirror dir would leak"

     `git check-ignore` exits 1 when a path is NOT ignored — the exact condition
     under test — but `||` bound to `sed`, so the warning never printed. Blank
     output read as "nothing to report." Rerun as `if <bare cmd>; then ...`, it
     reported NOT IGNORED for all three candidate paths and changed the design.

  2. 2026-08-14, testing whether `check_public_pii.py --scan` fails on an empty
     sweep:

         python3 tools/check_public_pii.py --scan <path> 2>&1 | tail -14
         echo "scan exit=$?"      # <- reports tail's status, not the script's

     Reported `scan exit=0` to Nick as evidence. The script actually exits 1.
     The wrong number shipped into a user-facing evidence claim.

WHAT IT TESTS (a property, not a presence)
------------------------------------------
Not "did you write a verdict" — the check proves the shape is ALWAYS-WRONG:
the exit status being consumed belongs to a final pipeline stage drawn from a
closed list of formatters whose status never encodes the predicate under test.
Two consumption shapes are flagged, both taken verbatim from the two fires:

  A.  <pipeline ending in a formatter> || <anything>
  B.  <pipeline ending in a formatter> ; (or newline) <statement reading `$?`>

`grep`, `awk`, `jq`, `test`, `diff`, `python3` etc. are deliberately NOT in the
formatter list: their exit status IS informative, so `cmd | grep -q x || echo
missing` is a legitimate verdict and must stay clean.

ALLOWLIST — the two correct ways to keep the pipe
-------------------------------------------------
If the command mentions `PIPESTATUS` or `pipefail`, the author is handling the
pipeline status explicitly and the hook exits 0.

BLOCK tier (exit 2) per feedback_warn_vs_block_hook_design.md: an exit-0 WARN on
PreToolUse is never surfaced by Claude Code, and this pattern has a single
deterministic correction (unpipe the command under test, or use PIPESTATUS).

Hook input: JSON via stdin from Claude Code. {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (no formatter-terminated pipeline feeding a verdict, PIPESTATUS/
      pipefail present, or parse failure -> fail-open)
  2 — verdict taken from a pipeline's exit status, tool call BLOCKED

Origin: memory/feedback_pipeline_masks_the_exit_status_you_are_testing.md
(2026-08-13 fire 1, 2026-08-14 fire 2 + "rule sharpened" supplement).
"""
import json
import os
import re
import sys

# Shared literal-context stripping (quoted spans + heredoc bodies). Single
# source of truth — never re-implement per hook (that drift caused the
# command-position family's 4th/5th fires). Installed layout puts this file in
# tools/ next to hook_command_lint.py; PYTHONPATH covers the staging layout.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# Final-stage commands whose exit status is a constant 0 in a pipeline — it can
# never carry the predicate under test. Informative-status commands (grep, awk,
# jq, test, diff, cmp, python3, ...) are intentionally absent.
FORMATTERS = frozenset(
    """
    sed tail head tr cut cat tee column fold nl rev fmt pr wc sort uniq
    expand unexpand paste tac less more strings hexdump xxd od csplit
    """.split()
)

# `set -o pipefail` / ${PIPESTATUS[0]} — author is handling pipeline status.
PIPE_AWARE = re.compile(r"PIPESTATUS|pipefail")

_VAR_PREFIX = re.compile(r"^\s*(?:\w+=\S*\s+)*")
_FIRST_WORD = re.compile(r"[\w./+-]+")
_AND_OR = re.compile(r"(\|\||&&)")
_LINE_CONT = re.compile(r"\\\n")


def _first_word(segment: str) -> str:
    """First actual command word of a pipeline stage, basename-normalised."""
    stripped = _VAR_PREFIX.sub("", segment)
    m = _FIRST_WORD.match(stripped)
    if not m:
        return ""
    return os.path.basename(m.group(0))


def _terminal_formatter(element: str) -> str | None:
    """If `element` is a pipeline whose LAST stage is a pure formatter, return
    that formatter's name; otherwise None."""
    if "|" not in element:
        return None
    last_stage = element.rsplit("|", 1)[1]
    word = _first_word(last_stage)
    return word if word in FORMATTERS else None


def find_violation(command: str) -> tuple[str, str] | None:
    """Return (formatter, shape) for the first masked-exit-status verdict, else
    None. `shape` is "||" (fire 1) or "$?" (fire 2)."""
    if PIPE_AWARE.search(command):
        return None

    text = _LINE_CONT.sub(" ", command)
    text = strip_literals(text)

    # Statements: top-level `;` / newline. `||` and `&&` contain neither, so
    # and-or lists survive intact inside a statement.
    statements = re.split(r"[;\n]", text)

    for i, stmt in enumerate(statements):
        parts = _AND_OR.split(stmt)
        # parts = [element, op, element, op, element, ...]
        for j in range(0, len(parts), 2):
            fmt = _terminal_formatter(parts[j])
            if not fmt:
                continue
            # Shape A: an `||` verdict hangs off this pipeline.
            if j + 1 < len(parts) and parts[j + 1] == "||":
                return fmt, "||"
            # Shape B: the next statement reads `$?`.
            if j + 2 >= len(parts):
                for nxt in statements[i + 1:]:
                    if not nxt.strip():
                        continue
                    if "$?" in nxt:
                        return fmt, "$?"
                    break
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    hit = find_violation(command)
    if not hit:
        sys.exit(0)

    fmt, shape = hit
    consumer = "`||`" if shape == "||" else "`$?`"
    print(
        f"BLOCKED: pipeline masks the exit status you are testing "
        f"({consumer} is reading `{fmt}`, not the command under test).\n"
        "\n"
        f"`{fmt}` in a pipeline exits 0 essentially always, so the failure "
        "branch can never fire and this check reports pass unconditionally — a "
        "check with no failing mode.\n"
        "\n"
        "Correct forms:\n"
        "  if cmd \"$X\"; then echo '  ok'; else echo '  FAILED'; fi\n"
        "  out=$(cmd \"$X\") || { echo '  FAILED'; exit 1; }\n"
        "  cmd \"$X\" > /tmp/o.txt 2>/tmp/e.txt; echo \"exit=$?\"\n"
        f"  cmd \"$X\" | {fmt} ...; [ \"${{PIPESTATUS[0]}}\" -eq 0 ] || echo FAILED\n"
        "\n"
        "When the EXIT STATUS is the thing under test, the command must not be "
        "in a pipeline at all. Format after the verdict is computed, not in the "
        "same expression.\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
