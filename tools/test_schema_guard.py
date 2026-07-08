#!/usr/bin/env python3
"""Tests for schema_guard.py and its wiring into pipeline_staleness.py and
todo_daily_metrics.py.

Regression coverage for the 2026-06-08 incident: a positional parser (cols[N])
silently mis-read data/job-pipeline.md after its live header drifted from what
the parser assumed, and stayed green in tests whose fixtures pinned the SAME
stale header. These tests confirm the guard (1) passes on the current canonical
schema, (2) raises on the exact drifted schema that caused the real incident,
and (3) that the drift surfaces as a clear JSON error from the wired scripts
instead of a silent misparse.

Run:  PYTHONIOENCODING=utf-8 python3 tools/test_schema_guard.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_guard import assert_schema, find_header_line, SchemaDriftError

PIPELINE_STALENESS = Path(__file__).parent / "pipeline_staleness.py"
TODO_DAILY_METRICS = Path(__file__).parent / "todo_daily_metrics.py"

CANONICAL_PIPELINE_HEADER = (
    "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
)
# The exact stale header from the 2026-06-08 incident (tools/pipeline_staleness.py
# read cols[4] as Next Action / cols[6..7] as notes/url against this obsolete layout).
DRIFTED_PIPELINE_HEADER = (
    "| Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |"
)

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


# --- Unit-level: schema_guard module itself -------------------------------

check(
    "matching schema passes silently",
    assert_schema(
        CANONICAL_PIPELINE_HEADER,
        ["Company", "Role", "Stage", "Date Updated", "Next Action", "CV Used", "Notes", "URL"],
    ) is None,
)

check("no header (None) passes silently (nothing to check yet)",
      assert_schema(None, ["Company", "Role"]) is None)

try:
    assert_schema(
        DRIFTED_PIPELINE_HEADER,
        ["Company", "Role", "Stage", "Date Updated", "Next Action", "CV Used", "Notes", "URL"],
    )
    check("drifted header (missing + unexpected columns) raises", False)
except SchemaDriftError as e:
    check("drifted header (missing + unexpected columns) raises", True)
    check("error names the missing column", "Next Action" in str(e))
    check("error names the unexpected column", "Date Added" in str(e))

try:
    assert_schema(
        "| Role | Company | Stage |",
        ["Company", "Role", "Stage"],
    )
    check("reordered-only header raises", False)
except SchemaDriftError as e:
    check("reordered-only header raises", True)
    check("reorder error mentions 'reordered'", "reordered" in str(e).lower())

check(
    "find_header_line finds the right row, ignores non-matching lines",
    find_header_line(
        "# Pipeline\n\n| Company | Role | Stage |\n|---|---|---|\n| Acme | PM | Applied |\n",
        "| Company |",
    ) == "| Company | Role | Stage |",
)
check("find_header_line returns None when absent",
      find_header_line("no tables here\n", "| Company |") is None)


# --- Integration-level: wired parsers actually fire the guard -------------

def _run(script: Path, root: Path):
    cmd = [sys.executable, str(script), "--repo-root", str(root)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return out.returncode, json.loads(out.stdout)


def _make_repo(pipeline_header: str) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "data" / "job-pipeline.md").write_text(
        "# Job Application Pipeline\n\n"
        f"{pipeline_header}\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Acme | PM | Applied | 2026-06-01 | Follow up | resume.pdf | some notes | https://example.com |\n",
        encoding="utf-8",
    )
    (root / "data" / "job-todos.md").write_text(
        "# Job Search To-Dos\n\n"
        "## Active\n\n"
        "| Task | Priority | Due | Status | Notes |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Example task | Med | 2026-06-05 | Pending | — |\n\n"
        "## Completed\n\n"
        "| Task | Priority | Completed | Notes |\n"
        "| --- | --- | --- | --- |\n",
        encoding="utf-8",
    )
    return root


happy_root = _make_repo(CANONICAL_PIPELINE_HEADER)
rc, out = _run(PIPELINE_STALENESS, happy_root)
check("pipeline_staleness.py: canonical schema exits 0", rc == 0)
check("pipeline_staleness.py: canonical schema parses (no error key)",
      "status" not in out or out.get("status") != "error")

rc, out = _run(TODO_DAILY_METRICS, happy_root)
check("todo_daily_metrics.py: canonical schema exits 0", rc == 0)
check("todo_daily_metrics.py: canonical schema parses (no error key)",
      "status" not in out or out.get("status") != "error")

drifted_root = _make_repo(DRIFTED_PIPELINE_HEADER)
rc, out = _run(PIPELINE_STALENESS, drifted_root)
check("pipeline_staleness.py: drifted schema exits non-zero", rc != 0)
check("pipeline_staleness.py: drifted schema reports schema_drift",
      out.get("code") == "schema_drift")

rc, out = _run(TODO_DAILY_METRICS, drifted_root)
check("todo_daily_metrics.py: drifted schema exits non-zero", rc != 0)
check("todo_daily_metrics.py: drifted schema reports schema_drift",
      out.get("code") == "schema_drift")

print(f"\nschema_guard tests: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
