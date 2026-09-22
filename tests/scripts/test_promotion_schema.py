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
