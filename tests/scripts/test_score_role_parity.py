"""One scoring mechanism, two callers, one answer.

WHY THIS EXISTS (2026-09-14). `career_scanner/scanner.py` scored roles with the
deterministic `scorer.score_role()`. `/scan-jobs` scored the same question with a prose
rubric in its SKILL.md ("80-100% shortlisted, 60-79% maybe, ...") evaluated by the model
at runtime. Two implementations of "how well does this role fit," which is the
duplicated-domain-logic case: every consumer wants the SAME answer for the same role, so
it is mechanism, not policy. This file is the parity guard for the consolidation.

2026-11-24: the --score-roles CLI wrapper and its parity tests were deleted. /scan-jobs
calls score_role through tools/scan_jobs.py, so the CLI had no caller left.
"""
import pytest
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.career_scanner.scorer import load_scoring_context, score_role  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

ROLES = [
    {"title": "Deployment Strategist", "company": "Someco",
     "location": "San Francisco Office",
     "description": "Embed with customer teams, scope and deliver AI agent workflows."},
    {"title": "Chief of Staff", "company": "Someco", "location": "San Francisco",
     "description": "Own the operating cadence for the exec team."},
    {"title": "Enterprise Account Executive", "company": "Someco",
     "location": "San Francisco",
     "description": "Carry a quota, land and expand a book of business."},
]


@pytest.mark.live_data
def test_the_scorer_actually_discriminates_these_three():
    """A parity test passes trivially if the scorer returns a constant. Pin the ordering.

    The negative comes from the live fit-spec, not from a title typed here. This test
    used to hard-code one title as the not-fit example; when fit-spec.yaml moved that
    title to positive_title_patterns, the test failed on a premise the data no longer
    held. Reading the same file the scorer reads keeps the test from drifting from it.
    """
    ctx = load_scoring_context(REPO)
    not_fit = [str(p) for p in (ctx["fit_spec"].get("not_fit_title_patterns") or [])]
    if not not_fit:
        pytest.skip("fit-spec.yaml has no not_fit_title_patterns, so there is no "
                    "not-fit title to order against")
    negative = {"title": not_fit[0].title(), "company": "Someco",
                "location": "San Francisco",
                "description": "Own the team's plan and report on it weekly."}
    s = {r["title"]: score_role(r, ctx) for r in ROLES + [negative]}
    assert s["Deployment Strategist"] > s[negative["title"]], (
        f"'deployment strateg' is a positive_title_pattern and {not_fit[0]!r} is a "
        f"not_fit_title_pattern in fit-spec.yaml; the scorer must separate them. Got {s}")
    assert s["Deployment Strategist"] > s["Enterprise Account Executive"], (
        f"quota/land-and-expand language is a scope_disqualifier. Got {s}")

    # The orderings above can hold on the base dimensions alone: a not-fit title also
    # scores lower on title match, so they pass even if the not-fit list is ignored.
    # Compare each role against the same scorer with the not-fit list emptied.
    no_not_fit = {**ctx, "fit_spec": {**ctx["fit_spec"], "not_fit_title_patterns": []}}
    assert score_role(negative, ctx) < score_role(negative, no_not_fit), (
        f"{not_fit[0]!r} is a not_fit_title_pattern, so it must lower the score of a "
        "role with that title")
    for title in ctx.get("target_titles") or []:
        role = {"title": title, "company": "Someco", "location": "San Francisco",
                "description": "Own the team's plan and report on it weekly."}
        assert score_role(role, ctx) >= score_role(role, no_not_fit), (
            f"{title!r} is an Open to Work title in goals.md, but a not_fit_title_pattern "
            "in fit-spec.yaml lowers its score; goals.md says every scorer must count "
            "each Open to Work title as in-lane")


def test_score_roles_flag_is_gone():
    """--score-roles was deleted 2026-11-24 (l1b-funnel-fixes-1124): it had no caller,
    since /scan-jobs scores through tools/scan_jobs.py (ce7211c), and it bypassed the
    goals.md staleness gate. One entry point to the scorer for scans: scanner.py."""
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "tools.career_scanner.cli", "--score-roles",
         "--repo-root", str(REPO)],
        input="[]", capture_output=True, text=True, cwd=str(REPO), timeout=60)
    assert proc.returncode == 2, f"rc={proc.returncode} stdout={proc.stdout[-300:]!r}"
    assert "unrecognized arguments: --score-roles" in proc.stderr, proc.stderr[-300:]


def test_the_description_field_is_aliased_to_the_schema_field():
    """Found by cross-model review 2026-09-14 (F4): score_role reads `description_plain`.

    A caller assembling a role by hand reaches for `description`. The alias lives in
    score_role, not in any caller, so every caller gets it.

    NOTE: this pins the ALIAS only. Whether the description then moves the score is a
    separate, currently-FAILING property tracked by tools/scoring_probe.py (I1): a real
    description scores 0.0 on keyword overlap while an empty one scores 3.0. Do not add
    an assertion here expecting rich > empty until W1 in
    data/workstreams/scoring-integrity.md is fixed; it will fail for a real reason.
    """
    ctx = load_scoring_context(REPO)
    base = {"title": "Deployment Strategist", "location": "San Francisco"}
    text = "Embed with customers, scope and prototype AI agent deployments."
    assert (score_role(dict(base, description=text), ctx)
            == score_role(dict(base, description_plain=text), ctx)), (
        "`description` must be treated as `description_plain`; otherwise a caller using "
        "the obvious key silently scores against an empty description")
