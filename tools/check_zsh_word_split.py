#!/usr/bin/env python3
"""
check_zsh_word_split.py — PreToolUse hook for Bash.

The Bash tool runs commands in zsh, and zsh does NOT word-split an unquoted `$VAR`. bash
does. So a command written the bash way passes a whole list as ONE argument, or a loop runs
once over the whole list, and the result is wrong without any error: a check that examined
nothing reports clean, a review is sent with no diff. Fired 11 times between 2026-08-12 and
2026-10-23 (memory: reference_zsh_no_word_split_false_pass_loops). The worst: six paths in
`$P` reached tools/codex_verify.py as one argument, so two cross-model review rounds ran
with no diff while the ledger recorded the files as reviewed.

Blocks three shapes, each one that fired, each read from the shell tokenizer
(tools/shell_tokens.py) rather than a regex over the raw text:

  1. LOOP OVER A SCALAR. `X=$(cmd)` or `X="a b"`, then `for v in $X` in the same command.
     zsh iterates once, over the whole value. A one-word literal (`X=abc`) loops correctly
     and is not flagged.
  2. A SPACED SCALAR AS ARGUMENTS. `P="a b c"`, then `$P` unquoted as an argument in the
     same command. zsh passes "a b c" as one argument.
  3. A SPACED LOOP ITEM AS ARGUMENTS. `for c in "x.py --flag" ...; do python3 tools/$c`.
     Each item reaches the command as one word, so python looks for a file named
     "x.py --flag".

Not blocked: `=word` (zsh EQUALS expansion). It was suspected in a 2026-09-30 fire, but in
this shell `echo =====` and `echo ===word` both print normally (measured 2026-10-23), and the
replay's 144 such commands showed no failure caused by it.

Scope, on purpose. Only variables ASSIGNED IN THE SAME COMMAND are judged: a variable set
in an earlier Bash call does not survive to this one, and one set by the environment has
an unknown value. An array (`X=(a b)`) splits correctly in zsh and is never flagged; nor is
`${=X}`, `"${X[@]}"`, or anything inside quotes. Not judged: arguments to echo, print and
eval (the first two print the same text either way; eval re-splits), and operands of `[[`,
`[`, `test` and `case`, where one operand is what the command wants. printf IS judged:
`printf '%s\n' $P` prints one line in zsh and one per item in bash. Wrappers (`command`,
`builtin`, `noglob`, `nocorrect`, `exec`, `nohup`, `time`) and their options (`command -p`,
`exec -a NAME`, `nohup --`) are read through to the real command.
Assignments through export, local, declare, typeset, readonly and integer are recorded
(`-a`/`-A` makes an array), and `Q=$P` copies P's kind. A scalar is flagged only when
`$P` is the whole word: `--name=$X` wants one argument and gets one. A loop item is
flagged inside any word (`tools/$c`). Once `setopt shwordsplit` or `emulate sh|bash|ksh`
runs, what follows it is not judged; what came before it still is.
Shape 1 also covers a scalar from `$(...)`; shapes 2-3 need a literal value with a space,
because `D=$(date +%F); tool --date $D` is ordinary and correct.

BLOCK tier (exit 2): each shape is wrong in zsh every time, and each has one correction,
named in the message. Fails open (exit 0) on an unreadable payload or a tokenizer error.

Threat model: honest mistakes by the agent writing bash idioms out of habit. It is not a
barrier against deliberate evasion; anything assigned in an earlier call, or built through
eval, passes.

Hook input: JSON via stdin, {"tool_input": {"command": "..."}}.
Exit codes: 0 clean or unreadable; 2 blocked.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from shell_tokens import tokenize  # noqa: E402

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_SPLIT_WORD = re.compile(r"^(?:sh_?word_?split|sh|bash|ksh)$", re.IGNORECASE)
_NO_JUDGE = {"echo", "print", "eval", "[[", "[", "test", "case"}
_WRAPPERS = {"time", "command", "builtin", "noglob", "nocorrect", "exec", "nohup"}
_CMD_START_WORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"} | _WRAPPERS
_ASSIGN_BUILTINS = {"export", "local", "declare", "typeset", "readonly", "integer"}
_COPY = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _ref(name: str) -> re.Pattern:
    # $NAME or ${NAME} not followed by more name characters; ${=NAME}, ${NAME[@]} and
    # ${#NAME} do not match.
    return re.compile(r"\$(?:\{" + re.escape(name) + r"\}|" + re.escape(name) + r"(?![A-Za-z0-9_]))")


def _classify(raw: str, value: str, scalars: dict) -> str:
    copied = _COPY.match(value)
    if copied:
        return scalars.get(copied.group(1), "plain")      # Q=$P is whatever P is
    if "$(" in raw or "`" in raw:
        return "subst"
    return "spaced" if any(c.isspace() for c in value) else "plain"


def find_violations(command: str) -> list[str]:
    try:
        tokens = tokenize(command)
    except Exception:  # noqa: BLE001 — fail open on anything the tokenizer cannot read
        return []
    src = command
    # A redirect's target (`> $P`, `<<< $P`) is one word in zsh, which is what a redirect
    # wants, so it is dropped here rather than judged as an argument (604.F1/610.F1).
    words, after_redir = [], False
    for t in tokens:
        if t.kind == "redir":
            after_redir = True
        elif t.kind == "word" and after_redir:
            after_redir = False
        elif t.kind in ("word", "op"):
            words.append(t)

    scalars: dict[str, str] = {}   # name -> "spaced" | "subst" | "plain"
    loop_spaced: set[str] = set()
    found: list[str] = []
    at_start = True          # is the next word in command position?
    cmd_word = None          # the command name of the current simple command
    wrapper = None           # the wrapper just read, whose options come before the command
    i = 0
    while i < len(words):
        t = words[i]
        if t.kind == "op":
            at_start, cmd_word, wrapper = True, None, None
            i += 1
            continue
        raw = src[t.start:t.end]

        if at_start:
            m = _ASSIGN.match(t.text) if not raw.startswith(("'", '"')) else None
            if m:
                # An array assignment (`X=(a b)`) has an empty value here, so it lands
                # in "plain" and is never judged: zsh splits arrays correctly.
                # `NAME=v cmd $NAME` is a prefix assignment: it sets NAME in cmd's
                # environment, not for the expansion of cmd's own arguments (591.F2).
                # A chain (`P="a b" X=1 cmd $P`) is prefix too: look past every
                # assignment to the first word that is not one (625.F4).
                j = i + 1
                while j < len(words) and words[j].kind == "word" and _ASSIGN.match(words[j].text):
                    j += 1
                prefix = j < len(words) and words[j].kind == "word"
                if not prefix:
                    scalars[m.group(1)] = _classify(raw, t.text[m.end():], scalars)
                i += 1
                continue     # still in command position after an assignment
            if raw in ("setopt", "emulate"):
                j = i + 1
                while j < len(words) and words[j].kind == "word":
                    if _SPLIT_WORD.match(words[j].text):
                        return found          # splitting is on from here: nothing after counts
                    j += 1
            if raw in _ASSIGN_BUILTINS:
                i = _read_assign_builtin(words, i + 1, src, scalars)
                at_start, cmd_word = True, None
                continue
            if wrapper and raw.startswith("-"):
                # A wrapper's own options are not the command: `command -p`, `time -p`,
                # `nohup --`, and `exec -a NAME`, whose NAME is skipped too (612.F1/624.F1).
                i += 2 if (wrapper == "exec" and raw == "-a") else 1
                continue
            if raw in _CMD_START_WORDS:
                wrapper = raw if raw in _WRAPPERS else None
                i += 1
                continue
            if raw == "for":
                i = _read_for(words, i, src, scalars, loop_spaced, found)
                at_start, cmd_word = True, None
                continue
            cmd_word = os.path.basename(t.text)
            at_start, wrapper = False, None
            i += 1
            continue

        # --- shapes 2 and 3: a spaced scalar (the whole word) or a spaced loop item
        # (anywhere in the word) used unquoted as an argument
        if cmd_word not in _NO_JUDGE and not t.quoted:
            for name in list(scalars) + list(loop_spaced):
                if name in loop_spaced:
                    hit = _ref(name).search(raw)
                else:
                    hit = scalars.get(name) == "spaced" and re.fullmatch(_ref(name).pattern, raw)
                if hit:
                    found.append(
                        f"`{raw}` passes ${name} unquoted, and ${name} holds a value with "
                        f"spaces. zsh does not split it, so `{cmd_word}` gets ONE argument. "
                        f"Use an array ({name}=(a b); \"${{{name}[@]}}\"), ${{={name}}}, or "
                        f"write the arguments out.")
        i += 1

    return found


def _read_assign_builtin(words, j, src, scalars) -> int:
    """Read the words after export/local/declare/... up to the next operator, recording
    each NAME=value. Flags (-a, -r) never match NAME=, and an array assignment
    (`declare -a X=(a b)`) has an empty value here, so it lands in "plain" either way."""
    while j < len(words) and words[j].kind == "word":
        w = words[j]
        m = _ASSIGN.match(w.text)
        if m:
            scalars[m.group(1)] = _classify(src[w.start:w.end], w.text[m.end():], scalars)
        j += 1
    return j


def _read_for(words, i, src, scalars, loop_spaced, found) -> int:
    """At a `for` word. Records the loop variable as spaced when any list item is a quoted
    string with whitespace, flags `for v in $SCALAR`, and returns the index after the list."""
    if i + 2 >= len(words) or words[i + 1].kind != "word":
        return i + 1
    var = words[i + 1].text
    j = i + 2            # the `in` keyword, when present, is an item that matches nothing
    items = []
    while j < len(words) and words[j].kind == "word" and words[j].text != "do":
        items.append(words[j])
        j += 1
    for it in items:
        raw = src[it.start:it.end]
        if it.quoted and any(c.isspace() for c in it.text):
            loop_spaced.add(var)
        for name, kind in scalars.items():   # a quoted "$X" fails the fullmatch on raw
            if kind in ("spaced", "subst") and re.fullmatch(_ref(name).pattern, raw):
                found.append(
                    f"`for {var} in {raw}` loops over the scalar ${name}. zsh runs the "
                    f"loop ONCE, over the whole value. Use an array "
                    f"({name}=(${{(f)\"$(cmd)\"}}) for lines), or `for {var} in ${{={name}}}`.")
    return j


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)          # fail open: an unreadable payload is not evidence of a bug
    found = find_violations(p.command or "")
    if not found:
        sys.exit(0)
    print("BLOCKED: zsh word-split mistake (zsh does not split an unquoted $VAR).\n\n"
          + "\n".join(f"- {f}" for f in found)
          + "\n\nThe Bash tool runs zsh. A bash habit here passes a list as one argument "
            "or loops once, with no error.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
