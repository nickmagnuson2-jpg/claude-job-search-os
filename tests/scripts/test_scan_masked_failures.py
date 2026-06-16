"""Tests for scan_transcript_failures.is_masked_python_failure — the 2026-06-05
masked-exit detection.

A masked failure is a Bash result that reports success (is_error falsy) because a
downstream pipe (`| head`) or `|| true` swallowed python's non-zero exit, while
the output still carries a Python traceback. The is_error filter never sees these,
so the Stop scan would miss them without this helper.

Origin: 2026-06-05 — `python3 -c "...append_entry(event=...)" 2>&1 | head -20`
raised a TypeError but exited 0 (head's exit), so it was auto-logged nowhere.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import scan_transcript_failures as st  # noqa: E402

TB = (
    "Traceback (most recent call last):\n"
    '  File "<string>", line 3, in <module>\n'
    "    s.append_entry(event='invocation')\n"
    "TypeError: append_entry() got an unexpected keyword argument 'event'"
)


def test_masked_pipe_to_head_detected():
    cmd = (
        'cd ~/x && python3 -c "import ss_log_append as s; s.append_entry(event=1)"'
        " 2>&1 | head -20"
    )
    assert st.is_masked_python_failure("Bash", cmd, TB, None) is True


def test_masked_with_explicit_false_is_error():
    cmd = 'python3 -c "import x" 2>&1 | head'
    assert st.is_masked_python_failure("Bash", cmd, TB, False) is True


def test_real_error_not_treated_as_masked():
    # is_error True is handled by the normal path, not the masked path.
    cmd = 'python3 -c "x"'
    assert st.is_masked_python_failure("Bash", cmd, TB, True) is False


def test_no_traceback_not_masked():
    cmd = 'python3 -c "print(1)"'
    assert st.is_masked_python_failure("Bash", cmd, "1\n", None) is False


def test_non_python_command_displaying_traceback_not_masked():
    # grep/cat surfacing a traceback already stored in a log must NOT be flagged.
    cmd = "grep -n Traceback memory/friction-log.md"
    text = "37:| 2026-06-05 | bash:cd | Traceback (most recent call last): ..."
    assert st.is_masked_python_failure("Bash", cmd, text, None) is False


def test_grep_for_python_substring_not_masked():
    cmd = "grep -n python3 memory/friction-log.md"
    text = "x Traceback (most recent call last): y"
    assert st.is_masked_python_failure("Bash", cmd, text, None) is False


def test_non_bash_tool_not_masked():
    assert st.is_masked_python_failure("Read", "", TB, None) is False


# --- crash-vs-display discrimination (smoke-derived, 2026-06-05) ---

def test_today_case_with_harness_footer_is_crash():
    # Real shape: TypeError, then the harness "Shell cwd was reset" footer.
    text = TB + "\nShell cwd was reset to /Users/mag/Documents/Obsidian/30-projects/job-search"
    cmd = 'cd ~/x && python3 -c "import ss_log_append as s; s.append_entry(event=1)" 2>&1 | head -20'
    assert st.looks_like_python_crash(text) is True
    assert st.is_masked_python_failure("Bash", cmd, text, None) is True


def test_python_dry_run_json_with_embedded_traceback_not_crash():
    # A successful dry-run that happened to print a stored traceback as data.
    cmd = 'python3 tools/pipe_write.py --repo-root . update --dry-run'
    text = (
        "Traceback (most recent call last):\n"
        '  File "x", line 1\n'
        "ValueError: old\n"
        '{"status": "ok", "action": "dry-run", "rows_before": 65}'
    )
    assert st.looks_like_python_crash(text) is False
    assert st.is_masked_python_failure("Bash", cmd, text, None) is False


def test_python_printing_friction_log_not_crash():
    # Command dumps the friction log (rows contain "Traceback" + exception cells),
    # but the final line is a table row, not a bare exception.
    cmd = 'python3 -c "import sys; print(open(\'memory/friction-log.md\').read())"'
    text = (
        "| 2026-06-05 | bash:cd | Traceback (most recent call last): | fix | 2 | memory |\n"
        "| 2026-05-28 | bash:git | PreToolUse hook | fix | 9 | script-patch |"
    )
    assert st.looks_like_python_crash(text) is False
    assert st.is_masked_python_failure("Bash", cmd, text, None) is False


def test_module_qualified_exception_is_crash():
    text = (
        "Traceback (most recent call last):\n"
        '  File "x", line 1\n'
        "google.auth.exceptions.RefreshError: ('invalid_grant: Token expired')"
    )
    assert st.looks_like_python_crash(text) is True


def test_returncode_print_not_crash():
    # A script that prints a child "returncode: 1" is not itself a python crash.
    text = (
        "Traceback (most recent call last):\n"
        "(captured from child)\n"
        "MATTHEW returncode: 1"
    )
    assert st.looks_like_python_crash(text) is False


# --- masked SHELL failures (2026-06-08): exit swallowed by a pipe/||, error text
# in output, but NOT a python traceback (zsh nomatch, BSD/GNU bad-flag). These
# slipped past both PostToolUseFailure (exit 0) and the python-masked path. ---

ZSH_NOMATCH = "(eval):5: no matches found: /tmp/analyze-*"
ZSH_NOMATCH2 = "zsh: no matches found: data/*friction*"
LS_BADFLAG = "ls: illegal option -- -\nusage: ls [-ABC...] [file ...]"


def test_zsh_no_matches_detected():
    cmd = 'ls /tmp/analyze-* 2>/dev/null && echo hit || echo "no temp clones"'
    assert st.is_masked_shell_failure("Bash", cmd, ZSH_NOMATCH, None) is True
    assert st.is_masked_shell_failure("Bash", cmd, ZSH_NOMATCH2, False) is True


def test_bsd_ls_illegal_option_detected():
    cmd = "ls -la --time-style=+%H:%M:%S data/ | awk '{print $6}'"
    assert st.is_masked_shell_failure("Bash", cmd, LS_BADFLAG, None) is True


def test_gnu_flag_piped_to_formatter_detected():
    # The real 2026-06-08 case: bad ls flag, exit swallowed by the awk pipe.
    cmd = "ls --full-time x | awk '{print $1}'"
    text = "ls: unrecognized option '--full-time'\nTry 'ls --help' for more information."
    assert st.is_masked_shell_failure("Bash", cmd, text, None) is True


def test_shell_real_error_not_treated_as_masked():
    # is_error True is handled by the normal path, not the masked-shell path.
    assert st.is_masked_shell_failure("Bash", "ls -z", ZSH_NOMATCH, True) is False


def test_shell_non_bash_not_masked():
    assert st.is_masked_shell_failure("Read", "", ZSH_NOMATCH, None) is False


def test_shell_clean_output_not_masked():
    assert st.is_masked_shell_failure("Bash", "ls -la", "total 8\nfile.txt", None) is False


# --- false-positive discrimination (the friction log itself contains the phrase) ---

def test_friction_log_table_row_not_masked():
    # cat/print of the friction log: rows start with `| ` so the line-anchored
    # signature must NOT match the phrase sitting inside a table cell.
    cmd = "cat memory/friction-log.md"
    text = (
        "| 2026-06-08 | bash | zsh aborts with 'no matches found' when a glob "
        "matches nothing | guard with 2>/dev/null | 1 | — |"
    )
    assert st.is_masked_shell_failure("Bash", cmd, text, None) is False


def test_grep_output_with_line_prefix_not_masked():
    # grep prefixes `N:` then the table row; still not at line start.
    cmd = 'grep -n "no matches" memory/friction-log.md'
    text = "33:| 2026-06-08 | bash | zsh ... no matches found ... | fix | 1 | — |"
    assert st.is_masked_shell_failure("Bash", cmd, text, None) is False


def test_masked_shell_error_line_extracts_clean_line():
    # The nature should be the error line, not the whole noisy output.
    text = "some prior output\n" + ZSH_NOMATCH + "\nno temp clones"
    line = st.masked_shell_error_line(text)
    assert line == ZSH_NOMATCH
