"""tools/scan_jobs.py: the deterministic half of /scan-jobs (dedupe, gate, score, status,
cache write). The same ads must always get the same verdict."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "scan_jobs.py"
sys.path.insert(0, str(REPO / "tools"))
import scan_jobs as sj  # noqa: E402

CACHE = """# Evaluated Ads Cache

Last updated: 2026-01-01

## Evaluated Listings

| Title | Company | Fit | Status | Ad Date | URL | Notes |
|-------|---------|-----|--------|---------|-----|-------|
| Senior Engineer / Backend | Acme Corp | 40% | skip | 2026-01-01 | — | old |
"""
PIPE = """# Job Pipeline

## Active

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Northwind Labs | PM | Researching | 2026-01-01 | — | — | — | — |
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".claude/skills/scan-jobs").mkdir(parents=True)
    (tmp_path / ".claude/skills/scan-jobs/cache.md").write_text(CACHE, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/job-pipeline.md").write_text(PIPE, encoding="utf-8")
    return tmp_path


def run(root, cmd, payload):
    p = subprocess.run([sys.executable, str(SCRIPT), cmd, "--repo-root", str(root)],
                       input=json.dumps(payload), capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


def test_dedupe_ignores_case_spacing_and_punctuation(root):
    _, out = run(root, "assess", [{"title": "senior engineer/backend", "company": "ACME corp"}])
    assert [c["company"] for c in out["cached"]] == ["ACME corp"] and out["new"] == []


def test_a_listing_repeated_in_one_scan_is_assessed_once(root):
    ad = {"title": "Deployment Strategist", "company": "Beacon AI", "location": "San Francisco"}
    _, out = run(root, "assess", [ad, dict(ad)])
    assert len(out["new"]) == 1


@pytest.mark.parametrize("score,status", [(10, "shortlisted"), (7, "shortlisted"),
                                          (6, "maybe"), (5, "maybe"), (4, "skip"), (1, "skip")])
def test_status_cutoffs(score, status):
    assert sj.status_for(score, sj.STATUS_CUTOFFS) == status


@pytest.mark.parametrize("location,status", [("New York, NY", "skip"), ("San Mateo, CA", "skip"),
                                             ("Palo Alto", "skip"), ("San Francisco, CA", "shortlisted"),
                                             ("", "shortlisted")])
def test_the_nightly_scanners_location_rule_applies(root, monkeypatch, location, status):
    """Outside the Bay or on the Peninsula fails whatever the score; unknown does not."""
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 10)
    out = sj.assess([{"title": "Deployment Strategist", "company": "Beacon AI",
                      "location": location, "description": "Lead deployments."}], root)
    assert out["new"][0]["status"] == status


def test_only_untracked_shortlisted_companies_are_proposed(root, monkeypatch):
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 8)
    ads = [{"title": "Strategist", "company": c, "location": "San Francisco",
            "description": "Run strategy."} for c in ("Northwind Labs", "Beacon AI")]
    out = sj.assess(ads, root)
    assert [r["company"] for r in out["propose_for_pipeline"]] == ["Beacon AI"]


def test_record_appends_rows_and_stamps_the_date(root):
    rows = [{"title": "Ops | Lead", "company": "Beacon AI", "score": 7, "status": "shortlisted",
             "url": "https://example.com/1", "ad_date": "2026-10-20", "reason": "score 7/10"}]
    code, out = run(root, "record", rows)
    text = (root / ".claude/skills/scan-jobs/cache.md").read_text(encoding="utf-8")
    assert code == 0 and out["appended"] == 1
    assert "| Ops / Lead | Beacon AI | 7/10 | shortlisted | 2026-10-20 | [Listing](https://example.com/1) |" in text
    assert "Last updated: 2026-01-01" not in text
    code, out = run(root, "record", rows)
    assert code == 1 and out["code"] == "duplicate", "a second record of the same ad is refused"


@pytest.mark.parametrize("payload,code", [({"title": "x"}, "not_a_list"),
                                          ([{"title": "x", "company": ""}], "missing_fields")])
def test_bad_input_is_refused(root, payload, code):
    rc, out = run(root, "assess", payload)
    assert rc == 1 and out["code"] == code


def test_dedupe_key_handles_unicode_and_keeps_title_and_company_apart():
    assert sj.key("Caf\u00e9 Lead", "X") == sj.key("Cafe\u0301 Lead", "X")   # NFC vs NFD
    assert sj.key("Caf\u00e9 Lead", "X") == sj.key("Cafe Lead", "X")        # Latin accent folds
    assert sj.key("\u65e5\u672c", "X") != sj.key("\u97d3\u56fd", "X")      # letters kept
    assert sj.key("Ops Lead", "Beacon") != sj.key("Ops", "Lead Beacon")


def test_department_and_team_reach_the_scorer(root, monkeypatch):
    seen = {}
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: seen.update(role) or 5)
    sj.assess([{"title": "Ops", "company": "Beacon AI", "department": "Health", "team": "Care"}], root)
    assert seen["department"] == "Health" and seen["team"] == "Care"


def test_a_pipe_in_a_url_cannot_split_the_cache_row(root):
    rows = [{"title": "Ops", "company": "Beacon AI", "score": 5, "status": "maybe",
             "url": "https://example.com/a|b", "ad_date": "", "reason": "score 5/10"}]
    run(root, "record", rows)
    row = [l for l in (root / ".claude/skills/scan-jobs/cache.md").read_text().splitlines() if "Beacon" in l][0]
    assert row.count("|") == 8 and "(https://example.com/a%7Cb)" in row, "encoded, not rewritten"
    _, out = run(root, "assess", [{"title": "Ops", "company": "Beacon AI", "url": "https://example.com/a|b"}])
    assert out["new"] == [], "the raw URL matches its encoded cache entry"


def test_one_title_at_one_company_in_two_offices_is_two_roles(root):
    """URL is the identity when present: a skipped NY posting must not hide the SF one."""
    run(root, "record", [{"title": "Strategist", "company": "Beacon AI", "score": 5, "status": "skip",
                          "url": "https://example.com/ny", "ad_date": "", "reason": "location excluded"}])
    _, out = run(root, "assess", [{"title": "Strategist", "company": "Beacon AI",
                                   "url": "https://example.com/sf", "location": "San Francisco"}])
    assert len(out["new"]) == 1


def test_a_url_less_cache_row_still_dedupes_by_title_and_company(root):
    _, out = run(root, "assess", [{"title": "Senior Engineer / Backend", "company": "Acme Corp",
                                   "url": "https://example.com/new"}])
    assert out["new"] == []


def test_a_url_with_parentheses_round_trips_and_dedupes(root):
    url = "https://example.com/jobs/ops-(sf)"
    run(root, "record", [{"title": "Ops", "company": "Beacon AI", "score": 5, "status": "maybe",
                          "url": url, "ad_date": "", "reason": "score 5/10"}])
    _, out = run(root, "assess", [{"title": "Ops", "company": "Beacon AI", "url": url}])
    assert out["new"] == [], "the stored link must read back whole"


@pytest.mark.parametrize("blank", ["", "   \n\t"])
def test_a_title_only_ad_is_never_shortlisted_or_proposed(root, monkeypatch, blank):
    """A title is not a posting: titles mislead about what a role is (platform vs field team),
    so an ad with no description text is capped at 'maybe', tagged title_only, and not
    proposed for the pipeline. The same ad with its description read stays shortlisted."""
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 10)
    ad = {"title": "Deployment Strategist", "company": "Beacon AI", "location": "San Francisco"}
    out = sj.assess([{**ad, "url": "https://example.com/read", "description": "Lead deployments."},
                     {**ad, "url": "https://example.com/blank", "description": blank}], root)
    read, title_only = out["new"]
    assert read["status"] == "shortlisted" and "title_only" not in read["reason"]
    assert title_only["status"] == "maybe" and "title_only" in title_only["reason"]
    assert [r["url"] for r in out["propose_for_pipeline"]] == ["https://example.com/read"]


def test_a_title_only_ad_below_the_maybe_cutoff_stays_skip(root, monkeypatch):
    """The cap only lowers a status; it never lifts a skip to maybe. The tag is still recorded."""
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 1)
    out = sj.assess([{"title": "Ops", "company": "Beacon AI", "location": "San Francisco"}], root)
    assert out["new"][0]["status"] == "skip" and "title_only" in out["new"][0]["reason"]
