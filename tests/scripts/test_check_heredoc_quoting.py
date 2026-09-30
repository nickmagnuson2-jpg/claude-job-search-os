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


# --- 2026-09-30: rebuilt on tools/shell_tokens.py ------------------------------------
# The regex version found an "opener" anywhere in the text, including inside a QUOTED
# heredoc's body and inside quoted strings, and then scanned the lines after it as if
# they were a body. It fired three times in one session on commands that wrote Python
# test code containing heredoc markers as string data.

def test_heredoc_marker_inside_a_QUOTED_heredoc_body_is_data():
    """The false positive, 3 fires on 2026-09-30."""
    cmd = ("cat >> t.py <<'EOF'\n"
           "src = 'cat <<A'\n"
           "print(" + BT + "g" + BT + ")\n"
           "A\n"
           "EOF\n")
    assert run(cmd) == 0


def test_heredoc_marker_inside_a_quoted_string_is_not_an_opener():
    assert run('echo "cat <<EOF"\necho ' + BT + "date" + BT) == 0


def test_an_escaped_delimiter_disables_expansion():
    assert run("cat <<\\EOF\n " + BT + "token" + BT + "\nEOF\n") == 0


def test_a_delimiter_with_punctuation_is_still_a_delimiter():
    assert run("cat <<END-MARK\n " + BT + "token" + BT + "\nEND-MARK\n") == 2
    assert run("cat <<'END-MARK'\n " + BT + "token" + BT + "\nEND-MARK\n") == 0


def test_backtick_AFTER_the_body_is_not_in_the_body():
    assert run("cat <<EOF > f\nplain\nEOF\necho " + BT + "date" + BT) == 0


def test_second_heredoc_on_one_line_is_checked_too():
    cmd = ("cat <<'A' > f; cat <<B > g\n" + BT + "x" + BT + "\nA\n"
           + BT + "y" + BT + "\nB\n")
    assert run(cmd) == 2


def test_the_block_message_names_the_offending_delimiter():
    """Each body is paired with ITS delimiter, in order, so the message names the right
    one (a fallback of "EOF" would hide a broken pairing)."""
    p = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"tool_input": {"command":
                           "cat <<'SAFE' > f; cat <<BAD > g\nx\nSAFE\n" + BT + "y" + BT + "\nBAD\n"}}),
                       capture_output=True, text=True)
    assert p.returncode == 2
    assert "<<BAD" in p.stderr
    assert "<<SAFE" not in p.stderr
    assert "<<'BAD'" in p.stderr


def test_a_descriptor_numbered_heredoc_is_still_a_heredoc():
    assert run("cat 0<<EOF\n " + BT + "token" + BT + "\nEOF\n") == 2


def test_an_even_run_of_backslashes_leaves_the_backtick_live():
    """Codex review of 68cf50a, F2: two backslashes escape each other, so the backtick
    after them runs."""
    assert run("cat <<A\n\\\\" + BT + "id\\\\" + BT + "\nA\n") == 2
    assert run("cat <<A\n\\" + BT + "id\\" + BT + "\nA\n") == 0
    assert run("cat <<A\n\\\\\\" + BT + "id\\\\\\" + BT + "\nA\n") == 0   # odd runs: escaped
