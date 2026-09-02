"""STOPWORDS must never suppress a real company name.

Origin 2026-08-19 (/audit-pii semantic pass): one entry in `STOPWORDS` was a real
pipeline company, present in both job-pipeline.md and networking.md, while the set's
own docstring promises it "deliberately holds NO real company names." Because
`is_distinctive_single()` rejects any STOPWORDS member, that token landed in NEITHER
the block denylist nor the ambiguous tier -- so the always-on PreToolUse hook would
not have stopped the company's name reaching a public artifact.

One line in a stopword list silently disarmed the guard for a live pipeline company.
The deterministic layer could not detect it BY CONSTRUCTION: the hole was in the
deterministic layer itself. This test turns the docstring's promise into a check, per
the CLAUDE.md enforcement-tier rule (a rule is real only if a gate reads it).

TWO DELIBERATE CHOICES ABOUT DISCLOSURE, both learned the hard way on the day:
  1. The offending token is pinned by SHA-256, not spelled out. The first draft of the
     fix wrote the literal into a code comment and immediately tripped the very hook it
     had just repaired -- documenting a leak must not re-commit it.
  2. Failures report only a COUNT and the source file, never the token. This test reads
     gitignored private data; printing a match would move a real company name out of
     private data and into a CI log.
"""
import hashlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import gen_pii_denylist as g  # noqa: E402

PIPELINE = REPO_ROOT / "data" / "job-pipeline.md"
NETWORKING = REPO_ROOT / "data" / "networking.md"
KNOWN_OFFENDER_SHA256 = "cfb12585da56e4c0e3e189f24a8418f08108e5a40a84d7aafc504c704e21512c"


def _entities(path: Path, parser) -> set:
    if not path.exists():
        pytest.skip(f"{path.name} absent (fresh clone / CI) — nothing to guard against")
    return {c.strip().casefold() for c in parser(path.read_text(encoding="utf-8")) if c.strip()}


def test_stopwords_contains_no_real_pipeline_company():
    offenders = {s for s in g.STOPWORDS if s.casefold() in _entities(PIPELINE, g.parse_pipeline_companies)}
    assert not offenders, (
        f"{len(offenders)} STOPWORDS entr(y/ies) match a real company in job-pipeline.md. "
        "Each is suppressed from BOTH denylist tiers, so the hook cannot block it. "
        "Remove it and regenerate. Token withheld: printing it would leak private data."
    )


def test_stopwords_contains_no_real_networking_entity():
    offenders = {s for s in g.STOPWORDS if s.casefold() in _entities(NETWORKING, g.parse_networking_names)}
    assert not offenders, (
        f"{len(offenders)} STOPWORDS entr(y/ies) match a real entity in networking.md. "
        "Token withheld deliberately; remove it and regenerate the denylist."
    )


def test_known_offender_stays_removed():
    """Regression pin for the 2026-08-19 finding, by hash so the token stays private."""
    hashes = {hashlib.sha256(s.casefold().encode()).hexdigest() for s in g.STOPWORDS}
    assert KNOWN_OFFENDER_SHA256 not in hashes, (
        "the 2026-08-19 offending token is back in STOPWORDS; it must stay out so the "
        "denylist can cover it"
    )


def test_stopwords_are_all_generic_glue():
    """Structural backstop needing no private data, so it runs on a fresh clone too.

    Digits, dots and internal capitals are 'brandish' by the module's own definition
    (is_distinctive_single), so such a token is never generic glue and must not sit here.
    """
    for tok in g.STOPWORDS:
        assert tok == tok.lower(), f"{tok!r}: STOPWORDS entries are matched casefolded"
        assert not any(ch.isdigit() for ch in tok), f"{tok!r} looks brandish, not glue"
        assert "." not in tok, f"{tok!r} looks brandish, not glue"
        assert not re.search(r"[a-z][A-Z]", tok), f"{tok!r} looks brandish, not glue"
