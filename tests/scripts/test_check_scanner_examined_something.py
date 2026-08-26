"""Tests for tools/check_scanner_examined_something.py — the false-pass hook.

The hook blocks (exit 2) a Bash invocation of a stdin-payload checker that passes
positional paths the tool provably discards: it reads nothing, matches nothing,
and exits 0. It must stay clean (exit 0) for the sweep-flag invocation, for a
piped/redirected payload, for tools that really do consume positional arguments,
for tools with no stdin hook path at all, and for the script path appearing inside
a quoted string, a grep pattern, or a heredoc body.

Fixtures are synthetic scripts written to tmp_path so the properties under test
(source has json.load(sys.stdin); source declares no positional) are varied
independently. One test additionally replays the VERBATIM origin command from
memory/feedback_wrong_cli_interface_returns_a_false_pass.md against the real
tools/check_public_pii.py when this file is installed in the repo.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_LOCAL = _HERE.parent / "check_scanner_examined_something.py"
SCRIPT = _LOCAL if _LOCAL.exists() else (
    _HERE.parents[2] / "tools" / "check_scanner_examined_something.py")


def _run(command: str, cwd: str = "") -> int:
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    if r.returncode not in (0, 2):  # pragma: no cover - surfaces crashes
        raise AssertionError(f"unexpected exit {r.returncode}: {r.stderr}")
    return r.returncode


def _proc(command, cwd: str = "", script=None, run_in=None):
    """Full CompletedProcess, with the script under test and the SUBPROCESS working
    directory both controllable — `cwd` here is the *payload* cwd, which is only the
    first of the three bases _resolve() tries."""
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    return subprocess.run([sys.executable, str(script or SCRIPT)],
                          input=payload, capture_output=True, text=True,
                          cwd=str(run_in) if run_in else None)


def _stderr(command: str, cwd: str = "") -> str:
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    return subprocess.run([sys.executable, str(SCRIPT)],
                          input=payload, capture_output=True, text=True).stderr


# --- fixtures: scripts with different, independently varied interfaces -------

DUAL = '''\
import json, sys
def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--scan":
        paths = argv[1:]
        print(json.dumps({"scanned": len(paths)}))
        sys.exit(0)
    data = json.load(sys.stdin)
    sys.exit(0)
main()
'''

HOOK_ONLY = '''\
import json, sys
data = json.load(sys.stdin)
sys.exit(0)
'''

POSITIONAL_CLI = '''\
import argparse, json, sys
p = argparse.ArgumentParser()
p.add_argument("paths", nargs="*")
p.add_argument("--json", action="store_true")
if len(sys.argv) > 1:
    a = p.parse_args()
    print(len(a.paths))
    sys.exit(0)
data = json.load(sys.stdin)
sys.exit(0)
'''

SUBCOMMANDS = '''\
import argparse, json, sys
p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="cmd")
w = sub.add_parser("write")
w.add_argument("--output")
data = json.load(sys.stdin)
'''

NO_STDIN = '''\
import sys
print(sys.argv[1:])
sys.exit(0)
'''


@pytest.fixture()
def repo(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "dual.py").write_text(DUAL)
    (tools / "hookonly.py").write_text(HOOK_ONLY)
    (tools / "positional.py").write_text(POSITIONAL_CLI)
    (tools / "subcmd.py").write_text(SUBCOMMANDS)
    (tools / "plain.py").write_text(NO_STDIN)
    (tmp_path / "a.md").write_text("x")
    return tmp_path


@pytest.fixture()
def repo_with_oversized(repo):
    """`repo` plus tools/big.py: a hook-only tool (would otherwise BLOCK) whose
    source is larger than _MAX_SOURCE_BYTES, so the hook must refuse to read it and
    fail open on that segment."""
    (repo / "tools" / "big.py").write_text(HOOK_ONLY + "\n# " + "x" * 2_000_000)
    return repo


# --- should BLOCK (exit 2): the invocation provably scans nothing ------------

@pytest.mark.parametrize("command", [
    "python3 tools/dual.py a.md b.md c.md",
    "PYTHONIOENCODING=utf-8 python3 tools/dual.py a.md",            # env prefix
    "ls && PYTHONIOENCODING=utf-8 python3 tools/dual.py a.md",      # after &&
    "cat x || python3 tools/dual.py a.md",                          # after || (not a pipe)
    "ls; python3 tools/dual.py a.md",                               # after ;
    "$(python3 tools/dual.py a.md)",                                # subshell
    'python3 tools/dual.py "docs/some file.md"',                    # quoted path arg
    "python3 tools/hookonly.py a.md",                               # hook-only tool
    "./tools/dual.py a.md",                                         # direct exec
    "git status\npython3 tools/dual.py a.md",                       # second line
])
def test_blocks_vacuous_invocation(command, repo):
    assert _run(command, cwd=str(repo)) == 2, \
        f"AssertionError: vacuous invocation was allowed: {command!r}"


def test_block_message_names_the_sweep_flag(repo):
    err = _stderr("python3 tools/dual.py a.md b.md", cwd=str(repo))
    assert "BLOCKED" in err
    assert "--scan" in err
    assert "scan NOTHING" in err


def test_block_message_says_hook_only_when_no_flags(repo):
    err = _stderr("python3 tools/hookonly.py a.md", cwd=str(repo))
    assert "no CLI flags at all" in err


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("command", [
    # the correct sweep invocation — the whole point of the block message
    "PYTHONIOENCODING=utf-8 python3 tools/dual.py --scan a.md b.md",
    "python3 tools/dual.py --scan a.md",
    # stdin actually supplied
    "echo '{}' | python3 tools/dual.py a.md",
    "python3 tools/dual.py < payload.json",
    "python3 tools/dual.py a.md < payload.json",
    "python3 tools/dual.py <<'EOF'\n{}\nEOF",
    # bare hook invocation (no discarded args)
    "python3 tools/dual.py",
    # tools that genuinely consume positionals
    "python3 tools/positional.py a.md b.md",
    "python3 tools/subcmd.py write",
    # not a stdin-payload tool at all
    "python3 tools/plain.py a.md",
    # path unresolvable -> fail open
    "python3 tools/nonexistent.py a.md",
    # the script path as text, not an invocation
    'echo "python3 tools/dual.py a.md"',
    "grep -n 'python3 tools/dual.py a.md' docs/",
    "git commit -m 'stop calling python3 tools/dual.py a.md'",
    "git commit -F - <<'EOF'\npython3 tools/dual.py a.md was wrong\nEOF",
    "cat tools/dual.py",
    "git add tools/dual.py",
    # unrelated python work
    "PYTHONIOENCODING=utf-8 python3 -m pytest tests/scripts/test_x.py",
    "python3 tools/mutation_check.py tools/dual.py --isolation",
])
def test_allows(command, repo):
    assert _run(command, cwd=str(repo)) == 0, \
        f"AssertionError: legitimate invocation was blocked: {command!r}"


def test_fails_open_on_garbage_payload():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_fails_open_on_empty_command(repo):
    assert _run("", cwd=str(repo)) == 0


# --- the ORIGIN input, replayed against the real tool -----------------------

def _find_repo() -> Path | None:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    cands = ([Path(env)] if env else []) + list(_HERE.parents)
    for c in cands:
        if (c / "tools" / "check_public_pii.py").is_file():
            return c
    return None


ORIGIN = ("PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py "
          "tests/scripts/test_pipe_write.py tools/pipe_write.py docs/usage.md")
CLEAN_VARIANT = ("PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py --scan "
                 "tests/scripts/test_pipe_write.py tools/pipe_write.py docs/usage.md")


def test_blocks_the_origin_input():
    """2026-08-14: this exact shape exited 0 having scanned nothing, and was
    reported as a PII verification over three files."""
    repo = _find_repo()
    if repo is None:
        pytest.skip("real repo not reachable from this location")
    assert _run(ORIGIN, cwd=str(repo)) == 2
    assert "--scan" in _stderr(ORIGIN, cwd=str(repo))


def test_allows_the_corrected_origin_input():
    repo = _find_repo()
    if repo is None:
        pytest.skip("real repo not reachable from this location")
    assert _run(CLEAN_VARIANT, cwd=str(repo)) == 0


# --- one earlier segment must never swallow a later vacuous invocation -------
#
# Each `continue` in _verdict skips ONE match and lets the loop keep looking. If the
# guard above it stops firing, the skipped match is processed instead and raises
# (None args / None script / None source), main's fail-open swallows it, and the
# genuinely vacuous invocation later in the same command is never reached: exit 0
# instead of exit 2. Every test below pairs a skipped segment with a blockable one.

def test_unparseable_quoting_earlier_still_blocks_a_later_vacuous_call(repo):
    # kills _verdict IF_FALSE / DROP_CONTINUE on `if args is None: continue`
    cmd = "python3 tools/plain.py don't\npython3 tools/dual.py a.md"
    assert _run(cmd, cwd=str(repo)) == 2, "AssertionError: later vacuous call not blocked"
    assert "dual.py" in _stderr(cmd, cwd=str(repo)), \
        "AssertionError: the block message names the wrong script"


def test_unresolvable_script_earlier_still_blocks_a_later_vacuous_call(repo):
    # kills _verdict IF_FALSE / DROP_CONTINUE on `if script is None: continue`
    cmd = "python3 tools/nonexistent.py a.md; python3 tools/dual.py a.md"
    assert _run(cmd, cwd=str(repo)) == 2, "AssertionError: later vacuous call not blocked"
    assert "dual.py" in _stderr(cmd, cwd=str(repo)), \
        "AssertionError: the block message names the wrong script"


def test_unreadable_source_earlier_still_blocks_a_later_vacuous_call(repo_with_oversized):
    # kills _verdict IF_FALSE / DROP_CONTINUE on `if src is None: continue`
    cmd = "python3 tools/big.py a.md; python3 tools/dual.py a.md"
    assert _run(cmd, cwd=str(repo_with_oversized)) == 2, "AssertionError: later vacuous call not blocked"
    assert "dual.py" in _stderr(cmd, cwd=str(repo_with_oversized)), \
        "AssertionError: the block message names the wrong script"


def test_oversized_source_is_not_read_and_fails_open(repo_with_oversized):
    # kills _read_source IF_FALSE on the _MAX_SOURCE_BYTES size guard: big.py is
    # hook-only with no positional, so reading it at all would produce a BLOCK.
    assert _run("python3 tools/big.py a.md", cwd=str(repo_with_oversized)) == 0, \
        "AssertionError: oversized source was read instead of skipped"


def test_path_only_inside_a_literal_is_not_invoked_even_beside_a_real_call(repo):
    # kills _verdict IF_FALSE / DROP_CONTINUE on `if path_token not in real_paths`.
    # real_paths is non-empty (the --scan call survives stripping), so the early
    # `if not real_paths` return cannot mask this: the hookonly.py line lives only
    # inside a quoted commit message and must not be treated as an invocation.
    cmd = ('python3 tools/dual.py --scan a.md\n'
           'git commit -m "oops\n'
           'python3 tools/hookonly.py a.md\n'
           'fixed"')
    assert _run(cmd, cwd=str(repo)) == 0, \
        "AssertionError: a path inside a quoted literal was treated as an invocation"


def test_piped_payload_is_allowed_silently(repo):
    # pins the `sep == "|"` guard with an assertion rather than a crash: a pipe
    # supplies the stdin payload, so the call is legitimate and must say nothing.
    cmd = "echo '{}' | python3 tools/dual.py a.md"
    assert _run(cmd, cwd=str(repo)) == 0, "AssertionError: piped payload was blocked"
    assert _stderr(cmd, cwd=str(repo)) == "", "AssertionError: piped payload produced output"


# --- resolution falls back past the payload cwd -----------------------------

def test_resolves_via_the_repo_root_when_the_payload_cwd_has_no_such_file(tmp_path):
    """kills _resolve IF_TRUE on `if cand.is_file()`: with the guard always true the
    first (non-existent) candidate is returned and the real script is never read."""
    repo = _find_repo()
    if repo is None or SCRIPT.parent.resolve() != (repo / "tools").resolve():
        pytest.skip("hook not installed in the real repo tools/ directory")
    # payload cwd AND process cwd both point at an empty dir, so only the third
    # base (the repo root this hook lives in) can resolve tools/check_public_pii.py.
    r = _proc(ORIGIN, cwd=str(tmp_path), run_in=tmp_path)
    assert r.returncode == 2, r.stderr
    assert "check_public_pii.py" in r.stderr


# --- fail-open surface ------------------------------------------------------

def test_non_string_command_fails_open_without_crashing():
    """kills main DROP_CALL on the `except Exception: sys.exit(0)` fail-open: a
    non-string command explodes inside _verdict, and dropping that exit leaves
    `verdict` unbound, so the hook dies with a traceback instead of allowing."""
    payload = json.dumps({"tool_input": {"command": 12345}, "cwd": ""})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    assert r.returncode == 0, f"AssertionError: expected fail-open, got {r.returncode}: {r.stderr}"
    assert "Traceback" not in r.stderr, "AssertionError: hook crashed instead of failing open"


# --- import-time behaviour and the shared strip_literals --------------------

def test_import_does_not_run_main_and_binds_the_shared_strip_literals(tmp_path):
    """Two properties in one import: importing the module must NOT execute main()
    (kills the `if __name__ == "__main__"` IF_TRUE), and it must bind the shared
    tools/hook_command_lint.strip_literals rather than the divergent in-file
    fallback (kills the module-level sys.path.insert DROP_CALL). The two
    implementations differ on an UNQUOTED heredoc body containing a substitution:
    the shared one keeps it (a real `$(python ...)` is still a call), the fallback
    blanks it unconditionally."""
    tools_dir = SCRIPT.parent.resolve()
    saved_path = list(sys.path)
    saved_mod = sys.modules.pop("hook_command_lint", None)
    sys.path[:] = [p for p in sys.path
                   if not (p and Path(p).resolve() == tools_dir)]
    try:
        spec = importlib.util.spec_from_file_location("_scanner_hook_under_test",
                                                      str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)   # must not read stdin / must not sys.exit
        except BaseException as exc:   # noqa: BLE001 - report as an assertion, not a crash
            raise AssertionError(
                f"AssertionError: importing the hook ran main(): {exc!r}") from None
        kept = mod.strip_literals("cat <<EOF\n$(python3 tools/dual.py a.md)\nEOF")
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("_scanner_hook_under_test", None)
        if saved_mod is not None:
            sys.modules["hook_command_lint"] = saved_mod
    assert "tools/dual.py" in kept, \
        "AssertionError: the in-file fallback strip_literals is bound, not the shared one"


def test_staged_copy_without_the_shared_helper_still_strips_and_blocks(tmp_path, repo):
    """kills the fallback strip_literals RETURN_NONE. Copied outside tools/, the
    shared import fails and the in-file fallback is the live implementation: it must
    return a real string (a None makes _verdict raise and the hook fail open on a
    provably vacuous call) and it must still blank quoted spans."""
    staged = tmp_path / "staged"
    staged.mkdir()
    copy = staged / SCRIPT.name
    copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    assert not (staged / "hook_command_lint.py").exists()

    vacuous = _proc("python3 tools/dual.py a.md", cwd=str(repo), script=copy,
                    run_in=staged)
    assert vacuous.returncode == 2, vacuous.stderr
    assert "BLOCKED" in vacuous.stderr, "AssertionError: fallback path did not block"

    quoted = _proc('echo "python3 tools/dual.py a.md"', cwd=str(repo), script=copy,
                   run_in=staged)
    assert quoted.returncode == 0, quoted.stderr
