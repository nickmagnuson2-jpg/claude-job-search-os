"""Tests for tools/outreach_metrics.py — analytics projection of the outreach log."""
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import outreach_metrics as om  # noqa: E402

HDR = "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |"
SEP = "| --- | --- | --- | --- | --- | --- | --- |"


def touch(date_="2026-05-01", skill="cold-outreach", channel="email",
          who="Jane Roe", company="Acme", subject="Intro", status="Sent"):
    return f"| {date_} | {skill} | {channel} | {who} | {company} | {subject} | {status} |"


def log(*rows):
    return "\n".join(["# Outreach Log", "", HDR, SEP, *rows]) + "\n"


def netlog(*entries):
    """networking.md rows that parse_networking_interactions will pick up."""
    head = "# Networking\n\n| Name | Company | Date | Channel | Notes |\n| --- | --- | --- | --- | --- |\n"
    return head + "\n".join(entries) + "\n"


# --- norm_company ---

@pytest.mark.parametrize("a,b", [
    ("Acme Inc", "acme"), ("Acme, LLC", "acme"), ("Acme Co.", "acme"),
    ("A-C-M-E", "acme"), ("  Acme  ", "acme"),
])
def test_norm_company_strips_legal_suffixes_and_punctuation(a, b):
    assert om.norm_company(a) == b


def test_norm_company_does_not_collapse_distinct_names():
    assert om.norm_company("Northgate Labs") != om.norm_company("Northgate Health")


def test_norm_company_handles_empty():
    assert om.norm_company("") == ""
    assert om.norm_company(None) == ""


# --- parse_rows ---

def test_parse_rows_skips_header_separator_and_short_rows():
    content = log(touch(), "| short | row |", touch(date_="2026-05-02"))
    assert len(om.parse_rows(content)) == 2


def test_parse_rows_captures_all_seven_fields():
    r = om.parse_rows(log(touch(who="Ada L", company="Zeta", status="Replied")))[0]
    assert r["recipient"] == "Ada L"
    assert r["company"] == "Zeta"
    assert r["status"] == "Replied"


# --- replied(): the reply-attribution rule ---

def test_replied_true_when_status_says_replied():
    assert om.replied({"status": "Replied", "date": "2026-05-01",
                       "recipient": "X", "company": "Y"}, {}) is True


def test_replied_false_for_plain_sent_with_no_interaction():
    assert om.replied({"status": "Sent", "date": "2026-05-01",
                       "recipient": "X", "company": "Y"}, {}) is False


def test_replied_requires_the_interaction_to_POSTDATE_the_touch():
    """The date comparison is the whole guard. Without it one reply from a person
    contacted many times scores as many replies (live file: 58% became 90%)."""
    row = {"status": "Sent", "date": "2026-05-10", "recipient": "Jane Roe",
           "company": "Acme"}
    key = om.reconcile_key("Jane Roe", "Acme")
    assert om.replied(row, {key: date(2026, 5, 20)}) is True, "after the touch counts"
    assert om.replied(row, {key: date(2026, 5, 1)}) is False, "before the touch must not"
    assert om.replied(row, {key: date(2026, 5, 10)}) is False, "same day is not after"


def test_replied_never_overrides_an_explicit_no_reply():
    """Parity with outreach_pending.py, which only upgrades sent/drafted/pending."""
    row = {"status": "No reply", "date": "2026-05-10", "recipient": "Jane Roe",
           "company": "Acme"}
    key = om.reconcile_key("Jane Roe", "Acme")
    assert om.replied(row, {key: date(2026, 6, 1)}) is False


def test_replied_false_on_unparseable_date():
    row = {"status": "Sent", "date": "not-a-date", "recipient": "Jane Roe",
           "company": "Acme"}
    key = om.reconcile_key("Jane Roe", "Acme")
    assert om.replied(row, {key: date(2026, 6, 1)}) is False


# --- compute ---

def test_compute_counts_cold_and_followup_and_their_ratio():
    rows = [touch(skill="cold-outreach")] * 2 + [touch(skill="follow-up")] * 6
    out = om.compute(log(*rows), "", [], date(2026, 6, 1))["totals"]
    assert out["cold"] == 2
    assert out["followup"] == 6
    assert out["followups_per_cold"] == 3.0
    assert out["cold_share"] == 25.0


def test_followups_per_cold_is_none_when_no_cold_outreach():
    out = om.compute(log(touch(skill="follow-up")), "", [], date(2026, 6, 1))["totals"]
    assert out["followups_per_cold"] is None, "must not divide by zero"


def test_compute_counts_distinct_recipients_not_touches():
    rows = [touch(who="Jane Roe"), touch(who="jane roe"), touch(who="Ada L")]
    out = om.compute(log(*rows), "", [], date(2026, 6, 1))
    assert out["totals"]["touches"] == 3
    assert out["totals"]["distinct_recipients"] == 2
    assert out["reach"]["touches_per_person"] == 1.5


def test_reach_buckets_split_one_off_from_repeated_contacts():
    rows = [touch(who="Solo")] + [touch(who="Repeat")] * 5
    reach = om.compute(log(*rows), "", [], date(2026, 6, 1))["reach"]
    assert reach["contacted_once"] == 1
    assert reach["contacted_5_plus"] == 1


def test_coverage_counts_pipeline_companies_with_no_outreach():
    pipeline = [{"company": "Acme"}, {"company": "Beta"}, {"company": "Gamma"}]
    out = om.compute(log(touch(company="Acme")), "", pipeline, date(2026, 6, 1))
    cov = out["coverage"]
    assert cov["pipeline_companies"] == 3
    assert cov["with_outreach"] == 1
    assert cov["without_outreach"] == 2
    assert cov["pct_without"] == 67


def test_by_month_separates_cold_from_followup():
    rows = [touch(date_="2026-05-01", skill="cold-outreach"),
            touch(date_="2026-05-02", skill="follow-up"),
            touch(date_="2026-06-01", skill="follow-up")]
    months = {m["month"]: m for m in
              om.compute(log(*rows), "", [], date(2026, 7, 1))["by_month"]}
    assert months["2026-05"] == {"month": "2026-05", "all": 2, "cold": 1,
                                 "followup": 1, "replied": 0}
    assert months["2026-06"]["cold"] == 0


def test_by_channel_is_ordered_by_volume_descending():
    rows = [touch(channel="email")] * 3 + [touch(channel="linkedin")]
    chans = om.compute(log(*rows), "", [], date(2026, 6, 1))["by_channel"]
    assert [c["channel"] for c in chans] == ["email", "linkedin"]
    assert chans[0]["n"] == 3


def test_sanity_warnings_empty_on_coherent_input():
    out = om.compute(log(touch(), touch(date_="2026-05-02")), "", [], date(2026, 6, 1))
    assert out["sanity_warnings"] == []


def test_response_rate_is_replies_over_touches():
    rows = [touch(status="Replied"), touch(status="Sent"),
            touch(status="Sent"), touch(status="Sent")]
    assert om.compute(log(*rows), "", [], date(2026, 6, 1))["totals"]["response_rate"] == 25.0


def test_empty_log_yields_zeros_not_a_crash():
    out = om.compute(log(), "", [], date(2026, 6, 1))
    assert out["totals"]["touches"] == 0
    assert out["totals"]["response_rate"] == 0.0


# --- main ---

def test_main_refuses_an_empty_outreach_log(tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    (d / "outreach-log.md").write_text("", encoding="utf-8")
    assert om.main(["--repo-root", str(tmp_path)]) == 2
    assert "refusing" in capsys.readouterr().err.lower()


# --- cross-tool parity ---

def test_parity_with_outreach_pending_on_the_live_file():
    """Both tools answer 'how many replies' and MUST agree. They diverged at build time
    (85 vs 76) because this one overrode an explicit 'No reply'. Cross-tool parity is
    the guard named in CLAUDE.md for duplicated domain logic."""
    import json
    import subprocess
    root = REPO
    if not (root / "data" / "outreach-log.md").exists():
        pytest.skip("live outreach log not present")
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    a = json.loads(subprocess.run(
        [sys.executable, str(root / "tools" / "outreach_metrics.py")],
        capture_output=True, text=True, env=env, cwd=root).stdout)
    b = json.loads(subprocess.run(
        [sys.executable, str(root / "tools" / "outreach_pending.py"),
         "--lookback-days", "400"],
        capture_output=True, text=True, env=env, cwd=root).stdout)
    assert a["totals"]["replied"] == b["recent_outreach"]["replied"]
    assert a["totals"]["touches"] == b["recent_outreach"]["sent"]


# --- sanity_check: the invariants must actually be able to fire ---

def test_sanity_check_silent_on_coherent_aggregates():
    assert om.sanity_check(n_replied=5, n_touches=10, cold=3, followup=7,
                           n_covered=2, n_pipeline=10) == []


def test_sanity_check_flags_more_replies_than_touches():
    w = om.sanity_check(n_replied=11, n_touches=10, cold=0, followup=0,
                        n_covered=0, n_pipeline=0)
    assert any("replied (11) > touches (10)" in x for x in w)


def test_sanity_check_flags_skill_counts_exceeding_touches():
    w = om.sanity_check(n_replied=0, n_touches=5, cold=4, followup=4,
                        n_covered=0, n_pipeline=0)
    assert any("cold+followup (8) > touches (5)" in x for x in w)


def test_sanity_check_flags_coverage_exceeding_the_pipeline():
    w = om.sanity_check(n_replied=0, n_touches=1, cold=0, followup=0,
                        n_covered=9, n_pipeline=3)
    assert any("covered companies (9) exceed pipeline (3)" in x for x in w)


def test_sanity_check_does_not_divide_by_zero_on_empty_input():
    assert om.sanity_check(0, 0, 0, 0, 0, 0) == []


def test_sanity_check_reports_every_violation_not_just_the_first():
    """11 replies over 10 touches trips BOTH the count invariant and the rate bound,
    so this input violates all four. The check must not short-circuit on the first."""
    w = om.sanity_check(n_replied=11, n_touches=10, cold=9, followup=9,
                        n_covered=5, n_pipeline=1)
    assert len(w) == 4


# --- aggregation branches ---

def test_replied_touches_are_counted_per_month_and_per_channel():
    rows = [touch(date_="2026-05-01", status="Replied", channel="email"),
            touch(date_="2026-05-02", status="Sent", channel="email"),
            touch(date_="2026-05-03", status="Replied", channel="linkedin")]
    out = om.compute(log(*rows), "", [], date(2026, 6, 1))
    may = next(m for m in out["by_month"] if m["month"] == "2026-05")
    assert may["replied"] == 2
    chans = {c["channel"]: c for c in out["by_channel"]}
    assert chans["email"]["replied"] == 1
    assert chans["linkedin"]["replied"] == 1


def test_rows_with_unparseable_dates_are_excluded_from_by_month():
    rows = [touch(date_="2026-05-01"), touch(date_="not-a-date")]
    out = om.compute(log(*rows), "", [], date(2026, 6, 1))
    assert sum(m["all"] for m in out["by_month"]) == 1
    assert out["totals"]["touches"] == 2, "still counted in totals, only binned out"


def test_a_skill_that_is_neither_cold_nor_followup_counts_in_neither():
    out = om.compute(log(touch(skill="draft-email")), "", [], date(2026, 6, 1))
    assert out["totals"]["cold"] == 0
    assert out["totals"]["followup"] == 0
    assert out["totals"]["touches"] == 1
    m = out["by_month"][0]
    assert m["all"] == 1 and m["cold"] == 0 and m["followup"] == 0


def test_contacted_once_is_exactly_one_not_at_most_one():
    """THREE distinct counts, deliberately. With only 1x and 2x present, `== 1` and
    `!= 1` both return 1 and the test proves nothing."""
    rows = [touch(who="Solo")] + [touch(who="Twice")] * 2 + [touch(who="Thrice")] * 3
    assert om.compute(log(*rows), "", [], date(2026, 6, 1))["reach"]["contacted_once"] == 1


def test_contacted_5_plus_includes_exactly_five():
    """Three buckets so `>= 5` and `< 5` give different answers (1 vs 2)."""
    rows = ([touch(who="Five")] * 5 + [touch(who="Four")] * 4
            + [touch(who="Three")] * 3)
    assert om.compute(log(*rows), "", [], date(2026, 6, 1))["reach"]["contacted_5_plus"] == 1


def test_company_cell_that_normalises_to_empty_is_not_a_distinct_company():
    """Guards `companies.discard("")`. Whitespace alone cannot reach this: parse_rows
    strips cells, so '   ' is caught by the placeholder filter. A cell that is ONLY a
    legal suffix survives that filter and norm_company then flattens it to '', which is
    the case the discard exists for. Without it, '' counts as a company."""
    assert om.norm_company("LLC") == "", "fixture assumption"
    rows = [touch(company="Acme"), touch(company="LLC")]
    out = om.compute(log(*rows), "", [], date(2026, 6, 1))
    assert out["totals"]["distinct_companies"] == 1


def test_blank_company_cells_do_not_become_a_company():
    rows = [touch(company="Acme"), touch(company="—"), touch(company="")]
    out = om.compute(log(*rows), "", [{"company": "Acme"}], date(2026, 6, 1))
    assert out["totals"]["distinct_companies"] == 1
    assert out["coverage"]["with_outreach"] == 1


def test_blank_pipeline_company_is_not_counted_as_uncovered():
    pipeline = [{"company": "Acme"}, {"company": ""}, {"company": "  "}]
    cov = om.compute(log(touch(company="Acme")), "", pipeline, date(2026, 6, 1))["coverage"]
    assert cov["pipeline_companies"] == 1
    assert cov["without_outreach"] == 0


# --- main --out path ---

def test_main_writes_out_file_and_reports_the_count(tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    (d / "outreach-log.md").write_text(log(touch(), touch(date_="2026-05-02")),
                                       encoding="utf-8")
    (d / "networking.md").write_text("", encoding="utf-8")
    (d / "job-pipeline.md").write_text("", encoding="utf-8")
    dest = tmp_path / "m.json"
    assert om.main(["--repo-root", str(tmp_path), "--out", str(dest)]) == 0
    assert dest.exists()
    assert '"touches": 2' in dest.read_text()
    assert "2 touches" in capsys.readouterr().out


def test_main_prints_to_stdout_when_no_out_file(tmp_path, capsys):
    import json
    d = tmp_path / "data"
    d.mkdir()
    (d / "outreach-log.md").write_text(log(touch()), encoding="utf-8")
    (d / "networking.md").write_text("", encoding="utf-8")
    (d / "job-pipeline.md").write_text("", encoding="utf-8")
    assert om.main(["--repo-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["touches"] == 1
