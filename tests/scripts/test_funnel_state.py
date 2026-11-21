"""Tests for tools/funnel_state.py: the morning page's funnel numbers. Placeholder data only."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(TOOLS))
from tools import funnel_state as fs  # noqa: E402

PIPE = """# Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Ops | Rejected | 2026-01-01 | — | | | |
| Acme Corp | Strategy | Withdrawn | 2026-01-02 | — | | | |
| Beta Inc | Ops | Rejected | 2026-01-01 | — | | | |
| Beta Inc | BizOps | Applied | 2026-01-05 | Await | | | |
| Zeta | Ops | Recruiter screen | 2026-01-06 | Prep | | | |
| Eta | Ops | To Apply | 2026-01-06 | Apply | | | |
| Theta | Ops | Interested | 2026-01-06 | Read | | | |
| Short | Ops |
"""

TARGETS = """
companies:
  - name: Acme Corp
    ats: ashby
    slug: acme
    notes: 'promoted 2025-11-02 from a drip'
  - name: beta inc
    ats: greenhouse
    slug: beta
  - name: Gamma LLC
    ats: lever
    slug: gamma
    added: 2025-12-01
  - name: Delta Co
    ats: lever
    active: false
    notes: 'Added 2025-12-20 by hand'
  - name: Epsilon
    notes: 'no ats; outreach only'
  - just a string
"""

QUEUE = {
    "scanned_at": "2026-01-06T16:30:00+00:00", "new_this_scan": 1, "standing_count": 2,
    "new": [{"company": "Gamma LLC", "title": "Deployment Strategist", "score": 8,
             "url": "https://jobs.example.com/g/1", "history": ""},
            "junk"],
    "standing": [{"company": "Beta Inc", "title": "Chief of Staff", "score": 6,
                  "url": "https://jobs.example.com/b/1", "history": "in pipeline: Applied (Ops)"},
                 {"company": "Gamma LLC", "title": "Deployment Strategist", "score": 8,
                  "url": "https://jobs.example.com/g/1"},
                 {"company": "Acme Corp", "title": "Unscored", "score": None,
                  "url": "https://jobs.example.com/a/9"},
                 {"company": "Acme Corp", "title": "Ops Lead", "score": 9,
                  "url": "https://jobs.example.com/a/1"}],
    "cut_at_fetch": [{"company": "Acme Corp", "title": "Ops Planning",
                      "url": "https://jobs.example.com/a/2", "rule": "title_filter",
                      "reason": "r"}],
}
NOW = datetime(2026, 1, 7, 16, 30, tzinfo=timezone.utc)


def _root(tmp_path, pipe=PIPE, targets=TARGETS, queue=QUEUE):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)
    if pipe is not None:
        (tmp_path / "data" / "job-pipeline.md").write_text(pipe)
    if targets is not None:
        (tmp_path / "data" / "scan-targets.yaml").write_text(targets)
    if queue is not None:
        (tmp_path / "tools" / ".role-queue.json").write_text(
            queue if isinstance(queue, str) else json.dumps(queue))
    return tmp_path


def _watched(root, rows, err):
    companies, c_err = fs.load_companies(root)
    return fs.watched(companies, c_err, rows, err)


def test_watched_counts_only_scannable_targets_and_classifies_by_stage_vocab(tmp_path):
    root = _root(tmp_path)
    rows, err = fs._pipeline(root)
    w = _watched(root, rows, err)
    assert w["error"] is None and w["count"] == 3 and w["entries"] == 6
    # Acme: every row terminal -> closed. beta inc (case differs) has an Applied row -> open.
    assert w["closed"] == ["Acme Corp"] and w["open"] == ["beta inc"] and w["never"] == ["Gamma LLC"]
    # The newest join date over every entry, active or not, from `added` or the notes.
    # Over the watched (scannable) set only: Delta Co (inactive, 2025-12-20) is left out.
    assert w["last_grew"] == "2025-12-01" and w["undated_entries"] == 1


def test_watched_without_a_pipeline_still_counts_and_says_why(tmp_path):
    root = _root(tmp_path, pipe=None)
    rows, err = fs._pipeline(root)
    assert rows is None and err == "file not found"
    w = _watched(root, rows, err)
    assert w["count"] == 3 and w["open"] is None and w["closed"] is None and w["never"] is None
    assert w["pipeline_error"] == "file not found"


@pytest.mark.parametrize("targets,needle", [
    (None, "not found"),
    ("companies: [unclosed", "unreadable"),
    ("other: 1\n", "no companies list"),
    ("- a\n- b\n", "no companies list"),
])
def test_watched_degrades_on_a_bad_targets_file(tmp_path, targets, needle):
    root = _root(tmp_path, targets=targets)
    assert needle in _watched(root, [], None)["error"]


def test_watched_with_no_dated_entry_has_no_last_grew(tmp_path):
    root = _root(tmp_path, targets="companies:\n  - name: A\n    ats: ashby\n")
    w = _watched(root, [], None)
    assert w["last_grew"] is None and w["undated_entries"] == 1 and w["never"] == ["A"]


def test_entry_date_prefers_added_then_notes():
    assert fs._entry_date({"added": "2025-01-02T10:00", "notes": "promoted 2024-01-01"}) == "2025-01-02"
    assert fs._entry_date({"notes": "x; promoted 2024-01-01 from y"}) == "2024-01-01"
    assert fs._entry_date({"notes": "ADDED 2024-02-03"}) == "2024-02-03"
    assert fs._entry_date({"notes": "repromoted2024-01-01"}) is None
    assert fs._entry_date({"notes": None}) is None and fs._entry_date({}) is None


def test_live_lists_active_pursuits_and_counts_other_open_rows(tmp_path):
    rows, err = fs._pipeline(_root(tmp_path))
    lv = fs.live(rows, err)
    assert [r["company"] for r in lv["rows"]] == ["Beta Inc", "Zeta"]
    assert lv["rows"][1] == {"company": "Zeta", "stage": "Recruiter screen", "updated": "2026-01-06"}
    # Eta (To Apply) and Theta (Interested) are open but not processes; the header and
    # separator rows are not rows.
    assert lv["other_open"] == 2 and lv["error"] is None
    assert fs.live(None, "file not found") == {"error": "file not found", "rows": None,
                                               "other_open": None}


def test_passed_merges_pending_and_standing_once_each_by_score(tmp_path):
    p = fs.passed(_root(tmp_path))
    assert p["error"] is None and p["new_this_scan"] == 1 and p["standing_count"] == 2
    assert [(r["company"], r["title"], r["score"]) for r in p["roles"]] == [
        ("Acme Corp", "Ops Lead", 9), ("Gamma LLC", "Deployment Strategist", 8),
        ("Beta Inc", "Chief of Staff", 6), ("Acme Corp", "Unscored", None)]
    assert p["roles"][2]["history"] == "in pipeline: Applied (Ops)"
    assert set(p["roles"][0]) == {"company", "title", "score", "history", "url", "apply_url"}
    assert p["roles"][0]["url"] == "https://jobs.example.com/a/1"


@pytest.mark.parametrize("queue,needle", [(None, "no role queue"), ("{bad", "unreadable"),
                                          ("[1]", "not an object")])
def test_passed_degrades(tmp_path, queue, needle):
    p = fs.passed(_root(tmp_path, queue=queue))
    assert needle in p["error"] and p["roles"] is None


def test_passed_with_no_lists_is_empty(tmp_path):
    p = fs.passed(_root(tmp_path, queue={"scanned_at": "x"}))
    assert p["roles"] == [] and p["error"] is None


def test_scan_age_hours():
    assert fs.scan_age_hours("2026-01-06T16:30:00+00:00", NOW) == 24.0
    assert fs.scan_age_hours("2026-01-06T16:30:00", NOW) == 24.0  # naive read as UTC
    assert fs.scan_age_hours("nope", NOW) is None
    # an offset other than UTC is honoured, never overwritten with UTC
    assert fs.scan_age_hours("2026-01-06T08:30:00-08:00", NOW) == 24.0
    naive_now = datetime(2026, 1, 7, 8, 30)
    expect = round((naive_now.astimezone() - datetime(2026, 1, 6, 16, 30, tzinfo=timezone.utc))
                   .total_seconds() / 3600, 1)
    assert fs.scan_age_hours("2026-01-06T16:30:00+00:00", naive_now) == expect


def test_collect_puts_it_together(tmp_path):
    out = fs.collect(_root(tmp_path), NOW)
    assert out["scan"] == {"scanned_at": "2026-01-06T16:30:00+00:00", "age_hours": 24.0,
                           "late_after_hours": fs.SCAN_LATE_HOURS}
    assert fs.SCAN_LATE_HOURS == 26
    assert len(out["cuts"]["cuts"]) == 1 and out["rulings"]["rulings"] == []
    assert out["watched"]["count"] == 3 and len(out["live"]["rows"]) == 2
    assert len(out["passed"]["roles"]) == 4


def test_collect_with_no_queue_has_no_scan_age(tmp_path):
    out = fs.collect(_root(tmp_path, queue=None), NOW)
    assert out["scan"]["scanned_at"] is None and out["scan"]["age_hours"] is None
    assert out["cuts"]["cuts"] is None


def test_main_returns_0_and_prints_the_cut_count(tmp_path, capsys):
    assert fs.main(["--repo-root", str(_root(tmp_path))]) == 0
    assert json.loads(capsys.readouterr().out)["cuts"]["cuts"] == 1


def test_the_script_prints_json_with_the_cut_count(tmp_path):
    root = _root(tmp_path)
    r = subprocess.run([sys.executable, str(TOOLS / "funnel_state.py"), "--repo-root", str(root)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["cuts"]["cuts"] == 1 and out["watched"]["count"] == 3


# --- what to review, and what is new ----------------------------------------------------------

SF = "San Francisco, CA"


@pytest.mark.parametrize("cut,target,want", [
    ({"rule": "title_filter", "title": "Head of Operations Planning", "location": SF}, {}, True),
    ({"rule": "title_filter", "title": "Head of Operations Planning", "location": "London"}, {}, False),
    ({"rule": "location", "title": "Strategy Lead", "location": SF}, {}, False),
    ({"rule": "title_filter", "title": "Senior Software Engineer", "location": SF}, {}, False),
    ({"rule": "title_filter", "title": "Senior Software Engineer", "location": SF},
     {"allow_engineer_titles": True}, True),
    ({"rule": "title_filter", "title": "Account Executive, West", "location": SF},
     {"role_excludes": ["account executive"]}, False),
    # The fetch gate rejects a target with malformed role_excludes (Codex F4): not to review.
    ({"rule": "title_filter", "title": "Ops Lead", "location": SF},
     {"role_excludes": "account executive"}, False),
    ({"rule": "title_filter", "title": "Ops Lead", "location": SF},
     {"role_excludes": [3]}, False),
    ({"rule": "title_filter", "title": "Ops Lead", "location": SF},
     {"role_excludes": None}, True),
    # geo_filter: false, as the fetch honours it (Fable F2)
    ({"rule": "title_filter", "title": "Ops Lead", "location": "London"}, {"geo_filter": False}, True),
    ({"rule": "title_filter", "title": "Ops Lead", "location": None}, {}, True),  # geo_ok keeps unknown
    # no config for the company: the gate cannot be replayed (Fable F9, round 2)
    ({"rule": "title_filter", "title": "Ops Lead", "location": SF}, None, False),
    ({"rule": "title_filter", "title": None, "location": SF}, {}, True),
])
def test_to_review_uses_the_scanners_own_location_and_title_tests(cut, target, want):
    assert fs.to_review(cut, target) is want


def test_record_scan_returns_the_previous_scan_and_keeps_the_last_three(tmp_path):
    assert fs.record_scan(tmp_path, "2026-01-01T08:30", ["a", "b", "a"]) == {"error": None, "previous": None}
    assert fs.record_scan(tmp_path, "2026-01-02T08:30", ["b"])["previous"] == {"a", "b"}
    # a re-render of the same scan compares with the scan before it, and does not rewrite
    path = fs.scans_path(tmp_path)
    before = path.read_text()
    assert fs.record_scan(tmp_path, "2026-01-02T08:30", ["zzz"])["previous"] == {"a", "b"}
    assert path.read_text() == before
    fs.record_scan(tmp_path, "2026-01-03T08:30", ["c"])
    fs.record_scan(tmp_path, "2026-01-04T08:30", ["d"])
    assert sorted(json.loads(path.read_text())) == ["2026-01-02T08:30", "2026-01-03T08:30",
                                                    "2026-01-04T08:30"]
    assert json.loads(path.read_text())["2026-01-02T08:30"] == ["b"]
    assert not path.with_suffix(".tmp").exists() and fs.KEEP_SCANS == 3


@pytest.mark.parametrize("body", ["{bad", "[1]", '{"x": 1}'])
def test_an_unreadable_scan_store_is_kept_and_reported(tmp_path, body):
    path = fs.scans_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(body)
    got = fs.record_scan(tmp_path, "2026-01-07T08:30", ["a"])
    assert "cut-scan store unreadable" in got["error"] and got["previous"] is None
    assert path.read_text() == body


def test_collect_flags_review_and_new_against_the_previous_scan(tmp_path):
    q = {**QUEUE, "cut_at_fetch": [
        {"company": "acme corp", "title": "Ops Planning", "url": "https://jobs.example.com/a/2",
         "rule": "title_filter", "reason": "r", "location": SF},
        {"company": "Acme Corp", "title": "Account Executive", "url": "https://jobs.example.com/a/3",
         "rule": "title_filter", "reason": "r", "location": SF},
        {"company": "Nobody", "title": "Ops", "url": "https://jobs.example.com/n/1",
         "rule": "title_filter", "reason": "r", "location": SF}]}
    targets = TARGETS.replace("    notes: 'promoted 2025-11-02 from a drip'",
                              "    notes: 'promoted 2025-11-02 from a drip'\n    role_excludes: ['account executive']")
    root = _root(tmp_path, queue=q, targets=targets)
    key0 = q["cut_at_fetch"][0]["url"]  # the store holds full posting identities
    key2 = q["cut_at_fetch"][2]["url"]
    # key2 was cut two scans ago, dropped out, and is back: new again (Codex F1).
    fs.scans_path(root).parent.mkdir(parents=True, exist_ok=True)
    fs.scans_path(root).write_text(json.dumps({"2026-01-04T16:30:00+00:00": [key2],
                                               "2026-01-05T16:30:00+00:00": [key0]}))
    out = fs.collect(root, NOW)
    c = out["cuts"]["cuts"]
    # Nobody has no scan-targets entry: not to review
    assert [x["review"] for x in c] == [True, False, False]
    assert [x["new"] for x in c] == [False, True, True]
    assert out["scans_error"] is None


def test_collect_with_an_unreadable_store_shows_everything_as_new(tmp_path):
    root = _root(tmp_path)
    fs.scans_path(root).parent.mkdir(parents=True, exist_ok=True)
    fs.scans_path(root).write_text("{bad")
    out = fs.collect(root, NOW)
    assert "unreadable" in out["scans_error"]
    assert all(c["new"] for c in out["cuts"]["cuts"])


@pytest.mark.parametrize("queue", [{"scanned_at": "x", "new": {}, "standing": []},
                                   {"scanned_at": "x", "new": "s", "standing": None}])
def test_passed_survives_malformed_nested_lists(tmp_path, queue):
    p = fs.passed(_root(tmp_path, queue=queue))
    assert p["error"] is None and p["roles"] == []


def test_passed_shows_a_non_numeric_score_as_unknown_and_sorts_it_last(tmp_path):
    q = {"scanned_at": "x", "new": [
        {"company": "A", "title": "x", "score": "high", "url": "u1"},
        {"company": "B", "title": "y", "score": True, "url": "u2"},
        {"company": "C", "title": "z", "score": 2.5, "url": "u3"}]}
    p = fs.passed(_root(tmp_path, queue=q))
    assert [(r["company"], r["score"]) for r in p["roles"]] == [("C", 2.5), ("A", None), ("B", None)]


def test_standing_roles_beyond_the_stored_cap_are_counted(tmp_path):
    p = fs.passed(_root(tmp_path, queue={**QUEUE, "standing_count": 30}))
    assert p["unlisted"] == 26  # 4 stored, 30 standing
    assert fs.passed(_root(tmp_path))["unlisted"] == 0  # standing_count 2 < 4 stored
    assert fs.passed(_root(tmp_path, queue={**QUEUE, "standing_count": "x"}))["unlisted"] == 0
    assert fs.passed(_root(tmp_path, queue={**QUEUE, "standing_count": 4}))["unlisted"] == 0



def test_a_scan_with_fetch_failures_carries_the_previous_keys_forward(tmp_path):
    # Grok F6 / Fable F1+F8, round 2: compared against a real previous scan, and stored as
    # this scan's keys plus the previous ones, so the baseline still advances.
    root = _root(tmp_path, queue={**QUEUE, "fetch_failures": 2})
    fs.scans_path(root).parent.mkdir(parents=True, exist_ok=True)
    fs.scans_path(root).write_text(json.dumps({"2026-01-05T16:30:00+00:00":
                                               ["https://jobs.example.com/a/2", "gone"]}))
    out = fs.collect(root, NOW)
    assert out["fetch_failures"] == 2
    assert [c["new"] for c in out["cuts"]["cuts"]] == [False]
    stored = json.loads(fs.scans_path(root).read_text())
    assert stored["2026-01-06T16:30:00+00:00"] == ["gone", "https://jobs.example.com/a/2"]
    assert fs.collect(tmp_path / "none", NOW)["fetch_failures"] is None


def test_record_scan_without_carry_stores_only_this_scan(tmp_path):
    fs.record_scan(tmp_path, "2026-01-01T08:30", ["a"])
    assert fs.record_scan(tmp_path, "2026-01-02T08:30", ["b"])["previous"] == {"a"}
    assert json.loads(fs.scans_path(tmp_path).read_text())["2026-01-02T08:30"] == ["b"]
    fs.record_scan(tmp_path, "2026-01-03T08:30", ["c"], carry=True)
    assert json.loads(fs.scans_path(tmp_path).read_text())["2026-01-03T08:30"] == ["b", "c"]


def test_a_store_holding_non_text_keys_is_unreadable_not_a_crash(tmp_path):
    path = fs.scans_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"2026-01-01T08:30": [["a"], {"b": 1}]}')
    got = fs.record_scan(tmp_path, "2026-01-02T08:30", ["a"])
    assert "unreadable" in got["error"] and got["previous"] is None


def test_a_store_that_cannot_be_written_degrades(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "write_text", boom)
    got = fs.record_scan(tmp_path, "2026-01-02T08:30", ["a"])
    assert got == {"error": "cut-scan store not written: read-only", "previous": None}


def test_watched_matches_names_with_the_shared_company_matcher(tmp_path):
    pipe = PIPE.replace("| Beta Inc | BizOps | Applied", "| Beta, Inc. | BizOps | Applied")
    root = _root(tmp_path, pipe=pipe)
    rows, err = fs._pipeline(root)
    assert _watched(root, rows, err)["open"] == ["beta inc"]


def test_passed_reads_non_string_fields_as_text(tmp_path):
    q = {"scanned_at": "x", "new": [{"company": None, "title": 5, "url": 7, "score": 3}],
         "goals_stale": True}
    p = fs.passed(_root(tmp_path, queue=q))
    assert p["roles"][0]["company"] == "" and p["roles"][0]["url"] == "7" and p["goals_stale"] is True
    assert fs.passed(_root(tmp_path))["goals_stale"] is False
