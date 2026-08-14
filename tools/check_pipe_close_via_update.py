#!/usr/bin/env python3
"""
check_pipe_close_via_update.py — PreToolUse hook for Bash.

BLOCKs `pipe_write.py update <company> "<terminal stage>"`. Closing a pipeline row is
`pipe_write.py remove --stage {Withdrawn,Rejected,Accepted}`; `update` leaves the row in
`## Active Pipeline` carrying a freeform stage string.

WHY THIS IS A BLOCK, NOT A WARN
-------------------------------
The damage is silent and compounding, and every `update` call returns `{"status": "ok"}`:

  1. `todo_write.py sync` treats a company as CLOSED only via `stage_vocab.is_terminal_stage()`
     on a row it can find. A row left in Active Pipeline with a freeform close string is
     still scanned, but it also lands in sync's **"still live"** set — which then BLOCKS that
     company from ever syncing, even after someone later archives it correctly.
  2. The row keeps counting as an active pursuit in some views and not others, so two tools
     disagree about how big the pipeline is.
  3. Nothing downstream ever flags it. It is only visible if you go looking.

Measured on the live pipeline 2026-08-14: **30 companies** in exactly this state, the oldest
four months old. The root cause was not improvisation — `.claude/skills/pipe/SKILL.md` step 0
of `update` explicitly listed close stages as valid `update` targets. That source was fixed in
the same pass; this hook stops the pattern coming back from anywhere else.

Per memory/feedback_warn_vs_block_hook_design.md: BLOCK is right here because the violation is
unambiguous and has exactly one correct replacement. A PreToolUse WARN (exit 0 + stderr) is not
surfaced by Claude Code at all, so it would be invisible.

Terminal detection delegates to `stage_vocab.is_terminal_stage()` — the single source of truth
(per the CLAUDE.md hard rule and tests/scripts/test_stage_classification_consistency.py). This
hook must never grow its own keyword list.

WHY NOT `strip_literals` (the usual command-hook helper)
--------------------------------------------------------
Most command hooks match a bare TOKEN (`python`, `--task`) and use
`hook_command_lint.strip_literals` so the token inside a quoted string is not read as an
invocation. This hook is different: it must read a quoted ARGUMENT, and the stage is always
quoted in real usage. Running strip_literals first blanks exactly the thing being inspected,
which was verified against the real 2026-08-14 command — the first draft of this hook returned
None on it and would have shipped as a no-op. That is the same false-pass shape as
[[feedback_wrong_cli_interface_returns_a_false_pass]]: a guard that never fires looks identical
to a guard that found nothing.

So instead: strip only HEREDOC BODIES (literal prose that could contain anything), then
`shlex.split` the rest. shlex gives quote-correct tokens for free, and it also supplies the
command-position discipline the regex approach was buying — a quoted `grep "pipe_write.py
update Foo Closed"` collapses to ONE token, which can never match the tool name exactly.
"""
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hook_command_lint import _strip_heredoc_bodies  # noqa: E402
from stage_vocab import is_terminal_stage  # noqa: E402

_SEPARATORS = {"|", "||", "&&", ";", "&", "\n"}


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream on shell command separators."""
    out, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            out.append(cur)
            cur = []
        else:
            cur.append(tok)
    out.append(cur)
    return out


def _positionals_after_tool(seg: list[str]) -> list[str] | None:
    """Return positional args following pipe_write.py in this segment, or None."""
    for i, tok in enumerate(seg):
        if tok.endswith("pipe_write.py"):
            rest = seg[i + 1:]
            break
    else:
        return None

    out: list[str] = []
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("--"):
            if "=" not in tok:  # `--flag value` consumes the next token
                skip_next = True
            continue
        out.append(tok)
    return out


def find_violation(command: str) -> str | None:
    """Return the offending stage string, or None. Pure — unit-testable."""
    try:
        tokens = shlex.split(_strip_heredoc_bodies(command), posix=True)
    except ValueError:
        return None  # unbalanced quotes — fail open

    for seg in _segments(tokens):
        args = _positionals_after_tool(seg)
        if not args or args[0] != "update":
            continue
        # `update <company> <new-stage>`
        if len(args) < 3:
            continue
        if is_terminal_stage(args[2]):
            return args[2]
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail open

    command = (data.get("tool_input", {}) or {}).get("command", "")
    if not command:
        sys.exit(0)

    stage = find_violation(command)
    if not stage:
        sys.exit(0)

    print(
        f"BLOCKED: pipe_write.py update called with a TERMINAL stage ({stage!r}).\n"
        "\n"
        "`update` does not close a row. It leaves it in ## Active Pipeline with a freeform\n"
        "stage, where todo_write.py sync counts the company as STILL LIVE — which blocks it\n"
        "from ever syncing, even after a later correct archive. 30 rows were found in this\n"
        "state on 2026-08-14.\n"
        "\n"
        "Use remove instead, and ASK which terminal stage rather than deriving it:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py remove \"<company>\" \\\n"
        "      [--role \"<role>\"] --stage Withdrawn|Rejected|Accepted\n"
        "\n"
        "  Rejected  = THEY passed on Nick (declined, ghosted, stopped advancing him)\n"
        "  Withdrawn = NICK passed on them (self-pass, hard-filter fail, role evaporated)\n"
        "  Accepted  = offer taken\n"
        "\n"
        "Never infer which one from the row's Notes — that inverts a fact about someone\n"
        "else's decision. Present the classification and let Nick confirm.\n"
        "See .claude/skills/pipe/SKILL.md (remove) and tools/HOOK_AUTHORING.md.\n"
        "False positive? PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append "
        "check_pipe_close_via_update.py \"FP: <what got wrongly blocked>\"\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
