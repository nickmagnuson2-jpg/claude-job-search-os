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

KNOWN_ORPHANS: dict[str, str] = {
    # Emptied 2026-09-02: all 12 entries were ported into tests/scripts/ and the originals
    # deleted. 129 test functions that had never executed now run on every suite invocation.
    # An entry added here must carry a reason, and must be removed the day it is ported.
}


def _pruned(path: Path, root: Path = REPO_ROOT) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(p in rel for p in PRUNED)


def orphaned_test_files(root: Path = REPO_ROOT) -> list[str]:
    """Every `test_*.py` outside tests/, excluding backups and vendored trees.

    `root` is a test seam, mirroring the PII_REPO_ROOT seam in check_public_pii.py. The
    allowlist is empty now, so "found nothing" is the passing state and a broken glob here
    is indistinguishable from success -- the seam is what lets the suite prove this
    function still FINDS things, against a synthetic tree instead of the live one.
    """
    out = []
    for p in root.rglob("test_*.py"):
        if _pruned(p, root):
            continue
        rel = p.relative_to(root).as_posix()
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


def blank_dispositions(mapping: dict[str, str]) -> list[str]:
    """Entries with a missing or whitespace disposition. Module-level so it is testable."""
    return sorted(k for k, v in mapping.items() if not str(v).strip())


def test_every_known_orphan_carries_a_disposition():
    assert not blank_dispositions(KNOWN_ORPHANS), (
        f"entries with no disposition: {blank_dispositions(KNOWN_ORPHANS)}")


def test_the_disposition_check_can_actually_fail():
    """Drives the REAL predicate against synthetic input. KNOWN_ORPHANS is empty, so the
    assertion above is satisfied no matter what the predicate does -- an inline lambda here
    tested a copy and left the real one unobserved (mutation-caught 2026-09-02)."""
    assert blank_dispositions({}) == []
    assert blank_dispositions({"a": "why"}) == []
    assert blank_dispositions({"a": "", "b": "why", "c": "  "}) == ["a", "c"]


def test_the_scan_machinery_is_alive():
    """The list is empty now, so 'no orphans found' is the PASSING state -- which means
    every other assertion in this file is satisfied by a scan that returns nothing at all.
    A broken rglob, a wrong REPO_ROOT, or an over-broad prune would look identical to
    success. This asserts the machinery still finds files, using the tree it prunes:
    if the walk works, it must see the suite's own test files.

    (Written 2026-09-02 replacing a `len(found) >= 5` floor, which stopped meaning anything
    the moment the debt was paid -- and whose first replacement, `len(found) == 0 or ...`,
    was vacuous in exactly the way this file exists to catch.)
    """
    all_found = [q for q in REPO_ROOT.rglob("test_*.py") if not _pruned(q)]
    assert len(all_found) > 50, (
        f"the walk found only {len(all_found)} test files anywhere; it should see the whole "
        "suite. A low count means the scan broke, not that the repo is small -- and every "
        "other assertion here passes vacuously when it does.")
    in_suite = [q for q in all_found
                if q.relative_to(REPO_ROOT).as_posix().startswith("tests/")]
    assert len(in_suite) > 50, "the walk cannot see tests/, so it cannot see anything else"
    assert (REPO_ROOT / "tests" / "scripts").is_dir()


def test_the_scanner_itself_still_finds_an_orphan(tmp_path):
    """Drives the REAL function against a synthetic tree containing one planted orphan.

    Without this, breaking the glob inside `orphaned_test_files` leaves every assertion in
    this file green, because an empty result satisfies all of them. Caught by mutation on
    2026-09-02: the earlier vacuity guard ran its own rglob and missed exactly that.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests" / "scripts").mkdir(parents=True)
    (tmp_path / "tools" / "test_planted.py").write_text("def test_x(): pass\n")
    (tmp_path / "tests" / "scripts" / "test_collected.py").write_text("def test_y(): pass\n")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "test_ignored.py").write_text("def test_z(): pass\n")

    (tmp_path / "aaa").mkdir()
    (tmp_path / "aaa" / "test_first.py").write_text("def test_a(): pass\n")

    found = orphaned_test_files(tmp_path)
    assert found == ["aaa/test_first.py", "tools/test_planted.py"], (
        "results must come back sorted -- an unstable order makes the failure message "
        "reshuffle between runs and turns a real diff into noise. "
        
        "The scanner must find test files outside tests/, must NOT flag one inside it, "
        f"and must prune output/. Got: {found}")


def test_the_prune_list_does_not_swallow_the_repo():
    """An over-broad prune is the other way this goes quietly green."""
    assert not _pruned(REPO_ROOT / "tools" / "example.py")
    assert not _pruned(REPO_ROOT / "tests" / "scripts" / "test_x.py")
    assert _pruned(REPO_ROOT / "output" / "co" / "test_x.py")
    assert _pruned(REPO_ROOT / "memory" / "global-claude-mirror" / "test_x.py")


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
