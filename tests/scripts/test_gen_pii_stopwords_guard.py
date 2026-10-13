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


# ---------------------------------------------------------------------------
# THE TARGET-NAME ALLOWLIST (feedback_pii_denylist_drops_common_word_company_names,
# 5 fires before this was built).
#
# Every test above defends one direction: an ordinary English word must not reach the
# BLOCK tier, because it would refuse pushes over the repo's own prose. The cost of that
# filter was the other direction: a company on the real target list whose name IS an
# ordinary word went to the WARN tier, and a PreToolUse WARN reaches nobody.
#
# Both directions came from deciding BLOCK membership by a dictionary lookup alone. The
# allowlist adds the missing input: a single-token name in the Company column of
# data/job-pipeline.md is a target by construction, so it reaches BLOCK whether or not
# the dictionary knows the word. Everything not in that column is filtered as before.
#
# Every company below is invented and was checked against both denylist tiers and
# data/ before being written here.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402

PIPELINE_HEADER = (
    "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n")


def _row(company: str) -> str:
    return f"| {company} | Analyst | Applied | 2026-01-01 | wait | cv | - | - |\n"


def _fixture_root(tmp_path, pipeline_body: str, scan_targets: str = "") -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "networking.md").write_text("# Networking\n", encoding="utf-8")
    (d / "job-pipeline.md").write_text(pipeline_body, encoding="utf-8")
    if scan_targets:
        (d / "scan-targets.yaml").write_text(scan_targets, encoding="utf-8")
    return tmp_path


def _dry_run(root: Path) -> dict:
    if not g.load_dictionary():
        pytest.skip("no system dictionary on this machine")
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_pii_denylist.py"),
         "--repo-root", str(root), "--dry-run"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_an_ordinary_word_company_in_the_pipeline_reaches_BLOCK(tmp_path):
    """THE OWED FIX. `Lantern` is in the dictionary, so the word filter alone routes it
    to WARN. It is in the pipeline Company column, so it must BLOCK."""
    pipeline = "# Pipeline\n\n## Active Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "lantern" in g.load_dictionary(), "precondition: the name is an ordinary word"
    assert "Lantern" in res["target_words"]


def test_the_name_goes_to_its_own_tier_and_not_into_the_plain_BLOCK_list(tmp_path):
    """Measured 2026-10-12 on the live data: in the plain BLOCK list, the ordinary-word
    pipeline names refused 11 of 647 tracked public files, 9 of them over the word
    starting a sentence. The hook matches the target-word tier with a matcher that
    skips sentence openers; the plain BLOCK list has no such exception, so the name
    must not be in it."""
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Lantern" in res["target_words"]
    assert "Lantern" not in res["tokens"]


def test_a_real_run_writes_the_target_word_file(tmp_path):
    """The hook reads a file, not the dry-run JSON."""
    if not g.load_dictionary():
        pytest.skip("no system dictionary on this machine")
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
    root = _fixture_root(tmp_path, pipeline)
    (root / "tools").mkdir()
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_pii_denylist.py"),
         "--repo-root", str(root)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    written = (root / g.TARGET_WORDS_REL).read_text(encoding="utf-8").splitlines()
    assert "Lantern" in written
    assert "Lantern" not in (root / g.OUTPUT_REL).read_text(encoding="utf-8").splitlines()


def test_an_allowlisted_name_leaves_the_WARN_tier(tmp_path):
    """One tier per token. Left in both, /audit-pii would report it twice and the WARN
    count would no longer mean 'covered by nothing automatic'."""
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Lantern" not in res["ambiguous"]


def test_archived_pipeline_rows_are_allowlisted_too(tmp_path):
    """Coverage outlives the pursuit: a closed company's name is still in whatever
    public file was written while it was live."""
    pipeline = ("# Pipeline\n\n## Active Pipeline\n\n" + PIPELINE_HEADER
                + "\n## Archived\n\n" + PIPELINE_HEADER + _row("Thimble"))
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Thimble" in res["target_words"]


def test_the_allowlisted_name_blocks_as_written_and_not_in_lowercase(tmp_path):
    """The hook matches case-sensitively on whole words. `Lantern` as a BLOCK token
    refuses the capitalised name; a lowercase `lantern` token would refuse the ordinary
    noun in every sentence that uses it, which is the 2026-09-03 failure again."""
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Lantern" in res["target_words"]
    assert "lantern" not in res["tokens"] and "lantern" not in res["target_words"]


def test_an_ordinary_word_company_outside_the_pipeline_stays_WARN(tmp_path):
    """The bypass is scoped to the pipeline Company column. A scan target is a company
    that was looked at, not one being pursued, and there are several times as many of
    them; blocking their ordinary-word names would widen the false-positive surface the
    filter exists to keep small."""
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER
    targets = "companies:\n  - name: Gravel\n    active: true\n"
    res = _dry_run(_fixture_root(tmp_path, pipeline, targets))
    assert "Gravel" not in res["tokens"] and "Gravel" not in res["target_words"]
    assert "Gravel" in res["ambiguous"]


def test_KEEP_still_wins_over_the_allowlist(tmp_path):
    """A public-safe employer in the pipeline column stays off the denylist."""
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Notion")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Notion" not in res["tokens"] and "Notion" not in res["target_words"]


def test_a_non_company_table_in_the_pipeline_file_is_not_allowlisted(tmp_path):
    """The bypass removes a filter, so its source must be exactly the Company column. A
    summary table added to the same file later (first column `Stage`) must not turn its
    row labels into BLOCK tokens."""
    pipeline = ("# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern")
                + "\n## Funnel\n\n| Stage | Count |\n| --- | --- |\n| Kettle | 3 |\n")
    res = _dry_run(_fixture_root(tmp_path, pipeline))
    assert "Lantern" in res["target_words"]
    assert "Kettle" not in res["tokens"] and "Kettle" not in res["target_words"]


def test_parse_pipeline_target_names_reads_only_company_tables():
    content = ("# Pipeline\n\n" + PIPELINE_HEADER + _row("Lantern") + _row("**Thimble**")
               + _row("Gravel (via referral)") + _row("Northwind Systems")
               + "\n| Stage | Count |\n| --- | --- |\n| Kettle | 3 |\n")
    assert g.parse_pipeline_target_names(content) == {
        "Lantern", "Thimble", "Gravel", "Northwind Systems"}


def test_the_dictionary_filter_is_unchanged_without_the_allowlist():
    """The default argument keeps every existing caller on the old behaviour."""
    d = g.load_dictionary()
    if not d:
        pytest.skip("no system dictionary on this machine")
    assert "Lantern" not in g.build_denylist(set(), {"Lantern"}, d)
    assert "Lantern" in g.build_denylist(set(), {"Lantern"}, d, {"Lantern"})


# --- mutation survivors, 2026-10-12: the allowlist decision, called directly ----------

def test_a_company_cell_that_cleans_to_nothing_is_not_a_target_name():
    content = "# Pipeline\n\n" + PIPELINE_HEADER + _row("**") + _row("(tbd)")
    assert g.parse_pipeline_target_names(content) == set()


@pytest.mark.parametrize("name, why", [
    ("Lantern Works", "two tokens: the phrase is distinctive and blocks whole"),
    ("Nub", "under four characters matches too much as a whole word"),
    ("Notion", "KEEP is public-safe by decision"),
])
def test_the_allowlist_does_not_bypass_length_KEEP_or_phrases(name, why):
    assert name.lower() in g.KEEP or name != "Notion"
    assert g.is_allowlisted_target(name, {name}) is False, why


def test_the_allowlist_does_not_bypass_STOPWORDS():
    word = next(w for w in sorted(g.STOPWORDS) if len(w) >= 4 and w.isalpha())
    assert g.is_allowlisted_target(word, {word}) is False


def test_allowlist_membership_is_exact_and_case_sensitive():
    assert g.is_allowlisted_target("Lantern", {"Lantern"}) is True
    assert g.is_allowlisted_target(" Lantern ", {"Lantern"}) is True
    assert g.is_allowlisted_target("lantern", {"Lantern"}) is False
    assert g.is_allowlisted_target("Lantern", set()) is False


def test_build_ambiguous_list_leaves_out_an_allowlisted_name():
    """main() subtracts allowlisted names a second time, so only a direct call shows
    whether this function does."""
    dictionary = {"lantern", "gravel"}
    assert g.build_ambiguous_list({"Lantern", "Gravel"}, dictionary) == ["Gravel", "Lantern"]
    assert g.build_ambiguous_list({"Lantern", "Gravel"}, dictionary, {"Lantern"}) == ["Gravel"]


# --- mutation survivors, 2026-10-12: parsers and tier builders, called directly -------
# 45 decisions in code older than the allowlist could be forced either way with every
# test green. Each case below asserts the exact result, so an extra or a missing token
# fails.

def test_load_dictionary_is_an_empty_set_without_a_system_dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "SYSTEM_DICT", tmp_path / "absent")
    assert g.load_dictionary() == set()


def test_parse_networking_names_takes_table_rows_and_nothing_else():
    content = ("# Networking\n\nCall notes for later\n\n"
               "| Name | Company |\n| --- | --- |\n"
               "| Zelda Quillfeather | Northwind |\n|  | Northwind |\n")
    assert g.parse_networking_names(content) == {"Zelda Quillfeather"}


def test_parse_inbox_companies_skips_an_empty_bold_and_other_blocks():
    content = ("## 2026-01-01 (company)\n- **Quillfeather Labs** note\n- ** ** note\n"
               "## 2026-01-02 (person)\n- **Zelda Quillfeather** note\n")
    assert g.parse_inbox_companies(content) == {"Quillfeather Labs"}


def test_parse_pipeline_companies_skips_the_header_rule_and_empty_cells():
    content = PIPELINE_HEADER + _row("Lantern Works") + _row("")
    assert g.parse_pipeline_companies(content) == {"Lantern Works"}


def test_is_distinctive_single_floor_and_brand_shapes():
    assert g.is_distinctive_single("Xq7", set()) is False            # under four characters
    assert g.is_distinctive_single("Notion", set()) is False         # KEEP
    assert g.is_distinctive_single("LanternWorks", {"lanternworks"}) is True   # inner capital
    assert g.is_distinctive_single("lantern.io", {"lantern.io"}) is True
    assert g.is_distinctive_single("Lantern", {"lantern"}) is False


def test_every_fictional_cast_name_is_covered_by_its_surname():
    """person_components relies on this instead of testing the cast list itself."""
    for name in g.FICTIONAL_CAST:
        toks = [x for x in re.split(r"[^A-Za-zÀ-ÿ'\-]+", name) if x]
        assert len(toks) < 2 or toks[-1].lower() in g.FICTIONAL_SURNAMES, name


def test_a_fictional_surname_is_never_emitted_as_a_component():
    surname = next(s for s in sorted(g.FICTIONAL_SURNAMES)
                   if g.is_distinctive_single(s.title(), set()))
    assert g.person_components(f"Zelda {surname.title()}", set()) == []
    assert g.person_components("Zelda Quillfeather", set()) == ["Quillfeather"]


def test_build_denylist_person_emits_phrase_slug_and_surname():
    assert set(g.build_denylist({"Zelda Quillfeather"}, set(), set())) == {
        "Zelda Quillfeather", "zelda-quillfeather", "Quillfeather"}


@pytest.mark.parametrize("names", [
    {"Madonna"},                 # one token: too likely to be an ordinary word
    {"Zelda Qu1ll"},             # a digit: not a name
    {"Ali Rohde"},               # KEEP
    {""},
])
def test_build_denylist_person_emits_nothing_for(names):
    assert g.build_denylist(names, set(), set()) == []


def test_build_denylist_never_emits_an_empty_slug():
    assert g.build_denylist({"'. .'"}, set(), set()) == ["'. .'"]
    assert g.build_denylist(set(), {"& &"}, set()) == ["& &"]
    assert g.build_denylist(set(), {"...."}, set()) == ["...."]


@pytest.mark.parametrize("companies", [
    {"Yahoo Sports"}, {"**Daily Stoic**"},       # KEEP, with and without emphasis
    {"The Group"},                               # every word is a stopword
    {""}, {"**"},
])
def test_build_denylist_company_emits_nothing_for(companies):
    assert g.build_denylist(set(), companies, set()) == []


def test_build_denylist_distinctive_single_company_emits_name_and_slug():
    assert set(g.build_denylist(set(), {"Quillfeather"}, set())) == {
        "Quillfeather", "quillfeather"}


@pytest.mark.parametrize("companies, dictionary", [
    ({"**"}, set()),                              # cleans to nothing
    ({"Quillfeather"}, set()),                    # distinctive: BLOCK tier already
    ({"Labs", "Notion"}, {"labs", "notion"}),     # a stopword and a KEEP name
])
def test_build_ambiguous_list_emits_nothing_for(companies, dictionary):
    assert g.build_ambiguous_list(companies, dictionary) == []


def test_build_ambiguous_list_emits_components_of_a_phrase_and_never_the_phrase():
    out = g.build_ambiguous_list({"Lantern Works"}, {"lantern works", "lantern", "works"})
    assert "Lantern Works" not in out
    assert "Lantern" in out


def test_parse_scan_target_companies_degrades_and_skips_nameless_entries(tmp_path):
    assert g.parse_scan_target_companies(tmp_path / "absent.yaml") == set()
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies: [unclosed\n", encoding="utf-8")
    assert g.parse_scan_target_companies(bad) == set()
    ok = tmp_path / "ok.yaml"
    ok.write_text("companies:\n  - name: Zephyrine Dynamics\n  - ats: greenhouse\n"
                  "  - just a string\nrejected:\n  - name: ''\n", encoding="utf-8")
    assert g.parse_scan_target_companies(ok) == {"Zephyrine Dynamics"}


def test_load_manual_additions_sections_and_first_run(tmp_path):
    path = tmp_path / "tools" / "manual.txt"
    assert g.load_manual_additions(path) == (set(), set())     # created from the template
    assert path.exists()
    path.write_text("# comment\n\n[block]\nZelda Quillfeather\n[ambiguous]\nGravel\n"
                    "[BLOCK]\nMarrowgate\n", encoding="utf-8")
    assert g.load_manual_additions(path) == ({"Zelda Quillfeather", "Marrowgate"}, {"Gravel"})


def test_a_real_run_writes_every_tier_file_and_reports(tmp_path):
    if not g.load_dictionary():
        pytest.skip("no system dictionary on this machine")
    pipeline = "# Pipeline\n\n" + PIPELINE_HEADER + _row("Quillfeather")
    root = _fixture_root(tmp_path, pipeline, "companies:\n  - name: Gravel\n")
    (root / "tools").mkdir()
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_pii_denylist.py"),
         "--repo-root", str(root)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["status"] == "ok" and report["count"] >= 2

    def lines(rel):
        return (root / rel).read_text(encoding="utf-8").splitlines()
    assert "Quillfeather" in lines(g.OUTPUT_REL)
    assert "Quillfeather" in lines(g.RETIRED_REL)
    assert "Gravel" in lines(g.AMBIGUOUS_REL)


def test_load_manual_additions_is_empty_when_the_path_cannot_be_read(tmp_path):
    (tmp_path / "manual.txt").mkdir()            # exists, and reading it raises OSError
    assert g.load_manual_additions(tmp_path / "manual.txt") == (set(), set())


# --- The WARN tier filters the networking-header harvest too (2026-10-13) -----
# The harvest was merged in after the KEEP and STOPWORDS checks, so public-safe names
# and the placeholder a blank header carries reached the tier.

def test_ambiguous_tier_drops_keep_names_and_placeholders_from_headers():
    d = {"lantern", "gravel"}
    got = g.ambiguous_tier(["Lantern"], {"LinkedIn", "Yahoo Sports", "TODO", "Unknown",
                                         "Partners", "Gravel", "Zephyrine Dynamics"}, d)
    assert got == ["Gravel", "Lantern", "Partners", "Zephyrine Dynamics"], (
        "a generic word in the company position of a header is still a company")


def test_ambiguous_tier_filters_the_company_side_as_well():
    assert g.ambiguous_tier(["Zuora", "tbd", "Lantern"], set(), {"lantern"}) == ["Lantern"]
    assert g.ambiguous_tier([], {"Health"}, {"health"}) == ["Health"]


def test_ambiguous_tier_drops_an_allowlisted_target_seen_in_a_header():
    d = {"lantern", "gravel"}
    assert g.ambiguous_tier([], {"Lantern", "Gravel"}, d, targets={"Lantern"}) == ["Gravel"]
    # a distinctive name is kept even when it is a target: only ordinary words move up
    assert g.ambiguous_tier([], {"Quillfeather"}, d, targets={"Quillfeather"}) == ["Quillfeather"]


def test_ambiguous_tier_is_sorted_and_deduplicated():
    assert g.ambiguous_tier(["Lantern", "Gravel"], {"Lantern"}, {"lantern", "gravel"}) == [
        "Gravel", "Lantern"]


def test_placeholder_cells_hold_no_company_name():
    assert g.PLACEHOLDER_CELLS == {"todo", "tbd", "unknown", "none", "n/a"}
