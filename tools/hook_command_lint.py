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
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_tokens import tokenize  # noqa: E402


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


def _closed(s: str, a: int, b: int) -> bool:
    """True if the quoted span s[a:b] ends with the quote that opened it."""
    if s.startswith("$'", a):
        return b - a >= 3 and s[b - 1] == "'"
    return b - a >= 2 and s[b - 1] == s[a]


def strip_literals(command: str) -> str:
    """Blank literal spans (quoted strings + heredoc bodies) so a trigger token
    inside them is not mistaken for a real command invocation.

    Read from shell_tokens' per-segment spans (Q11, Nick 2026-11-16: "Rebuild on
    shell_tokens"), so each quote pairs with the quote that really closes it. The two
    regexes this replaced paired `"$f"`'s closing quote with the NEXT span's opening
    quote, blanking real code and exposing quoted text.

    Policy, unchanged from the regex version:
      - a single-quoted or $'...' span is blanked;
      - a double-quoted span with no `$` or backtick is blanked; one with either is
        kept, with each `$( )` / backtick body inside it stripped recursively;
      - an unquoted `$( )` / backtick body is stripped recursively;
      - a heredoc body is blanked when its delimiter is quoted or the body holds no
        real substitution (tokenize's subs, so an escaped backtick is not one);
        otherwise kept as written.
    """
    edits: list[tuple[int, int, str]] = []
    for tok in tokenize(command):
        if tok.kind == "heredoc":
            if tok.quoted or not tok.subs:
                edits.append((tok.start, tok.end, " "))
        # A heredoc token has no spans. tokenize records a "sub" only where it runs
        # (unquoted or inside a "dqx" span), never inside a literal one.
        for kind, a, b in tok.spans:
            # Only a span that CLOSES is a literal. tokenize reads an unterminated quote
            # to the end of the input; blanking that would hide the rest of the command
            # from a blocking hook, so it stays visible (the regex version left it too).
            if kind in ("sq", "dq") and _closed(command, a, b):
                edits.append((a, b, " "))
            elif kind == "sub":
                edits.append((a, b, strip_literals(command[a:b])))
    out = command
    for a, b, text in sorted(edits, reverse=True):
        out = out[:a] + text + out[b:]
    return out


# --- python interpreter calls, read from shell_tokens words -------------------------
#
# Moved here 2026-10-14 from check_todo_write_kwargs.todo_write_args, when a second hook
# (check_owner_only_actions.py, deleted 2026-10-15) needed the same answer: which script, or which -c code,
# does this `python ...` word list run? Mechanism, not policy.

# The interpreter word, by basename: python, python3, python3.12, /usr/bin/python3.
PYTHON = re.compile(r"python(\d+(\.\d+)*)?")



@dataclass
class PythonCall:
    script: str | None    # the script file run, or None for -c / -m / stdin
    args: list[str]       # words after the script (or after the -c code / -m module)
    code: str | None      # the -c program text, if any
    module: str | None    # the -m module name, if any


def python_calls(words: list[str]):
    """Every python interpreter invocation in ONE simple command's words, in order.

    The script is the first word after the interpreter that is not an interpreter
    option. `-c CODE` and `-m MODULE` run no script file. A bare `-` (or nothing)
    reads the program from stdin, which a heredoc supplies; that call has no script
    and no code here, and the caller reads the heredoc body off the command.
    """
    for i, word in enumerate(words):
        if not PYTHON.fullmatch(os.path.basename(word)):
            continue
        j = i + 1
        call = None
        while j < len(words) and words[j].startswith("-") and words[j] != "-":
            w = words[j]
            if w.startswith("--"):
                # The one long option that takes its value as the next word.
                j += 2 if w == "--check-hash-based-pycs" else 1
                continue
            # A short-option CLUSTER, read the way CPython reads it: `-uc CODE` is -u
            # then -c, and an option taking a value (-c, -m, -X, -W) takes the rest of
            # the cluster or else the next word (Grok d71cd64 F1: `-uc` hid the code).
            step = 1
            for k in range(1, len(w)):
                ch = w[k]
                if ch in "cmXW":
                    inline = w[k + 1:]
                    val = inline or (words[j + 1] if j + 1 < len(words) else "")
                    step = 1 if inline else 2
                    if ch in "cm":
                        rest = words[j + step:]
                        call = (PythonCall(None, rest, val, None) if ch == "c"
                                else PythonCall(None, rest, None, val))
                    break
            if call is not None:
                break
            j += step
        if call is None:
            if j < len(words) and words[j] != "-":
                call = PythonCall(words[j], words[j + 1:], None, None)
            else:
                call = PythonCall(None, words[j + 1:], None, None)
        yield call


def python_script_args(words: list[str], script_basename: str):
    """The argument words of the first `python ... <script_basename> ...` call in ONE
    simple command, or None."""
    for call in python_calls(words):
        if call.script and os.path.basename(call.script) == script_basename:
            return call.args
    return None
