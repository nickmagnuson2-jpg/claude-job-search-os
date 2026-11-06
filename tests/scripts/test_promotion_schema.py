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


# --- I6: the `promoted` token vocabulary (2026-09-21) --------------------------------

def test_I6_rejects_a_token_outside_the_vocabulary():
    """MEASURED on the live tier: 3 files opened with 'n/a' or 'SUPERSEDED'. Because
    scan_promotion_candidates deliberately fails OPEN on an unrecognised token -- it
    assumes a tier name -- each of those read as a COMPLETED promotion and left the
    backlog silently. The vocabulary has to be gated somewhere, and it cannot be there."""
    v = ps.check_file(
        "f.md", {"promoted": "n/a -- reference"}, grandfathered=False)
    assert any("I6" in x for x in v), v
    v = ps.check_file(
        "f.md", {"promoted": "SUPERSEDED 2026-08-21 -- the gate was deleted"},
        grandfathered=False)
    assert any("I6" in x for x in v), v


@pytest.mark.parametrize("value", ["no", "yes", "partial", "skill", "hook",
                                   "principle", "hard-rule",
                                   "yes -- hook tier, 2026-08-19",
                                   "partial -- open_draft.py reply mode (NOT proposed)"])
def test_I6_accepts_every_documented_token(value):
    """A qualifier after the token is the GOOD shape and must never be what fails.
    Tier names are included because scan_promotion_candidates reads a bare tier as a
    promotion, and that behaviour is pinned by its own tests."""
    v = ps.check_file("f.md", {"promoted": value}, grandfathered=False)
    assert not any("I6" in x for x in v), v


def test_I6_does_not_read_n_slash_a_as_the_tier_n():
    """Splitting the token on '/' would turn 'n/a' into 'n', and a one-letter token that
    matches nothing is a different error message than the one a reader needs."""
    assert ps.promoted_token("n/a -- reference") == "n/a"


def test_I6_keeps_a_hyphenated_tier_whole():
    assert ps.promoted_token("hard-rule") == "hard-rule"


def test_I6_is_silent_on_an_absent_promoted_key():
    v = ps.check_file("f.md", {}, grandfathered=False)
    assert not any("I6" in x for x in v), v


def test_promoted_token_returns_empty_when_there_is_no_leading_word():
    """The no-match branch. Without this the regex could stop matching entirely and
    every I5 check would silently pass, because '' is treated as 'no token declared'."""
    assert ps.promoted_token("") == ""
    assert ps.promoted_token("123 -- numeric") == ""
    assert ps.promoted_token("   ") == ""
    assert ps.promoted_token(None) == ""


# --- gate_names_a_number: the shapes people actually write (2026-09-21) --------------
# The original pattern was wrong in three ways at once and every one of them REJECTED a
# well-formed gate. That matters more than a missed match: the fix for an I4 violation is
# to edit the rule file, so a detector that rejects good gates makes the corpus worse when
# it is obeyed.

@pytest.mark.parametrize("gate", [
    "2nd time a correction is written into a data file",   # the corpus's own convention
    "3rd fire",
    "7th fire",                    # the literal list stopped at 6th
    "32nd fire",                   # a rule in this corpus sits at 31 occurrences
    "10th instance",
    "reopen at 3 fires",           # plural: (?:fire)\b could not match "fires"
    "2 more fires",
    "TRIPPED 2026-08-14 by fire 6",   # number AFTER the noun
    "GATE MET 2026-08-14: 2 fires in one session",
    "the third fire",
    "2nd catch",
    "3rd instance found OUTSIDE the scorer subsystem",
])
def test_gate_number_recognised(gate):
    assert ps.gate_names_a_number(gate) is True, gate


@pytest.mark.parametrize("gate", [
    "reopen on the next dated fire",      # the schema's own named anti-pattern
    "No structural gate set",
    "reopen when it happens again",
    "reopen if this recurs",
    "",
])
def test_gate_without_a_number_is_rejected(gate):
    assert ps.gate_names_a_number(gate) is False, gate


def test_gate_pattern_recognises_everything_the_old_one_did():
    """DIFFERENTIAL. A first attempt at this pattern dropped the standalone-ordinal
    branch, which the corpus uses constantly ('2nd time X happens'), and silently
    regressed 90+ files from passing to violating. Widening a matcher is not automatically
    safe: check the old shapes still match before trusting the new ones."""
    import re
    old = re.compile(
        r"\b(\d+\s*(?:more\s+)?(?:dated\s+)?(?:fire|occurrence)"
        r"|2nd|3rd|4th|5th|6th|second|third|fourth|fifth)\b", re.IGNORECASE)
    for gate in ("2nd time", "3rd", "4th", "5th", "6th", "second", "fifth",
                 "2 fires", "3 dated occurrences", "reopen on the 2nd"):
        if old.search(gate):
            assert ps.gate_names_a_number(gate) is True, f"regressed: {gate!r}"


# --- YAML block scalars in frontmatter (2026-09-21) ---------------------------------

def test_block_scalar_value_is_read_not_the_marker():
    """`reopen_gate: >-` puts the value on the FOLLOWING lines. The flat scrape stored
    the marker ">-" as the value, so one corpus file whose gate reads "4th fire, OR any
    fix shipped with a test that was never run against the reverted code" was reported as
    naming no number for as long as the check existed. A parser that returns a marker
    instead of a value invents a defect."""
    text = (
        "---\n"
        "name: x\n"
        "metadata:\n"
        "  reopen_gate: >-\n"
        "    4th fire, OR any fix shipped with a test that was never run\n"
        "    against the reverted code.\n"
        "  last_cited: 2026-08-25\n"
        "---\n\nbody\n")
    fm = ps.parse_frontmatter(text)
    assert fm["reopen_gate"].startswith("4th fire")
    assert "against the reverted code" in fm["reopen_gate"]
    assert ps.gate_names_a_number(fm["reopen_gate"]) is True
    # the key AFTER the block must still parse -- the folded lines must not swallow it
    assert fm["last_cited"] == "2026-08-25"


def test_literal_block_scalar_is_also_read():
    text = ("---\nname: x\nmetadata:\n  reopen_gate: |\n    3rd fire\n"
            "  promoted: no\n---\n\nbody\n")
    fm = ps.parse_frontmatter(text)
    assert "3rd fire" in fm["reopen_gate"]
    assert fm["promoted"] == "no"


def test_a_plain_value_is_unaffected_by_block_scalar_support():
    text = "---\nname: x\nmetadata:\n  reopen_gate: 3rd fire\n  promoted: no\n---\n\nb\n"
    fm = ps.parse_frontmatter(text)
    assert fm["reopen_gate"] == "3rd fire" and fm["promoted"] == "no"


def test_a_value_that_merely_starts_with_gt_is_not_a_block_marker():
    """'>= 3 fires' is a value, not a folding marker. Only a BARE marker folds."""
    text = "---\nname: x\nmetadata:\n  reopen_gate: '>= 3 fires'\n  promoted: no\n---\n\nb\n"
    fm = ps.parse_frontmatter(text)
    assert fm["reopen_gate"] == ">= 3 fires"
    assert fm["promoted"] == "no"


# --- I7: exit2 MEANS in MEMORY.md (2026-09-21) --------------------------------------

def test_I7_exit2_rule_absent_from_memory_md_is_a_violation():
    """exit2 is defined at the top of promotion_schema as 'a principle no gate can
    express, PROMOTED INTO ALWAYS-LOADED MEMORY.md'. I3 charges a detector as the price
    of admission; nothing checked that the rule was admitted. Measured 2026-09-21: 50
    rules claim exit2, 2 are in MEMORY.md."""
    v = ps.check_file("feedback_x.md",
                      {"promoted": "yes", "promoted_date": "2026-09-01",
                       "exit_path": "exit2", "detector_signature": "sig",
                       "reopen_gate": "3rd fire"},
                      grandfathered=False, in_memory_md=False)
    assert any("I7" in x for x in v), v


def test_I7_exit2_rule_present_in_memory_md_is_clean():
    v = ps.check_file("feedback_x.md",
                      {"promoted": "yes", "promoted_date": "2026-09-01",
                       "exit_path": "exit2", "detector_signature": "sig",
                       "reopen_gate": "3rd fire"},
                      grandfathered=False, in_memory_md=True)
    assert not any("I7" in x for x in v), v


def test_I7_cannot_run_without_sight_of_memory_md():
    """Unknowable is not a pass and not a failure. A caller pointed at a corpus with no
    MEMORY.md simply does not run this check."""
    v = ps.check_file("feedback_x.md",
                      {"promoted": "yes", "promoted_date": "2026-09-01",
                       "exit_path": "exit2", "detector_signature": "sig",
                       "reopen_gate": "3rd fire"},
                      grandfathered=False, in_memory_md=None)
    assert not any("I7" in x for x in v), v


def test_I7_does_not_fire_for_exit1_or_terminal():
    """Only exit2 claims the always-loaded channel."""
    for ep in ("exit1", "terminal"):
        v = ps.check_file("feedback_x.md",
                          {"promoted": "yes", "promoted_date": "2026-09-01",
                           "exit_path": ep, "reopen_gate": "3rd fire"},
                          grandfathered=False, in_memory_md=False)
        assert not any("I7" in x for x in v), (ep, v)


def test_scan_reads_memory_md_and_reports_I7(tmp_path):
    """End to end: scan() must read the channel once and pass the answer down."""
    mem = tmp_path / "memory"; mem.mkdir()
    (mem / "MEMORY.md").write_text("## Critical Context\n\n- nothing here\n", encoding="utf-8")
    (mem / "feedback_orphan.md").write_text(
        "---\nname: feedback_orphan\nmetadata:\n  occurrences: 2\n  promoted: yes\n"
        "  promoted_date: 2026-09-01\n  exit_path: exit2\n  detector_signature: sig\n"
        "  reopen_gate: \"3rd fire\"\n  last_cited: 2026-09-01\n---\n\nbody\n",
        encoding="utf-8")
    rep = ps.scan(mem, {})
    assert any("I7" in v for v in rep["violations"]), rep["violations"]


def test_scan_without_a_memory_md_does_not_invent_I7(tmp_path):
    mem = tmp_path / "memory"; mem.mkdir()
    (mem / "feedback_orphan.md").write_text(
        "---\nname: feedback_orphan\nmetadata:\n  occurrences: 2\n  promoted: yes\n"
        "  promoted_date: 2026-09-01\n  exit_path: exit2\n  detector_signature: sig\n"
        "  reopen_gate: \"3rd fire\"\n  last_cited: 2026-09-01\n---\n\nbody\n",
        encoding="utf-8")
    rep = ps.scan(mem, {})
    assert not any("I7" in v for v in rep["violations"]), rep["violations"]


# ---------------------------------------------------------------- I8 a completed exit1 is proven
#
# memory-hygiene v3 step 1: an exit1 record must name a concrete gate and carry evidence
# that the gate blocks its own origin. Every name below is a placeholder.

GATE = "tools/check_example.py"
TESTS = "tests/scripts/test_check_example.py"
EVIDENCE = f"{TESTS}::test_blocks_its_origin"

EVIDENCE_FILE = """\
import pytest
import check_example
import check_example as ce


def test_blocks_its_origin():
    assert check_example.check('origin') == 2


def test_via_alias():
    assert ce.check('origin') == 2


def test_runs_the_hook_as_a_subprocess():
    assert run(['tools/check_example.py', 'origin']) == 2


def test_asserts_nothing():
    check_example.check('origin')


def test_never_reaches_the_gate():
    assert 1 + 1 == 2


def test_raises_counts_as_an_assertion():
    with pytest.raises(SystemExit):
        check_example.check('origin')


@pytest.mark.parametrize("x", ["origin"])
def test_param(x):
    assert check_example.check(x) == 2


class TestCases:
    def test_in_a_class(self):
        assert check_example.check('origin') == 2


async def test_async_case():
    assert check_example.check('origin') == 2
"""


@pytest.fixture()
def repo(tmp_path):
    """A throwaway repo holding gates of each kind and one evidence file."""
    root = tmp_path / "repo"
    (root / "tools" / "hooks").mkdir(parents=True)
    (root / "tests" / "scripts").mkdir(parents=True)
    (root / GATE).write_text("def check(x):\n    return 2\n", encoding="utf-8")
    (root / TESTS).write_text(EVIDENCE_FILE, encoding="utf-8")
    hook = root / "tools/hooks/pre-example"
    hook.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    hook.chmod(0o755)
    (root / "tools/hooks/not-executable").write_text("x\n", encoding="utf-8")
    (root / "tools/example-allow.json").write_text("{}\n", encoding="utf-8")
    (root / "framework").mkdir()
    (root / "framework/example.md").write_text("prose\n", encoding="utf-8")
    return root


def exit1(**extra):
    fm = {"promoted": "yes", "exit_path": "exit1", "promoted_date": "2026-11-06",
          "reopen_gate": "3rd fire"}
    fm.update(extra)
    return fm


def i8(fm, repo_root):
    return [v for v in ps.check_file("feedback_x.md", fm, grandfathered=False,
                                     repo_root=repo_root) if ": I8 " in v]


def test_I8_a_completed_exit1_without_gate_or_evidence_is_two_violations(repo):
    v = i8(exit1(), repo)
    assert len(v) == 2
    assert any("exit1_gate is missing" in x for x in v)
    assert any("exit1_evidence is missing" in x for x in v)


def test_I8_a_proven_exit1_is_clean(repo):
    assert ps.check_file("feedback_x.md", exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE),
                         grandfathered=False, repo_root=repo) == []


def test_I8_a_gate_with_a_suffix_and_prose_resolves_to_its_file(repo):
    gate = f"`{GATE}::rule_B11` blocks the origin shape"
    assert i8(exit1(exit1_gate=gate, exit1_evidence=f"`{EVIDENCE}`"), repo) == []


@pytest.mark.parametrize("gate", ["framework/example.md", "notes.TXT",
                                  "tools/example-allow.json"])
def test_I8_a_document_or_data_file_is_not_a_gate(repo, gate):
    # Exactly one violation: a gate that already failed is not ALSO reported as
    # unreached by the evidence, which would double-count one defect.
    v = i8(exit1(exit1_gate=gate, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "is not executable code" in v[0]


def test_I8_an_executable_extensionless_hook_is_a_gate(repo):
    ev = f"{TESTS}::test_runs_the_hook_as_a_subprocess"
    (repo / TESTS).write_text(EVIDENCE_FILE.replace("tools/check_example.py",
                                                    "tools/hooks/pre-example"), "utf-8")
    assert i8(exit1(exit1_gate="tools/hooks/pre-example", exit1_evidence=ev), repo) == []


def test_I8_an_extensionless_file_that_is_not_executable_is_not_a_gate(repo):
    v = i8(exit1(exit1_gate="tools/hooks/not-executable", exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "is not executable, so nothing runs it" in v[0]


def test_I8_a_gate_that_does_not_exist_is_a_violation(repo):
    v = i8(exit1(exit1_gate="tools/check_missing.py", exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "does not resolve to a file" in v[0]


@pytest.mark.parametrize("gate", ["/etc/hosts.py", "../repo/tools/check_example.py",
                                  "~/tools/check_example.py"])
def test_I8_a_gate_outside_the_repo_is_a_violation(repo, gate):
    v = i8(exit1(exit1_gate=gate, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "not a repo-relative path" in v[0]


@pytest.mark.parametrize("evidence", [
    "ran a replay, all blocked",
    TESTS,
    "tools/check_example.py::test_blocks_its_origin",
    f"{TESTS}::helper",
    "tests/../x/test_check_example.py::test_blocks_its_origin",
])
def test_I8_evidence_that_is_not_a_test_node_is_a_violation(repo, evidence):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=evidence), repo)
    assert len(v) == 1 and "is not a test node id" in v[0]


@pytest.mark.parametrize("evidence", [
    f"{TESTS}::test_param[origin]",
    f"{TESTS}::TestCases::test_in_a_class",
    f"{TESTS}::test_async_case",
    f"{TESTS}::test_via_alias",
    f"{TESTS}::test_runs_the_hook_as_a_subprocess",
    f"{TESTS}::test_raises_counts_as_an_assertion",
])
def test_I8_every_collectable_node_shape_that_reaches_the_gate_passes(repo, evidence):
    assert i8(exit1(exit1_gate=GATE, exit1_evidence=evidence), repo) == []


def test_I8_a_class_qualified_node_must_name_the_real_class(repo):
    """codex F2: the class segment was matched and then ignored, so a renamed class
    left a stale node reading as proven."""
    v = i8(exit1(exit1_gate=GATE,
                 exit1_evidence=f"{TESTS}::Nonexistent::test_in_a_class"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


def test_I8_a_method_is_not_found_at_module_level(repo):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::test_in_a_class"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


def test_I8_a_parameter_id_on_an_unparametrized_test_is_a_violation(repo):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE + "[case-1]"), repo)
    assert len(v) == 1 and "is not a parameter id this test can be shown to generate" in v[0]


def test_I8_evidence_that_asserts_nothing_is_a_violation(repo):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::test_asserts_nothing"), repo)
    assert len(v) == 1 and "asserts nothing" in v[0]


def test_I8_evidence_whose_own_body_never_reaches_the_gate_is_a_violation(repo):
    """codex F1 / grok F1: the module imports the gate and a sibling test uses it, and
    neither is evidence about THIS node."""
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::test_never_reaches_the_gate"),
           repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_test_of_another_module_is_not_evidence_for_this_gate(repo):
    (repo / "tools/check_other.py").write_text("x = 1\n", encoding="utf-8")
    v = i8(exit1(exit1_gate="tools/check_other.py", exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate 'tools/check_other.py'" in v[0]


def test_I8_a_gate_name_that_only_prefixes_the_imported_module_does_not_count(repo):
    # "check_ex" is a prefix of "check_example", which is what the test imports.
    (repo / "tools/check_ex.py").write_text("x = 1\n", encoding="utf-8")
    v = i8(exit1(exit1_gate="tools/check_ex.py", exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_from_import_names_count_as_reaching_the_gate(repo):
    (repo / TESTS).write_text(
        "from tools.check_example import check as chk\n\n"
        "def test_blocks_its_origin():\n    assert chk('origin') == 2\n", "utf-8")
    assert i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo) == []


def test_I8_a_dotted_import_binds_its_top_package(repo):
    (repo / TESTS).write_text(
        "import tools.check_example\n\n"
        "def test_blocks_its_origin():\n"
        "    assert tools.check_example.check('origin') == 2\n", "utf-8")
    assert i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo) == []


def test_I8_an_unrelated_import_alias_does_not_reach_the_gate(repo):
    (repo / TESTS).write_text(
        "import other_module as check_example_like\nimport json as ce\n\n"
        "def test_blocks_its_origin():\n    assert ce.dumps(1)\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_an_import_of_a_module_whose_stem_differs_is_not_bound(repo):
    (repo / TESTS).write_text(
        "from pkg.other import check_example\n\n"
        "def test_blocks_its_origin():\n    assert check_example('origin') == 2\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_string_naming_another_file_does_not_reach_the_gate(repo):
    (repo / TESTS).write_text(
        "def test_blocks_its_origin():\n    assert run(['tools/other.py']) == 2\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_evidence_file_that_does_not_parse_is_a_violation(repo):
    (repo / TESTS).write_text("def test_blocks_its_origin(:\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "does not parse" in v[0]


def test_I8_an_evidence_file_that_does_not_exist_is_a_violation(repo):
    v = i8(exit1(exit1_gate=GATE,
                 exit1_evidence="tests/scripts/test_gone.py::test_blocks_its_origin"), repo)
    assert len(v) == 1 and "does not exist" in v[0]


def test_I8_an_evidence_test_that_is_not_defined_is_a_violation(repo):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::test_never_written"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


def test_I8_partial_is_held_too(repo):
    assert len(i8(exit1(promoted="partial -- one rule of a family"), repo)) == 2


def test_I8_grandfathering_CANNOT_waive_it(repo):
    v = ps.check_file("feedback_x.md", exit1(), grandfathered=True, repo_root=repo)
    assert sum(": I8 " in x for x in v) == 2


def test_I8_a_proposed_exit1_is_not_a_completed_one(repo):
    assert i8(exit1(promoted="no"), repo) == []


@pytest.mark.parametrize("ep", ["exit2", "terminal"])
def test_I8_does_not_fire_off_exit1(repo, ep):
    assert i8(exit1(exit_path=ep), repo) == []


def test_I8_without_a_repo_is_unverified_never_proven():
    """codex F3: with no repo the node cannot be resolved, and unknowable is not a pass."""
    assert len(i8(exit1(), None)) == 2
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), None)
    assert len(v) == 1 and "unverified, not proven" in v[0]
    assert len(i8(exit1(exit1_gate=GATE, exit1_evidence="a replay"), None)) == 1
    assert len(i8(exit1(exit1_gate="framework/x.md", exit1_evidence="a replay"), None)) == 2


def test_I8_check_file_defaults_to_the_real_repo():
    v = ps.check_file("feedback_x.md", exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE),
                      grandfathered=False)
    assert any("does not resolve to a file" in x for x in v)


def test_I8_a_real_gate_in_this_repo_resolves():
    """The same check against the real tree. If the module or this test moves, the
    exemplar a reader copies from breaks here first."""
    fm = exit1(exit1_gate="tools/promotion_schema.py",
               exit1_evidence="tests/scripts/test_promotion_schema.py::"
                              "test_I8_the_exemplar_node_reaches_its_gate")
    assert i8(fm, REPO_ROOT) == []


def test_I8_the_exemplar_node_reaches_its_gate():
    """Evidence for the exemplar above: this body names the module it tests."""
    import promotion_schema
    assert promotion_schema.check_file(
        "feedback_x.md", exit1(), grandfathered=False, repo_root=None)


def test_scan_reports_exit1_reach_and_the_summary_names_its_denominator(tmp_path, repo, capsys):
    mem = tmp_path / "mem"
    mem.mkdir()
    write_rule(mem, "feedback_proven.md", **exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE))
    write_rule(mem, "feedback_unproven.md", **exit1())
    write_rule(mem, "feedback_proposed.md", **exit1(promoted="no"))
    write_rule(mem, "feedback_other.md", **exit1(exit_path="terminal"))
    report = ps.scan(mem, {}, repo_root=repo)
    assert (report["exit1_completed"], report["exit1_proven"]) == (2, 1)
    assert sum(": I8 " in v for v in report["violations"]) == 2
    assert all(v.startswith("feedback_unproven.md") for v in report["violations"])

    gf = tmp_path / "gf.json"
    gf.write_text("{}", encoding="utf-8")
    # main() resolves against the real repo, where the placeholder gate does not exist,
    # so the proven record fails there: the summary line must count it as unproven.
    assert ps.main(["--memory-dir", str(mem), "--grandfather", str(gf)]) == 2
    assert "exit1 proven 0 of 2 completed" in capsys.readouterr().out


def test_I8_a_shell_gate_is_not_reached_by_importing_a_python_module_of_its_stem(repo):
    """Only a .py gate can be imported. A test that imports a Python module sharing the
    stem of a shell gate tests that module, not the script."""
    (repo / "tools/check_example.sh").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    v = i8(exit1(exit1_gate="tools/check_example.sh", exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]



# ---- round 2 of /verify (codex F1-F5, grok F1-F3): the static reach and collectability
# approximations were looser than the node they claimed to resolve.

def _body(repo, body, header=""):
    (repo / TESTS).write_text(header + "\n\ndef test_blocks_its_origin():\n" + body, "utf-8")
    return i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)


@pytest.mark.parametrize("path", [
    "tests/scripts/test_check_example.py",   # the test's own file shares the basename
    "tools/not_check_example.py",
    "/tmp/check_example.py",
])
def test_I8_a_string_sharing_only_the_basename_does_not_reach_the_gate(repo, path):
    v = _body(repo, f"    assert run([{path!r}]) == 2\n")
    assert len(v) == 1 and "never reaches the gate" in v[0]


@pytest.mark.parametrize("path", ["tools/check_example.py",
                                  "/abs/repo/tools/check_example.py",
                                  "python3 tools/check_example.py --x"])
def test_I8_a_string_naming_the_gate_path_reaches_it(repo, path):
    assert _body(repo, f"    assert run({path!r}) == 2\n") == []


def test_I8_a_same_stem_module_from_another_package_is_not_the_gate(repo):
    v = _body(repo, "    assert ce.check('origin') == 2\n",
              "import vendor.check_example as ce")
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_dotted_import_used_only_through_its_package_does_not_reach_the_gate(repo):
    v = _body(repo, "    assert tools.unrelated.helper() == 1\n", "import tools.check_example")
    assert len(v) == 1 and "never reaches the gate" in v[0]


@pytest.mark.parametrize("header", ["from check_example import check",
                                    "from tools.check_example import check"])
def test_I8_a_from_import_of_the_gate_reaches_it(repo, header):
    assert _body(repo, "    assert check('origin') == 2\n", header) == []


def test_I8_a_locally_rebound_name_is_not_the_imported_gate(repo):
    v = _body(repo, "    check_example = object()\n    assert check_example\n",
              "import check_example")
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_an_argument_that_shadows_the_import_is_not_the_gate(repo):
    (repo / TESTS).write_text(
        "import check_example\n\ndef test_blocks_its_origin(check_example):\n"
        "    assert check_example\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_class_pytest_does_not_collect_is_not_a_node(repo):
    (repo / TESTS).write_text(
        "import check_example\n\nclass Cases:\n    def test_blocks_its_origin(self):\n"
        "        assert check_example.check('origin') == 2\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE,
                 exit1_evidence=f"{TESTS}::Cases::test_blocks_its_origin"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


PARAM_FILE = """\
import pytest
import check_example

@pytest.mark.parametrize("x", ["origin", 3, None])
def test_values(x):
    assert check_example.check(x)

@pytest.mark.parametrize("a,b", [("origin", 1), ("other", 2)])
def test_tuples(a, b):
    assert check_example.check(a)

@pytest.mark.parametrize("x", [1, 2], ids=["first", "second"])
def test_ids(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", [pytest.param(1, id="named"), pytest.param(2)])
def test_param_objects(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", CASES)
def test_non_literal(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", [1])
@pytest.mark.parametrize("y", [2])
def test_stacked(x, y):
    assert check_example.check(x)

@pytest.mark.flaky(reason="do not parametrize this")
def test_skipped(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", [{"k": 1}])
def test_dict_value(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", ids=["a"])
def test_too_few_args(x):
    assert check_example.check(x)

@pytest.mark.parametrize("x", [1], ids=IDS)
def test_non_literal_ids(x):
    assert check_example.check(x)
"""


@pytest.mark.parametrize("node", ["test_values[origin]", "test_values[3]",
                                  "test_values[None]", "test_tuples[origin-1]",
                                  "test_ids[second]", "test_param_objects[named]",
                                  "test_param_objects[2]", "test_non_literal",
                                  "test_stacked"])
def test_I8_a_parameter_id_pytest_generates_is_accepted(repo, node):
    (repo / TESTS).write_text(PARAM_FILE, "utf-8")
    assert i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::{node}"), repo) == []


@pytest.mark.parametrize("node", ["test_values[windows]", "test_tuples[origin]",
                                  "test_ids[1]", "test_param_objects[1]",
                                  "test_non_literal[x]", "test_stacked[1-2]",
                                  "test_skipped[x]", "test_dict_value[x0]",
                                  "test_too_few_args[a]", "test_non_literal_ids[a]"])
def test_I8_a_parameter_id_that_cannot_be_shown_to_exist_fails_closed(repo, node):
    (repo / TESTS).write_text(PARAM_FILE, "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::{node}"), repo)
    assert len(v) == 1 and "is not a parameter id this test can be shown" in v[0]


def test_I8_a_non_ascii_string_id_is_not_guessed(repo):
    (repo / TESTS).write_text(
        "import pytest\nimport check_example\n\n"
        "@pytest.mark.parametrize('x', ['caf\\u00e9'])\n"
        "def test_blocks_its_origin(x):\n    assert check_example.check(x)\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE + "[café]"), repo)
    assert len(v) == 1 and "can be shown" in v[0]


def test_I8_a_test_class_that_does_not_exist_is_not_a_node(repo):
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::TestMissing::test_in_a_class"),
           repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


def test_I8_a_relative_import_of_an_unrelated_name_does_not_bind_the_gate(repo):
    v = _body(repo, "    assert check('origin') == 2\n", "from . import check")
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_an_attribute_or_constant_that_is_not_the_gate_does_not_reach_it(repo):
    v = _body(repo, "    assert other.attr == 2 and 3 == 3\n", "import tools.check_example")
    assert len(v) == 1 and "never reaches the gate" in v[0]



# ---- round 3 of /verify (codex F1-F2, grok F1-F2)

def test_I8_an_import_inside_a_sibling_test_binds_nothing_in_the_cited_node(repo):
    (repo / TESTS).write_text(
        "def test_sibling():\n    import check_example\n    assert check_example\n\n"
        "def test_blocks_its_origin():\n    assert check_example.check('origin') == 2\n",
        "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_an_import_inside_the_cited_node_binds(repo):
    assert _body(repo, "    import check_example\n    assert check_example.check(1)\n") == []


def test_I8_a_module_level_import_inside_a_try_block_binds(repo):
    header = "try:\n    import check_example\nexcept ImportError:\n    pass"
    assert _body(repo, "    assert check_example.check(1)\n", header) == []


def test_I8_an_import_inside_a_class_binds_nothing_at_module_level(repo):
    header = "class Helper:\n    import check_example"
    v = _body(repo, "    assert check_example.check(1)\n", header)
    assert len(v) == 1 and "never reaches the gate" in v[0]


@pytest.mark.parametrize("header", [
    'HOOK = REPO / "tools" / "check_example.py"',
    'HOOK = "tools/check_example.py"',
    'HOOK = Path(__file__).parents[2] / "tools/check_example.py"',
])
def test_I8_a_module_constant_naming_the_gate_path_reaches_it(repo, header):
    assert _body(repo, "    assert run([HOOK, 'origin']) == 2\n", header) == []


def test_I8_a_module_constant_naming_another_path_does_not_reach_the_gate(repo):
    v = _body(repo, "    assert run([HOOK, 'origin']) == 2\n",
              'HOOK = REPO / "tools" / "other.py"')
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_constant_assigned_inside_the_node_is_not_a_module_binding(repo):
    v = _body(repo, '    HOOK = "unrelated"\n    assert run(HOOK) == 2\n',
              'HOOK = "tools/check_example.py"')
    assert len(v) == 1 and "never reaches the gate" in v[0]


@pytest.mark.parametrize("decorator,node", [
    ('@pytest.mark.parametrize("x", [1, 1])', "test_blocks_its_origin[1]"),
    ('@pytest.mark.parametrize("x", [1, 2], ids=["a", "a"])', "test_blocks_its_origin[a]"),
])
def test_I8_a_duplicated_parameter_id_fails_closed(repo, decorator, node):
    (repo / TESTS).write_text(
        f"import pytest\nimport check_example\n\n{decorator}\n"
        "def test_blocks_its_origin(x):\n    assert check_example.check(x)\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=f"{TESTS}::{node}"), repo)
    assert len(v) == 1 and "can be shown" in v[0]


@pytest.mark.parametrize("promoted", ["hook", "skill -- landed", "n/a -- reference",
                                      "schema"])
def test_I8_holds_every_value_the_scanner_reads_as_completed(repo, promoted):
    """grok r3 F1: scan_promotion_candidates reads a bare tier as a completed promotion,
    so a bare tier on an exit1 was a way around I8."""
    assert len(i8(exit1(promoted=promoted), repo)) == 2


@pytest.mark.parametrize("promoted", ["no", "not yet", "false", "", "  "])
def test_I8_skips_values_the_scanner_reads_as_unpromoted(repo, promoted):
    assert i8(exit1(promoted=promoted), repo) == []


def test_I8_population_covers_everything_the_weekly_scanner_counts_as_done():
    """Every value the scanner drops from the backlog must be held by I8, or a record can
    leave the backlog unproven. `partial` stays in the backlog AND is held by I8."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import scan_promotion_candidates as spc
    for v in ["yes", "partial", "partial -- half", "hook", "skill x", "n/a", "no",
              "not", "false x", "no -- CORRECTED"]:
        if spc.is_promoted({"promoted": v}):
            assert ps.claims_a_landed_gate(v), v
    assert ps.claims_a_landed_gate("partial") and not spc.is_promoted({"promoted": "partial"})
    assert not ps.claims_a_landed_gate("no -- CORRECTED")


def test_scan_counts_a_bare_tier_exit1_as_completed(tmp_path, repo):
    mem = tmp_path / "mem"
    mem.mkdir()
    write_rule(mem, "feedback_tier.md", **exit1(promoted="hook"))
    report = ps.scan(mem, {}, repo_root=repo)
    assert (report["exit1_completed"], report["exit1_proven"]) == (1, 0)


def test_I8_a_function_defined_inside_a_module_level_if_binds_nothing(repo):
    header = "if True:\n    def helper():\n        import check_example"
    v = _body(repo, "    assert check_example.check(1)\n", header)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_module_constant_inside_a_try_block_binds(repo):
    header = 'try:\n    HOOK = "tools/check_example.py"\nexcept Exception:\n    pass'
    assert _body(repo, "    assert run([HOOK]) == 2\n", header) == []


# ---- round 4 of /verify (codex F1-F2)

@pytest.mark.parametrize("rebind", [
    "check_example = object()",
    "def check_example():\n    return 1",
    "class check_example:\n    pass",
    "from other import check_example",
    "import other as check_example",
    "for check_example in []:\n    pass",
    "with open('x') as check_example:\n    pass",
    "try:\n    pass\nexcept Exception as check_example:\n    pass",
    "try:\n    import check_example\nexcept ImportError:\n    check_example = None",
    "if True:\n    check_example = 1",
    "if True:\n    def check_example():\n        return 1",
    "if (check_example := 1):\n    pass",
])
def test_I8_a_module_level_rebinding_of_the_import_does_not_reach_the_gate(repo, rebind):
    v = _body(repo, "    assert check_example.check(1)\n", "import check_example\n" + rebind)
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_module_level_rebinding_of_a_dotted_package_drops_its_chain(repo):
    v = _body(repo, "    assert tools.check_example.check(1)\n",
              "import tools.check_example\ntools = object()")
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_unrelated_module_bindings_leave_the_gate_name_alone(repo):
    header = ("import os, check_example\nfrom json import dumps\nX = 1\n"
              "for i in []:\n    pass\nwith open('x') as fh:\n    pass\n"
              "try:\n    pass\nexcept Exception as err:\n    pass\n"
              "try:\n    pass\nexcept Exception:\n    pass\n"
              "while False:\n    pass\nif X:\n    Y = 2\n"
              "def helper():\n    return 1\nclass Other:\n    pass")
    assert _body(repo, "    assert check_example.check(1)\n", header) == []


def test_I8_a_gate_path_constant_is_not_dropped_as_a_rebinding(repo):
    header = 'import check_example\nHOOK = "tools/check_example.py"'
    assert _body(repo, "    assert run([HOOK]) == 2\n", header) == []


def test_I8_a_test_class_with_init_is_not_collected(repo):
    (repo / TESTS).write_text(
        "import check_example\n\nclass TestCases:\n    def __init__(self):\n        pass\n"
        "    def test_blocks_its_origin(self):\n        assert check_example.check(1)\n",
        "utf-8")
    v = i8(exit1(exit1_gate=GATE,
                 exit1_evidence=f"{TESTS}::TestCases::test_blocks_its_origin"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


def test_I8_an_unrelated_import_inside_the_node_does_not_reach_the_gate(repo):
    v = _body(repo, "    import json as ce\n    assert ce.dumps(1)\n")
    assert len(v) == 1 and "never reaches the gate" in v[0]


def test_I8_a_method_or_nested_def_named_like_the_import_is_not_a_module_rebinding(repo):
    header = ("import check_example\nclass Other:\n    def check_example(self):\n"
              "        return 1\ndef helper():\n    def check_example():\n        return 1")
    assert _body(repo, "    assert check_example.check(1)\n", header) == []


# ---- round 5 of /verify (codex F1-F2, grok F1). These fixes postdate the last review.

def test_I8_a_test_class_with_new_is_not_collected(repo):
    (repo / TESTS).write_text(
        "import check_example\n\nclass TestCases:\n    def __new__(cls):\n"
        "        return object.__new__(cls)\n"
        "    def test_blocks_its_origin(self):\n        assert check_example.check(1)\n",
        "utf-8")
    v = i8(exit1(exit1_gate=GATE,
                 exit1_evidence=f"{TESTS}::TestCases::test_blocks_its_origin"), repo)
    assert len(v) == 1 and "pytest cannot collect" in v[0]


@pytest.mark.parametrize("src,node", [
    ("@pytest.mark.skip\ndef test_blocks_its_origin():\n    assert check_example.check(1)\n",
     EVIDENCE),
    ("@pytest.mark.skip(reason='later')\ndef test_blocks_its_origin():\n"
     "    assert check_example.check(1)\n", EVIDENCE),
    ("pytestmark = pytest.mark.skip\n\ndef test_blocks_its_origin():\n"
     "    assert check_example.check(1)\n", EVIDENCE),
    ("pytestmark = [pytest.mark.slow, pytest.mark.skip('x')]\n\n"
     "def test_blocks_its_origin():\n    assert check_example.check(1)\n", EVIDENCE),
    ("@pytest.mark.skip\nclass TestCases:\n    def test_blocks_its_origin(self):\n"
     "        assert check_example.check(1)\n", f"{TESTS}::TestCases::test_blocks_its_origin"),
])
def test_I8_an_unconditionally_skipped_test_is_not_evidence(repo, src, node):
    (repo / TESTS).write_text("import pytest\nimport check_example\n\n" + src, "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=node), repo)
    assert len(v) == 1 and "skipped unconditionally" in v[0]


@pytest.mark.parametrize("src", [
    "@pytest.mark.skipif(sys.platform == 'win32', reason='posix')\n"
    "def test_blocks_its_origin():\n    assert check_example.check(1)\n",
    "pytestmark = pytest.mark.slow\n\ndef test_blocks_its_origin():\n"
    "    assert check_example.check(1)\n",
    "OTHER = pytest.mark.skip\n\ndef test_blocks_its_origin():\n"
    "    assert check_example.check(1)\n",
    "@pytest.mark.skip\nclass TestOther:\n    pass\n\n"
    "def test_blocks_its_origin():\n    assert check_example.check(1)\n",
])
def test_I8_a_conditional_skip_or_an_unrelated_mark_is_not_a_skip(repo, src):
    (repo / TESTS).write_text("import pytest\nimport check_example\n\n" + src, "utf-8")
    assert i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo) == []


def test_I8_a_decorator_string_naming_the_gate_is_not_the_body_reaching_it(repo):
    """grok r5 F1: a parametrize list naming the gate path made any node in the file
    'reach' it, while the body never touched the gate."""
    (repo / TESTS).write_text(
        "import pytest\n\n@pytest.mark.parametrize('p', ['tools/check_example.py'])\n"
        "def test_blocks_its_origin(p):\n    assert p\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE + "[tools/check_example.py]"), repo)
    assert any("never reaches the gate" in x for x in v)


def test_I8_an_assert_only_in_a_decorator_is_not_an_assertion(repo):
    (repo / TESTS).write_text(
        "import pytest\nimport check_example\n\n"
        "@pytest.mark.parametrize('x', [pytest.raises])\n"
        "def test_blocks_its_origin(x):\n    check_example.check(x)\n", "utf-8")
    v = i8(exit1(exit1_gate=GATE, exit1_evidence=EVIDENCE), repo)
    assert len(v) == 1 and "asserts nothing" in v[0]
