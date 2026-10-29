"""Tests for tools/booking_tracker.py. Generic fixtures only (public repo)."""
import csv
import datetime as dt
import json
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import booking_tracker as bt  # noqa: E402

UTC = dt.timezone.utc


def slot(sid, start, offering="Sauna Session", price=40, cap=30, res=0, rules=()):
    return {"id": sid, "datetime": start, "time": "6:00 PM", "offering_name": offering, "duration": 90,
            "price": price, "capacity": cap, "reservations": res, "waitlist_count": 0, "category": "shared",
            "capacity_context": {"held_capacity": 0},
            "pricing_context": {"rules_applied": [{"name": r} for r in rules]}}


def page(props):
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": props}})}</script></html>'


MENU = {"bootstrap": {"discovery": {"filters": [{"key": "offering_name", "options": [
    {"label": "Sauna Session"}, {"label": "New Space", "enabled": False}]}]}},
        "orgConfig": {"bookingPageStatus": "LIVE"}}
STORE = {"entries": [{"entryType": "SECTION_HEADER"},
                     {"entryType": "PRODUCT", "displayName": "5 Pack", "product": {"productType": "PASS", "priceAmount": 200}}]}
VENUE = {"name": "Example Spa", "handle": "examplespa", "timezone": "America/Chicago",
         "primary_offering": "Sauna Session", "primary_capacity": 30, "watch_offering": "New Space"}


def fake_fetch(payload, menu=MENU, store=STORE, fail=()):
    def fetch(url, headers=None):
        if "/api/" in url:
            assert headers == {"X-Handle": "examplespa"}
            return json.dumps(payload)
        if url.endswith("/store"):
            if "store" in fail:
                raise OSError("404")
            return page(store)
        if "menu" in fail:
            raise OSError("404")
        return page(menu)
    return fetch


def payload(slots, more=False):
    return {"data": {"2026-01-05": slots}, "pagination": {"hasMore": more, "lastSlotDate": None}}


def test_truncated_or_missing_feed_raises():
    with pytest.raises(ValueError):
        bt.slots_from_payload(payload([], more=True))
    with pytest.raises(ValueError):
        bt.slots_from_payload({"error": "x"})
    with pytest.raises(ValueError):
        bt.slots_from_payload({"data": None, "pagination": {"hasMore": False}})
    for pag in (None, {}, {"lastSlotDate": None}, {"hasMore": None}):   # no explicit hasMore: false
        p = {"data": {"2026-01-05": []}}
        if pag is not None:
            p["pagination"] = pag
        with pytest.raises(ValueError):
            bt.slots_from_payload(p)
    assert bt.slots_from_payload(payload([])) == ([], {"hasMore": False, "lastSlotDate": None})


def test_empty_pages_raise():
    with pytest.raises(ValueError):
        bt.page_props("<html></html>")
    with pytest.raises(ValueError):
        bt.menu_from_props({})
    with pytest.raises(ValueError):
        bt.store_from_props({"entries": []})
    with pytest.raises(ValueError):
        bt.store_from_props({})
    # structurally nonempty pages with nothing in them are outages, not empty catalogs
    with pytest.raises(ValueError):
        bt.menu_from_props({"bootstrap": {"discovery": {"filters": [{"key": "k", "options": []}]}}})
    with pytest.raises(ValueError):
        bt.store_from_props({"entries": [{"entryType": "SECTION_HEADER"}]})
    with pytest.raises(ValueError):
        bt.store_from_props({"entries": [dict(STORE["entries"][1], isVisible=False)]})


def test_menu_and_store_parse():
    menu, status = bt.menu_from_props(MENU)
    assert menu == {"offering_name: Sauna Session": "on", "offering_name: New Space": "off"} and status == "LIVE"
    assert bt.store_from_props(STORE) == {"5 Pack": "PASS $200"}


def test_diff_reports_added_removed_and_changed():
    old = {"menu": {"a": "off"}, "store": {"p": "PASS $200"}, "price_rules": ["Peak"], "offering_prices": [],
           "watched_slots": ["2026-01-05T01:00 New Space cap 8"], "page_status": "LIVE", "last_slot_date": "2026-02-01"}
    new = {"menu": {"a": "on"}, "store": {"p": "PASS $220"}, "price_rules": ["Peak", "Off-Peak"], "offering_prices": [],
           "watched_slots": ["2026-01-06T01:00 New Space cap 8"], "page_status": "LIVE", "last_slot_date": "2026-03-01"}
    d = bt.diff(old, new)
    assert "menu: a: off -> on" in d and "store: p: PASS $200 -> PASS $220" in d
    assert "price_rules: added Off-Peak" in d
    assert any("watched slots: 1 new" in x for x in d) and any("watched slots: 1 removed" in x for x in d)
    assert "last_slot_date: 2026-02-01 -> 2026-03-01" in d
    assert bt.diff(new, new) == []


def test_run_venue_baseline_then_change_then_outage(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    now = dt.datetime(2026, 1, 5, 20, 0, tzinfo=UTC)
    s1 = [slot("A", "2026-01-06T01:00:00+0000", res=3, rules=["Peak"]), slot("W", "2026-01-07T01:00:00+0000", offering="New Space", cap=8)]
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1)), now=now)
    assert r["slots"] == 2 and r["reservations"] == 3 and r["changes"] == 1
    changes = (tmp_path / "changes.md").read_text()
    assert "baseline" in changes and "1 watched slots" in changes
    # a store outage keeps last values: no false change, error logged
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1), fail=("store",)), now=now + dt.timedelta(hours=1))
    assert r["changes"] == 0 and r["errors"]
    assert "store" in (tmp_path / "errors.log").read_text()
    # the watched slot disappears: reported as removed
    before = (tmp_path / "changes.md").read_text()
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1)), now=now + dt.timedelta(hours=2))   # nothing changed: file untouched
    assert r["changes"] == 0 and (tmp_path / "changes.md").read_text() == before
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1[:1])), now=now + dt.timedelta(hours=3))
    text = (tmp_path / "changes.md").read_text()
    assert r["changes"] >= 1 and "1 removed" in text and text.count("# Booking-page changes") == 1
    rows = list(csv.DictReader(open(tmp_path / "snapshots.csv")))
    assert len(rows) == 7 and rows[0]["local_date"] == "2026-01-05"   # 01:00 UTC is the evening before in Chicago


def test_truncated_feed_writes_nothing(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        bt.run_venue(v, fetch=fake_fetch(payload([slot("A", "2026-01-06T01:00:00+0000")], more=True)))
    assert not (tmp_path / "snapshots.csv").exists()


def row(snap, sid, start, offering="Sauna Session", price=40, cap=30, res=0):
    return {"snapshot_utc": snap, "slot_id": sid, "slot_start_utc": start, "local_date": "2026-01-05",
            "local_time": "6:00 PM", "offering": offering, "price": str(price), "capacity": str(cap), "reservations": str(res)}


def test_final_rows_use_last_pre_start_snapshot_and_drop_canceled():
    rows = [row("2026-01-05T20:00:00Z", "A", "2026-01-06T01:00:00+0000", res=3),
            row("2026-01-05T23:00:00Z", "A", "2026-01-06T01:00:00+0000", res=9),
            row("2026-01-06T02:00:00Z", "A", "2026-01-06T01:00:00+0000", res=99),   # after start: ignored
            row("2026-01-05T20:00:00Z", "B", "2026-01-06T01:30:00+0000", res=5)]    # missing at 23:00: canceled
    final = bt.final_rows(rows, dt.datetime(2026, 1, 7, tzinfo=UTC))
    assert [(r["slot_id"], r["reservations"]) for r in final] == [("A", "9")]


def test_final_rows_empty_snapshot_cancels():
    rows = [row("2026-01-05T20:00:00Z", "B", "2026-01-06T01:30:00+0000", res=5)]
    now = dt.datetime(2026, 1, 7, tzinfo=UTC)
    assert [r["slot_id"] for r in bt.final_rows(rows, now)] == ["B"]
    assert bt.final_rows(rows, now, ["2026-01-05T20:00:00Z", "2026-01-05T23:00:00Z"]) == []
    assert len(bt.final_rows(rows, now, ["2026-01-06T02:00:00Z"])) == 1   # after start: not a cancellation


def test_run_venue_empty_schedule_cancels_in_summary(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    b = slot("B", "2026-01-06T01:30:00+0000", res=5)
    bt.run_venue(v, fetch=fake_fetch(payload([b])), now=dt.datetime(2026, 1, 5, 20, tzinfo=UTC))
    assert "2026-01-05      1       5" in bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC))
    r = bt.run_venue(v, fetch=fake_fetch(payload([])), now=dt.datetime(2026, 1, 5, 23, tzinfo=UTC))
    assert r["slots"] == 0
    assert (tmp_path / "snapshot-times.txt").read_text().split() == ["2026-01-05T20:00:00Z", "2026-01-05T23:00:00Z"]
    assert bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC)) == "No slot has started since the first snapshot yet."


@pytest.mark.parametrize("part", ["menu", "store"])
def test_first_run_partial_read_writes_no_baseline(tmp_path, part):
    v = dict(VENUE, data_dir=str(tmp_path))
    s1 = [slot("A", "2026-01-06T01:00:00+0000", res=3)]
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1), fail=(part,)), now=dt.datetime(2026, 1, 5, tzinfo=UTC))
    assert r["changes"] == 0 and r["slots"] == 1 and r["errors"][0].startswith(part)
    assert not (tmp_path / "changes.md").exists() and not (tmp_path / "watch-state.json").exists()
    assert len(list(csv.DictReader(open(tmp_path / "snapshots.csv")))) == 1     # slots still kept
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1)), now=dt.datetime(2026, 1, 5, tzinfo=UTC))
    text = (tmp_path / "changes.md").read_text()
    assert r["changes"] == 1 and "baseline: 2 menu options (1 switched off), 1 store items" in text
    assert "added" not in text


def test_primary_filter_excludes_other_offerings_free_and_odd_capacity():
    assert bt.is_primary(row("x", "A", "y"), VENUE)
    assert not bt.is_primary(row("x", "A", "y", offering="Late Session"), VENUE)
    assert not bt.is_primary(row("x", "A", "y", price=0), VENUE)
    assert not bt.is_primary(row("x", "A", "y", cap=20), VENUE)


def test_one_failing_venue_does_not_stop_others(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, name="Bad", data_dir=str(tmp_path / "b")),
                                          dict(VENUE, name="Good", data_dir=str(tmp_path / "g"))]}))
    calls = []

    def fake_run(v, fetch=None, now=None):
        calls.append(v["name"])
        if v["name"] == "Bad":
            raise OSError("down")
        return {"slots": 1, "reservations": 0, "changes": 0, "errors": []}
    monkeypatch.setattr(bt, "run_venue", fake_run)
    assert bt.main(["--config", str(cfg)]) == 1
    assert calls == ["Bad", "Good"]


def test_get_retries_then_succeeds_and_finally_raises(monkeypatch):
    sleeps, calls = [], {"n": 0}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("404")
        return Resp()
    monkeypatch.setattr(bt.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bt.time, "sleep", sleeps.append)
    assert bt.get("http://x") == "ok" and sleeps == [5, 10]
    calls["n"], sleeps[:] = -10, []
    with pytest.raises(OSError):
        bt.get("http://x", tries=2)
    assert sleeps == [5]


def test_page_props_and_parse_utc():
    assert bt.page_props(page(MENU)) == MENU
    assert bt.parse_utc("2026-01-06T01:00:00+0000") == bt.parse_utc("2026-01-06T01:00:00Z")
    assert bt.parse_utc("2026-01-06T01:00:00+00:00") == dt.datetime(2026, 1, 6, 1, tzinfo=UTC)


def test_final_rows_order_independent():
    rows = [row("2026-01-05T23:00:00Z", "A", "2026-01-06T01:00:00+0000", res=9),
            row("2026-01-05T20:00:00Z", "A", "2026-01-06T01:00:00+0000", res=3)]
    assert [r["reservations"] for r in bt.final_rows(rows, dt.datetime(2026, 1, 7, tzinfo=UTC))] == ["9"]


def test_parse_utc_works_on_a_strict_fromisoformat(monkeypatch):
    """Python 3.10's fromisoformat rejects '+0000' and 'Z'; parse_utc must normalize both first."""
    class Strict(dt.datetime):
        @classmethod
        def fromisoformat(cls, s):
            if not re.search(r"[+-]\d\d:\d\d$", s):
                raise ValueError(f"3.10 rejects {s!r}")
            return dt.datetime.fromisoformat(s)
    monkeypatch.setattr(bt, "dt", types.SimpleNamespace(datetime=Strict))
    want = dt.datetime(2026, 1, 6, 1, tzinfo=UTC)
    assert bt.parse_utc("2026-01-06T01:00:00+0000") == want
    assert bt.parse_utc("2026-01-06T01:00:00Z") == want
    assert bt.parse_utc("2026-01-06T01:00:00-0500") == dt.datetime(2026, 1, 6, 6, tzinfo=UTC)
    assert bt.parse_utc("2026-01-06T01:00:00+00:00") == want


def test_diff_truncates_long_slot_lists():
    many = [f"2026-01-{d:02d}T01:00 New Space cap 8" for d in range(1, 8)]
    assert any(x.endswith(" ...)") for x in bt.diff({"watched_slots": []}, {"watched_slots": many}))
    assert not any(" ..." in x for x in bt.diff({"watched_slots": []}, {"watched_slots": many[:6]}))


def test_run_venue_files(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path / "d"))
    menu3 = {"bootstrap": {"discovery": {"filters": [{"key": "k", "options": [
        {"label": "a"}, {"label": "b"}, {"label": "c", "enabled": False}]}]}}}
    bt.run_venue(v, fetch=fake_fetch(payload([slot("A", "2026-01-06T01:00:00+0000")]), menu=menu3),
                 now=dt.datetime(2026, 1, 5, tzinfo=UTC))
    text = (tmp_path / "d" / "changes.md").read_text()
    assert text.startswith("# Booking-page changes: Example Spa") and "3 menu options (1 switched off)" in text
    assert not (tmp_path / "d" / "errors.log").exists()
    bt.run_venue(v, fetch=fake_fetch(payload([slot("A", "2026-01-06T01:00:00+0000")]), fail=("menu",)),
                 now=dt.datetime(2026, 1, 5, 1, tzinfo=UTC))
    assert "menu:" in (tmp_path / "d" / "errors.log").read_text()
    assert (tmp_path / "d" / "changes.md").read_text().count("# Booking-page changes") == 1


def write_snaps(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bt.FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_summary_report(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    assert bt.summary(v) == "no snapshots yet"
    write_snaps(tmp_path / "snapshots.csv", [row("2026-01-05T20:00:00Z", "A", "2099-01-06T01:00:00+0000", res=3)])
    assert bt.summary(v) == "No slot has started since the first snapshot yet."
    write_snaps(tmp_path / "snapshots.csv", [
        row("2026-01-05T20:00:00Z", "A", "2026-01-06T01:00:00+0000", res=8),
        row("2026-01-05T20:00:00Z", "B", "2026-01-06T03:00:00+0000", offering="Late Session", price=30, res=2)])
    out = bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC))
    assert "2026-01-05      2      10               8           380" in out   # 8*40 + 2*30
    assert "Mon  6:00 PM:   8.0  (n=1)" in out and "Regular 'Sauna Session' sessions (paid, 30 seats)" in out


def test_main_venue_filter_and_summary(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, data_dir=str(tmp_path / "a")), dict(VENUE, name="Other", data_dir=str(tmp_path / "o"))]}))
    assert bt.main(["--config", str(cfg), "--summary", "--venue", "Example Spa"]) == 0
    out = capsys.readouterr().out
    assert "== Example Spa\nno snapshots yet" in out and "Other" not in out
    with pytest.raises(SystemExit):
        bt.main(["--config", str(cfg), "--venue", "Nope"])


def test_main_prints_results_and_failures(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, data_dir=str(tmp_path / "a")), dict(VENUE, name="Bad", data_dir=str(tmp_path / "b"))]}))

    def fake_run(v, fetch=None, now=None):
        if v["name"] == "Bad":
            raise OSError("down")
        return {"slots": 3, "reservations": 4, "changes": 1, "errors": ["store: x"]}
    monkeypatch.setattr(bt, "run_venue", fake_run)
    assert bt.main(["--config", str(cfg)]) == 1
    o = capsys.readouterr()
    assert "Example Spa: 3 slots, 4 reservations, 1 change line(s), errors: ['store: x']" in o.out
    assert "Bad: FAILED down" in o.err
    assert "| population 2 | included 0 | excluded: not selected by --venue 0, failed 1, partial read 1" in o.out


@pytest.mark.parametrize("errors,code", [([], 0), (["store: x"], 1)])
def test_main_partial_read_fails_the_run(tmp_path, monkeypatch, errors, code):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, data_dir=str(tmp_path / "a"))]}))
    monkeypatch.setattr(bt, "run_venue", lambda v, fetch=None, now=None:
                        {"slots": 3, "reservations": 4, "changes": 0, "errors": errors})
    assert bt.main(["--config", str(cfg)]) == code


def lrow(snap, sid, start, **kw):
    r = row(snap, sid, start, **{k: v for k, v in kw.items() if k in ("offering", "price", "cap", "res")})
    r.update({"held": str(kw.get("held", 0)), "waitlist": str(kw.get("waitlist", 0)),
              "category": kw.get("category", "shared"), "event_name": kw.get("event_name", "")})
    return r


def test_slot_observations_lag_and_baselines():
    start = "2026-01-06T02:00:00+0000"   # 8:00 PM Chicago on 01-05
    rows = [lrow("2026-01-05T12:00:00Z", "A", start, res=1),   # 6 AM local: previous-day? no, same local date
            lrow("2026-01-05T23:55:00Z", "A", start, res=4),   # 2h05 before start: the 2h baseline
            lrow("2026-01-06T01:55:00Z", "A", start, res=7),   # last pre-start read, 5 min before
            lrow("2026-01-06T02:00:00Z", "A", start, res=50)]  # AT start: not a pre-start read
    tz = bt.ZoneInfo("America/Chicago")
    [o] = bt.slot_observations(rows, dt.datetime(2026, 1, 7, tzinfo=UTC), tz=tz)
    assert (o["final"], o["lag_min"], o["base_day"], o["base_day_at"], o["base_2h"], o["base_2h_lag"]) == (7, 5, 1, "06:00", 4, 125)


def test_slot_observations_missing_baselines_and_unstarted():
    tz = bt.ZoneInfo("America/Chicago")
    rows = [lrow("2026-01-06T01:00:00Z", "A", "2026-01-06T02:00:00+0000", res=3),   # one read, 1h before
            lrow("2026-01-06T01:00:00Z", "B", "2099-01-06T02:00:00+0000", res=3)]   # not started
    [o] = bt.slot_observations(rows, dt.datetime(2026, 1, 7, tzinfo=UTC), tz=tz)
    assert o["row"]["slot_id"] == "A" and o["base_2h"] is None and o["base_day"] == 3
    rows = [lrow("2026-01-05T01:00:00Z", "C", "2026-01-06T02:00:00+0000", res=2)]   # only read is the day before
    [o] = bt.slot_observations(rows, dt.datetime(2026, 1, 7, tzinfo=UTC), tz=tz)
    assert o["base_day"] is None and o["base_2h"] == 2


def test_anomaly_predicates():
    o = {"row": {"offering": "Sauna Session", "event_name": " Community Night "}, "price": 0.0, "capacity": 24}
    assert bt.anomaly(o, {"Sauna Session": 30}) == ["price $0", "capacity 24 vs usual 30", "named 'Community Night'"]
    assert bt.anomaly({"row": {"offering": "Sauna Session"}, "price": 40.0, "capacity": 30}, {"Sauna Session": 30}) == []


def test_levers_report_blocks(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    s1, s2, s3 = "2026-01-06T00:00:00+0000", "2026-01-06T01:00:00+0000", "2026-01-06T02:00:00+0000"
    write_snaps(tmp_path / "snapshots.csv", [
        lrow("2026-01-05T15:00:00Z", "A", s1, res=2), lrow("2026-01-05T21:55:00Z", "A", s1, res=10),
        lrow("2026-01-05T23:55:00Z", "A", s1, res=30, waitlist=3),
        lrow("2026-01-05T15:00:00Z", "B", s2, price=0, cap=20, res=0, event_name="Buyout"),
        lrow("2026-01-05T23:55:00Z", "B", s2, price=0, cap=20, res=0, event_name="Buyout"),
        lrow("2026-01-05T15:00:00Z", "C", s3, offering="Yoga", price=15, res=1, category="class"),
        lrow("2026-01-05T23:55:00Z", "C", s3, offering="Yoga", price=15, res=4, category="class", held=2)])
    out = bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC), levers=True)
    assert "COVERAGE: 3 finished slots" in out and "1 within 10 min of start, 0 at 11-30 min, 2 over 30 min" in out
    assert "price $0; capacity 20 vs usual 30; named 'Buyout'" in out
    assert "capacity 30 vs usual" not in out   # a tie between 30 and 20 must not flag the normal slot
    assert "(1 candidates; 1 other slots with held seats" in out
    assert "31 of 34 bookings" in out and "n=2 slots" in out and "1 zero-booking slots excluded" in out
    assert "Added in the last 2 hours before start: 20 bookings, n=2 slots" in out and "1 slots without one excluded" in out
    assert bt.NOT_VISIBLE in out
    assert "COVERAGE" not in bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC))


def test_main_levers_flag(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, data_dir=str(tmp_path / "a"))]}))
    assert bt.main(["--config", str(cfg), "--levers"]) == 0
    assert "== Example Spa\nno snapshots yet" in capsys.readouterr().out


def test_lever_headings_and_helpers():
    assert bt.num("", 5) == 5 and bt.num("x", 7) == 7 and bt.num("2.5") == 2.5
    assert bt.hour_key("9:00 AM") < bt.hour_key("10:00 AM") < bt.hour_key("6:00 PM")
    assert bt.hour_key("bad") == dt.time(0) and bt.hour_key(None) == dt.time(0)
    assert bt.median_time([]) == "n/a" and bt.median_time(["09:00", None, "07:55", "08:10"]) == "08:10"


def lever_out(tmp_path, rows, venue=None):
    v = dict(venue or VENUE, data_dir=str(tmp_path))
    write_snaps(tmp_path / "snapshots.csv", rows)
    return bt.summary(v, now=dt.datetime(2026, 1, 7, tzinfo=UTC), levers=True)


def test_lever_report_has_three_blocks(tmp_path):
    out = lever_out(tmp_path, [lrow("2026-01-05T23:55:00Z", "A", "2026-01-06T00:00:00+0000", res=3)])
    for h in ("COVERAGE:", "EVENT / ANOMALOUS-CAPACITY CANDIDATES", "ADDED SINCE THE FIRST SAME-DAY SNAPSHOT"):
        assert h in out
    for gone in ("FILL BY POSTED PRICE", "CLASSES:", "PUBLIC WAITLIST", "BOOKABLE SEATS LEFT"):
        assert gone not in out


def test_usual_capacity_comes_from_paid_slots_and_config(tmp_path):
    s = "2026-01-06T0{}:00:00+0000"
    rows = [lrow("2026-01-05T15:00:00Z", "A", s.format(1), offering="Event Room", price=20, cap=20, res=1),
            lrow("2026-01-05T15:00:00Z", "B", s.format(2), offering="Event Room", price=0, cap=25, res=0),
            lrow("2026-01-05T15:00:00Z", "C", s.format(3), offering="Event Room", price=0, cap=25, res=0)]
    out = lever_out(tmp_path, rows)
    assert out.count("price $0; capacity 25 vs usual 20") == 2 and "capacity 20 vs usual" not in out
    # the primary offering's usual capacity is the configured seat count, even when most slots differ
    rows = [lrow("2026-01-05T15:00:00Z", x, s.format(i), cap=36, res=1) for i, x in enumerate("ABC", 1)]
    assert lever_out(tmp_path, rows).count("capacity 36 vs usual 30") == 3
    assert "capacity 36 vs usual" not in lever_out(tmp_path, rows, dict(VENUE, primary_capacity=None))


def test_summary_sorted_by_clock_time(tmp_path):
    rows = [dict(lrow("2026-01-05T13:00:00Z", "A", "2026-01-05T16:00:00+0000", res=1), local_time="10:00 AM"),
            dict(lrow("2026-01-05T13:00:00Z", "B", "2026-01-05T15:00:00+0000", res=1), local_time="9:00 AM")]
    out = lever_out(tmp_path, rows)
    assert out.index("Mon  9:00 AM") < out.index("Mon 10:00 AM")
    assert "fill   3%  lag 120m" in out


def test_usual_capacity_for_an_all_free_offering_uses_its_own_slots(tmp_path):
    rows = [lrow("2026-01-05T15:00:00Z", "A", "2026-01-06T01:00:00+0000", res=1),   # paid primary, 30 seats
            lrow("2026-01-05T15:00:00Z", "B", "2026-01-06T02:00:00+0000", offering="Open House", price=0, cap=25)]
    out = lever_out(tmp_path, rows)
    assert "Open House: price $0" in out and "capacity 25 vs usual" not in out


def counting_fetch(inner):
    calls = []
    def fetch(url, headers=None):
        calls.append("api" if "/api/" in url else "store" if url.endswith("/store") else "menu")
        return inner(url, headers)
    return fetch, calls


def test_page_reads_happen_about_hourly(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    t0 = dt.datetime(2026, 1, 5, 14, 55, tzinfo=UTC)   # :55 run, 48 min after a :07 one, so always a page check
    s = [slot("A", "2026-01-06T01:00:00+0000", res=3)]
    f, calls = counting_fetch(fake_fetch(payload(s)))
    bt.run_venue(v, fetch=f, now=t0)                                   # first run: all three
    assert calls == ["api", "menu", "store"]
    calls.clear()
    other_menu = {"bootstrap": {"discovery": {"filters": [{"key": "k", "options": [{"label": "Changed"}]}]}},
                  "orgConfig": {"bookingPageStatus": "PAUSED"}}
    f2, calls2 = counting_fetch(fake_fetch(payload(s), menu=other_menu))
    r = bt.run_venue(v, fetch=f2, now=t0 + dt.timedelta(minutes=12))   # 15:07: availability only
    assert calls2 == ["api"] and r["changes"] == 0 and not r["errors"]
    state = json.load(open(tmp_path / "watch-state.json"))
    assert state["page_status"] == "LIVE" and state["pages_checked_utc"] == "2026-01-05T14:55:00Z"
    r = bt.run_venue(v, fetch=f2, now=t0 + dt.timedelta(minutes=60))   # 15:55: 60 min since the check
    assert calls2[-3:] == ["api", "menu", "store"] and r["changes"] >= 1
    assert "page_status: LIVE -> PAUSED" in (tmp_path / "changes.md").read_text()
    assert len(list(csv.DictReader(open(tmp_path / "snapshots.csv")))) == 3   # a snapshot every run


def test_failed_page_read_is_retried_next_run(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    t0 = dt.datetime(2026, 1, 5, 14, 7, tzinfo=UTC)
    s = [slot("A", "2026-01-06T01:00:00+0000")]
    bt.run_venue(v, fetch=fake_fetch(payload(s)), now=t0)
    r = bt.run_venue(v, fetch=fake_fetch(payload(s), fail=("store",)), now=t0 + dt.timedelta(minutes=60))
    assert r["errors"] and json.load(open(tmp_path / "watch-state.json"))["pages_checked_utc"] == "2026-01-05T14:07:00Z"
    f, calls = counting_fetch(fake_fetch(payload(s), fail=("menu",)))
    r = bt.run_venue(v, fetch=f, now=t0 + dt.timedelta(minutes=70))   # 10 min later: still due, retried
    assert calls == ["api", "menu", "store"] and r["errors"] == [r["errors"][0]] and r["errors"][0].startswith("menu:")


def test_future_page_stamp_is_stale(tmp_path):
    v = dict(VENUE, data_dir=str(tmp_path))
    s = [slot("A", "2026-01-06T01:00:00+0000")]
    bt.run_venue(v, fetch=fake_fetch(payload(s)), now=dt.datetime(2026, 1, 5, 15, tzinfo=UTC))
    f, calls = counting_fetch(fake_fetch(payload(s)))
    bt.run_venue(v, fetch=f, now=dt.datetime(2026, 1, 5, 14, 30, tzinfo=UTC))   # clock moved back
    assert calls == ["api", "menu", "store"]
    f, calls = counting_fetch(fake_fetch(payload(s)))
    bt.run_venue(v, fetch=f, now=dt.datetime(2026, 1, 5, 15, 14, 59, tzinfo=UTC))   # 44:59 after 14:30
    assert calls == ["api"]
    f, calls = counting_fetch(fake_fetch(payload(s)))
    bt.run_venue(v, fetch=f, now=dt.datetime(2026, 1, 5, 15, 15, tzinfo=UTC))   # exactly 45 min: due
    assert calls == ["api", "menu", "store"]

