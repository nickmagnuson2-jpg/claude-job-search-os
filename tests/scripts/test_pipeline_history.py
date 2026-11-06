"""tools/pipeline_history.py: one history check for both role scanners (scan-drops-1106).

Nick, 2026-11-06: a role at a company with history ALWAYS shows, labelled; only an exact
repeat of a posting he passed on is held back, onto a visible list. Spec:
output/infra-backlog/110626-pipeline-history-check-spec.md.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools import pipeline_history as ph  # noqa: E402

PIPE = """# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Northwind Labs | Deployment Strategist | Applied | 2026-10-20 | — | — | — | https://n.example/1 |
| Quiet Co | Ops Lead |  |  | — | — | — | — |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Beacon AI | Ops Lead | Rejected | 2026-10-03 | — | — | — | https://b.example/1 |
| Lantern Works | Strategy Lead | Withdrawn | 2026-09-12 | — | — | comp below floor. Also far. | https://l.example/1 |
| Lantern Works | BizOps | Withdrawn | 2026-08-01 | — | — | — | https://l.example/2 |
| Lantern Works | Chief of Staff | Closed - filled | 2026-07-01 | — | — | — | — |
"""


@pytest.fixture
def rows(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    return ph.load_history(tmp_path)


def role(title, company, url=""):
    return {"title": title, "company": company, "url": url}


def test_load_history_reads_every_row_with_an_outcome(rows):
    got = {(r["company"], r["role"]): r["outcome"] for r in rows}
    assert got == {("Northwind Labs", "Deployment Strategist"): "in_pipeline",
                   ("Quiet Co", "Ops Lead"): "unknown",
                   ("Beacon AI", "Ops Lead"): "declined_you",
                   ("Lantern Works", "Strategy Lead"): "you_passed",
                   ("Lantern Works", "BizOps"): "you_passed",
                   ("Lantern Works", "Chief of Staff"): "closed"}


def test_load_history_missing_file_is_empty(tmp_path):
    assert ph.load_history(tmp_path) == []


@pytest.mark.parametrize("stage,outcome", [
    ("Rejected", "declined_you"), ("Closed - they passed", "declined_you"),
    ("Withdrawn", "you_passed"), ("Skipped", "you_passed"), ("Considered - passed (self, 7/7)", "you_passed"),
    ("Closed - filled", "closed"), ("Applied", "in_pipeline"), ("To Apply", "in_pipeline"),
    ("", "unknown"), ("   ", "unknown"),
])
def test_classify(stage, outcome):
    assert ph.classify(stage) == outcome


@pytest.mark.parametrize("a,b,same,overlap", [
    ("Acme", "acme", True, False), ("Acme, Inc.", "Acme Inc", True, False),
    ("Acme", "Acme AI", False, True), ("Acme AI", "Acme", False, True),
    ("Northwindtech", "Northwind", False, False), ("", "Acme", False, False), ("—", "Acme", False, False),
])
def test_company_matcher(a, b, same, overlap):
    assert ph.same_company(a, b) is same
    assert ph.company_overlap(a, b) is overlap


def test_a_different_role_at_a_company_that_declined_you_shows_labelled(rows):
    h = ph.history(role("Strategy Lead", "Beacon AI"), rows)
    assert h == {"label": "they declined you 10/03 (Ops Lead)", "declined_repeat": False,
                 "already_in_pipeline": False, "possible_match": ""}


def test_the_same_posting_that_declined_you_still_shows(rows):
    h = ph.history(role("Ops Lead", "Beacon AI", "https://b.example/1"), rows)
    assert h["declined_repeat"] is False and h["label"].startswith("they declined you")


def test_you_passed_label_is_newest_first_and_counts_the_rest(rows):
    h = ph.history(role("Founding Ops", "Lantern Works"), rows)
    # No reason from notes: live notes do not lead with one (2026-11-06 real-data run).
    assert h["label"] == ("you passed 09/12 (Strategy Lead); "
                          "you passed 08/01 (BizOps); +1 more")


def test_an_exact_repeat_of_a_posting_you_passed_on_is_flagged(rows):
    h = ph.history(role("Strategy Lead", "Lantern Works", "https://l.example/1/"), rows)
    assert h["declined_repeat"] is True


def test_a_similar_title_at_a_new_url_is_not_a_repeat_but_is_labelled_likely(rows):
    h = ph.history(role("Strategy Lead", "Lantern Works", "https://l.example/99"), rows)
    assert h["declined_repeat"] is False
    assert h["label"].startswith("likely the role you passed on; you passed 09/12")


def test_the_same_role_active_in_the_pipeline_is_flagged(rows):
    h = ph.history(role("Deployment Strategist", "Northwind Labs"), rows)
    assert h["already_in_pipeline"] is True
    assert h["label"] == "in pipeline: Applied (Deployment Strategist)"


def test_a_different_role_at_an_active_company_is_not_already_in_pipeline(rows):
    h = ph.history(role("Chief of Staff", "Northwind Labs"), rows)
    assert h["already_in_pipeline"] is False and h["label"] == "in pipeline: Applied (Deployment Strategist)"


def test_a_blank_stage_is_unknown_and_never_hides(rows):
    h = ph.history(role("Ops Lead", "Quiet Co"), rows)
    assert h == {"label": "stage unknown, date unknown (Ops Lead)", "declined_repeat": False,
                 "already_in_pipeline": False, "possible_match": ""}


def test_a_possible_company_match_is_labelled_never_hidden(rows):
    h = ph.history(role("Ops Lead", "Beacon"), rows)
    assert h == {"label": "possible match: Beacon AI", "declined_repeat": False,
                 "already_in_pipeline": False, "possible_match": "Beacon AI"}


def test_no_history_is_an_empty_label(rows):
    assert ph.history(role("Ops Lead", "Fresh Co"), rows)["label"] == ""


# ---------- wiring: both scanners use it, and agree (parity) ----------

def _scan_jobs_root(tmp_path):
    (tmp_path / ".claude/skills/scan-jobs").mkdir(parents=True)
    (tmp_path / ".claude/skills/scan-jobs/cache.md").write_text(
        "# Evaluated Ads Cache\n\nLast updated: 2026-01-01\n\n| Title | Company | Fit | Status | Ad Date | URL | Notes |\n"
        "|-------|---------|-----|--------|---------|-----|-------|\n", encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    return tmp_path


ADS = [
    {"title": "Strategy Lead", "company": "Beacon AI", "location": "San Francisco",
     "url": "https://b.example/9", "description": "Run strategy."},
    {"title": "Strategy Lead", "company": "Lantern Works", "location": "San Francisco",
     "url": "https://l.example/1", "description": "Run strategy."},
    {"title": "Deployment Strategist", "company": "Northwind Labs", "location": "San Francisco",
     "url": "https://n.example/7", "description": "Run strategy."},
    {"title": "Chief of Staff", "company": "Northwind Labs", "location": "San Francisco",
     "url": "https://n.example/8", "description": "Run strategy."},
]


def test_scan_jobs_labels_holds_back_only_a_declined_repeat_and_proposes_the_rest(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = _scan_jobs_root(tmp_path)
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    out = sj.assess([dict(a) for a in ADS], root)
    new = {(r["company"], r["title"]): r for r in out["new"]}
    assert ("Lantern Works", "Strategy Lead") not in new
    [rep] = out["skipped_repeats"]
    assert rep["company"] == "Lantern Works" and rep["history"].startswith("you passed 09/12")
    assert new[("Beacon AI", "Strategy Lead")]["history"] == "they declined you 10/03 (Ops Lead)"
    assert new[("Northwind Labs", "Deployment Strategist")]["already_in_pipeline"] is True
    proposed = [(r["company"], r["title"]) for r in out["propose_for_pipeline"]]
    assert proposed == [("Beacon AI", "Strategy Lead"), ("Northwind Labs", "Chief of Staff")]


def test_the_nightly_scan_labels_and_holds_back_the_same_way(tmp_path, monkeypatch):
    from tools.career_scanner import scanner
    root = tmp_path
    (root / "data").mkdir()
    (root / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "x", "ats": "ashby", "slug": "x"}])
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda t, errors=None, cuts=None: [dict(a) for a in ADS])
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    s = scanner.scan_all_targets(root)
    payload = scanner.read_queue_payload(root)
    queued = {(r["company"], r["title"]): r for r in payload["new"]}
    assert ("Lantern Works", "Strategy Lead") not in queued
    assert [r["company"] for r in payload["skipped_repeats"]] == ["Lantern Works"]
    assert s["skipped_repeats"] == 1
    assert queued[("Beacon AI", "Strategy Lead")]["history"] == "they declined you 10/03 (Ops Lead)"
    # The active same-role row is the nightly scanner's existing duplicate drop.
    assert ("Northwind Labs", "Deployment Strategist") not in queued


def test_parity_the_same_ad_gets_the_same_label_and_hold_back_from_both(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    from tools.career_scanner import scanner
    a, b = tmp_path / "a", tmp_path / "b"
    _scan_jobs_root(a)
    (b / "data").mkdir(parents=True)
    (b / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    sj_out = sj.assess([dict(x) for x in ADS], a)
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "x", "ats": "ashby", "slug": "x"}])
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda t, errors=None, cuts=None: [dict(x) for x in ADS])
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    scanner.scan_all_targets(b)
    nightly = scanner.read_queue_payload(b)
    lab = lambda rows: {(r["company"], r["title"]): r["history"] for r in rows}
    shared = set(lab(sj_out["new"])) & set(lab(nightly["new"]))
    assert shared and all(lab(sj_out["new"])[k] == lab(nightly["new"])[k] for k in shared)
    assert lab(sj_out["skipped_repeats"]) == lab(nightly["skipped_repeats"])
