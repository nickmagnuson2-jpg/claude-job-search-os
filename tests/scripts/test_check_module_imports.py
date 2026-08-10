"""Tests for tools/check_module_imports.py — the PostToolUse import probe.

Origin 2026-08-07: an edit made tools/career_scanner/scanner.py unimportable
(`Path` referenced before `from pathlib import Path`). 1022 tests passed while it
was broken. py_compile and ast.parse both PASS on that file — the NameError only
surfaces at import. This hook exists to catch exactly that, so these tests must
prove it catches the real shape and stays quiet on clean files.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_module_imports.py"


def run_hook(file_path):
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout


def in_tools(tmp_path) -> Path:
    """The hook is scoped to tools/, so fixtures must live under a tools/ dir."""
    d = tmp_path / "tools"
    d.mkdir(exist_ok=True)
    return d


def test_clean_module_is_silent(tmp_path):
    m = in_tools(tmp_path) / "clean_mod.py"
    m.write_text("from pathlib import Path\nX = Path('.')\n", encoding="utf-8")
    rc, out = run_hook(m)
    assert rc == 0 and out.strip() == ""


def test_python_outside_tools_is_ignored(tmp_path):
    """Scope decision 2026-08-07: only tools/ is probed, so a scratch script with
    import side effects is never executed by the hook."""
    m = tmp_path / "scratch.py"
    m.write_text("raise RuntimeError('should never be imported by the hook')\n",
                 encoding="utf-8")
    rc, out = run_hook(m)
    assert rc == 0 and out.strip() == ""


def test_the_actual_regression_shape_is_caught(tmp_path):
    """Path used before pathlib is imported — valid syntax, fails at import."""
    m = in_tools(tmp_path) / "broken_mod.py"
    m.write_text(
        "import sys\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from pathlib import Path\n",
        encoding="utf-8")
    # Prove the cheaper checks do NOT catch it, which is this hook's whole reason.
    import ast
    ast.parse(m.read_text(encoding="utf-8"))
    rc, out = run_hook(m)
    assert rc == 0                      # never blocks
    assert "FAILS TO IMPORT" in out
    assert "NameError" in out


def test_syntax_error_is_caught(tmp_path):
    m = in_tools(tmp_path) / "syntax_mod.py"
    m.write_text("def broken(\n", encoding="utf-8")
    rc, out = run_hook(m)
    assert rc == 0 and "import" in out.lower()


def test_missing_dependency_is_soft_not_a_defect_claim(tmp_path):
    m = in_tools(tmp_path) / "dep_mod.py"
    m.write_text("import a_package_that_does_not_exist_xyz\n", encoding="utf-8")
    rc, out = run_hook(m)
    assert rc == 0
    assert "missing dependency" in out
    assert "FAILS TO IMPORT" not in out


@pytest.mark.parametrize("name", ["notes.md", "data.json", "script.sh"])
def test_non_python_ignored(tmp_path, name):
    f = in_tools(tmp_path) / name
    f.write_text("whatever", encoding="utf-8")
    rc, out = run_hook(f)
    assert rc == 0 and out.strip() == ""


def test_missing_file_fails_open(tmp_path):
    rc, out = run_hook(in_tools(tmp_path) / "gone.py")
    assert rc == 0 and out.strip() == ""


def test_bad_json_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="{not json",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_empty_payload_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="{}",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_skipped_directory_ignored(tmp_path):
    d = in_tools(tmp_path) / "__pycache__"
    d.mkdir()
    m = d / "x.py"
    m.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    rc, out = run_hook(m)
    assert rc == 0 and out.strip() == ""


def test_real_repo_modules_import_cleanly():
    """Live smoke against the actual writers touched this session. Unit tests use
    inputs I chose; this uses the real files."""
    for rel in ("tools/inbox_lock.py", "tools/granola_auto_debrief.py",
                "tools/agent_collect.py", "tools/remember_apply.py",
                "tools/career_scanner/scanner.py"):
        rc, out = run_hook(REPO_ROOT / rel)
        assert rc == 0, rel
        assert "FAILS TO IMPORT" not in out, f"{rel} is broken:\n{out}"
