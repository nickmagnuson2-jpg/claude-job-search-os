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


# --- NEW: python heredoc attribution (2026-07-08 friction-log audit) -------
# `python3 <<EOF ... EOF` was falling through every check to the generic
# bash:<first-token> fallback (e.g. "bash:cd"), and the short/truncated
# traceback text these tend to produce ("File \"<string>\", line 4" with no
# exception line) is generic enough that unrelated heredoc scripts collide at
# Jaccard 1.0 in dedup — silently merging distinct one-off failures into one
# wrongly-promoted row.

def test_heredoc_attributes_to_imported_module():
    cmd = "cd /repo && python3 <<'EOF'\nimport ss_log_append\nss_log_append.mark_seen('x')\nEOF"
    err = 'File "<string>", line 2\nAttributeError: nope'
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_heredoc_unquoted_delimiter():
    cmd = "cd /repo && python3 <<EOF\nimport pipeline_staleness\nEOF"
    err = "File \"<string>\", line 1\nImportError: bad"
    assert fs.derive_surface("Bash", cmd, err) == "pipeline_staleness.py"


def test_heredoc_prefers_real_pyfile_in_traceback():
    cmd = "python3 <<'EOF'\nimport ss_log_append\nss_log_append.mark_seen('x')\nEOF"
    err = (
        'File "<string>", line 2, in <module>\n'
        '  File "/Users/x/tools/ss_log_append.py", line 40, in mark_seen\n'
        "FileNotFoundError: [Errno 2] No such file"
    )
    assert fs.derive_surface("Bash", cmd, err) == "ss_log_append.py"


def test_heredoc_no_import_falls_back_to_heredoc_label():
    # Truncated/generic traceback with no importable module and no real .py
    # frame — must NOT collide with the -c fallback label (distinguishable
    # so a -c failure and a heredoc failure don't merge into one row either).
    cmd = "cd /repo && python3 <<'EOF'\nx = 1/0\nEOF"
    err = 'File "<string>", line 1'
    assert fs.derive_surface("Bash", cmd, err) == "inline-python-heredoc"


def test_heredoc_stdlib_only_import_is_not_a_fake_surface():
    cmd = "python3 <<'EOF'\nimport os\nprint(1/0)\nEOF"
    err = "ZeroDivisionError: division by zero"
    assert fs.derive_surface("Bash", cmd, err) == "inline:os"


# --- NEW: heredoc unbounded-fallback hardening (code review, 2026-07-08) ---
# _heredoc_body() no longer sweeps the unbounded command remainder into "the
# body" when no closing delimiter is found — it returns "" (safe generic
# fallback) instead of scanning unrelated later text for imports.

def test_heredoc_unterminated_falls_back_to_generic_label_not_unrelated_import():
    # No closing "EOF" line anywhere in the captured text; a later unrelated
    # "import numpy" must NOT get picked up as the surface.
    cmd = (
        "python3 <<EOF\n"
        "import json\n"
        'data = json.loads(open("foo.json").read())\n'
        "print(data)\n"
        "\n"
        "import numpy\n"
        "print(numpy.__version__)"
    )
    err = 'FileNotFoundError: foo.json'
    assert fs.derive_surface("Bash", cmd, err) == "inline-python-heredoc"


def test_heredoc_body_isolated_stops_at_unterminated_point():
    cmd = (
        "python3 <<EOF\n"
        "import json\n"
        "\n"
        "import numpy\n"
    )
    assert fs._heredoc_body(cmd) == ""


def test_heredoc_closing_delimiter_with_trailing_command_substitution_syntax():
    # "EOF)" closes a heredoc feeding a $(...) command substitution — a
    # common idiom (x=$(python3 <<EOF ... EOF)). The closing-line regex must
    # recognize this, not just a bare "EOF" alone on its line.
    cmd = (
        'x=$(python3 <<EOF\n'
        "import json\n"
        'data = json.loads(open("foo.json").read())\n'
        "EOF)"
    )
    body = fs._heredoc_body(cmd)
    assert "json" in body
    assert "EOF" not in body


def test_plain_cd_without_python_still_falls_back_to_bash_cd():
    # Sanity: the heredoc path must not fire on unrelated cd commands.
    cmd = "cd /repo && ls"
    err = "ls: cannot access 'x': No such file or directory"
    assert fs.derive_surface("Bash", cmd, err) == "bash:cd"


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


# --- looks_like_real_error (2026-07-08 masked-benign PostToolUseFailure guard) ---
# Gates the exit=? fallback: a chained/piped bash command can report a real
# nonzero exit driven by a benign downstream stage, while the only text we can
# extract is an EARLIER stage's successful stdout (no error signal in it).

def test_masked_benign_git_status_stdout():
    assert fs.looks_like_real_error("On branch main") is False


def test_masked_benign_shebang_line():
    assert fs.looks_like_real_error("#!/usr/bin/env python3") is False


def test_masked_benign_markdown_header():
    assert fs.looks_like_real_error("# Job Search To-Dos") is False


def test_real_error_traceback():
    assert fs.looks_like_real_error("Traceback (most recent call last):") is True


def test_real_error_pascalcase_exception():
    assert fs.looks_like_real_error(
        "FileNotFoundError: [Errno 2] No such file or directory"
    ) is True


def test_real_error_command_not_found():
    assert fs.looks_like_real_error("bash: python: command not found") is True


def test_real_error_permission_denied():
    assert fs.looks_like_real_error("Permission denied") is True


def test_real_error_gitignore_message():
    assert fs.looks_like_real_error(
        "ignored by one of your .gitignore files"
    ) is True


def test_empty_and_none_text_not_real_error():
    assert fs.looks_like_real_error("") is False
    assert fs.looks_like_real_error(None) is False


def test_substring_terror_does_not_false_match():
    # Lowercase "error" as a substring of an unrelated word must not match —
    # only PascalCase Python exception names or the bare word "error".
    assert fs.looks_like_real_error("Terror management theory") is False


def test_usage_line_is_a_real_error():
    # Regression: "usage:" always ends in a non-word char (the colon), so a
    # trailing \b right after it can never be satisfied (colon-to-space and
    # colon-to-end-of-string are not word-boundary transitions). This must be
    # its own alternative with only a leading \b, not folded into the
    # shared-trailing-\b group.
    assert fs.looks_like_real_error("usage: todo_write.py [-h]") is True
    assert fs.looks_like_real_error("usage:") is True
