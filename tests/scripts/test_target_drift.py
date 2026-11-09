"""Target titles have one source, goals are fresh, and the tools accept what Nick advertises.

target-drift-1106 (2026-11-06). Spec: output/infra-backlog/110626-target-drift-spec.md.
A thesis refresh updated goals.md and LinkedIn but none of the tools that read them: the
scorer kept reading a superseded role list, and the fetch filters cut half the titles Nick
advertises.
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

SCRIPT = REPO / "tools" / "target_drift.py"
TODAY = date(2026, 11, 20)

GOALS = """# Job Search Goals & Thesis

Last reviewed: {reviewed}

## Search Thesis

Thesis text.

## Target Criteria

- **Role family A:** placeholder.
- **Open to Work titles (LinkedIn):** {titles}.

## History (superseded)

**Role types** (ranked):
1. Generalist ops role (old list)
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


# ---------- mutation round 1 (killers on the changed lines) ----------

def test_an_open_to_work_line_outside_target_criteria_is_never_read():
    text = ("# Goals\n\n## Target Criteria\n\n- nothing\n\n## History (superseded)\n\n"
            "- **Open to Work titles (LinkedIn):** Old Title.\n")
    assert scorer._extract_target_titles(text) == []


@pytest.mark.parametrize("ctx,expected", [
    ({"goals_age_days": None, "goals_last_reviewed": None},
     "unranked: goals.md last reviewed unknown (no valid date; limit 14)"),
    ({"goals_age_days": 50, "goals_last_reviewed": "2026-10-01"},
     "unranked: goals.md last reviewed 2026-10-01 (50 days; limit 14)"),
])
def test_unranked_reason(ctx, expected):
    assert scorer.unranked_reason(ctx) == expected


def test_a_cut_is_named_with_its_reach_in_problems(tmp_path):
    _, out = run(make(tmp_path, titles="Deployment Strategist; Head of Business Operations"))
    assert "'Head of Business Operations' is cut at fetch by 1 of 2 scanned targets" in out["problems"]
    assert out["targets_checked"] == 2
    assert "population 2 | included 2" in out["reach"]


def test_a_bad_date_is_named_in_problems(tmp_path):
    _, out = run(make(tmp_path, reviewed="soon"))
    assert any("goals.md last reviewed soon (no valid date; limit 14)" in p for p in out["problems"])


@pytest.mark.parametrize("targets,spec", [
    ("companies: 3\n", SPEC),            # companies is not a list
    (TARGETS, "- a\n- b\n"),             # fit-spec is not a mapping
])
def test_a_wrong_shaped_input_exits_2_with_an_error(tmp_path, targets, spec):
    code, out = run(make(tmp_path, targets=targets, spec=spec))
    assert code == 2 and out["error"]


def test_unreadable_input_reports_the_error(tmp_path):
    code, out = run(make(tmp_path, targets="companies: [unclosed\n"))
    assert code == 2 and "scan-targets.yaml" in out["error"]


def test_a_bad_today_exits_2(tmp_path):
    code, out = run(make(tmp_path), today="2026-13-40")
    assert code == 2 and "bad --today" in out["error"]


def test_nightly_records_an_unreadable_input_as_drift(tmp_path):
    from tools import target_drift as td
    root = make(tmp_path, targets="companies: [unclosed\n")
    out = td.nightly(root, TODAY)
    assert out["drift"] is True and out["problems"][0].startswith("unreadable input:")


# ---------- review r1 (Codex F1-F4 / Grok F1, F4-F7) ----------

@pytest.mark.parametrize("line,expected", [
    ("Sr. Director of Operations; Chief of Staff", ["Sr. Director of Operations", "Chief of Staff"]),
    ("Chief of Staff; Sr. Director", ["Chief of Staff", "Sr. Director"]),
    ("A; B.", ["A", "B"]),
])
def test_periods_inside_titles_are_kept(line, expected):
    text = f"## Target Criteria\n\n- **Open to Work titles (LinkedIn):** {line}\n"
    assert scorer._extract_target_titles(text) == expected


def test_a_colon_outside_the_bold_is_read():
    text = "## Target Criteria\n\n- **Open to Work titles (LinkedIn)**: A; B.\n"
    assert scorer._extract_target_titles(text) == ["A", "B"]


def test_a_company_entry_that_is_not_a_mapping_exits_2(tmp_path):
    targets = TARGETS.replace("companies:\n", "companies:\n  - 7\n")
    code, out = run(make(tmp_path, targets=targets))
    assert code == 2 and "not a mapping" in out["error"]


def test_the_check_and_the_scan_select_targets_with_one_function():
    from tools import target_drift as td
    from tools.career_scanner import scanner
    assert td.scannable is scanner.scannable
    cs = [{"name": "A", "ats": "ashby"}, {"name": "B", "ats": "ashby", "active": False}, {"name": "C"}]
    assert [c["name"] for c in cs if scanner.scannable(c)] == ["A"]


def test_load_targets_skips_a_non_mapping_entry_instead_of_crashing(tmp_path):
    from tools.career_scanner import scanner
    root = make(tmp_path, targets="companies:\n  - 7\n  - name: X\n    ats: ashby\n")
    assert [t["name"] for t in scanner.load_targets(root)] == ["X"]


def test_nightly_on_unreadable_targets_writes_the_drift_and_a_failure(tmp_path, monkeypatch):
    from tools.career_scanner import scanner
    root = make(tmp_path, targets="companies: [unclosed\n")
    s = scanner.scan_all_targets(root, today=TODAY)
    assert s["fetch_failures"] == 1 and "scan-targets.yaml" in s["fetch_failure_detail"][0]["reason"]
    payload = scanner.read_queue_payload(root)
    assert payload["target_drift"]["drift"] is True
    assert payload["fetch_failures"] == 1


@pytest.mark.parametrize("spec", [
    'positive_title_patterns: "strategist"\nnot_fit_title_patterns: []\n',
    'positive_title_patterns: ["strategist"]\nnot_fit_title_patterns: "head of growth"\n',
])
def test_a_scalar_pattern_field_exits_2(tmp_path, spec):
    code, out = run(make(tmp_path, spec=spec))
    assert code == 2 and "not a list" in out["error"]


def _queue(tmp_path, goals_stale, roles):
    from tools.career_scanner import scanner
    p = scanner._queue_payload(roles, roles, [], [], drift={"drift": goals_stale, "goals_stale": goals_stale})
    scanner.write_queue_payload(tmp_path, p)


def test_on_stale_goals_the_queue_is_ordered_by_posting_date_not_by_old_scores(tmp_path):
    import tools.role_queue_read as rq
    old = [{"title": f"Old {i}", "company": "X", "url": f"u{i}", "score": s, "published_at": "2026-10-01"}
           for i, s in enumerate((7, 6, 5, 4, 3))]
    new = [{"title": "Deployment Strategist", "company": "Acme", "url": "n", "score": None,
            "published_at": "2026-11-19"}]
    _queue(tmp_path, True, old + new)
    out = rq.read_queue(tmp_path, top=5)
    assert out["roles"][0]["title"] == "Deployment Strategist"
    assert out["order"].startswith("posting date")


def test_on_fresh_goals_the_queue_is_ranked_by_score(tmp_path):
    import tools.role_queue_read as rq
    roles = [{"title": "Low", "company": "X", "url": "a", "score": 3, "published_at": "2026-11-19"},
             {"title": "High", "company": "X", "url": "b", "score": 8, "published_at": "2026-10-01"}]
    _queue(tmp_path, False, roles)
    out = rq.read_queue(tmp_path)
    assert [r["title"] for r in out["roles"]] == ["High", "Low"]
    assert out["order"].startswith("score")


def test_todo_placeholders_are_not_titles():
    text = "## Target Criteria\n\n- **Open to Work titles (LinkedIn):** TODO; Chief of Staff; todo (e.g., X).\n"
    assert scorer._extract_target_titles(text) == ["Chief of Staff"]


def test_the_template_and_the_example_parse():
    """The file that generates goals.md must produce the format the extractor reads (Grok F6)."""
    tpl = (REPO / "framework/templates/goals.md").read_text(encoding="utf-8")
    assert scorer._extract_target_titles(tpl) == []                 # TODO placeholders only
    assert scorer.goals_freshness(tpl, TODAY)["goals_stale"] is True  # YYYY-MM-DD is not a date
    ex = (REPO / "examples/data/goals.md").read_text(encoding="utf-8")
    assert len(scorer._extract_target_titles(ex)) == 4
    assert scorer.goals_freshness(ex, date(2026, 11, 7))["goals_stale"] is False


@pytest.mark.parametrize("line,expected", [
    # The live shape: titles, then a sentence about them after a sentence-ending period.
    ("A Strategist; Operations Lead. Every scanner must pass each one; the check reads this line.",
     ["A Strategist", "Operations Lead"]),
    ("Sr. Director; U.S. Operations Lead. Every scanner must pass each one.",
     ["Sr. Director", "U.S. Operations Lead"]),
    ("Mgr. Partnerships; Chief of Staff", ["Mgr. Partnerships", "Chief of Staff"]),
])
def test_trailing_prose_ends_the_list_but_an_abbreviation_does_not(line, expected):
    text = f"## Target Criteria\n\n- **Open to Work titles (LinkedIn):** {line}\n"
    assert scorer._extract_target_titles(text) == expected


# ---------- review r2 (Codex F1-F2 / Grok F1-F3) ----------

@pytest.mark.parametrize("targets", ["- name: X\n  ats: ashby\n", "just a string\n", "42\n"])
def test_a_valid_yaml_file_of_the_wrong_shape_does_not_crash_the_nightly_scan(tmp_path, targets):
    from tools.career_scanner import scanner
    root = make(tmp_path, targets=targets)
    assert scanner.load_targets(root) == []
    s = scanner.scan_all_targets(root, today=TODAY)
    assert s["fetch_failures"] == 1
    payload = scanner.read_queue_payload(root)
    assert payload["target_drift"]["drift"] is True and payload["fetch_failures"] == 1


@pytest.mark.parametrize("which", ["targets", "spec", "goals"])
def test_non_utf8_input_exits_2_and_never_crashes_the_nightly_scan(tmp_path, which):
    from tools.career_scanner import scanner
    root = make(tmp_path)
    path = {"targets": root / "data" / "scan-targets.yaml",
            "spec": root / "data" / "calibration" / "fit-spec.yaml",
            "goals": root / "data" / "goals.md"}[which]
    path.write_bytes(b"companies:\n  - name: \xff\xfe bad\n")
    code, out = run(root)
    assert code == 2 and out["error"]
    s = scanner.scan_all_targets(root, today=TODAY)      # must not raise
    assert scanner.read_queue_payload(root)["target_drift"]["drift"] is True


def test_unreadable_goals_is_stale_not_a_crash(tmp_path):
    root = make(tmp_path)
    (root / "data" / "goals.md").write_bytes(b"Last reviewed: 2026-11-10\n\xff\n")
    ctx = scorer.load_scoring_context(root, today=TODAY)
    assert ctx["goals_stale"] is True and ctx["target_titles"] == []


def test_on_stale_goals_an_undated_new_role_sorts_by_first_seen(tmp_path):
    import tools.role_queue_read as rq
    old = [{"title": f"Old {i}", "company": "X", "url": f"u{i}", "score": 7, "published_at": "2026-10-01",
            "first_seen": "2026-10-02"} for i in range(5)]
    new = [{"title": "Deployment Strategist", "company": "Acme", "url": "n", "score": None,
            "published_at": "", "first_seen": "2026-11-19"}]
    _queue(tmp_path, True, old + new)
    assert rq.read_queue(tmp_path, top=5)["roles"][0]["title"] == "Deployment Strategist"


@pytest.mark.parametrize("line,expected", [
    ("Exec. Director of Operations; Chief of Staff", ["Exec. Director of Operations", "Chief of Staff"]),
    ("Asst. Chief of Staff; Dept. Operations Lead; Corp. Strategy Lead. Every scanner must pass each one.",
     ["Asst. Chief of Staff", "Dept. Operations Lead", "Corp. Strategy Lead"]),
    ("Assoc. Director; Intl. Operations Lead", ["Assoc. Director", "Intl. Operations Lead"]),
])
def test_four_letter_title_abbreviations_do_not_end_the_list(line, expected):
    text = f"## Target Criteria\n\n- **Open to Work titles (LinkedIn):** {line}\n"
    assert scorer._extract_target_titles(text) == expected


# ---------- review r3 (Codex F1-F2 / Grok F1-F3) ----------

@pytest.mark.parametrize("spec", ["- a\n- b\n", "just a string\n", "42\n"])
def test_a_fit_spec_that_is_not_a_mapping_reads_as_missing(tmp_path, spec):
    root = make(tmp_path, spec=spec)
    assert scorer.load_fit_spec(root) == {}
    role = {"title": "Strategist", "company": "A", "location": "San Francisco", "description": "x"}
    assert isinstance(scorer.score_role(role, scorer.load_scoring_context(root, today=TODAY)), int)


def test_nightly_on_fresh_goals_and_a_list_fit_spec_still_writes_the_queue(tmp_path, monkeypatch):
    from tools.career_scanner import scanner
    root = make(tmp_path, spec="- a\n- b\n")
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "Open Board Co", "ats": "ashby"}])
    monkeypatch.setattr(scanner, "fetch_company_roles", lambda target, errors=None, cuts=None: [
        {"title": "Strategist", "company": "A", "location": "San Francisco", "url": "u", "description": "x"}])
    s = scanner.scan_all_targets(root, today=TODAY)
    assert len(s["roles"]) == 1 and scanner.read_queue_payload(root)["target_drift"]["drift"] is True


@pytest.mark.parametrize("field", ["role_filters", "role_excludes"])
def test_a_filter_field_that_is_not_a_list_exits_2(tmp_path, field):
    targets = f"companies:\n  - name: X\n    ats: ashby\n    {field}: 7\n"
    code, out = run(make(tmp_path, targets=targets))
    assert code == 2 and "not a list" in out["error"]


def test_a_filter_field_that_is_not_a_list_is_a_fetch_failure_not_a_crash(monkeypatch):
    from tools.career_scanner import scanner
    import tools.career_scanner.ashby as ashby   # the parser fetch_company_roles imports
    monkeypatch.setattr(ashby, "fetch_ashby", lambda slug, errors=None: [
        {"title": "Strategist", "company": "X", "location": "San Francisco", "url": "u"}])
    errors, cuts = [], []
    roles = scanner.fetch_company_roles({"name": "X", "ats": "ashby", "slug": "x", "role_filters": 7},
                                        errors=errors, cuts=cuts)
    assert roles == [] and "role_filters is not a list" in errors[0]["reason"]


def test_nightly_never_raises(tmp_path, monkeypatch):
    from tools import target_drift as td
    def boom(root, today):
        raise TypeError("unexpected")
    monkeypatch.setattr(td, "check", boom)
    out = td.nightly(tmp_path, TODAY)
    assert out["drift"] is True and "TypeError" in out["problems"][0]


def test_on_fresh_goals_rows_left_unscored_by_a_stale_scan_come_first(tmp_path):
    import tools.role_queue_read as rq
    old = [{"title": f"Old {i}", "company": "X", "url": f"u{i}", "score": 7 - i, "published_at": "2026-10-01"}
           for i in range(5)]
    unranked = [{"title": "Deployment Strategist", "company": "Acme", "url": "n", "score": None,
                 "published_at": "2026-11-10"}]
    _queue(tmp_path, False, old + unranked)
    out = rq.read_queue(tmp_path, top=5)
    assert out["roles"][0]["title"] == "Deployment Strategist"
    assert [r["title"] for r in out["roles"][1:]] == ["Old 0", "Old 1", "Old 2", "Old 3"]
    assert out["order"].startswith("unranked first")


# ---------- the catch-all stays visible (4c, 2026-11-07) ----------

def test_a_caught_drift_exception_is_named_in_the_scan_companies_output(tmp_path, monkeypatch, capsys):
    """nightly() swallows every exception so the scan still writes its queue. That is only
    safe if the exception is SHOWN: the career-scanner CLI (what /scan-companies runs)
    must print it, by type and message, in target_drift.problems."""
    from tools import target_drift as td
    from tools.career_scanner import cli, scanner
    root = make(tmp_path)

    def boom(r, today):
        raise TypeError("an input shape nobody anticipated")
    monkeypatch.setattr(td, "check", boom)
    monkeypatch.setattr(scanner, "load_targets", lambda r: [{"name": "Open Board Co", "ats": "ashby"}])
    monkeypatch.setattr(scanner, "fetch_company_roles", lambda target, errors=None, cuts=None: [])
    monkeypatch.setattr(sys, "argv", ["cli.py", "--repo-root", str(root), "--dry-run"])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["target_drift"]["drift"] is True
    assert out["target_drift"]["problems"] == [
        "drift check failed: TypeError: an input shape nobody anticipated"]


# ---------- r4 P0s (Grok r4 F1 / F2): wrong-typed elements inside a valid container ----------

_NON_STRING_FILTERS = {
    "a number": "[7]",
    "a dangling list item": "\n      - strategist\n      -\n",
}


@pytest.mark.parametrize("field", ["role_filters", "role_excludes"])
@pytest.mark.parametrize("value", list(_NON_STRING_FILTERS.values()), ids=list(_NON_STRING_FILTERS))
def test_a_filter_list_with_a_non_string_element_exits_2(tmp_path, field, value):
    targets = f"companies:\n  - name: X\n    ats: ashby\n    {field}: {value}\n"
    code, out = run(make(tmp_path, targets=targets))
    assert code == 2 and "not a list of strings" in out["error"]


@pytest.mark.parametrize("field", ["role_filters", "role_excludes"])
@pytest.mark.parametrize("value", [[7], ["strategist", None]], ids=["a number", "a dangling list item"])
def test_a_filter_list_with_a_non_string_element_is_a_fetch_failure_not_a_crash(monkeypatch, field, value):
    from tools.career_scanner import scanner
    import tools.career_scanner.ashby as ashby
    monkeypatch.setattr(ashby, "fetch_ashby", lambda slug, errors=None: [
        {"title": "Strategist", "company": "X", "location": "San Francisco", "url": "u"}])
    errors, cuts = [], []
    roles = scanner.fetch_company_roles({"name": "X", "ats": "ashby", "slug": "x", field: value},
                                        errors=errors, cuts=cuts)
    assert roles == [] and f"{field} is not a list of strings" in errors[0]["reason"]


@pytest.mark.parametrize("weights", ["7", "[1]", "high", "{in_lane_title: high}"])
def test_fit_spec_weights_that_are_not_a_mapping_of_numbers_exit_2(tmp_path, weights):
    code, out = run(make(tmp_path, spec=SPEC + f"weights: {weights}\n"))
    assert code == 2 and "weights" in out["error"]


def test_fit_spec_numeric_weights_still_pass(tmp_path):
    code, out = run(make(tmp_path, spec=SPEC + "weights: {in_lane_title: 2.5, not_fit_title: -4}\n"))
    assert code == 0, out
