#!/usr/bin/env python3
"""
check_screenshot_path.py — PreToolUse hook for Bash.

Fires when a Bash command references a `~/Documents/screenshots/Screenshot
<date> at <time> AM/PM.png` filename with a REGULAR ASCII space before AM/PM.
macOS screenshot filenames embed a NARROW NO-BREAK SPACE (U+202F) there, not a
regular space — a hand-typed or hand-reconstructed path with a normal space
does not exist on disk and raises FileNotFoundError on open/getmtime/cp/mv.

This is the structural promotion of the `/ss` skill's own repeated warnings
(SKILL.md Step 1/2.10/4: "Never hand-reconstruct a screenshot path... Always
pass the raw bytes from ls/glob through"). Those warnings did not stop
recurrence: `ss_log_append.py` FileNotFoundError sits at 18 friction-log
occurrences across 2026-06-15..2026-07-08, including after the library's own
`resolve_screenshot_path()` glob-recovery fix (2026-06-18) — meaning the
failures are from hand-typed paths in raw Bash (`cp`/`mv`/inline python
`open()`/`getmtime()`) that never reach that library function at all.

Detection is a plain substring scan (no command-position anchoring): unlike a
`python`-style token that has many legitimate contexts, a malformed screenshot
filename has none — nobody legitimately quotes a mistyped path. Deliberately
does NOT strip quoted literals first (the standard hook_command_lint pattern),
because the very thing being detected — a hand-typed path — is always inside
quotes in a real shell command (it contains spaces), so stripping quotes would
strip the bug out of view.

BLOCK tier (exit 2). Per feedback_warn_vs_block_hook_design.md: unambiguous
violation, single known correction (re-glob and use the exact returned bytes).

Hook input: JSON via stdin from Claude Code. {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (no malformed screenshot filename, or parse failure -> fail-open)
  2 — hand-typed screenshot path detected, tool call BLOCKED

Origin: 2026-07-08 friction-log audit. See memory/MEMORY.md
[[feedback_glob_resolve_screenshot_path_never_hand_type]].
"""
import json
import re
import sys

# "Screenshot 2026-06-15 at 1.28.08 PM.png" — matches only when the character
# immediately before AM/PM is a regular space (U+0020). The real filename uses
# U+202F there, so this pattern can only match a hand-typed/reconstructed path.
BAD_SCREENSHOT_PATH = re.compile(
    r"Screenshot \d{4}-\d{2}-\d{2} at \d{1,2}\.\d{2}\.\d{2} (?:AM|PM)\.png"
)

# Documentation/logging context, not a real file operation: citing the exact
# bad filename as an example (e.g. `friction_log.py append/resolve ... "...bad
# filename..."`, or a `git commit` message documenting this exact bug) is not
# the bug this hook exists to catch. Without this exclusion the hook blocks
# itself forever, since the ledger permanently stores this row's nature text
# verbatim. Found live 2026-07-08: blocked its own `friction_log.py resolve`
# call quoting this exact row. Broadened same-session (code review) after a
# second concrete repro: a `git commit -m "..."` message documenting the bug
# was still blocked — `git` never takes this filename as a real file operand
# (commit messages, log messages, branch ops), so it's safe to exempt
# wholesale rather than growing a per-caller allowlist one script at a time.
#
# Checked per SEGMENT, not over the whole command: `cp "bad path" dest &&
# python3 tools/friction_log.py append ...` must still block the cp even
# though the command AS A WHOLE also mentions friction_log.py — exempting the
# whole string would let a real file operation hide behind an unrelated
# doc-context call later in the same compound command.
DOC_CONTEXT_RE = re.compile(r"\bfriction_log\.py\b")
GIT_COMMAND_RE = re.compile(r"^\s*(?:\w+=\S+\s+)*git\b")
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||[|;&]")


def is_doc_context(segment: str) -> bool:
    return bool(DOC_CONTEXT_RE.search(segment) or GIT_COMMAND_RE.search(segment.strip()))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    m = None
    for segment in _SHELL_SPLIT_RE.split(command):
        if is_doc_context(segment):
            continue
        m = BAD_SCREENSHOT_PATH.search(segment)
        if m:
            break
    if not m:
        sys.exit(0)

    print(
        f"BLOCKED: hand-typed screenshot path detected ({m.group(0)!r}).\n"
        "\n"
        "macOS screenshot filenames use a NARROW NO-BREAK SPACE (U+202F) "
        "before AM/PM, not a regular space. A hand-typed or hand-reconstructed "
        "path with a normal space does not exist on disk.\n"
        "\n"
        "Re-glob and use the exact returned bytes instead:\n"
        "  ls -t ~/Documents/screenshots/*.png | head -1\n"
        "(or `glob.glob(...)` in a script) — pass that string through verbatim, "
        "never type it by hand.\n"
        "Reference: memory rule [[feedback_glob_resolve_screenshot_path_never_hand_type]].\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
