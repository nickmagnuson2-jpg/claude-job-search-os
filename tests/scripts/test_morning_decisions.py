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
    assert out == {"error": None, "board": "ok", "open": [_q(1)], "ladder": {}, "ladder_error": None}
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


def test_a_question_asked_over_a_prior_ruling_names_the_override():
    q = _q(1, group="policy", not_answered={"topics": ["topic-a", "topic-b"],
                                             "reason": "the ruling predates the change"})
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "overrides topic-a, topic-b: the ruling predates the change" in sec


def test_no_override_line_without_not_answered():
    sec = _section(_page({"error": None, "board": "ok", "open": [_q(1, group="policy")]}),
                   "decisions")
    assert "overrides" not in sec


def test_a_group_exactly_at_the_cap_has_no_more_line():
    qs = [_q(i, group="policy") for i in range(mp.MAX_ITEMS)]
    sec = _section(_page({"error": None, "board": "ok", "open": qs}), "decisions")
    assert sec.count("<li") == mp.MAX_ITEMS and "more:" not in sec


@pytest.mark.parametrize("exc", [KeyError("id"), TypeError("bad row"), AttributeError("x")])
def test_a_schema_damaged_queue_is_stated_not_a_crash(monkeypatch, tmp_path, exc):
    """Codex r1 F3: {"type": "asked"} raises KeyError in listing(); the launchd job must still
    write the page, saying the source is unavailable."""
    def bad(p):
        raise exc

    monkeypatch.setattr(mp.decision_write, "resolve_main", lambda root: tmp_path)
    monkeypatch.setattr(mp.decision_write, "listing", bad)
    out = mp.decisions_waiting(tmp_path)
    assert out["open"] is None and type(exc).__name__ in out["error"]


def test_a_real_malformed_asked_event_is_stated(tmp_path):
    """The same, through the real listing on a scratch main checkout."""
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    rm = root / "data" / "run-manifest"
    rm.mkdir(parents=True)
    (rm / "questions.jsonl").write_text('{"type": "asked"}\n', encoding="utf-8")
    out = mp.decisions_waiting(root)
    assert out["open"] is None and out["error"]


@pytest.mark.parametrize("bad", [
    {"rests_on": 7}, {"rests_on": "data/goals.md:1"}, {"not_answered": "a string"},
    {"not_answered": {"topics": 5, "reason": "r"}}, {"options": None, "question": None},
])
def test_a_wrongly_typed_field_renders_instead_of_crashing(bad):
    """Grok r1 F1: listing() succeeded, but a field of the wrong type must not abort the page."""
    q = _q(1, group="policy", **bad)
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "Q1" in sec


def test_a_string_rests_on_is_not_split_into_characters():
    q = _q(1, group="policy", rests_on="data/goals.md:1")
    sec = _section(_page({"error": None, "board": "ok", "open": [q]}), "decisions")
    assert "<code>d</code>" not in sec and "no data line recorded" in sec


# --- the trust ladder line (overnight 2026-11-24, S2) ---------------------------------------
# One line per class, read ONLY from decision_write.ladder(): the page never counts rungs itself.

def _ladder_section(ladder):
    return _section(_page({"error": None, "board": "ok", "open": [], "ladder": ladder}), "decisions")


def _rung(**kw):
    base = {"rung": "R1", "climb_at": 20, "blind_rulings": 10, "shown_rulings": 0, "corrections": 0,
            "amendments": 0, "agreements_since_correction": 10,
            "last_sample_at": None, "days_since_sample": None, "lapsed": False,
            "last_ruling_at": "2026-01-01T00:00:00+00:00", "days_since_ruling": 6}
    return {**base, **kw}


def test_one_ladder_line_per_class_with_the_count_toward_twenty():
    sec = _ladder_section({"scope": _rung(), "workstream-status": _rung(agreements_since_correction=12)})
    assert "workstream-status: R1, 12 of 20 blind agreements since the last correction" in sec
    assert "scope: R1, 10 of 20" in sec
    assert sec.index("scope:") < sec.index("workstream-status:")


def test_a_climbed_class_reads_r2():
    sec = _ladder_section({"workstream-status": _rung(rung="R2", agreements_since_correction=20)})
    assert "workstream-status: R2, 20 blind agreements since the last correction" in sec


def test_a_demoted_class_shows_its_correction():
    sec = _ladder_section({"workstream-status": _rung(agreements_since_correction=0, corrections=1)})
    assert "workstream-status: R1, 0 of 20" in sec and "1 correction" in sec


def test_a_lapsed_class_says_so():
    sec = _ladder_section({"scope": _rung(rung="R2", agreements_since_correction=20,
                                          days_since_sample=31, lapsed=True)})
    assert "last sample of 20 completed 31 days ago, lapsed" in sec
    assert "last blind ruling" not in sec


def test_a_class_with_no_completed_sample_shows_its_last_ruling():
    sec = _ladder_section({"scope": _rung()})
    assert "last blind ruling 6 days ago" in sec and "last sample" not in sec


def test_shown_lean_rulings_are_named_as_not_counted():
    sec = _ladder_section({"scope": _rung(shown_rulings=4)})
    assert "4 shown-lean rulings not counted" in sec


def test_no_shown_lean_clause_when_none_were_shown():
    """Mutation 2026-11-24 (_ladder_line IF_TRUE): a zero count is not printed."""
    sec = _ladder_section({"scope": _rung(shown_rulings=0)})
    assert "shown-lean" not in sec


def test_no_last_ruling_clause_without_a_ruling_date():
    """Mutation 2026-11-24 (_ladder_line IF_TRUE): no sample and no ruling date prints neither."""
    sec = _ladder_section({"scope": _rung(days_since_ruling=None, last_ruling_at=None)})
    assert "last blind ruling" not in sec and "None" not in sec and "last sample" not in sec


def test_a_ladder_that_is_not_a_mapping_is_unavailable_not_a_crash():
    """Mutation 2026-11-24 (checked_ladder IF_FALSE / RETURN_NONE)."""
    page = _page({"error": None, "board": "ok", "open": [_q(1)], "ladder": ["scope"]})
    sec = _section(page, "decisions")
    assert "Trust ladder unavailable: ladder is list, not a mapping" in sec
    assert "Question 1?" in sec


def test_no_ladder_says_none_resolved_yet():
    assert "No blind rulings resolved yet" in _ladder_section({})


def test_a_missing_ladder_is_stated_not_hidden():
    sec = _section(_page({"error": None, "board": "ok", "open": []}), "decisions")
    assert "Trust ladder unavailable" in sec


# Codex 1339 / Grok 1342 (org b7 ruling: fix all three before commit 2).

def test_the_threshold_and_correction_count_come_from_the_ladder_record():
    """F1: the page supplies no number of its own: "of N" is the record's climb_at."""
    sec = _ladder_section({"scope": _rung(climb_at=7, agreements_since_correction=3, corrections=5)})
    assert "scope: R1, 3 of 7 blind agreements" in sec and "5 corrections" in sec
    assert "of 20" not in sec


@pytest.mark.parametrize("bad", [None, [], "x", {"rung": "R1"},
                                 {**{"rung": "R1", "climb_at": 20, "corrections": 0,
                                     "agreements_since_correction": "ten"}}])
def test_a_malformed_class_record_makes_the_ladder_unavailable_not_a_crash(bad):
    """F2: a bad record never reaches the renderer; the reason is stated; questions still list."""
    page = _page({"error": None, "board": "ok", "open": [_q(1)], "ladder": {"scope": bad}})
    sec = _section(page, "decisions")
    assert "Trust ladder unavailable: scope" in sec
    assert "Question 1?" in sec


@pytest.mark.parametrize("exc", [ValueError, RuntimeError])
def test_a_ladder_failure_keeps_the_open_questions(monkeypatch, tmp_path, exc):
    """F3, and Codex 1344.F2: ANY ladder exception is stated and the questions still show."""
    monkeypatch.setattr(mp.decision_write, "resolve_main", lambda root: tmp_path)
    monkeypatch.setattr(mp.decision_write, "listing", lambda p: ([_q(1)], "ok"))

    def boom(events):
        raise exc("bad timestamp")

    monkeypatch.setattr(mp.decision_write, "ladder", boom)
    out = mp.decisions_waiting(tmp_path)
    assert out["open"] == [_q(1)] and out["ladder"] is None
    assert f"{exc.__name__}: bad timestamp" in out["ladder_error"]
    page = mp.render(mp.build_view({**mp.gather_demo(), "decisions": out}, NOW), NOW, demo=True)
    sec = _section(page, "decisions")
    assert f"Trust ladder unavailable: {exc.__name__}: bad timestamp" in sec and "Question 1?" in sec


def test_the_demo_ladder_is_valid_and_renders():
    """Grok 1347.F1: the packaged demo fixture must pass checked_ladder."""
    v = mp.build_view(mp.gather_demo(), NOW)["decisions"]
    assert v["ladder_error"] is None and set(v["ladder"]) == {"design-shape", "workstream-status"}
    sec = _section(mp.render(mp.build_view(mp.gather_demo(), NOW), NOW, demo=True), "decisions")
    assert "workstream-status: R2, 20 blind agreements" in sec


def test_ladder_lines_are_not_clamped_to_two_lines():
    """Grok 1347.F2: every li is clamped to two lines unless its list opts out."""
    page = mp.render(mp.build_view(mp.gather_demo(), NOW), NOW, demo=True)
    assert "ul.ladder li{display:block" in page


def test_the_line_reads_decision_writes_ladder_function(monkeypatch, tmp_path):
    """Mutation guard: swap ladder() for a sentinel; the page must show exactly the sentinel."""
    monkeypatch.setattr(mp.decision_write, "resolve_main", lambda root: tmp_path)
    monkeypatch.setattr(mp.decision_write, "listing", lambda p: ([], "ok"))
    monkeypatch.setattr(mp.decision_write, "ladder",
                        lambda events: {"sentinel-class": _rung(agreements_since_correction=17,
                                                                climb_at=31, corrections=4)})
    src = {**mp.gather_demo(), "decisions": mp.decisions_waiting(tmp_path)}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    sec = _section(page, "decisions")
    assert "sentinel-class: R1, 17 of 31" in sec and "4 corrections" in sec


def test_a_blind_open_question_says_no_lean_shown():
    sec = _section(_page({"error": None, "board": "ok", "open": [_q(1, lean=None, lean_shown=False)],
                          "ladder": {}}), "decisions")
    assert "blind: no lean shown" in sec and "no lean recorded" not in sec


def _main(tmp_path, outcomes):
    """A real queue on disk: blind workstream-status questions resolved with these outcomes."""
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    q = root / "data" / "run-manifest"
    q.mkdir(parents=True)
    rows = []
    for n, out in enumerate(outcomes, 1):
        rows.append({"type": "asked", "id": f"Q{n}", "class": "workstream-status", "lean_shown": False})
        rows.append({"type": "resolved", "id": f"Q{n}", "outcome": out, "lean_shown": False,
                     "at": f"2026-01-{n % 28 + 1:02d}T00:00:{n % 60:02d}+00:00"})
    (q / "questions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return root


@pytest.mark.parametrize("outcomes,line", [
    (["agree"] * 20, "workstream-status: R2, 20 blind agreements"),
    (["agree"] * 20 + ["correction"], "workstream-status: R1, 0 of 20"),
])
def test_climb_and_demotion_show_on_a_real_queue(tmp_path, monkeypatch, outcomes, line):
    root = _main(tmp_path, outcomes)
    monkeypatch.setattr(mp.decision_write, "listing", lambda p: ([], "ok"))
    src = {**mp.gather_demo(), "decisions": mp.decisions_waiting(root)}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert line in _section(page, "decisions")
