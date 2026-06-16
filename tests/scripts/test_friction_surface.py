"""Tests for tools/friction_surface.py — shared friction surface/nature derivation.

Covers the existing behavior (tools/*.py extraction, env-prefix fallback) AND the
2026-06-02 improvements (PreToolUse-block attribution + inline `python3 -c`
attribution) that replace coarse bash:cd / bash:git buckets.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import friction_surface as fs  # noqa: E402


# --- existing behavior must be preserved ---

def test_explicit_tools_script():
    cmd = "cd ~/proj && PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . update"
    assert fs.derive_surface("Bash", cmd, "") == "pipe_write.py"


def test_env_prefix_fallback_to_first_real_token():
    cmd = "PYTHONIOENCODING=utf-8 git status"
    assert fs.derive_surface("Bash", cmd, "") == "bash:git"


def test_non_bash_tool():
    assert fs.derive_surface("Edit", "", "anything") == "tool:Edit"


def test_excluded_script_does_not_become_surface():
    cmd = "cd ~/proj && python3 tools/friction_log.py append x y"
    # friction_log.py is excluded; falls through to first-token (cd).
    assert fs.derive_surface("Bash", cmd, "") == "bash:cd"


# --- NEW: PreToolUse block attribution (was bash:git / bash:cd) ---

def test_block_attributes_to_operand_script():
    err = (
        "PreToolUse:Bash hook error: [python3 /Users/x/tools/check_todo_write_kwargs.py]: "
        "BLOCKED: todo_write.py invoked with --priority (kwarg). Argparse will reject this."
    )
    # Compound command whose first token is git, so tools/ parse misses.
    cmd = "cd ~/proj && git rev-parse && python3 todo_write.py add 'x' --priority High"
    assert fs.derive_surface("Bash", cmd, err) == "todo_write.py"


def test_block_falls_back_to_check_script():
    err = (
        "PreToolUse:Bash hook error: [python3 /Users/x/tools/check_bare_python.py]: "
        "BLOCKED: bare `python` invocation detected."
    )
    cmd = "cd ~/proj && python tools/pipeline_staleness.py"
    # tools/pipeline_staleness.py is present and non-excluded -> step 2 wins first.
    assert fs.derive_surface("Bash", cmd, err) == "pipeline_staleness.py"


def test_block_check_script_when_no_tools_in_command():
    err = (
        "PreToolUse:Bash hook error: [python3 /Users/x/tools/check_bare_python.py]: "
        "BLOCKED: bare `python` invocation detected."
    )
    cmd = "python somescript.py"  # no tools/, no BLOCKED operand .py
    assert fs.derive_surface("Bash", cmd, err) == "check_bare_python.py"


# --- NEW: inline python -c attribution (was bash:cd Traceback) ---

def test_inline_c_attributes_to_imported_module():
    cmd = (
        "cd ~/.claude/skills/ss/scripts && PYTHONIOENCODING=utf-8 python3 -c "
        '"import ss_log_append as L; L.append_entry(event=1)"'
    )
    err = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "TypeError: append_entry() got an unexpected keyword argument 'event'"
    )
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_inline_c_prefers_real_pyfile_in_traceback():
    cmd = 'cd ~/x && python3 -c "import ss_log_append as L; L.mark_seen()"'
    err = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        '  File "/Users/x/skills/ss/scripts/ss_log_append.py", line 40, in mark_seen\n'
        "FileNotFoundError: [Errno 2] No such file"
    )
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_inline_c_stdlib_only_import_is_not_a_fake_surface():
    cmd = 'python3 -c "import os; print(1/0)"'
    err = "ZeroDivisionError: division by zero"
    # os is stdlib noise -> inline:os, not os.py
    assert fs.derive_surface("Bash", cmd, err) == "inline:os"


def test_inline_c_no_import():
    cmd = "python3 -c \"print(1/0)\""
    err = "ZeroDivisionError: division by zero"
    assert fs.derive_surface("Bash", cmd, err) == "inline-python"


# --- NEW: comma-separated import list prefers first non-stdlib (Defect B, 2026-06-05) ---

def test_inline_c_comma_import_prefers_non_stdlib():
    # Real 2026-06-05 shape: traceback has only <string>/<frozen> frames, and the
    # -c body leads with stdlib imports before the real module. Must NOT log inline:os.
    cmd = (
        "cd ~/x && PYTHONIOENCODING=utf-8 python3 -c "
        '"import os, glob, shutil, ss_log_append as s; s.mark_seen(1,2,3)"'
    )
    err = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        '  File "<frozen genericpath>", line 91, in getmtime\n'
        "FileNotFoundError: [Errno 2] No such file"
    )
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_inline_c_all_stdlib_comma_import_still_inline():
    cmd = 'python3 -c "import os, sys, json; print(1/0)"'
    err = "ZeroDivisionError: division by zero"
    assert fs.derive_surface("Bash", cmd, err) == "inline:os"


# --- NEW: python_invoked command-position detection (masked-failure support, 2026-06-05) ---

def test_python_invoked_at_start():
    assert fs.python_invoked('python3 -c "x"') is True


def test_python_invoked_after_env_and_cd():
    assert fs.python_invoked('cd ~/x && PYTHONIOENCODING=utf-8 python3 -c "x"') is True


def test_python_invoked_after_pipe():
    assert fs.python_invoked("cat f | python3 script.py") is True


def test_python_invoked_with_real_masking_pipe():
    # The exact 2026-06-05 shape, including 2>&1 (& triggers a split) and | head.
    cmd = 'cd ~/x && PYTHONIOENCODING=utf-8 python3 -c "import x" 2>&1 | head -20'
    assert fs.python_invoked(cmd) is True


def test_python_invoked_path_prefixed():
    assert fs.python_invoked("/usr/bin/python3 foo.py") is True


def test_python_invoked_false_for_grep_substring():
    assert fs.python_invoked("grep -n python3 memory/friction-log.md") is False


def test_python_invoked_false_for_string_mention():
    assert fs.python_invoked('echo "run python3 now" | head') is False


def test_python_invoked_false_for_empty():
    assert fs.python_invoked("") is False


# --- nature ---

def test_nature_prefers_json_message():
    err = '{"status": "error", "message": "Unknown flag: --due"}'
    assert fs.derive_nature(err, "auto") == "[auto] Unknown flag: --due"


def test_nature_tag_is_configurable():
    err = "Traceback (most recent call last):\nTypeError: boom"
    out = fs.derive_nature(err, "auto-stop")
    assert out.startswith("[auto-stop] ")
    assert "|" not in out


def test_nature_strips_pipes_and_newlines():
    err = "bad | thing\nsecond line"
    out = fs.derive_nature(err)
    assert "|" not in out and "\n" not in out


def test_nature_traceback_strips_harness_footer():
    # The harness appends "Shell cwd was reset ..." after a cd; the nature must be
    # the exception line, not the footer (2026-06-05 regression).
    err = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 3, in <module>\n'
        "TypeError: append_entry() got an unexpected keyword argument 'event'\n"
        "Shell cwd was reset to /Users/mag/Documents/Obsidian/30-projects/job-search"
    )
    out = fs.derive_nature(err, "auto-stop")
    assert out.startswith("[auto-stop] TypeError: append_entry()")
    assert "Shell cwd was reset" not in out


def test_nature_traceback_uses_exception_line_not_header():
    err = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "TypeError: append_entry() got an unexpected keyword argument 'event'"
    )
    out = fs.derive_nature(err, "auto")
    assert out.startswith("[auto] TypeError: append_entry()")
    assert "Traceback (most recent" not in out
