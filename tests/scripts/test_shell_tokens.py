"""Tests for tools/shell_tokens.py -- a small POSIX-shell tokenizer used to find the
files a command writes. It replaces regexes that decided where a command's arguments
ended, which missed ordinary shell forms (Codex reviews, 2026-09-28)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from shell_tokens import tokenize, simple_commands  # noqa: E402


def kinds(s):
    return [(t.kind, t.text) for t in tokenize(s)]


def test_words_and_quotes():
    assert kinds("""sed -i '' 's/a b/c/' "my file.md" a\\ b""") == [
        ("word", "sed"), ("word", "-i"), ("word", ""), ("word", "s/a b/c/"),
        ("word", "my file.md"), ("word", "a b"),
    ]


def test_adjacent_quoted_and_bare_spans_are_one_word():
    assert kinds("""x'a b'"c"d""") == [("word", "xa bcd")]


def test_double_quote_escapes():
    assert kinds('"a \\"b\\" \\$c \\n"') == [("word", 'a "b" $c \\n')]


def test_quoted_flag():
    toks = tokenize("""plain 'q' "q" x'q'""")
    assert [t.quoted for t in toks] == [False, True, True, True]


def test_control_operators():
    assert kinds("a && b || c; d | e & f |& g") == [
        ("word", "a"), ("op", "&&"), ("word", "b"), ("op", "||"), ("word", "c"),
        ("op", ";"), ("word", "d"), ("op", "|"), ("word", "e"), ("op", "&"),
        ("word", "f"), ("op", "|&"), ("word", "g"),
    ]


def test_newline_and_parens_are_operators():
    assert kinds("(a)\nb") == [("op", "("), ("word", "a"), ("op", ")"), ("op", "\n"), ("word", "b")]


@pytest.mark.parametrize("src,expected", [
    ("a 2>&1 b", [("word", "a"), ("redir", "2>&"), ("word", "1"), ("word", "b")]),
    ("a 3<&0 b", [("word", "a"), ("redir", "3<&"), ("word", "0"), ("word", "b")]),
    ("a &>f b", [("word", "a"), ("redir", "&>"), ("word", "f"), ("word", "b")]),
    # &> takes no descriptor number: the digits stay a word (Codex fd12aa3 F4)
    ("a 2&>f", [("word", "a"), ("word", "2"), ("redir", "&>"), ("word", "f")]),
    ("a &>>f", [("word", "a"), ("redir", "&>>"), ("word", "f")]),
    ("a >>f", [("word", "a"), ("redir", ">>"), ("word", "f")]),
    ("a >|f", [("word", "a"), ("redir", ">|"), ("word", "f")]),
    ("a <<<x", [("word", "a"), ("redir", "<<<"), ("word", "x")]),
    ("a <<-EOF", [("word", "a"), ("redir", "<<-"), ("word", "EOF")]),
    ("a <>f", [("word", "a"), ("redir", "<>"), ("word", "f")]),
    # digits only become an fd when they touch the operator
    ("a 2 > f", [("word", "a"), ("word", "2"), ("redir", ">"), ("word", "f")]),
    ("a x2>f", [("word", "a"), ("word", "x2"), ("redir", ">"), ("word", "f")]),
    # a quoted operator is text
    ("""a '>' "2>&1" """, [("word", "a"), ("word", ">"), ("word", "2>&1")]),
])
def test_redirections(src, expected):
    assert kinds(src) == expected


def test_command_substitution_stays_in_the_word():
    assert kinds("echo $(date +%s; echo ')') x") == [
        ("word", "echo"), ("word", "$(date +%s; echo ')')"), ("word", "x"),
    ]
    assert kinds("echo `a | b` x") == [("word", "echo"), ("word", "`a | b`"), ("word", "x")]
    assert kinds("echo ${A:-x y} z") == [("word", "echo"), ("word", "${A:-x y}"), ("word", "z")]


def test_comment_only_at_word_start():
    assert kinds("a # b > c\nd") == [("word", "a"), ("op", "\n"), ("word", "d")]
    assert kinds("a#b") == [("word", "a#b")]


def test_line_continuation():
    assert kinds("a \\\nb") == [("word", "a"), ("word", "b")]


def test_unterminated_quote_takes_the_rest():
    assert kinds("a 'b c") == [("word", "a"), ("word", "b c")]


def test_simple_commands_split_words_and_redirections():
    cmds = simple_commands("sed -i x 2>&1 f > out.log | tee -a g; echo h")
    assert [c.words for c in cmds] == [["sed", "-i", "x", "f"], ["tee", "-a", "g"], ["echo", "h"]]
    assert [(r.op, r.target) for r in cmds[0].redirections] == [("2>&", "1"), (">", "out.log")]


# --- edge paths found by mutation testing -----------------------------------------

def test_command_substitution_edges():
    assert kinds("echo $(a \\) b) c") == [("word", "echo"), ("word", "$(a \\) b)"), ("word", "c")]
    assert kinds("echo $(a (b) c) d") == [("word", "echo"), ("word", "$(a (b) c)"), ("word", "d")]
    assert kinds("echo $(a b") == [("word", "echo"), ("word", "$(a b")]


def test_trailing_backslash_does_not_crash():
    assert kinds("a \\") == [("word", "a"), ("word", "")]


def test_backslash_newline_inside_double_quotes_is_removed():
    assert kinds('"a\\\nb"') == [("word", "ab")]


def test_empty_commands_are_dropped():
    assert [c.words for c in simple_commands(";a;;\n")] == [["a"]]


def test_redirection_with_no_word_after_it():
    cmds = simple_commands("a > ; b")
    assert [(c.words, [(r.op, r.target) for r in c.redirections]) for c in cmds] == [
        (["a"], [(">", "")]), (["b"], []),
    ]


def test_escape_directly_before_a_closing_character():
    # an escaped paren right before the closing one
    assert kinds("echo $(a \\)) b") == [("word", "echo"), ("word", "$(a \\))"), ("word", "b")]
    # an escaped quote right before the closing quote
    assert kinds('"a\\"" b') == [("word", 'a"'), ("word", "b")]


def subs(src):
    return [t.subs for t in tokenize(src) if t.kind == "word"]


def test_command_substitutions_are_recorded_while_tokenizing():
    assert subs("x=$(tee a)") == [["tee a"]]
    assert subs("`b c`") == [["b c"]]
    assert subs("a$(b)c$(d (e))f") == [["b", "d (e)"]]
    assert subs("$(a $(b))") == [["a $(b)"]]            # outer only; caller recurses
    assert subs("${x:-y}") == [[]]
    assert subs("plain") == [[]]
    assert subs("$(unclosed") == [["unclosed"]]


def test_substitutions_inside_double_quotes_run():
    assert subs('"a $(tee f) b"') == [["tee f"]]
    assert subs('"a `tee f` b"') == [["tee f"]]
    assert subs('"a $(echo ")") b"') == [['echo ")"']]


def test_escaped_or_single_quoted_substitutions_do_not_run():
    """Real-data replay 2026-09-30: `\`` inside double quotes is a literal backtick,
    but scanning the finished word text read it as a live one."""
    assert subs('"a \\`b\\` c"') == [[]]
    assert subs('"a \\$(b) c"') == [[]]
    assert subs("'a $(b) `c`'") == [[]]


def test_substitution_text_stays_in_the_word_inside_double_quotes():
    assert kinds('"a $(b) c"') == [("word", "a $(b) c")]
    assert kinds('"a `b` c"') == [("word", "a `b` c")]
    assert kinds('"$(b)"') == [("word", "$(b)")]
    assert kinds('"`b`"') == [("word", "`b`")]


def test_redirect_target_substitutions_are_collected():
    assert simple_commands("a > $(tee f)")[0].subs == ["tee f"]
