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
from datetime import date
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
  last_cited: __TODAY__
---
body
"""
# Cited today, so this fixture is never a staleness hit. A literal date here made
# test_never_cited_files_get_their_own_bucket fail once it was --stale-days (60) old.
VISIBLE = VISIBLE.replace("__TODAY__", date.today().isoformat())

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
    # --min-coverage 0: these fixtures are deliberately low-coverage corpora and the
    # tests are about the denominators. The floor has its own tests at the end.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(memory_dir),
         "--repo-root", str(repo_root), "--mode", "interactive", "--dry-run",
         "--min-coverage", "0"],
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


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import scan_promotion_candidates as sp  # noqa: E402


# ---------------------------------------------------------------- frozen shards (2026-08-25)
# The ~24KB shard budget existed to keep a file small enough to be READ. The shards were
# frozen after measuring effectively zero consultations in ordinary sessions across 1229
# transcripts, so their size stopped being a tax. Counting them kept the headline number
# tracking a budget nobody was bound by, which buried CLAUDE.md -- the one file whose bytes
# still cost something every session.

def _oversized_shard(tmp_path, name, frozen: bool):
    banner = ("# Title\n\n> **FROZEN 2026-08-25 -- ARCHIVED, NOT MAINTAINED.**\n\n"
              if frozen else "# Title\n\n")
    (tmp_path / name).write_text(banner + ("x" * 40000), encoding="utf-8")


def test_a_frozen_shard_is_exempt_from_the_byte_budget(tmp_path):
    _oversized_shard(tmp_path, "index-frozen.md", frozen=True)
    out = sp.oversized_context_files(tmp_path, tmp_path)
    assert [d["file"] for d in out] == [], (
        "a frozen shard is not loaded, so its size is not a per-session tax"
    )


def test_an_UNfrozen_oversized_shard_is_still_reported(tmp_path):
    _oversized_shard(tmp_path, "index-live.md", frozen=False)
    out = sp.oversized_context_files(tmp_path, tmp_path)
    assert [d["file"] for d in out] == ["index-live.md"], (
        "the exemption must key on the banner, not silence the budget for every shard"
    )


def test_the_exemption_does_not_leak_to_MEMORY_md(tmp_path):
    """MEMORY.md is the only channel measured to reach the model; it is never exempt."""
    (tmp_path / "MEMORY.md").write_text(
        "# M\n\n> **FROZEN 2026-08-25 -- ARCHIVED, NOT MAINTAINED.**\n\n" + "x" * 40000,
        encoding="utf-8")
    out = sp.oversized_context_files(tmp_path, tmp_path)
    assert [d["file"] for d in out] == ["MEMORY.md"]


def test_is_frozen_shard_reads_only_the_head(tmp_path):
    """A banner buried past the head must not exempt the file, or any shard that ever
    quoted the banner text in a body entry would silently drop out of the budget."""
    p = tmp_path / "index-x.md"
    p.write_text("# Title\n\n" + ("y" * 5000) + "\nARCHIVED, NOT MAINTAINED\n", encoding="utf-8")
    assert sp.is_frozen_shard(p) is False


def test_is_frozen_shard_on_an_unreadable_path_is_false(tmp_path):
    assert sp.is_frozen_shard(tmp_path / "absent.md") is False


def test_a_shard_UNDER_budget_is_not_reported(tmp_path):
    """Kills the mutant that forces `size > limit` true: without this, the budget
    comparison itself can be made a no-op and every file reports as oversized."""
    (tmp_path / "index-small.md").write_text("# Title\n\nshort\n", encoding="utf-8")
    assert sp.oversized_context_files(tmp_path, tmp_path) == []


def test_the_report_carries_the_overage_arithmetic(tmp_path):
    (tmp_path / "index-live.md").write_text("# T\n\n" + "x" * 40000, encoding="utf-8")
    row = sp.oversized_context_files(tmp_path, tmp_path)[0]
    assert row["bytes"] == row["limit"] + row["over_by"]
    assert row["ratio"] == round(row["bytes"] / row["limit"], 2)


def test_CLAUDE_md_is_measured_against_its_own_larger_budget(tmp_path):
    """CLAUDE.md is always-loaded and carries a different limit than a shard. Without this,
    the line that registers it as a target can be deleted with the suite green -- and it is
    the only file the budget signal currently fires on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("x" * (sp.CLAUDE_MD_LIMIT_BYTES + 100), encoding="utf-8")
    out = sp.oversized_context_files(tmp_path, repo)
    assert [d["file"] for d in out] == ["CLAUDE.md"]
    assert out[0]["limit"] == sp.CLAUDE_MD_LIMIT_BYTES
    assert out[0]["over_by"] == 100


def test_a_CLAUDE_md_under_its_budget_is_not_reported(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("x" * (sp.CLAUDE_MD_LIMIT_BYTES - 1), encoding="utf-8")
    assert sp.oversized_context_files(tmp_path, repo) == []


# ---------------------------------------------------------------------------
# is_promoted(): a value that EXPLAINS why a rule is unpromoted must still
# count as unpromoted.
#
# ORIGIN 2026-08-28. `is_promoted` returned `v not in ("no", "false", "", "0")`,
# so it treated any annotated value as a promotion tier. The 2026-08-25
# adversarial re-verification run wrote `promoted: "no -- CORRECTED ..."` onto
# rules it had just REFUTED, and every one of them silently left the backlog:
# 11 rules at >=2 fires, three of them at 5, including
# feedback_verify_the_surface_fires_before_anchoring_to_it -- whose own subject
# is mechanisms that report as landed while being dead.
#
# This is the same defect the `partial` branch was added to fix on 2026-08-13,
# in a different value shape. Writing down WHY a rule is unpromoted was the act
# that hid it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "no",
    "no -- CORRECTED 2026-08-25 by adversarial re-verification, which refuted the claim",
    "no -- gate met 2026-08-17, promotion recommended, pending Nick's decision",
    "No -- capitalised, because frontmatter is written by hand",
    "not promoted -- the surface it would land on never fires",
    "false",
    "",
    "0",
    "partial -- only the sweep half landed",
])
def test_values_meaning_unpromoted_are_not_counted_as_promoted(value):
    assert sp.is_promoted({"promoted": value}) is False, (
        f"{value!r} means UNPROMOTED; counting it as promoted drops the rule "
        "from the backlog while it is still accumulating fires"
    )


@pytest.mark.parametrize("value", [
    "yes -- CLAUDE.md Hard Rule",
    "yes",
    "skill",
    "hook",
    "principle",
    "hard-rule",
])
def test_real_promotion_tiers_still_count_as_promoted(value):
    assert sp.is_promoted({"promoted": value}) is True, (
        f"{value!r} is a real promotion tier; a rule that IS enforced must leave "
        "the backlog or the list fills with noise"
    )


def test_missing_promoted_key_defaults_to_unpromoted():
    """Absent means nobody has promoted it, which is the safe direction."""
    assert sp.is_promoted({}) is False


def test_an_annotated_no_still_reaches_the_candidate_list(tmp_path):
    """End-to-end guard, not just the predicate.

    A unit test on is_promoted can pass while the caller filters the file out
    some other way. This asserts the rule actually arrives in the output.
    """
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "feedback_annotated_no.md").write_text(
        "---\n"
        "name: feedback_annotated_no\n"
        "description: a refuted rule that still carries fires\n"
        "metadata:\n"
        "  node_type: memory\n"
        "  type: feedback\n"
        "  occurrences: 3\n"
        '  promoted: "no -- CORRECTED 2026-08-25, the claim was refuted"\n'
        '  reopen_gate: "3rd fire"\n'
        "  last_cited: 2026-08-25\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    out = json.loads(subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(mem), "--dry-run"],
        capture_output=True, text=True, check=True).stdout)
    names = [c["file"] for c in out["promotion_candidates"]]
    assert "feedback_annotated_no.md" in names, (
        f"annotated-no rule missing from candidates: {names}"
    )


# ---------------------------------------------------- due by the rule's own gate (2026-10-13)
# "Fired twice and not promoted" listed 186 rules when 99 were due by their own gates.

@pytest.mark.parametrize("gate,occ,expected", [
    ("3rd fire. Target: a hook", 3, "due"),
    ("3rd fire. Target: a hook", 4, "due"),
    ("3rd fire. Target: a hook", 2, "not_due"),
    ("5th fire, OR any single session with 2+ fires", 4, "not_due"),
    ("2nd time a company is found", 2, "due"),
    ("3rd occurrence of the same shape", 2, "not_due"),
    ("REOPEN on the 2nd dated fire", 2, "due"),
    ("10th fire", 9, "not_due"),
    ("10th fire", 10, "due"),
    # the rule file says so itself, whatever the count
    ("TRIPPED 2026-09-14 by fire 2 on a narrower substrate", 2, "due"),
    ("GATE TRIPPED 2026-09-06; guard OWED, not built", 2, "due"),
    ("REOPENED 2026-09-17 (2nd dated fire)", 2, "due"),
    ("the gate tripped once in lowercase prose, 5th fire", 2, "not_due"),
    # no number: a defect in the gate, not a guess
    ("count verified 2026-08-13. No structural gate set", 6, "no_number"),
    ("the next dated fire", 2, "no_number"),
    ("fire 3 of this shape", 5, "no_number"),
    ("", 2, "no_number"),
    (None, 2, "no_number"),
])
def test_gate_state(gate, occ, expected):
    assert sp.gate_state(gate, occ) == expected


RULE = """---
name: feedback_{name}
description: a rule
metadata:
  node_type: memory
  type: feedback
  occurrences: {occ}
  promoted: no
  reopen_gate: "{gate}"
  last_cited: __TODAY__
---
body
""".replace("__TODAY__", date.today().isoformat())


def _gated_corpus(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    for name, occ, gate in (("due_a", 3, "3rd fire"), ("due_b", 2, "TRIPPED 2026-09-14"),
                            ("early", 2, "5th fire"), ("vague", 4, "the next dated fire"),
                            ("once", 1, "2nd fire")):
        (d / f"feedback_{name}.md").write_text(RULE.format(name=name, occ=occ, gate=gate))
    return d


def _scan(memory_dir, repo_root, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(memory_dir), "--repo-root",
         str(repo_root), "--dry-run", *extra], capture_output=True, text=True)


def test_candidates_are_counted_by_their_own_gate(tmp_path):
    report = run(_gated_corpus(tmp_path), tmp_path)
    assert report["promotion_count"] == 4, "the total still means fired twice, not promoted"
    assert (report["due_count"], report["not_due_count"],
            report["gate_no_number_count"]) == (2, 1, 1)
    assert {c["file"]: c["gate_state"] for c in report["promotion_candidates"]} == {
        "feedback_due_a.md": "due", "feedback_due_b.md": "due",
        "feedback_early.md": "not_due", "feedback_vague.md": "no_number"}


def test_the_backlog_is_written_as_three_lists(tmp_path):
    d = _gated_corpus(tmp_path)
    r = subprocess.run([sys.executable, str(SCRIPT), "--memory-dir", str(d), "--repo-root",
                        str(tmp_path), "--mode", "cron"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (d / "promotion-backlog.md").read_text(encoding="utf-8")
    due, rest = text.split("## Gate needs a number", 1)
    vague, rest = rest.split("## Not due yet", 1)
    early = rest.split("## Demotion candidates", 1)[0]
    assert "## Due for promotion" in due
    assert "feedback_due_a.md" in due and "feedback_due_b.md" in due
    assert "feedback_early.md" not in due and "feedback_vague.md" not in due
    assert "feedback_vague.md" in vague and "feedback_due_a.md" not in vague
    assert "feedback_early.md" in early and "(2x) -- gate: 5th fire" in early
    assert "feedback_once.md" not in text
    assert "LOW COVERAGE" not in text
    assert "\n\n\n" not in text, "one blank line between sections, never two"


def test_an_empty_list_says_none(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_due_a.md").write_text(RULE.format(name="due_a", occ=3, gate="3rd fire"))
    subprocess.run([sys.executable, str(SCRIPT), "--memory-dir", str(d), "--repo-root",
                    str(tmp_path), "--mode", "cron"], capture_output=True, text=True)
    text = (d / "promotion-backlog.md").read_text(encoding="utf-8")
    assert text.split("## Gate needs a number", 1)[1].split("## Not due yet", 1)[0].strip().endswith("_none_")
    assert text.split("## Not due yet", 1)[1].split("## Demotion", 1)[0].strip().endswith("_none_")


# ---------------------------------------------------- the coverage floor

@pytest.mark.parametrize("mode", ["interactive", "cron"])
def test_a_scan_below_the_floor_is_exit_3_and_says_low_coverage(tmp_path, mode):
    d = build_corpus(tmp_path, visible=3, invisible=27, projects=0)
    r = _scan(d, tmp_path, "--mode", mode)
    assert r.returncode == 3
    report = json.loads(r.stdout)
    assert report["status"] == "LOW_COVERAGE" and report["floor_pct"] == 95.0
    assert "10.0% of feedback rules (3 of 30)" in report["reason"]


def test_the_floor_is_inclusive_and_can_be_moved(tmp_path):
    d = build_corpus(tmp_path, visible=19, invisible=1, projects=0)      # exactly 95.0%
    r = _scan(d, tmp_path, "--mode", "interactive")
    assert r.returncode == 0 and json.loads(r.stdout)["status"] == "ok"
    r = _scan(d, tmp_path, "--mode", "interactive", "--min-coverage", "96")
    assert r.returncode == 3 and json.loads(r.stdout)["status"] == "LOW_COVERAGE"


def test_a_directory_with_no_feedback_rules_is_not_failed_for_coverage(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    r = _scan(d, tmp_path, "--mode", "interactive")
    assert r.returncode == 0 and json.loads(r.stdout)["status"] == "ok"


def test_a_low_coverage_backlog_says_so_at_the_top(tmp_path):
    d = build_corpus(tmp_path, visible=3, invisible=27, projects=0)
    r = subprocess.run([sys.executable, str(SCRIPT), "--memory-dir", str(d), "--repo-root",
                        str(tmp_path), "--mode", "cron"], capture_output=True, text=True)
    assert r.returncode == 3
    text = (d / "promotion-backlog.md").read_text(encoding="utf-8")
    assert "**LOW COVERAGE: the scan can see 10.0% of feedback rules" in text
    assert text.index("LOW COVERAGE") < text.index("## Due for promotion")


def test_an_unreadable_rule_file_counts_against_coverage(tmp_path):
    """Review 309: a file that could not be read left the denominator, so an unreadable
    corpus could pass the floor."""
    d = build_corpus(tmp_path, visible=19, invisible=0, projects=0)
    (d / "feedback_undecodable_a.md").write_bytes(b"\xff\xfe")
    r = _scan(d, tmp_path, "--mode", "interactive")
    cov = json.loads(r.stdout)["schema_coverage"]
    assert (cov["feedback_rules"], cov["feedback_visible"], cov["feedback_pct"]) == (20, 19, 95.0)
    assert r.returncode == 0
    (d / "feedback_undecodable_b.md").write_bytes(b"\xff\xfe")
    r = _scan(d, tmp_path, "--mode", "interactive")
    assert r.returncode == 3 and json.loads(r.stdout)["status"] == "LOW_COVERAGE"
