"""Tests for the two gaps that let a real company through the always-on PII hook.

Both were found on 2026-09-01 by a live leak, not by reasoning: a public framework file
named a real pipeline company and `check_public_pii.py` passed it.

GAP 1 - COMPONENT TOKENS. build_denylist emits a multi-token company as the whole phrase
plus its slug, and nothing else. The denylist held the two-word form while the public file
used one word of it, and a phrase matcher does not fire on half a phrase. 171 of 470
entries were multi-token at the time, so this was not a one-off.

GAP 2 - RETIRED TARGETS. The list is rebuilt from CURRENT data/ each run, so a company
that leaves scan-targets.yaml silently loses its protection while its name stays behind in
public files. Measured the same day: a former scan target sat in two files on the public
remote, on NEITHER tier, because it had been retargeted away a week earlier.

The tier split is what keeps this from drowning the hook in noise: a distinctive component
BLOCKs, an ordinary-English component goes to the WARN tier that /audit-pii reads.

Every company and person below is fictional and was checked against both denylist tiers
and data/ before being written here.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "tools" / "gen_pii_denylist.py"

spec = importlib.util.spec_from_file_location("gen_pii_denylist_components", MOD_PATH)
gen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gen
spec.loader.exec_module(gen)


@pytest.fixture
def dictionary():
    return gen.load_dictionary()


# --------------------------------------------------------------------------
# Gap 1 - component tokens of a multi-token company
# --------------------------------------------------------------------------

def test_a_distinctive_component_of_a_two_word_company_is_blocked(dictionary):
    """The exact 2026-09-01 leak shape: the list held the two-word name, the public file
    used one word of it, and the phrase matcher never fired."""
    out = gen.build_denylist(set(), {"Northwind Systems"}, dictionary)
    assert "Northwind Systems" in out, "the whole phrase must still be covered"
    assert "Northwind" in out, "the distinctive component is what the leak actually used"


def test_a_generic_corporate_suffix_is_never_promoted_on_its_own(dictionary):
    """'AI', 'Labs', 'Inc' as standalone BLOCK tokens would fire on essentially every
    file in this repo, which is how a gate gets switched off."""
    out = gen.build_denylist(set(), {"Contoso AI", "Fabrikam Labs", "Initech Inc"},
                             dictionary)
    for suffix in ("AI", "Labs", "Inc", "ai", "labs", "inc"):
        assert suffix not in out


def test_an_ordinary_english_component_does_not_reach_the_BLOCK_tier(dictionary):
    """A company named from two common words must not put either on the block list -
    both are ordinary prose in this repo's own docs, and a false BLOCK trains the gate
    to be bypassed."""
    out = gen.build_denylist(set(), {"Silver Harbor"}, dictionary)
    assert "Silver" not in out
    assert "Silver Harbor" in out


def test_an_ordinary_english_component_DOES_reach_the_warn_tier(dictionary):
    """It is still a real company word. /audit-pii is the reader for this tier, and the
    alternative - dropping it entirely - is what made the leak invisible."""
    amb = gen.build_ambiguous_list({"Silver Harbor"}, dictionary)
    assert "Silver" in amb or "Harbor" in amb


def test_a_three_word_company_contributes_each_distinctive_component(dictionary):
    out = gen.build_denylist(set(), {"Zorptech Quantum Holdings"}, dictionary)
    assert "Zorptech" in out
    assert "Holdings" not in out, "a generic corporate word stays out of BLOCK"


def test_a_person_contributes_a_surname_but_never_a_bare_first_name(dictionary):
    """This repo's public docs use a fictional cast (Sarah Chen, Jordan Lee, Priya Anand).
    Promoting a real contact's FIRST name would fire on every one of those placeholders
    and make the hook unusable; the surname carries the identifying weight anyway."""
    out = gen.build_denylist({"Sarah Zorpwallader"}, set(), dictionary)
    assert "Zorpwallader" in out
    assert "Sarah" not in out


def test_a_surname_that_is_an_ordinary_word_stays_out_of_BLOCK(dictionary):
    """A surname that is also a common noun would fire on ordinary prose."""
    out = gen.build_denylist({"Jordan Field"}, set(), dictionary)
    assert "Field" not in out


def test_the_whole_phrase_is_still_emitted_alongside_its_components(dictionary):
    """Components ADD coverage. If adding them ever removed the phrase, a file using the
    full name would stop being caught - a strictly worse gate than before the fix."""
    out = gen.build_denylist(set(), {"Northwind Systems"}, dictionary)
    assert {"Northwind Systems", "northwind-systems"} <= set(out)


# --------------------------------------------------------------------------
# Gap 2 - a target that leaves the data must keep its coverage
# --------------------------------------------------------------------------

def test_a_token_from_a_previous_run_survives_when_the_company_leaves_the_data(tmp_path):
    """The retargeting case. The company is gone from scan-targets.yaml; its name is not
    gone from the public files written while it was a target."""
    retired = tmp_path / "retired.txt"
    retired.write_text("Zorptech\nzorptech\n", encoding="utf-8")
    merged = gen.merge_retired(["Northwind"], retired)
    assert "Zorptech" in merged
    assert "Northwind" in merged


def test_the_current_run_adds_its_own_tokens_to_the_retired_file(tmp_path):
    """Accumulation has to happen on the way out, or nothing is ever retained and the
    next run starts from the same amnesia."""
    retired = tmp_path / "retired.txt"
    gen.record_retired(["Northwind", "Contoso"], retired)
    body = retired.read_text(encoding="utf-8")
    assert "Northwind" in body and "Contoso" in body


def test_accumulation_is_idempotent_and_sorted(tmp_path):
    retired = tmp_path / "retired.txt"
    gen.record_retired(["Beta", "Alpha"], retired)
    gen.record_retired(["Alpha"], retired)
    lines = [l for l in retired.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert lines == ["Alpha", "Beta"], "no duplicates, stable order for a readable diff"


def test_a_missing_retired_file_is_not_an_error(tmp_path):
    """First run on a fresh clone. A generator that raises here blocks the denylist from
    ever being built, which fails the gate open."""
    assert gen.merge_retired(["Northwind"], tmp_path / "absent.txt") == ["Northwind"]


def test_comments_in_the_retired_file_are_ignored(tmp_path):
    retired = tmp_path / "retired.txt"
    retired.write_text("# hand-pruned; delete a line to drop it\nZorptech\n",
                       encoding="utf-8")
    merged = gen.merge_retired([], retired)
    assert merged == ["Zorptech"]


def test_a_line_deleted_from_the_retired_file_stays_deleted(tmp_path):
    """The escape hatch. Sticky accumulation with no way out means one bad token poisons
    the gate forever, so the file is hand-editable and merge must respect an edit."""
    retired = tmp_path / "retired.txt"
    gen.record_retired(["Keepme", "Dropme"], retired)
    retired.write_text("Keepme\n", encoding="utf-8")
    assert gen.merge_retired([], retired) == ["Keepme"]


# --------------------------------------------------------------------------
# is_distinctive_single: the dictionary is not a complete word list
# --------------------------------------------------------------------------
#
# Found 2026-09-01 by running the tightened generator over all 522 tracked public files:
# 7 hits, 6 of them false positives of exactly two shapes. The system dictionary holds
# singulars but NOT plurals, and omits closed compounds. So an ordinary plural component
# looked like a distinctive brand token and reached the BLOCK tier, where it fires on
# ordinary prose in this repo's own docs. A gate that cries wolf on its own documentation
# is a gate that gets bypassed.
#
# The words below are deliberately unrelated to any real entity: they exercise the same
# morphology, and the real ones cannot be written here - the hook (correctly) blocks them.

@pytest.mark.parametrize("plural", ["Rockets", "Gardens", "Ladders", "Kettles"])
def test_a_plural_of_an_ordinary_word_is_not_distinctive(plural, dictionary):
    """The singular is in the dictionary; the plural is not. Testing only the surface
    form is what let this whole class onto the BLOCK tier."""
    assert not gen.is_distinctive_single(plural, dictionary)


@pytest.mark.parametrize("sector", ["Healthtech", "Fintech", "Marketplace"])
def test_a_closed_compound_sector_word_is_not_distinctive(sector, dictionary):
    """The system dictionary omits these, so they read as brand tokens and reached BLOCK.
    "<Something> Healthcare" is a naming convention, not an identifier.

    Handled by an explicit list, NOT by a split-into-two-words rule: that rule was written
    and rejected the same day because "Northwind" is "north" + "wind" and it dissolved the
    repo's own placeholder brand. See test_a_compound_brand_name_survives below."""
    assert not gen.is_distinctive_single(sector, dictionary)


def test_a_compound_brand_name_survives(dictionary):
    """The regression that killed the general compound rule. Both halves of this name are
    ordinary dictionary words, and it is still a brand that must BLOCK."""
    assert gen.is_distinctive_single("Northwind", dictionary)


@pytest.mark.parametrize("brand", ["Zorptech", "Northwind", "Fabrikam", "Contoso"])
def test_a_genuine_brand_token_is_still_distinctive(brand, dictionary):
    """The morphology relaxation must not swallow real brand names - that would silently
    shrink coverage, which is the failure this whole change exists to prevent."""
    assert gen.is_distinctive_single(brand, dictionary)


def test_a_brandish_token_with_a_digit_or_internal_capital_stays_distinctive(dictionary):
    """Pre-existing behaviour, pinned so the morphology check cannot regress it."""
    assert gen.is_distinctive_single("7x.ai", dictionary)
    assert gen.is_distinctive_single("LocateThing", dictionary)


def test_the_compound_split_requires_both_halves_to_be_real_words(dictionary):
    """'Zorptech' must not be dissolved by finding some 4-letter prefix in the dictionary;
    the remainder has to be a word too."""
    assert gen.is_distinctive_single("Zorptech", dictionary)


# --------------------------------------------------------------------------
# mutation-driven: each of these killed a mutant that survived the tests above
# --------------------------------------------------------------------------

def test_a_brand_that_merely_ENDS_in_s_is_still_distinctive(dictionary):
    """The plural rule must check that the STEM is a real word. Accepting any -s ending
    would drop every brand name that happens to end in s off the BLOCK tier - a silent
    loss of coverage, which is the failure mode this change exists to prevent."""
    assert gen.is_distinctive_single("Contosos", dictionary)
    assert gen.is_distinctive_single("Zorptechs", dictionary)


def test_company_components_skips_short_and_suffix_tokens_entirely(dictionary):
    """Neither tier should carry 'AI' or 'Inc'. Asserting only on the BLOCK list hid this:
    a short token fails the distinctiveness test anyway and lands in WARN, where it is
    just as wrong and nobody notices."""
    block, warn = gen.company_components("Contoso AI Inc", dictionary)
    assert "AI" not in block and "AI" not in warn
    assert "Inc" not in block and "Inc" not in warn
    assert "Contoso" in block


def test_a_single_token_person_name_yields_no_surname(dictionary):
    """A mononym has no surname to promote. Taking the last token regardless would emit
    the whole name as if it were a surname."""
    assert gen.person_components("Madonna", dictionary) == []


def test_person_components_returns_a_LIST_not_None_for_a_rejected_name(dictionary):
    """The caller does `out.update(person_components(...))`. None is a TypeError that
    aborts the whole denylist build, which fails the gate open."""
    assert gen.person_components("Madonna", dictionary) == []
    assert gen.person_components("Jordan Li", dictionary) == []
    out = gen.build_denylist({"Madonna"}, set(), dictionary)
    assert isinstance(out, list)


def test_a_very_short_surname_is_not_promoted(dictionary):
    """A two-letter surname would match inside ordinary prose constantly."""
    assert gen.person_components("Jordan Li", dictionary) == []


def test_record_retired_creates_a_missing_parent_directory(tmp_path):
    """The retired file lives beside the denylist. On a fresh clone that directory may not
    exist yet, and an unhandled failure here loses the accumulation silently."""
    target = tmp_path / "nested" / "deeper" / "retired.txt"
    gen.record_retired(["Northwind"], target)
    assert target.exists() and "Northwind" in target.read_text(encoding="utf-8")


def test_a_distinctive_mononym_is_not_treated_as_a_surname(dictionary):
    """The len(toks) < 2 guard IS load-bearing, unlike the length/KEEP guard beside it:
    without it a one-word entry is read as its own surname and promoted to BLOCK."""
    assert gen.person_components("Zorptech", dictionary) == []
    assert gen.person_components("Sarah Zorptech", dictionary) == ["Zorptech"]


# ── GAP 3: a FIXTURE name harvested from real data blocks its own placeholders ──
#
# Found 2026-09-02, live. A fixture name used by the act_apply contact-add tests
# reached data/networking.md as a real-looking `### Name — Company` section. The
# harvester cannot tell a demo row from a real contact, so it promoted the name to
# BLOCK, and the always-on PreToolUse hook then blocked three public files that use
# it as a placeholder -- including the skill doc documenting the command that made it.
#
# The trap that makes this its own gap rather than a variant of GAP 2: deleting the
# row from networking.md does NOT release the block, because merge_retired() folds
# the retired file back in on every run. The ratchet is right for real entities and
# wrong for fixtures, so the exclusion must be declarative and permanent.

def test_a_declared_fixture_name_is_never_promoted_to_the_denylist(dictionary):
    """The regression: a fixture name must be excluded even when it arrives via the
    same `### Name — Company` shape a real contact uses."""
    tokens = gen.build_denylist(
        names={"Alex Park"}, companies=set(), dictionary=dictionary
    )
    assert "Alex Park" not in tokens, (
        "a fixture name reached the denylist; it will block the public files that "
        "use it as a placeholder"
    )


def test_the_fixture_exclusion_does_not_also_swallow_a_real_contact(dictionary):
    """Guard on the guard. The exclusion must be narrow: a real two-word contact
    sharing the fixture's FIRST name must still be promoted, or 'exclude the
    fixture' quietly becomes 'exclude everyone called Alex'."""
    tokens = gen.build_denylist(
        names={"Alex Kowalczyk"}, companies=set(), dictionary=dictionary
    )
    assert "Alex Kowalczyk" in tokens, (
        "the fixture exclusion widened to a real contact sharing a first name"
    )
