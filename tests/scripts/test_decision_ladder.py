"""Tests for the trust ladder inside decision_write.py (overnight 2026-11-24, section S2).

(a) A class on BLIND_CLASSES is asked with no lean: the asked event says lean_shown false and
    `list` shows no lean. Every other class still needs one.
(b) ladder(): one function computes, per class, the blind record and the rung, from the
    rulings only (output/codify-ui-102726/03-plan.md D1, D2, D6).
(d) takeover reads the org row from the newest board table, not only the Workstreams table.

Generic fixtures only (public repo).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
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
CONTEXT = ["--rests-on", "data/facts.md:1", "--if-nothing", "the owner waits", "--group", "policy"]
LEAN = ["--lean", "a: the data favors it", *CONTEXT]
BLIND = dw.BLIND_CLASSES and sorted(dw.BLIND_CLASSES)[0]


@pytest.fixture
def main(tmp_path):
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    ws = root / "data" / "workstreams"
    ws.mkdir(parents=True)
    (ws / "alpha-work.md").write_text("# Workstream: alpha-work\n", encoding="utf-8")
    (root / "data" / "facts.md").write_text("a fact\n", encoding="utf-8")
    board = root / "output" / "overnight-merge-back"
    board.mkdir(parents=True)
    (board / "SESSIONS.md").write_text(BOARD, encoding="utf-8")
    return root


def run(root, capsys, *argv):
    code = dw.main(["--repo-root", str(root), *argv])
    out, _ = capsys.readouterr()
    try:
        data = json.loads(out) if out.strip() else None
    except ValueError:
        data = None
    return code, data


def add(root, capsys, cls, question, extra, predict="a"):
    return run(root, capsys, "add", "--owner", "alpha-work", "--class", cls,
               "--question", question, "--option", "a: yes", "--option", "b: no",
               "--acts", "the owner acts", "--predict", predict, "--predict-reason", "r", *extra)


def rule_and_resolve(root, capsys, qid, option, flag="--same-reason"):
    f = root / "data" / "workstreams" / "alpha-work.md"
    f.write_text(f.read_text(encoding="utf-8")
                 + f"\nRULING {qid} | option: {option} | reason: because | window: aa\n",
                 encoding="utf-8")
    args = ["resolve", qid] + ([flag] if flag else [])
    return run(root, capsys, *args)


def events(root):
    p = root / "data" / "run-manifest" / "questions.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- (a) blind add ---------------------------------------------------------------------------

def test_workstream_status_is_the_blind_class():
    assert dw.BLIND_CLASSES == {"workstream-status"}
    assert dw.BLIND_CLASSES <= set(dw.CLASSES)


def test_a_blind_class_is_asked_with_no_lean_and_lists_with_none(main, capsys):
    code, data = add(main, capsys, BLIND, "Is alpha done?", CONTEXT)
    assert code == 0, data
    asked = events(main)[-1]
    assert asked["lean_shown"] is False and asked["lean"] is None and asked["lean_label"] is None
    assert asked["rests_on"] == ["data/facts.md:1"] and asked["group"] == "policy"
    code, listed = run(main, capsys, "list", "--json")
    q = listed["open"][0]
    assert q["lean"] is None and q["lean_shown"] is False and q["lean_label"] is None


def test_a_blind_class_refuses_a_lean(main, capsys):
    code, data = add(main, capsys, BLIND, "Is alpha done?", LEAN)
    assert code == 1 and data["code"] == "lean_on_blind_class"
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


@pytest.mark.parametrize("cls", sorted(set(dw.CLASSES) - dw.BLIND_CLASSES - {"spend"}))
def test_every_other_class_still_requires_a_lean(main, capsys, cls):
    code, data = add(main, capsys, cls, "Ship it?", CONTEXT)
    assert code == 1 and data["code"] == "lean_required"
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


def test_a_blind_question_still_needs_its_context_fields(main, capsys):
    with pytest.raises(SystemExit):
        add(main, capsys, BLIND, "Is alpha done?", ["--if-nothing", "x", "--group", "policy"])


def test_blind_seam_add_list_resolve_keeps_lean_shown_false(main, capsys):
    add(main, capsys, BLIND, "Is alpha done?", CONTEXT)
    code, data = rule_and_resolve(main, capsys, "Q1", "a")
    assert code == 0 and data["outcome"] == "agree" and data["lean_shown"] is False
    assert data["prediction"]["option"] == "a"
    resolved = events(main)[-1]
    assert resolved["type"] == "resolved" and resolved["lean_shown"] is False


# --- (b) the ladder ---------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def asked(i, cls, shown=False):
    return {"type": "asked", "id": f"Q{i}", "class": cls, "lean_shown": shown}


def resolved(i, outcome, day, shown=False):
    return {"type": "resolved", "id": f"Q{i}", "outcome": outcome, "lean_shown": shown,
            "at": (T0 + timedelta(days=day)).isoformat()}


def history(outcomes, cls="workstream-status", shown=False, start=1):
    evs = []
    for n, out in enumerate(outcomes):
        i = start + n
        evs += [asked(i, cls, shown), resolved(i, out, n, shown)]
    return evs


def at(day):
    return T0 + timedelta(days=day)


def test_twenty_blind_agreements_climb_to_r2():
    lad = dw.ladder(history(["agree"] * 20), now=at(20))["workstream-status"]
    assert lad["rung"] == "R2" and lad["agreements_since_correction"] == 20
    assert lad["blind_rulings"] == 20 and lad["corrections"] == 0


def test_nineteen_stay_at_r1():
    lad = dw.ladder(history(["agree"] * 19), now=at(19))["workstream-status"]
    assert lad["rung"] == "R1" and lad["agreements_since_correction"] == 19


def test_amendments_count_toward_the_climb_d6():
    lad = dw.ladder(history(["agree"] * 10 + ["amendment"] * 10), now=at(20))["workstream-status"]
    assert lad["rung"] == "R2" and lad["amendments"] == 10


def test_one_correction_demotes_and_resets_the_count_d1():
    lad = dw.ladder(history(["agree"] * 20 + ["correction"]), now=at(21))["workstream-status"]
    assert lad["rung"] == "R1" and lad["agreements_since_correction"] == 0
    assert lad["corrections"] == 1


def test_a_correction_at_r1_does_not_go_below_r1():
    lad = dw.ladder(history(["correction", "agree"]), now=at(2))["workstream-status"]
    assert lad["rung"] == "R1" and lad["agreements_since_correction"] == 1


def test_restored_by_another_twenty_after_a_correction():
    lad = dw.ladder(history(["agree"] * 20 + ["correction"] + ["agree"] * 20),
                    now=at(41))["workstream-status"]
    assert lad["rung"] == "R2" and lad["agreements_since_correction"] == 20


def test_shown_lean_rulings_never_feed_the_rung():
    evs = history(["agree"] * 5) + history(["agree"] * 30, shown=True, start=100)
    lad = dw.ladder(evs, now=at(40))["workstream-status"]
    assert lad["blind_rulings"] == 5 and lad["shown_rulings"] == 30 and lad["rung"] == "R1"


def test_a_shown_correction_does_not_demote():
    evs = history(["agree"] * 20) + history(["correction"], shown=True, start=100)
    assert dw.ladder(evs, now=at(21))["workstream-status"]["rung"] == "R2"


def test_order_is_by_resolve_time_not_file_order():
    evs = history(["agree"] * 20)
    late_fix = [asked(99, "workstream-status"), resolved(99, "correction", -1)]
    lad = dw.ladder(late_fix + evs, now=at(20))["workstream-status"]
    assert lad["rung"] == "R2" and lad["agreements_since_correction"] == 20


def test_days_since_sample_and_lapse_are_reported_not_applied_below_r3():
    lad = dw.ladder(history(["agree"] * 20), now=at(19 + 31))["workstream-status"]
    assert lad["days_since_sample"] == 31 and lad["lapsed"] is True
    assert lad["rung"] == "R2"          # D1(5): a lapse bites only from R3


def test_one_later_ruling_does_not_refresh_the_sample_clock():
    """Codex r1 1331.F1: D1(4) needs a fresh audited SAMPLE (D2: 20 items), not one ruling."""
    evs = history(["agree"] * 20)                     # sample complete on day 19
    evs += [asked(50, "workstream-status"), resolved(50, "agree", 29)]
    lad = dw.ladder(evs, now=at(50))["workstream-status"]
    assert lad["days_since_sample"] == 31 and lad["lapsed"] is True
    assert lad["days_since_ruling"] == 21


def test_a_second_block_of_twenty_is_a_fresh_sample():
    lad = dw.ladder(history(["agree"] * 40), now=at(45))["workstream-status"]
    assert lad["days_since_sample"] == 6 and lad["lapsed"] is False


def test_no_completed_sample_has_no_sample_clock():
    lad = dw.ladder(history(["agree"] * 19), now=at(60))["workstream-status"]
    assert lad["last_sample_at"] is None and lad["days_since_sample"] is None
    assert lad["lapsed"] is False and lad["days_since_ruling"] == 42


def test_a_correction_voids_the_sample_clock_until_twenty_more():
    lad = dw.ladder(history(["agree"] * 20 + ["correction"] + ["agree"] * 5),
                    now=at(30))["workstream-status"]
    assert lad["last_sample_at"] is None and lad["rung"] == "R1"


def test_classes_are_separate_and_open_questions_are_ignored():
    evs = history(["agree"] * 3, cls="scope") + history(["correction"], cls="design-shape", start=50)
    evs.append(asked(200, "scope"))
    lad = dw.ladder(evs, now=at(5))
    assert lad["scope"]["blind_rulings"] == 3 and lad["design-shape"]["corrections"] == 1


def test_ladder_reports_the_d2_threshold():
    assert dw.CLIMB_AT == 20 and dw.LAPSE_DAYS == 30


def test_check_carries_the_ladder(main, capsys):
    add(main, capsys, BLIND, "Is alpha done?", CONTEXT)
    code, data = rule_and_resolve(main, capsys, "Q1", "b", flag=None)   # predicted a
    assert code == 0 and data["outcome"] == "correction"
    code, data = run(main, capsys, "check")
    assert code == 0
    assert data["ladder"]["workstream-status"]["corrections"] == 1
    assert data["ladder"]["workstream-status"]["rung"] == "R1"


# --- (d) takeover reads the newest table ------------------------------------------------------

NEW_TABLE = """# Sessions board

## Live sessions (newest)

| Session (name [ref]) | Owns now | Worktree / branch | Status | Log current? |
|---|---|---|---|---|
| **org** (job-search-new) [abc123] | org | none | ok | yes |
| ff [def456] | former org | none | closed | yes |

## Live sessions (older)

| Session | Owns now | Worktree | Status | Waiting on Nick? | Workstreams |
|---|---|---|---|---|---|
| **org** (job-search-old) | landing order | none | ok | No | - |
| **aa** | a branch | wt | ok | No | alpha-work |
"""


def test_takeover_reads_the_org_row_of_the_newest_table(main, capsys):
    (main / "output" / "overnight-merge-back" / "SESSIONS.md").write_text(NEW_TABLE, encoding="utf-8")
    code, data = run(main, capsys, "takeover", "--org", "job-search-new")
    assert code == 0, data
    assert events(main)[-1]["org"] == "job-search-new"


def test_takeover_refuses_an_org_only_an_older_table_names(main, capsys):
    (main / "output" / "overnight-merge-back" / "SESSIONS.md").write_text(NEW_TABLE, encoding="utf-8")
    code, data = run(main, capsys, "takeover", "--org", "job-search-old")
    assert code == 1 and data["code"] == "board_disagrees"
    assert not (main / "data" / "run-manifest" / "questions.jsonl").exists()


def test_takeover_refuses_two_org_rows_in_the_newest_table(main, capsys):
    two = NEW_TABLE.replace("| ff [def456] |", "| **org** (job-search-x) |")
    (main / "output" / "overnight-merge-back" / "SESSIONS.md").write_text(two, encoding="utf-8")
    code, data = run(main, capsys, "takeover", "--org", "job-search-new")
    assert code == 1 and data["code"] == "board_disagrees"


def test_routing_still_reads_the_workstreams_table(main, capsys):
    (main / "output" / "overnight-merge-back" / "SESSIONS.md").write_text(NEW_TABLE, encoding="utf-8")
    add(main, capsys, "scope", "Ship it?", LEAN)
    code, listed = run(main, capsys, "list", "--json")
    assert listed["open"][0]["route"] == "aa"


def test_newest_org_row_helper():
    assert dw.newest_org_row(NEW_TABLE.splitlines()) == ["**org** (job-search-new) [abc123]"]
    assert dw.newest_org_row(["no table here"]) == []
