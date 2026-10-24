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


@pytest.mark.parametrize("second", [
    {"url": "https://example.com/1"},                                  # same posting URL
    {"url": ""},                                                       # both without a URL
])
def test_record_refuses_the_same_role_twice_in_one_call(root, second):
    """565.F3: the cache index was built once, so two identical rows in one call both
    landed. Nothing is written when any row is a duplicate."""
    base = {"title": "Ops Lead", "company": "Beacon AI", "score": 7, "status": "shortlisted",
            "ad_date": "2026-10-20", "reason": "score 7/10"}
    first = {**base, "url": second["url"]}
    before = (root / ".claude/skills/scan-jobs/cache.md").read_text(encoding="utf-8")
    code, out = run(root, "record", [first, {**base, **second}])
    assert code == 1 and out["code"] == "duplicate"
    assert (root / ".claude/skills/scan-jobs/cache.md").read_text(encoding="utf-8") == before


def test_record_keeps_one_title_at_two_offices_apart(root):
    rows = [{"title": "Ops Lead", "company": "Beacon AI", "score": 7, "status": "shortlisted",
             "url": f"https://example.com/{n}", "ad_date": "2026-10-20", "reason": "r"}
            for n in (1, 2)]
    code, out = run(root, "record", rows)
    assert code == 0 and out["appended"] == 2


def test_a_listing_with_a_url_repeated_in_one_scan_is_assessed_once(root):
    ad = {"title": "Deployment Strategist", "company": "Beacon AI", "location": "San Francisco",
          "url": "https://example.com/role/9"}
    _, out = run(root, "assess", [ad, dict(ad)])
    assert len(out["new"]) == 1 and out["cached"] == [{"title": ad["title"], "company": ad["company"]}]


def test_record_treats_a_url_less_row_as_covering_the_same_role_with_a_url(root):
    """The cache rule (cached_index): a URL-less row hides the same title+company with a URL.
    Within one call the same rule applies."""
    base = {"title": "Ops Lead", "company": "Beacon AI", "score": 7, "status": "shortlisted",
            "ad_date": "2026-10-20", "reason": "r"}
    code, out = run(root, "record", [{**base, "url": ""}, {**base, "url": "https://example.com/5"}])
    assert code == 1 and out["code"] == "duplicate"


def test_table_header_and_separator_rows_are_not_cache_entries(root):
    """An ad whose fields reduce to the same key as the header or separator row must not read
    as already evaluated."""
    _, out = run(root, "assess", [{"title": "Title", "company": "Company"},
                                  {"title": "--", "company": "--"}])
    assert out["cached"] == [] and len(out["new"]) == 2


def test_the_pipeline_header_is_not_read_as_a_tracked_company(root, monkeypatch):
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 8)
    ads = [{"title": "Strategist", "company": "Beacon Company", "location": "San Francisco",
            "description": "Run strategy."}]
    out = sj.assess(ads, root)
    assert [r["company"] for r in out["propose_for_pipeline"]] == ["Beacon Company"]


def test_record_refuses_an_empty_cache(root):
    (root / ".claude/skills/scan-jobs/cache.md").write_text("", encoding="utf-8")
    code, out = run(root, "record", [{"title": "x", "company": "y", "score": 1, "status": "skip",
                                      "reason": "r"}])
    assert code == 1 and out["code"] == "file_not_found"


def test_stdin_that_is_not_json_is_refused(root):
    p = subprocess.run([sys.executable, str(SCRIPT), "assess", "--repo-root", str(root)],
                       input="not json", capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["code"] == "bad_json"


def test_main_returns_0_on_success(root, monkeypatch, capsys):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([{"title": "A", "company": "B"}])))
    assert sj.main(["assess", "--repo-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_the_script_imports_its_libraries_from_any_working_directory(tmp_path):
    p = subprocess.run([sys.executable, "-c",
                        f"import runpy; runpy.run_path({str(SCRIPT)!r}, run_name='x')"],
                       cwd=str(tmp_path), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("company,proposed,mark", [
    ("Northwind Labs", False, None),                 # tracked exactly (case and spacing fold)
    ("NORTHWIND  labs", False, None),
    ("Northwindtech", True, None),                    # 613.F1: substring is not a match
    ("Northwind Labs Europe", True, "northwind labs"),  # whole-word overlap is marked, not dropped
    ("Beacon AI", True, None),
])
def test_proposals_drop_only_an_exact_pipeline_company(root, monkeypatch, company, proposed, mark):
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 8)
    out = sj.assess([{"title": "Strategist", "company": company, "location": "San Francisco",
                      "description": "Run strategy."}], root)
    got = out["propose_for_pipeline"]
    assert bool(got) is proposed
    if proposed:
        assert got[0].get("possible_match") == mark


@pytest.mark.parametrize("company", ["Job Pipeline", "Active", "Company"])
def test_pipeline_headings_and_header_row_are_not_tracked_companies(root, monkeypatch, company):
    """Only table data rows name tracked companies: not the '# Job Pipeline' or '## Active'
    headings, and not the header row."""
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 8)
    out = sj.assess([{"title": "Strategist", "company": company, "location": "San Francisco",
                      "description": "Run strategy."}], root)
    assert [r["company"] for r in out["propose_for_pipeline"]] == [company]
    assert "possible_match" not in out["propose_for_pipeline"][0]


def test_a_placeholder_pipeline_name_matches_nothing():
    """A row whose company normalizes to nothing (an em dash placeholder) must not mark
    every candidate as a possible match."""
    assert sj.tracked_match("Beacon AI", ["—"]) == ""
