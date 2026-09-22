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
    # F14 added 2026-09-21 (the code drain). CANNOT_RUN here for a reason that is itself
    # the point of the check: `states()` calls run_checks WITHOUT a frame path, so the
    # scripts/ tree cannot be enumerated, so COVERAGE was never compared to anything.
    # A well-formed-looking PASS in that situation would be a check reporting clean while
    # measuring nothing -- exactly what this module's three-state design refuses.
    # F15 added 2026-09-21 (the reverse of F2a). CANNOT_RUN here for the same reason F14
    # is: `states()` calls run_checks WITHOUT a deck, and what a surface PRINTS cannot be
    # read from the frame alone. The frame's own declarations are the thing F15 tests, so
    # they cannot stand in for the artifact, and a PASS here would be a rule reporting
    # clean while measuring nothing.
    # F16 added 2026-09-21 (the retrospective delivery marker). CANNOT_RUN on the
    # reconstruction because it records no delivery -- and "this frame's artifact has not
    # been recorded as gone out" is exactly the state, not a pass.
    expected_cannot = {"F1b", "F2b", "F8b", "F9", "F14", "F15", "F16"}
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


# ---------------------------------------------------------------- F14, the code drain
# Added 2026-09-21. The self-learning loop drains rules and friction and nothing else;
# one engagement closed with 34 scripts beside its frame, four of which were already
# probes the method was separately planning to build from scratch.
#
# F14 answers TWO questions that a first draft collapsed into one: are the declarations
# WELL-FORMED (frame alone), and do they COVER the tree (needs a frame path). Collapsing
# them returned PASS -- "all N dispositioned" -- having compared them to nothing.


# F14 verifies that a promote/superseded TARGET actually exists, so fixtures must name a
# real file. Using a fake path made four tests fail the moment that check landed -- which
# was the check working, not the tests breaking.
REAL_TARGET = "tools/chrome_runner.py"


def _f14(frame, frame_path=None):
    return cfi.check_F14(frame, frame_path)


def _tree(tmp_path, *names):
    d = tmp_path / "scripts"
    d.mkdir()
    for n in names:
        (d / n).write_text("# x\n", encoding="utf-8")
    return tmp_path / "frame.yaml"


def test_F14_no_block_and_no_path_cannot_run():
    """'produced none' and 'never recorded' are indistinguishable without a tree."""
    r = _f14({})
    assert r.state == cfi.CANNOT_RUN
    assert "cannot be told apart" in r.detail


def test_F14_scripts_present_with_no_block_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py", "b.py")
        r = _f14({}, fp)
    assert r.state == cfi.FAIL
    assert "no `scripts` block" in r.detail


def test_F14_empty_tree_with_no_block_is_cannot_run_not_pass():
    """Silence is not a declaration. An empty tree does not certify 'produced none'."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td))
        r = _f14({}, fp)
    assert r.state == cfi.CANNOT_RUN
    assert "silence is not a declaration" in r.detail.lower()


def test_F14_explicit_empty_map_against_empty_tree_passes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td))
        r = _f14({"scripts": {}}, fp)
    assert r.state == cfi.PASS
    assert "produced none" in r.detail


def test_F14_wellformed_declarations_without_a_path_are_CANNOT_RUN_not_pass():
    """THE HOLE THIS TEST EXISTS FOR. Coverage unchecked must never read as covered."""
    frame = {"scripts": {"a.py": {"disposition": "promote", "target": REAL_TARGET}}}
    r = _f14(frame)
    assert r.state == cfi.CANNOT_RUN
    assert "COVERAGE was never checked" in r.detail


def test_F14_undispositioned_script_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py", "orphan.py")
        r = _f14({"scripts": {"a.py": {"disposition": "engagement_only",
                                       "reason": "policy, not mechanism"}}}, fp)
    assert r.state == cfi.FAIL
    # The specifics live in `offenders`; `detail` is the count. A FAIL that does not NAME
    # the offending file forces a human to go find it, which is how a gate gets ignored.
    blob = " ".join(r.offenders)
    assert "orphan.py" in blob
    assert "undispositioned" in blob


def test_F14_unknown_disposition_value_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "maybe_later"}}}, fp)
    assert r.state == cfi.FAIL


def test_F14_promote_without_a_target_fails():
    """A disposition with no required field is a label, not a decision."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "promote"}}}, fp)
    assert r.state == cfi.FAIL


def test_F14_engagement_only_without_a_reason_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "engagement_only"}}}, fp)
    assert r.state == cfi.FAIL


def test_F14_fully_dispositioned_tree_passes():
    """A promoted script is RETIRED, so it is declared and absent; `superseded` and
    `engagement_only` legitimately stay on disk. Before the 2026-09-21 amendment this
    fixture kept a promoted source in the tree and still passed."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "b.py", "c.py")
        r = _f14({"scripts": {
            "b.py": {"disposition": "superseded", "target": REAL_TARGET},
            "c.py": {"disposition": "engagement_only", "reason": "policy, not mechanism"},
        }}, fp)
    assert r.state == cfi.PASS
    assert "2 script(s) on disk dispositioned" in r.detail


def test_F14_stale_declaration_is_noted_but_not_fatal():
    """Declared, absent on disk: the registry drifted. Never silent."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {
            "a.py": {"disposition": "engagement_only", "reason": "policy"},
            "deleted.py": {"disposition": "promote", "target": REAL_TARGET},
        }}, fp)
    assert r.state == cfi.PASS
    assert "deleted.py" in r.detail and "absent on disk" in r.detail


def test_F14_non_mapping_entry_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": "promote"}}, fp)
    assert r.state == cfi.FAIL


# --- F14 regressions from adversarial review, 2026-09-21 -----------------------------


def test_F14_sees_non_python_scripts():
    """`d.glob("*.py")` silently pre-filtered the population to top-level Python, so a
    shell script beside the frame read as 'the tree agrees: produced none'."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "scripts"
        d.mkdir()
        (d / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        r = _f14({"scripts": {}}, Path(td) / "frame.yaml")
    assert r.state == cfi.FAIL
    assert "run.sh" in " ".join(r.offenders)


def test_F14_sees_scripts_in_subdirectories():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "scripts" / "nested"
        d.mkdir(parents=True)
        (d / "deep.py").write_text("x = 1\n", encoding="utf-8")
        r = _f14({"scripts": {}}, Path(td) / "frame.yaml")
    assert r.state == cfi.FAIL
    assert "nested/deep.py" in " ".join(r.offenders)


def test_F14_ignores_pycache_and_dotfiles():
    """Junk must not be dispositionable, or the check trains people to ignore it."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "scripts"
        (d / "__pycache__").mkdir(parents=True)
        (d / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")
        (d / ".DS_Store").write_text("", encoding="utf-8")
        r = _f14({"scripts": {}}, Path(td) / "frame.yaml")
    assert r.state == cfi.PASS


def test_F14_absent_frame_directory_is_cannot_run_not_verified_empty():
    """`enumerated = True` was set unconditionally when scripts/ was not a directory, so
    a mistyped path read as a verified empty tree."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = _f14({"scripts": {}}, Path(td) / "no_such_dir" / "frame.yaml")
    assert r.state == cfi.CANNOT_RUN
    assert "does not exist" in r.detail


def test_F14_scripts_path_that_is_a_file_is_cannot_run():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "scripts").write_text("not a directory\n", encoding="utf-8")
        r = _f14({"scripts": {}}, Path(td) / "frame.yaml")
    assert r.state == cfi.CANNOT_RUN
    assert "not a directory" in r.detail


def test_F14_parent_exists_with_no_scripts_dir_is_a_verified_absence():
    """The branch every other test skipped.

    `_tree()` always mkdir's scripts/, so the "readable parent, genuinely no scripts
    directory" path was never exercised and a mutant flipping it survived. That case is
    the ONLY absence this check may treat as verified: the parent is readable, so the
    absence was observed rather than assumed.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        frame_path = Path(td) / "frame.yaml"       # note: NO scripts/ created
        assert not (Path(td) / "scripts").exists()
        r = _f14({"scripts": {}}, frame_path)
    assert r.state == cfi.PASS
    assert "produced none" in r.detail


def test_F14_clean_tree_carries_no_stale_note():
    """Nothing declared-but-absent must produce NO note at all.

    A mutant forcing the stale branch on appended 'NOTE 0 declared but absent on disk:'
    to a clean PASS and every existing assertion still held, because they all checked for
    the presence of the good text and never for the absence of noise. A verdict that
    reports a defect count of zero as a defect trains the reader to skim.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "engagement_only",
                                       "reason": "policy"}}}, fp)
    assert r.state == cfi.PASS
    assert "absent on disk" not in r.detail
    assert "NOTE" not in r.detail


def test_F14_promote_target_that_does_not_exist_fails():
    """A disposition is a DECISION; a target that was never created is an unfinished one.

    Without this, `target: tools/foo.py` is a note-to-self: the frame records that a
    script should be promoted and nothing ever checks that it was. Six such entries sat
    in a real frame reading PASS.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": "tools/does_not_exist_anywhere.py"}}}, fp)
    assert r.state == cfi.FAIL
    assert "never carried out" in " ".join(r.offenders)


def test_F14_superseded_target_must_exist_too():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "superseded",
                                       "target": "tools/nope_not_here.py"}}}, fp)
    assert r.state == cfi.FAIL


def test_F14_bare_filename_target_is_not_treated_as_a_repo_path():
    """`target: build_workbook.py` names a SIBLING script, not a repo-root path.

    Treating it as a repo path would fail every 'superseded by another script in this
    same directory' disposition, which is the common case.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "superseded",
                                       "target": "build_workbook.py"}}}, fp)
    assert r.state == cfi.PASS


def test_F14_engagement_only_needs_no_target():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "engagement_only",
                                       "reason": "policy, not mechanism"}}}, fp)
    assert r.state == cfi.PASS


# ---------------------------------------------------------------- F15
#
# The reverse of F2a: every number a surface PRINTS must be carried by an element
# declaring that surface. Fixtures are synthetic and generic by design (public repo).

F15_DECK = """<html><body>
<div class="slide"><h1>Widgets shipped 3,142 units</h1>
<p>and 28% of them arrived late</p>
<p class="src">Source: 010125-input.xlsx</p></div>
<div class="slide"><h1>The rate was 88.8%</h1></div>
</body></html>"""


def _deck(tmp_path, body=F15_DECK):
    p = tmp_path / "deck.html"
    p.write_text(body, encoding="utf-8")
    return p


def _f15_frame(because, surface="slide-1", facts=None):
    return {
        "schema_version": 3,
        "facts": facts if facts is not None else {
            "fA": {"text": "Widgets shipped 3,142 units", "tier": "A", "source": "x"},
            "fB": {"text": "28.0% arrived late", "tier": "A", "source": "x"},
        },
        "elements": [{"id": "e1", "name": "n", "measure": "m",
                      "name_surface": surface, "measure_surface": surface,
                      "because": because}],
    }


def test_F15_without_a_deck_is_cannot_run_not_pass(tmp_path):
    """The frame's own declarations are the thing under test. They cannot stand in for
    the artifact, so a missing deck is an untested rule, never a clean one."""
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), None)
    assert r.state == cfi.CANNOT_RUN


def test_F15_passes_when_every_printed_number_is_carried(tmp_path):
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), _deck(tmp_path))
    assert r.state == cfi.PASS, r.detail


def test_F15_catches_the_incident_shape(tmp_path):
    """THE REGRESSION. A number printed on slide 1 whose only citing element declares a
    different surface. This is the 2026-09-21 defect: the concurrency figure was on the
    page and the element carrying it declared `workbook`, and every gate stayed green."""
    frame = _f15_frame(["fA"])  # fB (the 28%) is no longer cited on slide-1
    frame["elements"].append({"id": "e2", "name": "n2", "measure": "m2",
                              "name_surface": "workbook",
                              "measure_surface": "workbook", "because": ["fB"]})
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.FAIL
    assert any("28%" in o for o in r.offenders)


def test_F15_normalizes_precision_so_46_0_carries_46(tmp_path):
    """The fact says 28.0% and the page prints 28%. Exact string matching misses the
    one case this rule was built for -- measured on the real deck."""
    frame = _f15_frame(["fA", "fB"])
    frame["facts"]["fB"]["text"] = "28.0% arrived late"
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS
    frame["facts"]["fB"]["text"] = "28% arrived late"
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS


def test_F15_normalizes_comma_grouping(tmp_path):
    frame = _f15_frame(["fA", "fB"])
    frame["facts"]["fA"]["text"] = "Widgets shipped 3142 units"
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS


def test_F15_does_not_merge_distinct_values(tmp_path):
    """No rounding tolerance. 28% and 29% are different claims."""
    frame = _f15_frame(["fA", "fB"])
    frame["facts"]["fB"]["text"] = "29% arrived late"
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.FAIL and any("28%" in o for o in r.offenders)


def test_F15_excludes_the_provenance_line(tmp_path):
    """A source citation is provenance, not a claim. Counting it would make every deck
    fail on its own filename date stamp -- measured on the real deck, where 091426 was
    reported as an undeclared number until the class was excluded."""
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), _deck(tmp_path))
    assert r.state == cfi.PASS
    assert not any("010125" in o for o in r.offenders)


def test_F15_ignores_bare_small_integers(tmp_path):
    """Step numbers and page furniture collide by construction. Including them fired 89
    times on a two-page deck."""
    deck = _deck(tmp_path, """<html><body><div class="slide">
    <p>Step 1 then step 2, and 3,142 units</p></div></body></html>""")
    frame = _f15_frame(["fA"])
    assert cfi.check_F15(frame, deck).state == cfi.PASS


def test_F15_only_checks_surfaces_the_frame_declares(tmp_path):
    """slide-2 prints 88.8% and no element declares slide-2. That is F15 staying silent
    on a surface outside its remit, not a pass on it."""
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), _deck(tmp_path))
    assert r.state == cfi.PASS
    assert "slide-2" not in r.detail


def test_F15_cannot_run_when_no_declared_surface_matches_the_deck(tmp_path):
    """A frame whose surfaces are all `workbook` cannot be tested against a deck. That
    is an untested rule, not a clean one."""
    r = cfi.check_F15(_f15_frame(["fA", "fB"], surface="workbook"), _deck(tmp_path))
    assert r.state == cfi.CANNOT_RUN


def test_F15_cannot_run_below_schema_v3(tmp_path):
    frame = _f15_frame(["fA", "fB"])
    frame["schema_version"] = 2
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.CANNOT_RUN


def test_F15_unreadable_deck_is_cannot_run_not_pass(tmp_path):
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), tmp_path / "nope.html")
    assert r.state == cfi.CANNOT_RUN


def test_F15_element_citing_nothing_does_not_carry_the_page(tmp_path):
    r = cfi.check_F15(_f15_frame([]), _deck(tmp_path))
    assert r.state == cfi.FAIL


def test_F15_runs_through_the_cli_with_a_deck(tmp_path):
    """The rule has to be reachable from the command line, or it is decoration."""
    frame = _f15_frame(["fA"])
    frame["elements"].append({"id": "e2", "name": "n2", "measure": "m2",
                              "name_surface": "workbook",
                              "measure_surface": "workbook", "because": ["fB"]})
    fp = tmp_path / "frame.yaml"
    fp.write_text(yaml.safe_dump(frame), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(fp), "--schema", str(SCHEMA),
         "--deck", str(_deck(tmp_path)), "--json"],
        capture_output=True, text=True)
    payload = json.loads(out.stdout)
    states = {r["rule"]: r["state"] for r in payload["checks"]}
    assert states["F15"] == "FAIL"


# --- F15 mutation-driven coverage ---------------------------------------------------
# Each test below exists because a mutant survived at that exact line. They are not
# decoration: revert the line they name and one of these dies.

def test_F15_norm_number_leaves_a_non_numeric_token_alone():
    """`return tok` on a token float() cannot parse. Without this the normalizer is
    only ever exercised on input it can parse, so its failure branch is untested."""
    assert cfi._norm_number("n/a") == "n/a"
    assert cfi._norm_number("%") == "%"


def test_F15_norm_number_keeps_a_real_decimal(tmp_path):
    """13.7% must not become 14%. The integer branch and the general branch produce the
    same string for 46.0, so only a genuine decimal distinguishes them."""
    assert cfi._norm_number("13.7%") == "13.7%"
    assert cfi._norm_number("28.0%") == "28%"


def test_F15_decimal_percentages_are_matched_not_rounded(tmp_path):
    deck = _deck(tmp_path, """<html><body><div class="slide">
    <p>the rate was 13.7%</p></div></body></html>""")
    frame = _f15_frame([], facts={"fA": {"text": "the rate was 13.7%"}})
    frame["elements"][0]["because"] = ["fA"]
    assert cfi.check_F15(frame, deck).state == cfi.PASS
    frame["facts"]["fA"]["text"] = "the rate was 14%"
    assert cfi.check_F15(frame, deck).state == cfi.FAIL


def test_F15_comma_grouped_numbers_are_read_off_the_page(tmp_path):
    """The comma-grouped pass is the only one that sees "3,142": the bare-integer regex
    refuses a token preceded by a comma, so dropping it makes the page look emptier and
    every finding on it silently disappear."""
    deck = _deck(tmp_path, """<html><body><div class="slide">
    <p>shipped 3,142 units</p></div></body></html>""")
    frame = _f15_frame([], facts={"fA": {"text": "unrelated"}})
    frame["elements"][0]["because"] = ["fA"]
    r = cfi.check_F15(frame, deck)
    assert r.state == cfi.FAIL
    assert any("3142" in o for o in r.offenders)


def test_F15_says_which_input_was_missing_when_no_deck_is_given():
    """A CANNOT_RUN that does not name what it needs is unactionable, and asserting only
    the state lets the branch be deleted while the state is reached another way."""
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), None)
    assert r.state == cfi.CANNOT_RUN
    assert "--deck" in r.detail


def test_F15_deck_with_no_pages_is_cannot_run_not_pass(tmp_path):
    deck = _deck(tmp_path, "<html><body><p>no slides here</p></body></html>")
    r = cfi.check_F15(_f15_frame(["fA", "fB"]), deck)
    assert r.state == cfi.CANNOT_RUN
    assert "no pages" in r.detail


def test_F15_frame_with_no_elements_is_cannot_run_not_pass(tmp_path):
    frame = _f15_frame(["fA"])
    frame["elements"] = []
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.CANNOT_RUN
    assert "elements" in r.detail


def test_F15_frame_with_no_facts_is_cannot_run_not_pass(tmp_path):
    frame = _f15_frame(["fA"])
    frame["facts"] = {}
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.CANNOT_RUN
    assert "facts" in r.detail


def test_F15_tolerates_an_element_with_no_surface(tmp_path):
    """An element that declares no surface carries nothing for any page. Reading its
    absent surface as a key would crash the gate on a partly-authored frame."""
    frame = _f15_frame(["fA", "fB"])
    frame["elements"].append({"id": "e2", "name": "n2", "measure": "m2",
                              "because": ["fA"]})
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS


def test_F15_tolerates_a_malformed_fact(tmp_path):
    """A fact that is a bare string rather than a mapping must not crash the rule; it
    simply carries nothing, and the numbers it would have carried are reported."""
    frame = _f15_frame(["fA", "fB"])
    frame["facts"]["fB"] = "28.0% arrived late"   # a string, not a mapping
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.FAIL
    assert any("28%" in o for o in r.offenders)


# --- F14 step 7: RETIRE THE ORIGINAL (2026-09-21 amendment) --------------------------

def test_F14_promoted_source_still_on_disk_fails():
    """THE AMENDMENT. F14 checked that the promotion TARGET exists and never that the
    SOURCE went away, so a promoted script could sit beside the frame as a full duplicate
    indefinitely. Three did on the frame this was built against, and one had drifted a
    whole check behind its promoted twin while the gate read clean."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": REAL_TARGET}}}, fp)
    assert r.state == cfi.FAIL
    assert any("still" in o and "on disk" in o for o in r.offenders)


def test_F14_promoted_and_retired_passes():
    """The other half: once the original is gone, the same declaration is clean."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td))          # empty tree
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": REAL_TARGET}}}, fp)
    assert r.state == cfi.PASS


def test_F14_unfinished_promotion_is_not_double_reported():
    """While the target does not yet exist the promotion is simply unfinished, and that
    is already reported. Firing 'retire the original' as well would demand deleting the
    only copy of a script whose replacement does not exist."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": "tools/does_not_exist_xyz.py"}}}, fp)
    assert r.state == cfi.FAIL
    assert len(r.offenders) == 1
    assert "does not" in r.offenders[0]


def test_F14_superseded_source_may_stay_on_disk():
    """`superseded` is not `promote`. A superseded script was replaced by something else
    in the same engagement and deleting it is not what the disposition means."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "superseded",
                                       "target": REAL_TARGET}}}, fp)
    assert r.state == cfi.PASS


def test_F14_retirement_check_needs_an_enumerated_tree():
    """Without a tree, 'still on disk' is unknowable. It must not be guessed either way."""
    r = _f14({"scripts": {"a.py": {"disposition": "promote", "target": REAL_TARGET}}})
    assert r.state == cfi.CANNOT_RUN


# --- also_printed_on (2026-09-21) ---------------------------------------------------

def test_F15_also_printed_on_accounts_for_a_setup_surface(tmp_path):
    """An element MADE on slide 2 whose counts are printed on slide 1. Before this field
    one element could name exactly one surface, so F15 reported real setup material as
    unaccounted."""
    # The real shape: slide-1 IS declared, by an element that carries only part of what
    # the page prints. The counts underneath belong to a DIFFERENT element, made on
    # slide 2. That element's facts are printed on slide 1 and accounted for nowhere.
    frame = _f15_frame(["fB"], surface="slide-1")           # carries 28% only
    frame["facts"]["fC"] = {"text": "The rate was 88.8%"}
    frame["elements"].append({"id": "e2", "name": "volume", "measure": "units",
                              "name_surface": "slide-2", "measure_surface": "slide-2",
                              "because": ["fA", "fC"]})     # fA holds 3,142
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.FAIL
    assert any("3142" in o and o.startswith("slide-1") for o in r.offenders), r.offenders

    frame["elements"][1]["also_printed_on"] = ["slide-1"]
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS


def test_F15_also_printed_on_only_covers_surfaces_it_lists(tmp_path):
    """Listing one surface must not account for every surface."""
    frame = _f15_frame(["fA", "fB"], surface="workbook")
    frame["elements"][0]["also_printed_on"] = ["slide-2"]
    r = cfi.check_F15(frame, _deck(tmp_path))
    assert r.state == cfi.FAIL          # slide-2 prints 88.8%, carried by neither fact
    assert all("slide-1" not in o for o in r.offenders)


def test_F1b_does_not_read_also_printed_on():
    """The asymmetry is deliberate. If F1b widened through this field an element could
    claim its measure sits on a surface it only supplies evidence to."""
    f = clean_frame()
    f["elements"][0]["measure_surface"] = "p12"
    f["elements"][0]["also_printed_on"] = ["p12"]
    assert states(f)["F1b"] == cfi.FAIL


def test_also_printed_on_entries_must_be_identifiers():
    """Unvalidated, this is the obvious way prose creeps back into a surface field -- and
    F15 then silently stops matching, which reads as the page being clean."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["elements"][0]["also_printed_on"] = ["the funnel panel on the opening page"]
    errs = cfi.validate_surface_identifiers(f, schema)
    assert errs and "also_printed_on" in errs[0]


def test_also_printed_on_must_be_a_list():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["elements"][0]["also_printed_on"] = "p5"
    errs = cfi.validate_surface_identifiers(f, schema)
    assert errs and "must be a list" in errs[0]


def test_also_printed_on_absent_is_fine():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert cfi.validate_surface_identifiers(clean_frame(), schema) == []


def test_also_printed_on_valid_list_produces_no_error():
    """The other half of the list check. Without this, a mutant that errors on EVERY
    also_printed_on entry survives, because every existing test feeds it bad input."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["elements"][0]["also_printed_on"] = ["p8", "board-left", "spoken"]
    assert cfi.validate_surface_identifiers(f, schema) == []


def test_also_printed_on_non_list_reports_exactly_one_error():
    """A string is iterable, so dropping the `continue` after the not-a-list error walks
    its CHARACTERS -- most of which match the identifier pattern, so the count stays
    plausible and the bug is invisible unless the count is asserted."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    # "P5" and not "p5": a lowercase string's characters each MATCH the identifier
    # pattern, so walking them adds no errors and the dropped `continue` is invisible.
    f["elements"][0]["also_printed_on"] = "P5"
    errs = cfi.validate_surface_identifiers(f, schema)
    assert len(errs) == 1, errs


def test_surface_error_truncates_only_long_prose():
    """The ellipsis is a length decision. Inverted, a short value grows a '...' and a long
    one loses it, and every assertion that only greps for 'identifier' still passes."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["elements"][0]["also_printed_on"] = ["x" * 80]
    assert "..." in cfi.validate_surface_identifiers(f, schema)[0]
    f["elements"][0]["also_printed_on"] = ["THIS IS PROSE"]
    assert "..." not in cfi.validate_surface_identifiers(f, schema)[0]


def test_F15_skips_a_non_mapping_element(tmp_path):
    """A malformed element must not crash the surface walk or silently carry facts."""
    frame = _f15_frame(["fA", "fB"])
    frame["elements"].append("not a mapping")
    assert cfi.check_F15(frame, _deck(tmp_path)).state == cfi.PASS


# ---------------------------------------------------------------- enum vocabulary
#
# `values:` in the schema was documentation exactly like `required:` was. _shape_of maps
# `enum` to `str`, so any string passed. Found on the live frame 2026-09-21: `status:
# submitted` sat in a field whose vocabulary is in_progress|awaiting_outcome|complete|
# abandoned, and passed every gate for a day.

def test_enum_value_outside_the_vocabulary_is_a_structural_error():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["status"] = "submitted"
    errs = cfi.validate_enums(f, schema)
    assert errs and "submitted" in errs[0] and "awaiting_outcome" in errs[0]


def test_enum_value_inside_the_vocabulary_is_clean():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    for v in ("in_progress", "awaiting_outcome", "complete", "abandoned"):
        f["status"] = v
        assert cfi.validate_enums(f, schema) == [], v


def test_enum_absent_field_is_not_an_error():
    """A frame is legitimately incomplete for most of its life."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f.pop("status", None)
    assert cfi.validate_enums(f, schema) == []


def test_enum_checks_map_values_and_not_map_keys():
    """d1.metric_roles is map[metric -> enum]. The keys are real metric names and
    checking them would reject every frame; the VALUES carry the vocabulary."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["d1"]["metric_roles"] = {"some_unusual_metric_name": "guardrail"}
    assert cfi.validate_enums(f, schema) == []
    f["d1"]["metric_roles"] = {"throughput": "objective"}      # not in the vocabulary
    errs = cfi.validate_enums(f, schema)
    assert errs and "objective" in errs[0]


def test_enum_covers_every_vocabulary_the_schema_declares():
    """Not just status. A future `values:` list must be enforced without a code edit."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    f = clean_frame()
    f["d1"]["problem_type"] = "not_a_problem_type"
    f["recommendation"]["confidence"] = "quite sure"
    errs = cfi.validate_enums(f, schema)
    assert len(errs) == 2, errs


def test_enum_violation_blocks_the_whole_run_as_structural():
    """It must be STRUCTURAL, not a rule FAIL: frame_write refuses a candidate with
    structural errors and lets rule FAILs through, and this has to stop the write."""
    f = clean_frame()
    f["status"] = "submitted"
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert any("submitted" in e for e in cfi.validate_structure(f, schema))


def test_enum_violation_makes_the_cli_refuse(tmp_path):
    f = clean_frame()
    f["status"] = "submitted"
    r = run(write(tmp_path, f))
    payload = json.loads(r.stdout)
    assert payload["structural_errors"]
    assert payload["clean"] is False
    assert r.returncode != 0


# --- validate_enums against SYNTHETIC schemas ---------------------------------------
# The real schema exercises only the scalar and map[] shapes, so the list[] branch and
# every defensive guard were unreachable from the live file and 14 mutants survived in
# them. A branch no test can reach is enforcement that is not there.

def test_enums_tolerate_a_schema_with_no_fields_block():
    assert cfi.validate_enums({"status": "x"}, {}) == []
    assert cfi.validate_enums({"status": "x"}, {"fields": "not a dict"}) == []


def test_enums_skip_a_malformed_field_spec():
    schema = {"fields": {"status": "not a mapping"}}
    assert cfi.validate_enums({"status": "anything"}, schema) == []


def test_enums_skip_a_field_with_no_values_list():
    schema = {"fields": {"status": {"type": "enum"}}}
    assert cfi.validate_enums({"status": "anything"}, schema) == []


def test_enums_check_every_entry_of_a_list_field():
    schema = {"fields": {"tags": {"type": "list[enum]", "values": ["a", "b"]}}}
    assert cfi.validate_enums({"tags": ["a", "b"]}, schema) == []
    errs = cfi.validate_enums({"tags": ["a", "zzz", "qqq"]}, schema)
    assert len(errs) == 2 and "zzz" in errs[0] and "qqq" in errs[1]


def test_enums_skip_a_list_field_holding_the_wrong_shape():
    """A wrong SHAPE is validate_structure's job. Reporting it here too would double it,
    and iterating a string would report each character as a bad enum value."""
    # "zz" and not "a": a string whose characters ARE in the vocabulary makes the
    # dropped guard invisible, because iterating it reports nothing.
    schema = {"fields": {"tags": {"type": "list[enum]", "values": ["a"]}}}
    assert cfi.validate_enums({"tags": "zz"}, schema) == []


def test_enums_skip_a_map_field_holding_the_wrong_shape():
    schema = {"fields": {"roles": {"type": "map[k -> enum]", "values": ["a"]}}}
    assert cfi.validate_enums({"roles": ["a"]}, schema) == []


def test_enums_report_every_bad_map_value_not_just_the_first():
    schema = {"fields": {"roles": {"type": "map[k -> enum]", "values": ["target"]}}}
    errs = cfi.validate_enums({"roles": {"m1": "bad1", "m2": "bad2"}}, schema)
    assert len(errs) == 2


def test_enums_treat_a_present_null_as_absent():
    """`status: ` with nothing after it is 'not authored yet', not a vocabulary breach."""
    schema = {"fields": {"status": {"type": "enum", "values": ["a"]}}}
    assert cfi.validate_enums({"status": None}, schema) == []


def test_enums_compare_as_strings_so_a_yaml_bool_is_caught():
    """YAML turns `no` into False. A vocabulary of strings must still reject it rather
    than pass it through some accidental equality."""
    schema = {"fields": {"flag": {"type": "enum", "values": ["no", "yes"]}}}
    assert cfi.validate_enums({"flag": False}, schema) != []


# ---------------------------------------------------------------- F16: delivery
# `locked: true` cannot be claimed after the fact -- the lock path demands a prediction,
# and a prediction is contaminated the instant feedback arrives. A frame whose artifact
# already shipped needs a way to say WHICH version went out without claiming a ritual it
# never performed.

def test_F16_absent_delivery_is_cannot_run_not_pass():
    r = cfi.check_F16(clean_frame())
    assert r.state == cfi.CANNOT_RUN
    assert "either way" in r.detail


def test_F16_records_a_delivery():
    f = clean_frame()
    f["delivery"] = {"version": 2, "at": "2026-09-20"}
    r = cfi.check_F16(f)
    assert r.state == cfi.PASS and "v2" in r.detail


def test_F16_retrospective_requires_a_basis():
    """A version reconstructed after the fact, with no stated basis, is a guess wearing
    the authority of a field. Same shape as status_reason being mandatory for abandoned."""
    f = clean_frame()
    f["delivery"] = {"version": 2, "at": "2026-09-20", "retrospective": True}
    r = cfi.check_F16(f)
    assert r.state == cfi.FAIL and any("basis" in o for o in r.offenders)
    f["delivery"]["basis"] = "confirmed in the 09-20 log"
    assert cfi.check_F16(f).state == cfi.PASS


def test_F16_a_non_retrospective_delivery_needs_no_basis():
    f = clean_frame()
    f["delivery"] = {"version": 2, "at": "2026-09-20", "retrospective": False}
    assert cfi.check_F16(f).state == cfi.PASS


def test_F16_says_which_kind_of_record_it_is():
    """A reader must be able to tell a witnessed delivery from a reconstructed one."""
    f = clean_frame()
    f["delivery"] = {"version": 2, "at": "2026-09-20"}
    assert "recorded at the time" in cfi.check_F16(f).detail
    f["delivery"]["retrospective"] = True
    f["delivery"]["basis"] = "log"
    assert "retrospective" in cfi.check_F16(f).detail


def test_F16_rejects_a_version_ahead_of_the_current_one():
    f = clean_frame()                      # version 2
    f["delivery"] = {"version": 9, "at": "2026-09-20"}
    r = cfi.check_F16(f)
    assert r.state == cfi.FAIL and any("ahead" in o for o in r.offenders)


def test_F16_requires_a_version_and_a_date():
    f = clean_frame()
    f["delivery"] = {}
    r = cfi.check_F16(f)
    assert r.state == cfi.FAIL and len(r.offenders) == 2


def test_F16_rejects_a_non_int_version():
    f = clean_frame()
    f["delivery"] = {"version": "44", "at": "2026-09-20"}
    assert cfi.check_F16(f).state == cfi.FAIL


def test_F16_rejects_a_bool_version():
    """True is an int in Python. A bool here is a mis-keyed field, not version 1."""
    f = clean_frame()
    f["delivery"] = {"version": True, "at": "2026-09-20"}
    assert cfi.check_F16(f).state == cfi.FAIL


def test_F16_rejects_a_non_mapping_delivery():
    f = clean_frame()
    f["delivery"] = "2026-09-20"
    assert cfi.check_F16(f).state == cfi.FAIL


# --- F13 after a delivery ------------------------------------------------------------

def test_F13_reports_a_permanently_lost_prediction_after_delivery():
    """An unlocked frame that ALREADY DELIVERED is not 'still open'. Reporting it that
    way is how an engagement reads as pending forever. The prediction is permanently
    gone and that must be said once, not deferred by a CANNOT_RUN every run."""
    f = clean_frame()
    f["locked"] = False
    f["prediction"] = None
    f["delivery"] = {"version": 2, "at": "2026-09-20", "retrospective": True,
                     "basis": "log"}
    r = cfi.check_F13(f)
    assert r.state == cfi.FAIL
    assert any("permanently lost" in o for o in r.offenders)
    assert any("do NOT author one now" in o for o in r.offenders)


def test_F13_delivery_with_a_real_prediction_is_not_a_failure():
    """A frame that stamped its prediction BEFORE delivering has lost nothing."""
    f = clean_frame()
    f["locked"] = False
    f["delivery"] = {"version": 2, "at": "2026-09-20"}
    r = cfi.check_F13(f)              # clean_frame carries a real prediction
    assert r.state == cfi.CANNOT_RUN


def test_F13_without_a_delivery_is_still_open():
    f = clean_frame()
    f["locked"] = False
    f["prediction"] = None
    r = cfi.check_F13(f)
    assert r.state == cfi.CANNOT_RUN and "still open" in r.detail


def test_F13_a_locked_frame_is_unaffected_by_delivery():
    f = clean_frame()                 # locked: True, full run record
    f["delivery"] = {"version": 2, "at": "2026-09-20"}
    assert cfi.check_F13(f).state == cfi.PASS


# --- cross-model review 2026-09-22: four P0s and a P1 in this session's own gates ----

def test_F15_does_not_pass_when_it_recognized_no_number(tmp_path):
    """F1 (P0). It printed 'all 0 number(s) ... are carried' -- a green verdict on a page
    it measured nothing about, which is the vacuous pass the three-state design refuses.
    This falsified a claim I had made explicitly: that F15 could not pass vacuously."""
    deck = _deck(tmp_path, "<html><body><div class='slide'><h1>All prose here</h1>"
                           "</div></body></html>")
    r = cfi.check_F15(_f15_frame(["fA"]), deck)
    assert r.state == cfi.CANNOT_RUN
    assert "measured nothing" in r.detail


def test_F15_notes_an_unmeasured_surface_but_still_checks_the_others(tmp_path):
    """A page may legitimately print no figures. That must not silence the page that does."""
    deck = _deck(tmp_path, "<html><body>"
                           "<div class='slide'><h1>prose only</h1></div>"
                           "<div class='slide'><p>shipped 9,999 units</p></div>"
                           "</body></html>")
    frame = _f15_frame(["fA"], surface="slide-1")
    frame["elements"].append({"id": "e2", "name": "n2", "measure": "m2",
                              "name_surface": "slide-2", "measure_surface": "slide-2",
                              "because": ["fA"]})
    r = cfi.check_F15(frame, deck)
    assert r.state == cfi.FAIL                       # slide-2 prints 9999, uncarried
    assert any("9999" in o for o in r.offenders)


def test_claim_numbers_keeps_the_sign():
    """F2 (P0). -5% and +5% are different claims; the regex dropped the sign."""
    assert cfi._claim_numbers("down -5.0%") != cfi._claim_numbers("up 5.0%")
    assert "-5%" in cfi._claim_numbers("down -5.0%")


def test_claim_numbers_does_not_truncate_a_grouped_decimal():
    """F2 (P0). `\\d{1,3}(?:,\\d{3})+` matched "1,234" out of "1,234.56" and threw the
    tail away, collapsing two different printed quantities onto one token."""
    a = cfi._claim_numbers("1,234.56")
    b = cfi._claim_numbers("1,234.99")
    assert a != b and "1234.56" in a and "1234.99" in b


def test_fact_stamps_reject_a_non_mapping_fact():
    """F3 (P0). A malformed fact was SKIPPED, so it bypassed the write guard entirely."""
    assert cfi.validate_fact_stamps({"facts": {"fA": "a string"}})


def test_fact_stamps_reject_a_non_integer_stamp():
    """F3 (P0). Any non-null value counted as a stamp, so `first_seen: "soon"` passed
    while being unusable by F2b and by the delivery comparison."""
    assert cfi.validate_fact_stamps({"facts": {"fA": {"first_seen": "soon"}}})
    assert cfi.validate_fact_stamps({"facts": {"fA": {"first_seen": 1.5}}})


def test_fact_stamps_reject_a_bool_stamp():
    """True is an int in Python. A mis-keyed flag must not read as version 1."""
    assert cfi.validate_fact_stamps({"facts": {"fA": {"first_seen": True}}})
    assert cfi.validate_fact_stamps({"facts": {"fA": {"first_seen": 3}}}) == []


def test_F14_bare_sibling_target_still_requires_retirement():
    """F4 (P0). `continue` on a bare filename skipped the repo-path existence test AND
    the retirement check below it, so a promote naming a sibling was exempt from both."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td), "a.py")
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": "sibling.py"}}}, fp)
    assert r.state == cfi.FAIL
    assert any("still" in o and "on disk" in o for o in r.offenders)


def test_F14_bare_sibling_target_is_clean_once_retired():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = _tree(Path(td))
        r = _f14({"scripts": {"a.py": {"disposition": "promote",
                                       "target": "sibling.py"}}}, fp)
    assert r.state == cfi.PASS


def test_F16_cannot_verify_a_delivery_version_without_a_frame_version():
    """F5 (P1). The ahead-of-current comparison was skipped when `version` was absent,
    so an arbitrary delivery version PASSED unchecked."""
    r = cfi.check_F16({"schema_version": 3, "delivery": {"version": 9999, "at": "2026-09-20"}})
    assert r.state == cfi.FAIL
    assert any("cannot be checked" in o for o in r.offenders)


def test_fact_stamps_report_a_plainly_missing_stamp():
    """The None branch itself. Every other stamp test feeds a WRONG type; without this
    the ordinary missing-stamp case -- the one the rule was built for -- is untested."""
    errs = cfi.validate_fact_stamps({"facts": {"fA": {"text": "x"}}})
    assert errs and "fA" in errs[0]


def test_fact_stamps_on_a_frame_with_no_facts_is_not_an_error():
    """A frame is legitimately factless early in its life; that is F2a's business."""
    assert cfi.validate_fact_stamps({"facts": {}}) == []
    assert cfi.validate_fact_stamps({}) == []


def test_fact_stamps_truncate_a_long_offender_list_with_a_count():
    """The cap must cap AND say how many it hid, or a 60-fact frame prints a wall."""
    facts = {f"f{i}": {"text": "x"} for i in range(12)}
    errs = cfi.validate_fact_stamps({"facts": facts})
    assert "+4 more" in errs[0]
    few = cfi.validate_fact_stamps({"facts": {f"f{i}": {"text": "x"} for i in range(3)}})
    assert "more" not in few[0]


def test_F15_note_appears_only_when_a_surface_was_unmeasured(tmp_path):
    """Both directions of the NOTE. Forced on, a clean two-page run grows a spurious
    'no recognized number on' clause; forced off, the unmeasured page vanishes silently
    and the reader cannot tell partial coverage from full."""
    deck = _deck(tmp_path, "<html><body>"
                           "<div class='slide'><h1>prose only</h1></div>"
                           "<div class='slide'><p>shipped 3,142 units</p></div>"
                           "</body></html>")
    frame = _f15_frame(["fA"], surface="slide-2")
    frame["elements"].append({"id": "e2", "name": "n2", "measure": "m2",
                              "name_surface": "slide-1", "measure_surface": "slide-1",
                              "because": ["fA"]})
    r = cfi.check_F15(frame, deck)
    assert r.state == cfi.PASS, r.offenders
    assert "no recognized number on slide-1" in r.detail

    # and with every covered surface measurable, no NOTE at all
    r2 = cfi.check_F15(_f15_frame(["fA", "fB"]), _deck(tmp_path))
    assert r2.state == cfi.PASS and "no recognized number" not in r2.detail


def test_F16_missing_delivery_version_is_reported_not_skipped():
    """The `dv is None` branch. Without it a delivery block with only a date reads as a
    recorded delivery, which is the field's whole value missing."""
    r = cfi.check_F16({"schema_version": 3, "version": 2, "delivery": {"at": "2026-09-20"}})
    assert r.state == cfi.FAIL
    assert any("version" in o for o in r.offenders)
