"""9g: reader_review.py --panel. Placeholder seats only (public repo); dispatch is mocked,
so no model is called."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools import reader_review as rr  # noqa: E402

SEATS = ["VP Operations | owns the rollout | on-time launch | a stalled pilot",
         "CFO | approves the spend | payback period | an unfunded promise",
         "Head of Support | runs the queue | resolution time | a surprise workload"]


def test_parse_seat_needs_all_four_fields():
    assert rr.parse_seat(SEATS[0]) == {"role": "VP Operations", "mandate": "owns the rollout",
                                       "measured_on": "on-time launch", "fears": "a stalled pilot"}
    for bad in ("only | three | parts", "a | | c | d", "a | b | c | d | e"):
        with pytest.raises(ValueError):
            rr.parse_seat(bad)


def test_load_seats_takes_three_to_five_from_flags_and_file(tmp_path):
    assert len(rr.load_seats(SEATS)) == 3
    f = tmp_path / "seats.json"
    f.write_text(json.dumps([{"role": "COO", "mandate": "m", "measured_on": "x", "fears": "y"}]))
    assert [s["role"] for s in rr.load_seats(SEATS, str(f))][-1] == "COO"
    with pytest.raises(ValueError, match="3 to 5"):
        rr.load_seats(SEATS[:2])
    with pytest.raises(ValueError, match="3 to 5"):
        rr.load_seats(SEATS * 2)
    f.write_text(json.dumps({"role": "x"}))
    with pytest.raises(ValueError, match="JSON list"):
        rr.load_seats(SEATS, str(f))
    f.write_text(json.dumps([{"role": "x", "mandate": "", "measured_on": "a", "fears": "b"}]))
    with pytest.raises(ValueError, match="each seat needs"):
        rr.load_seats(SEATS, str(f))


def test_a_seat_prompt_holds_that_seat_only_and_forbids_approval():
    seats = rr.load_seats(SEATS)
    t = rr.seat_target(seats[0], "page.md", "decide whether to pilot")
    assert "VP Operations" in t and "on-time launch" in t and "a stalled pilot" in t
    assert "CFO" not in t and "Head of Support" not in t
    assert "never approval" in t and "do not praise" in t and "page.md" in t


def test_two_unrelated_objections_on_one_line_are_not_agreement():
    """846.F1/850.F1: the same line with nothing in common is two problems."""
    per = {"A": [{"location": "p:3", "summary": "Pricing figure contradicts appendix"}],
           "B": [{"location": "p:3", "summary": "Tone condescending toward operators"}]}
    assert [c["seats"] for c in rr.cluster_findings(per)] == [["A"], ["B"]]


def test_clusters_put_agreement_first():
    per = {
        "VP Operations": [{"location": "page.md:12", "summary": "Launch timeline unrealistic given budget", "severity": "P1"},
                          {"location": "page.md:40", "summary": "Typo in the header", "severity": "P2"}],
        "CFO": [{"location": "page.md:12", "summary": "Launch timeline has no budget behind it", "severity": "P0"}],
        "Head of Support": [{"location": "page.md:70", "summary": "Support staffing assumption 30% lower than "
                                                                  "headcount numbers implies overload staffing",
                             "severity": "P1"}],
        "Fourth": [{"location": "", "summary": "Support staffing assumption understates headcount overload numbers",
                    "severity": "P1"}, "not a dict"],
    }
    clusters = rr.cluster_findings(per)
    assert [c["seats"] for c in clusters[:2]] == [["CFO", "VP Operations"], ["Fourth", "Head of Support"]]
    assert clusters[-1]["seats"] == ["VP Operations"]
    assert rr.cluster_findings({"A": None}) == []


def test_panel_report_order_and_failed_seats():
    clusters = rr.cluster_findings({
        "A": [{"location": "p:1", "summary": "staffing headcount missing"}],
        "B": [{"location": "p:1", "summary": "staffing headcount absent"}, {"location": "p:2", "summary": "z"}]})
    seats = [{"role": r} for r in ("A", "B", "C")]
    text = rr.panel_report(clusters, seats, "doc.md", failed=["C"])
    must = text.index("## Must fix"); tri = text.index("## Triage")
    assert must < text.index("**2 seats** (A, B)") < tri < text.index("- B: z [p:2]")
    assert "Seats with no usable result:** C" in text
    empty = rr.panel_report([], seats, "doc.md")
    assert empty.count("None.") == 2 and "no usable result" not in empty


def _repo(tmp_path):
    doc = tmp_path / "output" / "acme" / "brief.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Brief\n\nLaunch in two weeks.\n")
    return doc


def test_run_panel_dispatches_each_seat_alone(tmp_path, monkeypatch, capsys):
    doc = _repo(tmp_path)
    calls = []

    def fake_run(argv, repo):
        """Like codex_verify: stdout carries a COUNT; the findings live in the report."""
        target = argv[argv.index("--target") + 1]
        calls.append(target)
        role = target.split(".")[0].replace("You are ", "")
        if role == "Head of Support":
            return {"rc": 1, "findings": None, "report": None, "stderr": "boom"}
        report = repo / argv[argv.index("--report") + 1]
        report.parent.mkdir(parents=True, exist_ok=True)
        block = [{"id": "F1", "severity": "P1", "location": "brief:3",
                  "summary": f"Launch timeline unrealistic, says {role}"}]
        report.write_text("# r\n\n## FINDINGS (machine-readable)\n\n```json\n" + json.dumps(block) + "\n```\n")
        return {"rc": 0, "report": str(report), "stderr": "", "findings": 1}
    monkeypatch.setattr(rr, "run_one", fake_run)
    rc = rr.main([str(doc), "--panel", "--purpose", "decide", "--repo-root", str(tmp_path),
                  *sum((["--seat", s] for s in SEATS), [])])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["failed"] == ["Head of Support"] and out["must_fix"] == 1
    assert len(calls) == 3
    for t in calls:  # no prompt names another seat
        assert sum(r in t for r in ("VP Operations", "CFO", "Head of Support")) == 1
    report = (tmp_path / out["panel_report"]).read_text()
    assert "**2 seats** (CFO, VP Operations)" in report


def test_run_panel_refusals_and_dry_run(tmp_path, monkeypatch, capsys):
    doc = _repo(tmp_path)
    monkeypatch.setattr(rr, "run_one", lambda *a: pytest.fail("dispatched"))
    base = [str(doc), "--panel", "--repo-root", str(tmp_path)]
    assert rr.main(base + ["--purpose", "p", "--seat", SEATS[0]]) == 2
    assert rr.main(base + sum((["--seat", s] for s in SEATS), [])) == 2
    assert "needs --purpose" in capsys.readouterr().err
    assert rr.main(base + ["--purpose", "p", "--dry-run", *sum((["--seat", s] for s in SEATS), [])]) == 0
    out = capsys.readouterr().out
    assert out.count("SEAT ") == 3 and "panel-1-vp-operations" in out


def test_the_three_question_mode_is_unchanged():
    t = rr.build_target("p.md", "a reader", "a purpose", "")
    assert "Q1 ACCURACY" in t and "Q2 USEFULNESS" in t and "Q3 CONFIDENTIALITY: SKIPPED" in t



def test_panel_report_lists_every_item_and_counts_extras():
    clusters = rr.cluster_findings({
        "A": [{"location": "p:1", "summary": "staffing headcount missing"},
              {"location": "p:5", "summary": "pricing discount wrong"},
              {"location": "p:5", "summary": "pricing discount stale"}],
        "B": [{"location": "p:1", "summary": "staffing headcount absent"}]})
    text = rr.panel_report(clusters, [{"role": "A"}, {"role": "B"}, {"role": "C"}], "d.md")
    assert "None." not in text
    assert "   - A: staffing headcount missing [p:1]" in text and "   - B: staffing headcount absent [p:1]" in text
    assert "(+1 more from the same seat)" in text
    one = rr.panel_report(rr.cluster_findings({"A": [{"location": "p:9", "summary": "x"}]}),
                          [{"role": "A"}], "d.md")
    assert "more from the same seat" not in one and one.count("None.") == 1


def test_run_panel_refusal_and_dry_run_print_their_text(tmp_path, monkeypatch, capsys):
    doc = _repo(tmp_path)
    monkeypatch.setattr(rr, "run_one", lambda *a: pytest.fail("dispatched"))
    base = [str(doc), "--panel", "--repo-root", str(tmp_path), "--purpose", "p"]
    assert rr.main(base + ["--seat", SEATS[0]]) == 2
    assert "reader_review: refused: a panel takes 3 to 5 seats" in capsys.readouterr().err
    assert rr.main(base + ["--dry-run", *sum((["--seat", s] for s in SEATS), [])]) == 0
    assert "Return concrete corrections only" in capsys.readouterr().out


def test_run_panel_counts_single_seat_items(tmp_path, monkeypatch, capsys):
    doc = _repo(tmp_path)

    def fake_run(argv, repo):
        report = repo / argv[argv.index("--report") + 1]
        report.parent.mkdir(parents=True, exist_ok=True)
        role = argv[argv.index("--target") + 1].split(".")[0]
        block = [{"id": "F1", "severity": "P2", "location": f"brief:{len(role)}",
                  "summary": f"distinct point {role}"}]
        report.write_text("## FINDINGS (machine-readable)\n\n```json\n" + json.dumps(block) + "\n```\n")
        return {"rc": 0, "report": str(report), "stderr": "", "findings": 1}
    monkeypatch.setattr(rr, "run_one", fake_run)
    rc = rr.main([str(doc), "--panel", "--purpose", "p", "--repo-root", str(tmp_path),
                  *sum((["--seat", s] for s in SEATS), [])])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["failed"] == [] and out["must_fix"] == 0 and out["single_seat"] == 3


def test_seats_that_slug_alike_get_distinct_reports(tmp_path, monkeypatch, capsys):
    doc = _repo(tmp_path)
    reports = []
    monkeypatch.setattr(rr, "run_one", lambda argv, repo: reports.append(argv[argv.index("--report") + 1])
                        or {"rc": 1, "findings": None, "report": None, "stderr": ""})
    seats = ["VP, Ops | a | b | c", "VP Ops | a | b | c", "CFO | a | b | c"]
    rr.main([str(doc), "--panel", "--purpose", "p", "--repo-root", str(tmp_path),
             *sum((["--seat", s] for s in seats), [])])
    assert len(set(reports)) == 3
