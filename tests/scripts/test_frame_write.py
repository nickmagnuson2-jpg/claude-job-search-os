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
    "operator_answer_count=0", "re_ask_count=0",
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


# ------------------------------------------------------------- lock

def test_lock_sets_prediction_and_satisfies_F13(tmp_path):
    f = frame(tmp_path)
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
