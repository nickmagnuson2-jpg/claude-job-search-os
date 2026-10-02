"""Tests for tools/check_guard_edit_approval.py — the guard-edit approval hook.

Content hook, so the false-positive surface is PATH SCOPE (tools/HOOK_AUTHORING.md).
The canonical trap for a content hook is judging its own fixtures; these tests pin
that `tests/` is excluded, or the suite could not run at all.

Origin: 2 fires of feedback_never_modify_guard_hook_to_unblock_self (2026-07-28,
2026-08-14), both "the fix is correct" and both the wrong sequence.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_guard_edit_approval.py"


def _run(path, tool="Edit", env=None):
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


# --- BLOCKS: guard infrastructure -------------------------------------------

@pytest.mark.parametrize("path", [
    "tools/check_todo_write_kwargs.py",          # the 2026-08-14 fire
    "tools/check_email_via_skill.py",            # the 2026-07-28 fire
    "tools/check_public_pii.py",
    "tools/hook_command_lint.py",                # shared strip logic; weakening it weakens every command hook
    ".claude/settings.json",                     # the wiring is guard infrastructure too
    ".claude/settings.local.json",
    "/Users/mag/Documents/Obsidian/30-projects/job-search/tools/check_bare_python.py",
])
def test_blocks_guard_edits(path):
    code, err = _run(path)
    assert code == 2, err
    assert "BLOCKED" in err


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_blocks_across_all_write_surfaces(tool):
    """Switching tool surface to dodge the matcher is the documented sibling bypass."""
    code, _ = _run("tools/check_public_pii.py", tool=tool)
    assert code == 2


def test_creating_a_brand_new_guard_is_also_blocked():
    """Deliberate. Building a guard is normal work, but Nick should know it is happening;
    the override makes it one keystroke rather than an argument."""
    code, _ = _run("tools/check_something_new.py", tool="Write")
    assert code == 2


def test_block_message_names_the_path_and_the_override():
    code, err = _run("tools/check_public_pii.py")
    assert code == 2
    assert "check_public_pii.py" in err
    assert "GUARD_EDIT_APPROVED=1" in err
    assert "WAIT for his yes" in err


def test_block_message_names_the_contract_changed_rationalization():
    """The 2026-08-14 fire's specific reasoning must be pre-refuted in the message,
    or the next session reconstructs it from scratch."""
    _, err = _run("tools/check_public_pii.py")
    assert "ask FASTER" in err


def test_block_message_gives_the_fp_logging_path():
    """PreToolUse blocks are invisible to the auto-logger — manual logging is the
    ONLY telemetry path for a false positive (HOOK_AUTHORING.md)."""
    _, err = _run("tools/check_public_pii.py")
    assert "friction_log.py append" in err


# --- CLEAN: the path-scope false-positive surface ---------------------------

@pytest.mark.parametrize("path", [
    "tests/scripts/test_check_guard_edit_approval.py",   # this very file
    "tests/scripts/test_check_todo_write_kwargs.py",     # a test is not a guard
    "tests/fixtures/tools/check_fake.py",
    "tools/HOOK_AUTHORING.md",                           # docs about hooks are not hooks
    "tools/todo_write.py",                               # an ordinary tool
    "tools/friction_log.py",
    "framework/frame-schema.yaml",
    "data/job-todos.md",
    "CLAUDE.md",
    ".claude/skills/apply/SKILL.md",                     # skills are not the hook wiring
])
def test_clean_paths_pass(path):
    code, err = _run(path)
    assert code == 0, f"false positive on {path}: {err}"


def test_its_own_tests_are_not_blocked():
    """THE canonical content-hook trap: a hook that judges its own fixtures makes the
    suite unrunnable. Origin check_prep_doc_format.py, 2026-08-12."""
    code, _ = _run("tests/scripts/test_check_guard_edit_approval.py")
    assert code == 0


def test_non_write_tools_pass():
    for tool in ("Read", "Grep", "Bash", "Glob"):
        code, _ = _run("tools/check_public_pii.py", tool=tool)
        assert code == 0, tool


# --- the override -----------------------------------------------------------

def test_override_allows_the_edit():
    import os
    env = {**os.environ, "GUARD_EDIT_APPROVED": "1"}
    code, _ = _run("tools/check_public_pii.py", env=env)
    assert code == 0


# --- hook libraries (Nick, 2026-10-01) ----------------------------------------

@pytest.mark.parametrize("path", ["tools/shell_tokens.py", "tools/hook_runtime.py"])
def test_hook_libraries_are_guarded(path):
    """A guard's decision lives in the libraries it parses with. shell_tokens.py was
    edited unapproved on 2026-10-01 because it was not on the list."""
    code, err = _run(path)
    assert code == 2, err


def test_every_tools_module_a_guard_imports_is_classified():
    """Adoption gate: a check_*.py importing a new tools module must put it in
    HOOK_LIBRARIES (guarded) or NOT_HOOK_LIBRARIES (with a reason). A new parsing
    library otherwise arrives unguarded, which is how shell_tokens.py did."""
    import re
    sys.path.insert(0, str(SCRIPT.parent))
    import check_guard_edit_approval as g
    tools = SCRIPT.parent
    seen, todo = set(), [p.stem for p in tools.glob("check_*.py")]
    while todo:
        mod = todo.pop()
        src = (tools / f"{mod}.py").read_text(encoding="utf-8")
        for name in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", src, re.M):
            if (tools / f"{name}.py").exists() and not name.startswith("check_") \
                    and name not in seen:
                seen.add(name)
                todo.append(name)
    unclassified = seen - set(g.HOOK_LIBRARIES) - set(g.NOT_HOOK_LIBRARIES)
    assert not unclassified, f"classify in check_guard_edit_approval.py: {sorted(unclassified)}"
    assert all(g.NOT_HOOK_LIBRARIES.values()), "every exemption needs a reason"


# --- Bash writes (Nick, 2026-10-01) --------------------------------------------

def _bash(command, env=None):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


@pytest.mark.parametrize("cmd", [
    "echo x >> tools/check_public_pii.py",
    "sed -i '' 's/a/b/' tools/shell_tokens.py",
    "cd tools && sed -i '' 's/a/b/' shell_tokens.py",
    "cat new.py | tee tools/hook_runtime.py",
    "cp /tmp/x.py tools/check_public_pii.py",
    "mv /tmp/x.py tools/shell_tokens.py",
    "rm tools/check_heredoc_quoting.py",
    "git checkout HEAD -- tools/shell_tokens.py",
    "perl -pi -e 's/a/b/' tools/check_public_pii.py",
    # the 2026-10-01 bypass: an inline interpreter rewriting a guard
    "python3 - <<'PY'\nfrom pathlib import Path\np = Path('tools/shell_tokens.py')\n"
    "p.write_text(p.read_text().replace('a', 'b'))\nPY",
    "python3 -c \"open('tools/check_public_pii.py', 'w').write('')\"",
    "echo '{}' > .claude/settings.json",
])
def test_bash_writes_to_a_guard_are_blocked(cmd):
    code, err = _bash(cmd)
    assert code == 2, cmd
    assert "BLOCKED" in err


def test_a_script_file_that_rewrites_a_guard_is_blocked(tmp_path):
    """The exact 2026-10-01 path: a scratchpad patch script run by python3."""
    script = tmp_path / "patch.py"
    script.write_text("from pathlib import Path\n"
                      "p = Path('/repo/tools/check_public_pii.py')\n"
                      "p.write_text(p.read_text())\n")
    code, _ = _bash(f"python3 {script}")
    assert code == 2


@pytest.mark.parametrize("cmd", [
    "cat tools/shell_tokens.py",
    "sed -n 1,40p tools/check_public_pii.py",
    "grep -n def tools/shell_tokens.py > /tmp/out.txt",
    "python3 -m pytest tests/scripts/test_check_public_pii.py",
    "python3 tools/check_public_pii.py --scan docs/x.md",
    "python3 tools/mutation_check.py tools/check_public_pii.py",
    "git diff tools/shell_tokens.py",
    "cp tools/shell_tokens.py /tmp/backup.py",
    "python3 -c \"print(open('tools/shell_tokens.py').read())\"",
    "echo hi > docs/notes.md",
])
def test_bash_reads_of_a_guard_pass(cmd):
    code, err = _bash(cmd)
    assert code == 0, (cmd, err)
    assert err == "", err            # a clean command prints nothing


@pytest.mark.parametrize("cmd", [
    "GUARD_EDIT_APPROVED=1 sed -i '' 's/a/b/' tools/shell_tokens.py",
    "GUARD_EDIT_APPROVED=1 python3 /tmp/patch.py && echo x >> tools/check_public_pii.py",
    "export GUARD_EDIT_APPROVED=1; rm tools/check_heredoc_quoting.py",
])
def test_bash_override_in_the_command_allows(cmd):
    code, err = _bash(cmd)
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    # Codex review of 88db514, F1: only =1 approves
    "GUARD_EDIT_APPROVED=0 rm tools/check_heredoc_quoting.py",
    "GUARD_EDIT_APPROVED=no rm tools/check_heredoc_quoting.py",
    # Grok review of 88db514, F7: the text alone is not an assignment
    "echo GUARD_EDIT_APPROVED=1; rm tools/check_heredoc_quoting.py",
    "cat > notes.md <<'EOF'\nGUARD_EDIT_APPROVED=1\nEOF\nrm tools/check_heredoc_quoting.py",
    # Grok F2: mv changes its SOURCE too
    "mv tools/shell_tokens.py /tmp/x.py",
    "mv tools/check_public_pii.py tools/hook_runtime.py /tmp/",
    # Codex F4: -t names the destination directory
    "cp -t tools /tmp/check_public_pii.py",
    "install -t tools/ /tmp/shell_tokens.py",
    "cp --target-directory=tools /tmp/hook_runtime.py",
])
def test_review_of_88db514_bash_forms_are_blocked(cmd):
    code, err = _bash(cmd)
    assert code == 2, cmd


@pytest.mark.parametrize("cmd", [
    "cp -t /tmp/backup tools/check_public_pii.py",
    "mv /tmp/a.py /tmp/b.py",
    "export GUARD_EDIT_APPROVED=1 && rm tools/check_heredoc_quoting.py",
])
def test_review_of_88db514_allowed_forms(cmd):
    code, err = _bash(cmd)
    assert code == 0, (cmd, err)


def test_a_shell_script_file_that_edits_a_guard_is_blocked(tmp_path):
    """Grok review of 88db514, F3: the scratchpad incident with bash instead of python."""
    guard = SCRIPT.parent / "shell_tokens.py"
    (tmp_path / "patch.sh").write_text(f"sed -i '' 's/a/b/' {guard}\n")
    assert _bash_in(tmp_path, "bash patch.sh") == 2
    (tmp_path / "ok.sh").write_text(f"sed -n 1p {guard}\n")
    assert _bash_in(tmp_path, "bash ok.sh") == 0


@pytest.mark.parametrize("cmd", [
    # another checkout's copy of a guard is not this repo's guard
    "W=/tmp/wt; cp tools/check_draft_voice.py $W/tools/check_draft_voice.py",
    "cp tools/shell_tokens.py /tmp/wt/tools/shell_tokens.py",
    "sed -i '' 's/a/b/' /tmp/wt/tools/check_public_pii.py",
])
def test_another_checkouts_guard_copy_is_not_guarded(cmd):
    code, err = _bash(cmd)
    assert code == 0, (cmd, err)


def test_relative_guard_paths_resolve_against_the_command_cwd(tmp_path):
    """`sed -i ... tools/shell_tokens.py` run in a scratch checkout edits that copy."""
    assert _bash_in(tmp_path, "sed -i '' 's/a/b/' tools/shell_tokens.py") == 0
    assert _bash_in(SCRIPT.parent.parent, "sed -i '' 's/a/b/' tools/shell_tokens.py") == 2


def test_an_unresolved_variable_path_is_still_blocked(tmp_path):
    """Judged as written even from a cwd outside the repo, where joining it to the
    cwd would otherwise place it out of scope."""
    assert _bash_in(tmp_path, "cp /tmp/x.py $SOMEWHERE_UNSET/tools/check_public_pii.py") == 2


@pytest.mark.parametrize("cmd", [
    "cp -ttools /tmp/check_public_pii.py",                 # attached -tDIR
    "cp /tmp/check_public_pii.py tools/ -v",               # a trailing option
])
def test_more_copy_forms_are_blocked(cmd):
    code, _ = _bash(cmd)
    assert code == 2, cmd


def test_approval_after_another_prefix_assignment_counts():
    code, err = _bash("X=1 GUARD_EDIT_APPROVED=1 rm tools/check_heredoc_quoting.py")
    assert code == 0, err


def test_shell_script_file_forms(tmp_path):
    guard = SCRIPT.parent / "shell_tokens.py"
    (tmp_path / "patch.sh").write_text(f"sed -i '' 's/a/b/' {guard}\n")
    assert _bash_in(tmp_path, "bash -e patch.sh") == 2
    assert _bash_in(tmp_path, "bash -o pipefail patch.sh") == 2
    # -c runs its string; patch.sh is only $0
    assert _bash_in(tmp_path, "bash -c 'echo hi' patch.sh") == 0
    # -s reads stdin; patch.sh is $1
    assert _bash_in(tmp_path, "bash -s patch.sh < /dev/null") == 0
    # only a shell runs a file: cat prints it
    assert _bash_in(tmp_path, "cat patch.sh") == 0


@pytest.mark.parametrize("cmd", [
    # Grok review of 2ffecb1, F3: a variable-held command string
    "c='rm tools/shell_tokens.py'; eval \"$c\"",
    "c='rm tools/shell_tokens.py'; echo \"$c\" | sh",
    # Grok F1: git global options before the subcommand
    "git --no-pager checkout HEAD -- tools/shell_tokens.py",
    "git -C . restore tools/shell_tokens.py",
    "git -c core.pager=cat checkout -- tools/check_public_pii.py",
])
def test_review_of_2ffecb1_forms_are_blocked(cmd):
    code, _ = _bash(cmd)
    assert code == 2, cmd


def test_sourcing_a_script_that_edits_a_guard_is_blocked(tmp_path):
    """Grok review of 2ffecb1, F4."""
    guard = SCRIPT.parent / "shell_tokens.py"
    (tmp_path / "patch.sh").write_text(f"sed -i '' 's/a/b/' {guard}\n")
    assert _bash_in(tmp_path, "source patch.sh") == 2
    assert _bash_in(tmp_path, ". ./patch.sh") == 2
    (tmp_path / "env.sh").write_text("export X=1\n")
    assert _bash_in(tmp_path, "source env.sh") == 0


def test_git_reads_of_a_guard_pass():
    for cmd in ("git -C . diff tools/shell_tokens.py", "git --no-pager log -- tools/shell_tokens.py"):
        code, err = _bash(cmd)
        assert code == 0, (cmd, err)


def test_shell_script_files_contract():
    g = _hook_module()
    assert g._shell_script_files(["-c", "echo hi", "patch.sh"]) == []
    assert g._shell_script_files(["-s", "a"]) == []
    assert g._shell_script_files(["-e", "--norc", "x.sh", "arg"]) == ["x.sh", "arg"]


def _bash_in(cwd, command):
    payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                          "tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True, cwd=str(cwd))
    return r.returncode


_REWRITE = ("from pathlib import Path\np = Path('tools/check_public_pii.py')\n"
            "p.write_text(p.read_text())\n")


def test_interpreter_options_before_the_script_are_skipped(tmp_path):
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert _bash_in(tmp_path, "python3 -u patch.py") == 2


def test_dash_m_names_a_module_not_a_script(tmp_path):
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert _bash_in(tmp_path, "python3 -m patch.py") == 0


def test_a_heredoc_to_a_non_interpreter_is_data():
    """Writing a note that QUOTES a patch script is not running it."""
    code, err = _bash("cat > notes.md <<'EOF'\n" + _REWRITE + "EOF")
    assert code == 0, err


def test_a_non_string_command_fails_open():
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 0


def _hook_module():
    sys.path.insert(0, str(SCRIPT.parent))
    import check_guard_edit_approval as g
    return g


def test_scripts_under_tools_are_not_read(tmp_path, monkeypatch):
    """mutation_check.py rewrites guards by design; repo tools are not judged."""
    g = _hook_module()
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert g.bash_guard_writes(f"python3 {tmp_path}/patch.py", str(tmp_path))
    monkeypatch.setattr(g, "_TOOLS_DIR", str(tmp_path))
    assert g.bash_guard_writes(f"python3 {tmp_path}/patch.py", str(tmp_path)) == []


def test_read_script_returns_text_or_empty(tmp_path):
    g = _hook_module()
    assert g._read_script("missing.py", str(tmp_path)) == ""
    f = tmp_path / "locked.py"
    f.write_text(_REWRITE)
    assert g._read_script("locked.py", str(tmp_path)) == _REWRITE
    f.chmod(0)
    try:
        assert g._read_script("locked.py", str(tmp_path)) == ""
    finally:
        f.chmod(0o644)


def test_nesting_is_capped():
    g = _hook_module()
    assert g.bash_guard_writes("rm tools/check_x.py", ".")
    assert g.bash_guard_writes("rm tools/check_x.py", ".", _depth=9) == []


def test_an_analysis_error_fails_open(monkeypatch, capsys):
    g = _hook_module()

    class P:
        ok, tool_name = True, "Bash"
        data = {"tool_input": {"command": "rm tools/check_x.py"}}
    monkeypatch.setattr(g, "read_payload", lambda: P())
    monkeypatch.delenv("GUARD_EDIT_APPROVED", raising=False)

    def boom(*a, **k):
        raise ValueError("parse")
    monkeypatch.setattr(g, "bash_guard_writes", boom)
    with pytest.raises(SystemExit) as e:
        g.main()
    assert e.value.code == 0
    assert "allowing" in capsys.readouterr().err


# --- fail-open --------------------------------------------------------------

def test_malformed_json_fails_open():
    """A guard that crashes blocks all work. Fail open on garbage."""
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_missing_file_path_fails_open():
    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_empty_stdin_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="",
                       capture_output=True, text=True)
    assert r.returncode == 0
