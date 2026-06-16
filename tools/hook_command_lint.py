#!/usr/bin/env python3
"""hook_command_lint.py — shared literal-context stripping for command-detection
PreToolUse hooks (check_bare_python.py, check_todo_write_kwargs.py).

Single source of truth so the strip logic cannot drift between hooks — the 4th/5th
fires of the command-position-not-substring family happened precisely because the
fix landed in one hook's copy of `_strip_literals` and not the other's
([[feedback_command_hook_match_position_not_substring]]).

`strip_literals(command)` blanks out spans where a trigger token (`python`,
`todo_write.py --flag`, ...) is literal text rather than a real invocation:

  - single-quoted spans  — never expand in shell → always stripped
  - double-quoted spans with no `$()`/backtick — literal → stripped (spans WITH
    substitution are kept so a real `"$(python x)"` is still scanned)
  - heredoc bodies:
      * quoted delimiter (`<<'EOF'` / `<<"EOF"`) — fully literal → body stripped
        (this is the 2026-06-05 / 2026-06-02 case: prose or `$(python)` inside a
        `git commit -F - <<'EOF'` message body wrongly tripped the hook)
      * unquoted delimiter (`<<EOF`) — body CAN expand, so it is stripped only
        when it contains no `$(`/backtick; a body with real substitution is kept
        so a genuine `$(python ...)` invocation is still caught.

Heredocs are stripped FIRST: a `<<'EOF'` operator contains quote chars that the
quoted-span passes would otherwise chew up, breaking heredoc detection.

Origin: 2026-06-05 — the parked "2nd heredoc fire" REOPEN gate tripped when
`git commit -F - <<'EOF' ... python crash ... EOF` was blocked by
check_bare_python.py. Pure functions, stdlib only.
"""
import re

_SINGLE_QUOTED = re.compile(r"'[^']*'")
_DOUBLE_QUOTED_LITERAL = re.compile(r'"[^"$`]*"')

# Heredoc: `<<` or `<<-`, optional ws, optional quote, TAG, (matching quote),
# rest-of-operator-line, body (non-greedy), then TAG alone on a line.
#   g1 = `<<`/`<<-` + leading ws   g2 = opening quote or ''   g3 = TAG
#   g4 = remainder of operator line incl newline   g5 = body   g6 = closing \n+TAG
_HEREDOC = re.compile(
    r"(<<-?[ \t]*)(['\"]?)([A-Za-z_]\w*)\2([^\n]*\n)(.*?)(\n[ \t]*\3\b)",
    re.DOTALL,
)


def _strip_heredoc_bodies(command: str) -> str:
    def repl(m: "re.Match") -> str:
        quoted = bool(m.group(2))
        body = m.group(5)
        if quoted or ("$(" not in body and "`" not in body):
            body = " "
        # reconstruct: op + quote + tag + quote + opline + (maybe blanked) body + close
        return m.group(1) + m.group(2) + m.group(3) + m.group(2) + m.group(4) + body + m.group(6)

    return _HEREDOC.sub(repl, command)


def strip_literals(command: str) -> str:
    """Blank literal spans (quoted strings + heredoc bodies) so a trigger token
    inside them is not mistaken for a real command invocation."""
    command = _strip_heredoc_bodies(command)
    command = _SINGLE_QUOTED.sub(" ", command)
    command = _DOUBLE_QUOTED_LITERAL.sub(" ", command)
    return command
