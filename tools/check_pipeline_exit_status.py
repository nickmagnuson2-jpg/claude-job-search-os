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

ALLOWLIST — the correct way to keep the pipe
--------------------------------------------
The hook exits 0 when the author handles pipeline status: `set -o pipefail` is in
effect for the statement (set in command position, not turned off again by
`set +o pipefail`). A mere mention, in a comment, a quoted string, or
`echo set -o pipefail`, does not exempt (Codex review of the hook wiring, run 15, F2).
Reading zsh's `$pipestatus[1]` needs no exemption: a status check in its own statement
does not read `$?`, so it never matched. The whole-command `$pipestatus` exemption was
removed (Q13, Nick 2026-11-16): it changed no verdict across 60 real commands and it
blessed `cmd | tail || echo $pipestatus[1]`, whose `||` can never fire (Grok q13-r2 F1).
Bash's uppercase `$PIPESTATUS` is its own violation, see find_violation.

BLOCK tier (exit 2) per feedback_warn_vs_block_hook_design.md: an exit-0 WARN on
PreToolUse is never surfaced by Claude Code, and this pattern has a single
deterministic correction (unpipe the command under test, or read the pipeline
status array: `$pipestatus[1]` in zsh, the shell Claude Code runs here; bash's
`${PIPESTATUS[0]}` is empty in zsh, 2026-11-16).

Hook input: JSON via stdin from Claude Code. {"tool_input": {"command": "..."}}

Exit codes:
  0 — clean (no formatter-terminated pipeline feeding a verdict, pipefail in
      effect, or parse failure -> fail-open)
  2 — verdict taken from a pipeline's exit status, tool call BLOCKED

Origin: memory/feedback_pipeline_masks_the_exit_status_you_are_testing.md
(2026-08-13 fire 1, 2026-08-14 fire 2 + "rule sharpened" supplement).
"""
import os
import re
import sys

# Shared literal-context stripping (quoted spans + heredoc bodies). Single
# source of truth — never re-implement per hook (that drift caused the
# command-position family's 4th/5th fires). Installed layout puts this file in
# tools/ next to hook_command_lint.py; PYTHONPATH covers the staging layout.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402
from shell_tokens import tokenize  # noqa: E402
from hook_runtime import read_payload  # noqa: E402

# Final-stage commands whose exit status is a constant 0 in a pipeline — it can
# never carry the predicate under test. Informative-status commands (grep, awk,
# jq, test, diff, cmp, python3, ...) are intentionally absent.
FORMATTERS = frozenset(
    """
    sed tail head tr cut cat tee column fold nl rev fmt pr wc sort uniq
    expand unexpand paste tac less more strings hexdump xxd od csplit
    """.split()
)

# The author is handling pipeline status only if pipefail is IN EFFECT for the
# pipeline (`set -o pipefail`, `set -euo pipefail`, and not turned off again by
# `set +o pipefail`). A bare mention in a
# comment, a quoted string, or `set +o pipefail` exempted the whole command (Codex
# review of the hook wiring, run 15, F2).
# `set` in command position (statement start, after && / || / a group opener, or
# after then / do / else: Codex and Grok reviews of 7f43845),
# with any options before it, including `-o NAME` pairs (`set -o errexit -o pipefail`).
# `echo set -o pipefail` is an argument, not a set (Grok review of 663a887, F4).
_SET_OPTS = r"(?:^|&&|\|\||[({]|\b(?:then|do|else)\b)\s*set\s+(?:[-+]\w+\s+(?:(?!pipefail\b)[a-z]\w*\s+)?)*"
_PIPEFAIL_ON = re.compile(_SET_OPTS + r"-[a-zA-Z]*o\s+pipefail\b")
_PIPEFAIL_OFF = re.compile(_SET_OPTS + r"\+[a-zA-Z]*o\s+pipefail\b")
# Bash's spelling, read where zsh EXPANDS it (outside single quotes and quoted
# heredocs). It is always empty in zsh, so the read reports no status at all: its own
# block, not just a lost exemption. 111 real commands read it; 89 expanded it, e.g.
# `git push ... | tail; echo "push exit: ${PIPESTATUS[0]}"` printed an empty code
# (Q13 scope, Nick 2026-11-16: "Add the rule"). The `$` must be preceded by an EVEN
# run of backslashes (none, or `\\\\` = a literal backslash, then expansion); an odd run
# escapes the `$` and zsh prints it (q13-r1 F2, q13-r2 Codex F1).
_BASH_PIPESTATUS = re.compile(r"(?<!\\)(?:\\\\)*\$\{?PIPESTATUS\b")
_COMMENT = re.compile(r"(?:^|(?<=\s))#[^\n]*")

_VAR_PREFIX = re.compile(r"^\s*(?:\w+=\S*\s+)*")
_FIRST_WORD = re.compile(r"[\w./+-]+")
_LINE_CONT = re.compile(r"\\\n")


def _first_word(segment: str) -> str | None:
    """First actual command word of a pipeline stage, basename-normalised; None if none."""
    m = _FIRST_WORD.match(_VAR_PREFIX.sub("", segment))
    return os.path.basename(m.group(0)) if m else None


_STATEMENT_OPS = (";", "\n", "&", ";;")
_AND_OR_OPS = ("&&", "||")
_PIPE_OPS = ("|", "|&")
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_TESTS = ("[", "[[", "test")


def _split(toks, ops):
    """Split a token list at op tokens whose text is in `ops`: [(tokens, op_after)].
    The tokens come from shell_tokens.tokenize, so an operator inside quotes or a
    substitution is never one of them (Q11: the regex split broke on a `|` inside
    `"$(wc -l < f | tr -d ' ')"`)."""
    out, cur = [], []
    for t in toks:
        if t.kind == "op" and t.text in ops:
            out.append((cur, t.text))
            cur = []
        else:
            cur.append(t)
    out.append((cur, None))
    return out


def _words(toks):
    return [t for t in toks if t.kind == "word"]


def _element_formatter(toks, src: str) -> str | None:
    """The formatter whose status this and-or element reports, or None.

    A pipeline reports its LAST stage. An assignment with no command word
    (`x=$(a | head)`, quoted or not) reports its last substitution's status, so that
    body is read instead."""
    stages = _split(toks, _PIPE_OPS)
    if len(stages) < 2:
        words = _words(toks)
        if words and all(_ASSIGNMENT.match(w.text) for w in words):
            # Every substitution the assignment runs, including one inside ${...}
            # (spans; Token.subs skips those), in source order. Its status is the last.
            subs = [src[a:b] for w in words for kind, a, b in w.spans if kind == "sub"]
            if subs:
                return _command_formatter(subs[-1])
        return None
    last = stages[-1][0]
    if not last:
        return None
    # The stage's first word is read from its source text with the same rule as
    # before, so `"tail"` or `$FMT` is still not a formatter word; only the split
    # points changed.
    word = _first_word(src[last[0].start:last[-1].end])
    return word if word in FORMATTERS else None


def _command_formatter(command: str) -> str | None:
    """The formatter whose status a whole command reports: its last element."""
    statements = [st for st, _ in _split(tokenize(command), _STATEMENT_OPS) if _words(st)]
    if not statements:
        return None
    return _element_formatter(_split(statements[-1], _AND_OR_OPS)[-1][0], command)


def _terminal_formatter(element: str) -> str | None:
    """String form of _element_formatter, for one and-or element."""
    return _element_formatter(tokenize(element), element)


def _is_test(toks) -> bool:
    words = [w.text for w in _words(toks)]
    if words and words[0] == "!":
        words = words[1:]
    return bool(words) and words[0] in _TESTS


def find_violation(command: str) -> tuple[str, str] | None:
    """Return (formatter, shape) for the first masked-exit-status verdict, else
    None. `shape` is "||" (fire 1), "$?" (fire 2), or "PIPESTATUS" (an expanded read
    of bash's array, which is empty in zsh; formatter is then "PIPESTATUS")."""
    text = _LINE_CONT.sub(" ", command)
    text = _COMMENT.sub("", strip_literals(text))
    if _BASH_PIPESTATUS.search(text):
        return "PIPESTATUS", "PIPESTATUS"

    return _find_in(command)


def _find_in(command: str, pipefail: bool = False) -> tuple[str, str] | None:
    """The shape scan over shell_tokens structure. Each substitution body is then
    scanned as a command of its own, so `$(a | cut -f2 || echo 0)` is still caught."""
    toks = tokenize(command)
    statements = [(st, command[st[0].start:st[-1].end])
                  for st, _ in _split(toks, _STATEMENT_OPS) if _words(st)]
    # In effect for the statements that follow; a substitution body inherits the state
    # of the statement it sits in (Codex/Grok q11-r1 F1).
    for i, (stmt, src) in enumerate(statements):
        flat = _COMMENT.sub("", strip_literals(src)).strip()
        if _PIPEFAIL_ON.search(flat):
            pipefail = True
        if _PIPEFAIL_OFF.search(flat):
            pipefail = False
        for tok in stmt:
            # Every body that runs: $( ) and backticks, also inside "..." and ${...}
            # (spans), and a heredoc's substitutions (subs; a heredoc has no spans).
            bodies = ([command[a:b] for kind, a, b in tok.spans if kind == "sub"]
                      if tok.kind != "heredoc" else tok.subs)
            for body in bodies:
                hit = _find_in(body, pipefail)
                if hit:
                    return hit
        if pipefail:
            continue
        parts = _split(stmt, _AND_OR_OPS)
        for j, (part, op_after) in enumerate(parts):
            fmt = _element_formatter(part, command)
            if not fmt:
                continue
            # Shape A: an `||` verdict hangs off this pipeline. Not when a TEST
            # guards it (`[ -f f ] && wc -l < f | tr -d ' ' || echo MISSING`): there the
            # `||` is the test's else-branch and does fire (Q14, Nick 2026-11-16).
            if op_after == "||":
                guarded = j >= 1 and parts[j - 1][1] == "&&" and _is_test(parts[j - 1][0])
                if not guarded:
                    return fmt, "||"
            # Shape B: the next statement reads `$?`.
            if j == len(parts) - 1 and i + 1 < len(statements):
                nxt = _COMMENT.sub("", strip_literals(statements[i + 1][1]))
                if "$?" in nxt:
                    return fmt, "$?"
    return None


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)

    command = p.command
    if not command:
        sys.exit(0)

    hit = find_violation(command)
    if not hit:
        sys.exit(0)

    fmt, shape = hit
    if shape == "PIPESTATUS":
        print(
            "BLOCKED: `$PIPESTATUS` is bash's array. In zsh, the shell Claude Code runs "
            "here, it is unset and expands EMPTY, so this reads no exit status at all "
            "(`echo \"exit=${PIPESTATUS[0]}\"` prints `exit=`).\n"
            "\n"
            "zsh's array is lowercase and 1-indexed:\n"
            "  cmd \"$X\" | tail -5; echo \"exit=$pipestatus[1]\"\n"
            "  cmd \"$X\" | tail -5; [ \"$pipestatus[1]\" -eq 0 ] || echo FAILED\n"
            "\n"
            "Or keep the command under test out of the pipeline:\n"
            "  cmd \"$X\" > /tmp/o.txt 2>&1; echo \"exit=$?\"; tail -5 /tmp/o.txt\n",
            file=sys.stderr,
        )
        sys.exit(2)
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
        f"  cmd \"$X\" | {fmt} ...; [ \"$pipestatus[1]\" -eq 0 ] || echo FAILED   # zsh\n"
        "\n"
        "When the EXIT STATUS is the thing under test, the command must not be "
        "in a pipeline at all. Format after the verdict is computed, not in the "
        "same expression.\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
