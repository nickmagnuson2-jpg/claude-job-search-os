"""No test file may live where the suite cannot collect it.

THE FAILURE THIS PREVENTS. `tools/test_schema_guard.py` is 148 lines of real regression
coverage, written for the 2026-06-08 column-drift incident, sitting in `tools/` -- which the
suite does not collect and `mutation_check.map_tests` does not glob. It ran only when
someone remembered to invoke it by hand, which is to say it stopped running in June and
nobody noticed. Meanwhile `schema_guard.py` reported 18 of 26 mutants surviving, a number
computed against unrelated files, and the module read as "badly tested" when the truth was
"well tested, in a directory nothing looks at."

Discovered 2026-09-02 by scanning the repo: 121 test functions across `tools/` and
`tools/career_scanner/` had never executed. (The first count said 38 -- it anchored
`def test_` at column 0 and missed every class-based test. Same defect this file guards.) Coverage that does not run is worse than no
coverage, because it looks like coverage in every count that matters.

TWO SHAPES, both caught here:
  1. a pytest file outside `tests/` -- collectible, but nothing collects it;
  2. a `test_*.py` that is a standalone assert script with its own PASS/FAIL counters and a
     `sys.exit()`. pytest reports `no tests ran` on those, so even pointing the runner at
     them is not enough -- they have to be ported.

KNOWN_ORPHANS is frozen debt with a per-entry disposition, not permission. Deleting an entry
when the file is ported is the point.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# rsync backup copies of the live memory tier, and vendored environments. Searching them
# double-counts every hit; CLAUDE.md forbids treating them as sources.
# `output/` and `data/` are gitignored by design (this repo is public) and hold generated
# artifacts, not repo infrastructure -- a test file under either is case material, never a
# suite this runner should collect. Excluded structurally rather than allowlisted by path,
# because an allowlist entry would have to NAME the directory, and those names are exactly
# the real-company slugs that must not appear in a public file.
PRUNED = (".git", ".venv", "node_modules", "__pycache__",
          "output/", "data/",
          "memory/global-claude-mirror", "memory/canonical-sidecar")

KNOWN_ORPHANS = {
    "tools/test_schema_guard.py":
        "script-style; ported to tests/scripts/test_schema_guard.py 2026-09-02, "
        "original retained until its callers are checked",
    "tools/test_scan_transcript_failures.py":
        "superseded by tests/scripts/test_scan_transcript_failures.py 2026-09-02",
    "tools/test_agent_core.py": "4 pytest tests, never collected -- port pending",
    "tools/test_agent_collect.py": "4 pytest tests, never collected -- port pending",
    "tools/test_act_classify_stale.py": "script-style, never collected -- port pending",
    "tools/test_call_analyzer.py": "21 pytest tests, never collected -- port pending",
    "tools/test_dossier_freshness_target.py": "script-style, never collected -- port pending",
    "tools/career_scanner/test_company_scorer.py": "23 pytest tests, never collected -- port pending",
    "tools/career_scanner/test_scorer.py": "28 pytest tests, never collected -- port pending",
    "tools/career_scanner/test_dedup.py": "16 pytest tests in classes, never collected -- port pending",
    "tools/career_scanner/test_parsers.py": "24 pytest tests, never collected -- port pending",
}


def _pruned(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return any(p in rel for p in PRUNED)


def orphaned_test_files() -> list[str]:
    """Every `test_*.py` outside tests/, excluding backups and vendored trees."""
    out = []
    for p in REPO_ROOT.rglob("test_*.py"):
        if _pruned(p):
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("tests/"):
            continue
        out.append(rel)
    return sorted(out)


def test_no_new_test_file_lands_outside_the_suite():
    new = [f for f in orphaned_test_files() if f not in KNOWN_ORPHANS]
    assert not new, (
        f"test file(s) outside tests/ that nothing collects: {new}. A suite the runner "
        "never sees is not coverage -- it reads as coverage in every count while running "
        "zero times. Move it under tests/scripts/, or add it to KNOWN_ORPHANS with a "
        "disposition.")


def test_known_orphans_are_retired_as_they_are_ported():
    stale = sorted(f for f in KNOWN_ORPHANS if not (REPO_ROOT / f).exists())
    assert not stale, (
        f"{stale} no longer exist -- delete them from KNOWN_ORPHANS so the entry cannot "
        "excuse a future regression.")


def test_every_known_orphan_carries_a_disposition():
    blank = sorted(k for k, v in KNOWN_ORPHANS.items() if not str(v).strip())
    assert not blank, f"KNOWN_ORPHANS entries with no disposition: {blank}"


def test_the_disposition_check_can_actually_fail():
    """Asserted against synthetic input: every real entry has a disposition, so the check
    above passes no matter what its predicate does."""
    pred = lambda m: sorted(k for k, v in m.items() if not str(v).strip())
    assert pred({"a": "why"}) == []
    assert pred({"a": "", "b": "why", "c": "  "}) == ["a", "c"]


def test_the_scan_actually_found_files():
    """Every assertion here is 'nothing bad in this list', so an empty list satisfies them
    all. If the glob or the prune breaks, this fails first instead of going quietly green."""
    found = orphaned_test_files()
    assert len(found) >= 5, (
        f"only {len(found)} orphan(s) found; the repo had 11 tracked ones on 2026-09-02. A low count "
        "means the scan broke, not that the debt was paid.")
    assert (REPO_ROOT / "tests" / "scripts").is_dir()


def test_the_suite_directory_is_where_collected_tests_live():
    """Guards the premise: if tests/ stopped being the collected root, every conclusion
    in this file inverts."""
    collected = list((REPO_ROOT / "tests" / "scripts").glob("test_*.py"))
    assert len(collected) > 50, f"only {len(collected)} suite files found under tests/scripts"


def test_script_style_orphans_are_named_as_such():
    """A `test_*.py` with no `def test_` is not merely misplaced -- pointing pytest at it
    reports `no tests ran`. Porting is required, so the disposition must say so."""
    for rel, why in KNOWN_ORPHANS.items():
        p = REPO_ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        # `^\s*def test_`, not `^def test_`: pytest collects class-based tests too, and
        # anchoring at column 0 misread tools/career_scanner/test_dedup.py -- 16 real
        # methods inside classes -- as having no tests at all.
        if not re.search(r"^\s*def test_", text, re.M):
            assert "script-style" in why or "not repo infrastructure" in why, (
                f"{rel} has no test functions; its disposition must say script-style so a "
                f"reader does not assume moving the file is enough. Got: {why!r}")
