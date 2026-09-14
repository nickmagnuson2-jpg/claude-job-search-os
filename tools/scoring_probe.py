"""scoring_probe.py — measure whether the fit scorers still discriminate, on REAL data.

    PYTHONIOENCODING=utf-8 python3 tools/scoring_probe.py [--json]

Why this exists (2026-09-14). Both scorers passed their unit suites while one of them was
inverted on live input: a role WITH a description scored 0.0 on the keyword dimension and
a role with NO description scored 3.0, so having a job ad was a penalty on 20% of the
weight. Its guard test passed, because that test's fixture contained only long skill
phrases and therefore always tripped the "no usable terms" branch. The defect was only
visible against the real `profile.md`.

So this probe never uses a fixture. It loads the live scoring context and reports
invariant violations, dead dimensions, and range compression. It is the derivable-state
probe for the scoring-integrity workstream: a record a run cannot skip, rather than prose
asking a future reader to re-measure.

Exit codes: 0 all invariants hold, 1 at least one violated, 2 the probe could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({100*n//d}%)" if d else "")


def probe() -> dict:
    from tools.career_scanner import company_scorer as cs
    from tools.career_scanner import scorer as rs

    ctx = rs.load_scoring_context(_REPO_ROOT)
    findings: list[dict] = []

    # --- I1: a present description must never score below a missing one -------------
    # The 2026-09-14 inversion. Uses the LIVE context, not a fixture.
    jd = ("6+ years customer-facing. Embed with customer teams, scope and prototype AI "
          "agent deployments, own integrations and APIs, SQL, onboarding and training.")
    with_d = rs._score_keyword_overlap({"description_plain": jd}, ctx)
    without_d = rs._score_keyword_overlap({"description_plain": ""}, ctx)
    if with_d < without_d:
        findings.append({
            "id": "I1", "severity": "P0", "dimension": "keyword_overlap",
            "detail": f"a real description scores {with_d} and an empty one {without_d}; "
                      "having a job ad is a penalty",
            "cause": "profile.md yields prose bullets, not matchable terms"})

    # --- I2: no dimension may be constant across obviously different roles ----------
    good = {"title": "Deployment Strategist", "location": "San Francisco",
            "description_plain": jd}
    bad = {"title": "Enterprise Account Executive", "location": "San Francisco",
           "description_plain": "Carry a quota, land and expand a book of business."}
    dims = {"title": rs._score_title_match, "seniority": rs._score_seniority_match,
            "industry": rs._score_industry_match, "keyword": rs._score_keyword_overlap}
    for name, fn in dims.items():
        a, b = fn(good, ctx), fn(bad, ctx)
        if a == b:
            findings.append({
                "id": "I2", "severity": "P1", "dimension": name,
                "detail": f"scores {a} for BOTH an in-lane and an off-lane role; the "
                          "dimension carries no signal between them"})

    # --- I3: the usable-term set must be non-trivial --------------------------------
    skills = ctx.get("skills") or []
    usable = [s for s in skills if len(s.split()) <= rs._MAX_USABLE_TERM_WORDS]
    if len(usable) < 5:
        findings.append({
            "id": "I3", "severity": "P1", "dimension": "keyword_overlap",
            "detail": f"only {len(usable)} of {len(skills)} extracted skills are short "
                      f"enough to match a posting ({usable})",
            "cause": "one barely-usable term disarms the 'no usable terms' neutral guard "
                     "while still matching nothing"})

    # --- I4: observed range on the labelled corpus ----------------------------------
    corpus = _REPO_ROOT / "data" / "calibration" / "lane-b-screen-2026-09-14.json"
    company_range = None
    if corpus.exists():
        import yaml
        d = json.loads(corpus.read_text(encoding="utf-8"))
        ps = yaml.safe_load((_REPO_ROOT / "data" / "discover-presets.yaml")
                            .read_text(encoding="utf-8"))
        ps = ps.get("presets") or ps
        W = {"stage": 0.50, "sector": 0.30, "keyword": 0.20}
        W.update(ps["lane-b"].get("scoring_weights") or {})
        KW = ps["lane-b"].get("keywords") or []
        scores = [cs.score_company(
            {"name": c["name"], "description": c["description"],
             "location": c["verified_location"], "stage_text": c["description"]},
            ctx, weights=W, keywords=KW)["score"] for c in d["companies"]]
        company_range = {"n": len(scores), "min": min(scores), "max": max(scores),
                         "distinct": len(set(scores)), "nominal_scale": "1-10"}
        if company_range["distinct"] <= 4:
            findings.append({
                "id": "I4", "severity": "P2", "dimension": "company_score_range",
                "detail": f"{len(scores)} labelled companies occupy only "
                          f"{company_range['distinct']} distinct values "
                          f"({company_range['min']}-{company_range['max']}) on a 1-10 "
                          "scale; the score cannot rank what it cannot separate"})

    return {"status": "ok",
            "invariants_checked": 4,
            "violations": len(findings),
            "findings": findings,
            "keyword_dim": {"with_description": with_d, "without_description": without_d,
                            "usable_terms": usable, "total_skills": len(skills)},
            "company_score_range": company_range}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    try:
        r = probe()
    except Exception as e:  # a probe that cannot run must say so, not report health
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))
        return 2

    if args.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"scoring probe: {r['violations']} violation(s) "
              f"across {r['invariants_checked']} invariants")
        for f in r["findings"]:
            print(f"  [{f['severity']}] {f['id']} {f['dimension']}: {f['detail']}")
            if f.get("cause"):
                print(f"        cause: {f['cause']}")
        k = r["keyword_dim"]
        print(f"  keyword dim: with-description {k['with_description']}, "
              f"without {k['without_description']}, "
              f"usable terms {_pct(len(k['usable_terms']), k['total_skills'])}")
        if r["company_score_range"]:
            c = r["company_score_range"]
            print(f"  company scores on {c['n']} labelled companies: {c['min']}-{c['max']}, "
                  f"{c['distinct']} distinct values on a {c['nominal_scale']} scale")
    return 1 if r["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
