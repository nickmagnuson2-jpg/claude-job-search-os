"""One scoring mechanism, two callers, one answer.

WHY THIS EXISTS (2026-09-14). `career_scanner/scanner.py` scored roles with the
deterministic `scorer.score_role()`. `/scan-jobs` scored the same question with a prose
rubric in its SKILL.md ("80-100% shortlisted, 60-79% maybe, ...") evaluated by the model
at runtime. Two implementations of "how well does this role fit," which is the
duplicated-domain-logic case: every consumer wants the SAME answer for the same role, so
it is mechanism, not policy. This file is the parity guard for the consolidation.
"""
import json
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


def test_cli_and_library_agree_on_every_role():
    """The CLI must be a thin wrapper, not a second implementation."""
    ctx = load_scoring_context(REPO)
    direct = [score_role(r, ctx) for r in ROLES]

    proc = subprocess.run(
        [sys.executable, "-B", "-m", "tools.career_scanner.cli", "--score-roles",
         "--repo-root", str(REPO)],
        input=json.dumps(ROLES), capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, f"CLI failed: {proc.stderr[-800:]}"
    via_cli = [r["score"] for r in json.loads(proc.stdout)["roles"]]

    assert via_cli == direct, (
        "the --score-roles CLI and scorer.score_role disagree on identical input. "
        "The CLI exists so /scan-jobs can reach the SAME scorer scanner.py uses; if it "
        f"computes its own answer the consolidation is fiction. cli={via_cli} direct={direct}")


def test_the_scorer_actually_discriminates_these_three():
    """A parity test passes trivially if the scorer returns a constant. Pin the ordering."""
    ctx = load_scoring_context(REPO)
    s = {r["title"]: score_role(r, ctx) for r in ROLES}
    assert s["Deployment Strategist"] > s["Chief of Staff"], (
        "'deployment strateg' is a positive_title_pattern and 'chief of staff' is a "
        f"not_fit_title_pattern in fit-spec.yaml; the scorer must separate them. Got {s}")
    assert s["Deployment Strategist"] > s["Enterprise Account Executive"], (
        f"quota/land-and-expand language is a scope_disqualifier. Got {s}")


def test_cli_rejects_junk_instead_of_scoring_it():
    """A guard that cannot read its input must refuse, not emit a confident number."""
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "tools.career_scanner.cli", "--score-roles",
         "--repo-root", str(REPO)],
        input="not json at all", capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode != 0, (
        "malformed stdin must exit nonzero rather than fall through to a score -- "
        "failing open on unreadable input is the defect class this repo keeps hitting")


def test_cli_refuses_non_object_elements_with_structured_output():
    """Found by cross-model review 2026-09-14 (F3): [1, null] raised AttributeError."""
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "tools.career_scanner.cli", "--score-roles",
         "--repo-root", str(REPO)],
        input="[1, null]", capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2, f"expected structured refusal, got rc={proc.returncode}"
    assert json.loads(proc.stdout)["code"] == "non_object_element", (
        "a malformed element must produce a structured refusal, not a traceback -- "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-300:]!r}")


def test_the_description_field_is_aliased_to_the_schema_field():
    """Found by cross-model review 2026-09-14 (F4): score_role reads `description_plain`.

    A caller assembling a role by hand reaches for `description`. The alias lives in
    score_role, NOT in the CLI -- putting it in the CLI made the CLI a second
    implementation and the parity test above caught it immediately.

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
