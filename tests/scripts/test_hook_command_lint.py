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
