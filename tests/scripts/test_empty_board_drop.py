"""A scan target whose board drops to 0 postings is brought up on /standup (empty-board-drop-1121).

Nick 2026-11-21, "4, sure", on Codex empty-board r1 F1: the accept-time empty-board flag
(Nick 2026-11-19: "empty boards can stay scan targets, but I want them to bring it up")
never fired for a target whose board went empty LATER. What must hold:

  * the nightly scan records each ATS target's raw posting count, but only when the fetch
    had no error: a failed fetch is a fetch failure, never an empty board (false-zero rule);
  * a target at 0 is flagged on first sight, or when it had postings at the last scan;
    a board that stays at 0 is not re-flagged, so an acked flag stays acked until the
    board has postings and drops to 0 again;
  * the flag lives in the company queue, written only through company_queue's own API
    under its lock, and `read` / `ack-empty` treat it like the accept-time flag;
  * a board that has postings again stops being named.

Placeholder companies only (public repo).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import tools.company_queue as cq  # noqa: E402
from tools.career_scanner import scanner  # noqa: E402

DAY1, DAY2, DAY3 = "2026-01-05", "2026-01-06", "2026-01-07"


def _c(name, count, ats="ashby", slug=None):
    return {"name": name, "ats": ats, "slug": slug or name.lower().replace(" ", "-"),
            "count": count}


def _state(tmp_path):
    return json.loads(cq.queue_path(tmp_path).read_text())


# --- the transition rule -----------------------------------------------------------------

def test_a_target_at_zero_on_first_sight_is_flagged(tmp_path):
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", 4)], DAY1)
    assert out["flagged"] == ["Acme"]
    names = [b["name"] for b in cq.read(tmp_path)["empty_boards"]]
    assert names == ["Acme"]


def test_a_board_that_stays_at_zero_is_not_flagged_again(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY2)
    assert out["flagged"] == []
    flag = _state(tmp_path)["scan_boards"]["board:ashby:acme"]["empty_board"]
    assert flag["detected_on"] == DAY1


def test_an_acked_flag_stays_acked_while_the_board_stays_empty(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    assert cq.ack_empty(tmp_path, "Acme")["status"] == "ok"
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY2)
    assert cq.read(tmp_path)["empty_boards"] == []


def test_postings_then_zero_again_flags_again_after_an_ack(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    cq.ack_empty(tmp_path, "Acme")
    cq.record_board_counts(tmp_path, [_c("Acme", 3)], DAY2)
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY3)
    assert out["flagged"] == ["Acme"]
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b["detected_on"] == DAY3 and b["source"] == "scan"


def test_a_board_that_drops_from_postings_to_zero_is_flagged(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 5)], DAY1)
    assert cq.read(tmp_path)["empty_boards"] == []
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY2)
    assert out["flagged"] == ["Acme"]


def test_a_board_with_postings_again_stops_being_named(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    out = cq.record_board_counts(tmp_path, [_c("Acme", 2)], DAY2)
    assert out["recovered"] == ["Acme"]
    assert cq.read(tmp_path)["empty_boards"] == []
    assert _state(tmp_path)["scan_boards"]["board:ashby:acme"]["last_count"] == 2


def test_the_read_line_carries_ats_slug_and_source(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme Labs", 0, ats="lever", slug="acmelabs")], DAY1)
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b == {"name": "Acme Labs", "ats": "lever", "slug": "acmelabs",
                 "detected_on": DAY1, "source": "scan", "after": "first_scan"}


def test_an_accept_time_flag_is_not_doubled_by_the_scan(tmp_path):
    cq.queue_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cq.queue_path(tmp_path).write_text(json.dumps({"pending": [], "decided": [
        {"name": "Acme", "decision": "accept", "board": {"ats": "ashby", "slug": "acme"},
         "empty_board": {"detected_on": DAY1, "acked_on": None}}]}))
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY2)
    assert out["flagged"] == []
    assert [b["detected_on"] for b in cq.read(tmp_path)["empty_boards"]] == [DAY1]
    assert _state(tmp_path)["scan_boards"]["board:ashby:acme"]["last_count"] == 0


def test_an_accept_time_flag_acked_then_postings_then_zero_flags_from_the_scan(tmp_path):
    cq.queue_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cq.queue_path(tmp_path).write_text(json.dumps({"pending": [], "decided": [
        {"name": "Acme", "decision": "accept", "board": {"ats": "ashby", "slug": "acme"},
         "empty_board": {"detected_on": DAY1, "acked_on": DAY1}}]}))
    cq.record_board_counts(tmp_path, [_c("Acme", 1)], DAY2)
    assert cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY3)["flagged"] == ["Acme"]


def test_a_target_left_out_of_the_counts_is_untouched(tmp_path):
    # The scanner leaves out a target whose fetch failed; its state must not move.
    cq.record_board_counts(tmp_path, [_c("Acme", 4)], DAY1)
    cq.record_board_counts(tmp_path, [_c("Beta", 1)], DAY2)
    assert _state(tmp_path)["scan_boards"]["board:ashby:acme"]["last_count"] == 4


@pytest.mark.parametrize("bad", [None, -1, "0", True, 1.0])
def test_a_count_that_is_not_a_non_negative_int_is_ignored(tmp_path, bad):
    out = cq.record_board_counts(tmp_path, [_c("Acme", bad)], DAY1)
    assert out["flagged"] == [] and out["ignored"] == 1
    assert "board:ashby:acme" not in _state(tmp_path).get("scan_boards", {})


def test_record_writes_through_the_queue_lock(tmp_path, monkeypatch):
    seen = []
    real = cq.inbox_lock.atomic_update

    def spy(path, transform, *a, **k):
        seen.append(Path(path))
        return real(path, transform, *a, **k)
    monkeypatch.setattr(cq.inbox_lock, "atomic_update", spy)
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    assert seen == [cq.queue_path(tmp_path)]


def test_record_keeps_pending_and_decided(tmp_path):
    cq.add(tmp_path, "lane-a", "company", [{"name": "Gamma", "score": 5}], DAY1)
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY2)
    s = _state(tmp_path)
    assert [e["name"] for e in s["pending"]] == ["Gamma"] and s["decided"] == []


def test_a_scan_boards_section_that_is_not_an_object_reads_as_unreadable(tmp_path):
    cq.queue_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cq.queue_path(tmp_path).write_text(json.dumps({"pending": [], "decided": [],
                                                    "scan_boards": [1]}))
    r = cq.read(tmp_path)
    assert r["exists"] is False and r["empty_boards"] is None


# --- the scanner side --------------------------------------------------------------------

@pytest.fixture
def fake_board(monkeypatch):
    def _install(roles, err=None):
        import tools.career_scanner.ashby as mod

        def fetch(slug, errors=None):
            if err and errors is not None:
                errors.append({"reason": err})
            return [dict(r) for r in roles]
        monkeypatch.setattr(mod, "fetch_ashby", fetch)
    return _install


def _target(**kw):
    t = {"name": "Placeholder Co", "ats": "ashby", "slug": "placeholder"}
    t.update(kw)
    return t


def test_fetch_records_the_raw_count_of_an_empty_board(fake_board):
    fake_board([])
    raw = {}
    scanner.fetch_company_roles(_target(), errors=[], raw_counts=raw)
    assert raw == {"ashby:placeholder": 0}


def test_fetch_records_the_raw_count_before_title_cuts(fake_board):
    fake_board([{"title": "Forward Deployed Engineer", "location": "New York, NY", "url": "u"}])
    raw = {}
    out = scanner.fetch_company_roles(_target(role_filters=["strategist"]), errors=[],
                                      cuts=[], raw_counts=raw)
    assert out == [] and raw == {"ashby:placeholder": 1}


def test_a_fetch_error_records_no_count(fake_board):
    fake_board([], err="HTTP 404")
    raw, errors = {}, []
    scanner.fetch_company_roles(_target(), errors=errors, raw_counts=raw)
    assert raw == {} and errors


def test_a_generic_target_records_no_count(monkeypatch):
    import tools.career_scanner.generic as mod
    monkeypatch.setattr(mod, "fetch_generic", lambda url, name, errors=None: [])
    raw = {}
    scanner.fetch_company_roles(_target(ats="generic", careers_url="https://example.com/jobs"),
                                errors=[], raw_counts=raw)
    assert raw == {}


def _scan(tmp_path, monkeypatch, fetch, dry_run=False, recorder=None):
    monkeypatch.setattr(scanner, "load_targets", lambda root: [
        _target(name="Acme", slug="acme"), _target(name="Beta", slug="beta")])
    monkeypatch.setattr(scanner, "fetch_company_roles", fetch)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    if recorder is not None:
        monkeypatch.setattr(cq, "record_board_counts", recorder)
    (tmp_path / "data").mkdir(exist_ok=True)
    return scanner.scan_all_targets(tmp_path, dry_run=dry_run)


def _fetch_acme_empty_beta_fails(target, errors=None, cuts=None, raw_counts=None):
    if target["name"] == "Beta":
        errors.append({"company": "Beta", "ats": "ashby", "reason": "HTTP 500"})
        return []
    raw_counts[f"{target['ats']}:{target['slug']}"] = 0
    return []


def test_the_scan_flags_an_empty_target_and_skips_a_failed_one(tmp_path, monkeypatch):
    s = _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails)
    assert s["empty_boards_flagged"] == ["Acme"] and s["empty_board_error"] is None
    assert [b["name"] for b in cq.read(tmp_path)["empty_boards"]] == ["Acme"]
    assert "board:ashby:beta" not in _state(tmp_path)["scan_boards"]


def test_a_dry_run_writes_no_flag(tmp_path, monkeypatch):
    calls = []
    s = _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails, dry_run=True,
              recorder=lambda *a, **k: calls.append(a))
    assert calls == [] and not cq.queue_path(tmp_path).exists()
    assert s["empty_boards_flagged"] == []


def test_a_failing_company_queue_write_never_fails_the_scan(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    s = _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails, recorder=boom)
    assert "disk full" in s["empty_board_error"] and s["empty_boards_flagged"] == []
    assert scanner.read_queue_payload(tmp_path)  # the role queue was still written


def test_an_old_style_fetch_without_raw_counts_still_scans(tmp_path, monkeypatch):
    # Every pre-existing fake fetch takes (target, errors=None, cuts=None).
    s = _scan(tmp_path, monkeypatch, lambda target, errors=None, cuts=None: [])
    assert s["empty_boards_flagged"] == [] and s["empty_board_error"] is None
    # No counts: the company queue is not touched at all.
    assert not cq.queue_path(tmp_path).exists()


def test_a_fetch_whose_signature_cannot_be_read_gets_no_raw_counts():
    assert scanner._takes_raw_counts(max) is False  # inspect.signature(max) raises
    assert scanner._takes_raw_counts(scanner.fetch_company_roles) is True


def test_a_failing_flag_write_is_said_on_stderr(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")
    _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails, recorder=boom)
    assert "empty-board flags not recorded (disk full)" in capsys.readouterr().err


def test_acking_one_scan_flag_leaves_the_others_named(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", 0)], DAY1)
    assert cq.ack_empty(tmp_path, "Beta")["name"] == "Beta"
    assert [b["name"] for b in cq.read(tmp_path)["empty_boards"]] == ["Acme"]


def test_a_retried_transform_reports_one_attempt_not_two(tmp_path, monkeypatch):
    # atomic_update may call the transform again after an external write; the result
    # must describe the attempt that was written, not the sum of attempts.
    real = cq.inbox_lock.atomic_update

    def twice(path, transform, *a, **k):
        transform(Path(path).read_text() if Path(path).exists() else "")
        return real(path, transform, *a, **k)
    monkeypatch.setattr(cq.inbox_lock, "atomic_update", twice)
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", "x")], DAY1)
    assert out == {"flagged": ["Acme"], "recovered": [], "retired": [], "ignored": 1, "stale": 0}


# --- identity and ordering (Codex r1 F1-F3) ----------------------------------------------

def test_two_targets_with_one_display_name_keep_separate_counts(tmp_path):
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"),
                                            _c("Acme", 7, slug="acme-eu")], DAY1)
    assert out["flagged"] == ["Acme"]
    s = _state(tmp_path)["scan_boards"]
    assert s["board:ashby:acme-us"]["last_count"] == 0 and s["board:ashby:acme-eu"]["last_count"] == 7


def test_names_that_normalize_alike_do_not_share_state(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme Co", 3, slug="a1"), _c("ACME-co", 0, slug="a2")], DAY1)
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b["slug"] == "a2"


def test_a_renamed_target_keeps_its_board_state(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme")], DAY1)
    out = cq.record_board_counts(tmp_path, [_c("Acme Labs", 0, slug="acme")], DAY2)
    assert out["flagged"] == []
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b["name"] == "Acme Labs" and b["detected_on"] == DAY1


def test_a_board_no_longer_a_target_stops_being_named(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    out = cq.record_board_counts(tmp_path, [_c("Beta", 2)], DAY2, current={"board:ashby:beta"})
    assert out["retired"] == ["Acme"]
    assert cq.read(tmp_path)["empty_boards"] == []


def test_a_target_whose_fetch_failed_is_still_a_target_and_keeps_its_flag(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    out = cq.record_board_counts(tmp_path, [_c("Beta", 2)], DAY2,
                                 current={"board:ashby:beta", "board:ashby:acme"})
    assert out["retired"] == []
    assert [b["name"] for b in cq.read(tmp_path)["empty_boards"]] == ["Acme"]


def test_without_a_target_set_nothing_is_retired(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    assert cq.record_board_counts(tmp_path, [_c("Beta", 2)], DAY2)["retired"] == []


def test_an_older_observation_never_overwrites_a_newer_one(tmp_path):
    newer = {**_c("Acme", 4), "observed_at": "2026-01-06T09:00:00+00:00"}
    older = {**_c("Acme", 0), "observed_at": "2026-01-06T08:00:00+00:00"}
    cq.record_board_counts(tmp_path, [newer], DAY2)
    out = cq.record_board_counts(tmp_path, [older], DAY2)
    assert out["flagged"] == [] and out["stale"] == 1
    assert _state(tmp_path)["scan_boards"]["board:ashby:acme"]["last_count"] == 4


def test_a_newer_observation_is_applied(tmp_path):
    cq.record_board_counts(tmp_path, [{**_c("Acme", 4), "observed_at": "2026-01-06T08:00:00+00:00"}], DAY1)
    out = cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": "2026-01-06T09:00:00+00:00"}], DAY2)
    assert out["flagged"] == ["Acme"]


def test_a_count_without_ats_or_slug_is_ignored(tmp_path):
    out = cq.record_board_counts(tmp_path, [{"name": "Acme", "ats": "ashby", "slug": "", "count": 0}], DAY1)
    assert out["ignored"] == 1 and out["flagged"] == []


def test_the_scan_passes_every_board_target_and_one_observation_time(tmp_path, monkeypatch):
    seen = {}

    def rec(root, counts, today, current=None, observed_at=None):
        seen.update(counts=counts, current=current)
        return {"flagged": [], "recovered": [], "retired": [], "ignored": 0, "stale": 0}
    _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails, recorder=rec)
    assert seen["current"] == {"board:ashby:acme", "board:ashby:beta"}
    assert [c["slug"] for c in seen["counts"]] == ["acme"]
    assert seen["counts"][0]["observed_at"]


def test_an_accepted_company_whose_board_was_not_empty_is_flagged_by_the_scan(tmp_path):
    cq.queue_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cq.queue_path(tmp_path).write_text(json.dumps({"pending": [], "decided": [
        {"name": "Acme", "decision": "accept", "board": {"ats": "ashby", "slug": "acme"}},
        {"name": "Beta", "decision": "accept", "board": "corrupt",
         "empty_board": {"detected_on": DAY1, "acked_on": None}}]}))
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", 0)], DAY2)
    assert out["flagged"] == ["Acme", "Beta"]


def test_a_count_without_an_observation_time_keeps_the_recorded_one(tmp_path):
    cq.record_board_counts(tmp_path, [{**_c("Acme", 4), "observed_at": "2026-01-06T09:00:00+00:00"}], DAY1)
    cq.record_board_counts(tmp_path, [_c("Acme", 4)], DAY2)
    out = cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": "2026-01-06T08:00:00+00:00"}], DAY2)
    assert out["stale"] == 1 and out["flagged"] == []



# --- Grok r1 F1-F4 -----------------------------------------------------------------------

def test_a_night_with_no_successful_counts_still_retires_a_removed_board(tmp_path, monkeypatch):
    cq.record_board_counts(tmp_path, [_c("Gone", 0, slug="gone")], DAY1)

    def all_fail(target, errors=None, cuts=None, raw_counts=None):
        errors.append({"company": target["name"], "ats": "ashby", "reason": "HTTP 500"})
        return []
    _scan(tmp_path, monkeypatch, all_fail)
    assert cq.read(tmp_path)["empty_boards"] == []
    rec = _state(tmp_path)["scan_boards"]["board:ashby:gone"]
    assert rec["empty_board"]["retired_on"]


def test_a_night_where_every_fetch_fails_retires_nothing_still_targeted(tmp_path, monkeypatch):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme")], DAY1)

    def all_fail(target, errors=None, cuts=None, raw_counts=None):
        errors.append({"company": target["name"], "ats": "ashby", "reason": "HTTP 500"})
        return []
    _scan(tmp_path, monkeypatch, all_fail)
    assert [b["name"] for b in cq.read(tmp_path)["empty_boards"]] == ["Acme"]


def test_ack_by_a_name_two_live_boards_share_is_refused(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY1)
    out = cq.ack_empty(tmp_path, "Acme")
    assert out["status"] == "error" and out["code"] == "ambiguous"
    assert len(cq.read(tmp_path)["empty_boards"]) == 2


def test_ack_with_a_board_clears_only_that_board(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY1)
    out = cq.ack_empty(tmp_path, "Acme", board="ashby:acme-eu")
    assert out["status"] == "ok"
    assert [b["slug"] for b in cq.read(tmp_path)["empty_boards"]] == ["acme-us"]


def test_ack_with_a_board_that_does_not_match_writes_nothing(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    out = cq.ack_empty(tmp_path, "Acme", board="ashby:other")
    assert out["status"] == "error" and len(cq.read(tmp_path)["empty_boards"]) == 1


def test_ack_skips_a_recovered_flag_and_finds_the_live_one(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY1)
    cq.record_board_counts(tmp_path, [_c("Acme", 3, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY2)
    assert cq.ack_empty(tmp_path, "Acme")["status"] == "ok"  # only one live flag left
    assert cq.read(tmp_path)["empty_boards"] == []
    assert _state(tmp_path)["scan_boards"]["board:ashby:acme-us"]["empty_board"]["acked_on"] is None


def test_the_cli_passes_board_to_ack(tmp_path, capsys):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY1)
    code = cq.main(["--repo-root", str(tmp_path), "ack-empty", "--name", "Acme", "--board", "ashby:acme-us"])
    assert code == 0
    assert [b["slug"] for b in cq.read(tmp_path)["empty_boards"]] == ["acme-eu"]


def test_a_retired_board_targeted_again_at_zero_is_named_again(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    cq.record_board_counts(tmp_path, [_c("Beta", 1)], DAY2, current={"board:ashby:beta"})
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY3,
                                 current={"board:ashby:beta", "board:ashby:acme"})
    assert out["flagged"] == ["Acme"]
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b["detected_on"] == DAY3


def test_the_flag_says_whether_the_board_was_empty_from_the_start_or_dropped(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", 5)], DAY1)
    cq.record_board_counts(tmp_path, [_c("Acme", 0), _c("Beta", 0)], DAY2)
    after = {b["name"]: b["after"] for b in cq.read(tmp_path)["empty_boards"]}
    assert after == {"Acme": "first_scan", "Beta": "postings"}


def test_a_scan_with_no_ats_board_never_touches_the_company_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "load_targets", lambda root: [
        _target(name="Gen", ats="generic", slug="", careers_url="https://example.com/jobs")])
    monkeypatch.setattr(scanner, "fetch_company_roles",
                        lambda target, errors=None, cuts=None, raw_counts=None: [])
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    (tmp_path / "data").mkdir(exist_ok=True)
    scanner.scan_all_targets(tmp_path)
    assert not cq.queue_path(tmp_path).exists()


# --- Codex r2 F1-F3 ----------------------------------------------------------------------

def _with_accept_flag(tmp_path, name="Acme", slug="acme-old", acked=None):
    cq.queue_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cq.queue_path(tmp_path).write_text(json.dumps({"pending": [], "decided": [
        {"name": name, "decision": "accept", "board": {"ats": "ashby", "slug": slug},
         "empty_board": {"detected_on": DAY1, "acked_on": acked}}]}))


def test_a_bare_ack_matching_an_accept_flag_and_a_scan_flag_is_refused(tmp_path):
    _with_accept_flag(tmp_path)
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-new")], DAY2)
    out = cq.ack_empty(tmp_path, "Acme")
    assert out["status"] == "error" and out["code"] == "ambiguous"
    assert sorted(out["boards"]) == ["ashby:acme-new", "ashby:acme-old"]
    assert len(cq.read(tmp_path)["empty_boards"]) == 2


def test_ack_with_a_board_selects_an_accept_time_flag(tmp_path):
    _with_accept_flag(tmp_path)
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-new")], DAY2)
    assert cq.ack_empty(tmp_path, "Acme", board="ashby:acme-old")["status"] == "ok"
    [b] = cq.read(tmp_path)["empty_boards"]
    assert b["slug"] == "acme-new" and b["source"] == "scan"


def test_a_bare_ack_with_only_an_accept_flag_still_works(tmp_path):
    _with_accept_flag(tmp_path)
    assert cq.ack_empty(tmp_path, "Acme")["status"] == "ok"
    assert cq.read(tmp_path)["empty_boards"] == []


def test_an_acked_board_removed_then_retargeted_at_zero_is_named_again(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    cq.ack_empty(tmp_path, "Acme")
    cq.record_board_counts(tmp_path, [_c("Beta", 1)], DAY2, current={"board:ashby:beta"})
    out = cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY3,
                                 current={"board:ashby:beta", "board:ashby:acme"})
    assert out["flagged"] == ["Acme"]


def test_an_older_scan_cannot_retire_what_a_newer_scan_observed(tmp_path):
    new, old = "2026-01-06T09:00:00+00:00", "2026-01-06T08:00:00+00:00"
    cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": new}], DAY2,
                           current={"board:ashby:acme"}, observed_at=new)
    out = cq.record_board_counts(tmp_path, [], DAY2, current={"board:ashby:beta"},
                                 observed_at=old)
    assert out["retired"] == []
    assert [b["name"] for b in cq.read(tmp_path)["empty_boards"]] == ["Acme"]


def test_an_older_count_cannot_undo_a_newer_retirement(tmp_path):
    new, old = "2026-01-06T09:00:00+00:00", "2026-01-06T08:00:00+00:00"
    cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": "2026-01-06T07:00:00+00:00"}], DAY1)
    cq.record_board_counts(tmp_path, [], DAY2, current=set(), observed_at=new)
    out = cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": old}], DAY2,
                                 current={"board:ashby:acme"}, observed_at=old)
    assert out["stale"] == 1 and cq.read(tmp_path)["empty_boards"] == []


def test_the_scan_passes_its_observation_time_for_retirement(tmp_path, monkeypatch):
    seen = {}

    def rec(root, counts, today, current=None, observed_at=None):
        seen.update(observed_at=observed_at, count_at=counts[0]["observed_at"])
        return {"flagged": [], "recovered": [], "retired": [], "ignored": 0, "stale": 0}
    _scan(tmp_path, monkeypatch, _fetch_acme_empty_beta_fails, recorder=rec)
    assert seen["observed_at"] and seen["observed_at"] == seen["count_at"]


def test_a_retargeted_board_flagged_at_zero_is_not_flagged_again_the_next_night(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    cq.record_board_counts(tmp_path, [], DAY2, current=set())
    both = {"board:ashby:acme"}
    assert cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY3, current=both)["flagged"] == ["Acme"]
    assert cq.record_board_counts(tmp_path, [_c("Acme", 0)], "2026-01-08", current=both)["flagged"] == []
    assert "retired_on" not in _state(tmp_path)["scan_boards"]["board:ashby:acme"]


def test_retiring_without_an_observation_time_keeps_the_recorded_one(tmp_path):
    cq.record_board_counts(tmp_path, [{**_c("Acme", 0), "observed_at": "2026-01-06T09:00:00+00:00"}], DAY1)
    cq.record_board_counts(tmp_path, [], DAY2, current=set())
    rec = _state(tmp_path)["scan_boards"]["board:ashby:acme"]
    assert rec["observed_at"] == "2026-01-06T09:00:00+00:00" and rec["retired_on"] == DAY2


def test_retiring_an_acked_board_reports_no_named_flag(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0)], DAY1)
    cq.ack_empty(tmp_path, "Acme")
    out = cq.record_board_counts(tmp_path, [], DAY2, current=set())
    assert out["retired"] == []
    rec = _state(tmp_path)["scan_boards"]["board:ashby:acme"]
    assert rec["retired_on"] == DAY2 and "retired_on" not in rec["empty_board"]


# --- Grok r2 F1 --------------------------------------------------------------------------

def test_ack_accepts_the_board_as_the_standup_line_prints_it(tmp_path):
    cq.record_board_counts(tmp_path, [_c("Acme", 0, slug="acme-us"), _c("Acme", 0, slug="acme-eu")], DAY1)
    assert cq.ack_empty(tmp_path, "Acme", board="ashby/acme-eu")["status"] == "ok"
    assert [b["slug"] for b in cq.read(tmp_path)["empty_boards"]] == ["acme-us"]


def test_the_standup_ack_instruction_names_the_board_option():
    text = (REPO / ".claude" / "skills" / "standup" / "SKILL.md").read_text(encoding="utf-8")
    assert 'ack-empty --name "<Name>" [--board <ats>/<slug>]' in text


# --- merge with company-key-1121 (Grok resolution review F2) -----------------------------

def test_ack_matches_scan_flags_by_exact_company_not_the_lossy_key(tmp_path):
    # norm() maps both names to "m ller labs"; company_key.exact keeps them apart, so a
    # bare ack of one is not refused as ambiguous and clears only that one.
    cq.record_board_counts(tmp_path, [_c("Møller Labs", 0, slug="moller"),
                                      _c("Möller Labs", 0, slug="moeller")], DAY1)
    out = cq.ack_empty(tmp_path, "Möller Labs")
    assert out["status"] == "ok" and out["name"] == "Möller Labs"
    assert [b["slug"] for b in cq.read(tmp_path)["empty_boards"]] == ["moller"]
