"""Tests for tools/check_heredoc_quoting.py.

THE DEFECT, 2026-09-16: an unquoted heredoc delimiter enables shell expansion for the
whole document, so every backticked token in the body runs as a command and is replaced
by its (usually empty) output. Three memory files were corrupted that way. The write
succeeded, the script exited 0, and grepping for an empty backtick PAIR found nothing,
because the substitution leaves nothing at all.

The hook must be NARROW: quoted delimiters and $VAR-only bodies are legitimate and
common, and a hook that blocks them would be turned off within a day.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "tools" / "check_heredoc_quoting.py"
BT = chr(96)


def run(command: str) -> int:
    p = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"tool_input": {"command": command}}),
                       capture_output=True, text=True)
    return p.returncode


def test_unquoted_delimiter_with_a_backtick_is_blocked():
    """The exact 2026-09-16 shape."""
    assert run("cat <<EOF\n a " + BT + "token" + BT + " here\nEOF\n") == 2


def test_a_quoted_delimiter_is_never_blocked():
    """<<'EOF' disables expansion; the content is written literally. This is the FIX the
    hook tells you to make, so blocking it would be incoherent."""
    assert run("cat <<'EOF'\n a " + BT + "token" + BT + " here\nEOF\n") == 0
    assert run('cat <<"EOF"\n a ' + BT + "token" + BT + ' here\nEOF\n') == 0


def test_an_unquoted_delimiter_without_backticks_is_allowed():
    """$VAR interpolation is usually WHY the delimiter is bare. Blocking it would make
    the hook a nuisance, and $ alone does not corrupt content."""
    assert run("cat <<EOF\n plain $VAR and $HOME\nEOF\n") == 0


def test_a_here_string_is_not_a_heredoc():
    """<<< takes a word, not a document. It has no body to corrupt."""
    assert run('grep x <<< "a ' + BT + "b" + BT + ' c"') == 0


def test_a_dash_heredoc_terminator_may_be_tab_indented():
    """<<- strips leading tabs from the terminator. Missing that would run the body scan
    past the real end and produce a false positive on whatever followed."""
    assert run("cat <<-EOF\n\tplain body\n\tEOF\ncat " + BT + "date" + BT) == 0


def test_only_the_offending_heredoc_is_reported():
    cmd = ("cat <<'SAFE'\n" + BT + "x" + BT + "\nSAFE\n"
           "cat <<BAD\n" + BT + "y" + BT + "\nBAD\n")
    assert run(cmd) == 2


def test_no_heredoc_at_all_is_clean():
    assert run("echo hello && git status") == 0


def test_malformed_input_fails_OPEN():
    """A hook that blocks on its own parse failure stops all work. Fail-open is the only
    safe direction for a PreToolUse guard."""
    p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert p.returncode == 0


def test_the_block_message_names_the_fix():
    """A BLOCK whose message does not carry the correction just costs a turn."""
    p = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"tool_input": {"command":
                           "cat <<EOF\n" + BT + "x" + BT + "\nEOF\n"}}),
                       capture_output=True, text=True)
    assert p.returncode == 2
    assert "<<'EOF'" in p.stderr
    assert "argument" in p.stderr


def test_the_hook_is_wired_into_settings():
    """An unwired hook is a file, not a guard."""
    s = (REPO / ".claude" / "settings.json").read_text()
    assert "check_heredoc_quoting.py" in s
