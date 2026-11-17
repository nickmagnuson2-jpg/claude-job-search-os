"""shell_tokens: per-segment source spans on each word (Q11, 2026-11-16).

`Token.quoted` is one flag per WORD, but a word can mix quoting (`--include="*.md"`,
`a'b'c`). strip_literals needs to know WHERE each quoted segment and each
substitution body sits in the source, so Token.spans records them. These tests pin
each kind; the existing test_shell_tokens.py pins that nothing else changed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from shell_tokens import tokenize  # noqa: E402


def _spans(s):
    """(kind, source text) for every span of every word, in order."""
    return [(k, s[a:b]) for t in tokenize(s) if t.kind == "word" for k, a, b in t.spans]


@pytest.mark.parametrize("s, expected", [
    ("echo 'a b'", [("sq", "'a b'")]),
    ('echo "a b"', [("dq", '"a b"')]),
    ('echo "x $f"', [("dqx", '"x $f"')]),
    ('echo "${x}"', [("dqx", '"${x}"')]),
    ("echo $'a\\'b'", [("sq", "$'a\\'b'")]),
    ("echo plain", []),
])
def test_each_quote_kind_records_its_span(s, expected):
    assert _spans(s) == expected


def test_a_word_with_mixed_quoting_records_only_the_quoted_segment():
    s = 'grep --include="*.md" x'
    assert _spans(s) == [("dq", '"*.md"')]


def test_adjacent_segments_in_one_word_are_separate_spans():
    s = "echo a'b'\"c\"d"
    assert _spans(s) == [("sq", "'b'"), ("dq", '"c"')]


def test_an_unquoted_substitution_records_its_body():
    s = 'f=$(grep -rl -- "--include=x" d/); echo ok'
    assert _spans(s) == [("sub", 'grep -rl -- "--include=x" d/')]


def test_a_substitution_inside_double_quotes_records_body_and_quote():
    s = 'echo "n: $(wc -l < f | tr -d " ")"'
    assert _spans(s) == [("sub", 'wc -l < f | tr -d " "'), ("dqx", '"n: $(wc -l < f | tr -d " ")"')]


def test_a_backtick_body_is_recorded():
    s = "echo `ls | head -1`"
    assert _spans(s) == [("sub", "ls | head -1")]


def test_an_escaped_dollar_does_not_make_a_double_quote_expand():
    assert _spans('echo "cost \\$5"') == [("dq", '"cost \\$5"')]


def test_an_unterminated_quote_runs_to_the_end():
    s = "echo 'open"
    assert _spans(s) == [("sq", "'open")]


def test_spans_reset_between_words():
    s = "echo 'a' b \"c\""
    words = [t for t in tokenize(s) if t.kind == "word"]
    assert [len(w.spans) for w in words] == [0, 1, 0, 1]


def test_a_backtick_inside_double_quotes_records_its_body():
    s = 'echo "a `ls` b"'
    assert _spans(s) == [("sub", "ls"), ("dqx", '"a `ls` b"')]


# Codex/Grok q11-r1 F2: a substitution inside an unquoted ${...} runs too. It is recorded
# as a "sub" span; Token.subs is unchanged (additive).
def test_a_substitution_inside_a_parameter_expansion_records_its_body():
    s = 'echo ${x:-$(cmd | head -1)}'
    assert _spans(s) == [("sub", "cmd | head -1")]
    word = [t for t in tokenize(s) if t.kind == "word"][1]
    assert word.subs == []


def test_a_quote_inside_a_parameter_expansion_does_not_start_a_sub():
    assert _spans("echo ${x:-'$(not run)'}") == []


@pytest.mark.parametrize("s, expected", [
    ("echo ${x:-\\$(not run)}", []),                          # an escaped $ starts nothing
    ("echo ${x:-'a'$(b)}", [("sub", "b")]),                   # a quoted part, then a real sub
    ("echo ${x:-`b`}", [("sub", "b")]),                       # a backtick inside ${...}
])
def test_parameter_expansion_sub_spans(s, expected):
    assert _spans(s) == expected
