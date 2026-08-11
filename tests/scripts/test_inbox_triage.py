"""Read-only triage extraction of data/inbox.md non-machine blocks.

Routing, not archiving. The oldest blocks turned out to be the most substantive
(a 425-line system-design handoff and an 84-line audit of whether the OS compounds
judgment, both months old and both still live work), so "age means dead" is the
wrong heuristic and this tool never uses it.

The invariant that matters most: this tool must NEVER mutate the inbox.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

FIXTURE = """\
# Inbox

## Career Scan Results

**Scanned:** 2026-07-09 12:30 UTC
- **[5/10]** Role at Northwind (SF) - [View](https://example.com/1)

## 2026-08-10 | Agent drip: lane-a (company)
- **Acme** - does things (score 4)

## 2026-05-02 | Meal plan and errands #personal

Groceries, school run.

## 2026-06-15 | SESSION HANDOFF for the widget work

Long unfinished thread.
More detail here.

## Call Debrief: someone at Globex

Interview notes.

## 2026-04-29 | Build a command around the dictation tool

One-line idea.

## 2026-03-01 | Something with no signature at all

Opaque.

<!--
## 2026-01-01 | A commented-out capture
-->
"""


def _run(tmp_path, out=None, extra=()):
    out = out or (tmp_path / "review.md")
    cmd = [sys.executable, str(TOOLS / "inbox_triage.py"),
           "--repo-root", str(tmp_path), "--out", str(out), *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return json.loads(r.stdout), r.returncode, Path(out)
    except json.JSONDecodeError:
        raise AssertionError(f"non-JSON: {r.stdout!r} / {r.stderr!r}")


def _write(tmp_path, text=FIXTURE):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "inbox.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_inbox_is_never_mutated(tmp_path):
    """The load-bearing safety property of this tool."""
    p = _write(tmp_path)
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    _run(tmp_path)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_machine_blocks_are_excluded(tmp_path):
    _write(tmp_path)
    res, code, out = _run(tmp_path)
    assert code == 0
    body = out.read_text(encoding="utf-8")
    assert "Career Scan Results" not in body
    assert "Agent drip" not in body


def test_commented_out_blocks_are_excluded(tmp_path):
    _write(tmp_path)
    _, _, out = _run(tmp_path)
    assert "A commented-out capture" not in out.read_text(encoding="utf-8")


def test_explicit_personal_tag_wins_over_inferred_signatures(tmp_path):
    """Nick tagged these himself. An inferred match must never override a hand tag."""
    _write(tmp_path)
    res, _, _ = _run(tmp_path)
    assert res["by_group"]["personal-vault"] == 1


def test_open_loop_is_detected(tmp_path):
    _write(tmp_path)
    res, _, _ = _run(tmp_path)
    assert res["by_group"]["open-loop"] == 1


def test_call_debrief_routes_to_coaching(tmp_path):
    _write(tmp_path)
    res, _, _ = _run(tmp_path)
    assert res["by_group"]["coaching"] == 1


def test_short_imperative_becomes_idea_todo(tmp_path):
    _write(tmp_path)
    res, _, _ = _run(tmp_path)
    assert res["by_group"]["idea-todo"] == 1


def test_unsignatured_block_is_unclassified_not_guessed(tmp_path):
    """Better to say 'needs your eyes' than to invent a destination."""
    _write(tmp_path)
    res, _, _ = _run(tmp_path)
    assert res["by_group"]["unclassified"] == 1


def test_every_block_carries_a_line_range_and_hash(tmp_path):
    """Routing must be traceable back to the exact source lines."""
    _write(tmp_path)
    _, _, out = _run(tmp_path)
    body = out.read_text(encoding="utf-8")
    import re
    assert len(re.findall(r"`L\d+-\d+`", body)) == 5      # the 5 non-machine blocks
    assert len(re.findall(r"- \[ \] keep", body)) == 5


def test_empty_extraction_is_an_error_not_a_pass(tmp_path):
    """Fail-open guard: no blocks means a broken parse, not a clean inbox."""
    _write(tmp_path, "# Inbox\n\nNothing here.\n")
    res, code, _ = _run(tmp_path)
    assert code != 0
    assert res["code"] == "empty_extraction"


def test_unbalanced_comment_aborts(tmp_path):
    _write(tmp_path, "# Inbox\n\n<!--\n## 2026-01-01 | orphan\n")
    res, code, _ = _run(tmp_path)
    assert code != 0
    assert res["code"] == "unbalanced_comment"
