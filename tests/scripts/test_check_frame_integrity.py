"""Tests for tools/check_frame_integrity.py.

The load-bearing contract under test is the THREE-STATE design: PASS / FAIL /
CANNOT_RUN, where a CANNOT_RUN is never counted as a pass. Several tests here exist
specifically to fail if someone "simplifies" that back to a boolean, because that
collapse rebuilds the exact failure the gate was built to prevent.

Fixtures are synthetic and generic by design (public repo).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "check_frame_integrity.py"
SCHEMA = REPO / "framework" / "frame-schema.yaml"

sys.path.insert(0, str(REPO / "tools"))
import check_frame_integrity as cfi  # noqa: E402


# ---------------------------------------------------------------- fixtures

def clean_frame():
    """A frame that should pass every runnable check."""
    return {
        "schema_version": 3,
        "version": 2,
        "locked": True,
        "engagement": "acme",
        "d1": {
            "problem_statement": "Where should Acme place its next investment?",
            "problem_type": "prioritization",
            "metric_roles": {"quality_score": "guardrail", "throughput": "target"},
        },
        "facts": {
            "f1": {"text": "throughput is 100/day", "tier": "A", "first_seen": 1},
            "f2": {"text": "budget is fixed", "tier": "A", "first_seen": 1},
        },
        "inputs": {
            "i_vol": {"name": "throughput", "aka": ["volume"]},
            "i_cost": {"name": "build cost", "aka": []},
        },
        "elements": [
            {"id": "e1", "name": "expected impact", "name_surface": "p5",
             "measure": "convertible volume", "measure_surface": "p5",
             "because": ["f1"], "inputs": ["i_vol"], "protected": True, "first_seen": 1},
            {"id": "e2", "name": "cost to build", "name_surface": "p5",
             "measure": "engineering weeks", "measure_surface": "p5",
             "because": ["f2"], "inputs": ["i_cost"], "protected": True, "first_seen": 1},
        ],
        "closure": "These two and no more because the constraint is one investment.",
        "exclusions": [{"element": "strategic signal", "reason": "a preference, not a property"}],
        "unknowns": {
            "u1": {"text": "split unknown", "disposition": "assumption",
                   "basis": "low/high case", "sensitivity": "flips at the high end"},
        },
        "recommendation": {
            "text": "Commit the next investment to the first option.",
            "confidence": "medium",
            "next_action": {"who": "the sponsor", "what": "approve two weeks of measurement"},
        },
        # The backfill-impossible pair. This fixture carried `locked: True` and
        # NEITHER of these until 2026-08-14, which is precisely the hole F13 closes:
        # the canonical "clean" frame had lost both run-record ledgers and still
        # passed everything.
        "proposals": [
            {"stage": "B", "proposed": "rank by raw volume",
             "status": "rejected", "reason": "volume is not convertible volume"},
        ],
        "prediction": {
            "made_at_version": 2,
            "will_be_probed": "the denominator under expected impact",
        },
    }


def write(tmp_path, data, name="frame.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def run(frame_path, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(frame_path), "--schema", str(SCHEMA), "--json", *extra],
        capture_output=True, text=True,
    )


def states(frame, prior=None):
    return {r.rule: r.state for r in cfi.run_checks(frame, prior)}


# ---------------------------------------------------------------- happy path

def test_clean_frame_passes_every_runnable_check():
    st = states(clean_frame())
    assert cfi.FAIL not in st.values(), st
    # F2b and F9 legitimately cannot run on a single uniform-version frame
    assert st["F2b"] == cfi.CANNOT_RUN
    assert st["F9"] == cfi.CANNOT_RUN
    for rule in ("F1a", "F1b", "F2a", "F3", "F5", "F8a", "F8b", "F10struct", "F12", "F13"):
        assert st[rule] == cfi.PASS, (rule, st[rule])


def test_clean_frame_exits_zero(tmp_path):
    r = run(write(tmp_path, clean_frame()))
    assert r.returncode == 0, r.stdout
    payload = json.loads(r.stdout)
    assert payload["clean"] is True
    # not fully covered: F2b/F9 cannot run
    assert payload["fully_covered"] is False


# ---------------------------------------------------------------- the three-state contract

def test_cannot_run_is_not_counted_as_pass():
    """The central contract. If this fails, the gate is lying about coverage."""
    f = clean_frame()
    del f["d1"]["metric_roles"]          # F8b can no longer run
    for e in f["elements"]:
        e.pop("name_surface"); e.pop("measure_surface")   # F1b can no longer run
    results = cfi.run_checks(f)
    by = {r.rule: r for r in results}
    assert by["F8b"].state == cfi.CANNOT_RUN
    assert by["F1b"].state == cfi.CANNOT_RUN
    passes = [r.rule for r in results if r.state == cfi.PASS]
    assert "F8b" not in passes and "F1b" not in passes


def test_cannot_run_always_explains_why():
    """A bare CANNOT_RUN with no reason is unactionable."""
    f = clean_frame()
    del f["d1"]["metric_roles"]
    for r in cfi.run_checks(f):
        if r.state == cfi.CANNOT_RUN:
            assert r.detail.strip(), r.rule
            assert len(r.detail) > 20, (r.rule, r.detail)


def test_fully_covered_false_whenever_anything_cannot_run(tmp_path):
    r = run(write(tmp_path, clean_frame()))
    p = json.loads(r.stdout)
    assert p["counts"]["cannot_run"] > 0
    assert p["fully_covered"] is False


# ---------------------------------------------------------------- per-rule failures

def test_F1a_fails_on_missing_measure():
    f = clean_frame()
    f["elements"][1]["measure"] = ""
    assert states(f)["F1a"] == cfi.FAIL


def test_F1b_fails_when_measure_sits_off_the_naming_surface():
    f = clean_frame()
    f["elements"][0]["measure_surface"] = "p12"
    assert states(f)["F1b"] == cfi.FAIL


def test_F1b_cannot_run_below_v3_because_surfaces_were_prose():
    """Measured 2026-08-13 on a real frame: comparing prose surface DESCRIPTIONS for
    equality produced 2 false failures out of 4. Both said "same line, stated
    parenthetically with the name" -- co-located -- but the strings differed because
    one carried extra detail.

    A check that cannot be trusted must say so. Emitting findings it knows are
    unreliable is worse than emitting none.
    """
    f = clean_frame()
    f["schema_version"] = 2
    r = [x for x in cfi.run_checks(f) if x.rule == "F1b"][0]
    assert r.state == cfi.CANNOT_RUN
    assert "prose" in r.detail


def test_surfaces_must_be_identifiers_not_prose_at_v3():
    """Without this the field drifts straight back to sentences and F1b silently
    returns to comparing descriptions, which is the bug v3 exists to fix."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["elements"][0]["measure_surface"] = (
        "072126-doc.md, Step 4 workplan and the kill criterion in Step 7; deck slide 10")
    errs = cfi.validate_surface_identifiers(f, schema)
    assert errs, "prose in a surface field must be a structural error at v3"
    assert "identifier" in errs[0]


def test_identifier_pattern_comes_from_the_schema_not_from_code():
    """Tightening the pattern must be a schema edit."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert schema.get("identifier_pattern")
    src = (REPO / "tools" / "check_frame_integrity.py").read_text(encoding="utf-8")
    assert "identifier_pattern" in src
    assert "^[a-z0-9]" not in src, "pattern must not be hardcoded in the module"


def test_prose_surfaces_are_not_flagged_below_v3():
    """Old frames keep working; the constraint applies from v3 onward only."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["schema_version"] = 2
    f["elements"][0]["measure_surface"] = "a long prose description of where it sits"
    assert cfi.validate_surface_identifiers(f, schema) == []


def test_F2a_fails_on_empty_because_not_just_unresolved():
    """Zero ids resolve vacuously. The non-empty half is the whole point."""
    f = clean_frame()
    f["elements"][0]["because"] = []
    r = [x for x in cfi.run_checks(f) if x.rule == "F2a"][0]
    assert r.state == cfi.FAIL
    assert "cites nothing" in " ".join(r.offenders)


def test_F2a_fails_on_unresolved_id():
    f = clean_frame()
    f["elements"][0]["because"] = ["f_does_not_exist"]
    assert states(f)["F2a"] == cfi.FAIL


def test_F2b_detects_retrofitted_citation():
    f = clean_frame()
    f["elements"][0]["first_seen"] = 2
    f["facts"]["f1"]["first_seen"] = 5     # fact newer than the element citing it
    assert states(f)["F2b"] == cfi.FAIL


def test_F2b_cannot_run_when_all_versions_identical():
    """A retrospective reconstruction stamps everything 1. That is not a pass."""
    assert states(clean_frame())["F2b"] == cfi.CANNOT_RUN


def test_F3_detects_shared_input():
    f = clean_frame()
    f["elements"][1]["inputs"] = ["i_vol"]   # now load-bearing in both
    r = [x for x in cfi.run_checks(f) if x.rule == "F3"][0]
    assert r.state == cfi.FAIL
    assert "i_vol" in " ".join(r.offenders)


def test_F3_matches_on_id_so_synonyms_cannot_dodge_it():
    """The registry exists so two wordings of one quantity collide by construction."""
    f = clean_frame()
    f["inputs"]["i_vol"]["aka"] = ["volume", "unit count"]
    f["elements"][1]["inputs"] = ["i_vol"]
    assert states(f)["F3"] == cfi.FAIL


@pytest.mark.parametrize("mutate", [
    lambda f: f.__setitem__("closure", ""),
    lambda f: f.__setitem__("exclusions", []),
    lambda f: f.__setitem__("exclusions", [{"element": "x", "reason": ""}]),
])
def test_F5_fails_without_closure_or_reasoned_exclusion(mutate):
    f = clean_frame()
    mutate(f)
    assert states(f)["F5"] == cfi.FAIL


def test_F8b_detects_guardrail_used_as_ranking_input():
    """The documented role-reassignment drift."""
    f = clean_frame()
    f["inputs"]["i_q"] = {"name": "quality_score", "aka": []}
    f["elements"][0]["inputs"] = ["i_vol", "i_q"]
    r = [x for x in cfi.run_checks(f) if x.rule == "F8b"][0]
    assert r.state == cfi.FAIL
    assert "guardrail" in " ".join(r.offenders)


def test_F8b_matches_guardrail_through_an_alias():
    f = clean_frame()
    f["inputs"]["i_q"] = {"name": "csat", "aka": ["quality_score"]}
    f["elements"][0]["inputs"] = ["i_vol", "i_q"]
    assert states(f)["F8b"] == cfi.FAIL


def test_F8b_cannot_run_without_metric_roles():
    f = clean_frame()
    del f["d1"]["metric_roles"]
    assert states(f)["F8b"] == cfi.CANNOT_RUN


def test_F9_detects_dropped_measure_against_prior():
    prior = clean_frame()
    now = clean_frame()
    now["elements"][0]["measure"] = ""
    r = [x for x in cfi.run_checks(now, prior) if x.rule == "F9"][0]
    assert r.state == cfi.FAIL
    assert "lost its measure" in " ".join(r.offenders)


def test_F9_cannot_run_without_a_prior():
    assert states(clean_frame())["F9"] == cfi.CANNOT_RUN


@pytest.mark.parametrize("disp,missing", [
    ("assumption", {"basis": "", "sensitivity": "x"}),
    ("assumption", {"basis": "x", "sensitivity": ""}),
    ("data_request", {"owner": "", "due": "week 1"}),
    ("question", {"owner": ""}),
])
def test_F10struct_fails_on_missing_disposition_fields(disp, missing):
    f = clean_frame()
    u = {"text": "t", "disposition": disp}
    u.update(missing)
    f["unknowns"] = {"u1": u}
    assert states(f)["F10struct"] == cfi.FAIL


def test_F10struct_fails_on_unknown_disposition_value():
    f = clean_frame()
    f["unknowns"] = {"u1": {"text": "t", "disposition": "maybe"}}
    assert states(f)["F10struct"] == cfi.FAIL


@pytest.mark.parametrize("mutate", [
    lambda r: r.__setitem__("confidence", ""),
    lambda r: r.__setitem__("next_action", None),
    lambda r: r.__setitem__("next_action", {"who": "", "what": "x"}),
])
def test_F12_fails_on_underspecified_recommendation(mutate):
    f = clean_frame()
    mutate(f["recommendation"])
    assert states(f)["F12"] == cfi.FAIL


# ---------------------------------------------------------------- schema gate + exit codes

def test_unknown_schema_version_is_refused_not_guessed(tmp_path):
    f = clean_frame()
    f["schema_version"] = 99
    r = run(write(tmp_path, f))
    assert r.returncode == 3
    assert json.loads(r.stdout)["status"] == "refused"


# ---------------------------------------------------------------- flat dotted keys
#
# Origin 2026-08-13, SECOND corpus run. The schema declares its own field names in
# dotted notation (`d1.problem_statement:`), so a transcribing agent copied them
# literally as FLAT top-level keys instead of nesting under `d1:`. Ten fields landed
# that way.
#
# validate_structure's dotted-path walk could not see it: with no `d1` parent every
# nested field read as "not authored yet". The frame passed structural validation and
# returned CANNOT_RUN on every check reading d1 or recommendation -- a well-formed
# file, a confident verdict, and nothing actually tested.
#
# The root cause is the schema's own notation, so this will recur with any new
# transcriber. It has to be caught mechanically.

def test_flat_dotted_keys_are_detected():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    frame = {
        "schema_version": 2,
        "d1.problem_statement": "written flat instead of nested",
        "d1.problem_type": "prioritization",
        "recommendation.text": "also flat",
    }
    errs = cfi.detect_flat_dotted_keys(frame, schema)
    assert errs, "mis-nesting must be caught"
    joined = " ".join(errs)
    assert "d1" in joined and "recommendation" in joined
    assert "CANNOT_RUN" in joined, "the error must explain the consequence"


def test_flat_dotted_keys_surface_through_validate_structure(tmp_path):
    """It must reach the CLI, not just the helper."""
    frame = {"schema_version": 2, "d1.problem_statement": "x", "recommendation.text": "y"}
    r = run(write(tmp_path, frame))
    payload = json.loads(r.stdout)
    assert payload["structural_errors"], payload
    assert payload["clean"] is False
    assert r.returncode == 2


def test_correctly_nested_frame_triggers_no_flat_key_error():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert cfi.detect_flat_dotted_keys(clean_frame(), schema) == []


def test_yaml_parsed_date_is_accepted_for_timestamp_fields():
    """YAML turns an unquoted 2026-07-21 into a date, not a str.

    Rejecting it was a false positive on a correctly-authored frame.
    """
    import datetime
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["locked_at"] = datetime.date(2026, 7, 21)
    assert not [e for e in cfi.validate_structure(f, schema) if "locked_at" in e]


# ---------------------------------------------------------------- version support
#
# The schema bumped 1 -> 2 on 2026-08-13 to add run-state fields. Equality-checking
# schema_version would have orphaned every frame written at v1, INCLUDING the
# retrospective reconstruction the acceptance regression is pinned to -- so a bump
# would have silently deleted the only evidence the gate works.

def test_schema_declares_which_frame_versions_it_supports():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    supported = schema.get("supports_frames_at")
    assert isinstance(supported, list) and supported, "schema must declare supported versions"
    assert schema["schema_version"] in supported, "current version must support itself"


@pytest.mark.parametrize("ver", [1, 2, 3])
def test_every_supported_version_is_accepted(tmp_path, ver):
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    if ver not in schema.get("supports_frames_at", []):
        pytest.skip(f"v{ver} not currently supported")
    f = clean_frame()
    f["schema_version"] = ver
    r = run(write(tmp_path, f))
    assert r.returncode == 0, r.stdout
    assert json.loads(r.stdout)["schema_version"] == ver


def test_supported_versions_come_from_the_schema_not_from_code():
    """Adding a version must be a schema edit, never a code edit."""
    src = (REPO / "tools" / "check_frame_integrity.py").read_text(encoding="utf-8")
    assert "supports_frames_at" in src
    # no hardcoded version allowlist in the module
    assert "[1, 2]" not in src and "(1, 2)" not in src


def test_v2_run_state_fields_are_type_checked(tmp_path):
    """The new fields must be real schema entries, not free-form."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    for field in ("segment_completed", "status", "status_reason", "declines"):
        assert field in schema["fields"], field
    f = clean_frame()
    f["schema_version"] = 2
    f["declines"] = "should be a list"
    errs = cfi.validate_structure(f, schema)
    assert any("declines" in e for e in errs), errs


def test_missing_schema_version_is_refused(tmp_path):
    f = clean_frame()
    del f["schema_version"]
    assert run(write(tmp_path, f)).returncode == 3


def test_unreadable_frame_exits_4(tmp_path):
    p = tmp_path / "broken.yaml"
    p.write_text("key: [unclosed\n", encoding="utf-8")
    assert run(p).returncode == 4


def test_non_mapping_frame_exits_4(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    assert run(p).returncode == 4


def test_any_failure_exits_2(tmp_path):
    f = clean_frame()
    f["closure"] = ""
    assert run(write(tmp_path, f)).returncode == 2


# ---------------------------------------------------------------- structural gate
#
# Origin 2026-08-13: an adversarial panel predicted, and a live run confirmed, that a
# frame with `elements` as a STRING and `d1` as a STRING returned clean=true and exit
# 0. Malformed fields made every reader helper return empty, so all checks degraded to
# CANNOT_RUN, and CANNOT_RUN does not block. A green light on a structurally invalid
# file -- the project's documented failure mode, rebuilt inside the gate meant to catch
# it. These tests exist so it cannot come back.

def garbage_frame():
    return {
        "schema_version": 1,
        "engagement": 12345,                     # should be str
        "d1": "this should be a mapping",        # should be dict
        "closure": "x",
        "exclusions": [{"element": "y", "reason": "z"}],
        "elements": "this should be a list",     # should be list
    }


def test_the_original_bug_case_is_not_clean(tmp_path):
    r = run(write(tmp_path, garbage_frame()))
    payload = json.loads(r.stdout)
    assert payload["clean"] is False, "a structurally invalid frame must never be clean"
    assert r.returncode == 2
    assert payload["structural_errors"], "malformed shapes must be reported, not swallowed"


def test_malformed_field_is_a_hard_error_not_a_cannot_run(tmp_path):
    """The precise mechanism: wrong type -> empty reader -> CANNOT_RUN -> not a failure."""
    payload = json.loads(run(write(tmp_path, garbage_frame())).stdout)
    joined = " ".join(payload["structural_errors"])
    assert "elements must be list" in joined
    assert "d1" in joined


def test_structural_errors_are_deduplicated(tmp_path):
    """One malformed parent is hit once per child declared under it."""
    errs = json.loads(run(write(tmp_path, garbage_frame())).stdout)["structural_errors"]
    assert len(errs) == len(set(errs))


def test_absent_fields_are_not_structural_errors():
    """A frame is legitimately incomplete for most of its life. Absent != malformed."""
    f = clean_frame()
    del f["closure"], f["exclusions"], f["recommendation"]
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert cfi.validate_structure(f, schema) == []


def test_bool_is_not_accepted_where_int_is_declared():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["version"] = True                      # bool subclasses int in python
    errs = cfi.validate_structure(f, schema)
    assert any("version" in e and "bool" in e for e in errs), errs


def test_coverage_floor_blocks_a_frame_nothing_could_test(tmp_path):
    """clean=true on 1 executed check is a meaningless green light."""
    payload = json.loads(run(write(tmp_path, {"schema_version": 1, "engagement": "x"})).stdout)
    assert payload["under_coverage_floor"] is True
    assert payload["clean"] is False
    assert payload["counts"]["executed"] < 2


def test_real_frame_has_no_structural_errors(tmp_path):
    """The fix must not fire on a well-formed frame."""
    payload = json.loads(run(write(tmp_path, clean_frame())).stdout)
    assert payload["structural_errors"] == []
    assert payload["clean"] is True


# ---------------------------------------------------------------- acceptance regression

def _find_reconstruction():
    """Locate the retrospective reconstruction without naming the engagement.

    It lives under gitignored output/, so this file (which is PUBLIC) must not
    hardcode its path: the directory name is a real company. Glob on the
    engagement-agnostic filename instead.
    """
    hits = sorted((REPO / "output").glob("*/casework/frame-*-reconstructed.yaml"))
    return hits[0] if hits else None


RECONSTRUCTION = _find_reconstruction()


@pytest.mark.skipif(RECONSTRUCTION is None,
                    reason="reconstruction lives in gitignored output/; local-only check")
def test_acceptance_measured_result_is_pinned():
    """Regression on the MEASURED 2026-08-13 result.

    The checker must independently fail the real reconstructed frame. If a future
    edit makes this frame pass, the checker has been broken, not the frame fixed.
    """
    frame = yaml.safe_load(RECONSTRUCTION.read_text(encoding="utf-8"))
    st = states(frame)

    # The headline: the double-counted input the room actually interrogated.
    assert st["F3"] == cfi.FAIL

    # F13 added 2026-08-14. It FAILs here, and that is a substantive finding rather
    # than an artifact of the frame being retrospective: the 2026-08-05 delivery
    # really did go in the room with `proposals: []` and `prediction: null`. Unlike
    # F2b, whose inputs are unknowable after the fact, this one is knowably absent.
    # A reconstruction cannot manufacture either, and it should not be able to.
    expected_fail = {"F1a", "F2a", "F3", "F5", "F10struct", "F12", "F13"}
    expected_cannot = {"F1b", "F2b", "F8b", "F9"}
    expected_pass = {"F8a"}

    got_fail = {k for k, v in st.items() if v == cfi.FAIL}
    got_cannot = {k for k, v in st.items() if v == cfi.CANNOT_RUN}
    got_pass = {k for k, v in st.items() if v == cfi.PASS}

    assert got_fail == expected_fail, f"FAIL drift: {got_fail ^ expected_fail}"
    assert got_cannot == expected_cannot, f"CANNOT_RUN drift: {got_cannot ^ expected_cannot}"
    assert got_pass == expected_pass, f"PASS drift: {got_pass ^ expected_pass}"


# ---------------------------------------------------------------- F13
# The backfill-impossible run record. Added 2026-08-14 because the schema marked
# `proposals` and `prediction` required and NOTHING READ THAT -- `required:` in
# frame-schema.yaml is documentation, and no rule in `validation:` asserted field
# presence. A locked frame that lost both came back clean.


def test_F13_fails_locked_frame_with_no_rejection_record():
    frame = clean_frame()
    frame["proposals"] = []
    assert states(frame)["F13"] == cfi.FAIL


def test_F13_fails_locked_frame_with_no_prediction():
    frame = clean_frame()
    frame["prediction"] = None
    assert states(frame)["F13"] == cfi.FAIL


def test_F13_fails_when_will_be_probed_is_whitespace_only():
    """An empty string dressed as a value is still a lost record."""
    frame = clean_frame()
    frame["prediction"] = {"made_at_version": 2, "will_be_probed": "   "}
    assert states(frame)["F13"] == cfi.FAIL


def test_F13_names_both_losses_not_just_the_first():
    """Reporting one of two missing ledgers would understate what the run lost."""
    frame = clean_frame()
    frame["proposals"] = []
    frame["prediction"] = None
    result = [r for r in cfi.run_checks(frame) if r.rule == "F13"][0]
    assert result.state == cfi.FAIL
    assert len(result.offenders) == 2
    blob = " ".join(result.offenders)
    assert "proposals" in blob and "will_be_probed" in blob


def test_F13_cannot_run_while_the_frame_is_unlocked():
    """An open run has not lost anything yet. Requiring it early would push the
    operator to invent a prediction before there is anything to predict about."""
    frame = clean_frame()
    frame["locked"] = False
    frame["proposals"] = []
    frame["prediction"] = None
    assert states(frame)["F13"] == cfi.CANNOT_RUN


def test_F13_cannot_run_when_locked_is_absent_entirely():
    frame = clean_frame()
    del frame["locked"]
    frame["proposals"] = []
    assert states(frame)["F13"] == cfi.CANNOT_RUN


def test_F13_cannot_run_is_not_a_pass():
    """The three-state rule, restated for this check specifically: an unlocked frame
    missing both ledgers must never read as covered."""
    frame = clean_frame()
    frame["locked"] = False
    frame["proposals"] = []
    frame["prediction"] = None
    result = [r for r in cfi.run_checks(frame) if r.rule == "F13"][0]
    assert result.state != cfi.PASS
    assert result.detail.strip()


def test_F13_passes_when_both_ledgers_are_present_at_lock():
    assert states(clean_frame())["F13"] == cfi.PASS


def test_F13_accepts_a_list_for_will_be_probed():
    """Free text is the contract, but a list of probes is the natural shape and
    must not be rejected on type."""
    frame = clean_frame()
    frame["prediction"] = {"made_at_version": 2,
                           "will_be_probed": ["the denominator", "the closure claim"]}
    assert states(frame)["F13"] == cfi.PASS


def test_F13_rejects_an_empty_list_for_will_be_probed():
    frame = clean_frame()
    frame["prediction"] = {"made_at_version": 2, "will_be_probed": []}
    assert states(frame)["F13"] == cfi.FAIL


def test_F13_is_registered_in_the_schema_validation_block():
    """A check the code runs but the schema does not document drifts silently.
    The schema is the contract; this pins them together."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert "F13" in schema["validation"]
