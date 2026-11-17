"""Quote PAIRING tests for hook_command_lint.strip_literals (proposal, 2026-11-16).

Written to fail on the two-regex version at HEAD. The old double-quote regex
`"[^"$`]*"` skips a span that holds `$`, then pairs that span's CLOSING quote with
the NEXT span's opening quote. Quoted text gets exposed and real unquoted code gets
blanked. These tests pin both directions.

"""
import json
import os
import subprocess
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
sys.path.insert(0, TOOLS)

import hook_command_lint as h  # noqa: E402

REPRO = 'f=$(grep -rl -- "--include=\\*" d/); echo "== $f"; grep -n -- "--include=\\*" "$f"'


def _hook(name: str, command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    r = subprocess.run([sys.executable, os.path.join(TOOLS, name)], input=payload,
                       capture_output=True, text=True, timeout=30)
    return r.returncode


# --- the reported repro -------------------------------------------------------------

def test_repro_quoted_include_glob_is_not_exposed():
    assert "--include=" not in h.strip_literals(REPRO)


def test_repro_passes_check_unquoted_glob():
    assert _hook("check_unquoted_glob.py", REPRO) == 0


def test_repro_parts_each_pass_check_unquoted_glob():
    for part in REPRO.split(";"):
        assert _hook("check_unquoted_glob.py", part.strip()) == 0


def test_real_unquoted_glob_after_a_dollar_span_is_still_blocked():
    # The other direction: the bad pairing used to BLANK the real code in between.
    cmd = 'echo "== $f"; grep -rn foo --include=*.md .; echo "done"'
    assert "--include=*.md" in h.strip_literals(cmd)
    # check_unquoted_glob now quotes a simple unquoted glob in place (exit 0 with an
    # updatedInput) instead of blocking; either way the real glob must be acted on.
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "check_unquoted_glob.py")],
                       input=payload, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 or "--include='*.md'" in r.stdout


# --- the other direction, for the command-position hooks ---------------------------

def test_real_code_between_a_dollar_span_and_a_literal_span_survives():
    cmd = 'echo "$x"; python tools/foo.py; echo "done"'
    out = h.strip_literals(cmd)
    assert "python tools/foo.py" in out
    assert "done" not in out


# --- nested command substitution ----------------------------------------------------

def test_quotes_inside_dollar_paren_inside_double_quotes():
    cmd = 'echo "$(grep -c "--include=*.md" x)"; ls'
    out = h.strip_literals(cmd)
    assert "--include" not in out      # inner literal stripped
    assert "grep -c" in out            # the substitution's command stays visible
    assert out.rstrip().endswith("ls")


def test_nested_two_levels():
    cmd = 'x="$(a "$(b "lit one")" "lit two")"; python run.py'
    out = h.strip_literals(cmd)
    assert "lit one" not in out and "lit two" not in out
    assert "python run.py" in out


def test_real_python_inside_dollar_paren_is_still_visible():
    assert "python" in h.strip_literals('echo "$(python x.py "arg")"')


def test_paren_inside_quotes_inside_subst_does_not_close_it():
    cmd = 'v="$(echo ")")"; python go.py'
    assert "python go.py" in h.strip_literals(cmd)


# --- escaped quotes and other quote forms -------------------------------------------

def test_escaped_double_quote_inside_double_quotes():
    cmd = 'echo "say \\"python now\\" please"; ls'
    out = h.strip_literals(cmd)
    assert "python" not in out
    assert "ls" in out


def test_escaped_quote_outside_quotes_is_not_an_opener():
    cmd = 'echo \\"; python go.py; echo \\"'
    assert "python go.py" in h.strip_literals(cmd)


def test_apostrophe_inside_double_quotes_does_not_open_single():
    cmd = 'echo "it\'s fine"; python go.py; echo \'end\''
    out = h.strip_literals(cmd)
    assert "python go.py" in out
    assert "fine" not in out and "end" not in out


def test_double_quote_inside_single_quotes_is_literal():
    cmd = "echo 'a \" b'; python go.py; echo \"c\""
    out = h.strip_literals(cmd)
    assert "python go.py" in out
    assert '"c"' not in out and " b" not in out


def test_ansi_c_quoting_with_escaped_single_quote():
    cmd = "echo $'it\\'s python'; ls"
    out = h.strip_literals(cmd)
    assert "python" not in out
    assert "ls" in out


def test_backtick_inside_double_quotes_keeps_span_and_strips_inner_literal():
    cmd = 'echo "`grep "lit" f`"; ls'
    out = h.strip_literals(cmd)
    assert "grep" in out and "lit" not in out


# --- exact output: the text outside literals is copied byte for byte -------------------

@pytest.mark.parametrize("cmd, expected", [
    # backslash outside quotes: copied as a pair, and \' does not open a span
    ("echo \\'; ls 'x'", "echo \\'; ls  "),
    # parentheses and $( ) outside quotes are copied, and a ) at top level does not end
    ("a $(b) (c) d 'e'", "a $(b) (c) d  "),
    # a kept "$var" span is copied whole, escapes included
    ('echo "== $f \\" x"; ls', 'echo "== $f \\" x"; ls'),
    # a kept span with a substitution: inner literal blanked, the rest copied
    ('echo "$(grep "lit" f) tail"', 'echo "$(grep   f) tail"'),
    # escaped backtick inside a backtick substitution inside double quotes
    ('"`a \\`b`" z', '"`a \\`b`" z'),
    # a backtick body of odd length is read one character at a time
    ('"`abc`" "lit"', '"`abc`"  '),
    # a ( ) pair inside $( ) does not close the substitution early
    ('echo "$(a (b) "lit")"; ls', 'echo "$(a (b)  )"; ls'),
])
def test_exact_output(cmd, expected):
    assert h.strip_literals(cmd) == expected


def test_unterminated_backtick_in_double_quotes_kept_verbatim():
    assert h.strip_literals('echo "`python x') == 'echo "`python x'


# --- unterminated input stays visible (a blocking hook should still see it) ---------

@pytest.mark.parametrize("cmd", ['echo "python open', "echo 'python open", 'echo "$(python x'])
def test_unterminated_spans_kept_verbatim(cmd):
    assert "python" in h.strip_literals(cmd)
