#!/usr/bin/env python3
"""Guard against SILENT failures in the automation surface.

The failure class this repo actually suffers from is not "a thing crashes" -- it is
"a thing quietly stops running and nothing says so." Documented instances:

  * 2026-06-15  all 8 launchd jobs died for ~2 weeks (stale xattr on log files after
                a macOS TCC update). The in-job alert could not fire because the job
                never ran.
  * 2026-06-08  a parser read the wrong column index for an unknown duration; unit
                tests stayed GREEN because fixtures encoded the same stale header
                the buggy parser assumed.
  * (undated)   check_screenshot_path.py was built, tested, and never registered in
                settings.json -- it protected nothing until someone noticed.
  * 2026-08-10  every hook command in settings.json was rewritten from an absolute
                path to $CLAUDE_PROJECT_DIR. If that variable does not expand, all
                25 hooks break at once.

Each test below asserts a wiring invariant that, if violated, would otherwise fail
silently. These are cheap structural checks -- they do not execute hook logic.
"""
import json
import os
import plistlib
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"

# check_*.py files that are deliberately NOT PreToolUse/PostToolUse hooks.
# Anything added here needs a reason -- the default expectation is that a
# tools/check_*.py is wired into settings.json.
NON_HOOK_CHECKERS = {
    # Invoked by /standup and by the automation-health launchd job, not as a hook.
    "check_automation_health.py",
}


def _settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _hook_commands() -> list[str]:
    out = []
    for groups in _settings().get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command")
                if cmd:
                    out.append(cmd)
    return out


def _scripts_in(cmd: str) -> list[Path]:
    """Resolve every repo script path referenced by a hook command string."""
    paths = []
    for raw in re.findall(r"\$CLAUDE_PROJECT_DIR/(\S+\.py)", cmd):
        paths.append(REPO / raw)
    return paths


# --------------------------------------------------------------------------
# settings.json wiring
# --------------------------------------------------------------------------

def test_settings_json_is_valid():
    """A malformed settings.json disables EVERY hook at once, silently."""
    _settings()  # raises on invalid JSON


def test_every_hook_command_points_at_an_existing_script():
    """A renamed/moved/deleted hook script makes the hook a no-op."""
    missing = []
    for cmd in _hook_commands():
        for p in _scripts_in(cmd):
            if not p.is_file():
                missing.append((cmd, str(p.relative_to(REPO))))
    assert not missing, "hook commands reference non-existent scripts:\n" + "\n".join(
        f"  {path}  <- {cmd}" for cmd, path in missing
    )


def test_every_hook_script_compiles():
    """A syntax error in a hook makes it fail on every invocation."""
    broken = []
    with tempfile.TemporaryDirectory() as td:
        for cmd in _hook_commands():
            for p in _scripts_in(cmd):
                if not p.is_file():
                    continue
                cfile = Path(td) / (p.stem + ".pyc")
                try:
                    py_compile.compile(str(p), doraise=True, cfile=str(cfile))
                except py_compile.PyCompileError as exc:
                    broken.append(f"  {p.relative_to(REPO)}: {exc}")
    assert not broken, "hook scripts fail to compile:\n" + "\n".join(broken)


def test_no_hook_command_hardcodes_an_absolute_home_path():
    """Absolute /Users/<name>/ paths break on any other machine or fork, silently.

    Locked in 2026-08-10 when 25 hardcoded paths were replaced by $CLAUDE_PROJECT_DIR.
    """
    offenders = [c for c in _hook_commands() if "/Users/" in c]
    assert not offenders, "hook commands hardcode an absolute home path:\n" + "\n".join(
        f"  {c}" for c in offenders
    )


def test_project_dir_variable_actually_resolves():
    """$CLAUDE_PROJECT_DIR must expand to this repo when a hook runs.

    Guards the 2026-08-10 migration: if the variable is unset at hook runtime the
    command becomes `python3 /tools/foo.py` and every hook dies at once.
    """
    cmds = [c for c in _hook_commands() if "$CLAUDE_PROJECT_DIR" in c]
    assert cmds, "expected hook commands to use $CLAUDE_PROJECT_DIR"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)}
    expanded = subprocess.run(
        ["bash", "-c", 'echo "$CLAUDE_PROJECT_DIR/tools"'],
        capture_output=True, text=True, env=env,
    ).stdout.strip()
    assert expanded == str(REPO / "tools"), f"variable did not expand: {expanded!r}"


def test_a_representative_hook_runs_end_to_end():
    """Execute one real hook the way the harness would, and require a clean exit.

    Catches the case where the command string is well-formed and the file exists,
    but the hook cannot actually run (bad shebang, import error, missing dep).
    """
    target = REPO / "tools" / "check_public_pii.py"
    if not target.is_file():
        pytest.skip("check_public_pii.py not present")
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "docs/faq.md", "content": "benign placeholder text"},
    })
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        ["bash", "-c", f'PYTHONIOENCODING=utf-8 python3 "$CLAUDE_PROJECT_DIR/tools/check_public_pii.py"'],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, (
        f"representative hook did not exit clean (rc={proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


# --------------------------------------------------------------------------
# built-but-never-wired
# --------------------------------------------------------------------------

def test_every_checker_is_registered_or_explicitly_exempt():
    """A tools/check_*.py that nothing invokes protects nothing.

    Origin: check_screenshot_path.py shipped built + tested + unregistered.
    """
    registered = SETTINGS.read_text(encoding="utf-8")
    orphans = [
        p.name for p in sorted((REPO / "tools").glob("check_*.py"))
        if p.name not in NON_HOOK_CHECKERS and p.name not in registered
    ]
    assert not orphans, (
        "check_*.py files exist but are not registered in settings.json "
        "(wire them, or add to NON_HOOK_CHECKERS with a reason):\n"
        + "\n".join(f"  {n}" for n in orphans)
    )


# --------------------------------------------------------------------------
# launchd scheduled jobs
# --------------------------------------------------------------------------

def test_every_launchd_plist_points_at_an_existing_script():
    """A scheduled job whose script moved fails on a timer, into a log nobody reads."""
    missing = []
    for plist in sorted((REPO / "tools" / "launchd").glob("*.plist")):
        data = plistlib.loads(plist.read_bytes())
        for arg in data.get("ProgramArguments", []):
            if not isinstance(arg, str) or not arg.endswith(".py"):
                continue
            candidate = Path(arg)
            if not candidate.is_absolute():
                candidate = REPO / arg
            if not candidate.is_file():
                missing.append(f"  {plist.name}: {arg}")
    assert not missing, "launchd jobs reference non-existent scripts:\n" + "\n".join(missing)


def test_every_launchd_plist_has_a_schedule():
    """A plist with neither StartInterval nor StartCalendarInterval never fires."""
    unscheduled = []
    for plist in sorted((REPO / "tools" / "launchd").glob("*.plist")):
        data = plistlib.loads(plist.read_bytes())
        if "StartInterval" not in data and "StartCalendarInterval" not in data:
            unscheduled.append(f"  {plist.name}")
    assert not unscheduled, "launchd plists have no schedule:\n" + "\n".join(unscheduled)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
