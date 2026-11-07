"""Tests for tools/decision_write.py, the question queue (run manifest part 1).

Generic fixtures only (public repo). Each test names the threat it pins (T<n>, from
output/question-queue-1106/110626-spec-threat-model.md).
"""
import contextlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import decision_write as dw  # noqa: E402

SENTINEL = "SENTINEL-PREDICTION-REASON"

BOARD = """# Sessions board

| Session | Owns now | Worktree | Status | Waiting on Nick? | Workstreams |
|---|---|---|---|---|---|
| **org** (job-search-zz) | landing order | none | ok | No | - |
| **aa** | a branch | wt | ok | No | alpha-work |
| **bb** [123abc] | b branch | wt | ok | No | beta-work, gamma-work |
| **cc** | c branch | wt | ok | No | gamma-work |
"""


@pytest.fixture
def main(tmp_path):
    """A fake main checkout: .git directory, three workstreams plus the organizer's, a board."""
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    ws = root / "data" / "workstreams"
    ws.mkdir(parents=True)
    for name in ("alpha-work", "beta-work", "gamma-work", "delta-work", "overnight-swarm"):
        (ws / f"{name}.md").write_text(f"# Workstream: {name}\n", encoding="utf-8")
    board = root / "output" / "overnight-merge-back"
    board.mkdir(parents=True)
    (board / "SESSIONS.md").write_text(BOARD, encoding="utf-8")
    return root


@pytest.fixture
def worktree(tmp_path, main):
    """A linked worktree of `main`: its .git is a FILE pointing at main/.git/worktrees/x."""
    (main / ".git" / "worktrees" / "x").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'x'}\n", encoding="utf-8")
    return wt


def run(root, *argv, capsys=None):
    """Run the CLI in-process; return (exit code, parsed stdout JSON or None, raw out, raw err)."""
    code = dw.main(["--repo-root", str(root), *argv])
    out, err = capsys.readouterr() if capsys else ("", "")
    try:
        data = json.loads(out) if out.strip() else None
    except ValueError:
        data = None
    return code, data, out, err


def add(root, capsys, *extra, owner="alpha-work", cls="scope", question="Ship part one first?",
        predict="a", reason=SENTINEL):
    args = ["add", "--owner", owner, "--class", cls, "--question", question,
            "--option", "a: ship it now", "--option", "b: wait for part two",
            "--acts", "the owner cuts the branch", "--predict", predict, "--predict-reason", reason,
            *extra]
    return run(root, *args, capsys=capsys)


def rule(root, owner, line):
    p = root / "data" / "workstreams" / f"{owner}.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n## 2026-01-05 | a ruling\n\n" + line + "\n",
                 encoding="utf-8")
    return p


def events(root):
    p = root / "data" / "run-manifest" / "questions.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ add: fields and refusals

def test_add_allocates_sequential_ids_and_prints_the_public_view(main, capsys):
    code, data, out, _ = add(main, capsys)
    assert code == 0 and data["status"] == "ok" and data["question"]["id"] == "Q1"
    code, data, _, _ = add(main, capsys, question="A second question?")
    assert data["question"]["id"] == "Q2"
    assert SENTINEL not in out and "predict" not in out


def test_T1_owner_is_required(main, capsys):
    with pytest.raises(SystemExit):
        dw.main(["--repo-root", str(main), "add", "--class", "scope", "--question", "q",
                 "--option", "a: x", "--option", "b: y", "--acts", "z", "--predict", "a",
                 "--predict-reason", "r"])


def test_T2_owner_must_be_a_workstream_file_or_org(main, capsys):
    code, data, _, _ = add(main, capsys, owner="no-such-work")
    assert code == 1 and data["code"] == "unknown_owner"
    code, data, _, _ = add(main, capsys, owner="aa")       # a session name, not a workstream
    assert code == 1 and data["code"] == "unknown_owner"
    code, data, _, _ = add(main, capsys, owner="org")
    assert code == 0 and data["question"]["owner"] == "org"


def test_class_must_be_codified(main, capsys):
    code, data, _, _ = add(main, capsys, cls="landing-odrer")
    assert code == 1 and data["code"] == "unknown_class"
    assert set(dw.CLASSES) == {"spend", "live-data-write", "guarded-edit", "push", "waiver", "scope",
                               "design-shape", "order", "assignment", "workstream-status",
                               "finding-rule", "fact-check"}


@pytest.mark.parametrize("cls,kind", [("live-data-write", "big"), ("guarded-edit", "big"),
                                      ("push", "big"), ("waiver", "big"), ("scope", "design"),
                                      ("design-shape", "design"), ("order", "design"),
                                      ("assignment", "design"), ("workstream-status", "design"),
                                      ("finding-rule", "design"), ("fact-check", "design")])
def test_kind_comes_from_the_class(main, capsys, cls, kind):
    code, data, _, _ = add(main, capsys, cls=cls)
    assert code == 0 and data["question"]["kind"] == kind


@pytest.mark.parametrize("extra,kind", [(["--usd", "0.40"], "design"), (["--usd", "4.99"], "design"),
                                        (["--usd", "5"], "big"), (["--usd", "250"], "big"),
                                        (["--usd", "unknown"], "big"),
                                        (["--usd", "1", "--commitment"], "big")])
def test_spend_is_big_from_five_dollars_unknown_or_a_commitment(main, capsys, extra, kind):
    code, data, _, _ = add(main, capsys, *extra, cls="spend")
    assert code == 0 and data["question"]["kind"] == kind


def test_spend_needs_a_usd_figure_and_rejects_a_bad_one(main, capsys):
    code, data, _, _ = add(main, capsys, cls="spend")
    assert code == 1 and data["code"] == "usd_required"
    code, data, _, _ = add(main, capsys, "--usd", "lots", cls="spend")
    assert code == 1 and data["code"] == "bad_usd"
    code, data, _, _ = add(main, capsys, "--usd", "-1", cls="spend")
    assert code == 1 and data["code"] == "bad_usd"
    code, data, _, _ = add(main, capsys, "--usd", "3")           # usd on a non-spend class
    assert code == 1 and data["code"] == "usd_not_spend"


def test_options_need_two_distinct_labelled_choices(main, capsys):
    base = ["add", "--owner", "alpha-work", "--class", "scope", "--question", "q", "--acts", "z",
            "--predict", "a", "--predict-reason", "r"]
    code, data, _, _ = run(main, *base, "--option", "a: only one", capsys=capsys)
    assert code == 1 and data["code"] == "too_few_options"
    code, data, _, _ = run(main, *base, "--option", "a: x", "--option", "a: y", capsys=capsys)
    assert code == 1 and data["code"] == "duplicate_option"
    code, data, _, _ = run(main, *base, "--option", "a: x", "--option", "no label", capsys=capsys)
    assert code == 1 and data["code"] == "bad_option"


@pytest.mark.parametrize("field", ["question", "acts", "reason"])
def test_blank_text_is_refused(main, capsys, field):
    kw = {"question": "q?", "reason": "r"}
    extra = []
    if field == "question":
        kw["question"] = "   "
    elif field == "reason":
        kw["reason"] = "  "
    code, data, _, _ = add(main, capsys, *extra, **kw) if field != "acts" else run(
        main, "add", "--owner", "alpha-work", "--class", "scope", "--question", "q?",
        "--option", "a: x", "--option", "b: y", "--acts", " ", "--predict", "a",
        "--predict-reason", "r", capsys=capsys)
    assert code == 1 and data["code"] == "blank_field"


def test_prediction_must_be_one_of_the_options(main, capsys):
    code, data, _, _ = add(main, capsys, predict="c")
    assert code == 1 and data["code"] == "bad_prediction"


def test_T12_identical_open_question_for_the_same_owner_is_refused(main, capsys):
    add(main, capsys, question="Ship part one first?")
    code, data, _, _ = add(main, capsys, question="  ship PART one   first? ")
    assert code == 1 and data["code"] == "duplicate_question" and data["existing"] == "Q1"
    code, _, _, _ = add(main, capsys, owner="beta-work", question="Ship part one first?")
    assert code == 0                                               # another owner may ask it


def test_T9_times_come_from_the_clock_with_no_flag(main, capsys, monkeypatch):
    monkeypatch.setattr(dw, "now", lambda: "2026-01-05T10:00:00+00:00")
    add(main, capsys)
    assert events(main)[0]["at"] == "2026-01-05T10:00:00+00:00"
    with pytest.raises(SystemExit):
        dw.main(["--repo-root", str(main), "add", "--at", "2020-01-01"])


def test_dry_run_writes_nothing(main, capsys):
    code, data, out, _ = add(main, capsys, "--dry-run")
    assert code == 0 and data["dry_run"] is True and SENTINEL not in out
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()
    assert not (main / "data" / "run-manifest" / "predictions.jsonl").exists()


# ------------------------------------------------------------------ T15: the prediction stays hidden

def test_T15_no_command_shows_the_prediction_while_open(main, capsys):
    outs = [add(main, capsys)[2], add(main, capsys, "--dry-run", question="other?")[2]]
    for argv in (["list"], ["list", "--json"], ["list", "--owner", "alpha-work"], ["check"],
                 ["takeover", "--org", "org"], ["resolve", "Q1"], ["resolve", "Q9"]):
        code, _, out, err = run(main, *argv, capsys=capsys)
        outs += [out, err]
    for text in outs:
        assert SENTINEL not in text
    pred = json.loads((main / "data" / "run-manifest" / "predictions.jsonl").read_text().splitlines()[0])
    assert pred["reason"] == SENTINEL and pred["option"] == "a" and pred["blind"] is True


def test_T15_listing_never_opens_the_predictions_file(main, capsys, monkeypatch):
    add(main, capsys)
    real = Path.read_text

    def guarded(self, *a, **k):
        assert self.name != "predictions.jsonl", "printing code opened the predictions file"
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", guarded)
    for argv in (["list"], ["list", "--json"], ["takeover", "--org", "org"]):
        assert run(main, *argv, capsys=capsys)[0] == 0


def test_resolve_reveals_the_prediction_and_the_outcome(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: part two is close | window: aa")
    code, data, out, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 0 and data["outcome"] == "correction"
    assert data["prediction"] == {"option": "a", "reason": SENTINEL}
    assert data["ruling"]["option"] == "b" and data["ruling"]["reason"] == "part two is close"


# ------------------------------------------------------------------ outcomes (D6)

def test_same_option_needs_a_declared_reason_comparison(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: a | reason: fine | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "reason_comparison_required"
    assert dw.open_questions(dw.load_events(dw.paths(main)["questions"]))   # still open
    code, data, _, _ = run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    assert code == 0 and data["outcome"] == "agree"


def test_new_reason_is_an_amendment(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: a | reason: other grounds | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", "--new-reason", capsys=capsys)
    assert code == 0 and data["outcome"] == "amendment"
    assert events(main)[-1]["outcome"] == "amendment"


def test_reason_flags_are_refused_on_a_correction(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: no | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    assert code == 1 and data["code"] == "reason_flag_on_correction"


# ------------------------------------------------------------------ T5, T6: the ruling block

@pytest.mark.parametrize("line", [
    "",                                                         # no ruling at all
    "Q1 waiting for Nick",                                      # a placeholder
    "supersedes RULING Q1 | option: a | reason: x | window: aa",  # not at line start
    "RULING Q12 | option: a | reason: x | window: aa",          # another id
    "RULING Q1 | option: c | reason: x | window: aa",           # not an option
    "RULING Q1 | option: a | reason:   | window: aa",           # blank reason
    "RULING Q1 | option: a | reason: x | window:  ",            # blank window
    "RULING Q1 | option: a | reason: x",                        # no window
])
def test_T5_resolve_needs_one_valid_ruling_line(main, capsys, line):
    add(main, capsys)
    rule(main, "alpha-work", line)
    code, data, _, _ = run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    assert code == 1 and data["code"] in ("no_ruling", "bad_ruling")
    assert [e["type"] for e in events(main)] == ["asked"]


def test_T5_two_ruling_lines_are_refused(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: a | reason: x | window: aa")
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: y | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "ambiguous_ruling"


def test_T6_only_the_owners_file_is_read(main, capsys):
    add(main, capsys)
    rule(main, "beta-work", "RULING Q1 | option: b | reason: x | window: bb")
    rule(main, "overnight-swarm", "RULING Q1 | option: b | reason: x | window: org")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "no_ruling"


def test_org_rulings_live_in_overnight_swarm(main, capsys):
    add(main, capsys, owner="org", cls="order")
    rule(main, "overnight-swarm", "RULING Q1 | option: b | reason: x | window: org")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 0 and data["ruling"]["file"].endswith("data/workstreams/overnight-swarm.md")


def test_resolve_refuses_unknown_and_already_resolved(main, capsys):
    code, data, _, _ = run(main, "resolve", "Q4", capsys=capsys)
    assert code == 1 and data["code"] == "unknown_question"
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    assert run(main, "resolve", "Q1", capsys=capsys)[0] == 0
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "already_resolved"


def test_resolved_questions_leave_the_open_list(main, capsys):
    add(main, capsys)
    add(main, capsys, question="Second?")
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    run(main, "resolve", "Q1", capsys=capsys)
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    assert [q["id"] for q in data["open"]] == ["Q2"]
    code, _, _, _ = add(main, capsys)                     # the same text may be asked again
    assert code == 0


# ------------------------------------------------------------------ T17: big items are never approvals

def test_T17_a_big_item_is_routed_and_never_recorded_as_an_approval(main, capsys):
    add(main, capsys, cls="push")
    rule(main, "alpha-work", "RULING Q1 | option: a | reason: ok | window: aa")
    code, data, out, _ = run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    assert code == 0 and data["approval"] == "none"
    assert "not an approval" in data["summary"]
    assert events(main)[-1]["approval"] == "none"


def test_design_items_carry_no_approval_field_either(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    run(main, "resolve", "Q1", capsys=capsys)
    assert events(main)[-1]["approval"] == "none"


def test_approval_payload_is_the_v2_signing_seam(main, capsys):
    add(main, capsys, cls="push")
    q = dw.open_questions(dw.load_events(dw.paths(main)["questions"]))["Q1"]
    ruling = {"option": "a", "window": "aa"}
    payload = dw.approval_payload(q, ruling)
    assert json.loads(payload) == {"namespace": "decision-approval", "question_hash": q["question_hash"],
                                   "option": "a", "owner": "alpha-work", "window": "aa"}
    assert payload == dw.approval_payload(dict(q), dict(ruling))   # deterministic bytes
    assert dw.SIGNING_NAMESPACE == "decision-approval"
    rule(main, "alpha-work", "RULING Q1 | option: a | reason: ok | window: aa")
    run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    ev = events(main)[-1]
    assert ev["payload_hash"] == dw.sha(payload) and ev["signature"] is None


# ------------------------------------------------------------------ T11: routing from the board

def test_T11_routing_is_one_row_or_unrouted(main, capsys):
    add(main, capsys, owner="alpha-work", question="q1?")
    add(main, capsys, owner="beta-work", question="q2?")
    add(main, capsys, owner="gamma-work", question="q3?")          # on two rows
    add(main, capsys, owner="delta-work", question="q4?")          # on none
    add(main, capsys, owner="org", question="q5?", cls="order")
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    route = {q["id"]: q["route"] for q in data["open"]}
    assert route == {"Q1": "aa", "Q2": "bb", "Q3": "UNROUTED: ambiguous (bb, cc)",
                     "Q4": "UNROUTED: no session holds delta-work", "Q5": "org"}


def test_board_session_name_parsing():
    assert dw.session_name("**8f** [5fe949]") == "8f"
    assert dw.session_name("**org** (job-search-4c)") == "org"
    assert dw.session_name(" plain ") == "plain"


def test_a_missing_board_leaves_everything_unrouted(main, capsys):
    (main / "output" / "overnight-merge-back" / "SESSIONS.md").unlink()
    add(main, capsys)
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    assert data["open"][0]["route"].startswith("UNROUTED") and data["board"] == "missing"


def test_list_text_says_where_to_answer_and_flags_big_items(main, capsys):
    add(main, capsys, cls="push", question="Push it?")
    add(main, capsys, owner="delta-work", question="Other?")
    code, _, out, _ = run(main, "list", capsys=capsys)
    assert "answer in aa's window only (big: push)" in out
    assert "UNROUTED" in out and out.index("UNROUTED") < out.index("aa's window")


def test_list_filters_by_owner(main, capsys):
    add(main, capsys, owner="alpha-work", question="one?")
    add(main, capsys, owner="beta-work", question="two?")
    code, data, _, _ = run(main, "list", "--json", "--owner", "beta-work", capsys=capsys)
    assert [q["owner"] for q in data["open"]] == ["beta-work"]


# ------------------------------------------------------------------ T10: organizer handover

def test_T10_takeover_checks_the_board_records_the_step_and_shows_org_work(main, capsys):
    add(main, capsys, owner="org", cls="order", question="Land which first?")
    add(main, capsys, owner="delta-work", question="Unrouted one?")
    add(main, capsys, owner="alpha-work", question="Not org's?")
    code, data, _, _ = run(main, "takeover", "--org", "org", capsys=capsys)
    assert code == 0 and [q["id"] for q in data["org_questions"]] == ["Q1"]
    assert [q["id"] for q in data["unrouted"]] == ["Q2"]
    assert events(main)[-1]["type"] == "takeover" and events(main)[-1]["org"] == "org"


def test_takeover_refuses_a_session_the_board_does_not_name_as_org(main, capsys):
    code, data, _, _ = run(main, "takeover", "--org", "aa", capsys=capsys)
    assert code == 1 and data["code"] == "board_disagrees"
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


# ------------------------------------------------------------------ T7, T24: paths

def test_T7_a_worktree_reads_and_writes_the_main_checkout(main, worktree, capsys):
    code, data, _, _ = add(worktree, capsys)
    assert code == 0
    assert (main / "data" / "run-manifest" / "questions.jsonl").exists()
    assert not (worktree / "data").exists()


def test_T7_a_root_that_is_no_checkout_is_refused(tmp_path, capsys):
    code, data, _, _ = run(tmp_path, "list", capsys=capsys)
    assert code == 1 and data["code"] == "no_main_checkout"


def test_T7_a_worktree_whose_main_is_gone_is_refused(tmp_path, capsys):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'x'}\n")
    code, data, _, _ = run(wt, "list", capsys=capsys)
    assert code == 1 and data["code"] == "no_main_checkout"


def test_T24_a_symlinked_owner_file_is_refused(main, tmp_path, capsys):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("RULING Q1 | option: b | reason: x | window: aa\n")
    target = main / "data" / "workstreams" / "alpha-work.md"
    add(main, capsys)
    target.unlink()
    target.symlink_to(outside)
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "owner_file_outside"


# ------------------------------------------------------------------ T8: concurrency and corrupt files

def test_T8_concurrent_adds_get_unique_ids(main):
    errors = []

    def one(i):
        try:
            dw.main(["--repo-root", str(main), "add", "--owner", "alpha-work", "--class",
                     "scope", "--question", f"question {i}?", "--option", "a: x",
                     "--option", "b: y", "--acts", "z", "--predict", "a",
                     "--predict-reason", "r"])
        except Exception as e:                       # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    # sys.stdout is process-wide: redirect it once around every thread, never per thread
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert not errors
    ids = [e["id"] for e in events(main)]
    assert sorted(ids, key=lambda s: int(s[1:])) == [f"Q{n}" for n in range(1, 9)]
    preds = (main / "data" / "run-manifest" / "predictions.jsonl").read_text().splitlines()
    assert sorted(json.loads(p)["id"] for p in preds) == sorted(ids)


def test_T8_an_unreadable_line_makes_every_command_refuse(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text(p.read_text() + "{not json\n")
    for argv in (["list"], ["resolve", "Q1"], ["takeover", "--org", "org"]):
        code, data, _, _ = run(main, *argv, capsys=capsys)
        assert code == 1 and data["code"] == "corrupt_queue"
    code, data, _, _ = add(main, capsys, question="new?")
    assert code == 1 and data["code"] == "corrupt_queue"


# ------------------------------------------------------------------ T22, T23, T16: check

def test_check_is_clean_on_a_healthy_queue(main, capsys):
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    run(main, "resolve", "Q1", capsys=capsys)
    add(main, capsys, question="still open?")
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 0 and data["problems"] == []


def test_T22_an_edited_question_is_refused_at_resolve_and_reported(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text(p.read_text().replace("Ship part one first?", "Ship everything?"))
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "question_changed"
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and any("Q1" in x and "changed" in x for x in data["problems"])


def test_T23_a_ruling_line_edited_after_resolve_is_reported(main, capsys):
    add(main, capsys)
    f = rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    run(main, "resolve", "Q1", capsys=capsys)
    f.write_text(f.read_text().replace("option: b", "option: a"))
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and any("Q1" in x and "ruling" in x for x in data["problems"])


def test_T16_a_missing_or_changed_prediction_is_reported(main, capsys):
    add(main, capsys)
    add(main, capsys, question="two?")
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    lines = p.read_text().splitlines()
    p.write_text(lines[0].replace('"option": "a"', '"option": "b"') + "\n")
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1
    assert any("Q1" in x and "prediction" in x for x in data["problems"])
    assert any("Q2" in x and "prediction" in x for x in data["problems"])


def test_check_reports_duplicate_ids(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text(p.read_text() * 2)
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and any("duplicate" in x for x in data["problems"])


def test_check_reports_an_unreadable_line(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text(p.read_text() + "{oops\n")
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and any("line" in x for x in data["problems"])


def test_resolve_refuses_when_the_prediction_is_missing(main, capsys):
    add(main, capsys)
    (main / "data" / "run-manifest" / "predictions.jsonl").write_text("")
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "prediction_missing"


def test_question_hash_covers_every_bound_field():
    q = {"id": "Q1", "owner": "alpha-work", "class": "scope", "question": "x?",
         "options": ["a: 1", "b: 2"], "acts": "y", "usd": None}
    base = dw.question_hash(q)
    for key, val in (("id", "Q2"), ("owner", "beta-work"), ("class", "order"), ("question", "z?"),
                     ("options", ["a: 1", "b: 3"]), ("acts", "w"), ("usd", 1.0)):
        assert dw.question_hash(dict(q, **{key: val})) != base, key


# ------------------------------------------------------------------ mutation round 1 additions

def test_the_script_runs_from_any_directory(main, tmp_path):
    """Run as a script, from outside tools/: the import path and the __main__ guard both work."""
    import subprocess
    r = subprocess.run([sys.executable, str(TOOLS / "decision_write.py"), "--repo-root", str(main),
                        "list", "--json"], capture_output=True, text=True, cwd=str(tmp_path),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "ok"


def test_event_times_are_real_iso_timestamps(main, capsys):
    from datetime import datetime
    add(main, capsys)
    assert datetime.fromisoformat(events(main)[0]["at"]).tzinfo is not None


def test_blank_lines_in_the_queue_are_ignored(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text("\n" + p.read_text() + "\n   \n")
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    assert code == 0 and [q["id"] for q in data["open"]] == ["Q1"]


def test_check_names_each_bad_line_once(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "questions.jsonl"
    p.write_text("{oops\n" + p.read_text() + "[1, 2]\n")
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1
    assert data["problems"] == ["questions.jsonl line 1: not JSON", "questions.jsonl line 3: not an object"]


BOARD_EDGES = """# Board

Intro prose mentioning workstreams.

| Other | Table |
|---|---|
| **zz** | alpha-work |

| Session | Owns | Workstreams |
|---|---|---|
| **aa** | a | alpha-work |
| **bb** | missing a cell |
| **cc** | c | - |
| **dd** | d | delta-work, |
Prose ends the table.
| **ee** | e | alpha-work |

| Session | Owns | Workstreams |
|---|---|---|
| **ff** | f | beta-work |
"""


CLEAN_EDGES = """# Board

Intro prose mentioning workstreams.

| Other | Table |
|---|---|
| **zz** | alpha-work |

| Session | Owns | Workstreams |
|---|---|---|
| **aa** | a | alpha-work |
| **cc** | c | - |
| **dd** | d | delta-work, |
Prose ends the table.
| **ee** | e | alpha-work |
"""


def test_board_reads_the_workstreams_table_and_stops_at_prose(tmp_path):
    f = tmp_path / "SESSIONS.md"
    f.write_text(CLEAN_EDGES)
    rows, status = dw.read_board(f)
    assert status == "ok"
    assert [(s, ws) for _, s, ws in rows] == [("aa", ["alpha-work"]), ("cc", []), ("dd", ["delta-work"])]


def test_a_jagged_row_or_a_second_table_makes_the_board_unreadable(tmp_path):
    """Code review r1 (Grok F1): a dropped row could hide a second holder, so nothing is guessed."""
    f = tmp_path / "SESSIONS.md"
    f.write_text(BOARD_EDGES)
    rows, status = dw.read_board(f)
    assert rows is None and status == "unreadable: more than one Workstreams table"
    f.write_text("| Session | Owns | Workstreams |\n|---|---|---|\n| **aa** | a | alpha-work |\n"
                 "| **bb** | missing a cell |\n")
    rows, status = dw.read_board(f)
    assert rows is None and status == "unreadable: line 4 has 2 cells, the header has 3"


def test_board_without_a_separator_or_the_column_is_unreadable(tmp_path):
    f = tmp_path / "SESSIONS.md"
    f.write_text("| Session | Workstreams |\n| **aa** | alpha-work |\n")
    assert dw.read_board(f) == (None, "unreadable: no separator under the Workstreams header")
    f.write_text("| Session | Owns |\n|---|---|\n| **aa** | alpha-work |\n")
    assert dw.read_board(f) == (None, "unreadable: no Workstreams table")
    f.write_text("| Session | Workstreams |")
    assert dw.read_board(f) == (None, "unreadable: no separator under the Workstreams header")
    assert dw.read_board(tmp_path / "absent.md") == (None, "missing")


def test_an_unreadable_board_unroutes_every_question_loudly(main, capsys):
    b = main / "output" / "overnight-merge-back" / "SESSIONS.md"
    b.write_text(b.read_text() + "| **zz** | broken |\n")
    add(main, capsys)
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    assert data["board"].startswith("unreadable: line")
    assert data["open"][0]["route"] == f"UNROUTED: board {data['board']}"


def test_list_text_prints_each_option_and_a_count(main, capsys):
    add(main, capsys)
    code, _, out, _ = run(main, "list", capsys=capsys)
    assert "    a: ship it now\n    b: wait for part two\n" in out
    assert out.rstrip().endswith("1 open question(s); board ok")


def test_resolving_a_later_question_finds_its_own_prediction(main, capsys):
    add(main, capsys)
    add(main, capsys, question="Second?", predict="b", reason="second reason")
    rule(main, "alpha-work", "RULING Q2 | option: b | reason: yes | window: aa")
    code, data, _, _ = run(main, "resolve", "Q2", "--same-reason", capsys=capsys)
    assert code == 0 and data["prediction"] == {"option": "b", "reason": "second reason"}
    assert data["outcome"] == "agree"


def test_a_prediction_edited_before_resolve_is_refused(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    p.write_text(p.read_text().replace('"option": "a"', '"option": "b"'))
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", "--same-reason", capsys=capsys)
    assert code == 1 and data["code"] == "prediction_changed"


def test_routes_come_only_from_routing(main, capsys, monkeypatch):
    """The seam (org relay 2026-11-07): ownership is read in ONE place. With routing() replaced
    and read_board() broken, list still works and shows only routing()'s answers."""
    add(main, capsys)
    add(main, capsys, owner="org", cls="order", question="Order?")
    monkeypatch.setattr(dw, "routing", lambda p: ((lambda owner: f"via-seam:{owner}"), "seam"))
    real = dw.read_board
    monkeypatch.setattr(dw, "read_board", lambda path: (_ for _ in ()).throw(AssertionError("bypass")))
    code, data, _, _ = run(main, "list", "--json", capsys=capsys)
    assert code == 0 and data["board"] == "seam"
    assert {q["route"] for q in data["open"]} == {"via-seam:alpha-work", "via-seam:org"}
    monkeypatch.setattr(dw, "read_board", real)     # takeover's org-row check reads the board itself
    code, data, _, _ = run(main, "takeover", "--org", "org", capsys=capsys)
    assert code == 0 and data["org_questions"][0]["route"] == "via-seam:org"


def test_takeover_accepts_the_session_named_in_the_org_row(main, capsys):
    code, data, _, _ = run(main, "takeover", "--org", "job-search-zz", capsys=capsys)
    assert code == 0 and data["org"] == "job-search-zz"


# ------------------------------------------------------------------ resolve_main contract (shared with
# workstream-backlog-1107, which imports it; agreed with ee 2026-11-07)

def test_resolve_main_contract(main, worktree, tmp_path):
    assert dw.resolve_main(main) == main and isinstance(dw.resolve_main(str(main)), Path)
    assert dw.resolve_main(worktree) == main
    bad = [tmp_path / "plain"]
    bad[0].mkdir()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".git").write_text(f"gitdir: {main / '.git' / 'modules' / 'x'}\n")     # a submodule
    bad.append(sub)
    gone = tmp_path / "gone"
    gone.mkdir()
    (gone / ".git").write_text(f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'x'}\n")
    bad.append(gone)
    for root in bad:
        with pytest.raises(dw.Refusal) as e:
            dw.resolve_main(root)
        assert isinstance(e.value, Exception)
        assert e.value.code == "no_main_checkout" and str(root) in e.value.message and e.value.extra == {}



# ------------------------------------------------------------------ code review r1 fixes (Codex F2-F4)

def edit_event(root, n, **changes):
    p = root / "data" / "run-manifest" / "questions.jsonl"
    lines = p.read_text().splitlines()
    ev = json.loads(lines[n])
    ev.update(changes)
    lines[n] = json.dumps(ev)
    p.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("n,change", [(0, {"kind": "design"}), (0, {"at": "2020-01-01T00:00:00+00:00"}),
                                      (0, {"by": "someone"}), (1, {"approval": "approved"}),
                                      (1, {"signature": "forged"}), (1, {"outcome": "agree"}),
                                      (1, {"window": "zz"})])
def test_check_sees_any_edit_to_any_event_field(main, capsys, n, change):
    add(main, capsys, cls="push")
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    run(main, "resolve", "Q1", capsys=capsys)
    assert run(main, "check", capsys=capsys)[0] == 0
    edit_event(main, n, **change)
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and any("record edited outside the tool" in x for x in data["problems"])


@pytest.mark.parametrize("field,val", [("at", "2020-01-01T00:00:00+00:00"), ("blind", False)])
def test_check_sees_an_edit_to_any_prediction_field(main, capsys, field, val):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    pred = json.loads(p.read_text())
    pred[field] = val
    p.write_text(json.dumps(pred) + "\n")
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and data["problems"] == ["Q1: prediction changed after it was made"]


def test_an_orphan_prediction_id_is_never_reused_and_is_reported(main, capsys):
    """An add interrupted between its two appends leaves a prediction with no question."""
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"id": "Q1", "option": "a", "reason": "r"}) + "\n")
    code, data, _, _ = add(main, capsys)
    assert code == 0 and data["question"]["id"] == "Q2"
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and data["problems"] == ["Q1: prediction with no question (an interrupted add)"]


def test_a_duplicate_prediction_is_refused_and_reported(main, capsys):
    add(main, capsys)
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    p.write_text(p.read_text() * 2)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: x | window: aa")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 1 and data["code"] == "prediction_duplicate"
    code, data, _, _ = run(main, "check", capsys=capsys)
    assert code == 1 and data["problems"] == ["Q1: duplicate prediction"]


def test_the_window_is_recorded_beside_the_route_and_never_enforced(main, capsys):
    """Codex code r1 F1 rejected: requiring window == route would make a typed name evidence again."""
    add(main, capsys)
    rule(main, "alpha-work", "RULING Q1 | option: b | reason: relayed | window: org")
    code, data, _, _ = run(main, "resolve", "Q1", capsys=capsys)
    assert code == 0
    ev = events(main)[-1]
    assert ev["window"] == "org" and ev["route_at_resolve"] == "aa" and ev["approval"] == "none"


def test_ids_skip_anything_that_is_not_a_q_number(main, capsys):
    p = main / "data" / "run-manifest" / "predictions.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"id": "X9"}) + "\n" + json.dumps({"id": None}) + "\n")
    code, data, _, _ = add(main, capsys)
    assert data["question"]["id"] == "Q1"


def test_ids_come_from_questions_even_when_predictions_are_lost(main, capsys):
    add(main, capsys)
    (main / "data" / "run-manifest" / "predictions.jsonl").write_text("")
    code, data, _, _ = add(main, capsys, question="After the loss?")
    assert code == 0 and data["question"]["id"] == "Q2"
