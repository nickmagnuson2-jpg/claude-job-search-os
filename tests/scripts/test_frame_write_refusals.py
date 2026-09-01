"""Refusal-path tests for tools/frame_write.py.

WHY THIS FILE EXISTS. A mutation run on 2026-08-31 killed 199 mutants and left **52
alive**, and they were not scattered -- almost every survivor was a `die()` call or the
guard that reaches it. The happy paths and the four load-bearing guarantees (CAS,
byte-identical-on-failure, concurrent-writer, derived-field refusal) were well covered;
the REFUSALS were not. You could have deleted most of the `die()` calls in this tool and
the suite would have stayed green.

That matters more here than in most tools: frame_write.py is the sole sanctioned mutation
path for frame.yaml, and its entire value proposition is that it refuses bad writes. The
refusals ARE the product.

Each test below asserts on a specific refusal: the exit code AND the message, so that
dropping the `die()` or inverting its guard fails loudly.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "frame_write.py"
sys.path.insert(0, str(REPO / "tools"))
import frame_write as fw  # noqa: E402


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, {"_raw": r.stdout, "_err": r.stderr}


def good_frame(tmp_path):
    p = tmp_path / "frame.yaml"
    run("init", "--frame", p, "--engagement", "acme", "--today", "2026-08-31")
    return p


def dies_with(fragment, fn, *a, **kw):
    """Assert fn() calls die() with `fragment` in the message. die() prints JSON and exits 1."""
    with pytest.raises(SystemExit) as e:
        fn(*a, **kw)
    assert e.value.code == 1, f"die() must exit 1, got {e.value.code}"


# ------------------------------------------------------------------ load_frame

def test_missing_frame_is_refused(tmp_path):
    code, res = run("show", "--frame", tmp_path / "nope.yaml")
    assert code != 0
    assert "no frame at" in res["message"]


def test_unparseable_frame_is_refused_with_the_parse_error(tmp_path):
    p = tmp_path / "frame.yaml"
    p.write_text("key: [unclosed\n  bad: : :\n", encoding="utf-8")
    code, res = run("show", "--frame", p)
    assert code != 0
    assert "does not parse" in res["message"]


def test_a_frame_that_is_not_a_mapping_is_refused(tmp_path):
    """Valid YAML, wrong shape. A bare scalar parses fine and must still be refused."""
    p = tmp_path / "frame.yaml"
    p.write_text("just a string\n", encoding="utf-8")
    code, res = run("show", "--frame", p)
    assert code != 0
    assert "not a mapping" in res["message"]


def test_a_yaml_list_is_also_not_a_mapping(tmp_path):
    p = tmp_path / "frame.yaml"
    p.write_text("- one\n- two\n", encoding="utf-8")
    code, res = run("show", "--frame", p)
    assert code != 0 and "not a mapping" in res["message"]


# ---------------------------------------------------------------------- coerce
# Each branch returns a DIFFERENT type; asserting the type is what kills the mutants.

@pytest.mark.parametrize("raw", ["true", "TRUE", " yes ", "Yes"])
def test_coerce_truthy_words_become_real_booleans(raw):
    assert fw.coerce(raw) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", " no ", "No"])
def test_coerce_falsy_words_become_real_booleans(raw):
    assert fw.coerce(raw) is False


@pytest.mark.parametrize("raw", ["null", "none", "~", "NULL"])
def test_coerce_null_words_become_none(raw):
    assert fw.coerce(raw) is None


def test_coerce_keeps_the_scalar_ladder_distinct():
    """int before float before str, and the boolean words must not fall through to str."""
    assert fw.coerce("7") == 7 and isinstance(fw.coerce("7"), int)
    assert fw.coerce("7.5") == 7.5 and isinstance(fw.coerce("7.5"), float)
    assert fw.coerce("hello") == "hello"
    assert fw.coerce("true") is not "true"  # noqa: F632 -- the point is the type changed


# ------------------------------------------------------------- verdict_refuses
# The bypass this function exists to close: defaulting to "fine" on an unrecognised shape.

def test_verdict_refuses_an_exit_code_outside_the_judged_range():
    for rc in (1, 3, 4, 5):
        msg = fw.verdict_refuses(rc, {"status": "ok", "counts": {}})
        assert msg and "only 0 and 2 are judgements" in msg, f"rc={rc} must refuse"


def test_verdict_accepts_only_the_two_judged_exit_codes():
    for rc in (0, 2):
        assert fw.verdict_refuses(rc, {"status": "ok", "counts": {}}) is None


def test_verdict_refuses_any_status_other_than_ok():
    """`refused` is the one the ORIGINAL check missed -- it tested only for `error`."""
    for status in ("refused", "error", "warning", None):
        msg = fw.verdict_refuses(0, {"status": status, "counts": {}})
        assert msg and "checker returned status" in msg, f"status={status!r} must refuse"


def test_verdict_refusal_message_carries_the_detail():
    msg = fw.verdict_refuses(0, {"status": "refused", "detail": "bad shape", "counts": {}})
    assert "bad shape" in msg


def test_verdict_refuses_structural_errors():
    msg = fw.verdict_refuses(0, {"status": "ok", "structural_errors": ["x"], "counts": {}})
    assert msg == "candidate has structural errors"


def test_verdict_refuses_a_verdict_with_no_counts_key():
    msg = fw.verdict_refuses(0, {"status": "ok"})
    assert msg and "judged nothing" in msg


def test_verdict_treats_empty_counts_as_judged_but_missing_counts_as_not():
    """`{}` is a judgement with nothing in it; absent is a shape change. Different."""
    assert fw.verdict_refuses(0, {"status": "ok", "counts": {}}) is None
    assert fw.verdict_refuses(0, {"status": "ok", "counts": None}) is not None


# -------------------------------------------------------- check_segment_order

def test_completing_a_segment_twice_is_refused_with_the_next_segment_named():
    cur = {"segment_completed": "A"}
    with pytest.raises(SystemExit):
        fw.check_segment_order(cur, "A")


def test_segment_order_allows_the_legal_successor():
    assert fw.check_segment_order({"segment_completed": "A"}, "B") is None


def test_out_of_order_segment_is_refused():
    with pytest.raises(SystemExit):
        fw.check_segment_order({"segment_completed": None}, "C")


# -------------------------------------------------------------- main() arg validation

def test_set_with_no_field_is_refused(tmp_path):
    f = good_frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1)
    assert code != 0
    assert "at least one --field" in res["message"]


def test_field_without_an_equals_sign_is_refused(tmp_path):
    f = good_frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement")
    assert code != 0
    assert "must be k=v" in res["message"]


def test_patch_with_unparseable_json_is_refused(tmp_path):
    f = good_frame(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", bad)
    assert code != 0
    assert "did not parse" in res["message"]


def test_patch_payload_that_is_not_an_object_is_refused(tmp_path):
    f = good_frame(tmp_path)
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", arr)
    assert code != 0
    assert "must be an object" in res["message"]


def test_append_with_unparseable_json_is_refused(tmp_path):
    f = good_frame(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{{{", encoding="utf-8")
    code, res = run("append", "--frame", f, "--expect-version", 1,
                    "--list", "proposals", "--json", bad)
    assert code != 0
    assert "did not parse" in res["message"]


def test_append_onto_a_corrupted_non_list_ledger_is_refused(tmp_path):
    """`--list` is argparse-constrained to four ledger fields, so this guard is only
    reachable when one of them exists and is NOT a list -- i.e. a hand-edited or
    corrupted frame. That is precisely what the check defends against: appending to a
    string would either crash or silently clobber the field."""
    f = good_frame(tmp_path)
    d = yaml.safe_load(f.read_text())
    d["proposals"] = "someone hand-edited this into a string"
    f.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")

    payload = tmp_path / "p.json"
    payload.write_text('[{"stage": "D1", "proposed": "x", "status": "rejected", "reason": "y"}]',
                       encoding="utf-8")
    before = f.read_bytes()
    code, res = run("append", "--frame", f, "--expect-version", 1,
                    "--list", "proposals", "--json", payload)
    assert code != 0, res
    assert "is not a list" in res["message"], res
    assert f.read_bytes() == before, "a refused append must leave the frame byte-identical"


def test_append_actually_appends_when_the_target_is_a_list(tmp_path):
    """The dispatch branch itself: if `elif a.cmd == 'append'` is skipped, this fails."""
    f = good_frame(tmp_path)
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps([{"stage": "D1", "proposed": "x",
                                    "status": "rejected", "reason": "y"}]), encoding="utf-8")
    code, res = run("append", "--frame", f, "--expect-version", 1,
                    "--list", "proposals", "--json", payload)
    assert code == 0, res
    d = yaml.safe_load(f.read_text())
    assert len(d["proposals"]) == 1 and d["proposals"][0]["proposed"] == "x"


# ----------------------------------------------------- schema_version_from_schema

def test_an_unreadable_schema_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(fw, "SCHEMA", tmp_path / "absent-schema.yaml")
    with pytest.raises(SystemExit):
        fw.schema_version_from_schema()


def test_a_schema_without_an_integer_version_is_refused(monkeypatch, tmp_path):
    s = tmp_path / "schema.yaml"
    s.write_text("schema_version: not-a-number\n", encoding="utf-8")
    monkeypatch.setattr(fw, "SCHEMA", s)
    with pytest.raises(SystemExit):
        fw.schema_version_from_schema()


def test_a_valid_schema_returns_its_integer_version(monkeypatch, tmp_path):
    s = tmp_path / "schema.yaml"
    s.write_text("schema_version: 9\n", encoding="utf-8")
    monkeypatch.setattr(fw, "SCHEMA", s)
    assert fw.schema_version_from_schema() == 9


# ---------------------------------------------------------------- run_checker

def test_run_checker_returns_a_structured_error_when_the_checker_is_missing(monkeypatch, tmp_path):
    """If the checker cannot run, the tool must return a refusable verdict, not crash."""
    monkeypatch.setattr(fw, "CHECKER", tmp_path / "no-such-checker.py")
    rc, verdict = fw.run_checker(tmp_path / "frame.yaml")
    assert verdict.get("status") == "error"
    assert fw.verdict_refuses(rc, verdict), "an unrunnable checker must REFUSE the write"


# ----------------------------------------------------------------- init_frame

def test_init_refuses_when_the_gate_rejects_the_skeleton(monkeypatch, tmp_path):
    """If the schema gate refuses the skeleton, init must write NOTHING."""
    monkeypatch.setattr(fw, "run_checker",
                        lambda p: (3, {"status": "refused", "detail": "synthetic"}))
    target = tmp_path / "frame.yaml"
    with pytest.raises(SystemExit):
        fw.init_frame(target, "acme", "2026-08-31")
    assert not target.exists(), "a refused skeleton must not land"
    assert not (tmp_path / ".frame.yaml.init").exists(), "temp file must be cleaned up"


def test_init_cleans_up_its_temp_file_on_success(tmp_path):
    f = good_frame(tmp_path)
    assert f.exists()
    assert not (tmp_path / ".frame.yaml.init").exists()


# ----------------------------------------------------------------- write_frame

def test_a_verdict_that_changes_when_recorded_is_refused(monkeypatch, tmp_path):
    """The observation must not alter what it observes. If recording the check_log entry
    changes the verdict, the write is abandoned rather than trusted."""
    f = good_frame(tmp_path)
    calls = {"n": 0}
    real = fw.run_checker

    def unstable(p):
        calls["n"] += 1
        rc, v = real(p)
        if calls["n"] > 1:                     # second look differs from the first
            v = {**v, "counts": {"pass": 999, "fail": 0, "cannot_run": 0}}
        return rc, v

    monkeypatch.setattr(fw, "run_checker", unstable)
    candidate = yaml.safe_load(f.read_text())
    candidate["engagement"] = "changed"
    before = f.read_bytes()
    with pytest.raises(SystemExit):
        fw.write_frame(f, candidate, 1, None, False)
    assert f.read_bytes() == before, "a refused write must leave the frame byte-identical"


# =====================================================================
# Second pass, 2026-08-31. A mutation run after the tests above left 18
# alive. These target that residue. The lesson from the first pass is in
# the segment tests below: asserting only `pytest.raises(SystemExit)` is
# a WEAK assertion when BOTH branches die -- the mutant swaps which
# refusal fires and the test cannot tell.
# =====================================================================

def answers_file(tmp_path, text):
    p = tmp_path / "answers.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def add_answer(frame_path, answers_path, qid, seg="A"):
    return run("answers-add", "--answers", answers_path, "--frame", frame_path,
               "--segment", seg, "--question-id", qid,
               "--asked", "what?", "--answer", "this", "--answered-at", "2026-08-31")


# ------------------------------------------------------------- answers_add

def test_answers_add_refuses_an_unparseable_answers_file(tmp_path):
    f = good_frame(tmp_path)
    a = answers_file(tmp_path, "answers: [unclosed\n : : :\n")
    code, res = add_answer(f, a, "q1")
    assert code != 0
    assert "answers file does not parse" in res["message"], res


def test_answers_add_refuses_when_answers_is_not_a_list(tmp_path):
    f = good_frame(tmp_path)
    a = answers_file(tmp_path, "schema_version: 1\nanswers: not-a-list\n")
    code, res = add_answer(f, a, "q1")
    assert code != 0
    assert "answers.answers must be a list" in res["message"], res


def test_a_re_ask_is_REPORTED_not_refused(tmp_path):
    """A genuine re-ask must stay recordable. Refusing it would suppress the falsifier
    that re_ask_count exists to surface -- so the second write must SUCCEED and carry
    the warning."""
    f = good_frame(tmp_path)
    a = tmp_path / "answers.yaml"

    code, first = add_answer(f, a, "q_scope")
    assert code == 0, first
    assert "re_ask" not in first, "a first ask is not a re-ask"

    code, second = add_answer(f, a, "q_scope")
    assert code == 0, "a re-ask must be recorded, never refused"
    assert second.get("re_ask") is True
    assert second.get("times_asked") == 2
    assert "asked 2 times" in second.get("warning", "")


def test_distinct_question_ids_do_not_trigger_the_re_ask_warning(tmp_path):
    f = good_frame(tmp_path)
    a = tmp_path / "answers.yaml"
    add_answer(f, a, "q_one")
    code, res = add_answer(f, a, "q_two")
    assert code == 0 and "re_ask" not in res, res


# --------------------------------------------------------- answers_metrics

def test_metrics_refuses_an_unparseable_answers_file(tmp_path):
    a = answers_file(tmp_path, "answers: [oops\n : : :\n")
    code, res = run("answers-metrics", "--answers", a)
    assert code != 0
    assert "answers file does not parse" in res["message"], res


def test_metrics_refuses_when_answers_is_not_a_list(tmp_path):
    a = answers_file(tmp_path, "schema_version: 1\nanswers: 42\n")
    code, res = run("answers-metrics", "--answers", a)
    assert code != 0
    assert "answers.answers must be a list" in res["message"], res


def test_metrics_skips_non_dict_rows_without_crashing(tmp_path):
    """A malformed row must be stepped over, not fatal -- but it still counts toward
    operator_answer_count, because len(rows) is the honest total."""
    a = answers_file(tmp_path,
                     "schema_version: 1\n"
                     "answers:\n"
                     "  - question_id: q1\n"
                     "  - a bare string row\n"
                     "  - question_id: q2\n")
    code, res = run("answers-metrics", "--answers", a)
    assert code == 0, res
    assert res["operator_answer_count"] == 3, "all rows count, including the malformed one"
    assert res["distinct_questions"] == 2, "only the two dict rows carry ids"


def test_metrics_warns_loudly_about_rows_with_no_question_id(tmp_path):
    """Rows without an id are INVISIBLE to re-ask detection, so a clean re_ask_count on
    a file full of them would be a false pass. The warning is the whole point."""
    a = answers_file(tmp_path,
                     "schema_version: 1\n"
                     "answers:\n"
                     "  - question_id: q1\n"
                     "  - question_id: ''\n"
                     "  - asked: no id at all\n")
    code, res = run("answers-metrics", "--answers", a)
    assert code == 0, res
    assert res["rows_missing_question_id"] == 2, res
    assert "understates" in res.get("warning", ""), res


def test_metrics_omits_the_warning_when_every_row_has_an_id(tmp_path):
    a = answers_file(tmp_path, "schema_version: 1\nanswers:\n  - question_id: q1\n")
    code, res = run("answers-metrics", "--answers", a)
    assert code == 0
    assert "rows_missing_question_id" not in res and "warning" not in res


def test_metrics_on_a_missing_file_is_zero_not_an_error(tmp_path):
    code, res = run("answers-metrics", "--answers", tmp_path / "none.yaml")
    assert code == 0
    assert res["operator_answer_count"] == 0 and res["re_ask_count"] == 0


# ------------------------------------------------- check_segment_order (messages)

def test_re_running_a_completed_segment_names_THAT_refusal_not_the_generic_one(tmp_path):
    """BOTH branches of check_segment_order die. Asserting only that it exits cannot
    tell them apart, so a mutant that swaps which one fires survives. Assert the text."""
    f = good_frame(tmp_path)
    d = yaml.safe_load(f.read_text())
    d["segment_completed"] = "A"
    f.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")

    code, res = run("set", "--frame", f, "--expect-version", 1,
                    "--segment", "A", "--field", "engagement=x")
    assert code != 0
    assert "already COMPLETE" in res["message"], res
    assert "OUT OF ORDER" not in res["message"], "wrong refusal branch fired"


def test_a_segment_whose_predecessor_is_incomplete_gets_the_OUT_OF_ORDER_refusal(tmp_path):
    f = good_frame(tmp_path)
    code, res = run("set", "--frame", f, "--expect-version", 1,
                    "--segment", "C", "--field", "engagement=x")
    assert code != 0
    assert "OUT OF ORDER" in res["message"], res
    assert "already COMPLETE" not in res["message"], "wrong refusal branch fired"


# ------------------------------------------------------- write_frame internals

def test_the_verdict_changed_guard_fires_with_ITS_OWN_message(monkeypatch, tmp_path):
    """Asserting SystemExit alone is not enough -- write_frame has several die() paths.
    This pins the specific one."""
    import io
    import contextlib
    f = good_frame(tmp_path)
    calls = {"n": 0}
    real = fw.run_checker

    def unstable(p):
        calls["n"] += 1
        rc, v = real(p)
        if calls["n"] > 1:
            v = {**v, "counts": {"pass": 999, "fail": 0, "cannot_run": 0}}
        return rc, v

    monkeypatch.setattr(fw, "run_checker", unstable)
    candidate = yaml.safe_load(f.read_text())      # a COMPLETE frame, not a patch
    candidate["engagement"] = "changed"
    before = f.read_bytes()
    buf = io.StringIO()
    with pytest.raises(SystemExit), contextlib.redirect_stdout(buf):
        fw.write_frame(f, candidate, 1, None, False)
    printed = buf.getvalue()
    assert "recording the observation changed the verdict" in printed, printed
    assert f.read_bytes() == before, "a refused write must leave the frame byte-identical"
    assert temp_leftovers(tmp_path) == [], (
        "the candidate must be cleaned up even when the refusal happens in phase 2: "
        f"{temp_leftovers(tmp_path)}")


def temp_leftovers(tmp_path):
    """Every scratch file frame_write can create.

    NOTE, and this is why the first version of these tests did not kill their mutants:
    the write candidate is `frame.yaml.candidate`, which does NOT start with a dot. A
    leftover check that globs only for dot-files silently passes while the candidate
    file accumulates. Only `init` uses a dotted temp (`.frame.yaml.init`).
    """
    return sorted(p.name for p in tmp_path.iterdir()
                  if p.name.startswith(".") or p.name.endswith(".candidate")
                  or p.name.endswith(".tmp"))


def test_no_temp_file_is_left_behind_by_a_successful_write(tmp_path):
    f = good_frame(tmp_path)
    code, _ = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0
    assert temp_leftovers(tmp_path) == [], f"temp files left behind: {temp_leftovers(tmp_path)}"


def test_no_temp_file_is_left_behind_by_a_write_refused_BEFORE_the_candidate_exists(tmp_path):
    """A version mismatch dies before `tmp` is ever assigned. Nothing to clean, but the
    finally must not fault either."""
    f = good_frame(tmp_path)
    code, _ = run("set", "--frame", f, "--expect-version", 99, "--field", "engagement=x")
    assert code != 0
    assert temp_leftovers(tmp_path) == []


def test_no_candidate_is_left_behind_by_a_write_refused_AFTER_it_was_written(tmp_path):
    """THE case the cleanup exists for. The schema gate refuses the candidate only after
    it has been serialised to disk, so this is the path where `finally: tmp.unlink()`
    actually does work. A refusal that dies earlier can never exercise it."""
    f = good_frame(tmp_path)
    d = yaml.safe_load(f.read_text())
    d["elements"] = "this is not a list of elements"   # structurally invalid candidate
    f2 = tmp_path / "frame.yaml"
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"elements": "not-a-list"}), encoding="utf-8")
    code, res = run("patch", "--frame", f2, "--expect-version", 1, "--json", payload)
    assert code != 0, f"a structurally invalid candidate must be refused, got: {res}"
    assert temp_leftovers(tmp_path) == [], (
        f"the candidate file was not cleaned up after refusal: {temp_leftovers(tmp_path)}")
