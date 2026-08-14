"""The promotion scanner must report coverage over an UNFILTERED denominator.

WHY. `load_memory_files()` skips any file lacking `occurrences:` -- that is correct for the
promotion/demotion signals, which cannot evaluate a file without the key. But it means any
ratio computed inside that loop is 100% by construction: the filter pre-selects its own
numerator. A first cut of `schema_coverage` did exactly that and reported `19/19 (100%)`
against a corpus where the real figure was `19/540 (3.5%)`.

That is the failure this whole project exists to fix, one level up: an empty candidate list
reading as an all-clear when it is really an empty scan. A coverage metric that inherits the
filter reproduces the bug while appearing to measure it.

These fixtures are synthetic; the real memory dir is outside the repo and gitignored.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "scan_promotion_candidates.py"

VISIBLE = """---
name: feedback_visible_{n}
description: a rule the detector can see
metadata:
  node_type: memory
  type: feedback
  occurrences: {occ}
  promoted: no
  reopen_gate: "x"
  last_cited: 2026-08-13
---
body
"""

INVISIBLE = """---
name: feedback_invisible_{n}
description: a rule written under the old schema
metadata:
  node_type: memory
  type: feedback
---
body
"""

NON_FEEDBACK = """---
name: project_thing_{n}
description: a fact, not a rule
metadata:
  node_type: memory
  type: project
---
body
"""


def build_corpus(tmp_path: Path, visible: int, invisible: int, projects: int) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    for i in range(visible):
        (d / f"feedback_visible_{i}.md").write_text(VISIBLE.format(n=i, occ=1))
    for i in range(invisible):
        (d / f"feedback_invisible_{i}.md").write_text(INVISIBLE.format(n=i))
    for i in range(projects):
        (d / f"project_thing_{i}.md").write_text(NON_FEEDBACK.format(n=i))
    # Non-rule files that must be excluded from every denominator.
    (d / "MEMORY.md").write_text("router\n")
    (d / "index-tools.md").write_text("index\n")
    (d / "archive-2026-01.md").write_text("archive\n")
    return d


def run(memory_dir: Path, repo_root: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(memory_dir),
         "--repo-root", str(repo_root), "--mode", "interactive", "--dry-run"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_coverage_denominator_is_not_the_filtered_set(tmp_path):
    """The motivating bug: 3 visible among 30 must not report 100%."""
    d = build_corpus(tmp_path, visible=3, invisible=27, projects=0)
    cov = run(d, tmp_path)["schema_coverage"]
    assert cov["files"] == 30, cov
    assert cov["visible"] == 3
    assert cov["invisible"] == 27
    assert cov["pct"] == 10.0
    assert cov["pct"] != 100.0, "coverage inherited the loader's filter"


def test_feedback_scope_is_reported_separately(tmp_path):
    """occurrences: is only meaningful for feedback rules -- backfill scope follows that."""
    d = build_corpus(tmp_path, visible=2, invisible=8, projects=10)
    cov = run(d, tmp_path)["schema_coverage"]
    assert cov["files"] == 20
    assert cov["feedback_rules"] == 10
    assert cov["feedback_visible"] == 2
    assert cov["feedback_invisible"] == 8, "this number is the backfill work-list"
    assert cov["feedback_pct"] == 20.0


def test_indexes_archives_and_router_are_excluded_from_the_denominator(tmp_path):
    d = build_corpus(tmp_path, visible=1, invisible=1, projects=1)
    cov = run(d, tmp_path)["schema_coverage"]
    assert cov["files"] == 3, "MEMORY.md / index-*.md / archive-*.md must not be counted"


def test_promotion_signal_still_only_sees_visible_files(tmp_path):
    """Coverage changed; the signal itself must not have."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_visible_0.md").write_text(VISIBLE.format(n=0, occ=2))  # a real candidate
    (d / "feedback_invisible_0.md").write_text(INVISIBLE.format(n=0))
    report = run(d, tmp_path)
    assert report["promotion_count"] == 1
    assert report["promotion_candidates"][0]["occurrences"] == 2
    assert report["schema_coverage"]["invisible"] == 1


def test_empty_corpus_does_not_divide_by_zero(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    cov = run(d, tmp_path)["schema_coverage"]
    assert cov["files"] == 0 and cov["pct"] == 0.0


BACKFILLED = """---
name: feedback_backfilled_{n}
description: a rule made visible by the Phase 1 backfill, with no last_cited
metadata:
  node_type: memory
  type: feedback
  occurrences: 1
  promoted: no
  reopen_gate: "UNREVIEWED -- schema backfill 2026-08-13"
  needs_review: true
---
body
"""


def test_never_cited_files_get_their_own_bucket(tmp_path):
    """The Phase 1 seed policy omits last_cited on purpose, which creates a third state.

    Visible to promotion, invisible to demotion. Silently skipping those files would hide
    the strongest demotion evidence in the corpus: never cited since it was written.
    """
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_visible_0.md").write_text(VISIBLE.format(n=0, occ=1))  # has last_cited
    for i in range(3):
        (d / f"feedback_backfilled_{i}.md").write_text(BACKFILLED.format(n=i))
    report = run(d, tmp_path)
    assert report["never_cited_count"] == 3
    assert {c["file"] for c in report["never_cited_sample"]} == {
        f"feedback_backfilled_{i}.md" for i in range(3)
    }
    assert report["needs_review_count"] == 3
    assert report["demotion_count"] == 0, "a never-cited file is not a dated staleness hit"


def test_never_cited_sample_is_bounded(tmp_path):
    """~383 files land in this bucket the day the backfill runs; the count is the signal."""
    d = tmp_path / "memory"
    d.mkdir()
    for i in range(25):
        (d / f"feedback_backfilled_{i}.md").write_text(BACKFILLED.format(n=i))
    report = run(d, tmp_path)
    assert report["never_cited_count"] == 25
    assert len(report["never_cited_sample"]) == 10


def test_backfilled_occurrences_do_not_surface_as_promotion_candidates(tmp_path):
    """`occurrences: 1` is a floor, not a count -- it must not fake a promotion gate."""
    d = tmp_path / "memory"
    d.mkdir()
    for i in range(5):
        (d / f"feedback_backfilled_{i}.md").write_text(BACKFILLED.format(n=i))
    assert run(d, tmp_path)["promotion_count"] == 0


TERMINAL = """---
name: feedback_terminal_{n}
description: a rule no artifact can enforce
metadata:
  node_type: memory
  type: feedback
  occurrences: {occ}
  promoted: no
  terminal: true
  terminal_reason: "judgment call about calibration; no artifact property to check"
  reopen_gate: "n/a -- terminal-behavioral"
  last_cited: 2026-08-13
---
body
"""


def test_terminal_rules_do_not_surface_as_promotion_candidates(tmp_path):
    """A rule no artifact can enforce inflates the candidate list forever.

    The 2026-08-13 corpus audit classified 25 of 230 rules terminal-behavioral: correct as
    written, promoted nowhere, and un-promotable in principle. Left unmarked they re-surface
    at every scan, and a candidate list padded with un-actionable rows trains the reader to
    skim the actionable ones.
    """
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_terminal_0.md").write_text(TERMINAL.format(n=0, occ=3))
    (d / "feedback_visible_0.md").write_text(VISIBLE.format(n=0, occ=2))
    report = run(d, tmp_path)
    assert report["promotion_count"] == 1, "the terminal rule must not be a candidate"
    assert report["promotion_candidates"][0]["file"] == "feedback_visible_0.md"


def test_terminal_rules_are_reported_not_silently_dropped(tmp_path):
    """Suppression without a count is indistinguishable from a scanner that lost the file.

    This is the same failure the coverage metric exists to prevent, one level down: a
    filtered-out rule has to remain visible AS filtered, with the reason it was filtered.
    """
    d = tmp_path / "memory"
    d.mkdir()
    for i in range(3):
        (d / f"feedback_terminal_{i}.md").write_text(TERMINAL.format(n=i, occ=4))
    report = run(d, tmp_path)
    assert report["terminal_count"] == 3
    assert {c["file"] for c in report["terminal_rules"]} == {
        f"feedback_terminal_{i}.md" for i in range(3)
    }
    assert all(c["terminal_reason"] for c in report["terminal_rules"]), (
        "a terminal mark with no stated reason is an unauditable silencer"
    )


def test_terminal_only_suppresses_promotion_not_the_denominators(tmp_path):
    """Marking a rule terminal must not shrink coverage -- it is still a schema-visible rule."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_terminal_0.md").write_text(TERMINAL.format(n=0, occ=3))
    (d / "feedback_invisible_0.md").write_text(INVISIBLE.format(n=0))
    cov = run(d, tmp_path)["schema_coverage"]
    assert cov["files"] == 2
    assert cov["visible"] == 1, "a terminal rule still carries the schema"


@pytest.mark.parametrize("value", ["false", "no", ""])
def test_falsy_terminal_values_leave_the_rule_a_candidate(tmp_path, value):
    """Fail open: only an explicit truthy mark suppresses. A typo must not silence a rule."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_terminal_0.md").write_text(
        TERMINAL.format(n=0, occ=3).replace("terminal: true", f"terminal: {value}")
    )
    report = run(d, tmp_path)
    assert report["promotion_count"] == 1, f"terminal: {value!r} must not suppress"
    assert report["terminal_count"] == 0


def test_oversized_reporting_is_advisory_and_never_mutates(tmp_path):
    d = build_corpus(tmp_path, visible=1, invisible=0, projects=0)
    big = d / "index-big.md"
    big.write_text("x" * (30 * 1024))
    before = big.read_bytes()
    report = run(d, tmp_path)
    hits = [o for o in report["oversized_context_files"] if o["file"] == "index-big.md"]
    assert len(hits) == 1 and hits[0]["bytes"] == 30 * 1024
    assert big.read_bytes() == before, "reporting must never touch the file"
