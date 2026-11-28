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
    # an absolute path into THIS checkout, derived so a worktree tests its own tree
    str(Path(__file__).resolve().parents[2] / "tools" / "check_bare_python.py"),
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
                      f"p = Path({str(SCRIPT.parents[1] / 'tools' / 'check_public_pii.py')!r})\n"
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


@pytest.mark.parametrize("cmd", [
    # Codex + Grok review of b9cc6a5: globs, directories and braces reach guards
    "rm tools/check_*.py",
    "rm -f tools/shell_tok*",
    "rm -rf tools",
    "rm -rf tools/",
    "mv tools/shell_tokens.py{,.bak}",
    "git restore .",
    "git checkout -- .",
    "git checkout HEAD -- tools",
    # Grok F4: an option cluster ending in c is -c
    "python3 -uc \"open('tools/check_public_pii.py','w').write('')\"",
])
def test_review_of_b9cc6a5_forms_are_blocked(cmd):
    code, _ = _bash(cmd)
    assert code == 2, cmd


@pytest.mark.parametrize("cmd", [
    "rm -rf tools/__pycache__/_pii_old_tmp*",     # 25 of these in history
    "rm -rf tools/__pycache__",
    "rm tools/*.bak",
    "git restore docs/",
    # `{}` is find's placeholder, not brace expansion (it named the repo root)
    "find tools tests -name __pycache__ -type d -exec rm -rf {} +",
    "mv docs/a.md{,.bak}",
    # whole-tree git operations restore COMMITTED content: out of this gate's scope
    "git stash",
    "git checkout main",
    "git reset --hard",
])
def test_review_of_b9cc6a5_allowed_forms(cmd):
    code, err = _bash(cmd)
    assert code == 0, (cmd, err)


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


# This repo's own guard, by absolute path: relative to a temp cwd it would name another
# checkout's copy, which is not a guard (the Python write analysis resolves it, 2026-10-21).
_GUARD_ABS = str(SCRIPT.parents[1] / "tools" / "check_public_pii.py")
_REWRITE = (f"from pathlib import Path\np = Path({_GUARD_ABS!r})\n"
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


# ---------------------------------------------------------------------------
# Python scripts: the write targets decide (friction ledger, 3 fires, 2026-10-21). A
# script that names a guard and writes ELSEWHERE passes; one whose write may reach a
# guard, or cannot be followed, still blocks. Commands run from the repo root.
# ---------------------------------------------------------------------------

_G = "tools/check_public_pii.py"
_PY_PASSES = {
    # control for feed_a_read_only_command_on_stdin: the same script, nothing fed
    "run_a_read_only_command_with_nothing_fed":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['cat', 'x.txt'])\nopen('{_G}')\nopen('n', 'w')\nPY",
    # the 2026-10-21 fire: hash a guard, write a JSON report to a scratch path from argv
    "hash_guard_write_report_from_argv":
        f"SP=/tmp/scratch; python3 - \"$SP/replay.json\" <<'PY'\nimport hashlib, json, sys\n"
        f"d = hashlib.sha256(open('{_G}', 'rb').read()).hexdigest()\n"
        "json.dump({'h': d}, open(sys.argv[1], 'w'))\nPY",
    # the 2026-10-14 fire: run a guard as a subprocess, write a marker file
    "run_guard_then_write_marker":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3', '{_G}'], input='{{}}', text=True)\n"
        "open('tools/.pending-draft.txt', 'w').write('x')\nPY",
    "run_this_repos_guard_then_write_elsewhere":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3', '{_G}'])\nopen('/tmp/o.txt', 'w')\nPY",
    # an edit script for a non-guard file whose inserted text names a guard path
    "edit_other_file_whose_text_names_a_guard":
        f"python3 - <<'PY'\np = 'notes/replay.py'\ns = open(p).read()\n"
        f"s = s.replace('X', \"open('{_G}').read()\")\nopen(p, 'w').write(s)\nPY",
    # a scratch copy whose directory is a literal outside the repo
    "write_a_scratch_copy_by_literal_dir":
        f"python3 - <<'PY'\nimport os, shutil\nW = '/private/tmp/wt'\n"
        f"shutil.copy('{_G}', os.path.join(W, '{_G}'))\nopen(os.path.join(W, '{_G}'), 'w').write('x')\nPY",
    "read_mode_only": f"python3 -c \"print(open('{_G}', 'r').read()); open('x.txt', 'a').write('1')\"",
    "path_write_text_elsewhere":
        f"python3 - <<'PY'\nfrom pathlib import Path\nt = Path('{_G}').read_text()\n"
        "Path('notes', 'copy.md').write_text(t)\nPY",
}
_PY_BLOCKS = {
    # a read-only program fed on stdin may be fed a program: only this repo's own guard
    # may take input (kills _run_targets::IF_FALSE::443ea2ec, 2026-10-30)
    "feed_a_read_only_command_on_stdin":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['cat'], input='x', text=True)\nopen('{_G}')\nopen('n', 'w')\nPY",
    "write_guard_by_join": f"python3 - <<'PY'\nimport os\nopen(os.path.join('tools', 'check_public_pii.py'), 'w').write('{_G}')\nPY",
    "write_target_from_environ":
        f"python3 - <<'PY'\nimport os\nopen(os.environ['T'], 'w').write('{_G}')\nPY",
    "unknown_dir_guard_name":
        f"python3 - <<'PY'\nimport os\nopen(os.getcwd() + '/{_G}', 'w').write('x')\nPY",
    "guard_name_from_unknown_argv": f"python3 - <<'PY'\nimport sys\nopen(sys.argv[1], 'w').write('{_G}')\nPY",
    "aliased_write_function":
        f"python3 - <<'PY'\nimport os\nw = os.replace\nw('/tmp/x', '{_G}')\nPY",
    "from_import_alias": f"python3 - <<'PY'\nfrom shutil import copy as c\nc('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "exec_reaches_code": f"python3 - <<'PY'\nexec(\"open('{_G}', 'w').write('')\")\nopen('n', 'w')\nPY",
    "getattr_reaches_a_name": f"python3 - <<'PY'\nimport os\ngetattr(os, 'replace')('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    # The guard path must appear as a whole literal for the script to be examined at
    # all (the pre-filter, unchanged); here it does, so the shell command is followed.
    "subprocess_shell_edits_a_guard":
        f"python3 - <<'PY'\nimport subprocess\nG = '{_G}'\nsubprocess.run(\"sed -i '' 's/a/b/' \" + G, shell=True)\nopen('n', 'w')\nPY",
    "subprocess_list_edits_a_guard":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['rm', '{_G}'])\nopen('n', 'w')\nPY",
    "subprocess_unknown_command":
        f"python3 - <<'PY'\nimport subprocess, os\nsubprocess.run(os.environ['C'], shell=True)\nopen('{_G}')\nopen('n', 'w')\nPY",
    "path_rename_onto_a_guard":
        f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/x').rename('{_G}')\nPY",
    "path_open_for_write": f"python3 - <<'PY'\nfrom pathlib import Path\nPath('{_G}').open('w').write('x')\nPY",
    "loop_variable_target":
        f"python3 - <<'PY'\nfor p in ['{_G}']:\n    open(p, 'w').write('x')\nPY",
    "unparseable": f"python3 - <<'PY'\nopen('{_G}', 'w').write(\nPY",
    "mode_not_a_literal": f"python3 - <<'PY'\nm = input()\nopen('{_G}', m).write('x')\nPY",
    "directory_holding_guards_removed":
        f"python3 - <<'PY'\nimport shutil\nshutil.rmtree('tools')\nprint('{_G}')\nopen('n', 'w')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_PASSES))
def test_python_script_writing_elsewhere_passes(name):
    code, err = _bash(_PY_PASSES[name])
    assert code == 0, (name, err)


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS))
def test_python_script_that_may_write_a_guard_blocks(name):
    code, _ = _bash(_PY_BLOCKS[name])
    assert code == 2, name


def test_non_python_interpreters_keep_the_heuristic():
    assert _bash(f"node -e \"require('fs').writeFileSync('/tmp/x', require('fs').readFileSync('{_G}'))\"")[0] == 2


def test_may_be_guard_contract():
    g = _hook_module()
    U = g._UNKNOWN
    root = g._REPO_ROOT
    assert g._may_be_guard(g._GUARD_HIT, root) is True
    assert g._may_be_guard("guard", root) is False        # a file named guard is a file
    assert g._may_be_guard("", root) is False
    assert g._may_be_guard(f"{root}/tools/check_x.py", root) is True
    # An unknown part may be absolute or hold '..', so a known directory proves nothing
    # (guard review round 1, Fable F1 / Grok F2): a guard-shaped name blocks.
    assert g._may_be_guard("/private/tmp/w/" + U + "/tools/check_x.py", root) is True
    assert g._may_be_guard(U + "/tools/check_x.py", root) is True
    assert g._may_be_guard(U + "/notes/report.json", root) is False
    assert g._may_be_guard(U + "/report" + U + ".md", root) is True     # unknown name part
    assert g._may_be_guard(U, root) is True
    assert g._may_be_guard(U + "/tools", root) is True
    assert g._may_be_guard("/private/tmp/../" + U + "/x.md", root) is False


# Second pass (own probes, 2026-10-21): the analysis applies only where its list of
# writes is complete, so a script reaching anything outside it keeps the heuristic.
_PY_BLOCKS_2 = {
    "module_outside_the_safe_list": f"python3 - <<'PY'\nimport io\nio.FileIO('{_G}', 'w')\nopen('n', 'w')\nPY",
    "fileinput_in_place":
        f"python3 - <<'PY'\nimport fileinput\nfor l in fileinput.input('{_G}', inplace=True):\n    print(l)\nopen('n', 'w')\nPY",
    "star_import": f"python3 - <<'PY'\nfrom os import *\nreplace('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "relative_import": f"python3 - <<'PY'\nfrom . import x\nprint('{_G}')\nopen('n', 'w')\nPY",
    "sys_modules": f"python3 - <<'PY'\nimport sys\nsys.modules['os'].replace('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "dunder_attribute": f"python3 - <<'PY'\nimport os\nos.__dict__['replace']('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "module_as_a_value": f"python3 - <<'PY'\nimport os\no = os\no.replace('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "module_passed_to_a_function":
        f"python3 - <<'PY'\nimport os\ndef f(m):\n    m.replace('/tmp/x', '{_G}')\nf(os)\nopen('n', 'w')\nPY",
    # S2c (2026-11-25): a chdir to ONE known absolute path is followed now (see
    # _PY_CHDIR_*); a chdir it cannot place still blocks.
    "working_directory_changed":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir(input())\nopen('n', 'w')\nPY",
    "process_spawned": f"python3 - <<'PY'\nimport os\nos.posix_spawn('/bin/rm', ['rm', '{_G}'], {{}})\nopen('n', 'w')\nPY",
    # The two below also write elsewhere, so the pre-filter (unchanged) examines them.
    "path_copy_onto_a_guard": f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/x').copy('{_G}')\nopen('n', 'w')\nPY",
    "archive_unpacked_into_tools":
        f"python3 - <<'PY'\nimport shutil\nprint('{_G}')\nshutil.unpack_archive('/tmp/a.zip', 'tools')\nopen('n', 'w')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS_2))
def test_python_script_outside_the_analysis_blocks(name):
    code, _ = _bash(_PY_BLOCKS_2[name])
    assert code == 2, name


def test_dict_copy_with_no_argument_is_not_a_path_move():
    code, err = _bash(f"python3 - <<'PY'\nd = {{'g': '{_G}'}}\ne = d.copy()\nopen('/tmp/o.json', 'w').write(str(e))\nPY")
    assert code == 0, err


# Guard review round 1 (Fable F1-F17, Grok F1-F6), each as a program the analysis must
# block, on top of a decoy write so the pre-filter examines it.
_PY_BLOCKS_3 = {
    "unknown_absolute_tail":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nopen(os.path.join('/private/tmp/wt', os.environ['X']), 'w').write('x')\nPY",
    "open_mode_in_kwargs": f"python3 - <<'PY'\nopen('{_G}', **dict(mode='w')).write('x')\nPY",
    "open_args_starred": f"python3 - <<'PY'\nopen(*['{_G}', 'w']).write('x')\nopen('n', 'w')\nPY",
    "rename_by_keyword": f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/x').rename(target='{_G}')\nopen('n', 'w')\nPY",
    "decoy_function_sharing_a_parameter_name":
        f"python3 - <<'PY'\ndef g(p):\n    pass\ng('/tmp/x')\ndef f(p):\n    open(p, 'w').write('x')\nf('{_G}')\nPY",
    "function_called_through_an_alias":
        f"python3 - <<'PY'\ndef f(p):\n    open(p, 'w')\nf('/tmp/x')\nh = f\nh('{_G}')\nPY",
    "decorated_function":
        f"python3 - <<'PY'\nimport functools\n@functools.lru_cache\ndef f(p):\n    open(p, 'w')\nf('/tmp/x')\nprint('{_G}')\nPY",
    "parameter_reassigned_inside":
        f"python3 - <<'PY'\ndef f(p):\n    p = '{_G}'\n    open(p, 'w')\nf('/tmp/x')\nPY",
    "argv_rewritten": f"python3 - /tmp/x <<'PY'\nimport sys\nsys.argv[1] = '{_G}'\nopen(sys.argv[1], 'w')\nPY",
    "argv_from_a_command_substitution":
        f"python3 - \"$(printf %s {_G})\" <<'PY'\nimport sys\nprint('{_G}')\nopen(sys.argv[1], 'w')\nPY",
    "hard_link_then_write":
        f"python3 - <<'PY'\nimport os\nos.link('{_G}', '/tmp/h')\nopen('/tmp/h', 'w').write('x')\nPY",
    "path_hardlink_then_write":
        f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/h').hardlink_to('{_G}')\nopen('/tmp/h', 'w')\nPY",
    "subprocess_with_cwd":
        "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['sed', '-i', '', 's/a/b/', './check_public_pii.py'], cwd='tools')\n"
        f"print('{_G}')\nopen('n', 'w')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS_3))
def test_guard_review_round_1_forms_block(name):
    code, _ = _bash(_PY_BLOCKS_3[name])
    assert code == 2, name


def test_interpreter_value_options_are_not_the_script(tmp_path):
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert _bash_in(tmp_path, "python3 -W ignore patch.py") == 2
    assert _bash_in(tmp_path, "python3 -X dev patch.py") == 2


def test_a_function_parameter_is_unknown_so_it_blocks():
    """Parameters are not folded (measured unused by every replayed false positive, and
    the source of the decoy/alias/decorator holes in review round 1)."""
    code, _ = _bash(f"python3 - <<'PY'\ndef f(p):\n    open(p, 'w').write('{_G}')\nf('/tmp/a.json')\nPY")
    assert code == 2


def test_a_script_named_like_a_guard_elsewhere_is_read_not_trusted(tmp_path):
    """A subprocess running `.../check_x.py` outside this repo is not 'running a guard':
    the command is scanned like the Bash form, which reads the script it runs."""
    evil = tmp_path / "check_public_pii.py"
    evil.write_text(_REWRITE)
    code, _ = _bash(f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3', '{evil}'])\n"
                    f"print('{_G}')\nopen('n', 'w')\nPY")
    assert code == 2


# Guard review round 2 (Fable F1-F3, Grok F1-F5): each closed form, on top of a write
# the pre-filter sees.
_PY_BLOCKS_4 = {
    "builtins_dunder_open": f"python3 - <<'PY'\n__builtins__.open('{_G}', 'w').write('x')\nPY",
    "builtins_dunder_exec":
        f"python3 - <<'PY'\n__builtins__.exec(\"open('{_G}', 'w').write('x')\")\nopen('n', 'w')\nPY",
    "module_through_a_module": f"python3 - <<'PY'\nimport shutil\nshutil.os.replace('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "chdir_through_a_module":
        "python3 - <<'PY'\nimport shutil\nshutil.os.chdir('tools')\n"
        f"open('./check_public_pii.py', 'w')\nprint('{_G}')\nPY",
    "shell_string_with_cd":
        "python3 - <<'PY'\nimport subprocess\nsubprocess.run('cd tools && echo x > ./check_public_pii.py', shell=True)\n"
        f"print('{_G}')\nopen('n', 'w')\nPY",
    "child_fed_a_program_on_stdin":
        "python3 - <<'PY'\nimport subprocess\n"
        f"subprocess.run(['python3', '-'], input=\"open('{_G}', 'w').write('x')\", text=True)\nopen('n', 'w')\nPY",
    "argparse_file_type": f"python3 - <<'PY'\nimport argparse\nargparse.FileType('w')('{_G}')\nopen('n', 'w')\nPY",
    "tempfile_in_tools":
        f"python3 - <<'PY'\nimport tempfile\nprint('{_G}')\ntempfile.mkstemp(dir='tools', prefix='check_x', suffix='.py')\nopen('n', 'w')\nPY",
    "unquoted_heredoc_expands_a_variable":
        f"T={_G}; python3 - <<PY\nprint('{_G}')\nopen('$T', 'w').write('x')\nPY",
    "dash_c_expands_a_variable":
        f"T={_G}; python3 -c \"print('{_G}'); open('$T', 'w').write('x')\"",
}


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS_4))
def test_guard_review_round_2_forms_block(name):
    code, _ = _bash(_PY_BLOCKS_4[name])
    assert code == 2, name


def test_a_quoted_heredoc_with_a_dollar_is_still_analysed():
    """A regex anchor in a quoted heredoc is Python text, not a shell expansion."""
    code, err = _bash(f"python3 - <<'PY'\nimport re\nt = open('{_G}').read()\n"
                      "open('/tmp/o.txt', 'w').write(re.sub(r'\\s+$', '', t))\nPY")
    assert code == 0, err


# Guard review round 3 (confirmation, Grok F1-F3, Fable F1-F5): ordinary edits the old
# write regex never reached, Bash-level cd, per-heredoc quoting, python option parsing.
# Forms the old pre-filter does not examine on their own (see NOT COVERED in the hook)
# carry an ordinary write so the analysis is reached.
_PY_BLOCKS_5 = {
    "subprocess_rm": f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['rm', '{_G}'])\nopen('n', 'w')\nPY",
    "path_replace": f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/x').replace('{_G}')\nopen('n', 'w')\nPY",
    "from_import_copy": f"python3 - <<'PY'\nfrom shutil import copy\ncopy('/tmp/x', '{_G}')\nopen('n', 'w')\nPY",
    "rmtree_tools": f"python3 - <<'PY'\nimport shutil\nprint('{_G}')\nshutil.rmtree('tools')\nopen('n', 'w')\nPY",
    "path_copy_with_keyword": f"python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/x').copy('{_G}', preserve_metadata=True)\nopen('n', 'w')\nPY",
    "bash_cd_then_relative": f"cd tools && python3 -c \"print('{_G}'); open('../{_G}', 'w').write('x')\"",
    "here_string_expands": f"T={_G}; python3 - <<< \"print('{_G}'); open('$T', 'w').write('x')\"",
    "unquoted_heredoc_after_a_quoted_one":
        f"cat <<'A' >/dev/null\nx\nA\nT={_G}; python3 - <<PY\nprint('{_G}')\nopen('$T', 'w').write('x')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS_5))
def test_guard_review_round_3_forms_block(name):
    code, _ = _bash(_PY_BLOCKS_5[name])
    assert code == 2, name


def test_python_dash_E_and_attached_W_still_read_the_script(tmp_path):
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert _bash_in(tmp_path, "python3 -E patch.py") == 2
    assert _bash_in(tmp_path, "python3 -Wignore patch.py") == 2


def test_harmless_forms_still_pass():
    for cmd in [f"python3 - <<'PY'\nimport sys, copy\nx = copy.copy([1])\nprint(sys.path, '{_G}')\nopen('/tmp/o.txt', 'w')\nPY",
                f"cd /tmp && python3 - <<'PY'\nprint(open('{SCRIPT.parents[1]}/{_G}').read())\nopen('/tmp/o.txt', 'w')\nPY"]:
        code, err = _bash(cmd)
        assert code == 0, (cmd, err)


def test_a_script_run_after_a_cd_is_read_from_there(tmp_path):
    """Guard review round 4 (Fable F1, Grok F2): `cd dir && python3 patch.py` runs
    dir/patch.py, so that is the file read."""
    (tmp_path / "patch.py").write_text(_REWRITE)
    assert _bash(f"cd {tmp_path} && python3 patch.py")[0] == 2
    assert _bash(f"cd {tmp_path} && bash patch.py")[0] == 0     # not a shell script


def test_many_cds_fall_back_to_the_heuristic():
    cds = " && ".join(f"cd d{i}" for i in range(5))
    code, _ = _bash(f"{cds} && python3 - <<'PY'\nprint(open('{_G}').read())\nopen('/tmp/o.txt', 'w')\nPY")
    assert code == 2


# Mutation round (2026-10-21): each check reached by a script that passes every other.
_CHAIN = "".join(f"a{i} = a{i - 1}\n" for i in range(1, 15))
_MANY = "\n".join(f"p = '/tmp/{i}'" for i in range(9))
_PY_PASSES_2 = {
    "names_a_guard_without_writing_and_imports_freely": f"python3 - <<'PY'\nimport io\nprint('{_G}')\nPY",
    "keyword_unpacking_in_a_harmless_call":
        f"python3 - <<'PY'\nd = dict(**{{'a': 1}})\nprint(open('{_G}').read())\nopen('/tmp/o.txt', 'w')\nPY",
    "path_opened_for_reading": f"python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('{_G}').open().read())\nopen('/tmp/o.txt', 'w')\nPY",
    "harmless_command_list": f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['ls'])\nprint(open('{_G}').read())\nopen('/tmp/o.txt', 'w')\nPY",
}
_PY_BLOCKS_6 = {
    "too_many_joined_values":
        f"python3 - <<'PY'\nimport os\n{_MANY}\nq = p\nprint('{_G}')\nopen(os.path.join(p, q), 'w').write('x')\nPY",
    "parameter_shadows_a_module_variable":
        f"python3 - <<'PY'\np = '/tmp/x'\ndef f(p):\n    open(p, 'w').write('x')\nf('{_G}')\nPY",
    "loop_rebinds_an_assigned_name":
        f"python3 - <<'PY'\np = '/tmp/x'\nfor p in ['{_G}']:\n    open(p, 'w').write('x')\nPY",
    "folding_past_the_depth_cap": f"python3 - <<'PY'\na0 = '/tmp/x'\n{_CHAIN}print('{_G}')\nopen(a14, 'w').write('x')\nPY",
    "write_function_missing_its_path": f"python3 - <<'PY'\nimport os\nos.remove()\nprint('{_G}')\nopen('n', 'w')\nPY",
    "undefined_name_as_target": f"python3 - <<'PY'\nprint('{_G}')\nopen(somewhere, 'w').write('x')\nPY",
    "dict_copy_beside_a_guard_write": f"python3 - <<'PY'\nd = {{}}\ne = d.copy()\nopen('{_G}', 'w').write('x')\nPY",
    "run_a_guard_and_also_write_it":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3', '{_G}'])\nopen('{_G}', 'w').write('x')\nPY",
    "command_list_with_cd":
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['sh', '-c', 'cd tools && rm ./check_public_pii.py'])\nprint('{_G}')\nopen('n', 'w')\nPY",
    "command_list_with_an_unknown_word":
        f"python3 - <<'PY'\nimport subprocess, os\nsubprocess.run(['rm', os.environ['X']])\nprint('{_G}')\nopen('n', 'w')\nPY",
    "argv_from_a_variable_naming_a_guard":
        f"G2={_G}; python3 - \"$G2\" <<'PY'\nimport sys\nprint('{_G}')\nopen(sys.argv[1], 'w').write('x')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_PASSES_2))
def test_mutation_round_passes(name):
    code, err = _bash(_PY_PASSES_2[name])
    assert code == 0, (name, err)


@pytest.mark.parametrize("name", sorted(_PY_BLOCKS_6))
def test_mutation_round_blocks(name):
    code, _ = _bash(_PY_BLOCKS_6[name])
    assert code == 2, name


def test_a_command_word_with_several_values_is_unknown():
    code, _ = _bash(f"python3 - <<'PY'\nimport subprocess\nt = '/tmp/a'\nt = '/tmp/b'\n"
                    f"subprocess.run(['rm', t])\nprint('{_G}')\nopen('n', 'w')\nPY")
    assert code == 2


@pytest.mark.parametrize("cmd", [["black", "tools/check_public_pii.py"], ["git", "apply", "x.diff", "tools/check_public_pii.py"],
                                 ["python3", "-m", "black", "tools/check_public_pii.py"]])
def test_a_tool_run_on_a_guard_blocks(cmd):
    """Pre-push review 2026-10-21: a formatter or patch tool rewrites a guard, and the
    Bash scan does not know those tools as writers."""
    code, _ = _bash(f"python3 - <<'PY'\nimport subprocess\nsubprocess.run({cmd!r})\nprint('{_G}')\nopen('n', 'w')\nPY")
    assert code == 2


def test_shell_true_with_a_list_is_unknown():
    code, _ = _bash(f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['echo x > {_G}'], shell=True)\nprint('{_G}')\nopen('n', 'w')\nPY")
    assert code == 2


@pytest.mark.parametrize("cmd", [["git", "apply", "/tmp/fix.diff"], ["patch", "-p1", "-i", "/tmp/fix.diff"],
                                 ["black", "tools"], ["ruff", "format", "."]])
def test_a_command_that_is_not_read_only_blocks(cmd):
    """Pre-push review 2 (2026-10-21): patch tools and directory formatters name no guard."""
    code, _ = _bash(f"python3 - <<'PY'\nimport subprocess\nsubprocess.run({cmd!r})\nprint('{_G}')\nopen('n', 'w')\nPY")
    assert code == 2


@pytest.mark.parametrize("cmd", [["git", "status"], ["ls", "-la"], ["git", "diff", "HEAD"]])
def test_a_read_only_command_passes(cmd):
    code, err = _bash(f"python3 - <<'PY'\nimport subprocess\nsubprocess.run({cmd!r})\nprint(open('{_G}').read())\nopen('/tmp/o.txt', 'w')\nPY")
    assert code == 0, err


# `cd X && <script>` (Nick approved, 2026-11-16). A `cd` kept the earlier directory as a
# possible cwd, because a cd can fail. After `&&` it cannot: a failed cd stops the chain,
# so a script editing ANOTHER checkout's copy of a guard was blocked as if it edited this
# repo's (in a replay of 39,816 past commands it cleared 5 blocks, all edits of another
# worktree's copy, and blocked nothing new). Only `&&` and a
# pipe carry the new directory; `;`, newline, `||`, `&` and `)` keep today's union.
_EDIT_COPY = ("python3 - <<'PY'\np = 'tools/check_public_pii.py'\ns = open(p).read()\n"
              "open(p, 'w').write(s)\nPY")
_REPO = SCRIPT.parents[1]


def _other_checkout(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "check_public_pii.py").write_text("x = 1\n")
    return tmp_path


@pytest.mark.parametrize("glue", ["cd {d} && ", "cd {d} && true && ", "cd {d} && echo x | ",
                                  "true && cd {d} && ", "true; cd {d} && "])
def test_cd_and_chain_into_another_checkout_edits_only_that_copy(tmp_path, glue):
    d = _other_checkout(tmp_path)
    assert _bash_in(_REPO, glue.format(d=d) + _EDIT_COPY) == 0


@pytest.mark.parametrize("glue", [
    "cd {d} ; ",                     # the cd may fail and the script still runs here
    "cd {d}\n",
    "cd {d} && true || ",            # the || branch runs when the cd failed
    "(cd {d} && true) && ",          # a subshell's cd does not reach the next command
    "cd {d} && cd - && ",            # back to where it started
    "cd -P {d} && ",                 # an option, not a directory: judged as before
    "cd {d} & ",
    # Review round 1 (Codex F2, Grok F1-F3, 2026-11-16): a cd that does not run, or
    # does not move the shell, or is undone, must not narrow the next command.
    "true || cd {d} && ",            # the cd is skipped when `true` succeeds
    "true | cd {d} && ",             # a pipeline element runs in a subshell
    "cd {r} && true | cd {d} && ",   # same, after a chain was already open
    "pushd {d} && popd && ",         # popd returns to the starting directory
    "cd {d} && source /dev/null && ",  # a sourced file may cd anywhere
    "cd {d} && {{ cd {r}; }} && ",    # a cd inside a group moves the shell too
    # Review round 2 (Codex F1, Grok F1): a directory-stack or OLDPWD operand is not a
    # path, and can lead straight back to the starting directory.
    "cd {d} && cd ~- && ",
    "pushd {d} && pushd +1 && ",
    "pushd {d} && cd ~1 && ",
])
def test_a_cd_that_may_not_hold_still_blocks_this_repos_guard(tmp_path, glue):
    d = _other_checkout(tmp_path)
    assert _bash_in(_REPO, glue.format(d=d, r=_REPO) + _EDIT_COPY) == 2


def test_cd_and_chain_inside_this_repo_still_blocks():
    assert _bash_in(_REPO, "cd tests && " + _EDIT_COPY.replace("'tools/", "'../tools/")) == 2
    assert _bash_in(_REPO, f"cd {_REPO} && " + _EDIT_COPY) == 2


@pytest.mark.parametrize("cmd", [
    "a && b | c ; d", "cd x && (y) || z", "echo hi > f && cat <<'E' | wc\nbody\nE\n",
    "( cd x ) && y", "a 2>&1 && b &", "", "x=1 && y",
    # From the mutation run (2026-11-16): a redirection whose next token is not a word,
    # and a heredoc that opens after `&` with no body, each split commands differently
    # when the redir or word branch of _following_ops was broken.
    "x\n< <(y)", "cat <<'E' &",
])
def test_following_ops_lines_up_with_simple_commands(cmd):
    sys.path.insert(0, str(SCRIPT.parent))
    import check_guard_edit_approval as g
    from shell_tokens import simple_commands
    assert len(g._following_ops(cmd)) == len(simple_commands(cmd))


def test_when_ops_and_commands_disagree_no_chain_is_used(tmp_path, monkeypatch):
    """The length fallback: if _following_ops ever drifts from simple_commands, zip would
    drop commands; instead every command is judged against the union, as before the chain."""
    sys.path.insert(0, str(SCRIPT.parent))
    import check_guard_edit_approval as g
    d = _other_checkout(tmp_path)
    cmd = f"cd {d} && " + _EDIT_COPY
    assert g.bash_guard_writes(cmd, str(_REPO)) == []          # the chain applies
    monkeypatch.setattr(g, "_following_ops", lambda c: [])
    assert g.bash_guard_writes(cmd, str(_REPO)) != []          # the union, which blocks



# --- S2c (overnight-112526, 2026-11-25): chdir, argparse, git pathspecs -----------------
# Two read-only scripts were blocked because the analysis gave up: one called
# os.chdir(<literal repo path>), the other imported argparse (friction ledger, 2 fires,
# 2026-11-16 and 2026-11-25). Both are followed now. A script that is analysable ONLY
# because of this change gets a strict verdict: every write target must be fully known.
_PY_CHDIR_PASSES = {
    "chdir_literal_then_write_elsewhere":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir('/private/tmp')\nopen('/private/tmp/o.json', 'w').write('x')\nPY",
    "chdir_literal_relative_write_not_a_guard":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir('/private/tmp')\nopen('o.json', 'w').write('x')\nPY",
    "argparse_reads_a_guard_writes_elsewhere":
        f"python3 - <<'PY'\nimport argparse\nap = argparse.ArgumentParser()\nap.add_argument('--n')\nprint(open('{_G}').read())\nopen('/private/tmp/o.json', 'w').write('x')\nPY",
    "argparse_filetype_read":
        f"python3 - <<'PY'\nimport argparse\nap = argparse.ArgumentParser()\nap.add_argument('f', type=argparse.FileType('r'))\nprint('{_G}')\nopen('/private/tmp/o.json', 'w')\nPY",
    "git_log_unknown_pathspec_after_dashdash":
        f"python3 - <<'PY'\nimport subprocess\nfor p in ['{_G}']:\n    subprocess.run(['git', 'log', '-1', '--format=%cs', '--', p])\nopen('/private/tmp/o.json', 'w')\nPY",
}
_PY_CHDIR_BLOCKS = {
    "chdir_from_environment":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir(os.environ['D'])\nopen('/private/tmp/o.json', 'w')\nPY",
    "chdir_relative_literal":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir('tools')\nopen('/private/tmp/o.json', 'w')\nPY",
    "chdir_aliased":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\ncd = os.chdir\ncd('/private/tmp')\nopen('/private/tmp/o.json', 'w')\nPY",
    "chdir_from_import":
        f"python3 - <<'PY'\nfrom os import chdir\nprint('{_G}')\nchdir(input())\nopen('/private/tmp/o.json', 'w')\nPY",
    # relative guard target, checked against the START cwd too (the repo)
    "chdir_elsewhere_then_relative_guard_write":
        f"python3 - <<'PY'\nimport os\nos.chdir('/private/tmp')\nopen('{_G}', 'w').write('x')\nPY",
    # strict: an unknown directory part is allowed elsewhere, not after a chdir. The
    # path sits in a variable because the pre-filter's open(...) pattern stops at a ')'.
    "chdir_then_unknown_dir_target":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir('/private/tmp')\nt = os.getcwd() + '/o.json'\nopen(t, 'w')\nPY",
    "argparse_then_unknown_dir_target":
        f"python3 - <<'PY'\nimport argparse, os\nprint('{_G}')\nt = os.getcwd() + '/o.json'\nopen(t, 'w')\nPY",
    "argparse_filetype_write":
        f"python3 - <<'PY'\nimport argparse\nap = argparse.ArgumentParser()\nap.add_argument('f', type=argparse.FileType('w'))\nprint('{_G}')\nopen('/private/tmp/o.json', 'w')\nPY",
    "argparse_filetype_aliased":
        f"python3 - <<'PY'\nimport argparse\nF = argparse.FileType\nprint('{_G}')\nopen('/private/tmp/o.json', 'w')\nPY",
    "argparse_filetype_unknown_mode":
        f"python3 - <<'PY'\nimport argparse\nm = input()\nargparse.FileType(m)\nprint('{_G}')\nopen('/private/tmp/o.json', 'w')\nPY",
    # an unknown word BEFORE git's `--` may be an option that writes
    "git_log_unknown_word_before_dashdash":
        f"python3 - <<'PY'\nimport subprocess\nfor p in ['x']:\n    subprocess.run(['git', 'log', p, '--', 'a'])\nprint('{_G}')\nopen('/private/tmp/o.json', 'w')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_CHDIR_PASSES))
def test_s2c_python_script_now_followed_passes(name):
    code, err = _bash(_PY_CHDIR_PASSES[name])
    assert code == 0, (name, err)


@pytest.mark.parametrize("name", sorted(_PY_CHDIR_BLOCKS))
def test_s2c_python_script_still_blocks(name):
    code, _ = _bash(_PY_CHDIR_BLOCKS[name])
    assert code == 2, name


def test_s2c_chdir_records_the_directory():
    import ast
    g = _hook_module()
    w = g._PyWrites(ast.parse("import os\nos.chdir('/private/tmp')\nopen('o', 'w')\n"), ["-"])
    assert w.targets(".") == ["o"]
    assert w.chdirs == ["/private/tmp"] and w.strict is True



# Codex S2c r1 F1 (2026-11-25): chdir order. In straight-line code a chdir applies only to
# writes after it; in a loop or function the union is kept on purpose (over-block only).
_CLAUDE_DIR = str(SCRIPT.parents[1] / ".claude")
_PY_ORDER_PASSES = {
    "write_before_chdir_into_guard_dir":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nt = 'settings.json'\nopen(t, 'w')\nos.chdir('{_CLAUDE_DIR}')\nPY",
}
_PY_ORDER_BLOCKS = {
    "write_after_chdir_into_guard_dir":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nos.chdir('{_CLAUDE_DIR}')\nt = 'settings.json'\nopen(t, 'w')\nPY",
    "loop_with_chdir_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nfor _ in range(2):\n    t = 'settings.json'\n    open(t, 'w')\n    os.chdir('{_CLAUDE_DIR}')\nPY",
    "function_write_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\ndef w():\n    t = 'settings.json'\n    open(t, 'w')\nos.chdir('{_CLAUDE_DIR}')\nw()\nPY",
    # Grok s2c 3.F1 (2026-11-25): a generator runs when it is consumed, not where it is
    # written, so a write inside one keeps the union like a function. List, set and dict
    # comprehensions run at once; they are treated the same on purpose (over-block only,
    # b7's ruling: "treat comprehension/generator target scopes like functions").
    "generator_write_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\ng = (open('settings.json', 'w') for _ in [0])\nos.chdir('{_CLAUDE_DIR}')\nnext(g)\nPY",
    "list_comprehension_write_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nx = [open('settings.json', 'w') for _ in [0]]\nos.chdir('{_CLAUDE_DIR}')\nPY",
    "set_comprehension_write_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nx = {{open('settings.json', 'w') for _ in [0]}}\nos.chdir('{_CLAUDE_DIR}')\nPY",
    "dict_comprehension_write_keeps_the_union":
        f"python3 - <<'PY'\nimport os\nprint('{_G}')\nx = {{0: open('settings.json', 'w') for _ in [0]}}\nos.chdir('{_CLAUDE_DIR}')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_ORDER_PASSES))
def test_s2c_write_before_a_chdir_is_not_moved_by_it(name):
    code, err = _bash(_PY_ORDER_PASSES[name])
    assert code == 0, (name, err)


@pytest.mark.parametrize("name", sorted(_PY_ORDER_BLOCKS))
def test_s2c_chdir_order_blocks(name):
    code, _ = _bash(_PY_ORDER_BLOCKS[name])
    assert code == 2, name


# --- s2c survivors (output/analysis/112526-s2c-r2-mutation-check_guard_edit_approval.json) ---
# Only a chdir or argparse makes the verdict strict (an unknown target then blocks). Without
# them, an unknown directory with a non-guard file name passes, as before s2c. Each case
# writes '<unknown dir>/o.json' (os.path.join keeps the name; '+' folds to a bare unknown),
# which _may_be_guard clears, so only strictness decides. The open() takes a variable so
# the _WRITE_CALL pre-filter (no ')' inside the call) sees a write.
_PY_STRICT_PASSES = {
    # kills :454 NEGATE_CMP (`import os` must not set strict)
    "plain_import_unknown_dir":
        f"python3 - <<'PY'\nimport os\nopen('{_G}')\np = os.path.join(os.getcwd(), 'o.json')\nopen(p, 'w')\nPY",
    # kills :460 NEGATE_CMP (`from os import ...` must not set strict)
    "from_import_unknown_dir":
        f"python3 - <<'PY'\nfrom os.path import join\nfrom os import getcwd\nopen('{_G}')\n"
        "p = join(getcwd(), 'o.json')\nopen(p, 'w')\nPY",
}
_PY_STRICT_BLOCKS = {
    # kills :679 IF_FALSE (argparse: every target must be known)
    "argparse_unknown_dir":
        f"python3 - <<'PY'\nimport argparse, os\nopen('{_G}')\np = os.path.join(os.getcwd(), 'o.json')\nopen(p, 'w')\nPY",
    # kills :568 IF_FALSE (FileType(**kw) hides its mode, so it is not followed)
    "file_type_kwargs_unseen":
        f"python3 - <<'PY'\nimport argparse\nk = {{'mode': 'w'}}\nargparse.FileType(**k)\nopen('{_G}')\nopen('n', 'w')\nPY",
}


@pytest.mark.parametrize("name", sorted(_PY_STRICT_PASSES))
def test_s2c_only_chdir_or_argparse_makes_unknown_targets_block(name):
    code, err = _bash(_PY_STRICT_PASSES[name])
    assert code == 0, (name, err)


@pytest.mark.parametrize("name", sorted(_PY_STRICT_BLOCKS))
def test_s2c_strict_and_unseen_file_type_block(name):
    code, _ = _bash(_PY_STRICT_BLOCKS[name])
    assert code == 2, name
