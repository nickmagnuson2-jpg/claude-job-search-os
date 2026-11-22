"""Tests for the morning page's "Decisions waiting" block (Build M, orchestration.md 2026-11-21
19:05): generated from tools/decision_write.py's open-question listing, grouped time-bound /
role / policy, each item with its lean, the data line it rests on, and what happens if Nick
does nothing. It replaces a hand-typed NEEDS-NICK list (a typed status block is a probe you
failed to write).

Demo fixture: tests/fixtures/morning_page/decisions.json (placeholders only).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import morning_page as mp  # noqa: E402

NOW = datetime(2026, 1, 7, 9, 0)


def _section(page, ident):
    m = re.search(rf'<section[^>]*id="{ident}"[^>]*>(.*?)</section>', page, re.S)
    assert m, ident
    return m.group(1)


def _page(decisions):
    src = {**mp.gather_demo(), "decisions": decisions}
    return mp.render(mp.build_view(src, NOW), NOW, demo=True)


def _q(i, **kw):
    base = {"id": f"Q{i}", "owner": "org", "class": "scope", "kind": "design",
            "question": f"Question {i}?", "options": ["a: x", "b: y"], "acts": "act",
            "usd": None, "at": "2026-01-06T19:00:00+00:00", "by": "s", "route": "job-search-aa"}
    return {**base, **kw}


# --- source -------------------------------------------------------------------------------

def test_decisions_is_a_page_source_with_a_demo_fixture():
    assert "decisions" in mp.SOURCES
    d = mp.gather_demo()["decisions"]
    assert d["error"] is None and len(d["open"]) == 5


def test_live_source_reads_decision_writes_own_listing(monkeypatch, tmp_path):
    seen = {}

    def fake_listing(p):
        seen["p"] = p
        return [_q(1)], "ok"

    monkeypatch.setattr(mp.decision_write, "resolve_main", lambda root: tmp_path)
    monkeypatch.setattr(mp.decision_write, "listing", fake_listing)
    out = mp.decisions_waiting(tmp_path)
    assert out == {"error": None, "board": "ok", "open": [_q(1)]}
    assert seen["p"]["questions"] == tmp_path / "data" / "run-manifest" / "questions.jsonl"


def test_live_source_failure_is_stated_not_hidden(monkeypatch, tmp_path):
    def boom(root):
        raise mp.decision_write.Refusal("no_main_checkout", "not a main checkout")

    monkeypatch.setattr(mp.decision_write, "resolve_main", boom)
    out = mp.decisions_waiting(tmp_path)
    assert out["open"] is None and "not a main checkout" in out["error"]
    page = _page(out)
    assert "Decisions unavailable" in _section(page, "decisions")
    assert "not a main checkout" in _section(page, "decisions")


def test_an_unreadable_queue_line_is_stated(monkeypatch, tmp_path):
    def bad(p):
        raise json.JSONDecodeError("Expecting value", "x", 0)

    monkeypatch.setattr(mp.decision_write, "resolve_main", lambda root: tmp_path)
    monkeypatch.setattr(mp.decision_write, "listing", bad)
    out = mp.decisions_waiting(tmp_path)
    assert out["open"] is None and "Expecting value" in out["error"]


def test_gather_live_includes_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(mp.standup_collect, "collect", lambda root, d, read_only: {"results": {}})
    monkeypatch.setattr(mp.day_check, "collect", lambda root, d: {})
    monkeypatch.setattr(mp, "calendar_events", lambda root: {"events": []})
    monkeypatch.setattr(mp, "waiting_on_them", lambda root: {"error": None, "rows": []})
    monkeypatch.setattr(mp.funnel_state, "collect", lambda root, now: {})
    monkeypatch.setattr(mp, "decisions_waiting", lambda root: {"error": None, "board": "ok",
                                                              "open": [_q(7)]})
    src = mp.gather_live(tmp_path, NOW)
    assert src["decisions"]["open"][0]["id"] == "Q7"


# --- grouping and order -------------------------------------------------------------------

def test_groups_come_in_order_time_bound_role_policy_then_ungrouped():
    v = mp.build_view(mp.gather_demo(), NOW)["decisions"]
    assert [g["name"] for g in v["groups"]] == ["time-bound", "role", "policy", "ungrouped"]
    assert v["total"] == 5


def test_time_bound_sorts_by_date_soonest_first():
    v = mp.build_view(mp.gather_demo(), NOW)["decisions"]
    tb = v["groups"][0]
    assert [i["id"] for i in tb["items"]] == ["Q1", "Q3"]
    assert tb["items"][0]["by_date"] == "2026-01-08"


def test_a_time_bound_item_without_a_date_sorts_last():
    qs = [_q(1, group="time-bound", by_date=None), _q(2, group="time-bound", by_date="2026-01-09")]
    v = mp.decisions_view({"error": None, "board": "ok", "open": qs})
    assert [i["id"] for i in v["groups"][0]["items"]] == ["Q2", "Q1"]


def test_an_unknown_group_value_is_ungrouped_not_dropped():
    v = mp.decisions_view({"error": None, "board": "ok", "open": [_q(1, group="someday")]})
    assert [g["name"] for g in v["groups"]] == ["ungrouped"]
    assert v["groups"][0]["items"][0]["id"] == "Q1" and v["total"] == 1


def test_empty_groups_are_left_out():
    v = mp.decisions_view({"error": None, "board": "ok", "open": [_q(1, group="policy")]})
    assert [g["name"] for g in v["groups"]] == ["policy"]


def test_no_open_questions_says_so():
    page = _page({"error": None, "board": "ok", "open": []})
    assert "No open questions in <code>data/run-manifest/questions.jsonl</code>." in _section(page, "decisions")


# --- each item carries lean, data line, if-nothing ----------------------------------------

def test_an_item_shows_question_lean_rests_on_if_nothing_and_route():
    sec = _section(_page(mp.gather_demo()["decisions"]), "decisions")
    assert "Reply to Beta Inc&#x27;s recruiter with Tuesday or Wednesday?" in sec
    assert "Lean: a: Tuesday keeps Wednesday free" in sec
    assert "data/job-pipeline.md:12" in sec
    assert "If nothing: the recruiter&#x27;s hold on both slots lapses" in sec
    assert "answer in job-search-bb" in sec
    assert "by 2026-01-08" in sec


def test_every_rests_on_line_is_shown():
    sec = _section(_page(mp.gather_demo()["decisions"]), "decisions")
    assert "data/goals.md:130" in sec and "data/goals.md:131" in sec


def test_a_legacy_row_without_the_fields_says_what_is_missing():
    sec = _section(_page(mp.gather_demo()["decisions"]), "decisions")
    m = re.search(r"Rewrite the example hook instead of blocking\?(.*?)</li>", sec, re.S)
    assert m
    assert "no lean recorded" in m.group(1) and "no data line recorded" in m.group(1)
    assert "UNROUTED: no session holds hooks" in m.group(1)


def test_the_sealed_prediction_is_never_shown():
    q = _q(1, group="policy", lean="a: shown", predict="b", predict_reason="sealed text")
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "sealed text" not in sec and "Lean: a: shown" in sec


def test_item_text_is_escaped():
    q = _q(1, group="policy", question="<script>x</script>?", lean="<b>a</b>")
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "<script>" not in sec and "&lt;script&gt;" in sec and "<b>a</b>" not in sec


def test_a_group_longer_than_the_cap_says_how_many_more():
    qs = [_q(i, group="policy") for i in range(mp.MAX_ITEMS + 2)]
    sec = _section(_page({"error": None, "board": "ok", "open": qs}), "decisions")
    assert sec.count("<li") == mp.MAX_ITEMS
    assert "and 2 more" in sec and "decision_write.py list" in sec


def test_the_heading_carries_the_total_from_the_queue():
    sec = _section(_page(mp.gather_demo()["decisions"]), "decisions")
    assert re.search(r"<h2>Decisions waiting \(5\)</h2>", sec)


# --- placement ----------------------------------------------------------------------------

def test_decisions_sit_after_waiting_on_me_and_before_waiting_on_them():
    page = _page(mp.gather_demo()["decisions"])
    pos = [page.index(f'id="{i}"') for i in ("b3", "b1", "b2", "decisions", "theirs", "b5", "b6")]
    assert pos == sorted(pos)


def test_a_long_question_is_cut_to_the_cap_with_an_ellipsis():
    q = _q(1, group="policy", question="x" * 500)
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "x" * (mp.QUESTION_CHARS - 1) + "\u2026" in sec and "x" * mp.QUESTION_CHARS not in sec


def test_a_question_at_the_cap_is_not_cut():
    q = _q(1, group="policy", question="y" * mp.QUESTION_CHARS)
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "y" * mp.QUESTION_CHARS in sec and "\u2026" not in sec
