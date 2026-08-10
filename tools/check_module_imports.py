#!/usr/bin/env python3
"""
check_module_imports.py — PostToolUse hook for Claude Code.

Imports a Python module immediately after it is edited, and warns if the import
raises. Triggered by .claude/settings.json PostToolUse on Write|Edit|MultiEdit.
Never exits non-zero — never blocks workflow.

WHY THIS EXISTS (2026-08-07):
    An inserted import line referenced `Path` before `from pathlib import Path`,
    making tools/career_scanner/scanner.py unimportable. **1022 tests passed while
    it was broken**, because no test imported that module. It was caught only by
    running the import by hand, minutes later.

    Neither `py_compile` nor `ast.parse` catches this class: the file is
    syntactically valid, and NameError happens at import time. Only a real import
    finds it. That is the entire justification for this hook — it does the one
    cheap check a green fixture suite provably cannot do.

    Root-cause writeup: memory/feedback_remediation_is_when_defects_are_introduced.md

DESIGN NOTES:
  - Runs the import in a SUBPROCESS. Importing arbitrary edited modules in-process
    would pollute the hook's own interpreter and could hang it; a subprocess with a
    timeout is contained and killable.
  - Import side effects are real: a module with top-level work will do that work.
    Mitigated by a short timeout and by skipping files that look executable-only.
    This is the accepted cost — the alternative is not catching the defect class.
  - Fails OPEN on every unexpected condition (bad JSON, missing file, timeout,
    non-Python path). A hook that blocks on its own bugs is worse than no hook.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 10

# Directories whose modules are not safe or not meaningful to import on edit.
SKIP_DIR_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "launchd",          # plists and installers, not importable library code
}

# Import errors worth surfacing. A module failing on a MISSING THIRD-PARTY dep is
# an environment fact, not an edit defect, so ModuleNotFoundError is reported
# separately and softly (the repo has a known pandas-less module).
HARD_ERRORS = ("NameError", "SyntaxError", "IndentationError", "AttributeError",
               "ImportError", "TypeError", "ValueError")


def _should_check(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    if any(part in SKIP_DIR_PARTS for part in path.parts):
        return False
    # Scoped to tools/ deliberately (decision 2026-08-07). That is where the
    # importable library code lives and where the defect happened. Probing every
    # .py anywhere would run import side effects on scratch scripts and one-off
    # files that were never meant to be imported, for no coverage gain.
    if "tools" not in path.parts:
        return False
    if not path.is_file():
        return False
    return True


def _import_probe(path: Path) -> tuple:
    """Import the module in a subprocess. Returns (ok, error_text)."""
    parent = str(path.parent)
    # Repo root too, so `from tools.x import y` style absolute imports resolve.
    repo_root = str(Path(__file__).resolve().parent.parent)
    code = (
        "import sys\n"
        f"sys.path.insert(0, {parent!r})\n"
        f"sys.path.insert(0, {repo_root!r})\n"
        "import importlib.util as _u\n"
        f"_s = _u.spec_from_file_location({path.stem!r}, {str(path)!r})\n"
        "_m = _u.module_from_spec(_s)\n"
        "_s.loader.exec_module(_m)\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            cwd=repo_root,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True, ""      # fail open: a slow/unrunnable probe is not a defect claim
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or "").strip()


def _last_exception_line(stderr: str) -> str:
    for line in reversed(stderr.splitlines()):
        if line.strip():
            return line.strip()
    return "import failed"


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    file_path = (data.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return

    path = Path(file_path)
    if not _should_check(path):
        return

    ok, stderr = _import_probe(path)
    if ok:
        return

    summary = _last_exception_line(stderr)

    # A missing third-party dependency is an environment fact, not an edit defect.
    if "ModuleNotFoundError" in summary:
        print(f"ℹ️  {path.name} does not import: {summary} "
              f"(missing dependency, not an edit defect — ignore if expected)")
        return

    if any(e in summary for e in HARD_ERRORS):
        print(f"⚠️  {path.name} FAILS TO IMPORT after this edit: {summary}\n"
              f"    py_compile and the test suite will NOT catch this. "
              f"Import it and fix before moving on:\n"
              f"    PYTHONIOENCODING=utf-8 python3 -c \"import sys; "
              f"sys.path.insert(0,'{path.parent}'); import {path.stem}\"")
        return

    print(f"⚠️  {path.name} failed to import after this edit: {summary}")


if __name__ == "__main__":
    main()
