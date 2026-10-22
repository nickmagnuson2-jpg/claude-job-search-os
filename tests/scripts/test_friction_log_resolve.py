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


def test_resolve_accepts_a_verbatim_excerpt_of_a_long_nature(tmp_path):
    """A short exact quote from a long row scored under the Jaccard threshold, so the
    most precise query available returned 'no row matches' (2026-10-22)."""
    long_nature = ("False positive on a compound Bash call: the hook saw a flag in a second "
                   "command segment and blocked the whole call; " + " ".join(
                       f"padding{i}word" for i in range(40)))
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER_SEED + f"| 2026-09-17 | check_x.py | {long_nature} | split calls | 3 | — |\n",
                 encoding="utf-8")
    code, payload = _run(p, "resolve", "check_x.py", "False positive on a compound Bash call",
                         "--note", "fixed")
    assert code == 0 and payload["action"] == "resolved"
    assert "check_x.py" in payload["matched"]
    assert "RESOLVED" in p.read_text(encoding="utf-8").splitlines()[-1]


def test_resolve_excerpt_only_matches_its_own_surface(tmp_path):
    """The excerpt shortcut must not cross surfaces: a short quote of another script's
    long row (too little word overlap for the fuzzy matcher) must not resolve it."""
    long_nature = "False positive on a compound Bash call " + " ".join(
        f"padding{i}word" for i in range(40))
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER_SEED + f"| 2026-09-17 | check_x.py | {long_nature} | split | 3 | — |\n",
                 encoding="utf-8")
    code, payload = _run(p, "resolve", "other_script.py", "False positive on a compound Bash call",
                         "--note", "x")
    assert code != 0 and "RESOLVED" not in p.read_text(encoding="utf-8")


def test_resolve_lone_row_on_a_surface_still_needs_the_query_to_match(ledger):
    """One row on a surface is not a license to close it with any query."""
    before = ledger.read_text(encoding="utf-8")
    code, _ = _run(ledger, "resolve", "check_voice_pure.py", "unrelated words entirely",
                   "--note", "x")
    assert code != 0 and ledger.read_text(encoding="utf-8") == before


def test_resolve_excerpt_shared_by_several_rows_refuses_without_writing(tmp_path):
    """An excerpt found in two rows must refuse, not fall back to fuzzy scoring and close one."""
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER_SEED
                 + "| 2026-09-17 | check_x.py | blocked a heredoc writing to output/ | a | 2 | — |\n"
                 + "| 2026-09-18 | check_x.py | blocked a heredoc writing to coaching/ | b | 2 | — |\n",
                 encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    code, payload = _run(p, "resolve", "check_x.py", "blocked a heredoc writing", "--note", "x")
    assert payload["action"] == "ambiguous" and len(payload["rows"]) == 2
    assert p.read_text(encoding="utf-8") == before
