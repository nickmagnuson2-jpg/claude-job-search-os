#!/usr/bin/env python3
"""
check_unquoted_glob.py — PreToolUse hook for Bash.

Fires when a Bash command passes an unquoted glob pattern to `--include=`/
`--exclude=` (e.g. `grep -r foo --include=*.md .`). zsh expands the glob
BEFORE the command runs; if no matching file exists in the current directory
(the common case — the glob is meant to filter grep's OWN search, not match
something already in cwd), zsh aborts the entire command with
`zsh: no matches found: --include=*.md` (exit 1) before grep ever executes.

This is always a bug when it fires — there is no legitimate reason to leave
`--include=`/`--exclude=` unquoted, and the deterministic single correction is
to quote the value. Friction-log surface `bash:(find` sat at 14 occurrences,
all this exact "no matches found: --include=*.md" shape (misattributed to
"find" by the coarse `bash:<first-token>` fallback — the actual command was a
`grep --include=` invocation grouped in a subshell/pipe).

REWRITE before BLOCK (2026-11-16, Nick's Q7 ruling, option a). The block cost a
median 4.5 s and 609 output tokens per fire across 79 transcript blocks
(output/infra-backlog/111626-telemetry/block-cost.md), and 78 of the 79 were the
plain `--include=*.ext` shape. So when EVERY flagged occurrence is the simple
shape, the hook returns the command with each value single-quoted, via
`hookSpecificOutput.updatedInput` on exit 0, instead of blocking.

  - NO permissionDecision is returned (Nick, Q7). The rewritten command goes
    through the normal permission flow; this hook grants nothing. In auto mode
    the docs say such a call CAN be denied by the classifier
    (hooks.md "PreToolUse updatedInput in auto mode").
  - updatedInput replaces the WHOLE tool_input, so every other field is copied.
  - The rewrite only changes quoting. That is checked, not assumed: the command
    must parse with shlex before and after, to the SAME word list. An occurrence
    that is really inside a quoted string changes the words and falls back to BLOCK.
  - Anything outside the simple shape (brace or bracket globs, $, backslash,
    heredocs, a value running into a quote) keeps today's BLOCK.
  - Detection is unchanged: strip_literals decides WHETHER to act, as before, so a
    command it reads as clean stays clean and no output is produced.
  - Other PreToolUse hooks run in parallel on the ORIGINAL command
    (hooks.md: "its matching hooks run in parallel") and are not re-run on the
    rewrite; a deny from any of them still wins over this hook ("deny > ... > allow").

Hook input: JSON via stdin from Claude Code. {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (no output), OR rewritten (hookSpecificOutput JSON on stdout), OR
      parse failure -> fail-open
  2 — unquoted glob detected and no safe rewrite, tool call BLOCKED

Origin: 2026-07-08 friction-log audit. Rewrite: 2026-11-16.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402
from hook_runtime import read_payload  # noqa: E402

# --include=/--exclude= NOT immediately followed by a quote char, with a bare
# glob character (*, ?) somewhere before the next whitespace. Run on
# strip_literals(command) so a genuinely quoted occurrence (safe) or a bare
# mention inside an unrelated quoted string (e.g. grepping for this exact
# text) is not double-counted or misflagged — strip_literals removes quoted
# spans entirely, so only an ACTUALLY unquoted flag=value survives to match.
UNQUOTED_GLOB = re.compile(r"--(?:include|exclude)=(?!['\"])\S*[*?]")

# The one shape the rewrite handles: a whole word `--include=<value>` whose value
# holds only plain filename characters plus * and ?, and ends at whitespace, end
# of string, or a shell separator. No braces, brackets, $, backslash or quotes, so
# single-quoting the value cannot change what the shell passes to the command.
SIMPLE = re.compile(
    r"(?<![^\s;|&(])(--(?:include|exclude)=)([A-Za-z0-9_.,+/*?-]*[*?][A-Za-z0-9_.,+/*?-]*)(?=$|[\s;|&)])"
)


def rewrite(command: str) -> str | None:
    """The command with every unquoted glob value single-quoted, or None when the
    rewrite cannot be shown to change quoting only (the caller then BLOCKS)."""
    if "<<" in command:
        return None                       # heredoc bodies: shlex cannot check them
    new = SIMPLE.sub(lambda m: f"{m.group(1)}'{m.group(2)}'", command)
    if UNQUOTED_GLOB.search(strip_literals(new)):
        return None                       # unchanged, or an occurrence outside the simple shape remains
    try:
        same_words = shlex.split(command) == shlex.split(new)
    except ValueError:
        return None                       # unbalanced quotes
    return new if same_words else None


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)

    command = p.command                   # "" when absent; detection below then finds nothing
    m = UNQUOTED_GLOB.search(strip_literals(command))
    if not m:
        sys.exit(0)

    new = rewrite(command)
    tool_input = p.data.get("tool_input")
    if new is not None and isinstance(tool_input, dict):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {**tool_input, "command": new},
            "additionalContext": (
                "check_unquoted_glob quoted the --include=/--exclude= glob value(s) in "
                "this Bash command before it ran, because zsh would have expanded them. "
                "Write them quoted next time, e.g. --include='*.md'."
            ),
        }}))
        sys.exit(0)

    print(
        f"BLOCKED: unquoted glob in {m.group(0)!r}.\n"
        "\n"
        "zsh expands the glob BEFORE the command runs. If no matching file "
        "exists in the current directory, the whole command aborts with "
        "`zsh: no matches found: ...` (exit 1) before it even starts.\n"
        "\n"
        "Quote the value:\n"
        "  --include='*.md'   (or --include=\"*.md\")\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
