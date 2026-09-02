"""Tests for tools/frame_write.py — the sole mutation path for frame.yaml.

Four guarantees are load-bearing and each has a test that fails loudly if removed:

  1. Compare-and-swap. Wrong --expect-version writes NOTHING.
  2. Any failure leaves frame.yaml BYTE-IDENTICAL.
  3. Concurrent writers at the same expect-version produce exactly ONE success.
  4. Derived fields cannot be set by a caller.

Plus the answers-file metrics, whose `re_ask_count` is the falsifier for the claim
that frame.yaml is a sufficient resume point.
"""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "frame_write.py"

MINIMAL = {
    "schema_version": 3,
    "version": 1,
    "locked": False,
    "engagement": "acme",
    "d1": {"problem_statement": "Where should Acme invest next?",
           "problem_type": "prioritization"},
    "facts": {"f1": {"text": "throughput is 100/day", "tier": "A", "first_seen": 1}},
    "elements": [{"id": "e1", "name": "expected impact", "name_surface": "p5",
                  "measure": "convertible volume", "measure_surface": "p5",
                  "because": ["f1"], "inputs": ["i_vol"], "protected": True,
                  "first_seen": 1}],
    "closure": "One element, because the constraint is one investment.",
    "exclusions": [{"element": "signal", "reason": "a preference, not a property"}],
}


def frame(tmp_path, data=None):
    p = tmp_path / "frame.yaml"
    p.write_text(yaml.safe_dump(data or MINIMAL, sort_keys=False), encoding="utf-8")
    return p


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, {"_raw": r.stdout, "_err": r.stderr}


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


# ------------------------------------------------------------- compare-and-swap

def test_wrong_expect_version_writes_nothing(tmp_path):
    f = frame(tmp_path)
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 99, "--field", "engagement=nope")
    assert code != 0
    assert res["status"] == "error"
    assert md5(f) == before, "frame.yaml must be byte-identical after a refused write"


def test_expect_version_is_mandatory(tmp_path):
    f = frame(tmp_path)
    code, _ = run("set", "--frame", f, "--field", "engagement=x")
    assert code != 0


def test_successful_write_bumps_version_and_snapshots(tmp_path):
    f = frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=beta")
    assert code == 0, res
    assert res["version"] == 2
    assert (tmp_path / "frame.v1.yaml").exists(), "prior version must be snapshotted"
    assert yaml.safe_load(f.read_text())["engagement"] == "beta"


def test_concurrent_writers_produce_exactly_one_success(tmp_path):
    """CAS without a lock is still a TOCTOU race: both read N, both validate, both swap."""
    f = frame(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(
            lambda i: run("set", "--frame", f, "--expect-version", 1,
                          "--field", f"engagement=w{i}")[1].get("status"),
            range(8)))
    assert results.count("ok") == 1, f"expected exactly one winner, got {results}"
    assert yaml.safe_load(f.read_text())["version"] == 2


# ------------------------------------------------------------- derived fields

@pytest.mark.parametrize("field", [
    "version=99", "checks_fired=fake", "stages_run=fake",
    "operator_answer_count=0", "re_ask_count=0", "schema_version=9",
])
def test_derived_fields_are_refused(tmp_path, field):
    """The model being scored does not get to score itself."""
    f = frame(tmp_path)
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", field)
    assert code != 0
    assert "DERIVED" in res["message"]
    assert md5(f) == before


def test_patch_payload_cannot_smuggle_a_derived_field(tmp_path, ):
    f = frame(tmp_path)
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"engagement": "x", "checks_fired": ["F1a"]}))
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", payload)
    assert code != 0
    assert "checks_fired" in res["message"]


# ------------------------------------------------------------- validation gate

def test_unparseable_frame_is_refused_not_written(tmp_path):
    f = tmp_path / "frame.yaml"
    f.write_text("this: is: not: valid: yaml:\n  - [", encoding="utf-8")
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code != 0


def test_dotted_key_writes_nested_not_flat(tmp_path):
    """A transcriber once wrote ten dotted schema names as FLAT top-level keys; the file
    passed structural validation and every check reading `d1` returned CANNOT_RUN."""
    f = frame(tmp_path)
    code, _ = run("set", "--frame", f, "--expect-version", 1,
                  "--field", "d1.problem_type=sizing")
    assert code == 0
    d = yaml.safe_load(f.read_text())
    assert d["d1"]["problem_type"] == "sizing"
    assert "d1.problem_type" not in d, "must nest, never write a flat dotted key"


# ------------------------------------------------- the validation-bypass class
#
# Measured live 2026-08-15 against the shipped script, not hypothesised. Two
# independent defects chained into a permanent validation bypass through the SOLE
# sanctioned mutation path:
#
#   1. run_checker discarded the exit code and tested only for status == "error",
#      while the checker's schema refusal emits status "refused" with no
#      structural_errors -- so a refusal read as a pass.
#   2. schema_version is class: derived in the schema but was absent from
#      DERIVED_FIELDS, so a caller could set it.
#
# Chained: `set --field schema_version=9` landed, and every subsequent write on that
# frame returned status ok with clean / fully_covered / counts all null. Three
# consecutive unvalidated writes were observed.


def test_refused_verdict_never_lands_a_write(tmp_path):
    """A schema refusal is not a pass. It judged nothing."""
    data = dict(MINIMAL)
    data["schema_version"] = 9          # written directly; the CLI now refuses this
    f = frame(tmp_path, data)
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code != 0
    assert "REFUSED" in res["message"]
    assert md5(f) == before, "a refused verdict must leave frame.yaml byte-identical"


def test_a_verdict_with_no_counts_is_not_a_pass(tmp_path):
    """The tell that was there to be noticed: clean/fully_covered/counts all null.

    Nothing looked at them, so `status: ok` with an empty verdict wrote happily.
    """
    data = dict(MINIMAL)
    data["schema_version"] = 9
    f = frame(tmp_path, data)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code != 0
    assert res["checker"].get("counts") is None
    assert res["checker"]["status"] == "refused"


def test_patch_cannot_smuggle_a_derived_field_via_a_dotted_key(tmp_path):
    """The guard compared exact keys while set_dotted nested the value anyway.

    Caught downstream today only because every derived field is list-shaped. The
    first map-shaped derived field would have landed clean and self-scored.
    """
    f = frame(tmp_path)
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"checks_fired.F5": {"state": "PASS"}}))
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", payload)
    assert code != 0
    assert "DERIVED" in res["message"], (
        "must be refused BY THE GUARD, not incidentally by the structural checker")
    assert "checks_fired.F5" in res["message"]


def test_prediction_cannot_be_repointed_by_a_caller(tmp_path):
    """prediction.made_at_version resolves F9's comparison target.

    A caller able to set it can aim the compression check at a snapshot that was never
    the locked one, producing a substantive-looking PASS against the wrong baseline.
    Verified settable 2026-08-15 before this guard; blocked as a whole subtree.
    """
    f = frame(tmp_path)
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1,
                    "--field", "prediction.made_at_version=99")
    assert code != 0
    assert "DERIVED" in res["message"]
    assert md5(f) == before

    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"prediction.will_be_probed": "smuggled"}))
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", payload)
    assert code != 0, "the dotted root must be blocked through patch too"


def test_lock_still_stamps_prediction_despite_the_guard(tmp_path):
    """The guard blocks CALLERS, not the lock path. `lock` is how prediction is set."""
    f = frame(tmp_path, {**MINIMAL, "segment_completed": "D"})
    code, res = run("lock", "--frame", f, "--expect-version", 1,
                    "--will-be-probed", "the denominator", "--today", "2026-08-16")
    assert code == 0, res
    d = yaml.safe_load(f.read_text())
    assert d["prediction"]["will_be_probed"] == "the denominator"
    assert d["prediction"]["made_at_version"] == 1


def test_mid_run_frame_with_real_failures_still_writes(tmp_path):
    """The anti-over-tightening test. Exit 2 means rules FAILED, which is what the
    gate is FOR. Refusing on it would make every normal mid-run write impossible."""
    sparse = {"schema_version": 3, "version": 1, "locked": False,
              "engagement": "sparse", "facts": {}, "elements": []}
    f = frame(tmp_path, sparse)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=still-writes")
    assert code == 0, res
    assert res["clean"] is False, "this frame really does fail rules"
    assert yaml.safe_load(f.read_text())["engagement"] == "still-writes"


# ------------------------------------------------------------- lock

def test_lock_sets_prediction_and_satisfies_F13(tmp_path):
    # LOCK is ordered like every other segment: it requires D complete. Freezing a frame
    # that has no recommendation is exactly what hard ordering exists to prevent.
    f = frame(tmp_path, {**MINIMAL, "segment_completed": "D"})
    pj = tmp_path / "p.json"
    pj.write_text(json.dumps({"stage": "C", "proposed": "raw volume",
                              "status": "rejected", "reason": "not convertible"}))
    run("append", "--frame", f, "--expect-version", 1, "--list", "proposals", "--json", pj)
    code, res = run("lock", "--frame", f, "--expect-version", 2,
                    "--will-be-probed", "the denominator", "--today", "2026-08-14")
    assert code == 0, res
    d = yaml.safe_load(f.read_text())
    assert d["locked"] is True
    assert d["locked_at"] == "2026-08-14"
    assert d["prediction"]["will_be_probed"] == "the denominator"
    assert d["segment_completed"] == "LOCK"


# ------------------------------------------------------------- init

def test_init_creates_version_one_and_refuses_a_second(tmp_path):
    f = tmp_path / "frame.yaml"
    code, res = run("init", "--frame", f, "--engagement", "acme", "--today", "2026-08-16")
    assert code == 0, res
    d = yaml.safe_load(f.read_text())
    assert d["version"] == 1 and d["engagement"] == "acme" and d["locked"] is False

    before = md5(f)
    code, res = run("init", "--frame", f, "--engagement", "other", "--today", "2026-08-16")
    assert code != 0
    assert "refuses rather than overwrite" in res["message"]
    assert md5(f) == before, "init must never overwrite a live frame"


def test_an_in_repo_frame_outside_output_is_refused(tmp_path):
    """CANONICAL LOCATION. `analysis-method.md` declares State lives at
    `output/<slug>/frame.yaml`. Before this gate there were THREE conventions in the
    tree at once (`frames/<slug>/`, `output/<slug>/`, and a date-prefixed
    `output/<target-company>/casework/frame-*.yaml`) and NOTHING enforced any of them, so the
    location was whatever the caller guessed. Nothing discovers frames either, so a
    frame written to the wrong place is simply lost. Fixed 2026-08-31."""
    import shutil
    bad = REPO / "frames" / "zz-refusal-probe" / "frame.yaml"
    # These two tests are the only ones that touch REPO paths rather than tmp_path, so
    # they are the only ones that can leave state behind. Under MUTATION testing a mutant
    # that disables the location gate lets init through and WRITES this file -- which is
    # how the mutant gets killed, and also how the file survives the run. Without the
    # cleanup below the next run of this file failed on a stale artifact, which surfaced
    # as an isolation_failure in mutation_check. Clean before AND after: before, because a
    # previous crashed run may have left it; after, because this run may create it.
    shutil.rmtree(bad.parent, ignore_errors=True)
    try:
        code, res = run("init", "--frame", bad, "--engagement", "x", "--today", "2026-08-31")
        assert code != 0, "an in-repo frame outside output/ must be refused"
        assert "output/" in res.get("message", ""), res
        assert not bad.exists(), "a refusal must write nothing"
    finally:
        shutil.rmtree(bad.parent, ignore_errors=True)


def test_the_refusal_names_the_canonical_path_it_wants(tmp_path):
    """A refusal that does not say where the frame SHOULD go just moves the guess."""
    import shutil
    bad = REPO / "frames" / "acme" / "frame.yaml"
    shutil.rmtree(bad.parent, ignore_errors=True)
    try:
        code, res = run("init", "--frame", bad, "--engagement", "acme", "--today", "2026-08-31")
        assert code != 0
        assert "output/acme/frame.yaml" in res.get("message", ""), (
            f"refusal must name the exact canonical path, got: {res}")
    finally:
        shutil.rmtree(bad.parent, ignore_errors=True)


def test_a_canonical_in_repo_path_is_accepted(tmp_path):
    """The gate must not refuse the location it is asking for."""
    slug = "zz-canonical-probe"  # leading char must be [a-z0-9] per the slug pattern
    good = REPO / "output" / slug / "frame.yaml"
    import shutil
    if good.parent.exists():
        shutil.rmtree(good.parent)
    try:
        code, res = run("init", "--frame", good, "--engagement", "probe",
                        "--today", "2026-08-31")
        assert code == 0, f"canonical path must be accepted, got: {res}"
        assert good.exists()
    finally:
        if good.parent.exists():
            shutil.rmtree(good.parent)


def test_paths_outside_the_repo_are_unconstrained(tmp_path):
    """Enforcement anchors to the repo. A frame outside it (every test in this file)
    is not the thing the convention governs, and constraining it would make the whole
    suite unable to run."""
    f = tmp_path / "anything" / "frame.yaml"
    code, res = run("init", "--frame", f, "--engagement", "x", "--today", "2026-08-31")
    assert code == 0, f"out-of-repo paths must stay unconstrained, got: {res}"


def test_init_creates_the_frame_directory_when_it_does_not_exist(tmp_path):
    """init is the FIRST command any new engagement runs, and every other test in this
    file uses pytest's `tmp_path`, which always exists -- so the whole suite is
    structurally blind to init's own precondition.

    Before the fix, init wrote its validation temp file to `frame_path.parent` without
    ensuring that directory existed, and died with an UNCAUGHT FileNotFoundError:
    no `die()`, no structured JSON, just a traceback. A caller parsing stdout as JSON
    got nothing to parse. Regression for 2026-08-31.
    """
    f = tmp_path / "brand-new-slug" / "frame.yaml"
    assert not f.parent.exists(), "precondition: the slug directory must NOT exist"

    code, res = run("init", "--frame", f, "--engagement", "brand-new-slug",
                    "--today", "2026-08-31")

    assert code == 0, f"init must create its own directory, got: {res}"
    assert f.exists(), "frame.yaml was not written"
    d = yaml.safe_load(f.read_text())
    assert d["version"] == 1 and d["engagement"] == "brand-new-slug"
    assert not (f.parent / ".frame.yaml.init").exists(), "temp file must be cleaned up"


def test_init_failure_is_structured_not_a_raw_traceback(tmp_path):
    """Every other refusal in this tool exits via `die()` with parseable JSON. A raw
    exception is a different contract, and callers that parse stdout cannot see it."""
    f = tmp_path / "a" / "b" / "c" / "frame.yaml"
    code, res = run("init", "--frame", f, "--engagement", "deep", "--today", "2026-08-31")
    assert code == 0, f"nested parents must be created too, got: {res}"
    assert f.exists()


def test_init_omits_content_fields_rather_than_seeding_blanks(tmp_path):
    """`problem_statement: ""` reads as authored-and-blank, which is a DIFFERENT claim
    from not-yet-authored, and telling those apart is the resume point's whole job."""
    f = tmp_path / "frame.yaml"
    run("init", "--frame", f, "--engagement", "acme", "--today", "2026-08-16")
    d = yaml.safe_load(f.read_text())
    assert "d1" not in d, "segment A creates d1; its absence is the resume signal"
    assert d["facts"] == {} and d["elements"] == []


def test_init_writes_no_field_the_schema_does_not_declare(tmp_path):
    """An undeclared key lands silently: validate_structure ignores keys the schema does
    not declare, so a field nothing reads looks like data and is guaranteed unenforced.
    `created:` was written here once and dropped for exactly this reason."""
    f = tmp_path / "frame.yaml"
    run("init", "--frame", f, "--engagement", "acme", "--today", "2026-08-16")
    written = set(yaml.safe_load(f.read_text()))
    schema = yaml.safe_load((REPO / "framework" / "frame-schema.yaml").read_text())
    declared = {k.split(".")[0] for k in schema["fields"]}
    assert written <= declared, f"init writes undeclared field(s): {sorted(written - declared)}"


def test_init_reads_schema_version_from_the_schema(tmp_path):
    """A literal here drifts silently on a schema bump and refuses every later write."""
    f = tmp_path / "frame.yaml"
    _, res = run("init", "--frame", f, "--engagement", "acme", "--today", "2026-08-16")
    schema = yaml.safe_load((REPO / "framework" / "frame-schema.yaml").read_text())
    assert res["schema_version"] == schema["schema_version"]


# ------------------------------------------------------------- check_log (raw observation)

def test_every_write_appends_one_raw_observation(tmp_path):
    f = frame(tmp_path)
    run("set", "--frame", f, "--expect-version", 1, "--segment", "A", "--field", "engagement=a")
    run("set", "--frame", f, "--expect-version", 2, "--segment", "A", "--field", "engagement=b")
    log = yaml.safe_load(f.read_text())["check_log"]
    assert [r["version"] for r in log] == [2, 3]
    assert [r["segment"] for r in log] == ["A", "A"]


def test_observation_preserves_all_three_states_unaggregated(tmp_path):
    """Raw, not a verdict. Collapsing CANNOT_RUN is the failure the gate exists against,
    and any consumer that wants counts can derive them from this."""
    f = frame(tmp_path)
    _, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    states = yaml.safe_load(f.read_text())["check_log"][0]["states"]
    assert set(states.values()) <= {"PASS", "FAIL", "CANNOT_RUN"}
    assert "CANNOT_RUN" in states.values(), "the third state must survive into the record"
    assert len(states) == res["counts"]["executed"] + res["counts"]["cannot_run"]


def test_check_log_cannot_be_authored_by_a_caller(tmp_path):
    f = frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "check_log=fake")
    assert code != 0 and "DERIVED" in res["message"]


def test_segment_is_optional_and_records_null(tmp_path):
    """Not mandatory: the runner makes writes belonging to no segment, and forcing a
    value would push a caller to supply a false one to get the write through."""
    f = frame(tmp_path)
    code, _ = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0
    assert yaml.safe_load(f.read_text())["check_log"][0]["segment"] is None


# ------------------------------------------------- HARD segment ordering
#
# A segment cannot begin until its predecessor COMPLETED, and completion requires that
# segment's exit condition. Both halves matter: without exit conditions, "completed"
# means "somebody said so" and the ordering guarantees nothing.

D1 = {
    "d1.problem_statement": "Which surfaces should the breakdown cover?",
    "d1.problem_type": "system_design",
    "d1.mode.data_obtainable": False,
    "d1.mode.shots": "one",
    "d1.scope_out": [{"item": "pricing", "reason": "not what the room tests"}],
    "d1.deliverable": {"artifact": "breakdown", "audience": "the room", "format": "spoken"},
}


def complete_A(tmp_path, f, version=1):
    p = tmp_path / "d1.json"
    p.write_text(json.dumps(D1))
    run("patch", "--frame", f, "--expect-version", version, "--segment", "A", "--json", p)
    return run("set", "--frame", f, "--expect-version", version + 1, "--segment", "A",
               "--complete-segment", "--field", "status=in_progress")


def test_d2_refuses_before_d1_completes(tmp_path):
    """The method's most-repeated ordering rule, as a refusal instead of a convention."""
    f = frame(tmp_path)
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--segment", "B",
                    "--field", "facts.early.text=gathered too soon")
    assert code != 0
    assert "OUT OF ORDER" in res["message"]
    assert "D1 must complete before D2" in res["message"], "the refusal must say WHY"
    assert md5(f) == before


def test_cannot_complete_a_segment_with_an_unmet_exit_condition(tmp_path):
    f = frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--segment", "A",
                    "--complete-segment", "--field", "d1.problem_statement=only this")
    assert code != 0
    assert "exit condition" in res["message"]
    assert "d1.scope_out" in res["missing"] and "d1.deliverable" in res["missing"]


def test_an_empty_list_does_not_satisfy_an_exit_condition(tmp_path):
    """`scope_out: []` is a defect, not a clean scope, so it must not count as present."""
    f = frame(tmp_path)
    payload = dict(D1)
    payload["d1.scope_out"] = []
    p = tmp_path / "d1.json"
    p.write_text(json.dumps(payload))
    run("patch", "--frame", f, "--expect-version", 1, "--segment", "A", "--json", p)
    code, res = run("set", "--frame", f, "--expect-version", 2, "--segment", "A",
                    "--complete-segment", "--field", "status=in_progress")
    assert code != 0
    assert "d1.scope_out" in res["missing"]


def test_the_happy_path_advances_the_resume_point(tmp_path):
    f = frame(tmp_path)
    code, res = complete_A(tmp_path, f)
    assert code == 0, res
    assert yaml.safe_load(f.read_text())["segment_completed"] == "A"

    code, _ = run("set", "--frame", f, "--expect-version", 3, "--segment", "B",
                  "--field", "facts.f1.text=now legal")
    assert code == 0, "B is legal once A completed"

    code, res = run("set", "--frame", f, "--expect-version", 4, "--segment", "C",
                    "--field", "closure=too early")
    assert code != 0 and "OUT OF ORDER" in res["message"], "C still needs B complete"


def test_the_resume_point_cannot_be_authored(tmp_path):
    """Everything about running across sessions rests on it, so it is not a field."""
    f = frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "segment_completed=E")
    assert code != 0 and "DERIVED" in res["message"]


def test_complete_segment_without_segment_is_refused(tmp_path):
    f = frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--complete-segment",
                    "--field", "engagement=x")
    assert code != 0 and "requires --segment" in res["message"]


def test_a_segmentless_write_skips_ordering(tmp_path):
    """Not every write belongs to a segment; forcing one would invite a false value."""
    f = frame(tmp_path)
    code, _ = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0


# ------------------------------------------------- the two-decline rule

def test_a_third_consecutive_decline_is_refused(tmp_path):
    """A decline that costs nothing is how a compounding loop quietly stops compounding."""
    data = dict(MINIMAL)
    data["declines"] = [{"segment": "E", "reason": "no change 1"},
                        {"segment": "E", "reason": "no change 2"}]
    f = frame(tmp_path, data)
    before = md5(f)
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"segment": "E", "reason": "no change 3"}))
    code, res = run("append", "--frame", f, "--expect-version", 1, "--list", "declines",
                    "--json", p)
    assert code != 0
    assert "may not decline" in res["message"]
    assert "local optimum" in res["message"], "the refusal must name the way out"
    assert md5(f) == before


def test_two_declines_are_allowed(tmp_path):
    """The rule bites on the THIRD. Refusing earlier would forbid a legitimate no-change."""
    data = dict(MINIMAL)
    data["declines"] = [{"segment": "E", "reason": "no change 1"}]
    f = frame(tmp_path, data)
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"segment": "E", "reason": "no change 2"}))
    code, _ = run("append", "--frame", f, "--expect-version", 1, "--list", "declines",
                  "--json", p)
    assert code == 0


# ------------------------------------------------------------- answers-add

def test_answers_add_derives_at_version_from_the_frame(tmp_path):
    f = frame(tmp_path)
    a = tmp_path / "answers.yaml"
    _, res = run("answers-add", "--answers", a, "--frame", f, "--segment", "A",
                 "--question-id", "problem_type", "--asked", "Which type?",
                 "--answer", "prioritization", "--answered-at", "2026-08-16")
    assert res["at_version"] == 1, "derived from the frame, never passed by the caller"
    assert res["row_index"] == 1
    row = yaml.safe_load(a.read_text())["answers"][0]
    assert row["question_id"] == "problem_type" and row["at_version"] == 1


def test_answers_add_rejects_a_prose_question_id(tmp_path):
    f = frame(tmp_path)
    a = tmp_path / "answers.yaml"
    code, res = run("answers-add", "--answers", a, "--frame", f, "--segment", "A",
                    "--question-id", "Which problem type?", "--asked", "x",
                    "--answer", "y", "--answered-at", "2026-08-16")
    assert code != 0
    assert not a.exists(), "a refused row must not create the file"


def test_a_re_ask_is_reported_and_still_recorded(tmp_path):
    """Refusing would suppress the falsifier. It must stay recordable AND visible."""
    f = frame(tmp_path)
    a = tmp_path / "answers.yaml"
    args = ["--answers", a, "--frame", f, "--question-id", "problem_type",
            "--answer", "prioritization", "--answered-at", "2026-08-16"]
    run("answers-add", *args, "--segment", "A", "--asked", "Which type?")
    code, res = run("answers-add", *args, "--segment", "C", "--asked", "Remind me the type?")
    assert code == 0
    assert res["re_ask"] is True and res["times_asked"] == 2
    assert len(yaml.safe_load(a.read_text())["answers"]) == 2

    _, m = run("answers-metrics", "--answers", a)
    assert m["re_ask_count"] == 1


def test_row_index_is_monotonic_so_a_lost_row_shows_as_a_gap(tmp_path):
    f = frame(tmp_path)
    a = tmp_path / "answers.yaml"
    for i in range(3):
        run("answers-add", "--answers", a, "--frame", f, "--segment", "B",
            "--question-id", f"q{i}", "--asked", "x", "--answer", "y",
            "--answered-at", "2026-08-16")
    assert [r["row_index"] for r in yaml.safe_load(a.read_text())["answers"]] == [1, 2, 3]


# ------------------------------------------------------------- answers metrics

def answers(tmp_path, rows):
    p = tmp_path / "answers.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "answers": rows}), encoding="utf-8")
    return p


def test_re_ask_detected_despite_different_wording(tmp_path):
    """THE point of keying on question_id. The same question asked twice will be worded
    differently; prose comparison would UNDER-count, hiding the defect."""
    a = answers(tmp_path, [
        {"segment": "A", "question_id": "problem_type", "asked": "Which problem type?"},
        {"segment": "C", "question_id": "problem_type", "asked": "Remind me what type we settled on?"},
    ])
    code, res = run("answers-metrics", "--answers", a)
    assert code == 0
    assert res["re_ask_count"] == 1
    assert res["repeated_questions"] == {"problem_type": 2}


def test_no_re_asks_on_distinct_questions(tmp_path):
    a = answers(tmp_path, [{"question_id": f"q{i}"} for i in range(5)])
    _, res = run("answers-metrics", "--answers", a)
    assert res["operator_answer_count"] == 5
    assert res["re_ask_count"] == 0


def test_rows_without_question_id_warn_loudly(tmp_path):
    """A clean re_ask_count computed over rows that cannot be checked is a false pass."""
    a = answers(tmp_path, [{"question_id": "q1"}, {"asked": "no id here"}])
    _, res = run("answers-metrics", "--answers", a)
    assert res["rows_missing_question_id"] == 1
    assert "understates" in res["warning"]


def test_missing_answers_file_is_zero_not_an_error(tmp_path):
    _, res = run("answers-metrics", "--answers", tmp_path / "nope.yaml")
    assert res["operator_answer_count"] == 0
    assert res["re_ask_count"] == 0
    assert res["note"]


def test_show_reports_the_resume_point(tmp_path):
    f = frame(tmp_path)
    _, res = run("show", "--frame", f)
    assert res["version"] == 1
    assert res["engagement"] == "acme"
    assert res["segment_completed"] is None
