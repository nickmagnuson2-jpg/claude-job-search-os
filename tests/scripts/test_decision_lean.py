"""Tests for the decision_write fields the morning page shows (Build M, orchestration.md
2026-11-21 19:05; brief rule 8): every new question carries a lean, the data line it rests on,
what happens if Nick does nothing, and a group (time-bound / role / policy, time-bound with a
date). Org c0's ruling (relayed by 7b, 2026-11-21): show the lean, stamp the asked event with
lean_shown and the lean's option label, and keep shown-lean questions out of R1's blind measure
by carrying the stamp onto the resolved event. Rows asked before these fields exist stay valid.

Generic fixtures only (public repo).
"""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import decision_write as dw  # noqa: E402

BOARD = """# Sessions board

| Session | Owns now | Worktree | Status | Waiting on Nick? | Workstreams |
|---|---|---|---|---|---|
| **org** (job-search-zz) | landing order | none | ok | No | - |
| **aa** | a branch | wt | ok | No | alpha-work |
"""
DATA = "line one\nline two\nline three\n"


@pytest.fixture
def main(tmp_path):
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    ws = root / "data" / "workstreams"
    ws.mkdir(parents=True)
    (ws / "alpha-work.md").write_text("# Workstream: alpha-work\n", encoding="utf-8")
    (root / "data" / "facts.md").write_text(DATA, encoding="utf-8")
    board = root / "output" / "overnight-merge-back"
    board.mkdir(parents=True)
    (board / "SESSIONS.md").write_text(BOARD, encoding="utf-8")
    return root


def run(root, capsys, *argv):
    code = dw.main(["--repo-root", str(root), *argv])
    out, err = capsys.readouterr()
    try:
        data = json.loads(out) if out.strip() else None
    except ValueError:
        data = None
    return code, data, out + err


LEAN = ["--lean", "a: the data favors it", "--rests-on", "data/facts.md:2",
        "--if-nothing", "the owner waits a day", "--group", "policy"]


def add(root, capsys, *extra, question="Ship part one first?", lean=LEAN):
    return run(root, capsys, "add", "--owner", "alpha-work", "--class", "scope",
               "--question", question, "--option", "a: ship it now", "--option", "b: wait",
               "--acts", "the owner cuts the branch", "--predict", "a", "--predict-reason", "r",
               *lean, *extra)


def events(root):
    p = root / "data" / "run-manifest" / "questions.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _without(flag, has_value=True):
    out, skip = [], False
    for i, x in enumerate(LEAN):
        if skip:
            skip = False
            continue
        if x == flag:
            skip = has_value
            continue
        out.append(x)
    return out


# --- the fields are stored and shown --------------------------------------------------------

def test_add_stores_lean_label_rests_on_if_nothing_group_and_lean_shown(main, capsys):
    code, data, _ = add(main, capsys)
    assert code == 0
    e = [x for x in events(main) if x["type"] == "asked"][0]
    assert e["lean"] == "a: the data favors it" and e["lean_label"] == "a"
    assert e["lean_shown"] is True
    assert e["rests_on"] == ["data/facts.md:2"] and e["if_nothing"] == "the owner waits a day"
    assert e["group"] == "policy" and e["by_date"] is None


def test_public_view_and_list_json_carry_the_new_fields(main, capsys):
    add(main, capsys)
    code, data, _ = run(main, capsys, "list", "--json")
    q = data["open"][0]
    for k, v in {"lean": "a: the data favors it", "lean_label": "a", "lean_shown": True,
                 "rests_on": ["data/facts.md:2"], "if_nothing": "the owner waits a day",
                 "group": "policy", "by_date": None}.items():
        assert q[k] == v, k


def test_public_view_never_carries_the_prediction(main, capsys):
    add(main, capsys)
    _, data, _ = run(main, capsys, "list", "--json")
    q = data["open"][0]
    assert "predict" not in json.dumps(q) and "prediction_hash" not in q


def test_public_view_carries_not_answered(main, capsys):
    q = dw.public_view({"id": "Q1", "not_answered": {"topics": ["t"], "reason": "why"}})
    assert q["not_answered"] == {"topics": ["t"], "reason": "why"}


def test_rests_on_is_repeatable(main, capsys):
    code, _, _ = add(main, capsys, "--rests-on", "data/facts.md:3")
    assert code == 0
    e = [x for x in events(main) if x["type"] == "asked"][0]
    assert e["rests_on"] == ["data/facts.md:2", "data/facts.md:3"]


def test_time_bound_carries_its_date(main, capsys):
    lean = _without("--group") + ["--group", "time-bound", "--by-date", "2026-01-09"]
    code, data, _ = add(main, capsys, lean=lean)
    assert code == 0 and data["question"]["by_date"] == "2026-01-09"
    assert data["question"]["group"] == "time-bound"


# --- refusals: required on new adds ---------------------------------------------------------

@pytest.mark.parametrize("flag", ["--lean", "--rests-on", "--if-nothing", "--group"])
def test_each_field_is_required_on_add(main, capsys, flag):
    with pytest.raises(SystemExit):
        add(main, capsys, lean=_without(flag))
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


@pytest.mark.parametrize("flag,value", [("--lean", "  "), ("--if-nothing", " ")])
def test_a_blank_field_is_refused(main, capsys, flag, value):
    lean = _without(flag) + [flag, value]
    code, data, _ = add(main, capsys, lean=lean)
    assert code != 0 and data["code"] == "blank_field"


def test_a_lean_must_name_one_of_the_options(main, capsys):
    lean = _without("--lean") + ["--lean", "c: not an option"]
    code, data, _ = add(main, capsys, lean=lean)
    assert code != 0 and data["code"] == "bad_lean"


def test_a_lean_needs_a_reason_after_the_label(main, capsys):
    lean = _without("--lean") + ["--lean", "a:"]
    code, data, _ = add(main, capsys, lean=lean)
    assert code != 0 and data["code"] == "bad_lean"


def test_an_unknown_group_is_refused(main, capsys):
    with pytest.raises(SystemExit):
        add(main, capsys, lean=_without("--group") + ["--group", "someday"])


def test_time_bound_without_a_date_is_refused(main, capsys):
    code, data, _ = add(main, capsys, lean=_without("--group") + ["--group", "time-bound"])
    assert code != 0 and data["code"] == "by_date_required"


def test_a_bad_date_is_refused(main, capsys):
    lean = _without("--group") + ["--group", "time-bound", "--by-date", "2026-13-40"]
    code, data, _ = add(main, capsys, lean=lean)
    assert code != 0 and data["code"] == "bad_by_date"


def test_a_date_on_a_group_that_is_not_time_bound_is_refused(main, capsys):
    code, data, _ = add(main, capsys, "--by-date", "2026-01-09")
    assert code != 0 and data["code"] == "by_date_not_time_bound"


@pytest.mark.parametrize("cite,code_", [
    ("data/facts.md", "bad_rests_on"),             # no line number
    ("data/facts.md:0", "bad_rests_on"),            # lines start at 1
    ("data/missing.md:1", "rests_on_missing"),      # no such file
    ("data/facts.md:4", "rests_on_missing"),        # past the end of the file
    ("../outside.md:1", "bad_rests_on"),            # outside the main checkout
    ("/etc/hosts:1", "bad_rests_on"),               # absolute
])
def test_a_rests_on_line_must_exist_in_the_main_checkout(main, capsys, cite, code_):
    lean = _without("--rests-on") + ["--rests-on", cite]
    code, data, _ = add(main, capsys, lean=lean)
    assert code != 0 and data["code"] == code_
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


def test_the_last_line_of_a_file_is_a_valid_citation(main, capsys):
    lean = _without("--rests-on") + ["--rests-on", "data/facts.md:3"]
    code, _, _ = add(main, capsys, lean=lean)
    assert code == 0


# --- R1: shown-lean questions are marked on the resolved event --------------------------------

def test_resolve_carries_lean_shown_onto_the_resolved_event(main, capsys):
    add(main, capsys)
    p = main / "data" / "workstreams" / "alpha-work.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "\nRULING Q1 | option: a | reason: fine | window: aa\n", encoding="utf-8")
    code, data, _ = run(main, capsys, "resolve", "Q1", "--same-reason")
    assert code == 0
    r = [x for x in events(main) if x["type"] == "resolved"][0]
    assert r["lean_shown"] is True and r["lean_label"] == "a"
    assert data["lean_shown"] is True


def test_a_legacy_question_resolves_with_lean_shown_false(main, capsys):
    """Rows asked before the lean fields existed were asked blind: R1 keeps them."""
    add(main, capsys)
    qs = main / "data" / "run-manifest" / "questions.jsonl"
    e = json.loads(qs.read_text(encoding="utf-8").splitlines()[0])
    for k in ("lean", "lean_label", "lean_shown", "rests_on", "if_nothing", "group", "by_date"):
        e.pop(k, None)
    e = dw.sealed({k: v for k, v in e.items() if k != "record_hash"})
    qs.write_text(json.dumps(e) + "\n", encoding="utf-8")
    p = main / "data" / "workstreams" / "alpha-work.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "\nRULING Q1 | option: a | reason: fine | window: aa\n", encoding="utf-8")
    code, _, _ = run(main, capsys, "resolve", "Q1", "--same-reason")
    assert code == 0
    r = [x for x in events(main) if x["type"] == "resolved"][0]
    assert r["lean_shown"] is False and r["lean_label"] is None


def test_a_legacy_row_still_lists_and_checks_clean(main, capsys):
    add(main, capsys)
    qs = main / "data" / "run-manifest" / "questions.jsonl"
    e = json.loads(qs.read_text(encoding="utf-8").splitlines()[0])
    for k in ("lean", "lean_label", "lean_shown", "rests_on", "if_nothing", "group", "by_date"):
        e.pop(k, None)
    e = dw.sealed({k: v for k, v in e.items() if k != "record_hash"})
    qs.write_text(json.dumps(e) + "\n", encoding="utf-8")
    _, data, _ = run(main, capsys, "list", "--json")
    q = data["open"][0]
    assert q["lean"] is None and q["rests_on"] is None and q["lean_shown"] is None
    code, data, _ = run(main, capsys, "check")
    assert code == 0 and data["problems"] == []


def test_the_r1_stamp_is_bound_by_the_record_hash(main, capsys):
    """Flipping lean_shown by hand to sneak a shown-lean question into R1 is caught by check."""
    add(main, capsys)
    qs = main / "data" / "run-manifest" / "questions.jsonl"
    e = json.loads(qs.read_text(encoding="utf-8").splitlines()[0])
    e["lean_shown"] = False
    qs.write_text(json.dumps(e) + "\n", encoding="utf-8")
    code, data, _ = run(main, capsys, "check")
    assert any("edited outside the tool" in x for x in data["problems"])
