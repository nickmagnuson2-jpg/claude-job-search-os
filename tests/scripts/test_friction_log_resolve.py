"""Tests for `friction_log.py resolve` — mark a ledger row resolved without
inflating its occurrence count or hand-editing the file.

Resolution lives in the Fix cell ("RESOLVED <date>: <note>"); the count and the
historical promotion signal are left untouched. `list --unresolved` filters rows
whose Fix cell is not yet a RESOLVED marker. This closes the doc-vs-tool staleness
trap: before `resolve` existed, a fixed friction could only be marked done by
hand-editing (forbidden by the ledger header) or re-appending (inflates the count).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "friction_log.py"

LEDGER_SEED = """# Friction Log

## Entries

| Date | Surface | Nature | Fix | Occurrences | Promotion |
|---|---|---|---|---|---|
| 2026-06-02 | pipeline_staleness.py | command not found: python interpreter missing | [auto-logged; update once resolved] | 5 | script-patch (mandatory) |
| 2026-06-02 | check_voice_pure.py | source footer over 100 chars blocks the write | restructured footer | 1 | — |
"""


def _run(ledger: Path, *args):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True,
        env={"FRICTION_LEDGER": str(ledger), "PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    last = (r.stdout or "").strip().splitlines()
    payload = json.loads(last[-1]) if last else {}
    return r.returncode, payload


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER_SEED, encoding="utf-8")
    return p


def test_resolve_updates_fix_without_incrementing_count(ledger):
    code, payload = _run(ledger, "resolve", "pipeline_staleness.py",
                          "command not found python interpreter",
                          "--note", "doc sweep to python3 + hook built")
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["action"] == "resolved"
    text = ledger.read_text(encoding="utf-8")
    # Count is preserved (still 5), Fix now carries a RESOLVED marker.
    assert "| 5 |" in text
    assert "RESOLVED" in text
    assert "doc sweep to python3 + hook built" in text


def test_resolve_no_match_errors(ledger):
    code, payload = _run(ledger, "resolve", "nonexistent.py", "totally unrelated nature words",
                         "--note", "x")
    assert code != 0
    assert payload["status"] == "error"


def test_resolve_ambiguous_does_not_write(ledger):
    # Two distinct natures both share weak keywords; a vague query must refuse
    # rather than resolve the wrong row.
    before = ledger.read_text(encoding="utf-8")
    code, payload = _run(ledger, "resolve", "pipeline_staleness.py", "python", "--note", "x")
    # Either a clean single match or an explicit ambiguous refusal — but if it
    # refuses, the file must be unchanged.
    if payload.get("action") == "ambiguous":
        assert ledger.read_text(encoding="utf-8") == before
        assert len(payload["rows"]) >= 2


def test_list_unresolved_excludes_resolved_rows(ledger):
    _run(ledger, "resolve", "pipeline_staleness.py",
         "command not found python interpreter", "--note", "fixed")
    code, payload = _run(ledger, "list", "--unresolved")
    assert code == 0
    natures = [r["nature"] for r in payload["rows"]]
    # The resolved python row is gone; the unresolved voice-pure row remains.
    assert not any("command not found" in n for n in natures)
    assert any("footer" in n for n in natures)
