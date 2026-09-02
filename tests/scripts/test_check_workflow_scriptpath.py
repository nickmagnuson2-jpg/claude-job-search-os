#!/usr/bin/env python3
"""Tests for check_workflow_scriptpath.py — the Workflow({name}) stale-snapshot guard.

Covers the origin input verbatim (a `name`-keyed plan-hardening launch), the two
property checks that a presence check would miss (scriptPath that does not resolve
on disk; scriptPath pointing into the persisted snapshot dir), and the clean and
fail-open cases.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "check_workflow_scriptpath.py")

# The repo-relative script that the real launches target.
REAL_SCRIPT = ".claude/workflows/plan-hardening.js"


def _run(payload, cwd=None, env=None):
    """Run the hook with `payload` on stdin; return (exit_code, stderr)."""
    proc_env = dict(os.environ)
    proc_env["PYTHONIOENCODING"] = "utf-8"
    proc_env.pop("CLAUDE_PROJECT_DIR", None)
    if env:
        proc_env.update(env)
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload) if not isinstance(payload, str) else payload,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=proc_env,
    )
    return proc.returncode, proc.stderr


def _import_hook_module():
    """Import the hook as a module, fresh each call (never cached in sys.modules).

    stdin is swapped for a payload that WOULD block if `main()` ran at import time,
    so an import that executes main() raises SystemExit(2) instead of passing
    silently.
    """
    spec = importlib.util.spec_from_file_location("_check_workflow_scriptpath_uut", HOOK)
    module = importlib.util.module_from_spec(spec)
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(
        json.dumps({"tool_name": "Workflow", "tool_input": {"name": "plan-hardening"}})
    )
    try:
        spec.loader.exec_module(module)
    finally:
        sys.stdin = original_stdin
    return module


@pytest.fixture
def fake_repo(tmp_path):
    """A tmp dir shaped like the repo: .claude/workflows/plan-hardening.js exists."""
    wf = tmp_path / ".claude" / "workflows"
    wf.mkdir(parents=True)
    (wf / "plan-hardening.js").write_text("// v2 on disk\n")
    snap = tmp_path / "session" / "workflows" / "scripts"
    snap.mkdir(parents=True)
    (snap / "plan-hardening-wf_abc123.js").write_text("// v1 snapshot\n")
    return tmp_path


# ---------------------------------------------------------------- BLOCK cases


def test_blocks_origin_input(fake_repo):
    """ORIGIN, 2026-08-21: the plan-hardening run launched by name, which executed
    v1 (24,410 bytes) while v2 (28,622) sat on disk."""
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {
                "name": "plan-hardening",
                "args": {"planPath": "output/analysis/plan.md", "context": "x"},
            },
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "CACHED SNAPSHOT" in err
    assert "scriptPath" in err


def test_blocks_name_even_with_resume(fake_repo):
    code, _ = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"name": "extract-verify", "resumeFromRunId": "wf_da51a541"},
        },
        cwd=str(fake_repo),
    )
    assert code == 2


def test_blocks_scriptpath_that_does_not_resolve(fake_repo):
    """PROPERTY, not presence: the field is filled and still wrong."""
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": ".claude/workflows/plan-hardening-v2.js"},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "does not resolve" in err


def test_blocks_absolute_scriptpath_that_does_not_resolve(fake_repo):
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": str(fake_repo / "nope" / "missing.js")},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "does not resolve" in err


def test_blocks_scriptpath_into_snapshot_dir(fake_repo):
    """The stale copy passed under a compliant-looking key. The file EXISTS, so a
    resolve-only check would pass it."""
    snap = fake_repo / "session" / "workflows" / "scripts" / "plan-hardening-wf_abc123.js"
    assert snap.is_file()
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": str(snap)},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "snapshot" in err


def test_blocks_empty_scriptpath_with_name(fake_repo):
    code, _ = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"name": "plan-hardening", "scriptPath": "   "},
        },
        cwd=str(fake_repo),
    )
    assert code == 2


def test_whitespace_scriptpath_blocks_with_the_empty_or_not_a_string_reason(fake_repo):
    """Kills IF_FALSE / DROP_CALL on the line-142 `not isinstance(...) or not strip()`
    guard and its `_block(`. Without the guard a whitespace scriptPath still exits 2,
    but for the WRONG reason ("does not resolve"), so only the message pins it."""
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"name": "plan-hardening", "scriptPath": "   "},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "empty or not a string" in err
    assert "does not resolve" not in err


def test_blocks_non_string_scriptpath(fake_repo):
    """Kills the same line-142 guard on the non-str arm: a truthy non-string
    scriptPath (e.g. a number) must BLOCK cleanly, not crash on `.strip()`."""
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": 12345},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "empty or not a string" in err
    assert "Traceback" not in err


def test_blocks_list_scriptpath(fake_repo):
    """Same guard, non-str arm via a list — a JSON payload can carry one."""
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": [REAL_SCRIPT]},
        },
        cwd=str(fake_repo),
    )
    assert code == 2
    assert "empty or not a string" in err


# ---------------------------------------------------------------- CLEAN cases


def test_allows_clean_relative_scriptpath(fake_repo):
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": REAL_SCRIPT, "args": {"context": "x"}},
        },
        cwd=str(fake_repo),
    )
    assert code == 0, err


def test_allows_clean_absolute_scriptpath(fake_repo):
    code, err = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": str(fake_repo / REAL_SCRIPT)},
        },
        cwd=str(fake_repo),
    )
    assert code == 0, err


def test_resolves_via_claude_project_dir_when_cwd_is_elsewhere(fake_repo, tmp_path):
    """Hook runs from a different cwd; $CLAUDE_PROJECT_DIR still resolves it."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    code, err = _run(
        {"tool_name": "Workflow", "tool_input": {"scriptPath": REAL_SCRIPT}},
        cwd=str(other),
        env={"CLAUDE_PROJECT_DIR": str(fake_repo)},
    )
    assert code == 0, err


def test_allows_resume_only_call(fake_repo):
    """No launch key at all — nothing to judge, fail open."""
    code, _ = _run(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"resumeFromRunId": "wf_da51a541"},
        },
        cwd=str(fake_repo),
    )
    assert code == 0


def test_allows_other_tools(fake_repo):
    """A Bash command that merely mentions a workflow name must not be blocked."""
    code, _ = _run(
        {
            "tool_name": "Bash",
            "cwd": str(fake_repo),
            "tool_input": {"command": "echo 'Workflow name: plan-hardening'"},
        },
        cwd=str(fake_repo),
    )
    assert code == 0


def test_allows_other_tool_whose_input_has_a_name_field(fake_repo):
    """Kills IF_FALSE and DROP_CALL on the line-112 non-Workflow bail-out. The
    existing Bash test cannot: its payload has no `name`, so it falls through to the
    same exit 0. A `name`-carrying input on another tool (a label create, a Skill
    call) is the input that guard actually exists for."""
    code, err = _run(
        {
            "tool_name": "mcp__claude_ai_Gmail__create_label",
            "cwd": str(fake_repo),
            "tool_input": {"name": "plan-hardening"},
        },
        cwd=str(fake_repo),
    )
    assert code == 0, err
    assert "BLOCKED" not in err


def test_allows_other_tool_with_an_unresolvable_scriptpath_field(fake_repo):
    """Same line-112 guard, scriptPath arm: another tool carrying a scriptPath-shaped
    field that does not exist on disk must not be judged by this hook."""
    code, err = _run(
        {
            "tool_name": "Read",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": "session/workflows/scripts/gone-wf_1.js"},
        },
        cwd=str(fake_repo),
    )
    assert code == 0, err


# --------------------------------------------------- module-level / unit surface


def test_importing_the_hook_does_not_run_main():
    """Kills IF_TRUE on `if __name__ == "__main__":`. If that guard is always true,
    importing the module executes main() against the stdin payload above and raises
    SystemExit(2) out of exec_module."""
    module = _import_hook_module()
    assert hasattr(module, "main")
    assert hasattr(module, "resolve_script_path")


def test_resolve_script_path_returns_none_for_non_string_input():
    """Kills IF_FALSE on the line-85 type/empty guard in resolve_script_path. Without
    it, a non-string reaches os.path.isabs() and raises TypeError instead of
    returning None."""
    module = _import_hook_module()
    assert module.resolve_script_path(None, {}) is None
    assert module.resolve_script_path(12345, {}) is None
    assert module.resolve_script_path(["a"], {}) is None


def test_resolve_script_path_returns_none_for_blank_string(fake_repo):
    """Blank scriptPath resolves to nothing even though `os.path.join(root, "")`
    yields an existing directory — the guard, not isfile(), is what stops it."""
    module = _import_hook_module()
    assert module.resolve_script_path("   ", {"cwd": str(fake_repo)}) is None
    assert module.resolve_script_path("", {"cwd": str(fake_repo)}) is None


def test_resolve_script_path_returns_the_resolved_candidate(fake_repo):
    """The return value is the path that resolved, not just a truthy flag."""
    module = _import_hook_module()
    resolved = module.resolve_script_path(REAL_SCRIPT, {"cwd": str(fake_repo)})
    assert resolved == os.path.join(str(fake_repo), REAL_SCRIPT)
    assert os.path.isfile(resolved)


def test_absolute_scriptpath_resolves_without_consulting_candidate_roots(fake_repo):
    """Kills IF_FALSE on the line-88 `os.path.isabs()` branch.

    The docstring's contract is "absolute paths are checked as given"; relative ones
    are joined against the candidate roots. On POSIX `os.path.join(root, "/abs")`
    returns "/abs", so with roots present BOTH branches return the same string and
    the branch is invisible. Emptying the roots separates them: the real code still
    resolves the absolute path from its own text, while a code path that fell through
    to the root loop has nothing to iterate and returns None.
    """
    module = _import_hook_module()
    module._candidate_roots = lambda data: []
    abs_script = str(fake_repo / REAL_SCRIPT)
    assert os.path.isfile(abs_script)
    assert module.resolve_script_path(abs_script, {}) == abs_script
    # And the negative arm still reports None rather than a bogus hit.
    assert module.resolve_script_path(str(fake_repo / "nope" / "missing.js"), {}) is None


def test_main_exits_zero_explicitly_on_the_clean_path(fake_repo):
    """Kills DROP_CALL on the line-183 `sys.exit(0)`.

    A subprocess cannot see this: dropping the final exit still lets the interpreter
    fall off the end of main() and terminate 0. The hook contract is that main()
    ALWAYS terminates by naming its exit code — every other outcome in this function
    is a `sys.exit`/`_block` — so the clean path must raise SystemExit(0), not return
    None to its caller.
    """
    module = _import_hook_module()
    payload = json.dumps(
        {
            "tool_name": "Workflow",
            "cwd": str(fake_repo),
            "tool_input": {"scriptPath": REAL_SCRIPT, "args": {"context": "x"}},
        }
    )
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(payload)
    try:
        with pytest.raises(SystemExit) as excinfo:
            module.main()
    finally:
        sys.stdin = original_stdin
    assert excinfo.value.code == 0


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "[]",
        json.dumps({"tool_name": "Workflow"}),
        json.dumps({"tool_name": "Workflow", "tool_input": {}}),
        json.dumps({"tool_name": "Workflow", "tool_input": "string-not-dict"}),
    ],
)
def test_fails_open(payload, fake_repo):
    code, _ = _run(payload, cwd=str(fake_repo))
    assert code == 0


# ------------------------------------------------- real-repo smoke (not fixture)


def test_real_repo_workflow_path_is_clean():
    """Verified on the REAL file, not a fixture: the repo's own plan-hardening.js
    resolves and passes. Skips if run outside the repo."""
    repo = os.environ.get("JOB_SEARCH_REPO", "/Users/mag/Documents/Obsidian/30-projects/job-search")
    if not os.path.isfile(os.path.join(repo, REAL_SCRIPT)):
        pytest.skip("repo not present")
    code, err = _run(
        {"tool_name": "Workflow", "cwd": repo, "tool_input": {"scriptPath": REAL_SCRIPT}},
        cwd=repo,
    )
    assert code == 0, err
