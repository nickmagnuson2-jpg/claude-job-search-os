"""Every handoff-shaped file under `output/` carries a valid DISPOSITION banner.

WHY THIS EXISTS. The producer gate (`tools/check_handoff_producer.py`) stops a 22nd handoff
being born. It cannot retire the 21 that already exist, and it cannot see a file that arrives
by a route it does not intercept -- a programmatic writer, an editor outside this session, a
git operation. This is the defence in depth: whatever lands, its lifecycle is declared.

WHAT A BANNER BUYS. The 2026-09-08 triage found that "superseded" has two meanings and that
most of these documents are dead because THE WORK LANDED, not because a later document
replaced them -- so a reader who finds one cannot tell whether to trust it. The banner records
the disposition, which of the two kinds it is, and the canonical successor, which is what
makes the file non-authoritative rather than merely old.

IT IMPORTS `is_handoff_shaped` RATHER THAN RESTATING THE GLOB. Cross-model review (F4,
2026-09-15) caught the hook governing all of `output/` while this test enumerated only
`output/analysis/`: a bypassed file elsewhere under `output/` would then be inside the
producer rule and outside the inventory. One predicate, imported, cannot drift from itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from check_handoff_producer import (  # noqa: E402
    banner_errors,
    is_handoff_shaped,
    parse_banner,
)

OUTPUT = REPO_ROOT / "output"


def handoff_files() -> list[Path]:
    """Every handoff-shaped markdown file under `output/`, recursively.

    `output/` is gitignored by design (this is a public repo), so on a fresh clone it is
    absent and there is genuinely nothing to check. ABSENT and UNREADABLE are kept distinct:
    a directory we cannot traverse is a failure, not a clean run, because collapsing the two
    is how a check reports green on a corpus it never read.
    """
    if not OUTPUT.exists():
        pytest.skip("output/ is gitignored and absent in this checkout")
    if not OUTPUT.is_dir():
        pytest.fail(f"{OUTPUT} exists but is not a directory")
    try:
        candidates = [p for p in OUTPUT.rglob("*.md") if p.is_file()]
    except OSError as exc:
        pytest.fail(f"output/ could not be traversed, so nothing was checked: {exc}")
    return sorted(
        p for p in candidates
        if is_handoff_shaped(p.relative_to(REPO_ROOT).as_posix()))


def test_every_handoff_declares_its_disposition():
    files = handoff_files()
    bad = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            bad[p.name] = [f"unreadable: {exc}"]
            continue
        errs = banner_errors(parse_banner(text))
        if errs:
            bad[p.name] = errs
    assert not bad, (
        "handoff-shaped files with no valid DISPOSITION banner:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(bad.items()))
        + "\n\nA handoff without a disposition is a second plausible source of truth. Stamp "
          "it:\n"
          '  <!-- DISPOSITION: {"status":"superseded","kind":"work-landed",'
          '"successor":"data/workstreams/<name>.md","date":"YYYY-MM-DD"} -->\n'
          "status: superseded | absorbed | reference | live. `kind` is required when "
          "superseded (work-landed = it is dead because the work got done; replaced-by-doc = "
          "a later document replaced it). `successor` is required unless it is a reference "
          "record retained for its own sake.")


def test_every_named_successor_exists():
    """A successor pointer to a file that is not there is a dead end, not a redirect."""
    missing = {}
    for p in handoff_files():
        banner = parse_banner(p.read_text(encoding="utf-8", errors="replace"))
        if not banner:
            continue  # the test above owns that failure
        successor = banner.get("successor")
        if successor and not (REPO_ROOT / successor).exists():
            missing[p.name] = successor
    assert not missing, (
        "DISPOSITION banners naming a successor that does not exist:\n"
        + "\n".join(f"  {k} -> {v}" for k, v in sorted(missing.items())))


def test_the_corpus_is_actually_being_read():
    """Every assertion above is satisfied by an empty list. If the predicate stops matching
    or the path moves, this fails instead of reporting a clean corpus that was never read."""
    files = handoff_files()
    assert len(files) >= 15, (
        f"only {len(files)} handoff-shaped files found under {OUTPUT}; the corpus held 21 on "
        "2026-09-15. A sharp drop means the predicate or the path stopped matching, not that "
        "the sprawl resolved itself.")
