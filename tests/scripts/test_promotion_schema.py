"""A promotion must leave a record, and exit 2 must cost a detector.

These tests exist to prove the guard can FAIL. The two that matter most are the
never-waived pair: a grandfathered file still cannot take exit 2 without a detector
signature (I3), and a dispositioned file still cannot carry a gate that names no
number (I4). If either could be waived, the escape hatch this module closes reopens.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import promotion_schema as ps  # noqa: E402


def write_rule(tmp_path: Path, name: str, **keys) -> Path:
    body = ["---", "name: " + name.removesuffix(".md"), "metadata:"]
    for k, v in keys.items():
        body.append(f"  {k}: {v}")
    body += ["---", "", "Rule body."]
    p = tmp_path / name
    p.write_text("\n".join(body), encoding="utf-8")
    return p


NUMERIC_GATE = '"3rd fire -> promote to a hook"'
NOOP_GATE = '"No structural gate set -- reopen on the next dated fire."'


# ---------------------------------------------------------------- I1 promoted_date

def test_promoted_without_a_date_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", exit_path="exit1",
               reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert not report["ok"]
    assert any("I1" in v and "promoted_date is missing" in v for v in report["violations"])


def test_a_malformed_promoted_date_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="Aug 25 2026",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I1" in v and "not YYYY-MM-DD" in v for v in report["violations"])


def test_partial_counts_as_a_promotion_and_needs_a_record(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="partial -- half landed",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I1" in v for v in report["violations"]), (
        "partial is a promotion event that happened on a date; if it is exempt, "
        "the half that landed is lost and gets re-derived from scratch later"
    )


def test_an_unpromoted_rule_needs_no_record(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    assert ps.scan(tmp_path, {})["ok"]


# ---------------------------------------------------------------- I2 exit_path

def test_promoted_without_an_exit_path_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25")
    report = ps.scan(tmp_path, {})
    assert any("I2" in v and "exit_path is missing" in v for v in report["violations"])


def test_an_unrecognised_exit_path_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit3", reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I2" in v and "exit3" in v for v in report["violations"])


# ---------------------------------------------------------------- I3 the detector price

def test_exit2_without_a_detector_signature_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit2", reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I3" in v for v in report["violations"])


def test_exit2_with_a_detector_signature_is_clean(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit2", detector_signature='"a ratio with no denominator"',
               reopen_gate=NUMERIC_GATE)
    assert ps.scan(tmp_path, {})["ok"]


def test_an_empty_detector_signature_does_not_satisfy_the_price(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit2", detector_signature='""', reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I3" in v for v in report["violations"])


def test_grandfathering_CANNOT_waive_the_exit2_detector(tmp_path):
    """The one that keeps the escape hatch shut."""
    write_rule(tmp_path, "feedback_a.md", promoted="yes", exit_path="exit2",
               reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {"feedback_a.md": "legacy promotion"})
    assert any("I3" in v for v in report["violations"]), (
        "if the allowlist can waive I3, exit 2 becomes free again and the whole "
        "mechanism collapses"
    )


# ---------------------------------------------------------------- I4 numeric gates

def test_a_dispositioned_rule_with_a_no_op_gate_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit1", reopen_gate=NOOP_GATE)
    report = ps.scan(tmp_path, {})
    assert any("I4" in v for v in report["violations"])


def test_grandfathering_CANNOT_waive_the_numeric_gate(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", exit_path="exit1",
               reopen_gate=NOOP_GATE)
    report = ps.scan(tmp_path, {"feedback_a.md": "legacy promotion"})
    assert any("I4" in v for v in report["violations"])


def test_an_undispositioned_rule_is_not_held_to_the_numeric_gate(tmp_path):
    """I4 governs promotions from now on; it does not retro-fail the untouched backlog."""
    write_rule(tmp_path, "feedback_a.md", promoted="no", reopen_gate=NOOP_GATE)
    assert ps.scan(tmp_path, {})["ok"]


@pytest.mark.parametrize("gate,expected", [
    ("3rd fire -> promote", True),
    ("2nd occurrence", True),
    ("1 more fire and it goes to a hook", True),
    ("reopen on the next dated fire", False),
    ("No structural gate set", False),
    ("", False),
])
def test_gate_names_a_number(gate, expected):
    assert ps.gate_names_a_number(gate) is expected


# ---------------------------------------------------------------- I5 allowlist discipline

def test_a_grandfather_entry_with_an_empty_reason_is_a_violation(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    report = ps.scan(tmp_path, {"feedback_a.md": "   "})
    assert any("I5" in v for v in report["violations"])


def test_grandfathering_waives_the_missing_record(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes")
    assert ps.scan(tmp_path, {"feedback_a.md": "promoted before the fields existed"})["ok"]


# ---------------------------------------------------------------- scan / parse / CLI

def test_an_empty_scan_is_an_error_not_a_clean_bill(tmp_path):
    with pytest.raises(ValueError):
        ps.scan(tmp_path, {})


def test_parse_frontmatter_reads_nested_and_quoted_values():
    fm = ps.parse_frontmatter(
        '---\nname: x\nmetadata:\n  promoted: "yes -- hook tier, 2026-08-25: wired"\n'
        '  exit_path: exit1\n---\nbody\n'
    )
    assert fm["exit_path"] == "exit1"
    assert fm["promoted"].startswith("yes")


def test_parse_frontmatter_returns_empty_without_a_block():
    assert ps.parse_frontmatter("no frontmatter here") == {}


@pytest.mark.parametrize("value,expected", [
    ("yes", True), ("yes -- hook", True), ("partial -- half", True),
    ("no", False), ("", False),
])
def test_is_promoted_value(value, expected):
    assert ps.is_promoted_value(value) is expected


def test_report_counts_dispositions_by_exit_path(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    write_rule(tmp_path, "feedback_b.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="terminal", reopen_gate=NUMERIC_GATE)
    report = ps.scan(tmp_path, {})
    assert report["by_exit_path"] == {"exit1": 1, "terminal": 1}
    assert report["promoted_with_date"] == 2


def test_main_blocks_with_exit_2_on_a_violation(tmp_path, capsys):
    write_rule(tmp_path, "feedback_a.md", promoted="yes")
    gf = tmp_path / "gf.json"
    gf.write_text("{}", encoding="utf-8")
    rc = ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf)])
    assert rc == 2, "an exit-0 warning is not a gate; Claude Code never surfaces hook stderr"
    assert "VIOLATION" in capsys.readouterr().out


def test_main_returns_0_when_clean(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    gf = tmp_path / "gf.json"
    gf.write_text("{}", encoding="utf-8")
    assert ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf)]) == 0


def test_main_rejects_a_missing_memory_dir(tmp_path):
    assert ps.main(["--memory-dir", str(tmp_path / "nope")]) == 1


def test_main_rejects_a_malformed_grandfather_file(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    gf = tmp_path / "gf.json"
    gf.write_text("[1,2,3]", encoding="utf-8")
    assert ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf)]) == 1


def test_json_output_is_parseable(tmp_path, capsys):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    gf = tmp_path / "gf.json"
    gf.write_text("{}", encoding="utf-8")
    ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf), "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------- the live corpus

def test_the_live_grandfather_file_has_no_empty_reasons():
    gf = ps.load_grandfather(ps.DEFAULT_GRANDFATHER)
    assert gf, "the grandfather file should list the legacy promotions, not be empty"
    empty = [k for k, v in gf.items() if not str(v).strip()]
    assert not empty, f"allowlist entries without a written reason: {empty}"


# ---------------------------------------------------------------- parser edges
# Added after the first mutation run left 18 mutants alive, most of them inside
# parse_frontmatter and the scan counters: the parser was accepting whatever it was
# given and the counters were never checked against a MIXED corpus, so forcing any
# of their branches changed nothing a test could see.

def test_a_block_not_at_the_top_of_the_file_is_not_frontmatter(tmp_path):
    """Without the startswith guard this parses a mid-file --- fence as frontmatter."""
    assert ps.parse_frontmatter("prose first\n---\npromoted: yes\n---\n") == {}


def test_an_unterminated_frontmatter_block_yields_nothing():
    assert ps.parse_frontmatter("---\npromoted: yes\nnever closed") == {}


def test_comment_lines_do_not_become_keys():
    fm = ps.parse_frontmatter("---\n# promoted: yes\nexit_path: exit1\n---\n")
    assert "promoted" not in fm
    assert fm["exit_path"] == "exit1"


def test_a_non_key_value_line_is_skipped_without_crashing():
    """Kills the DROP_CONTINUE after `if not m`, which otherwise dereferences None."""
    fm = ps.parse_frontmatter("---\nexit_path: exit1\n  - a bare list item\nname: x\n---\n")
    assert fm["exit_path"] == "exit1"
    assert fm["name"] == "x"


def test_load_grandfather_returns_empty_when_the_file_is_absent(tmp_path):
    assert ps.load_grandfather(tmp_path / "absent.json") == {}


# ---------------------------------------------------------------- counters on a MIXED corpus

def test_counters_discriminate_promoted_from_unpromoted(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    write_rule(tmp_path, "feedback_b.md", promoted="no")
    write_rule(tmp_path, "feedback_c.md", promoted="no")
    report = ps.scan(tmp_path, {})
    assert report["scanned"] == 3
    assert report["promoted_total"] == 1, "an unpromoted rule must not count as a promotion"


def test_promoted_with_date_does_not_count_an_undated_promotion(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    write_rule(tmp_path, "feedback_b.md", promoted="yes")
    report = ps.scan(tmp_path, {"feedback_b.md": "legacy, no date recoverable"})
    assert report["promoted_total"] == 2
    assert report["promoted_with_date"] == 1, (
        "this counter IS the drain-rate instrument; if it counts undated promotions "
        "the metric reports progress that did not happen"
    )


def test_with_exit_path_does_not_count_an_undispositioned_rule(tmp_path):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="terminal", reopen_gate=NUMERIC_GATE)
    write_rule(tmp_path, "feedback_b.md", promoted="no")
    report = ps.scan(tmp_path, {})
    assert report["with_exit_path"] == 1
    assert report["by_exit_path"] == {"terminal": 1}


# ---------------------------------------------------------------- diagnostics must be real
# Second mutation round: the CLI's error messages were unasserted, so every diagnostic
# print could be deleted with the suite green. A checker that blocks without saying why
# is a checker nobody can act on.

def test_a_frontmatter_block_not_at_the_top_is_not_parsed_even_when_it_holds_keys():
    """Without the startswith guard, `promoted: yes` below prose is read as frontmatter."""
    assert ps.parse_frontmatter("abc\npromoted: yes\n---\nbody") == {}


def test_a_missing_memory_dir_says_so(tmp_path, capsys):
    rc = ps.main(["--memory-dir", str(tmp_path / "nope")])
    assert rc == 1
    assert "not a directory" in capsys.readouterr().err


def test_a_malformed_grandfather_file_reports_the_reason(tmp_path, capsys):
    write_rule(tmp_path, "feedback_a.md", promoted="no")
    gf = tmp_path / "gf.json"
    gf.write_text("[1,2,3]", encoding="utf-8")
    rc = ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf)])
    assert rc == 1
    assert "file -> reason" in capsys.readouterr().err


def test_the_summary_line_reports_the_drain_instrument(tmp_path, capsys):
    write_rule(tmp_path, "feedback_a.md", promoted="yes", promoted_date="2026-08-25",
               exit_path="exit1", reopen_gate=NUMERIC_GATE)
    gf = tmp_path / "gf.json"
    gf.write_text("{}", encoding="utf-8")
    ps.main(["--memory-dir", str(tmp_path), "--grandfather", str(gf)])
    out = capsys.readouterr().out
    assert "scanned 1 rules" in out
    assert "promoted 1 (1 dated)" in out
    assert "dispositioned 1" in out
