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

from dataclasses import dataclass, field

_CONTROL = ("&&", "||", ";;", "|&", ";", "&", "|", "(", ")")
# Longest first, so `>>` is not read as `>` then `>`.
_REDIR = ("&>>", "<<<", "<<-", "&>", ">>", ">|", ">&", "<<", "<&", "<>", ">", "<")


@dataclass
class Token:
    kind: str          # "word" | "op" | "redir"
    text: str
    quoted: bool = False


@dataclass
class Redirection:
    op: str            # includes any descriptor number, e.g. "2>&"
    target: str        # the word after the operator ("" if the line ended)


@dataclass
class SimpleCommand:
    words: list[str] = field(default_factory=list)
    quoted: list[bool] = field(default_factory=list)
    redirections: list[Redirection] = field(default_factory=list)


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
        if ch in "'\"":
            j = s.find(ch, i + 1)
            i = n if j == -1 else j + 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    word: list[str] = []
    in_word = False            # a word has started (even if empty, e.g. '')
    quoted = False
    i = 0
    n = len(s)

    def end_word():
        nonlocal word, in_word, quoted
        if in_word:
            toks.append(Token("word", "".join(word), quoted))
        word, in_word, quoted = [], False, False

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
                word.append(s[i])
                i += 1
            i += 1                                   # closing quote (or past the end)
            in_word = quoted = True
            continue

        if ch == "$" and i + 1 < n and s[i + 1] in "({":
            j = _read_balanced(s, i + 1, s[i + 1], ")" if s[i + 1] == "(" else "}")
            word.append(s[i:j])
            in_word = True
            i = j
            continue

        if ch == "`":
            j = s.find("`", i + 1)
            j = n if j == -1 else j + 1
            word.append(s[i:j])
            in_word = True
            i = j
            continue

        if ch in "<>" or (ch == "&" and s.startswith("&>", i)):
            # A word made only of unquoted digits, touching the operator, is its fd.
            fd = ""
            if in_word and not quoted and word and "".join(word).isdigit():
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
                k += 1
            cur.redirections.append(Redirection(t.text, target))
            k += 1
            continue
        cur.words.append(t.text)
        cur.quoted.append(t.quoted)
        k += 1
    if cur.words or cur.redirections:
        cmds.append(cur)
    return cmds
