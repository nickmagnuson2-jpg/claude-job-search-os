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
    # Bind the count to its OWN name before asserting. `assert not offenders` prints the
    # list, and `assert len(offenders) == 0` still prints it via pytest's "where 2 =
    # len([...])" introspection -- both leak the tokens this file's header forbids
    # printing. Only a bare int in the expression is safe. Found by breaking the guard
    # on purpose and reading the failure output, twice, 2026-09-03.
    offender_count = len(offenders)
    assert offender_count == 0, (
        f"{len(offenders)} STOPWORDS entr(y/ies) match a real company in job-pipeline.md. "
        "Each is suppressed from BOTH denylist tiers, so the hook cannot block it. "
        "Remove it and regenerate. Token withheld: printing it would leak private data."
    )


def test_stopwords_contains_no_real_networking_entity():
    offenders = {s for s in g.STOPWORDS if s.casefold() in _entities(NETWORKING, g.parse_networking_names)}
    # Bind the count to its OWN name before asserting. `assert not offenders` prints the
    # list, and `assert len(offenders) == 0` still prints it via pytest's "where 2 =
    # len([...])" introspection -- both leak the tokens this file's header forbids
    # printing. Only a bare int in the expression is safe. Found by breaking the guard
    # on purpose and reading the failure output, twice, 2026-09-03.
    offender_count = len(offenders)
    assert offender_count == 0, (
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


# ---------------------------------------------------------------------------
# VERB INFLECTIONS. /usr/share/dict/words holds BASE FORMS: "streamline" is in it,
# "streamlined" is not. The rule directly above strips PLURAL suffixes only, so every
# -ed/-ing form of an ordinary verb was classified as a distinctive brand token and
# promoted to the hard BLOCK tier.
#
# This was live and it was blocking. On 2026-09-03 the first real push through the
# newly-repaired pre-push hook was refused because two tracked public files use an
# ordinary past-tense verb in ordinary prose, and a single-token pipeline company
# happens to share that word. The company genuinely belongs in the corpus -- but
# hard-blocking an ordinary English word makes the gate fire on the repo's own
# documentation, and this generator's own docstring already recorded the cost:
# "a gate that cries wolf on its own docs is a gate that gets bypassed."
#
# Same shape and same tradeoff as the plural rule: an ordinary inflected word is not
# DROPPED, it is routed to the ambiguous/WARN tier that /audit-pii reads.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word,stem", [
    ("streamlined", "streamline"),   # -d on a verb ending in e (the 2026-09-03 shape)
    ("streamlining", "streamline"),  # -ing, stem needs its e restored
    ("deployed", "deploy"),          # -ed on a plain verb
    ("deploying", "deploy"),
    ("curated", "curate"),
    ("managed", "manage"),
    ("scored", "score"),
    ("tracked", "track"),
])
def test_ordinary_verb_inflections_are_recognised_as_ordinary(word, stem):
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")
    assert stem in d, f"precondition: {stem} should be in the dictionary"
    assert g._is_ordinary_word(word, d) is True, (
        f"{word!r} was judged a distinctive brand token; it would hard-block on "
        f"ordinary prose in this repo's own docs")


@pytest.mark.parametrize("brand", ["Labelbox", "Databricks"])
def test_real_brands_are_not_dissolved_by_the_inflection_rule(brand):
    """Guard on the guard. A rule that downgrades genuine brands trades a noisy gate
    for a blind one, which is the strictly worse failure."""
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")
    assert g.is_distinctive_single(brand, d) is True, (
        f"{brand} was downgraded out of the BLOCK tier")


def test_a_short_stem_is_not_over_stripped():
    """The stem floor must stop a three-letter token from matching something
    accidental once a suffix is removed."""
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")
    assert g._is_ordinary_word("aed", d) is False


def test_an_inflected_company_lands_in_the_WARN_tier_not_nowhere():
    """THE POINT. Downgrading must not mean dropping: excluding a single-token company
    entirely is what let a live pipeline company reach six public files on 2026-08-10.
    It moves from BLOCK to the ambiguous tier /audit-pii reads, not out of the corpus."""
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")
    companies = {"Streamlined"}
    assert "Streamlined" not in g.build_denylist(set(), companies, d)
    assert "Streamlined" in g.build_ambiguous_list(companies, d)


def test_the_BLOCK_tier_carries_no_ordinary_english_words():
    """THE DURABLE INVARIANT, and the one that closes the ratchet.

    `.pii-denylist-retired.txt` is unioned into every rebuild so a company that leaves
    data/ keeps its coverage. That stickiness means fixing the GENERATOR is not enough:
    a bad token already recorded there is folded straight back in, which is exactly
    what happened on 2026-09-03 -- the generator fix changed nothing until the retired
    file was hand-purged, because the ratchet re-added the token on the next run.

    This test watches the OUTPUT rather than the generator, so it catches a bad token
    however it arrives: from data/, from the retired file, or from manual additions.

    Reports a COUNT only, never the token: this reads gitignored private data and
    printing a match would move a real company name into a CI log. Same disclosure
    discipline as the tests above.
    """
    denylist = REPO_ROOT / "tools" / ".pii-denylist.txt"
    if not denylist.exists():
        pytest.skip("no generated denylist (fresh clone / CI)")
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")

    offenders = []
    for line in denylist.read_text(encoding="utf-8").splitlines():
        tok = line.strip()
        # Single bare alphabetic tokens only. Phrases, slugs and domains are
        # distinctive by construction and are meant to be here.
        if not tok or " " in tok or not tok.isalpha():
            continue
        if tok.lower() in g.KEEP or tok.lower() in g.STOPWORDS:
            continue
        if g._is_ordinary_word(tok.lower(), d):
            offenders.append(tok)

    # Bind the count to its OWN name before asserting. `assert not offenders` prints the
    # list, and `assert len(offenders) == 0` still prints it via pytest's "where 2 =
    # len([...])" introspection -- both leak the tokens this file's header forbids
    # printing. Only a bare int in the expression is safe. Found by breaking the guard
    # on purpose and reading the failure output, twice, 2026-09-03.
    offender_count = len(offenders)
    assert offender_count == 0, (
        f"{len(offenders)} ordinary English word(s) are in the BLOCK tier. Each will "
        f"hard-refuse any push whose public files use that word in ordinary prose, and "
        f"a gate that fires on the repo's own documentation gets bypassed rather than "
        f"obeyed. Fix: they belong in the ambiguous/WARN tier that /audit-pii reads. "
        f"Check tools/.pii-denylist-retired.txt too -- it is unioned back in on every "
        f"rebuild, so fixing the generator alone does not remove them.")
