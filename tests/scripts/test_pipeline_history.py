"""tools/pipeline_history.py: one history check for both role scanners (scan-drops-1106).

Nick, 2026-11-06: a role at a company with history ALWAYS shows, labelled; only an exact
repeat of a posting he passed on is held back, onto a visible list. Spec:
output/infra-backlog/110626-pipeline-history-check-spec.md.

R9 (Nick, 2026-11-18): "Move any company that's rejected me to a skipped list." A role at a
company (exact name match) with a declined_you row and no active row is held back onto the
same visible list, labelled. Design: output/funnel-intake/111826-r9-design.md (option B).
"""
import json
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
| Harbor Co | Strategy Lead | Applied | 2026-10-25 | — | — | — | https://h.example/2 |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Beacon AI | Ops Lead | Rejected | 2026-10-03 | — | — | — | https://b.example/1 |
| Lantern Works | Strategy Lead | Withdrawn | 2026-09-12 | — | — | comp below floor. Also far. | https://l.example/1 |
| Lantern Works | BizOps | Withdrawn | 2026-08-01 | — | — | — | https://l.example/2 |
| Lantern Works | Chief of Staff | Closed - filled | 2026-07-01 | — | — | — | — |
| Harbor Co | Ops Lead | Rejected | 2026-09-20 | — | — | — | https://h.example/1 |
| Cove Labs | Analyst | Withdrawn | 2026-10-10 | — | — | — | — |
| Cove Labs | BizOps | Withdrawn | 2026-10-05 | — | — | — | — |
| Cove Labs | Ops Lead | Rejected | 2026-08-02 | — | — | — | — |
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
                   ("Lantern Works", "Chief of Staff"): "closed",
                   ("Harbor Co", "Strategy Lead"): "in_pipeline",
                   ("Harbor Co", "Ops Lead"): "declined_you",
                   ("Cove Labs", "Analyst"): "you_passed",
                   ("Cove Labs", "BizOps"): "you_passed",
                   ("Cove Labs", "Ops Lead"): "declined_you"}


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


def test_a_different_role_at_a_company_that_declined_you_is_held_back_labelled(rows):
    """R9: any role at a company that declined Nick goes to the skipped list, labelled."""
    h = ph.history(role("Strategy Lead", "Beacon AI"), rows)
    assert h == {"label": "they declined you 10/03 (Ops Lead)", "declined_repeat": False,
                 "declined_company": True, "held_back": True,
                 "already_in_pipeline": False, "possible_match": ""}


def test_the_same_posting_that_declined_you_is_held_back_as_a_declined_company(rows):
    h = ph.history(role("Ops Lead", "Beacon AI", "https://b.example/1"), rows)
    assert h["declined_repeat"] is False and h["declined_company"] is True
    assert h["label"].startswith("they declined you")


def test_a_declining_company_with_an_active_row_is_not_held_back(rows):
    """R9 grey area 4: never skip a company Nick has an active process with."""
    h = ph.history(role("Chief of Staff", "Harbor Co"), rows)
    assert h["declined_company"] is False and h["held_back"] is False
    assert "they declined you 09/20 (Ops Lead)" in h["label"]


def test_a_fuzzy_only_match_to_a_declining_company_is_not_held_back(rows):
    """"Beacon" only possibly matches "Beacon AI": labelled, never skipped."""
    h = ph.history(role("Ops Lead", "Beacon"), rows)
    assert h["declined_company"] is False and h["held_back"] is False


def test_a_company_you_only_passed_on_is_not_held_back(rows):
    h = ph.history(role("Founding Ops", "Lantern Works"), rows)
    assert h["declined_company"] is False and h["held_back"] is False


def test_a_declined_company_label_names_the_decline_even_when_it_is_not_newest(rows):
    """The two newest rows at Cove Labs are withdrawals; the label must still say why the
    role is on the skipped list."""
    h = ph.history(role("Strategy Lead", "Cove Labs"), rows)
    assert h["declined_company"] is True
    assert h["label"] == "they declined you 08/02 (Ops Lead); you passed 10/10 (Analyst); +1 more"


def test_a_declined_company_label_leads_with_the_decline_when_the_title_matches_a_pass(rows):
    """Grok r1 F1: Analyst matches the Analyst withdrawal, so the 11-06 "likely the role you
    passed on" marker fires; the label must still lead with the decline."""
    h = ph.history(role("Analyst", "Cove Labs"), rows)
    assert h["declined_company"] is True
    assert h["label"] == ("they declined you 08/02 (Ops Lead); likely the role you passed on; "
                          "you passed 10/10 (Analyst); +1 more")


def test_a_declined_repeat_is_held_back(rows):
    h = ph.history(role("Strategy Lead", "Lantern Works", "https://l.example/1"), rows)
    assert h["declined_repeat"] is True and h["held_back"] is True


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
                 "declined_company": False, "held_back": False,
                 "already_in_pipeline": False, "possible_match": ""}


def test_a_possible_company_match_is_labelled_never_hidden(rows):
    h = ph.history(role("Ops Lead", "Beacon"), rows)
    assert h == {"label": "possible match: Beacon AI", "declined_repeat": False,
                 "declined_company": False, "held_back": False,
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
    {"title": "Chief of Staff", "company": "Harbor Co", "location": "San Francisco",
     "url": "https://h.example/9", "description": "Run strategy."},
    {"title": "Ops Lead", "company": "Beacon", "location": "San Francisco",
     "url": "https://bc.example/1", "description": "Run strategy."},
]


def test_scan_jobs_holds_back_a_declined_repeat_and_a_declining_company_and_proposes_the_rest(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = _scan_jobs_root(tmp_path)
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    out = sj.assess([dict(a) for a in ADS], root)
    new = {(r["company"], r["title"]): r for r in out["new"]}
    assert ("Lantern Works", "Strategy Lead") not in new
    assert ("Beacon AI", "Strategy Lead") not in new
    held = {r["company"]: r for r in out["skipped_repeats"]}
    assert set(held) == {"Lantern Works", "Beacon AI"}
    assert held["Lantern Works"]["history"].startswith("you passed 09/12")
    # R9: the declining company's role is on the skipped list WITH its label and URL.
    assert held["Beacon AI"] == {"title": "Strategy Lead", "company": "Beacon AI",
                                 "url": "https://b.example/9",
                                 "history": "they declined you 10/03 (Ops Lead)"}
    assert new[("Northwind Labs", "Deployment Strategist")]["already_in_pipeline"] is True
    proposed = [(r["company"], r["title"]) for r in out["propose_for_pipeline"]]
    assert proposed == [("Northwind Labs", "Chief of Staff"), ("Harbor Co", "Chief of Staff"),
                        ("Beacon", "Ops Lead")]


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
    assert ("Beacon AI", "Strategy Lead") not in queued
    assert [r["company"] for r in payload["skipped_repeats"]] == ["Beacon AI", "Lantern Works"]
    assert s["skipped_repeats"] == 2
    assert payload["skipped_repeats"][0]["history"] == "they declined you 10/03 (Ops Lead)"
    # A declining company with an active row, and a fuzzy-only match, still show.
    assert "they declined you 09/20" in queued[("Harbor Co", "Chief of Staff")]["history"]
    assert queued[("Beacon", "Ops Lead")]["history"] == "possible match: Beacon AI"
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


# ---------- mutation round 1 killers ----------

def test_the_same_url_is_the_same_role_whatever_the_title(rows):
    # The posting was retitled; its URL is its identity.
    h = ph.history(role("Senior Deployment Strategist II", "Northwind Labs", "https://n.example/1"), rows)
    assert h["already_in_pipeline"] is True


def test_a_closed_row_is_labelled_closed_with_its_stage():
    row = {"outcome": "closed", "date": "2026-07-01", "role": "Chief of Staff", "stage": "Closed - filled"}
    assert ph._phrase(row) == "closed 07/01: Closed - filled (Chief of Staff)"


def test_a_pending_role_from_an_earlier_scan_gets_this_scans_label(tmp_path, monkeypatch):
    from tools.career_scanner import scanner
    from tools.career_scanner.dedup import role_key
    (tmp_path / "data").mkdir()
    pend = {"title": "Strategy Lead", "company": "Beacon AI", "url": "https://b.example/9", "history": "old"}
    scanner.write_queue_payload(tmp_path, scanner._queue_payload([pend], [pend], [], []))
    fresh = {**pend, "history": "they declined you 10/03 (Ops Lead)"}
    scanner._write_role_queue_locked(tmp_path, [], [fresh], [])
    [p] = scanner.read_queue_payload(tmp_path)["new"]
    assert role_key(p) == role_key(pend) and p["history"] == "they declined you 10/03 (Ops Lead)"


# ---------- review r1 P0s ----------

def test_scan_jobs_labels_a_cached_ad_too(tmp_path, monkeypatch):
    """Codex F1 / Grok F2: a current cache hit returned before history ran."""
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = _scan_jobs_root(tmp_path)
    ad = dict(ADS[3])   # Northwind Labs Chief of Staff: labelled, not held back
    cache = root / ".claude/skills/scan-jobs/cache.md"
    cache.write_text(cache.read_text() + f"| {ad['title']} | {ad['company']} | 5/10 | listed | — | "
                     f"[Listing]({ad['url']}) | by hand |\n", encoding="utf-8")
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    out = sj.assess([ad], root)
    assert out["cached"] == [{"title": ad["title"], "company": ad["company"],
                              "history": "in pipeline: Applied (Deployment Strategist)"}]


def test_parity_every_ad_gets_the_same_verdict_from_both(tmp_path, monkeypatch):
    """Grok F3: compare every ad, not the intersection. The one designed difference: the same
    role already active in the pipeline is listed and flagged by /scan-jobs, and counted as a
    duplicate (absent from the queue) by the nightly scanner."""
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
    summary = scanner.scan_all_targets(b)
    q = scanner.read_queue_payload(b)
    key = lambda r: (r["company"], r["title"])
    sj_new = {key(r): r for r in sj_out["new"]}
    nightly = {key(r): r for r in q["new"]}
    for ad in ADS:
        k = key(ad)
        if k in {key(r) for r in sj_out["skipped_repeats"]}:
            assert k in {key(r) for r in q["skipped_repeats"]} and k not in nightly
        elif sj_new[k].get("already_in_pipeline"):
            assert k not in nightly
        else:
            assert nightly[k]["history"] == sj_new[k]["history"]
    assert summary["skipped_dupes"] == sum(bool(r.get("already_in_pipeline")) for r in sj_out["new"])
    assert ({(key(r), r["history"]) for r in sj_out["skipped_repeats"]}
            == {(key(r), r["history"]) for r in q["skipped_repeats"]})


def test_the_nightly_summary_lists_the_held_back_repeats(tmp_path, monkeypatch):
    """Grok F1: the /scan-companies CLI exposed only a count."""
    from tools.career_scanner import scanner
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "x", "ats": "ashby", "slug": "x"}])
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda t, errors=None, cuts=None: [dict(x) for x in ADS])
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    s = scanner.scan_all_targets(tmp_path, dry_run=True)
    assert [r["company"] for r in s["skipped_repeat_roles"]] == ["Beacon AI", "Lantern Works"]
    assert s["skipped_repeats"] == 2
    assert all(r["company"] != "Beacon AI" for r in s["roles"])


def test_the_cli_top_roles_carry_the_history_label(tmp_path, monkeypatch, capsys):
    from tools.career_scanner import cli, scanner
    monkeypatch.setattr(scanner, "scan_all_targets", lambda root, dry_run=False: {
        "roles": [{"title": "T", "company": "C", "score": 5, "url": "u", "history": "they declined you 10/03 (Ops Lead)"}],
        "skipped_repeats": 1, "skipped_repeat_roles": [{"title": "R", "company": "L", "url": "v", "history": "you passed 09/12 (R)"}]})
    monkeypatch.setattr(sys, "argv", ["cli.py", "--repo-root", str(tmp_path)])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["top_roles"][0]["history"] == "they declined you 10/03 (Ops Lead)"
    assert out["skipped_repeat_roles"][0]["company"] == "L"


def test_role_queue_read_forwards_the_held_back_repeats(tmp_path):
    """Grok F6."""
    import tools.role_queue_read as rq
    from tools.career_scanner import scanner
    rep = [{"title": "R", "company": "L", "url": "v", "history": "you passed 09/12 (R)"}]
    scanner.write_queue_payload(tmp_path, scanner._queue_payload([], [], [], [], None, rep))
    out = rq.read_queue(tmp_path)
    assert out["skipped_repeats"] == rep


def test_a_cached_exact_declined_repeat_goes_to_skipped_repeats(tmp_path, monkeypatch):
    """Codex r2 F1: a cached exact repeat of a posting Nick passed on sat in `cached`, which the
    skill shows only as a count, instead of the visible held-back list."""
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = _scan_jobs_root(tmp_path)
    ad = dict(ADS[1])   # Lantern Works Strategy Lead at the URL he withdrew from
    cache = root / ".claude/skills/scan-jobs/cache.md"
    cache.write_text(cache.read_text() + f"| {ad['title']} | {ad['company']} | 5/10 | listed | — | "
                     f"[Listing]({ad['url']}) | by hand |\n", encoding="utf-8")
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    out = sj.assess([ad], root)
    assert out["cached"] == []
    assert [r["company"] for r in out["skipped_repeats"]] == ["Lantern Works"]


def test_the_nightly_scan_labels_roles_the_fetch_gates_cut(tmp_path, monkeypatch):
    """title-gate-1118: the history check runs on cut roles too. Before, it ran only on roles
    that passed the title gate, so a cut role was never matched to a past decision."""
    from tools.career_scanner import scanner
    root = tmp_path
    (root / "data").mkdir()
    (root / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "x", "ats": "ashby", "slug": "x"}])

    def fake_fetch(t, errors=None, cuts=None):
        cuts.append({"company": "Beacon AI", "title": "Strategy Lead", "location": "San Francisco",
                     "url": "https://b.example/9", "rule": "title_filter", "reason": "r"})
        return []
    monkeypatch.setattr(scanner, "fetch_company_roles", fake_fetch)
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    scanner.scan_all_targets(root)
    [cut] = scanner.read_queue_payload(root)["cut_at_fetch"]
    assert cut["history"] == "they declined you 10/03 (Ops Lead)"


# ---------- R9: a company that declined Nick goes to the skipped list ----------

def test_a_cached_ad_at_a_declining_company_goes_to_skipped_repeats(tmp_path, monkeypatch):
    """R9: the cached path routes the same way as the fresh one."""
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = _scan_jobs_root(tmp_path)
    ad = dict(ADS[0])   # Beacon AI Strategy Lead: Beacon AI declined Nick
    cache = root / ".claude/skills/scan-jobs/cache.md"
    cache.write_text(cache.read_text() + f"| {ad['title']} | {ad['company']} | 5/10 | listed | — | "
                     f"[Listing]({ad['url']}) | by hand |\n", encoding="utf-8")
    monkeypatch.setattr(sj, "score_role", lambda r, ctx: 9)
    out = sj.assess([ad], root)
    assert out["cached"] == []
    assert out["skipped_repeats"] == [{"title": ad["title"], "company": ad["company"],
                                       "history": "they declined you 10/03 (Ops Lead)",
                                       "url": ad["url"]}]


def test_parity_both_scanners_hold_back_exactly_the_same_ads(tmp_path, monkeypatch):
    """R9 parity: the same ads, the same held-back set (company, title, label) from both, and
    every non-held ad is present in the other scanner's shown list."""
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
    q = scanner.read_queue_payload(b)
    trip = lambda rows: {(r["company"], r["title"], r["history"]) for r in rows}
    assert trip(sj_out["skipped_repeats"]) == trip(q["skipped_repeats"])
    assert {c for c, _, _ in trip(q["skipped_repeats"])} == {"Beacon AI", "Lantern Works"}
    for ad in ADS:
        held = ad["company"] in {"Beacon AI", "Lantern Works"} and ad["url"] in (
            "https://b.example/9", "https://l.example/1")
        assert held is ((ad["company"], ad["title"]) in {(c, t) for c, t, _ in trip(q["skipped_repeats"])})
        assert held is ((ad["company"], ad["title"]) in {(c, t) for c, t, _ in trip(sj_out["skipped_repeats"])})


def test_a_pending_role_now_held_back_moves_from_new_to_skipped(tmp_path, monkeypatch):
    """Codex r2 F1: a role queued before the company declined Nick (or before R9) stayed in
    the pending `new` list while also listed on skipped_repeats. It must move, and an
    unrelated pending role must stay."""
    from tools.career_scanner import scanner
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(PIPE, encoding="utf-8")
    held = {"title": "Strategy Lead", "company": "Beacon AI", "url": "https://b.example/9", "history": ""}
    other = {"title": "Ops Lead", "company": "Fresh Co", "url": "https://f.example/1", "history": ""}
    scanner.write_queue_payload(tmp_path, scanner._queue_payload([held, other], [held, other], [], []))
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "x", "ats": "ashby", "slug": "x"}])
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda t, errors=None, cuts=None: [dict(ADS[0])])
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    scanner.scan_all_targets(tmp_path)
    q = scanner.read_queue_payload(tmp_path)
    assert [r["url"] for r in q["new"]] == ["https://f.example/1"]
    assert [(r["url"], r["history"]) for r in q["skipped_repeats"]] == [
        ("https://b.example/9", "they declined you 10/03 (Ops Lead)")]
