"""A cached /scan-jobs verdict is re-checked when the rules change (scan-drops-1106).

Until this branch a cached ad was skipped before assessment for good: after a rule change
(the Chief of Staff stage split, a cutoff, a fit-spec pattern) a role cut under the old
rules never came back (Codex 940.F2 / Grok 941.F4). Now every row scan_jobs.py writes
carries the rules version it was judged under. A cached ad seen again under a different
version is re-assessed once; it re-surfaces only when its status changed, labelled with
the old one. Rows Nick wrote by hand (no "(scan_jobs.py" marker) are never re-judged.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import scan_jobs as sj  # noqa: E402

HEAD = """# Evaluated Ads Cache

Last updated: 2026-01-01

## Evaluated Listings

| Title | Company | Fit | Status | Ad Date | URL | Notes |
|-------|---------|-----|--------|---------|-----|-------|
"""
PIPE = "# Job Pipeline\n\n| Company | Role | Stage |\n| --- | --- | --- |\n"


def make_root(tmp_path, rows, spec="weights: {}\n"):
    (tmp_path / ".claude/skills/scan-jobs").mkdir(parents=True)
    (tmp_path / ".claude/skills/scan-jobs/cache.md").write_text(HEAD + "".join(rows), encoding="utf-8")
    (tmp_path / "data/calibration").mkdir(parents=True)
    (tmp_path / "data/job-pipeline.md").write_text(PIPE, encoding="utf-8")
    (tmp_path / "data/calibration/fit-spec.yaml").write_text(spec, encoding="utf-8")
    return tmp_path


def machine_row(title, company, status, url, version):
    v = f" rules:{version}" if version else ""
    return f"| {title} | {company} | 3/10 | {status} | — | [Listing]({url}) | score 3/10 (scan_jobs.py{v}) |\n"


def hand_row(title, company, status, url):
    return f"| {title} | {company} | 25% | {status} | — | [Listing]({url}) | RESEARCHED by hand |\n"


AD = {"title": "Founding Chief of Staff", "company": "Beacon AI", "location": "San Francisco",
      "description": "Work with the founder.", "url": "https://example.com/1"}


def test_rules_version_is_stable_and_follows_fit_spec(tmp_path):
    a = make_root(tmp_path / "a", [])
    b = make_root(tmp_path / "b", [], spec="weights: {title: 2}\n")
    assert sj.rules_version(a) == sj.rules_version(a)
    assert len(sj.rules_version(a)) == 8
    assert sj.rules_version(a) != sj.rules_version(b)


def test_a_current_version_row_stays_cached(tmp_path, monkeypatch):
    root = make_root(tmp_path, [])
    v = sj.rules_version(root)
    (root / sj.CACHE).write_text(HEAD + machine_row(AD["title"], AD["company"], "skip", AD["url"], v))
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 9)
    out = sj.assess([AD], root)
    assert out["new"] == [] and [c["title"] for c in out["cached"]] == [AD["title"]]
    assert out["rechecked_unchanged"] == 0


def test_an_old_version_row_whose_status_changed_resurfaces_labelled(tmp_path, monkeypatch):
    root = make_root(tmp_path, [machine_row(AD["title"], AD["company"], "skip", AD["url"], "00000000")])
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 9)
    out = sj.assess([AD], root)
    assert out["cached"] == []
    [r] = out["new"]
    assert r["status"] == "shortlisted" and r["rechecked"] is True
    assert r["reason"] == "score 9/10 (re-checked under new rules; was skip)"


def test_a_pre_version_row_counts_as_old(tmp_path, monkeypatch):
    root = make_root(tmp_path, [machine_row(AD["title"], AD["company"], "skip", AD["url"], "")])
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 9)
    assert sj.assess([AD], root)["new"][0]["rechecked"] is True


def test_an_old_version_row_whose_status_held_is_counted_not_shown(tmp_path, monkeypatch):
    root = make_root(tmp_path, [machine_row(AD["title"], AD["company"], "skip", AD["url"], "00000000")])
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 2)
    out = sj.assess([AD], root)
    assert out["new"] == [] and out["rechecked_unchanged"] == 1
    assert [c["title"] for c in out["cached"]] == [AD["title"]]


def test_a_hand_row_is_never_rejudged(tmp_path, monkeypatch):
    root = make_root(tmp_path, [hand_row(AD["title"], AD["company"], "skip", AD["url"])])
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 10)
    out = sj.assess([AD], root)
    assert out["new"] == [] and out["rechecked_unchanged"] == 0


def test_record_writes_the_version(tmp_path):
    root = make_root(tmp_path, [])
    sj.record([{**AD, "score": 4, "status": "skip", "reason": "score 4/10", "ad_date": ""}], root)
    text = (root / sj.CACHE).read_text()
    assert f"(scan_jobs.py rules:{sj.rules_version(root)}) |" in text


def test_record_replaces_a_rechecked_row_in_place(tmp_path, monkeypatch):
    other = machine_row("Ops Lead", "Northwind", "skip", "https://example.com/2", "00000000")
    root = make_root(tmp_path, [machine_row(AD["title"], AD["company"], "skip", AD["url"], "00000000"), other])
    monkeypatch.setattr(sj, "score_role", lambda role, ctx: 9)
    [r] = sj.assess([AD], root)["new"]
    sj.record([r], root)
    rows = [l for l in (root / sj.CACHE).read_text().splitlines() if l.startswith("| ") and "Title" not in l]
    assert len(rows) == 2
    assert "| shortlisted |" in rows[0] and f"rules:{sj.rules_version(root)}" in rows[0]
    assert rows[1] == other.rstrip("\n")


def test_record_still_refuses_a_plain_duplicate(tmp_path):
    root = make_root(tmp_path, [machine_row(AD["title"], AD["company"], "skip", AD["url"], "00000000")])
    with pytest.raises(SystemExit):
        sj.record([{**AD, "score": 4, "status": "skip", "reason": "x", "ad_date": ""}], root)
