"""attention.py aggregates every review queue that currently dead-ends.

WHY. Measured 2026-08-13: four producers write review-gated items and none is consumed on a
cadence. The weekly promotion scan writes memory/promotion-backlog.md (in a directory 94% of
which has never been read) and files a Low-priority todo (where PARKED items from May still
sit). data/inbox.md holds 157 live items and grows ~158 lines/day. The queues work; the
surface does not exist.

The two properties these tests exist to protect, both learned the hard way today:

1. **A skipped queue must be loud.** A missing source file that silently drops out of the
   report turns "nothing needs attention" into a lie. Every skip is counted and named.
2. **Every count carries its denominator.** A bare "12" is not a finding. This is the
   CLAUDE.md scope hard rule applied to the tool's own output.

Read-only by construction: it takes no lock because it never writes, so it cannot corrupt
data/inbox.md while a parallel drain session holds it.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "attention.py"


def run(repo_root, expect=0):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root),
         "--memory-dir", str(Path(repo_root) / "memory"), "--json"],
        capture_output=True, text=True)
    assert proc.returncode == expect, f"rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def make_repo(tmp_path, todos=None, inbox=None):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    if todos is not None:
        (tmp_path / "data" / "job-todos.md").write_text(todos, encoding="utf-8")
    if inbox is not None:
        (tmp_path / "data" / "inbox.md").write_text(inbox, encoding="utf-8")
    return tmp_path


TODOS = """# Job To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| Overdue high thing | High | 2026-01-01 | Pending | x |
| Overdue med thing | Med | 2026-02-01 | Pending | x |
| No due date | Low | — | Pending | x |
| Already done | High | 2026-01-01 | Done | x |
"""

INBOX = """# Inbox

## 2026-08-01 | Capture one
body

## 2026-08-02 | Capture two
body
"""


def test_every_queue_reports_a_denominator(tmp_path):
    """A bare count is not a finding -- the CLAUDE.md scope rule, applied to this tool."""
    r = run(make_repo(tmp_path, todos=TODOS, inbox=INBOX))
    assert r["queues"], "no queues reported"
    for q in r["queues"]:
        assert "count" in q and "denominator" in q, q
        assert "status" in q and "source" in q, q


def test_missing_source_is_a_loud_skip_not_a_zero(tmp_path):
    """The failure this tool exists to prevent, one level down: absence reading as all-clear."""
    r = run(make_repo(tmp_path, todos=TODOS))  # no inbox.md
    inbox = [q for q in r["queues"] if q["name"] == "inbox"][0]
    assert inbox["status"] == "SKIPPED", inbox
    assert inbox["reason"], "a skip with no reason is unauditable"
    assert inbox["count"] is None, "a skipped queue must NOT report 0"
    assert r["skipped_count"] >= 1
    assert r["complete"] is False, "a report with a skipped queue is not complete"


def test_all_sources_present_reports_complete(tmp_path):
    r = run(make_repo(tmp_path, todos=TODOS, inbox=INBOX))
    todos = [q for q in r["queues"] if q["name"] == "todos"][0]
    assert todos["status"] == "ok"
    assert todos["count"] == 2, "two overdue Pending rows; Done and no-due excluded"
    assert todos["denominator"] == 4, "denominator is all Active data rows"


def test_inbox_count_uses_the_census_not_a_naive_scan(tmp_path):
    """A naive '## ' scan miscounts: a long HTML comment hides headers. Census is the oracle."""
    hidden = INBOX + "\n<!--\n## 2026-08-03 | Hidden in a comment\n-->\n"
    r = run(make_repo(tmp_path, todos=TODOS, inbox=hidden))
    inbox = [q for q in r["queues"] if q["name"] == "inbox"][0]
    assert inbox["count"] == 2, f"commented header must not count: {inbox}"


def test_is_read_only(tmp_path):
    """It must be safe to run while a parallel session holds data/inbox.md."""
    repo = make_repo(tmp_path, todos=TODOS, inbox=INBOX)
    before = {p: p.read_bytes() for p in (repo / "data").glob("*.md")}
    run(repo)
    for p, b in before.items():
        assert p.read_bytes() == b, f"{p} was modified by a read-only tool"


def test_empty_repo_does_not_claim_all_clear(tmp_path):
    """Zero queues readable must never render as 'nothing needs attention'."""
    r = run(make_repo(tmp_path))
    assert r["complete"] is False
    assert r["skipped_count"] == len(r["queues"])
    assert r["total_open"] is None, "an all-skipped run has no meaningful total"
