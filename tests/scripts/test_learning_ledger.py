"""One record shape across every lane the learning loop captures into.

The case that carries this file is test_a_friction_rung_is_a_demand_not_a_completion. The
friction Promotion column looks like a done/not-done flag and is not: it records which
ladder rung the row reached. Reading it as "promoted" reported 0 of 303 promoted, a
degenerate zero that was a parser bug, not a finding. The real state is that 79 rows demand
a mandatory script patch and no field anywhere records whether one was written.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import learning_ledger as ll  # noqa: E402


# ---------------------------------------------------------------- table parsing

def test_a_separator_row_is_not_a_record():
    assert ll._row_cells("|---|---|---|") == []
    assert ll._row_cells("| :--- | ---: |") == []


def test_a_data_row_splits_into_cells():
    assert ll._row_cells("| a | b | c |") == ["a", "b", "c"]


def test_a_non_table_line_yields_nothing():
    assert ll._row_cells("just prose") == []


@pytest.mark.parametrize("raw,expected", [("3", 3), ("occurrences: 5", 5), ("", 1), ("—", 1)])
def test_int_extraction(raw, expected):
    assert ll._int(raw) == expected


def test_int_uses_the_given_default_when_no_digits():
    assert ll._int("—", default=0) == 0


# ---------------------------------------------------------------- friction semantics

def test_a_friction_rung_is_a_demand_not_a_completion(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text(
        "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
        "|---|---|---|---|---|---|\n"
        "| 2026-08-01 | tool.py | broke | did x | 3 | script-patch (mandatory) |\n"
        "| 2026-08-02 | other.py | broke | did y | 1 | — |\n", encoding="utf-8")
    recs = ll.read_friction(p)
    assert len(recs) == 2
    demanded = [r for r in recs if r["disposition"] == ll.DISPOSITION_OPEN]
    assert len(demanded) == 1 and demanded[0]["occurrences"] == 3
    assert "script-patch" in demanded[0]["evidence"]
    assert recs[1]["disposition"] == "no-action-needed", (
        "an em-dash rung demands nothing; counting it as open inflates the backlog"
    )


def test_a_rung_of_memory_do_now_also_counts_as_demanded(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text("| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
                 "|---|---|---|---|---|---|\n"
                 "| 2026-08-01 | t.py | b | f | 2 | memory (do now) |\n", encoding="utf-8")
    assert ll.read_friction(p)[0]["disposition"] == ll.DISPOSITION_OPEN


def test_the_friction_header_row_is_not_a_record(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text("| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
                 "|---|---|---|---|---|---|\n", encoding="utf-8")
    assert ll.read_friction(p) == []


def test_a_missing_friction_file_is_not_an_error(tmp_path):
    assert ll.read_friction(tmp_path / "absent.md") == []


# ---------------------------------------------------------------- lessons

def test_section_2_carries_occurrences_and_promotion(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 2 — Email\n"
                 "| Pattern | Rule | Occurrences | Promoted | Date |\n"
                 "|---|---|---|---|---|\n"
                 "| flat opener | add warmth | 2 | Yes | 2026-08-13 |\n"
                 "| hedged ask | cut it | 3 | No | 2026-08-14 |\n", encoding="utf-8")
    recs = ll.read_lessons(p)
    assert [r["occurrences"] for r in recs] == [2, 3]
    assert [r["disposition"] for r in recs] == [ll.DISPOSITION_DONE, ll.DISPOSITION_OPEN]


def test_section_1_has_no_occurrence_column_and_says_so(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 1 — General\n"
                 "| # | Pattern (what went wrong) | Rule (what to do instead) | Date |\n"
                 "|---|---|---|---|\n"
                 "| 1 | did a thing | do the other thing | 2026-02-24 |\n", encoding="utf-8")
    recs = ll.read_lessons(p)
    assert len(recs) == 1 and recs[0]["occurrences"] == 1
    assert "no occurrence column" in recs[0]["evidence"], (
        "a lane that cannot express a repeat can never trip a count gate; that must be "
        "stated in the record, not silently defaulted to 1"
    )


def test_lines_before_any_section_heading_are_ignored(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("| stray | table | row | here |\n## Section 1\n", encoding="utf-8")
    assert ll.read_lessons(p) == []


def test_a_missing_lessons_file_is_not_an_error(tmp_path):
    assert ll.read_lessons(tmp_path / "absent.md") == []


# ---------------------------------------------------------------- memory

def write_rule(d: Path, name, occ=1, promoted="no", terminal="false"):
    (d / name).write_text(
        f'---\nname: {name[:-3]}\ndescription: "a rule"\nmetadata:\n'
        f'  occurrences: {occ}\n  promoted: "{promoted}"\n  terminal: {terminal}\n'
        f'  reopen_gate: "3rd fire"\n---\nBody\n', encoding="utf-8")


def test_memory_dispositions(tmp_path):
    write_rule(tmp_path, "feedback_a.md", occ=2, promoted="no")
    write_rule(tmp_path, "feedback_b.md", occ=4, promoted="yes -- hook")
    write_rule(tmp_path, "feedback_c.md", occ=5, promoted="no", terminal="true")
    recs = {r["ref"]: r for r in ll.read_memory(tmp_path)}
    assert recs["feedback_a.md"]["disposition"] == ll.DISPOSITION_OPEN
    assert recs["feedback_b.md"]["disposition"] == ll.DISPOSITION_DONE
    assert recs["feedback_c.md"]["disposition"] == ll.DISPOSITION_TERMINAL


def test_partial_is_open_not_promoted(tmp_path):
    write_rule(tmp_path, "feedback_a.md", occ=3, promoted="partial -- half landed")
    assert ll.read_memory(tmp_path)[0]["disposition"] == ll.DISPOSITION_OPEN, (
        "partial means enforcement is half-landed; counting it as done retires it early"
    )


# ---------------------------------------------------------------- anti-patterns

def test_resolved_and_active_antipatterns_are_distinguished(tmp_path):
    p = tmp_path / "anti-pattern-tracker.md"
    p.write_text("## Active Anti-Patterns\n### Filler hedging\n**Status:** Persistent\n"
                 "## Resolved Anti-Patterns\n### Old thing\n**Status:** Fixed\n",
                 encoding="utf-8")
    recs = ll.read_antipatterns(p)
    assert [r["disposition"] for r in recs] == [ll.DISPOSITION_OPEN, ll.DISPOSITION_DONE]


def test_the_status_line_becomes_the_evidence(tmp_path):
    p = tmp_path / "anti-pattern-tracker.md"
    p.write_text("## Active\n### Thing\n**Status:** Persistent\n", encoding="utf-8")
    assert ll.read_antipatterns(p)[0]["evidence"] == "Persistent"


# ---------------------------------------------------------------- aggregate + CLI

def test_due_counts_only_open_records_at_two_or_more(tmp_path):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_due.md", occ=3, promoted="no")
    write_rule(mem, "feedback_once.md", occ=1, promoted="no")
    write_rule(mem, "feedback_done.md", occ=9, promoted="yes -- hook")
    repo = tmp_path / "repo"; (repo / "memory").mkdir(parents=True)
    (repo / "coaching").mkdir()
    report = ll.collect(repo, mem)
    assert report["lanes"]["memory"]["due"] == 1
    assert report["due_total"] == 1


def test_collect_survives_a_repo_with_no_optional_lanes(tmp_path):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_a.md")
    repo = tmp_path / "repo"; repo.mkdir()
    assert ll.collect(repo, mem)["total"] == 1


def test_main_rejects_a_missing_memory_dir(tmp_path, capsys):
    assert ll.main(["--memory-dir", str(tmp_path / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_main_prints_a_lane_table(tmp_path, capsys):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_a.md", occ=2)
    ll.main(["--memory-dir", str(mem), "--repo-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "memory" in out and "TOTAL" in out


def test_due_flag_lists_only_due_records(tmp_path, capsys):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_due.md", occ=4)
    write_rule(mem, "feedback_quiet.md", occ=1)
    ll.main(["--memory-dir", str(mem), "--repo-root", str(tmp_path), "--due"])
    out = capsys.readouterr().out
    assert "feedback_due.md" in out and "feedback_quiet.md" not in out


def test_json_output_is_parseable(tmp_path, capsys):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_a.md")
    ll.main(["--memory-dir", str(mem), "--repo-root", str(tmp_path), "--json"])
    assert json.loads(capsys.readouterr().out)["total"] == 1


# ---------------------------------------------------------------- the live repo

def test_the_live_ledger_reads_every_lane():
    mem = Path.home() / ".claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory"
    if not mem.is_dir():
        pytest.skip("live memory tier not present")
    report = ll.collect(REPO_ROOT, mem)
    assert set(report["lanes"]) >= {"memory", "friction", "lessons-2"}
    assert report["total"] > 500


# ---------------------------------------------------------------- mutation-driven gaps

@pytest.mark.parametrize("raw,expected", [
    ("Yes", ll.DISPOSITION_DONE), ("promoted 2026-08", ll.DISPOSITION_DONE),
    ("No", ll.DISPOSITION_OPEN), ("partial -- half", ll.DISPOSITION_OPEN),
    ("", ll.DISPOSITION_OPEN),
])
def test_disposition_mapping(raw, expected):
    assert ll._disposition(raw) == expected


def test_a_section_1_row_with_too_few_cells_is_skipped(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 1\n| 1 | only two |\n", encoding="utf-8")
    assert ll.read_lessons(p) == []


def test_a_section_2_row_with_too_few_cells_is_skipped(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 2\n| a | b | c |\n", encoding="utf-8")
    assert ll.read_lessons(p) == []


def test_a_section_1_header_row_is_not_a_record(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 1\n| # | Pattern | Rule | Date |\n"
                 "| 1 | a | b | 2026-01-01 |\n", encoding="utf-8")
    recs = ll.read_lessons(p)
    assert len(recs) == 1 and recs[0]["what"] == "a"


def test_section_1_rows_stop_at_the_section_2_heading(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 1\n| 1 | one | r | 2026-01-01 |\n"
                 "## Section 2\n| Pattern | Rule | Occurrences | Promoted | Date |\n"
                 "| two | r | 2 | No | 2026-02-02 |\n", encoding="utf-8")
    recs = ll.read_lessons(p)
    assert [r["lane"] for r in recs] == ["lessons-1", "lessons-2"]


def test_a_separator_inside_a_section_is_skipped(tmp_path):
    p = tmp_path / "lessons.md"
    p.write_text("## Section 1\n|---|---|---|---|\n| 1 | a | b | 2026-01-01 |\n",
                 encoding="utf-8")
    assert len(ll.read_lessons(p)) == 1


def test_prose_between_antipattern_entries_does_not_overwrite_evidence(tmp_path):
    p = tmp_path / "anti-pattern-tracker.md"
    p.write_text("## Active\n### Thing\n**Status:** Persistent\nsome prose\n"
                 "### Other\n**Status:** New\n", encoding="utf-8")
    recs = ll.read_antipatterns(p)
    assert [r["evidence"] for r in recs] == ["Persistent", "New"]


def test_a_status_line_before_any_heading_is_ignored(tmp_path):
    p = tmp_path / "anti-pattern-tracker.md"
    p.write_text("**Status:** stray\n## Active\n### Thing\n**Status:** Persistent\n",
                 encoding="utf-8")
    assert [r["evidence"] for r in ll.read_antipatterns(p)] == ["Persistent"]


def test_the_due_boundary_is_two_not_one(tmp_path):
    """Kills the comparison mutant. A rule at exactly 1 fire has not demonstrated
    recurrence and must not enter the backlog; a rule at exactly 2 has."""
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_one.md", occ=1)
    assert ll.collect(tmp_path, mem)["due_total"] == 0
    write_rule(mem, "feedback_two.md", occ=2)
    assert ll.collect(tmp_path, mem)["due_total"] == 1


def test_the_lane_table_prints_its_header_row(tmp_path, capsys):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_a.md", occ=2)
    ll.main(["--memory-dir", str(mem), "--repo-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "lane" in out and "records" in out and "due" in out, (
        "a column of numbers with no header is not a report anyone can read"
    )


def test_main_returns_0_on_a_successful_read(tmp_path):
    mem = tmp_path / "mem"; mem.mkdir()
    write_rule(mem, "feedback_a.md")
    assert ll.main(["--memory-dir", str(mem), "--repo-root", str(tmp_path)]) == 0
