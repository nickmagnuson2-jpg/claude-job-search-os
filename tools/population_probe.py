#!/usr/bin/env python3
"""The FOUNDATION PROBE: is a comparison's denominator commensurable on both sides?

WHY THIS EXISTS, AND WHY IT IS A MODULE IN tools/ RATHER THAN A SCRIPT IN AN ENGAGEMENT.

`d1.metric_roles` can declare `population_commensurability` a GUARDRAIL, and check F8b will
then report PASS -- by NOT MATCHING. F8b looks for a guardrail metric used as a ranking
`input` by some element, but commensurability is not a measurable an element ranks on. It is
a PROPERTY OF A COMPARISON. So the right guardrail gets declared, the gate that reads
guardrails cannot express it, and the run is green while nothing measured the thing.

That is not a bug in F8b. It is a kind mismatch: **a guardrail that is a property needs an
INSTRUMENT, not a name in a registry.** This module is that instrument.

WHAT IT CHECKS. Given two populations being compared and the filters that define or describe
them, compute EVERY filter on BOTH sides and flag any whose shares diverge. The failure it is
built against is not a wrong number -- it is a correct number measured across two populations
that are not the same kind of thing, which no arithmetic check can see.

THREE-STATE, DELIBERATELY. Every filter reports PASS, FAIL or CANNOT_RUN, and a filter that
could not be evaluated on one side is NEVER reported as a pass. `clean` is true only when
there are zero FAILs; `fully_covered` only when there are additionally zero CANNOT_RUNs. Read
both. This mirrors tools/check_frame_integrity.py on purpose: collapsing CANNOT_RUN into PASS
produces a green result that means nothing and reads like coverage.

EMPTY INPUT IS AN ERROR, NOT A NEGATIVE RESULT. An empty side raises. A probe that scans
nothing and returns "no asymmetries found" is the defect this whole family is named for.

USAGE (the policy -- which filters matter -- stays with the caller):

    from population_probe import compare_populations, Filter

    report = compare_populations(
        a=new_service_rows, b=prior_service_rows,
        label_a="new service", label_b="prior service",
        filters=[
            Filter("junk", lambda d: d["cr"].isin(JUNK)),
            Filter("undispositioned", lambda d: d["cr"].eq("No Disposition")),
        ],
    )
    print(report.render())
    # EXIT ON COVERAGE, NOT ON `clean`. An earlier version of this recipe said
    # `0 if report.clean else 2`, which exits 0 when EVERY filter CANNOT_RUNs -- the
    # module's own documentation teaching the collapse the module exists to prevent.
    # A caller who copies this gets a silent green from a run that measured nothing.
    sys.exit(0 if report.fully_covered else 2)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Sequence

PASS = "PASS"
FAIL = "FAIL"
CANNOT_RUN = "CANNOT_RUN"

# Defaults chosen from measured history, not taste. See tools/tests or the engagement
# validation harness: the three known real asymmetries were all above 18pp,
# and the intended non-finding sat well under 5pp. A threshold above ~18pp would have
# missed a defect that actually shipped.
DEFAULT_THRESHOLD_PP = 5.0
DEFAULT_RATIO = 3.0


@dataclass(frozen=True)
class Filter:
    """A named cut, applied identically to both sides.

    `fn` takes a population and returns a boolean mask. It should raise (or reference a
    missing column) when it cannot be computed -- that becomes CANNOT_RUN, not a pass.
    """

    name: str
    fn: Callable
    note: str = ""


@dataclass
class FilterResult:
    name: str
    state: str
    share_a: float | None
    share_b: float | None
    n_a: int
    n_b: int
    detail: str

    @property
    def diff_pp(self) -> float | None:
        if self.share_a is None or self.share_b is None:
            return None
        return abs(self.share_a - self.share_b) * 100.0


@dataclass
class Report:
    label_a: str
    label_b: str
    n_a: int
    n_b: int
    results: list[FilterResult] = field(default_factory=list)
    threshold_pp: float = DEFAULT_THRESHOLD_PP
    ratio_threshold: float = DEFAULT_RATIO

    @property
    def fails(self) -> list[FilterResult]:
        return [r for r in self.results if r.state == FAIL]

    @property
    def cannot_run(self) -> list[FilterResult]:
        return [r for r in self.results if r.state == CANNOT_RUN]

    @property
    def clean(self) -> bool:
        """Zero FAILs. Says nothing about coverage -- read `fully_covered` too."""
        return not self.fails

    @property
    def fully_covered(self) -> bool:
        """Zero FAILs AND zero CANNOT_RUNs. The only state that means 'checked'."""
        return self.clean and not self.cannot_run

    def as_dict(self) -> dict:
        return {
            "populations": {self.label_a: self.n_a, self.label_b: self.n_b},
            "threshold_pp": self.threshold_pp,
            "ratio_threshold": self.ratio_threshold,
            "clean": self.clean,
            "fully_covered": self.fully_covered,
            "counts": {
                PASS: sum(r.state == PASS for r in self.results),
                FAIL: len(self.fails),
                CANNOT_RUN: len(self.cannot_run),
            },
            "results": [
                {
                    "filter": r.name,
                    "state": r.state,
                    f"share_{self.label_a}": r.share_a,
                    f"share_{self.label_b}": r.share_b,
                    "diff_pp": r.diff_pp,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }

    def render(self) -> str:
        head = (
            f"population commensurability: {self.label_a} n={self.n_a:,} "
            f"vs {self.label_b} n={self.n_b:,}\n"
            f"clean={self.clean}  fully_covered={self.fully_covered}  "
            f"(threshold {self.threshold_pp}pp or {self.ratio_threshold}x)"
        )
        lines = [head, ""]
        for r in self.results:
            a = "  --  " if r.share_a is None else f"{r.share_a:6.2%}"
            b = "  --  " if r.share_b is None else f"{r.share_b:6.2%}"
            d = "" if r.diff_pp is None else f"{r.diff_pp:6.1f}pp"
            lines.append(f"  {r.state:11} {r.name:24} {a} vs {b} {d:>9}  {r.detail}")
        return "\n".join(lines)


class MaskContractError(Exception):
    """The filter did not return one boolean decision per population member.

    NOT a ValueError raised to the caller: a bad mask is a failure to MEASURE this
    filter, which is CANNOT_RUN, not a crash and emphatically not a PASS.
    """


def _share(mask, n: int) -> float:
    """Share of the population the mask selects, with the conservation check inline.

    THE DEFECT THIS EXISTS FOR (found by adversarial review, 2026-09-21): the first
    version divided `mask.sum()` by `n` and asked nothing else. A filter returning an
    EMPTY mask therefore produced 0.0 on both sides, compared equal, and was certified
    PASS with `fully_covered=true` -- the instrument built to catch "measured nothing,
    reported clean" doing exactly that. A non-boolean mask was likewise accepted and
    could report a 500% share.

    One decision per member is the conservation anchor. Without it, "the filter ran" is
    being taken as proof that "the filter measured the population".
    """
    try:
        m = len(mask)
    except TypeError:
        m = None
    if m is None:
        raise MaskContractError("mask has no length; cannot prove it covered the population")
    if m != n:
        raise MaskContractError(
            f"mask covers {m} row(s) but the population has {n}; a filter must return one "
            "decision per member or it has measured a different population")
    try:
        total = float(mask.sum())
    except (TypeError, ValueError) as exc:
        raise MaskContractError(f"mask is not summable: {exc}") from None
    if not (0.0 <= total <= n):
        raise MaskContractError(
            f"mask sums to {total} over {n} row(s); a boolean mask cannot exceed its "
            "population, so this is a count or a score, not a selection")
    return total / n


def compare_populations(
    a,
    b,
    filters: Sequence[Filter],
    *,
    label_a: str = "A",
    label_b: str = "B",
    threshold_pp: float = DEFAULT_THRESHOLD_PP,
    ratio_threshold: float = DEFAULT_RATIO,
) -> Report:
    """Compute every filter on BOTH populations and flag divergence.

    Raises ValueError on an empty side or an empty filter list: an empty scan is an error,
    not a negative result. See memory/feedback_guard_must_hard_abort_on_empty_input.
    """
    # An empty GENERATOR is truthy, so a truthiness guard lets it through and the probe
    # returns a clean, fully-covered report over zero filters. Materialise first: the
    # guard must test the POPULATION of filters, not the container's truthiness.
    filters = list(filters)
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        raise ValueError(
            f"population_probe received an EMPTY side ({label_a}={n_a}, {label_b}={n_b}); "
            "an empty comparison is an error, not a clean result"
        )
    if not filters:
        raise ValueError(
            "population_probe received ZERO filters; a probe that checks nothing and "
            "reports clean is the defect this module exists to prevent"
        )

    rep = Report(label_a, label_b, n_a, n_b, threshold_pp=threshold_pp,
                 ratio_threshold=ratio_threshold)

    for f in filters:
        try:
            mask_a = f.fn(a)
            sa = _share(mask_a, n_a)
        except MaskContractError as exc:
            rep.results.append(FilterResult(
                f.name, CANNOT_RUN, None, None, n_a, n_b,
                f"mask contract violated on {label_a}: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - any failure to compute is CANNOT_RUN
            rep.results.append(FilterResult(
                f.name, CANNOT_RUN, None, None, n_a, n_b,
                f"not computable on {label_a}: {type(exc).__name__}: {exc}"))
            continue
        try:
            mask_b = f.fn(b)
            sb = _share(mask_b, n_b)
        except MaskContractError as exc:
            rep.results.append(FilterResult(
                f.name, CANNOT_RUN, sa, None, n_a, n_b,
                f"mask contract violated on {label_b}: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001
            rep.results.append(FilterResult(
                f.name, CANNOT_RUN, sa, None, n_a, n_b,
                f"not computable on {label_b}: {type(exc).__name__}: {exc}"))
            continue

        diff_pp = abs(sa - sb) * 100.0
        lo, hi = sorted((sa, sb))
        ratio = (hi / lo) if lo > 0 else float("inf")

        if diff_pp > threshold_pp or (ratio > ratio_threshold and hi > 0.005):
            reasons = []
            if diff_pp > threshold_pp:
                reasons.append(f"{diff_pp:.1f}pp apart")
            if ratio > ratio_threshold and hi > 0.005:
                reasons.append("ratio inf" if lo == 0 else f"{ratio:.1f}x")
            detail = f"{f.name} differs across the two sides ({', '.join(reasons)})"
            if f.note:
                detail += f" -- {f.note}"
            rep.results.append(FilterResult(f.name, FAIL, sa, sb, n_a, n_b, detail))
        else:
            rep.results.append(FilterResult(
                f.name, PASS, sa, sb, n_a, n_b, "commensurate within threshold"))

    return rep


def main(argv: list[str] | None = None) -> int:
    """No CLI mode on purpose.

    This module needs a caller to supply the two populations and the filters that matter,
    which is POLICY and cannot live here. A CLI that guessed them would produce a
    confident answer about a comparison nobody specified. Import it instead.
    """
    print(json.dumps({
        "status": "error",
        "message": ("population_probe is a library, not a CLI. Import compare_populations "
                    "and supply both populations plus the filters that define them. "
                    "See the module docstring."),
    }))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
