"""Tests for tools/population_probe.py.

These exist to FAIL when the probe breaks, not to be green. Each one targets a branch whose
silent loss would reproduce the defect family the probe is built against: a check that runs,
returns clean, and measured nothing.

Run alone as well as in the suite -- `pytest tests/scripts/test_population_probe.py` must pass
on its own, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from population_probe import (  # noqa: E402
    CANNOT_RUN,
    FAIL,
    PASS,
    Filter,
    compare_populations,
)


class Mask(list):
    """Minimal boolean mask: the probe only ever calls .sum() on it."""

    def sum(self):
        return sum(self)


class Pop(list):
    """Minimal population: the probe only ever calls len() on it.

    Deliberately NOT a DataFrame. tools/population_probe.py imports no dataframe library,
    and a test suite that needs one would be testing pandas rather than this contract.
    """

    marker = None


def pop(flags, marker=None) -> Pop:
    p = Pop(flags)
    p.marker = marker
    return p


def cut(d):
    return Mask(1 if x else 0 for x in d)


# --------------------------------------------------------------- empty / vacuous guards


def test_empty_side_a_raises():
    """An empty scan is an error, not a clean result."""
    with pytest.raises(ValueError, match="EMPTY"):
        compare_populations(pop([]), pop([1, 0]), [Filter("c", cut)])


def test_empty_side_b_raises():
    """Both sides guarded, not just the first -- a one-sided guard is half a guard."""
    with pytest.raises(ValueError, match="EMPTY"):
        compare_populations(pop([1, 0]), pop([]), [Filter("c", cut)])


def test_zero_filters_raises():
    """A probe that checks nothing must not report clean."""
    with pytest.raises(ValueError, match="ZERO filters"):
        compare_populations(pop([1, 0]), pop([1, 0]), [])


# --------------------------------------------------------------- three-state


def test_uncomputable_on_a_is_cannot_run_not_pass():
    missing = Filter("missing", lambda d: d.no_such_attribute)
    rep = compare_populations(pop([1, 0]), pop([1, 0]), [missing])
    assert rep.results[0].state == CANNOT_RUN
    assert rep.results[0].state != PASS


def test_uncomputable_on_b_only_is_still_cannot_run():
    """The B branch is separate code. Losing it would silently pass a half-measured filter."""

    def only_a(d):
        if getattr(d, "marker", None) == "B":
            raise KeyError("cannot compute on B")
        return cut(d)

    a = pop([1, 0])
    b = pop([1, 0], marker="B")
    rep = compare_populations(a, b, [Filter("half", only_a)])
    assert rep.results[0].state == CANNOT_RUN
    assert rep.results[0].share_a is not None, "the A-side share should still be reported"
    assert rep.results[0].share_b is None


def test_cannot_run_clears_fully_covered_but_not_clean():
    """clean and fully_covered are DIFFERENT questions. Collapsing them is the whole defect."""
    missing = Filter("missing", lambda d: d.no_such_attribute)
    rep = compare_populations(pop([1, 0]), pop([1, 0]), [missing])
    assert rep.clean is True, "no FAILs means clean"
    assert rep.fully_covered is False, "a CANNOT_RUN must clear fully_covered"


def test_fail_clears_both():
    rep = compare_populations(pop([1] * 10), pop([0] * 10), [Filter("c", cut)])
    assert rep.clean is False
    assert rep.fully_covered is False


# --------------------------------------------------------------- the thresholds


def test_commensurate_populations_pass():
    """PASS must be reachable, or every other assertion here is vacuous."""
    rep = compare_populations(pop([1, 0, 0, 0]), pop([1, 0, 0, 0]), [Filter("c", cut)])
    assert rep.results[0].state == PASS
    assert rep.fully_covered is True


def test_percentage_point_threshold_fires():
    # 50% vs 10% -> 40pp apart
    rep = compare_populations(pop([1] * 5 + [0] * 5), pop([1] + [0] * 9), [Filter("c", cut)])
    assert rep.results[0].state == FAIL
    assert rep.results[0].diff_pp == pytest.approx(40.0)


def test_just_under_threshold_passes():
    """4pp apart, default threshold 5pp. Guards against a mutated comparison operator."""
    a = pop([1] * 20 + [0] * 80)   # 20%
    b = pop([1] * 16 + [0] * 84)   # 16%
    rep = compare_populations(a, b, [Filter("c", cut)])
    assert rep.results[0].state == PASS


def test_ratio_threshold_fires_independently_of_pp():
    """A large RATIO on small shares is still an asymmetry; pp alone would miss it."""
    a = pop([1] * 4 + [0] * 96)    # 4.0%
    b = pop([1] * 40 + [0] * 3960)  # 1.0% -> 3pp apart (under), 4x (over)
    rep = compare_populations(a, b, [Filter("c", cut)])
    assert rep.results[0].state == FAIL
    assert "x" in rep.results[0].detail


def test_tiny_shares_do_not_fire_on_ratio_alone():
    """The hi>0.005 floor: 0.4% vs 0.1% is 4x and noise. Losing the floor spams FAILs."""
    a = pop([1] * 4 + [0] * 996)      # 0.4%
    b = pop([1] * 1 + [0] * 999)      # 0.1%
    rep = compare_populations(a, b, [Filter("c", cut)])
    assert rep.results[0].state == PASS


def test_zero_on_one_side_is_infinite_ratio_and_fails():
    a = pop([1] * 10 + [0] * 90)   # 10%
    b = pop([0] * 100)             # 0%
    rep = compare_populations(a, b, [Filter("c", cut)])
    assert rep.results[0].state == FAIL
    assert "inf" in rep.results[0].detail


# --------------------------------------------------------------- reporting


def test_shares_are_reported_not_just_the_verdict():
    """A verdict without its numbers cannot be audited."""
    rep = compare_populations(pop([1] * 5 + [0] * 5), pop([1] + [0] * 9), [Filter("c", cut)])
    r = rep.results[0]
    assert r.share_a == pytest.approx(0.5)
    assert r.share_b == pytest.approx(0.1)
    assert r.n_a == 10 and r.n_b == 10


def test_report_counts_match_results():
    fil = [Filter("c", cut), Filter("missing", lambda d: d.no_such_attribute)]
    rep = compare_populations(pop([1] * 10), pop([0] * 10), fil)
    d = rep.as_dict()
    assert d["counts"][FAIL] == 1
    assert d["counts"][CANNOT_RUN] == 1
    assert d["clean"] is False and d["fully_covered"] is False


def test_render_names_both_populations_and_n():
    rep = compare_populations(pop([1, 0]), pop([1, 0]), [Filter("c", cut)],
                              label_a="left", label_b="right")
    out = rep.render()
    assert "left" in out and "right" in out
    assert "n=2" in out
    assert "fully_covered" in out


# --------------------------------------------------------------- the DETAIL text
# The verdict is what a gate reads; the detail is what a HUMAN reads to decide whether to
# act on it. A mutation run on 2026-09-21 killed 40 mutants and left 15, three of them on
# the threshold branches at lines 220-222 -- which only build this text, so weak assertions
# on it left the explanation untested while the verdict looked well covered.


def _only(a, b):
    return compare_populations(a, b, [Filter("c", cut)]).results[0]


def test_detail_names_pp_when_pp_triggered():
    r = _only(pop([1] * 5 + [0] * 5), pop([1] + [0] * 9))   # 40pp, 5x
    assert "pp apart" in r.detail
    assert "40.0pp apart" in r.detail


def test_detail_omits_pp_when_only_ratio_triggered():
    """3pp apart (under threshold) but 4x. The pp clause must NOT appear."""
    r = _only(pop([1] * 4 + [0] * 96), pop([1] * 40 + [0] * 3960))
    assert "pp apart" not in r.detail
    assert "4.0x" in r.detail


def test_detail_names_both_clauses_when_both_trigger():
    r = _only(pop([1] * 5 + [0] * 5), pop([1] + [0] * 9))   # 40pp AND 5x
    assert "pp apart" in r.detail and "x" in r.detail
    assert r.detail.count(",") >= 1, "two reasons are joined by a comma"


def test_detail_says_inf_not_a_number_when_one_side_is_zero():
    r = _only(pop([1] * 10 + [0] * 90), pop([0] * 100))
    assert "ratio inf" in r.detail
    assert "0.0x" not in r.detail


def test_note_is_appended_to_detail_when_given():
    f = Filter("c", cut, note="f59: halves the headline")
    r = compare_populations(pop([1] * 10), pop([0] * 10), [f]).results[0]
    assert "f59: halves the headline" in r.detail


def test_passing_detail_states_commensurate():
    r = _only(pop([1, 0, 0, 0]), pop([1, 0, 0, 0]))
    assert "commensurate" in r.detail
    assert "apart" not in r.detail


# --------------------------------------------------------------- render / as_dict


def test_render_shows_placeholder_for_uncomputable_shares():
    """A CANNOT_RUN row must still RENDER, with a visible gap rather than a fake number."""
    missing = Filter("missing", lambda d: d.no_such_attribute)
    out = compare_populations(pop([1, 0]), pop([1, 0]), [missing]).render()
    assert "CANNOT_RUN" in out
    assert "--" in out, "a missing share renders as a placeholder, never as 0"
    assert "0.00%" not in out


def test_render_shows_percentages_and_diff_for_computed_rows():
    out = compare_populations(pop([1] * 5 + [0] * 5), pop([1] + [0] * 9),
                              [Filter("c", cut)]).render()
    assert "50.00%" in out and "10.00%" in out
    assert "40.0pp" in out


def test_as_dict_pass_count_is_real():
    fil = [Filter("same", cut), Filter("missing", lambda d: d.no_such_attribute)]
    d = compare_populations(pop([1, 0, 0, 0]), pop([1, 0, 0, 0]), fil).as_dict()
    assert d["counts"][PASS] == 1
    assert d["counts"][CANNOT_RUN] == 1
    assert d["counts"][FAIL] == 0


def test_as_dict_labels_shares_by_population_name():
    d = compare_populations(pop([1, 0]), pop([1, 0]), [Filter("c", cut)],
                            label_a="left", label_b="right").as_dict()
    row = d["results"][0]
    assert "share_left" in row and "share_right" in row


# --------------------------------------------------------------- second mutation round
# 2026-09-21: 49 killed / 6 survived. These close four of the six. Each existed because an
# assertion was true for the WRONG reason -- the classic shape where a test passes whether
# or not the branch it names is doing anything.


def test_pass_count_cannot_be_satisfied_by_counting_non_pass():
    """Two PASSes against one of everything else.

    The previous version had 1 PASS and 1 CANNOT_RUN, so `== PASS` and `!= PASS` both
    counted 1 and a negated comparison survived.
    """
    fil = [
        Filter("p1", cut),
        Filter("p2", cut),
        Filter("boom", lambda d: d.no_such_attribute),
    ]
    a, b = pop([1, 0, 0, 0]), pop([1, 0, 0, 0])
    d = compare_populations(a, b, fil).as_dict()
    assert d["counts"][PASS] == 2
    assert d["counts"][CANNOT_RUN] == 1
    assert d["counts"][FAIL] == 0


def test_pp_only_trigger_does_not_claim_a_ratio():
    """50% vs 20%: 30pp apart (fires) but only 2.5x (does not).

    Forcing the ratio branch true would append a ratio clause that is not true of the data,
    which is how a probe's explanation starts lying while its verdict stays right.
    """
    r = _only(pop([1] * 50 + [0] * 50), pop([1] * 20 + [0] * 80))
    assert r.state == FAIL
    assert "pp apart" in r.detail
    assert "x" not in r.detail.split("(", 1)[1], "no ratio clause when ratio is under threshold"


def test_no_note_leaves_no_dangling_separator():
    """A filter with no note must not render an empty ' -- ' tail."""
    r = compare_populations(pop([1] * 10), pop([0] * 10),
                            [Filter("c", cut)]).results[0]
    assert not r.detail.rstrip().endswith("--")
    assert " -- " not in r.detail


def test_module_refuses_to_act_as_a_cli(capsys):
    """main() is a refusal stub on purpose: the populations and filters are POLICY.

    It must SAY so and exit non-zero. A stub that silently exits 0 would let a caller
    believe a comparison ran.
    """
    import json as _json

    from population_probe import main

    rc = main([])
    assert rc == 2
    payload = _json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "library" in payload["message"]


# =====================================================================================
# REGRESSION: adversarial cross-model review, 2026-09-21.
# A second model found three ways this probe -- built to catch "reported clean while
# measuring nothing" -- did exactly that. All three were reproduced before being fixed.
# =====================================================================================


class BadMask(list):
    """A mask that is summable but does not cover the population."""

    def sum(self):
        return sum(self)


def test_zero_length_mask_is_cannot_run_not_a_fully_covered_pass():
    """THE ONE THAT MATTERS. An empty mask divided 0 by n on BOTH sides, compared equal,
    and certified PASS with fully_covered=true."""
    rep = compare_populations(pop([1, 0, 1, 0]), pop([1, 0, 1, 0]),
                              [Filter("empty", lambda d: BadMask())])
    assert rep.results[0].state == CANNOT_RUN
    assert rep.fully_covered is False
    assert "mask covers 0 row(s)" in rep.results[0].detail


def test_mask_shorter_than_population_is_cannot_run():
    """A partial mask measures a different population than the one being reported on."""
    rep = compare_populations(pop([1, 0, 1, 0]), pop([1, 0, 1, 0]),
                              [Filter("partial", lambda d: BadMask([1, 0]))])
    assert rep.results[0].state == CANNOT_RUN
    assert "population has 4" in rep.results[0].detail


def test_non_boolean_mask_cannot_report_a_share_above_one():
    """A numeric mask reported a 500% share and PASSED. A selection cannot exceed its
    population; a mask that does is a count or a score, not a selection."""
    rep = compare_populations(pop([1, 1]), pop([1, 1]),
                              [Filter("numeric", lambda d: BadMask([5, 5]))])
    assert rep.results[0].state == CANNOT_RUN
    assert "cannot exceed its population" in rep.results[0].detail


def test_mask_contract_violation_on_b_side_only_still_reports_a_side():
    def only_a(d):
        return BadMask() if getattr(d, "marker", None) == "B" else cut(d)

    rep = compare_populations(pop([1, 0]), pop([1, 0], marker="B"),
                              [Filter("half", only_a)], label_a="A", label_b="B")
    r = rep.results[0]
    assert r.state == CANNOT_RUN
    assert r.share_a is not None and r.share_b is None
    assert "mask contract violated on B" in r.detail


def test_empty_generator_of_filters_raises():
    """An empty GENERATOR is truthy, so the truthiness guard passed it through and the
    probe returned clean=true, fully_covered=true over zero filters."""
    with pytest.raises(ValueError, match="ZERO filters"):
        compare_populations(pop([1, 0]), pop([1, 0]), (f for f in []))


def test_non_empty_generator_of_filters_still_works():
    """The fix materialises the iterable; it must not consume it before use."""
    rep = compare_populations(pop([1, 0]), pop([1, 0]),
                              (Filter(n, cut) for n in ("x", "y")))
    assert len(rep.results) == 2
    assert rep.fully_covered is True


class LengthlessMask:
    """Summable, but with no length: it cannot prove what population it covered."""

    def sum(self):
        return 1


def test_mask_without_a_length_is_cannot_run_and_says_why():
    """Distinct from a wrong-length mask, and the DETAIL must distinguish them.

    Both end in CANNOT_RUN, so a test asserting only the state passes whichever branch
    ran. A mutation survivor at the `m is None` check proved it: forcing that branch
    false still raised, via the length-mismatch path, with a different and misleading
    explanation. The verdict was right and the reason was wrong, which is the failure the
    whole detail field exists to prevent.
    """
    rep = compare_populations(pop([1, 0]), pop([1, 0]),
                              [Filter("lengthless", lambda d: LengthlessMask())])
    r = rep.results[0]
    assert r.state == CANNOT_RUN
    assert "no length" in r.detail
    assert "row(s) but the population has" not in r.detail


def test_documented_exit_recipe_keys_on_coverage_not_clean():
    """The module's own usage recipe taught `sys.exit(0 if report.clean else 2)`, which
    exits 0 when every filter CANNOT_RUNs -- the documentation teaching the collapse the
    module exists to prevent. Pinned here because a poisoned source re-emits the defect
    faster than a guard can catch it."""
    import population_probe as pp

    doc = pp.__doc__ or ""
    # Assert the ACTIVE recipe line. The docstring also NAMES the old one in prose, on
    # purpose -- a fix that erases the defect it corrected teaches nothing.
    assert "sys.exit(0 if report.fully_covered else 2)" in doc
    assert "sys.exit(0 if report.clean else 2)" not in doc
