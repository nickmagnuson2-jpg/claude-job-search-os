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

DATE GATE — LIFTED 2026-08-13. The 383 legacy files were backfilled by
`tools/backfill_memory_schema.py` (Phase 1), so `ENFORCE_FROM` is now None and the whole
corpus is enforced. Coverage at the lift: 403/403 feedback rules carry the three keys.

WHY `last_cited` IS NOT REQUIRED. It is stamped by the `memory-last-cited-stamp` PostToolUse
hook on a genuine Read — never authored, never backfilled. Seeding it would have faked either
freshness (today) or staleness (mtime); the backfill omitted it on purpose, so requiring it
here would demand a fabricated value. When present it must still be a valid ISO date, since a
malformed one is silently skipped by the demotion signal.

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

# None = enforce corpus-wide. Was date(2026, 8, 13) until the Phase 1 backfill landed.
ENFORCE_FROM = None

# The keys an AUTHOR (or the backfill) writes. `last_cited` is hook-stamped and is
# deliberately absent from most of the corpus — see the module docstring.
REQUIRED_KEYS = ("occurrences", "promoted", "reopen_gate")

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


def test_last_cited_when_present_is_a_valid_iso_date():
    """A malformed date is swallowed by the demotion signal's except-and-skip."""
    files = _feedback_files()
    if not files:
        pytest.skip("memory dir absent — Tier 2 only")

    bad = []
    for path in files:
        fm = _frontmatter(path)
        if fm is None:
            continue
        m = re.search(r"^\s*last_cited:\s*(.+)$", fm, re.M)
        if not m:
            continue  # hook has not stamped it yet — expected for most of the corpus
        raw = m.group(1).strip().strip("'\"")
        try:
            date.fromisoformat(raw)
        except ValueError:
            bad.append(f"{path.name}: last_cited={raw!r}")
    assert not bad, "last_cited must be YYYY-MM-DD:\n  " + "\n  ".join(bad)


def test_coverage_is_reported_even_when_green():
    """Not an assertion on the backlog — a visible number so 5% cannot hide behind a pass."""
    files = _feedback_files()
    if not files:
        pytest.skip("memory dir absent — Tier 2 only")
    have = [p for p in files if (fm := _frontmatter(p)) and re.search(r"^\s*occurrences:", fm, re.M)]
    pct = 100 * len(have) / len(files)
    print(f"\n[memory schema coverage] {len(have)}/{len(files)} ({pct:.0f}%) carry occurrences:")
    assert len(files) > 0
