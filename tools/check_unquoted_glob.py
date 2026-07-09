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

BLOCK tier (exit 2). Per feedback_warn_vs_block_hook_design.md: unambiguous
violation (this pattern can only ever be a live bug, never a legitimate
choice), single known correction (quote the value).

Hook input: JSON via stdin from Claude Code. {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (value is quoted, no --include=/--exclude= present, or parse
      failure -> fail-open)
  2 — unquoted glob detected, tool call BLOCKED

Origin: 2026-07-08 friction-log audit.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# --include=/--exclude= NOT immediately followed by a quote char, with a bare
# glob character (*, ?) somewhere before the next whitespace. Run on
# strip_literals(command) so a genuinely quoted occurrence (safe) or a bare
# mention inside an unrelated quoted string (e.g. grepping for this exact
# text) is not double-counted or misflagged — strip_literals removes quoted
# spans entirely, so only an ACTUALLY unquoted flag=value survives to match.
UNQUOTED_GLOB = re.compile(r"--(?:include|exclude)=(?!['\"])\S*[*?]")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    m = UNQUOTED_GLOB.search(strip_literals(command))
    if not m:
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
