"""Memory feedback files must carry the keys the promotion detector actually reads.

`tools/scan_promotion_candidates.py` surfaces a rule when `occurrences >= 2 AND promoted: no`.
A file without those keys is invisible to it — the rule accumulates fires while reporting as a
first-timer, and no promotion ever surfaces. Prose (a `## Promotion criterion` section) does not
register; only frontmatter does.

WHY THIS TEST EXISTS. On 2026-08-13 the corpus measured 403 `feedback_*.md` files with **19**
carrying `occurrences:` — 5% coverage, so the detector returned 1 candidate corpus-wide and that
read as an all-clear. Root cause was a doc contradiction: `CLAUDE.md`'s Self-Improvement Loop
Step 1 prescribed `name`/`description`/`metadata.type` only, while the `lessons-learned` skill
required the full schema. The more commonly-followed authority emitted the invisible variant.
Both were fixed 2026-08-13.

DATE GATE. 384 legacy files predate the fix and are backfilled in Phase 1 of
`output/analysis/081326-memory-hygiene-project.md`. Enforcing on them today would ship a red
suite, which gets muted rather than fixed. So this asserts on files whose frontmatter is
*newer than the cutoff* — new captures cannot regress — and the cutoff drops to None once
backfill lands, at which point the whole corpus is enforced.

The memory dir is OUTSIDE the repo and is gitignored, so on a clean clone this SKIPS loudly
rather than passing vacuously.
"""
import os
import re
from datetime import date
from pathlib import Path

import pytest

MEMORY_DIR = Path(
    os.path.expanduser(
        "~/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory"
    )
)

# Files whose `last_cited` is on/after this date must carry the full schema.
# Phase 1 backfill -> set to None to enforce corpus-wide.
ENFORCE_FROM = date(2026, 8, 13)

REQUIRED_KEYS = ("occurrences", "promoted", "reopen_gate", "last_cited")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.S)
_LAST_CITED_RE = re.compile(r"^\s*last_cited:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.M)


def _feedback_files() -> list[Path]:
    if not MEMORY_DIR.is_dir():
        return []
    return sorted(MEMORY_DIR.glob("feedback_*.md"))


def _frontmatter(path: Path) -> str | None:
    m = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else None


def _in_scope(fm: str) -> bool:
    """A file is in scope when its last_cited is on/after the cutoff."""
    if ENFORCE_FROM is None:
        return True
    m = _LAST_CITED_RE.search(fm)
    if not m:
        return False  # no last_cited at all -> legacy, Phase 1 backfill territory
    try:
        return date.fromisoformat(m.group(1)) >= ENFORCE_FROM
    except ValueError:
        return False


def test_memory_dir_present_or_skip_loudly():
    if not MEMORY_DIR.is_dir():
        pytest.skip(f"SKIPPED: {MEMORY_DIR} absent (outside repo, gitignored) — Tier 2 only")
    assert _feedback_files(), f"{MEMORY_DIR} has no feedback_*.md — did the glob break?"


def test_in_scope_feedback_files_carry_the_detector_keys():
    files = _feedback_files()
    if not files:
        pytest.skip("memory dir absent — Tier 2 only")

    offenders = []
    checked = 0
    for path in files:
        fm = _frontmatter(path)
        if fm is None or not _in_scope(fm):
            continue
        checked += 1
        missing = [k for k in REQUIRED_KEYS if not re.search(rf"^\s*{k}:", fm, re.M)]
        if missing:
            offenders.append(f"{path.name}: missing {missing}")

    # A zero-file check is a vacuous pass, not evidence. Today's own captures are in scope,
    # so this also fails if the scope predicate silently stops matching anything.
    assert checked > 0, (
        f"scoped ZERO files (cutoff {ENFORCE_FROM}) — the scope predicate matched nothing, "
        "which is a broken check, not a clean corpus"
    )
    assert not offenders, (
        f"{len(offenders)} of {checked} in-scope memory file(s) are invisible to "
        f"scan_promotion_candidates.py:\n  " + "\n  ".join(offenders)
    )


def test_occurrences_is_an_integer_when_present():
    """A quoted or non-numeric occurrences silently breaks the >= 2 comparison."""
    files = _feedback_files()
    if not files:
        pytest.skip("memory dir absent — Tier 2 only")

    bad = []
    for path in files:
        fm = _frontmatter(path)
        if fm is None:
            continue
        m = re.search(r"^\s*occurrences:\s*(.+)$", fm, re.M)
        if m and not re.fullmatch(r"\d+", m.group(1).strip()):
            bad.append(f"{path.name}: occurrences={m.group(1).strip()!r}")
    assert not bad, "occurrences must be a bare integer:\n  " + "\n  ".join(bad)


def test_coverage_is_reported_even_when_green():
    """Not an assertion on the backlog — a visible number so 5% cannot hide behind a pass."""
    files = _feedback_files()
    if not files:
        pytest.skip("memory dir absent — Tier 2 only")
    have = [p for p in files if (fm := _frontmatter(p)) and re.search(r"^\s*occurrences:", fm, re.M)]
    pct = 100 * len(have) / len(files)
    print(f"\n[memory schema coverage] {len(have)}/{len(files)} ({pct:.0f}%) carry occurrences:")
    assert len(files) > 0
