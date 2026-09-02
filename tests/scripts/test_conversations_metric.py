"""Invariants for the outcome metric that /standup now leads with.

Two defect classes are pinned here, both found on the 2026-09-01 first real-data run
(the fixture-green/real-data-wrong trap): an exact-match drill set silently counted
"pre-call prep drill" as a real interview, and loop OUTCOME records (offer/rejection
news) were counted as conversations that happened, inflating 30d by 2.

The third invariant is the one that matters most for honesty: an unreadable source must
yield null, never 0. "No conversations" and "could not read the file" must never render
the same, because /standup reshapes Nick's whole day on a low count.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import conversations_metric as cm  # noqa: E402


class TestStageClassification:
    def test_prep_drill_is_a_drill_not_an_interview(self):
        # exact-match set missed this on 2026-09-01; substring match is required
        assert cm._classify_stage("pre-call prep drill") == "drill"

    def test_plain_drill_stages_are_drills(self):
        for s in ("drill", "sim", "practice", "rep", "n/a", ""):
            assert cm._classify_stage(s) == "drill", s

    def test_loop_outcome_is_not_a_conversation(self):
        assert cm._classify_stage("loop-outcome") == "outcome"

    def test_real_stages_are_conversations(self):
        for s in ("recruiter-screen", "onsite-loop", "founder-meet",
                  "hiring-manager", "peer-meet", "networking"):
            assert cm._classify_stage(s) == "conversation", s

    def test_classification_is_case_insensitive(self):
        assert cm._classify_stage("  Loop-Outcome ") == "outcome"
        assert cm._classify_stage("PRE-CALL PREP DRILL") == "drill"


class TestUnreadableSourcesAreNullNotZero:
    def test_missing_networking_file_yields_null_counts(self, tmp_path):
        r = cm.count_conversations(tmp_path / "nope.md", date(2026, 9, 1))
        assert r["readable"] is False
        assert r["counts"] is None, "must be null, never 0"

    def test_missing_progress_dir_yields_null_counts(self, tmp_path):
        r = cm.count_interviews(tmp_path / "nope", date(2026, 9, 1))
        assert r["readable"] is False
        assert r["counts"] is None, "must be null, never 0"

    def test_missing_outreach_file_yields_null_counts(self, tmp_path):
        r = cm.count_outreach(tmp_path / "nope.md", date(2026, 9, 1))
        assert r["readable"] is False
        assert r["counts"] is None, "must be null, never 0"

    def test_incomplete_run_reports_complete_false(self, tmp_path):
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "conversations_metric.py"),
             "--repo-root", str(tmp_path), "--target-date", "2026-09-01"],
            capture_output=True, text=True, check=True)
        d = json.loads(out.stdout)
        assert d["complete"] is False
        assert len(d["unreadable_sources"]) == 3
        assert d["conversations_and_interviews"] is None


class TestUnknownTypesAreReportedNotDropped:
    def test_unmapped_interaction_type_is_surfaced(self, tmp_path):
        f = tmp_path / "networking.md"
        f.write_text("#### 2026-08-30 | telepathy | chat\n", encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["unknown_types"] == [("telepathy", 1)]

    def test_live_type_counted_async_type_not(self, tmp_path):
        f = tmp_path / "networking.md"
        f.write_text("#### 2026-08-30 | call | x\n#### 2026-08-30 | email | y\n",
                     encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["counts"]["7d"] == 1
        assert r["async_counts"]["7d"] == 1


class TestNoConversionRateIsEverComputed:
    def test_output_carries_counts_not_a_ratio(self, tmp_path):
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "conversations_metric.py"),
             "--repo-root", str(ROOT), "--target-date", "2026-09-01"],
            capture_output=True, text=True, check=True)
        d = json.loads(out.stdout)
        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        assert "ratio" not in set(keys(d)), "a ratio key reads as causal and is unsupported"
        assert "_note" in d["outcome_vs_input"]


class TestWindowBoundaries:
    def test_entry_older_than_window_is_excluded(self, tmp_path):
        f = tmp_path / "networking.md"
        # 2026-08-24 is 8 days before 2026-09-01 -> outside 7d, inside 30d
        f.write_text("#### 2026-08-24 | call | x\n", encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["counts"]["7d"] == 0
        assert r["counts"]["30d"] == 1

    def test_future_dated_entry_is_not_counted(self, tmp_path):
        f = tmp_path / "networking.md"
        f.write_text("#### 2026-09-20 | call | x\n", encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["counts"]["7d"] == 0


def _progress(tmp_path, name, stage):
    d = tmp_path / "coaching" / "progress"
    d.mkdir(parents=True, exist_ok=True)
    body = "<!-- session-metadata\nformat: x\n"
    if stage is not None:
        body += f"stage: {stage}\n"
    body += "-->\n\n# title\n"
    (d / name).write_text(body, encoding="utf-8")
    return d


class TestCountInterviewsOnFixtures:
    TODAY = date(2026, 9, 1)

    def test_real_interview_counted_in_right_windows(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-acme-screen.md", "recruiter-screen")
        r = cm.count_interviews(d, self.TODAY)
        assert r["readable"] is True
        assert r["counts"] == {"7d": 1, "30d": 1, "90d": 1}

    def test_drill_is_excluded_from_counts(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-x-sim.md", "drill")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["7d"] == 0

    def test_prep_drill_excluded_via_substring(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-x.md", "pre-call prep drill")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["7d"] == 0, "the 2026-09-01 real-data defect"

    def test_outcome_counted_separately_not_as_conversation(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-x-outcome.md", "loop-outcome")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["7d"] == 0
        assert r["outcome_counts"]["7d"] == 1

    def test_untagged_file_is_reported_not_counted(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-x.md", None)
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["7d"] == 0
        assert "2026-08-30-1100-x.md" in r["untagged"]

    def test_undated_filename_ignored(self, tmp_path):
        d = _progress(tmp_path, "_summary.md", "recruiter-screen")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["90d"] == 0

    def test_out_of_window_interview_not_in_recent(self, tmp_path):
        d = _progress(tmp_path, "2026-01-02-1100-old.md", "recruiter-screen")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["90d"] == 0
        assert r["recent"] == []


class TestCountOutreachOnFixtures:
    TODAY = date(2026, 9, 1)
    HEAD = ("| Date | Skill | Channel | Recipient | Company | Subject | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n")

    def _log(self, tmp_path, rows):
        f = tmp_path / "outreach-log.md"
        f.write_text(self.HEAD + rows, encoding="utf-8")
        return f

    def test_sent_row_counted(self, tmp_path):
        f = self._log(tmp_path, "| 2026-08-30 | cold | email | A | B | s | Sent |\n")
        assert cm.count_outreach(f, self.TODAY)["counts"]["7d"] == 1

    def test_drafted_row_not_counted(self, tmp_path):
        f = self._log(tmp_path, "| 2026-08-30 | cold | email | A | B | s | Drafted |\n")
        assert cm.count_outreach(f, self.TODAY)["counts"]["7d"] == 0

    def test_header_separator_rows_ignored(self, tmp_path):
        f = self._log(tmp_path, "")
        assert cm.count_outreach(f, self.TODAY)["counts"] == {"7d": 0, "30d": 0, "90d": 0}

    def test_short_row_ignored(self, tmp_path):
        f = self._log(tmp_path, "| 2026-08-30 | Sent |\n")
        assert cm.count_outreach(f, self.TODAY)["counts"]["7d"] == 0

    def test_status_match_is_case_insensitive(self, tmp_path):
        f = self._log(tmp_path, "| 2026-08-30 | cold | email | A | B | s | SENT |\n")
        assert cm.count_outreach(f, self.TODAY)["counts"]["7d"] == 1

    def test_window_split(self, tmp_path):
        f = self._log(
            tmp_path,
            "| 2026-08-30 | c | email | A | B | s | Sent |\n"
            "| 2026-08-10 | c | email | A | B | s | Sent |\n"
            "| 2026-06-20 | c | email | A | B | s | Sent |\n")
        assert cm.count_outreach(f, self.TODAY)["counts"] == {"7d": 1, "30d": 2, "90d": 3}


class TestMetaTypesAreSilent:
    def test_meta_type_is_neither_counted_nor_unknown(self, tmp_path):
        f = tmp_path / "networking.md"
        f.write_text("#### 2026-08-30 | closeout | x\n", encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["counts"]["7d"] == 0
        assert r["async_counts"]["7d"] == 0
        assert r["unknown_types"] == [], "META must be silent, not unknown"

    def test_undated_or_malformed_header_skipped(self, tmp_path):
        f = tmp_path / "networking.md"
        f.write_text("#### 2026-13-45 | call | x\n#### 2026-08-30 | call | y\n",
                     encoding="utf-8")
        r = cm.count_conversations(f, date(2026, 9, 1))
        assert r["counts"]["7d"] == 1


class TestBucket:
    TODAY = date(2026, 9, 1)

    def test_today_lands_in_smallest_window(self):
        assert cm._bucket(date(2026, 9, 1), self.TODAY) == 7

    def test_boundary_day_7_falls_to_next_window(self):
        assert cm._bucket(date(2026, 8, 25), self.TODAY) == 30

    def test_older_than_largest_window_is_none(self):
        assert cm._bucket(date(2025, 1, 1), self.TODAY) is None

    def test_future_date_is_none(self):
        assert cm._bucket(date(2026, 9, 20), self.TODAY) is None


class TestRecentAndUntaggedListsAreScoped:
    TODAY = date(2026, 9, 1)

    def test_in_window_interview_appears_in_recent(self, tmp_path):
        d = _progress(tmp_path, "2026-08-30-1100-acme-screen.md", "recruiter-screen")
        r = cm.count_interviews(d, self.TODAY)
        assert r["recent"] == [{"date": "2026-08-30",
                                "file": "2026-08-30-1100-acme-screen.md",
                                "stage": "recruiter-screen"}]

    def test_untagged_out_of_window_is_not_listed(self, tmp_path):
        d = _progress(tmp_path, "2025-01-02-1100-old.md", None)
        r = cm.count_interviews(d, self.TODAY)
        assert r["untagged"] == [], "untagged list is window-scoped"

    def test_unparseable_date_in_filename_is_skipped(self, tmp_path):
        d = _progress(tmp_path, "2026-13-45-1100-bad.md", "recruiter-screen")
        r = cm.count_interviews(d, self.TODAY)
        assert r["counts"]["90d"] == 0
        assert r["recent"] == []


class TestOutreachMalformedRows:
    TODAY = date(2026, 9, 1)
    HEAD = ("| Date | Skill | Channel | Recipient | Company | Subject | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n")

    def test_unparseable_date_row_skipped(self, tmp_path):
        f = tmp_path / "outreach-log.md"
        f.write_text(self.HEAD + "| not-a-date | c | email | A | B | s | Sent |\n",
                     encoding="utf-8")
        assert cm.count_outreach(f, self.TODAY)["counts"]["90d"] == 0

    def test_prose_line_between_rows_skipped(self, tmp_path):
        f = tmp_path / "outreach-log.md"
        f.write_text(self.HEAD + "some prose\n"
                     "| 2026-08-30 | c | email | A | B | s | Sent |\n", encoding="utf-8")
        assert cm.count_outreach(f, self.TODAY)["counts"]["7d"] == 1


class TestMainExitPaths:
    def test_bad_target_date_errors_with_exit_2(self, tmp_path):
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "conversations_metric.py"),
             "--repo-root", str(tmp_path), "--target-date", "not-a-date"],
            capture_output=True, text=True)
        assert out.returncode == 2
        assert json.loads(out.stdout)["status"] == "error"

    def test_good_run_exits_zero_and_combines(self, tmp_path):
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "conversations_metric.py"),
             "--repo-root", str(ROOT), "--target-date", "2026-09-01"],
            capture_output=True, text=True)
        assert out.returncode == 0
        d = json.loads(out.stdout)
        assert d["complete"] is True
        assert d["conversations_and_interviews"] is not None
        # combined must actually be the sum of its two parts, not a placeholder
        for w in ("7d", "30d", "90d"):
            assert (d["conversations_and_interviews"][w]
                    == d["live_conversations"]["counts"][w]
                    + d["interviews"]["counts"][w])
        assert d["outcome_vs_input"]["7d"]["outreach_sent"] == d["outreach_sent"]["counts"]["7d"]
