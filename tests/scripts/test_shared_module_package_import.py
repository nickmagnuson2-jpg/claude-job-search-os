"""Every tool that imports a shared sibling module (md_table, hook_runtime) must also
import as `tools.<name>`, not only as a script.

Run as a script, Python puts tools/ first on sys.path, so `from md_table import ...`
resolves on its own. Imported as part of the `tools` package (a test doing
`from tools.granola_save import ...`, or a tool doing `from tools import vault_paths`)
it does not. On 2026-10-21 ten tools lost that ability in one refactor: the line that
puts tools/ on sys.path looked redundant, a mutation run confirmed no test noticed its
removal, and a cross-model review caught it. Each tool is imported in a fresh process,
so one tool's sys.path change cannot rescue another.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = re.compile(r"^from (md_table|hook_runtime) import ", re.M)
TOOLS = sorted(p.stem for p in (REPO / "tools").glob("*.py")
               if SHARED.search(p.read_text(encoding="utf-8")))


def test_the_scan_found_the_shared_module_users():
    assert len(TOOLS) >= 10, TOOLS


@pytest.mark.parametrize("name", TOOLS)
def test_tool_imports_as_part_of_the_tools_package(name):
    r = subprocess.run([sys.executable, "-c", f"import tools.{name}"], cwd=REPO,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
