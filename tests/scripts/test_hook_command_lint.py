"""Tests for tools/hook_command_lint.strip_literals — shared literal-context
stripping for command-detection PreToolUse hooks.

The heredoc cases are the 2026-06-05 addition: the parked "2nd heredoc fire" gate
tripped when a `git commit -F - <<'EOF' ... python ... EOF` message body was
wrongly read as a command. See
[[feedback_command_hook_match_position_not_substring]].
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import hook_command_lint as h  # noqa: E402


# --- quoted-span behavior (preserved from the per-hook originals) ---

def test_single_quoted_stripped():
    assert "secret" not in h.strip_literals("echo 'secret python'")


def test_double_quoted_literal_stripped():
    out = h.strip_literals('grep "find python here" file')
    assert "grep" in out and "find" not in out


def test_double_quoted_with_substitution_preserved():
    # A real `$(python ...)` inside double quotes must survive for scanning.
    out = h.strip_literals('echo "$(python x.py)"')
    assert "python" in out


# --- heredoc bodies (the new behavior) ---

def test_quoted_heredoc_body_stripped_todays_case():
    cmd = (
        "git commit -F - <<'EOF'\n"
        "fix the python crash bug\n"
        "ran python and crashed\n"
        "EOF"
    )
    out = h.strip_literals(cmd)
    assert "crash" not in out          # body gone
    assert "git commit" in out         # operator line kept


def test_unquoted_heredoc_without_substitution_stripped():
    cmd = "cat <<EOF\njust some python words here\nEOF"
    assert "words" not in h.strip_literals(cmd)


def test_unquoted_heredoc_with_substitution_preserved():
    # Unquoted heredoc body CAN expand; a real $() invocation must be kept so a
    # genuine bare-python inside it is still caught.
    cmd = "cat <<EOF\n$(python danger.py)\nEOF"
    assert "python" in h.strip_literals(cmd)


def test_dash_heredoc_indented_close_stripped():
    cmd = "cat <<-'EOF'\n\tpython crash text\n\tEOF"
    assert "crash" not in h.strip_literals(cmd)


def test_double_quoted_delimiter_heredoc_stripped():
    cmd = 'cat <<"EOF"\npython words\nEOF'
    assert "words" not in h.strip_literals(cmd)


def test_heredoc_in_command_substitution_6_2_case():
    # `git commit -m "$(cat <<'EOF' ... EOF)"` — quoted heredoc body fully
    # stripped even though it contains a $() (no expansion under a quoted tag).
    cmd = "git commit -m \"$(cat <<'EOF'\nfix $(python x) desc\nEOF\n)\""
    out = h.strip_literals(cmd)
    assert "desc" not in out


def test_real_python_outside_heredoc_survives():
    # The fix must NOT over-strip: a real bare-python before a heredoc stays.
    cmd = "python tools/x.py && git commit -F - <<'EOF'\npython prose\nEOF"
    out = h.strip_literals(cmd)
    assert out.lstrip().startswith("python tools/x.py")


def test_no_heredoc_is_unchanged_modulo_quotes():
    cmd = "python3 tools/foo.py --flag"
    assert h.strip_literals(cmd) == cmd


# --- python_calls: which script, -c code or -m module a python word list runs --------

import pytest  # noqa: E402


@pytest.mark.parametrize("words, script, args, code, module", [
    (["python3", "s.py", "a"], "s.py", ["a"], None, None),
    (["/usr/bin/python3.12", "-u", "s.py"], "s.py", [], None, None),
    (["python3", "-X", "dev", "s.py", "a"], "s.py", ["a"], None, None),
    (["python3", "-Xdev", "s.py"], "s.py", [], None, None),
    (["python3", "-W", "ignore", "-c", "X", "a"], None, ["a"], "X", None),
    (["python3", "--quiet", "s.py"], "s.py", [], None, None),
    (["python3", "-c", "X"], None, [], "X", None),
    (["python3", "-cX", "a"], None, ["a"], "X", None),
    # a CLUSTER, read as CPython reads it (Grok d71cd64 F1: -uc hid the code)
    (["python3", "-uc", "X"], None, [], "X", None),
    (["python3", "-Buc", "X", "a"], None, ["a"], "X", None),
    (["python3", "-um", "mod", "a"], None, ["a"], None, "mod"),
    (["python3", "-m", "mod"], None, [], None, "mod"),
    (["python3", "-"], None, [], None, None),
    (["python3"], None, [], None, None),
])
def test_python_calls_reads_the_program(words, script, args, code, module):
    (call,) = list(h.python_calls(words))
    assert (call.script, call.args, call.code, call.module) == (script, args, code, module)


def test_python_calls_finds_every_interpreter_and_ignores_other_words():
    calls = list(h.python_calls(["time", "python3", "a.py", "python-dateutil"]))
    assert [c.script for c in calls] == ["a.py"]
    assert list(h.python_calls(["echo", "python.md"])) == []


def test_a_trailing_c_with_no_code_is_empty_not_a_crash():
    (call,) = list(h.python_calls(["python3", "-c"]))
    assert call.code == ""


def test_python_script_args():
    assert h.python_script_args(["python3", "tools/t.py", "x"], "t.py") == ["x"]
    assert h.python_script_args(["python3", "-c", "t.py"], "t.py") is None


def test_a_second_c_after_the_code_is_an_argument_not_an_option():
    (call,) = list(h.python_calls(["python3", "-c", "X", "-c", "Y"]))
    assert call.code == "X" and call.args == ["-c", "Y"]


def test_a_long_option_that_takes_a_value_skips_it():
    (call,) = list(h.python_calls(["python3", "--check-hash-based-pycs", "always", "s.py"]))
    assert call.script == "s.py" and call.code is None
