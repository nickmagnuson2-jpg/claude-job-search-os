#!/usr/bin/env python3
"""
scorer_eval.py — read-only evaluation harness for the job-search scoring systems.

Step 1 of the scoring-audit redesign (output/analysis/071426-scoring-audit-
recommendations.md, "Cross-cutting: calibration & ground truth"): a ZERO-write
outcome resolver + label inventory over data Nick already generates. It answers
the gating question before any model change — *do enough labels even exist to
calibrate a scorer, and is any scorer obviously mis-ordered today?*

Ground truth (weak labels, byproducts of Nick working his search):
  - which-company scorer  -> data/job-pipeline.md. positive = the company reached
    a real engagement stage (phone screen / interview / onsite / offer / final
    round), i.e. mutual interest was established, REGARDLESS of the eventual close.
    negative = applied/considered then closed WITHOUT reaching engagement (an early
    screen-out). unlabeled = backlog / researching / applied-and-still-pending.
  - who-to-contact scorer -> data/outreach-log.md Status. positive = 'Replied'
    (a received reply). not-positive = 'Sent' / 'No reply' (acted on, no reply yet).
    'Drafted' = unlabeled (not yet acted). Never-contacted people are excluded.

PU / implicit-feedback discipline (per the audit doc): a non-reply or a
not-yet-pursued item is `unlabeled`, NEVER a negative. A candidate is never docked
for silence. precision@k is only defensible over items Nick actually acted on.

Why no precision@k number yet: computing precision@k of a scorer's PAST output
needs the scores it produced then (the original candidate features), which are not
stored. That is exactly what the prediction log (migration step 2) will capture.
Until then this tool reports LABEL AVAILABILITY + a readiness verdict, so Nick
knows whether calibration is live now or still deferred.

Stage classification reuses tools/stage_vocab.py (single source of truth). The
markdown row-split here is mechanical; a parity test pins its active-row count to
pipe_read's so the two cannot drift (fable-audit Theme 2 lesson).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/scorer_eval.py report --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/scorer_eval.py report --repo-root . --json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage_vocab  # single source of truth for stage classification

# Engagement in the STAGE field is reliable (the stage names the process state), so
# the full keyword set applies there. Excludes "applied"/"researching" (contact made,
# no advancement). Leading word-boundary so stems catch ("interview"->"Interviewing").
STAGE_ENGAGEMENT_KEYWORDS = (
    "screen", "interview", "onsite", "offer", "phone",
    "round", "case", "loop", "final", "recruiter",
)

# In the freeform NOTES field the same bare words false-positive on company
# DESCRIPTIONS ("we offer...", "use case", "final product"), so only high-precision
# PROCESS phrases count there. Anything subtler is left to the LLM extraction pass,
# which reads Nick's first-person context to tell a process event from a description.
NOTES_ENGAGEMENT_PHRASES = (
    "recruiter screen", "phone screen", "executive screen", "screen passed",
    "screen scheduled", "onsite", "interview", "final round", "debrief",
    "hiring manager call", "hm call",
)

# Signals that NICK declined/withdrew of his own accord (his preference, NOT a
# company fit-outcome). Per the calibration taxonomy these are EXCLUDED from the
# positive/negative resolved set: a company Nick self-passed on may still be a
# great fit he skipped for timing/preference. Detected only when NO mutual
# engagement occurred first (an engaged-then-Nick-withdrew company is still a
# positive fit signal — mutual interest existed).
NICK_DECLINE_SIGNALS = (
    "passed (self", "considered - passed", "considered-passed",
    "withdrawn", "skipped", "self-passed", "deprioritized",
)

# Below this many resolved (positive + negative) outcomes, precision@k swings on a
# single lucky reply and any "it works" figure is inflated (audit doc: n=50 hit
# AUC 0.95 on pure noise). Do not report a metric under this floor.
MIN_RESOLVED_FOR_METRIC = 30

POSITIVE, NEGATIVE, NICK_DECLINED, UNLABELED = (
    "positive", "negative", "nick-declined", "unlabeled")

import re


def _reached_engagement(stage: str, notes: str = "") -> bool:
    """True if a real process event (screen/interview/onsite/...) is evident.

    Stage field: full keyword set (reliable). Notes field: high-precision process
    phrases only, to avoid matching product-description words.
    """
    st = (stage or "").lower()
    if any(re.search(rf"\b{re.escape(k)}", st) for k in STAGE_ENGAGEMENT_KEYWORDS):
        return True
    nt = (notes or "").lower()
    return any(p in nt for p in NOTES_ENGAGEMENT_PHRASES)


def _nick_declined(stage: str) -> bool:
    s = (stage or "").lower()
    return any(sig in s for sig in NICK_DECLINE_SIGNALS)


def classify_company_outcome(stage: str, notes: str = "") -> str:
    """Map a freeform pipeline stage (+ notes) to a weak outcome label.

    Priority (first match wins):
      positive      = mutual engagement occurred (screen/interview/onsite/offer),
                      found in EITHER the stage or the notes, regardless of who
                      eventually closed it.
      nick-declined = Nick self-passed / withdrew / deprioritized and no engagement
                      occurred — his preference, excluded from pos/neg.
      negative      = terminal (they closed/rejected) without engagement — an early
                      screen-out.
      unlabeled     = backlog / researching / applied-and-pending (outcome unknown).

    Notes are scanned for engagement because the pipeline's Stage field holds only
    the CURRENT state ("Closed - rejected") and loses that a company reached a
    screen/onsite first — that history survives in the Notes cell.
    """
    if _reached_engagement(stage, notes):
        return POSITIVE
    if _nick_declined(stage):
        return NICK_DECLINED
    if stage_vocab.is_terminal_stage(stage):
        return NEGATIVE
    return UNLABELED


# ---------------------------------------------------------------------------
# Pipeline (which-company) resolver
# ---------------------------------------------------------------------------

def _pipeline_rows(content: str):
    """Yield (company, stage, notes) for every real table row (incl. closed).

    Mechanical split matching the documented column order
    (0=Company,1=Role,2=Stage,...,6=Notes). Classification lives in stage_vocab.
    """
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip header + separator rows.
        if re.match(r"\|\s*(Company|:?-{2,})", line):
            continue
        if re.match(r"\|\s*-{2,}", line):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3:
            continue
        company = cols[0]
        stage = cols[2]
        notes = cols[6] if len(cols) > 6 else ""
        if not company or company == "---":
            continue
        yield company, stage, notes


# The verified ledger (data/calibration/company-outcomes.json) is produced by the
# extract-verify workflow: each company's outcome is extracted from Nick's
# first-person sources and independently re-derived by a fresh-context verifier. Its
# labels are AUTHORITATIVE over the deterministic stage classification (which only
# sees the pipeline's current stage + notes and mislabels engaged-then-closed
# companies).
#
# TWO-AXIS MODEL (2026-07-15 hand-labeling, grey-area-rules-hardened.md): the ledger
# now carries `fit_verdict` (Axis 1 — the SCORER TARGET) and `market_response`
# (Axis 2 — did the market engage). fit_verdict is what a which-company scorer is
# trained to reproduce: a company Nick judged a fit / pursued earnestly is the
# positive; his own reasoned declines are the highest-signal negatives. The old
# single-axis `final_label` (engaged/rejected-early/...) conflated fit with market
# outcome; it is kept only as a fallback for records predating the two-axis pass.
#
# fit_verdict → scorer taxonomy:
#   fit      → positive  (judged a fit / pursued earnestly)
#   not-fit  → negative  (reasoned fit-negative: domain/role-shape/whole-lane miss)
#   neutral  → EXCLUDED   (logistics/geo/comp/culture knockout, counterfactual-yes —
#                          a preference knockout, never a fit outcome; bucketed with
#                          nick-declined so it drops out of the resolved set)
#   unknown  → unlabeled (no matching-shape role / never assessed / in-flight)
FIT_VERDICT_MAP = {
    "fit": POSITIVE,
    "not-fit": NEGATIVE,
    "neutral": NICK_DECLINED,  # excluded from resolved (preference knockout, not fit)
    "unknown": UNLABELED,
}

# Legacy fallback for any record lacking fit_verdict (pre-two-axis extract runs).
LEDGER_LABEL_MAP = {
    "engaged": POSITIVE,
    "rejected-early": NEGATIVE,
    "nick-declined": NICK_DECLINED,
    "unlabeled": UNLABELED,
}


def scorer_label(rec: dict) -> str | None:
    """Map a ledger record to this module's scorer taxonomy, fit_verdict first.

    Returns None if the record carries neither a recognized fit_verdict nor a
    recognized final_label (caller falls back to deterministic stage classification).
    """
    fv = rec.get("fit_verdict")
    if fv in FIT_VERDICT_MAP:
        return FIT_VERDICT_MAP[fv]
    fl = rec.get("final_label")
    if fl in LEDGER_LABEL_MAP:
        return LEDGER_LABEL_MAP[fl]
    return None


def load_verified_ledger(repo_root: Path) -> dict:
    """Return {entity: record} from the verified ledger, or {} if absent."""
    path = repo_root / "data" / "calibration" / "company-outcomes.json"
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return {r["entity"]: r for r in records if isinstance(r, dict) and r.get("entity")}


def resolve_company_outcomes(repo_root: Path) -> list:
    path = repo_root / "data" / "job-pipeline.md"
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    verified = load_verified_ledger(repo_root)
    out = []
    for company, stage, notes in _pipeline_rows(content):
        det = classify_company_outcome(stage, notes)
        rec = verified.get(company)
        verified_label = scorer_label(rec) if rec else None
        if verified_label is not None:
            # Verified ledger wins: first-person evidence + fresh-context verify,
            # keyed on the fit_verdict axis (falls back to legacy final_label).
            label, source = verified_label, "verified"
        else:
            label, source = det, "deterministic"
        out.append({"entity": company, "stage": stage,
                    "label": label, "deterministic_label": det, "source": source})
    return out


# ---------------------------------------------------------------------------
# Outreach (who-to-contact) resolver
# ---------------------------------------------------------------------------

def classify_contact_outcome(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "replied":
        return POSITIVE
    if s in ("sent", "no reply"):
        return NEGATIVE  # acted on, no positive outcome (precision denominator)
    return UNLABELED  # 'Drafted' or unknown -> not yet acted


def resolve_contact_outcomes(repo_root: Path) -> list:
    path = repo_root / "data" / "outreach-log.md"
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    out = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        # Columns: Date | Skill | Channel | Recipient | Company | Subject | Status
        if len(cols) < 7:
            continue
        if cols[0] in ("Date", "") or set(cols[0]) <= set("-: "):
            continue
        recipient, status = cols[3], cols[6]
        if not recipient:
            continue
        out.append({"entity": recipient, "company": cols[4], "status": status,
                    "label": classify_contact_outcome(status)})
    return out


# ---------------------------------------------------------------------------
# Inventory + readiness
# ---------------------------------------------------------------------------

def _tally(rows: list) -> dict:
    counts = {POSITIVE: 0, NEGATIVE: 0, NICK_DECLINED: 0, UNLABELED: 0}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    # Resolved = the labels a fit-scorer is graded on. nick-declined and unlabeled
    # are deliberately EXCLUDED (preference / unknown, not a fit outcome).
    resolved = counts[POSITIVE] + counts[NEGATIVE]
    counts["resolved"] = resolved
    counts["total"] = len(rows)
    counts["metric_ready"] = resolved >= MIN_RESOLVED_FOR_METRIC
    return counts


def build_inventory(repo_root: Path) -> dict:
    companies = resolve_company_outcomes(repo_root)
    contacts = resolve_contact_outcomes(repo_root)
    verified_n = sum(1 for r in companies if r.get("source") == "verified")
    return {
        "which-company (job-pipeline.md)": _tally(companies),
        "who-to-contact (outreach-log.md)": _tally(contacts),
        "_meta": {"companies_verified": verified_n, "companies_total": len(companies)},
        "_positives": {
            "companies": [r["entity"] for r in companies if r["label"] == POSITIVE],
            "contacts": [r["entity"] for r in contacts if r["label"] == POSITIVE],
        },
    }


def _readiness_line(name: str, t: dict) -> str:
    if t["metric_ready"]:
        return (f"- **{name}:** {t['resolved']} resolved (>= {MIN_RESOLVED_FOR_METRIC}) "
                f"-> precision@k reportable with caution once the prediction log exists (step 2).")
    return (f"- **{name}:** {t['resolved']} resolved (< {MIN_RESOLVED_FOR_METRIC}) "
            f"-> NOT enough to report a metric; any figure is optimistic. Keep working the search; re-check later.")


def render_report(inv: dict) -> str:
    lines = ["# Scorer evaluation — label inventory (read-only baseline)", ""]
    lines.append("| Domain | Positive | Negative | Nick-declined | Unlabeled | Resolved | Total | Metric-ready? |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, t in inv.items():
        if name.startswith("_"):
            continue
        lines.append(f"| {name} | {t['positive']} | {t['negative']} | {t['nick-declined']} | {t['unlabeled']} | "
                     f"{t['resolved']} | {t['total']} | {'yes' if t['metric_ready'] else 'no'} |")
    lines.append("")
    lines.append("## Readiness")
    for name, t in inv.items():
        if name.startswith("_"):
            continue
        lines.append(_readiness_line(name, t))
    lines.append("")
    lines.append("_Positive = mutual interest / reply established. Negative = acted-on, no positive outcome. "
                 "Unlabeled = backlog / not-yet-acted (never counted as negative, per PU discipline). "
                 "precision@k reporting is gated on the prediction log (migration step 2); this baseline "
                 "reports label availability only._")
    meta = inv.get("_meta", {})
    if meta.get("companies_verified"):
        lines.append("")
        lines.append(f"_Company labels: {meta['companies_verified']}/{meta['companies_total']} come from the "
                     f"verified ledger (data/calibration/company-outcomes.json — first-person evidence + "
                     f"fresh-context verify); the rest fall back to deterministic stage classification._")
    pos = inv.get("_positives", {})
    if pos.get("companies"):
        lines.append("")
        lines.append(f"**Companies with a positive (engaged) outcome:** {', '.join(pos['companies'])}")
    return "\n".join(lines)


def render_ledger(repo_root: Path) -> str:
    """Regenerate the COMPLETE human-readable calibration ledger from the verified
    JSON (all records). Deterministic + re-runnable — this is the authoritative
    ledger; the workflow's synthesis .md can truncate on large corpora.

    Two-axis (2026-07-15): Axis 1 fit_verdict is the scorer target (resolved =
    fit + not-fit); Axis 2 market_response is reported alongside but never feeds
    the resolved count (a fit company the market ghosted is still a fit positive)."""
    verified = load_verified_ledger(repo_root)
    # Exclude control/meta records (e.g. __CALIBRATION_META__ carries the knockout
    # hierarchy, not a company outcome).
    records = [r for r in verified.values() if not str(r.get("entity", "")).startswith("__")]
    if not records:
        return "# Calibration Ledger\n\n_No verified ledger found (data/calibration/company-outcomes.json)._"

    def fit(r):
        return r.get("fit_verdict") or "unknown"

    def market(r):
        return r.get("market_response") or "none-yet"

    from collections import Counter
    fdist = Counter(fit(r) for r in records)
    mdist = Counter(market(r) for r in records)
    human_n = sum(1 for r in records if r.get("human_labeled"))
    resolved = fdist.get("fit", 0) + fdist.get("not-fit", 0)
    ready = resolved >= MIN_RESOLVED_FOR_METRIC

    L = ["# Calibration Ledger (complete)", ""]
    L.append(f"_Verified per-company outcomes for the scoring loop. {len(records)} companies "
             f"({human_n} hand-labeled by Nick, authoritative). Two axes: **fit_verdict** (Axis 1, "
             f"the scorer target — did Nick judge the company a fit) and **market_response** (Axis 2 — "
             f"did the market engage). The axes decouple: a fit company can be ghosted; a not-fit "
             f"company can have engaged._")
    L.append("")
    L.append("## Axis 1 — Fit verdict (scorer target)")
    L.append("| fit_verdict | Count | Scorer role |")
    L.append("|---|---|---|")
    L.append(f"| fit | {fdist.get('fit', 0)} | positive (pursued / judged a fit) |")
    L.append(f"| not-fit | {fdist.get('not-fit', 0)} | negative (reasoned fit-miss — highest signal) |")
    L.append(f"| neutral | {fdist.get('neutral', 0)} | excluded (logistics/geo/comp/culture knockout) |")
    L.append(f"| unknown | {fdist.get('unknown', 0)} | unlabeled (no in-lane role / never assessed) |")
    L.append(f"| **total** | **{len(records)}** | |")
    L.append("")
    L.append(f"**Resolved (fit + not-fit) = {resolved}.** "
             + (f"At or above the {MIN_RESOLVED_FOR_METRIC}-outcome floor — a which-company scorer can be "
                f"calibrated on this set (precision@k still gated on the prediction log)."
                if ready else
                f"BELOW the {MIN_RESOLVED_FOR_METRIC}-outcome floor — do NOT report a company-scorer metric yet; "
                f"the honest signal is n={resolved}. (neutral and unknown are excluded — preference-knockout / unknown, not fit outcomes.)"))
    L.append("")
    L.append("## Axis 2 — Market response (reported, never in the resolved count)")
    L.append("| market_response | Count |")
    L.append("|---|---|")
    for k in ("engaged", "rejected", "ghosted", "pending", "none-yet"):
        L.append(f"| {k} | {mdist.get(k, 0)} |")
    L.append(f"| **total** | **{len(records)}** |")
    L.append("")

    disagreements = [r for r in records if r.get("disagreement")]
    L.append(f"## Extractor vs verifier disagreements ({len(disagreements)})")
    if disagreements:
        L.append("| Company | Extractor | Verifier (final) | Reason |")
        L.append("|---|---|---|---|")
        for r in disagreements:
            L.append(f"| {r['entity']} | {r.get('label')} | {r.get('verifier_label')} | "
                     f"{(r.get('verifier_reason') or '')[:160]} |")
    else:
        L.append("_None._")
    L.append("")

    review = [r for r in records if r.get("needs_review")]
    L.append(f"## Needs human review — low-confidence / no quotable source ({len(review)})")
    for r in review:
        L.append(f"- **{r['entity']}** (fit={fit(r)}, market={market(r)}): {(r.get('justification') or '')[:180]}")
    if not review:
        L.append("_None._")
    L.append("")

    declined_after = [r for r in records if r.get("declined_after_engagement")]
    if declined_after:
        L.append(f"## Engaged, then Nick declined ({len(declined_after)})")
        for r in declined_after:
            L.append(f"- **{r['entity']}** — fit={fit(r)}, interest: {r.get('interest_level')}")
        L.append("")

    # Negative space: fit-unknown but researched (dossier or stated interest) = likely scorer misses.
    negspace = [r for r in records if fit(r) == "unknown"
                and (r.get("interest_level") in ("high", "medium") or (r.get("fit_features") or {}).get("thesis_fit_note"))]
    L.append(f"## Never-assessed but researched (negative-space, potential scorer misses) ({len(negspace)})")
    if negspace:
        for r in negspace[:40]:
            L.append(f"- **{r['entity']}** — interest: {r.get('interest_level')}")
    else:
        L.append("_None flagged._")
    L.append("")

    fit_cos = [r["entity"] for r in records if fit(r) == "fit"]
    L.append(f"## Fit companies (Axis 1 positives) ({len(fit_cos)})")
    L.append(", ".join(fit_cos) if fit_cos else "_None._")
    L.append("")
    market_engaged = [r["entity"] for r in records if market(r) == "engaged"]
    L.append(f"## Market-engaged companies (Axis 2) ({len(market_engaged)})")
    L.append(", ".join(market_engaged) if market_engaged else "_None._")
    L.append("")

    L.append("## Continuous-learning loop (how this recalibrates the scorer)")
    L.append("1. **Fit is Nick's judgment, not the market's outcome.** The two-axis split is the core "
             "lesson: a company Nick judged in-lane but that ghosted him is still a fit-positive, and a "
             "company the market engaged that he judged off-lane (CS-shape, wrong domain) is a "
             "fit-negative. Train the which-company scorer on Axis 1 (fit_verdict), never on Axis 2.")
    L.append("2. **His own reasoned declines are the highest-signal negatives.** Market rejections are "
             "confounded by noise; a self-decline with a nameable domain/role-shape reason is a clean "
             "revealed-preference negative. Weight not-fit accordingly.")
    L.append("3. **Refresh cadence.** Re-run the extract-verify workflow as new outcomes land, then "
             "`scorer_eval ledger` regenerates this file. Hand-labeled records are authoritative and "
             "must never be overwritten; disagreement + needs_review rows are the correction signal.")
    L.append("4. **Metric gate.** Do not report a company-scorer precision@k until resolved >= "
             f"{MIN_RESOLVED_FOR_METRIC} (currently {resolved}). Below that, calibrate qualitatively "
             "against revealed-preferences.md, not a number.")
    L.append("5. **Exploration budget.** Reserve ~15-20% of scoring attention for low-ranked / "
             "never-assessed-but-researched entities. Calibrating only to past engagements reproduces "
             "Nick's inbound-only bias; the exploration slice keeps negative-space misses visible.")
    L.append("")
    L.append("_Regenerate anytime: `PYTHONIOENCODING=utf-8 python3 tools/scorer_eval.py ledger --repo-root .`_")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only scorer evaluation / label inventory.")
    ap.add_argument("command", nargs="?", default="report", choices=["report", "ledger"])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of markdown.")
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()

    if args.command == "ledger":
        print(render_ledger(repo_root))
        return 0

    inv = build_inventory(repo_root)
    if args.json:
        print(json.dumps(inv, indent=2, ensure_ascii=False))
    else:
        print(render_report(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
