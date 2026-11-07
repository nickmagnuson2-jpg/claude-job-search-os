"""Target titles have one source, goals are fresh, and the tools accept what Nick advertises.

target-drift-1106 (2026-11-06). Spec: output/infra-backlog/110626-target-drift-spec.md.
The 10/20 thesis refresh updated goals.md, professional-identity.md and LinkedIn together
and none of the tools that read them: for 17 days the scorer read the June role list, and
the fetch filter on 29 targets cut 4 of the 8 titles on Nick's LinkedIn Open to Work.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.career_scanner import scorer  # noqa: E402

SCRIPT = REPO / "tools" / "check_target_drift.py"
TODAY = date(2026, 11, 20)

GOALS = """# Job Search Goals & Thesis

Last reviewed: {reviewed}

## Search Thesis

Thesis text.

## Target Criteria

- **Family A, deployment:** deployment strategist.
- **Open to Work titles (LinkedIn, live since 2026-10-20):** {titles}. Every scanner must pass each one.

## History (superseded)

**Role types** (ranked):
1. Generalist ops role at a small-but-supportive growth-stage company
"""

TARGETS = """x-lane-filters: &lane_filters
  - "strategist"
  - "chief of staff"
x-lane-excludes: &lane_excludes
  - "account executive"
companies:
  - name: Placeholder Co
    ats: ashby
    slug: placeholder
    role_filters: *lane_filters
    role_excludes: *lane_excludes
  - name: Open Board Co
    ats: ashby
    slug: open
"""

SPEC = """positive_title_patterns:
  - "strategist"
  - "chief of staff"
not_fit_title_patterns:
  - "head of growth"
"""


def make(tmp_path, titles="Deployment Strategist; Chief of Staff", reviewed="2026-11-10",
         targets=TARGETS, spec=SPEC):
    (tmp_path / "data" / "calibration").mkdir(parents=True)
    (tmp_path / "data" / "goals.md").write_text(GOALS.format(titles=titles, reviewed=reviewed), encoding="utf-8")
    (tmp_path / "data" / "scan-targets.yaml").write_text(targets, encoding="utf-8")
    (tmp_path / "data" / "calibration" / "fit-spec.yaml").write_text(spec, encoding="utf-8")
    return tmp_path


# ---------- one source for target titles ----------

def test_titles_come_from_the_open_to_work_line_only(tmp_path):
    root = make(tmp_path)
    text = (root / "data" / "goals.md").read_text()
    assert scorer._extract_target_titles(text) == ["Deployment Strategist", "Chief of Staff"]


def test_a_role_types_list_outside_target_criteria_is_never_read(tmp_path):
    """The June-list regression: a heading match anywhere in the file used to start a list."""
    root = make(tmp_path)
    titles = scorer._extract_target_titles((root / "data" / "goals.md").read_text())
    assert not any("Generalist" in t for t in titles)


@pytest.mark.parametrize("line,expected", [
    ("A;  B ;C", ["A", "B", "C"]),
    ("", []),
])
def test_title_line_parsing(tmp_path, line, expected):
    root = make(tmp_path, titles=line)
    assert scorer._extract_target_titles((root / "data" / "goals.md").read_text()) == expected


def test_no_open_to_work_line_gives_no_titles():
    assert scorer._extract_target_titles("# Goals\n\n## Target Criteria\n\n- nothing here\n") == []


# ---------- freshness (14 days) ----------

@pytest.mark.parametrize("reviewed,age,stale", [
    ("2026-11-06", 14, False),   # the boundary: 14 days is fresh
    ("2026-11-05", 15, True),
    ("2026-11-20", 0, False),
])
def test_goals_freshness(reviewed, age, stale):
    f = scorer.goals_freshness(f"Last reviewed: {reviewed}\n", TODAY)
    assert f == {"goals_last_reviewed": reviewed, "goals_age_days": age, "goals_stale": stale}


@pytest.mark.parametrize("text", ["# Goals\n", "Last reviewed: soon\n", "Last reviewed: 2026-13-40\n"])
def test_a_missing_or_bad_date_is_stale(text):
    f = scorer.goals_freshness(text, TODAY)
    assert f["goals_stale"] is True and f["goals_age_days"] is None


def test_the_limit_is_fourteen_days():
    assert scorer.GOALS_FRESH_DAYS == 14


# ---------- the drift check ----------

def run(root, today="2026-11-20"):
    p = subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(root), "--today", today],
                       capture_output=True, text=True)
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None)


def test_clean(tmp_path):
    code, out = run(make(tmp_path))
    assert code == 0 and out["drift"] is False and out["problems"] == []


def test_a_title_cut_at_fetch_names_the_target_and_reason(tmp_path):
    code, out = run(make(tmp_path, titles="Deployment Strategist; Head of Business Operations"))
    assert code == 1 and out["drift"] is True
    [t] = [x for x in out["titles"] if x["title"] == "Head of Business Operations"]
    assert t["cut_at_fetch"] == [{"target": "Placeholder Co", "reason": "title not in the target's role_filters"}]
    assert t["in_lane"] is False


def test_a_target_with_no_filters_cuts_nothing(tmp_path):
    code, out = run(make(tmp_path, titles="Head of Business Operations"))
    [t] = out["titles"]
    assert [c["target"] for c in t["cut_at_fetch"]] == ["Placeholder Co"]


def test_a_title_not_in_lane_is_drift(tmp_path):
    spec = 'positive_title_patterns:\n  - "strategist"\nnot_fit_title_patterns: []\n'
    code, out = run(make(tmp_path, spec=spec))
    assert code == 1
    assert [x["title"] for x in out["titles"] if not x["in_lane"]] == ["Chief of Staff"]


def test_a_not_fit_hit_is_drift(tmp_path):
    spec = SPEC + '  - "chief of staff"\n'
    code, out = run(make(tmp_path, spec=spec))
    assert code == 1
    assert [x["title"] for x in out["titles"] if x["not_fit"]] == ["Chief of Staff"]


def test_stale_goals_is_drift(tmp_path):
    code, out = run(make(tmp_path, reviewed="2026-10-01"))
    assert code == 1 and out["goals_stale"] is True and out["goals_age_days"] == 50


def test_no_advertised_titles_is_drift(tmp_path):
    code, out = run(make(tmp_path, titles=""))
    assert code == 1 and "no Open to Work titles" in " ".join(out["problems"])


def test_unreadable_input_exits_2(tmp_path):
    code, _ = run(make(tmp_path, targets="companies: [unclosed\n"))
    assert code == 2


# ---------- stale goals: list every role, rank none ----------

CACHE = ("# Evaluated Ads Cache\n\nLast updated: 2026-01-01\n\n| Title | Company | Fit | Status | Ad Date | URL | Notes |\n"
         "|-------|---------|-----|--------|---------|-----|-------|\n")


def test_stale_goals_list_every_role_unranked(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = make(tmp_path, reviewed="2026-10-01")
    (root / ".claude/skills/scan-jobs").mkdir(parents=True)
    (root / ".claude/skills/scan-jobs/cache.md").write_text(CACHE, encoding="utf-8")
    (root / "data" / "job-pipeline.md").write_text("# Job Pipeline\n", encoding="utf-8")
    monkeypatch.setattr(sj, "TODAY", lambda: TODAY, raising=False)
    ads = [{"title": "Strategist", "company": c, "location": "San Francisco", "description": "x",
            "url": f"https://example.com/{c}"} for c in ("B Co", "A Co")]
    out = sj.assess(ads, root)
    assert out["goals_stale"] is True
    assert [r["score"] for r in out["new"]] == [None, None]
    assert all(r["reason"].startswith("unranked: goals.md last reviewed 2026-10-01") for r in out["new"])
    assert [r["company"] for r in out["propose_for_pipeline"]] == ["B Co", "A Co"]


def test_a_stale_row_is_recorded_with_no_score(tmp_path, monkeypatch):
    """An unranked row writes '—' in the Fit column, never 'None/10'."""
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    line = sj._cache_line({"title": "T", "company": "C", "score": None, "status": "listed",
                           "url": "", "reason": "unranked: goals.md last reviewed 2026-10-01"}, "abcd1234")
    assert "| — | listed |" in line and "None" not in line


def test_fresh_goals_still_rank(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import scan_jobs as sj
    root = make(tmp_path, reviewed="2026-11-10")
    (root / ".claude/skills/scan-jobs").mkdir(parents=True)
    (root / ".claude/skills/scan-jobs/cache.md").write_text(CACHE, encoding="utf-8")
    (root / "data" / "job-pipeline.md").write_text("# Job Pipeline\n", encoding="utf-8")
    monkeypatch.setattr(sj, "TODAY", lambda: TODAY, raising=False)
    out = sj.assess([{"title": "Strategist", "company": "A Co", "location": "San Francisco",
                      "description": "x", "url": "https://example.com/a"}], root)
    assert out["goals_stale"] is False
    assert isinstance(out["new"][0]["score"], int)


# ---------- nightly: the queue carries goals_stale and target_drift ----------

def _nightly(tmp_path, monkeypatch, reviewed):
    from tools.career_scanner import scanner
    root = make(tmp_path, reviewed=reviewed)
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "Open Board Co", "ats": "ashby"}])
    roles = [{"title": "Strategist", "company": c, "location": "San Francisco", "url": f"u-{c}",
              "description": "x"} for c in ("B Co", "A Co")]
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda target, errors=None, cuts=None: [dict(r) for r in roles])
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    return scanner, root, scanner.scan_all_targets(root, today=TODAY)


def test_nightly_on_stale_goals_lists_every_role_unranked(tmp_path, monkeypatch):
    scanner, root, s = _nightly(tmp_path, monkeypatch, "2026-10-01")
    assert [r["score"] for r in s["roles"]] == [None, None]
    assert [r["company"] for r in s["roles"]] == ["B Co", "A Co"]   # input order, not ranked
    assert s["goals_stale"] is True
    assert s["target_drift"]["drift"] is True and s["target_drift"]["goals_age_days"] == 50
    payload = scanner.read_queue_payload(root)
    assert payload["goals_stale"] is True
    assert payload["target_drift"]["goals_stale"] is True


def test_nightly_on_fresh_goals_ranks_and_reports_no_drift(tmp_path, monkeypatch):
    scanner, root, s = _nightly(tmp_path, monkeypatch, "2026-11-10")
    assert all(isinstance(r["score"], int) for r in s["roles"])
    assert s["goals_stale"] is False and s["target_drift"]["drift"] is False


def test_role_queue_read_reports_target_drift(tmp_path, monkeypatch):
    import tools.role_queue_read as rq
    scanner, root, _ = _nightly(tmp_path, monkeypatch, "2026-10-01")
    out = rq.read_queue(root)
    assert out["goals_stale"] is True
    assert out["target_drift"]["drift"] is True


def test_role_queue_read_on_an_old_queue_reports_no_drift_block(tmp_path):
    import tools.role_queue_read as rq
    from tools.career_scanner import scanner
    scanner.write_queue_payload(tmp_path, scanner._queue_payload([], [], [], []))
    out = rq.read_queue(tmp_path)
    assert out["target_drift"] is None and out["goals_stale"] is None
