"""Recall test for the fetch-time title gate (title-gate-1118, funnel-intake step 2).

The old gate was an allowlist of exact phrases. A 2026-11-17 hand review of the 454 titles it
cut found 3 fits and 6 maybes among them, plus 6 in-lane titles outside SF. Done-test for the
rebuild: every one of those 15 rows passes the title gate, and the clearly out-of-lane rows are
still cut.

Two copies of the labelled set:
  * tests/fixtures/title_gate/title_cut_review_sample.json (tracked): company names replaced
    with placeholders, titles that name a real company or product dropped, plus a snapshot of
    the lane filters as of 2026-11-18. Runs everywhere.
  * data/calibration/title-cut-review-2026-11-17.json with data/scan-targets.yaml (gitignored):
    the full 454 rows against the live filters. Skips on a public clone; not decorative on the
    owner's machine, where the filters change.

The "clear out-of-lane" categories and the 90% floor were fixed before the new gate was
measured (2026-11-18). Measured on the full file: 304 of 308 cut. The known passes listed
below are the ambiguous ones, left to the scorer on purpose.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scanner import (  # noqa: E402
    normalize_title, out_of_lane_family, title_cut_reason, title_head)

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "title_gate" / "title_cut_review_sample.json"
REAL = REPO_ROOT / "data" / "calibration" / "title-cut-review-2026-11-17.json"
TARGETS = REPO_ROOT / "data" / "scan-targets.yaml"

IN_LANE = {"fit", "maybe", "location", "fit-but-previously-withdrawn"}
CLEAR = {"sales", "marketing", "recruiting/talent", "research/technical", "solutions/presales",
         "sales/account mgmt", "customer success/support", "legal/finance/admin", "people",
         "design", "product"}
# Clear-category rows the gate passes on purpose: the head names no out-of-lane function
# ("GTM Strategy", "Client Services Lead", "Head of Go-to-Market"), so the scorer decides.
KNOWN_CLEAR_PASSES = {
    "GTM Strategy: Products and Markets",
    "Client Services Lead, Finance",
    "Client Services Lead, Legal",
    "Founding Head of Go-to-Market",
    # Names an in-lane function (PROTECTED_FUNCTIONS "strategy"), Codex r1 F1 fix.
    "Compute Finance and Strategy",
}
# Not-fit rows outside the clear categories that may pass the gate (the scorer ranks them).
# Measured 2026-11-18 on the full file: 79 of 131 (108 before the second family pass).
# A ceiling, so noise growth is visible.
OTHER_PASS_CEILING = 79


def _sets():
    out = []
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out.append(pytest.param(fx["rows"], fx["lane_filters"], fx["lane_excludes"], id="fixture"))
    if REAL.is_file() and TARGETS.is_file():
        doc = yaml.safe_load(TARGETS.read_text(encoding="utf-8"))
        rows = json.loads(REAL.read_text(encoding="utf-8"))["rows"]
        out.append(pytest.param(rows, doc["x-lane-filters"], doc["x-lane-excludes"], id="real"))
    return out


@pytest.mark.parametrize("rows,includes,excludes", _sets())
def test_every_fit_and_maybe_passes_the_title_gate(rows, includes, excludes):
    in_lane = [r for r in rows if r["verdict"] in IN_LANE]
    assert len(in_lane) == 15, "the labelled set lost an in-lane row; this test would weaken"
    missed = [(r["title"], title_cut_reason(r["title"], includes, excludes))
              for r in in_lane if title_cut_reason(r["title"], includes, excludes)]
    assert not missed, f"in-lane titles cut at the gate: {missed}"


@pytest.mark.parametrize("rows,includes,excludes", _sets())
def test_clear_out_of_lane_titles_are_still_cut(rows, includes, excludes):
    clear = [r for r in rows if r["verdict"] == "not-fit" and r["why"] in CLEAR]
    assert clear
    passed = {r["title"].strip() for r in clear
              if title_cut_reason(r["title"], includes, excludes) is None}
    assert passed <= KNOWN_CLEAR_PASSES, f"clear out-of-lane titles now pass: {passed - KNOWN_CLEAR_PASSES}"
    cut = sum(title_cut_reason(r["title"], includes, excludes) is not None for r in clear)
    assert cut / len(clear) >= 0.90


@pytest.mark.parametrize("rows,includes,excludes", _sets())
def test_noise_from_other_not_fit_titles_stays_under_the_ceiling(rows, includes, excludes):
    other = [r for r in rows if r["verdict"] == "not-fit" and r["why"] not in CLEAR]
    passed = sum(title_cut_reason(r["title"], includes, excludes) is None for r in other)
    assert passed <= OTHER_PASS_CEILING


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_no_title_the_old_gate_passed_is_cut_now():
    """Monotonic (Codex r1 claim 2; Grok r1 F1 showed the first version could not fail, because
    `rows` holds only titles the old gate cut). `old_gate_passed` holds titles the pre-1118 gate
    passed in the 2026-11-18 nightly scan; every one must still pass."""
    fx = _fixture()
    assert len(fx["old_gate_passed"]) >= 15
    cut = [(t, title_cut_reason(t, fx["lane_filters"], fx["lane_excludes"]))
           for t in fx["old_gate_passed"]
           if title_cut_reason(t, fx["lane_filters"], fx["lane_excludes"])]
    assert cut == []


def test_unlabelled_ops_family_titles_pass():
    """14 SF ops-family titles another session found cut on 2026-11-18 (not owner-labelled,
    so kept apart from `rows`). The old gate passed 0 of them."""
    fx = _fixture()
    titles = fx["extra_recall_unlabelled"]
    assert len(titles) == 14
    inc, exc = fx["lane_filters"], fx["lane_excludes"]
    assert not any(any(f.lower() in t.lower() for f in inc) for t in titles)
    assert [t for t in titles if title_cut_reason(t, inc, exc)] == []


def test_the_old_allowlist_failed_this_set():
    """The recall test discriminates: the pre-1118 gate (raw substring allowlist) cut all 15."""
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    inc = fx["lane_filters"]
    old_pass = [r for r in fx["rows"] if r["verdict"] in IN_LANE
                and any(f.lower() in r["title"].lower() for f in inc)]
    assert old_pass == []


# --- normalization -----------------------------------------------------------------------

@pytest.mark.parametrize("raw,norm", [
    ("Strategy & Ops", "strategy and operations"),
    ("Strategy and Operations Lead", "strategy and operations lead"),
    ("Forward-Deployed Lead", "forward deployed lead"),
    ("Sr. BizOps Mgr", "senior business operations manager"),
    ("Biz Ops Lead", "business operations lead"),
    ("Growth + Ops", "growth and operations"),
    ("Strategy – Ops", "strategy operations"),
    ("  Business   Operations ", "business operations"),
    ("", ""),
])
def test_normalize_title(raw, norm):
    assert normalize_title(raw) == norm
    assert normalize_title(norm) == norm   # idempotent


def test_normalization_reaches_spelling_variants_of_an_include():
    inc = ["strategy and operations lead", "business operations"]
    for t in ("Strategy & Ops Lead", "Strategy and Ops Lead", "Biz Ops"):
        assert normalize_title(t) and any(normalize_title(f) in normalize_title(t) for f in inc), t


@pytest.mark.parametrize("raw,head", [
    ("Sales Director - Seoul", "Sales Director"),
    ("Strategy and Operations Lead, Sales", "Strategy and Operations Lead"),
    ("Recruiter (Contract)", "Recruiter "),
    ("GTM Strategy: Products", "GTM Strategy"),
    ("Sales Director – Seoul", "Sales Director"),
    ("Manager, Business Development", " Business Development"),
    ("Senior Director, Corporate Marketing", " Corporate Marketing"),
    ("General Manager, Marketplace", "General Manager"),
    ("Head of Operations Planning", "Head of Operations Planning"),
    ("Manager", "Manager"),
    (", Sales", ", Sales"),                 # nothing before the delimiter: whole title
    ("(Sales)", "(Sales)"),
    ("Manager,", "Manager"),                # level-only head with an empty next segment
])
def test_title_head(raw, head):
    assert title_head(raw) == head


@pytest.mark.parametrize("title,family", [
    ("Strategy and Operations Lead, Sales", None),      # family word only in the suffix
    ("Data Product Operations Lead", None),             # "product" not as product manager
    ("General Manager, Data Operations", None),
    ("Head of Sales", "sales"),
    ("Senior Technical Recruiter", "recruiting"),
    ("Product Marketing Manager", "marketing"),
    ("Head of People", "people"),
    ("Legal Counsel", "legal/finance/admin"),
    ("Customer Success Manager", "customer support"),
    ("Member of Technical Staff", "research/technical"),
    ("Solutions Consultant - West Coast", "solutions/presales"),
    ("Product Designer", "design"),
    ("Senior Product Manager", "product"),
    ("Manager, Business Development", "sales"),
    ("Head of Associate AE", "sales"),
    ("Workplace & Facilities Lead", "legal/finance/admin"),
    ("IT Administrator", "legal/finance/admin"),
    ("Senior IT Manager", "legal/finance/admin"),
    # Codex r1 F1: an in-lane function in the head outranks a family word beside it.
    ("Business Operations and Sales Lead", None),
    ("Business Operations / Customer Success Manager", None),
    ("Special Projects and Sales Lead", None),
    ("General Manager and Head of Sales", None),
    ("Strategy and Sales Lead", None),
    ("Chief of Staff, Marketing", None),
    ("Strategic Project Lead", None),
    # Second pass: engineering heads without "engineer", and labelled not-fit groups that
    # reached the scorer at score 4-5 in the 2026-11-18 dry run.
    ("Tech Lead Manager", "research/technical"),
    ("Tech Lead, Applied AI - Evals", "research/technical"),
    ("Senior Technical Program Manager", "research/technical"),
    ("Senior SRE", "research/technical"),
    ("RevOps Lead", "sales"),
    ("Senior Manager, Revenue Operations", "sales"),
    ("Senior Deal Desk Manager", "sales"),
    ("Paid Search & SEM", "marketing"),
    ("SEO & AEO", "marketing"),
    ("Head of Global Events", "marketing"),
    ("Head of Analyst Relations", "marketing"),
    ("PR & Communications Manager", "marketing"),
    ("Developer Community Manager", "marketing"),
    ("Developer Advocates Lead", "marketing"),
    ("Developer Relationship Manager", None),       # Grok r2 F2: word-bounded, no prefix match
    ("Senior Compliance Manager", "legal/finance/admin"),
    ("Global Security Manager", "legal/finance/admin"),
    ("Strategy & Operations Programs, Support", None),
    ("Revenue Strategy & Operations Lead", None),     # protected function beats the family
])
def test_out_of_lane_family(title, family):
    assert out_of_lane_family(title) == family


def test_an_include_phrase_outranks_a_family_word():
    assert title_cut_reason("Sales Strategist", ["strategist"]) is None
    assert title_cut_reason("Sales Lead", ["strategist"]) == "title in out-of-lane family 'sales'"


def test_excludes_are_normalized_too():
    assert title_cut_reason("Strategic-Finance Lead", ["strategist"], ["strategic finance"]) \
        == "title matches role_excludes 'strategic finance'"


def test_no_includes_means_no_family_cut():
    assert title_cut_reason("Head of Sales", []) is None
