"""Tests for tools/booking_tracker.py. Generic fixtures only (public repo)."""
import csv
import datetime as dt
import json
import sys
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
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1), fail=("store",)), now=now)
    assert r["changes"] == 0 and r["errors"]
    assert "store" in (tmp_path / "errors.log").read_text()
    # the watched slot disappears: reported as removed
    before = (tmp_path / "changes.md").read_text()
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1)), now=now)          # nothing changed: file untouched
    assert r["changes"] == 0 and (tmp_path / "changes.md").read_text() == before
    r = bt.run_venue(v, fetch=fake_fetch(payload(s1[:1])), now=now)
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
                 now=dt.datetime(2026, 1, 5, tzinfo=UTC))
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


@pytest.mark.parametrize("errors,code", [([], 0), (["store: x"], 1)])
def test_main_partial_read_fails_the_run(tmp_path, monkeypatch, errors, code):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"venues": [dict(VENUE, data_dir=str(tmp_path / "a"))]}))
    monkeypatch.setattr(bt, "run_venue", lambda v, fetch=None, now=None:
                        {"slots": 3, "reservations": 4, "changes": 0, "errors": errors})
    assert bt.main(["--config", str(cfg)]) == code
