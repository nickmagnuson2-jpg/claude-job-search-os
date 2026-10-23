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


def test_an_excluded_location_is_skipped_whatever_the_score(root, monkeypatch):
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 10)
    out = sj.assess([{"title": "Deployment Strategist", "company": "Beacon AI",
                      "location": "New York, NY"}], root)
    assert out["new"][0]["status"] == "skip" and out["new"][0]["reason"] == "location excluded"


def test_only_untracked_shortlisted_companies_are_proposed(root, monkeypatch):
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 8)
    ads = [{"title": "Strategist", "company": "Northwind Labs", "location": "San Francisco"},
           {"title": "Strategist", "company": "Beacon AI", "location": "San Francisco"}]
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
