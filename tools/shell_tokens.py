"""shell_tokens.py -- split a shell command line into words, operators, redirections
and heredoc bodies, each with its span in the source.

MECHANISM, not policy: this module only says how the shell would read a command. What
counts as a write, or as a leak, is decided by the caller (check_public_pii.py).

Why it exists. The PII hook found a command's arguments with regexes that decided where
an argument list ended, and a Codex review found a new ordinary shell form each time one
was patched: a quoted expression with spaces, `|` inside quotes, `2>&1` before a file,
a quoted program path (2026-09-28, four rounds). Every one of those is a tokenizing
question, so the fix is one tokenizer, not a fifth regex. Heredocs and source spans were
added so the hook's segmenter and heredoc masker could stop being separate hand-written
scanners that disagreed with this one (Codex review of 6514b88, 2026-09-30).

Covered: single and double quotes (with the backslash escapes double quotes allow),
bash ANSI-C `$'...'` and locale `$"..."` quoting,
backslash escapes and line continuations, adjacent quoted and bare spans joining into one
word, `$(...)`, backticks and `${...}` kept inside their word (the commands inside are
recorded in Token.subs), `#` comments, control operators (`;` `&` `&&` `|` `||` `|&`
`(` `)` newline `;;`), redirections with an optional descriptor number that touches the
operator (`2>&1`, `3<&0`, `&>`, `>>`, `>|`, `<>`, `<<<`, `>&`, `<&`), and heredocs
(`<<` and `<<-`, any delimiter word, quoted or escaped delimiters turning expansion off,
several on one line, bodies emitted as "heredoc" tokens just before the newline that
ends them).

Not covered: `{fd}>` named descriptors, alias or function expansion, and heredocs opened
inside a `$(...)`. An unterminated quote takes the rest of the input as the word; the
shell would refuse to run it at all.
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
_HEREDOC_OPS = ("<<", "<<-")


@dataclass
class Token:
    kind: str          # "word" | "op" | "redir" | "heredoc"
    text: str
    quoted: bool = False   # a word: any quoting; a heredoc: its delimiter was quoted
    # Command strings inside `$(...)` or backticks that RUN when this token is
    # expanded (outermost only; a caller recurses for nested ones). Recorded while
    # tokenizing, because only then is an escaped or single-quoted `$(` or backtick
    # distinguishable from a live one.
    subs: list[str] = field(default_factory=list)
    start: int = 0     # span in the source: s[start:end]
    end: int = 0


@dataclass
class Redirection:
    op: str            # includes any descriptor number, e.g. "2>&"
    target: str        # the word after the operator ("" if the line ended)
    body: str | None = None      # a heredoc's body, for `<<` and `<<-`


@dataclass
class SimpleCommand:
    words: list[str] = field(default_factory=list)
    quoted: list[bool] = field(default_factory=list)
    redirections: list[Redirection] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)    # from words, targets, heredoc bodies


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


def _backtick_sub(s: str, i: int) -> tuple[str, int]:
    """(inner command, index past it) for the backtick at s[i]."""
    k = _closing_backtick(s, i)
    return _BACKTICK_UNESCAPE.sub(r"\1", s[i + 1:k]), k + 1


_ANSI_NAMED = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
               "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
_HEX = "0123456789abcdefABCDEF"


def _ansi_c(s: str, j: int) -> tuple[str, int]:
    """Decode a bash $'...' body starting at s[j]; (value, index past the closing quote).

    Decoded as bash does, because the VALUE is the path the shell writes to: a
    partially decoded `$'data/\\x2e\\x2e/docs/f.md'` read as a data/ path while bash
    wrote to docs/ (Codex review of a7d6f2a). An unknown escape is kept as written.
    """
    out: list[str] = []
    n = len(s)
    while j < n and s[j] != "'":
        if s[j] != "\\" or j + 1 >= n:
            out.append(s[j])
            j += 1
            continue
        c = s[j + 1]
        if c in _ANSI_NAMED:
            out.append(_ANSI_NAMED[c])
            j += 2
        elif c in "01234567":
            k = j + 1
            while k < n and k < j + 4 and s[k] in "01234567":
                k += 1
            out.append(chr(int(s[j + 1:k], 8) & 0xFF))
            j = k
        elif c in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[c]
            k = j + 2
            while k < n and k < j + 2 + width and s[k] in _HEX:
                k += 1
            code = int(s[j + 2:k], 16) if k > j + 2 else -1
            # Out-of-range or surrogate: kept as written. Raising here would make the
            # hook fail open (Codex review of 837e638). Bash emits raw BYTES for
            # \\xHH >= 0x80; this yields the code point instead, a documented gap.
            if code < 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                out.append(s[j:k])
            else:
                out.append(chr(code))
            j = k
        elif c == "c" and j + 2 < n and s[j + 2] != "'":
            out.append(chr(ord(s[j + 2]) & 0x1F))
            j += 3
        else:
            out.append(s[j:j + 2])
            j += 2
    return "".join(out), j + 1


def _expansion_subs(text: str) -> list[str]:
    """Commands in `$(...)` and backticks in text read like the inside of double quotes
    (an unquoted heredoc body): a backslash escapes, nothing else quotes."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith("$(", i):
            inner, i = _paren_sub(text, i)
            out.append(inner)
            continue
        if text[i] == "`":
            inner, i = _backtick_sub(text, i)
            out.append(inner)
            continue
        i += 1
    return out


def _read_heredoc_bodies(s: str, pos: int, pending: list[tuple[str, bool, bool]],
                         toks: list[Token]) -> int:
    """Emit one "heredoc" token per pending heredoc, reading bodies from `pos` (the
    first character after the newline that ended the command line). Returns the index
    of the newline after the last delimiter line, or len(s)."""
    n = len(s)
    next_pos = pos - 1
    for delim, strip_tabs, expand in pending:
        body_start = pos
        while True:
            if pos >= n:
                body_end, next_pos = n, n
                break
            le = s.find("\n", pos)
            le = n if le == -1 else le
            line = s[pos:le]
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                body_end, next_pos = pos, le
                break
            pos = le + 1
        body = s[body_start:body_end]
        # quoted=True: the delimiter was quoted or escaped, so the body is literal.
        toks.append(Token("heredoc", body, quoted=not expand,
                          subs=_expansion_subs(body) if expand else [],
                          start=body_start, end=body_end))
        pos = next_pos + 1
    return next_pos


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    word: list[str] = []
    subs: list[str] = []
    in_word = False            # a word has started (even if empty, e.g. '')
    quoted = False             # any quote in the word
    escaped = False            # any backslash escape in the word
    wstart = 0
    heredoc_op = None          # set after `<<`: the next word is its delimiter
    pending: list[tuple[str, bool, bool]] = []     # (delimiter, strip tabs, expand body)
    i = 0
    n = len(s)

    def start_word(at: int) -> None:
        nonlocal in_word, wstart
        if not in_word:
            in_word, wstart = True, at

    def end_word(at: int) -> None:
        nonlocal word, in_word, quoted, escaped, subs, heredoc_op
        if in_word:
            text = "".join(word)
            toks.append(Token("word", text, quoted, subs, wstart, at))
            if heredoc_op is not None:
                # Any quoting in the delimiter turns expansion of the body off.
                pending.append((text, heredoc_op == "<<-", not (quoted or escaped)))
                heredoc_op = None
        word, in_word, quoted, escaped, subs = [], False, False, False, []

    while i < n:
        ch = s[i]

        if ch in " \t":
            end_word(i)
            i += 1
            continue

        if ch == "\n":
            end_word(i)
            if pending:
                i = _read_heredoc_bodies(s, i + 1, pending, toks)
                pending = []
                if i >= n:
                    break
            toks.append(Token("op", "\n", start=i, end=i + 1))
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
            start_word(i)
            if i + 1 < n:
                word.append(s[i + 1])
            escaped = True
            i += 2
            continue

        if ch == "'":
            start_word(i)
            j = s.find("'", i + 1)
            j = n if j == -1 else j
            word.append(s[i + 1:j])
            quoted = True
            i = j + 1
            continue

        if ch == '"':
            start_word(i)
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
                    inner, j = _backtick_sub(s, i)
                    subs.append(inner)
                    word.append(s[i:j])
                    i = j
                    continue
                word.append(s[i])
                i += 1
            i += 1                                   # closing quote (or past the end)
            quoted = True
            continue

        if s.startswith("$'", i):                     # ANSI-C quoting (bash)
            start_word(i)
            text, i = _ansi_c(s, i + 2)
            word.append(text)
            quoted = True
            continue

        if s.startswith('$"', i):                      # locale quoting: a double quote
            start_word(i)
            i += 1
            continue

        if s.startswith("$(", i):
            start_word(i)
            inner, j = _paren_sub(s, i)
            subs.append(inner)
            word.append(s[i:j])
            i = j
            continue

        if s.startswith("${", i):                      # parameter expansion, not a command
            start_word(i)
            j = _read_balanced(s, i + 1, "{", "}")
            word.append(s[i:j])
            i = j
            continue

        if ch == "`":
            start_word(i)
            inner, j = _backtick_sub(s, i)
            subs.append(inner)
            word.append(s[i:j])
            i = j
            continue

        if ch in "<>" or (ch == "&" and s.startswith("&>", i)):
            # A word made only of unquoted digits, touching the operator, is its fd.
            # `&>` takes no descriptor number: in `tee 2&>f` the 2 is an argument.
            fd, rstart = "", i
            if (ch != "&" and in_word and not quoted and not escaped and word
                    and "".join(word).isdigit()):
                fd, rstart = "".join(word), wstart
                word, in_word = [], False
            end_word(i)
            op = next(o for o in _REDIR if s.startswith(o, i))
            toks.append(Token("redir", fd + op, start=rstart, end=i + len(op)))
            if op in _HEREDOC_OPS:
                heredoc_op = op
            i += len(op)
            continue

        if ch in ";&|()":
            end_word(i)
            op = next(o for o in _CONTROL if s.startswith(o, i))
            toks.append(Token("op", op, start=i, end=i + len(op)))
            i += len(op)
            continue

        start_word(i)
        word.append(ch)
        i += 1

    end_word(i)
    if pending:                                 # heredoc opened on the last line
        _read_heredoc_bodies(s, n, pending, toks)
    return toks


def simple_commands(s: str) -> list[SimpleCommand]:
    """The command line as simple commands: words, with redirections pulled out.

    Each redirection operator takes the next word as its target, so that word is never
    mistaken for an argument (the defect behind `2>&1 docs/f.md` being missed). Each
    heredoc body is attached to the `<<` that opened it, in order, even when the body
    arrives after later commands on the same line.
    """
    cmds: list[SimpleCommand] = []
    cur = SimpleCommand()
    awaiting_body: list[tuple[SimpleCommand, Redirection]] = []
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
        if t.kind == "heredoc":
            if awaiting_body:
                owner, redir = awaiting_body.pop(0)
                redir.body = t.text
                owner.subs.extend(t.subs)
            k += 1
            continue
        if t.kind == "redir":
            target = ""
            if k + 1 < len(toks) and toks[k + 1].kind == "word":
                target = toks[k + 1].text
                cur.subs.extend(toks[k + 1].subs)
                k += 1
            redir = Redirection(t.text, target)
            cur.redirections.append(redir)
            if t.text.lstrip("0123456789") in _HEREDOC_OPS:
                awaiting_body.append((cur, redir))
            k += 1
            continue
        cur.words.append(t.text)
        cur.quoted.append(t.quoted)
        cur.subs.extend(t.subs)
        k += 1
    if cur.words or cur.redirections:
        cmds.append(cur)
    return cmds
