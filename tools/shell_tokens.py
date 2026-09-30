"""shell_tokens.py -- split a shell command line into words, operators and redirections.

MECHANISM, not policy: this module only says how the shell would read a command. What
counts as a write, or as a leak, is decided by the caller (check_public_pii.py).

Why it exists. The PII hook found a command's arguments with regexes that decided where
an argument list ended, and a Codex review found a new ordinary shell form each time one
was patched: a quoted expression with spaces, `|` inside quotes, `2>&1` before a file,
a quoted program path (2026-09-28, four rounds). Every one of those is a tokenizing
question, so the fix is one tokenizer, not a fifth regex.

Covered: single and double quotes (with the backslash escapes double quotes allow),
backslash escapes and line continuations, adjacent quoted and bare spans joining into one
word, `$(...)`, backticks and `${...}` kept inside their word, `#` comments, control
operators (`;` `&` `&&` `|` `||` `|&` `(` `)` newline, `;;`), and redirections with an
optional descriptor number that touches the operator (`2>&1`, `3<&0`, `&>`, `>>`, `>|`,
`<>`, `<<`, `<<-`, `<<<`, `>&`, `<&`).

Not covered: heredoc BODIES (mask them first; check_public_pii.mask_heredoc_bodies does),
`{fd}>` named descriptors, and alias or function expansion. An unterminated quote takes
the rest of the line as the word; the shell would refuse to run it at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Inside backticks, an escaped backtick, backslash or dollar is unescaped before the
# command runs, so an escaped backtick there opens a NESTED command substitution.
_BACKTICK_UNESCAPE = re.compile(r"\\([\\`$])")

_CONTROL = ("&&", "||", ";;", "|&", ";", "&", "|", "(", ")")
# Longest first, so `>>` is not read as `>` then `>`.
_REDIR = ("&>>", "<<<", "<<-", "&>", ">>", ">|", ">&", "<<", "<&", "<>", ">", "<")


@dataclass
class Token:
    kind: str          # "word" | "op" | "redir"
    text: str
    quoted: bool = False
    # Command strings inside `$(...)` or backticks that RUN when this word is
    # expanded (outermost only; a caller recurses for nested ones). Recorded while
    # tokenizing, because only then is an escaped or single-quoted `$(` or backtick
    # distinguishable from a live one.
    subs: list[str] = field(default_factory=list)


@dataclass
class Redirection:
    op: str            # includes any descriptor number, e.g. "2>&"
    target: str        # the word after the operator ("" if the line ended)


@dataclass
class SimpleCommand:
    words: list[str] = field(default_factory=list)
    quoted: list[bool] = field(default_factory=list)
    redirections: list[Redirection] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)    # from words AND redirect targets


def _read_balanced(s: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the `close_ch` matching the `open_ch` at s[i]. Quotes inside
    are skipped. Runs to the end if unbalanced."""
    depth = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            j = s.find("'", i + 1)
            i = n if j == -1 else j + 1
            continue
        if ch == '"':                  # escapes apply inside double quotes: a
            i += 1                     # backslash-quote is not the end (Codex, 6514b88)
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _closing_backtick(s: str, i: int) -> int:
    """Index of the backtick closing the one at s[i] (escapes skipped), or len(s)."""
    k = i + 1
    while k < len(s) and s[k] != "`":
        k += 2 if s[k] == "\\" else 1
    return min(k, len(s))


def _paren_sub(s: str, i: int) -> tuple[str, int]:
    """(inner command, index past it) for the `$(` at s[i]."""
    j = _read_balanced(s, i + 1, "(", ")")
    inner_end = j - 1 if s[j - 1:j] == ")" else j
    return s[i + 2:inner_end], j


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    word: list[str] = []
    subs: list[str] = []
    in_word = False            # a word has started (even if empty, e.g. '')
    quoted = False
    i = 0
    n = len(s)

    def end_word():
        nonlocal word, in_word, quoted, subs
        if in_word:
            toks.append(Token("word", "".join(word), quoted, subs))
        word, in_word, quoted, subs = [], False, False, []

    while i < n:
        ch = s[i]

        if ch in " \t":
            end_word()
            i += 1
            continue

        if ch == "\n":
            end_word()
            toks.append(Token("op", "\n"))
            i += 1
            continue

        if ch == "#" and not in_word:
            j = s.find("\n", i)
            i = n if j == -1 else j
            continue

        if ch == "\\":
            if i + 1 < n and s[i + 1] == "\n":       # line continuation
                i += 2
                continue
            if i + 1 < n:
                word.append(s[i + 1])
            in_word = True
            i += 2
            continue

        if ch == "'":
            j = s.find("'", i + 1)
            j = n if j == -1 else j
            word.append(s[i + 1:j])
            in_word = quoted = True
            i = j + 1
            continue

        if ch == '"':
            i += 1
            while i < n and s[i] != '"':
                if s[i] == "\\" and i + 1 < n and s[i + 1] in '\\"$`\n':
                    if s[i + 1] != "\n":
                        word.append(s[i + 1])
                    i += 2
                    continue
                if s.startswith("$(", i):              # runs inside double quotes
                    inner, j = _paren_sub(s, i)
                    subs.append(inner)
                    word.append(s[i:j])
                    i = j
                    continue
                if s[i] == "`":
                    k = _closing_backtick(s, i)
                    subs.append(_BACKTICK_UNESCAPE.sub(r"\1", s[i + 1:k]))
                    word.append(s[i:k + 1])
                    i = k + 1
                    continue
                word.append(s[i])
                i += 1
            i += 1                                   # closing quote (or past the end)
            in_word = quoted = True
            continue

        if s.startswith("$(", i):
            inner, j = _paren_sub(s, i)
            subs.append(inner)
            word.append(s[i:j])
            in_word = True
            i = j
            continue

        if s.startswith("${", i):                      # parameter expansion, not a command
            j = _read_balanced(s, i + 1, "{", "}")
            word.append(s[i:j])
            in_word = True
            i = j
            continue

        if ch == "`":
            k = _closing_backtick(s, i)
            subs.append(_BACKTICK_UNESCAPE.sub(r"\1", s[i + 1:k]))
            word.append(s[i:k + 1])
            in_word = True
            i = k + 1
            continue

        if ch in "<>" or (ch == "&" and s.startswith("&>", i)):
            # A word made only of unquoted digits, touching the operator, is its fd.
            fd = ""
            # `&>` takes no descriptor number: in `tee 2&>f` the 2 is an argument.
            if ch != "&" and in_word and not quoted and word and "".join(word).isdigit():
                fd = "".join(word)
                word, in_word = [], False
            end_word()
            op = next(o for o in _REDIR if s.startswith(o, i))
            toks.append(Token("redir", fd + op))
            i += len(op)
            continue

        if ch in ";&|()":
            end_word()
            op = next(o for o in _CONTROL if s.startswith(o, i))
            toks.append(Token("op", op))
            i += len(op)
            continue

        word.append(ch)
        in_word = True
        i += 1

    end_word()
    return toks


def simple_commands(s: str) -> list[SimpleCommand]:
    """The command line as simple commands: words, with redirections pulled out.

    Each redirection operator takes the next word as its target, so that word is never
    mistaken for an argument (the defect behind `2>&1 docs/f.md` being missed).
    """
    cmds: list[SimpleCommand] = []
    cur = SimpleCommand()
    toks = tokenize(s)
    k = 0
    while k < len(toks):
        t = toks[k]
        if t.kind == "op":
            if cur.words or cur.redirections:
                cmds.append(cur)
            cur = SimpleCommand()
            k += 1
            continue
        if t.kind == "redir":
            target = ""
            if k + 1 < len(toks) and toks[k + 1].kind == "word":
                target = toks[k + 1].text
                cur.subs.extend(toks[k + 1].subs)
                k += 1
            cur.redirections.append(Redirection(t.text, target))
            k += 1
            continue
        cur.words.append(t.text)
        cur.quoted.append(t.quoted)
        cur.subs.extend(t.subs)
        k += 1
    if cur.words or cur.redirections:
        cmds.append(cur)
    return cmds

