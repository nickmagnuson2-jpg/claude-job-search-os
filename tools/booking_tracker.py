#!/usr/bin/env python3
"""booking_tracker.py -- snapshot public booking-page data for venues on Zettlor; watch for changes.

For each venue in a gitignored config (default data/booking-tracker.json), every run:
  1. reads the public availability feed  /api/v1/bookings/public/availability/  (header X-Handle):
     every scheduled slot from today through HORIZON_DAYS, with capacity and current reservations;
  2. reads the booking page /c/<handle> (product menu, incl. switched-off offerings) and the store
     page /c/<handle>/store (packs, passes, memberships, rentals, prices) -- but only when the last
     successful page check is 45+ minutes old, so about hourly. KNOWN LIMIT (accepted 2026-10-29):
     a menu or store change that appears and reverts between two page checks is never seen, and a
     lasting one can surface a run or two late. Slots, prices and schedule come from step 1 every run;
  3. appends slot counts to <data_dir>/snapshots.csv, the snapshot time to <data_dir>/snapshot-times.txt
     (so an empty schedule still counts as a snapshot), and diffs the menu, store, price rules and the
     watched offering's slots against <data_dir>/watch-state.json, appending dated lines to
     <data_dir>/changes.md (a workstream PROBE can count them so /standup shows a change).

What the counts are: ADVANCE bookings at the moment of the snapshot. The last snapshot before a slot
starts is the best public read of its final bookings. Not visible: walk-ins, bookings taken outside
the platform, no-shows, booking channel. No personal data is stored.

Config (gitignored; nothing venue-specific lives in this public file):
  {"venues": [{"name": "Example Spa", "handle": "examplespa", "timezone": "America/Chicago",
               "data_dir": "output/example-spa/tracker/data",
               "primary_offering": "Sauna Session", "primary_capacity": 30,
               "watch_offering": "New Space Session"}]}
  data_dir is relative to the repo root. primary_offering/primary_capacity pick the regular paid
  sessions that --summary averages by weekday and start time; watch_offering is the product whose
  slots are diffed one by one (e.g. a new space that has not opened yet).

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/booking_tracker.py              # snapshot every venue
    PYTHONIOENCODING=utf-8 python3 tools/booking_tracker.py --summary --venue "Example Spa"
    PYTHONIOENCODING=utf-8 python3 tools/booking_tracker.py --levers --venue "Example Spa"   # + lever blocks

--levers adds three descriptive blocks: coverage and read lag, event/anomalous-capacity candidates, and
bookings added since the first same-day read and in the last 2 hours. Every block reads slot_observations()
and prints its denominator; none of them measures willingness to pay, buyout revenue or acquisition.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

from count_report import report

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(REPO, "data", "booking-tracker.json")
BASE = "https://www.zettlor.com"
HORIZON_DAYS = 75
PAGE_EVERY = dt.timedelta(minutes=45)   # booking and store pages: re-read at most this often
UA = "Mozilla/5.0 (personal demand research; a few requests per hour)"
FIELDS = ["snapshot_utc", "slot_id", "slot_start_utc", "local_date", "local_time", "offering",
          "duration_min", "price", "capacity", "reservations", "held", "waitlist", "category", "event_name"]


# ---------------------------------------------------------------- fetching
def get(url, headers=None, tries=3):
    """One-off 404s have been seen on store pages; retry before giving up."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def page_props(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("page has no __NEXT_DATA__ block")
    return json.loads(m.group(1))["props"]["pageProps"]


# ---------------------------------------------------------------- parsing (pure)
def slots_from_payload(payload):
    """Slots in date order. Raises on a missing data dict, missing pagination, or a truncated
    response, so none of them is ever stored as a complete schedule. The feed always sends
    pagination.hasMore; only an explicit false counts as complete."""
    data, pag = payload.get("data"), payload.get("pagination")
    if not isinstance(data, dict):
        raise ValueError(f"availability payload has no data dict: {str(payload)[:200]}")
    if not isinstance(pag, dict) or pag.get("hasMore") is not False:
        raise ValueError(f"availability may be truncated (pagination {pag})")
    return [s for day in sorted(data) for s in data[day]], pag


def menu_from_props(pp):
    filters = (pp.get("bootstrap") or {}).get("discovery", {}).get("filters")
    if not filters:
        raise ValueError("booking page has no discovery filters")
    menu = {f"{f.get('key')}: {o.get('label')}": ("off" if o.get("enabled") is False else "on")
            for f in filters for o in (f.get("options") or [])}
    if not menu:
        raise ValueError("booking page filters list no options")
    return menu, (pp.get("orgConfig") or {}).get("bookingPageStatus")


def store_from_props(pp):
    items = {}
    for e in pp.get("entries") or []:
        if e.get("entryType") == "SECTION_HEADER" or not e.get("isVisible", True):
            continue
        prod, pos = e.get("product") or {}, e.get("posItem") or {}
        price = prod.get("priceAmount", pos.get("price"))
        kind = prod.get("productType") or pos.get("itemType") or e.get("entryType")
        extra = [f"membership={prod['membershipType']}"] if prod.get("membershipType") else []
        extra += ["recurring"] if prod.get("isRecurring") else []
        name = e.get("displayName") or prod.get("name") or pos.get("name")
        items[name] = f"{kind} ${price}" + (f" ({', '.join(extra)})" if extra else "")
    if not items:
        raise ValueError("store page has no visible items")
    return items


def parse_utc(s):
    s = s.replace("Z", "+00:00")
    if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return dt.datetime.fromisoformat(s)


def slot_rows(slots, now_utc, tz):
    rows = []
    for s in slots:
        cc = s.get("capacity_context") or {}
        local = parse_utc(s["datetime"]).astimezone(tz)
        rows.append({"snapshot_utc": now_utc, "slot_id": s["id"], "slot_start_utc": s["datetime"],
                     "local_date": local.date().isoformat(), "local_time": s.get("time"),
                     "offering": s.get("offering_name"), "duration_min": s.get("duration"),
                     "price": s.get("price"), "capacity": s.get("capacity"),
                     "reservations": s.get("reservations"), "held": cc.get("held_capacity"),
                     "waitlist": s.get("waitlist_count"), "category": s.get("category"),
                     "event_name": s.get("event_name") or ""})
    return rows


def watch_state(slots, pagination, menu, status, store, watch_offering):
    rules = sorted({r.get("name") for s in slots
                    for r in ((s.get("pricing_context") or {}).get("rules_applied") or []) if r.get("name")})
    return {"menu": menu, "page_status": status, "store": store, "price_rules": rules,
            "offering_prices": sorted({f"{s.get('offering_name')} ${s.get('price')}" for s in slots}),
            "watched_slots": sorted({f"{s['datetime'][:16]} {s.get('offering_name')} cap {s.get('capacity')}"
                                     for s in slots if (s.get("offering_name") or "") == watch_offering}),
            # the feed omits lastSlotDate when the window covers the whole schedule
            "last_slot_date": pagination.get("lastSlotDate") or max((s["datetime"][:10] for s in slots), default=None)}


def diff(old, new):
    out = []
    for key in ("menu", "store"):
        o, n = old.get(key) or {}, new.get(key) or {}
        out += [f"{key}: {k}: {o.get(k, 'absent')} -> {n.get(k, 'absent')}"
                for k in sorted(set(o) | set(n)) if o.get(k) != n.get(k)]
    for key in ("price_rules", "offering_prices"):
        o, n = set(old.get(key) or []), set(new.get(key) or [])
        out += [f"{key}: added {x}" for x in sorted(n - o)] + [f"{key}: removed {x}" for x in sorted(o - n)]
    o, n = set(old.get("watched_slots") or []), set(new.get("watched_slots") or [])
    for label, items in (("new", sorted(n - o)), ("removed", sorted(o - n))):
        if items:
            out.append(f"watched slots: {len(items)} {label} ({', '.join(items[:6])}{' ...' if len(items) > 6 else ''})")
    out += [f"{key}: {old.get(key)} -> {new.get(key)}" for key in ("page_status", "last_slot_date")
            if old.get(key) != new.get(key)]
    return out


def num(v, default=0):
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def slot_observations(rows, now, snap_times=(), tz=dt.timezone.utc):
    """One record per slot that has started, the single source every report block reads.
    final = reservations at the LAST OBSERVED PRE-START snapshot (snapshot < start), which is not a
    true final count: lag_min says how long before the start it was taken. A slot that a later
    pre-start snapshot no longer listed is treated as canceled and dropped. snap_times adds
    snapshots that wrote no rows (an empty schedule is still a snapshot).
    base_day = reservations at the first snapshot on the slot's local date (None if none): bookings
    made before that run are inside the baseline. base_2h = reservations at the last snapshot at or
    before start - 2h (None if none), with its own lag."""
    by_slot, snap_times = {}, set(snap_times)
    for r in rows:
        snap_times.add(r["snapshot_utc"])
        if parse_utc(r["snapshot_utc"]) < parse_utc(r["slot_start_utc"]):
            by_slot.setdefault(r["slot_id"], []).append(r)
    snaps = sorted(parse_utc(t) for t in snap_times)
    out = []
    for obs in by_slot.values():
        obs.sort(key=lambda r: parse_utc(r["snapshot_utc"]))
        last, start = obs[-1], parse_utc(obs[-1]["slot_start_utc"])
        if start > now or any(parse_utc(last["snapshot_utc"]) < t < start for t in snaps):
            continue
        day = start.astimezone(tz).date()
        same_day = [r for r in obs if parse_utc(r["snapshot_utc"]).astimezone(tz).date() == day]
        early = [r for r in obs if parse_utc(r["snapshot_utc"]) <= start - dt.timedelta(hours=2)]
        out.append({"row": last, "final": int(num(last["reservations"])), "capacity": int(num(last["capacity"])),
                    "price": num(last["price"]), "held": int(num(last.get("held"))),
                    "waitlist": int(num(last.get("waitlist"))),
                    "lag_min": (start - parse_utc(last["snapshot_utc"])).total_seconds() / 60,
                    "base_day": int(num(same_day[0]["reservations"])) if same_day else None,
                    "base_day_at": parse_utc(same_day[0]["snapshot_utc"]).astimezone(tz).strftime("%H:%M") if same_day else None,
                    "base_2h": int(num(early[-1]["reservations"])) if early else None,
                    "base_2h_lag": (start - parse_utc(early[-1]["snapshot_utc"])).total_seconds() / 60 if early else None})
    return out


def final_rows(rows, now, snap_times=()):
    """For each slot that has started: its row from the last snapshot taken before the start."""
    return [o["row"] for o in slot_observations(rows, now, snap_times)]


def is_primary(r, venue):
    return (r["offering"] == venue.get("primary_offering") and float(r["price"] or 0) > 0
            and int(r["capacity"] or 0) == int(venue.get("primary_capacity") or 0))


# ---------------------------------------------------------------- per-venue run
def paths(venue):
    d = venue["data_dir"] if os.path.isabs(venue["data_dir"]) else os.path.join(REPO, venue["data_dir"])
    return d, os.path.join(d, "snapshots.csv"), os.path.join(d, "watch-state.json"), os.path.join(d, "changes.md")


def times_path(venue):
    return os.path.join(paths(venue)[0], "snapshot-times.txt")


def run_venue(venue, fetch=get, now=None):
    tz = ZoneInfo(venue.get("timezone", "UTC"))
    data_dir, snaps_p, state_p, changes_p = paths(venue)
    os.makedirs(data_dir, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone.utc)
    now_utc, stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    old = json.load(open(state_p)) if os.path.exists(state_p) else None
    start = now.astimezone(tz).date()
    url = (f"{BASE}/api/v1/bookings/public/availability/?start_date={start}"
           f"&end_date={start + dt.timedelta(days=HORIZON_DAYS)}&page_size=500")
    slots, pag = slots_from_payload(json.loads(fetch(url, {"X-Handle": venue["handle"]})))   # required
    errors, checked = [], (old or {}).get("pages_checked_utc")
    # a stamp in the future (clock moved back) is stale, never fresh
    due = not checked or not (now - PAGE_EVERY < parse_utc(checked) <= now)
    menu, status, store = (old or {}).get("menu") or {}, (old or {}).get("page_status"), (old or {}).get("store") or {}
    if due:
        try:
            menu, status = menu_from_props(page_props(fetch(f"{BASE}/c/{venue['handle']}")))
        except Exception as e:   # keep last run's values: an outage is never reported as a change
            errors.append(f"menu: {e}")
        try:
            store = store_from_props(page_props(fetch(f"{BASE}/c/{venue['handle']}/store")))
        except Exception as e:
            errors.append(f"store: {e}")
        if not errors:   # a failed page read leaves the stamp alone, so the next run retries
            checked = now_utc
    rows = slot_rows(slots, now_utc, tz)
    new_file = not os.path.exists(snaps_p)
    with open(snaps_p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    with open(times_path(venue), "a") as f:
        f.write(now_utc + "\n")
    if errors:
        with open(os.path.join(data_dir, "errors.log"), "a") as f:
            f.write("".join(f"{stamp} {e}\n" for e in errors))
    if old is None and errors:   # no baseline from a partial read: the next clean run writes it
        return {"slots": len(rows), "reservations": sum(r["reservations"] or 0 for r in rows),
                "changes": 0, "errors": errors}
    state = dict(watch_state(slots, pag, menu, status, store, venue.get("watch_offering")), pages_checked_utc=checked)
    if old is None:
        lines = [f"- {stamp} baseline: {len(menu)} menu options ({sum(v == 'off' for v in menu.values())} switched off), "
                 f"{len(store)} store items, {len(state['watched_slots'])} watched slots, schedule to {state['last_slot_date']}"]
    else:
        lines = [f"- {stamp} {c}" for c in diff(old, state)]
    if lines:
        new_changes = not os.path.exists(changes_p)
        with open(changes_p, "a") as f:
            if new_changes:
                f.write(f"# Booking-page changes: {venue['name']}\n\nOne line per detected change (tools/booking_tracker.py). Newest at the bottom.\n\n")
            f.write("\n".join(lines) + "\n")
    tmp = state_p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, state_p)
    return {"slots": len(rows), "reservations": sum(r["reservations"] or 0 for r in rows),
            "changes": len(lines), "errors": errors}


ORDER = "MonTueWedThuFriSatSun"
NOT_VISIBLE = ("Not visible in this feed: walk-ins, booking channel (member, ClassPass, direct), customer "
               "identity, repeat visits, refunds and comps, private-event fees, HSA/FSA, bookings after the last "
               "pre-start read.")


def mean(v):
    return sum(v) / len(v) if v else 0.0


def median(v):
    v = sorted(v)
    return (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2 if v else 0.0


def weekday(o):
    return dt.date.fromisoformat(o["row"]["local_date"]).strftime("%a")


def anomaly(o, modal):
    """Which predicates mark a slot as an event or anomalous-capacity candidate (never proof of a buyout)."""
    r, fired = o["row"], []
    if o["price"] == 0:
        fired.append("price $0")
    if o["capacity"] != modal.get(r["offering"], o["capacity"]):
        fired.append(f"capacity {o['capacity']} vs usual {modal[r['offering']]}")
    if (r.get("event_name") or "").strip():
        fired.append(f"named '{r['event_name'].strip()[:40]}'")
    return fired


def lever_blocks(obs, venue):
    """The lever report: three descriptive blocks over slot_observations, each with its denominator.
    Fill by start time, with read lag, is in the base summary above."""
    out = []
    lags = [o["lag_min"] for o in obs]
    out.append(f"\nCOVERAGE: {len(obs)} finished slots, {obs and min(o['row']['local_date'] for o in obs)} to "
               f"{obs and max(o['row']['local_date'] for o in obs)}. Last pre-start read: "
               f"{sum(l <= 10 for l in lags)} within 10 min of start, {sum(10 < l <= 30 for l in lags)} at 11-30 min, "
               f"{sum(l > 30 for l in lags)} over 30 min. Counts below are last observed pre-start, not final.")
    modal = {}   # usual capacity per offering: from paid slots, ties to the larger (the primary uses its configured seats)
    for off in {o["row"]["offering"] for o in obs}:
        caps = [o["capacity"] for o in obs if o["row"]["offering"] == off and o["price"] > 0] or \
               [o["capacity"] for o in obs if o["row"]["offering"] == off]
        modal[off] = max(set(caps), key=lambda c: (caps.count(c), c))
    if venue.get("primary_offering") in modal and venue.get("primary_capacity"):
        modal[venue["primary_offering"]] = int(venue["primary_capacity"])
    out.append("\nEVENT / ANOMALOUS-CAPACITY CANDIDATES (predicates only; not proof of a buyout or of lost revenue)")
    cands = [(o, anomaly(o, modal)) for o in obs]
    for o, fired in [c for c in cands if c[1]]:
        out.append(f"  {o['row']['local_date']} {o['row']['local_time']:>8} {o['row']['offering'][:30]}: {'; '.join(fired)}")
    held = [o for o in obs if o["held"] and not anomaly(o, modal)]
    out.append(f"  ({sum(bool(c[1]) for c in cands)} candidates; {len(held)} other slots with held seats at the last read, "
               "listed separately because holds are transient)")
    elig = [o for o in obs if o["base_day"] is not None and o["final"] > 0]
    zero = sum(o["final"] == 0 for o in obs)
    late = [o for o in obs if o["base_2h"] is not None and o["base_2h_lag"] <= 150]   # a read 2h-2.5h before start
    out.append("\nADDED SINCE THE FIRST SAME-DAY SNAPSHOT (bookings made before that read are in its baseline)")
    out.append(f"  {sum(o['final'] - o['base_day'] for o in elig)} of {sum(o['final'] for o in elig)} bookings "
               f"({mean([(o['final'] - o['base_day']) / o['final'] for o in elig]):.0%} average per slot), "
               f"n={len(elig)} slots; median baseline read at {median_time([o['base_day_at'] for o in elig])}; "
               f"{zero} zero-booking slots excluded")
    out.append(f"  Added in the last 2 hours before start: {sum(o['final'] - o['base_2h'] for o in late)} bookings, "
               f"n={len(late)} slots with a read 2 to 2.5 hours before start (median {median([o['base_2h_lag'] for o in late]):.0f} min before); "
               f"{len(obs) - len(late)} slots without one excluded")
    out.append(f"\n{NOT_VISIBLE}")
    return out


def hour_key(t):
    try:
        return dt.datetime.strptime(t, "%I:%M %p").time()
    except (TypeError, ValueError):
        return dt.time(0)


def median_time(ts):
    ts = sorted(t for t in ts if t)
    return ts[(len(ts) - 1) // 2] if ts else "n/a"


def summary(venue, now=None, levers=False):
    _, snaps_p, _, _ = paths(venue)
    if not os.path.exists(snaps_p):
        return "no snapshots yet"
    tp = times_path(venue)
    times = open(tp).read().split() if os.path.exists(tp) else ()
    obs = slot_observations(list(csv.DictReader(open(snaps_p))), now or dt.datetime.now(dt.timezone.utc), times,
                            ZoneInfo(venue.get("timezone", "UTC")))
    if not obs:
        return "No slot has started since the first snapshot yet."
    by_day, by_start = {}, {}
    for o in obs:
        r = o["row"]
        by_day.setdefault(r["local_date"], []).append(r)
        if is_primary(r, venue):
            by_start.setdefault((weekday(o), r["local_time"]), []).append(o)
    # booked_x_price is booked seats times posted price, not revenue (members, comps, refunds, channel unseen)
    out = ["date        slots  booked  primary_booked  booked_x_price"]
    for day in sorted(by_day):
        rs = by_day[day]
        out.append(f"{day}  {len(rs):5d}  {sum(int(r['reservations'] or 0) for r in rs):6d}  "
                   f"{sum(int(r['reservations'] or 0) for r in rs if is_primary(r, venue)):14d}  "
                   f"{sum(int(r['reservations'] or 0) * float(r['price'] or 0) for r in rs):12,.0f}")
    out.append(f"\nRegular '{venue.get('primary_offering')}' sessions (paid, {venue.get('primary_capacity')} seats): "
               "average last pre-start bookings by weekday and start time:")
    for (wd, t), v in sorted(by_start.items(), key=lambda x: (ORDER.index(x[0][0]), hour_key(x[0][1]))):
        out.append(f"  {wd} {t:>8}: {mean([o['final'] for o in v]):5.1f}  (n={len(v)})  "
                   f"fill {mean([o['final'] / o['capacity'] for o in v if o['capacity']]):4.0%}  "
                   f"lag {median([o['lag_min'] for o in v]):3.0f}m")
    return "\n".join(out + (lever_blocks(obs, venue) if levers else []))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--venue", help="only this venue (by name)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--levers", action="store_true", help="--summary plus the lever blocks")
    a = ap.parse_args(argv)
    every = json.load(open(a.config))["venues"]
    venues = [v for v in every if not a.venue or v["name"] == a.venue]
    if a.venue and not venues:
        sys.exit(f"no venue named {a.venue!r} in {a.config}")
    if a.summary or a.levers:
        for v in venues:
            print(f"== {v['name']}\n{summary(v, levers=a.levers)}")
        return 0
    failed = partial = 0
    for v in venues:
        try:
            r = run_venue(v)
        except Exception as e:   # one venue failing must not stop the others
            failed += 1
            print(f"{v['name']}: FAILED {e}", file=sys.stderr)
            continue
        partial += bool(r["errors"])   # a partial read is a failed run, even though its slots were kept
        print(f"{v['name']}: {r['slots']} slots, {r['reservations']} reservations, {r['changes']} change line(s)"
              + (f", errors: {r['errors']}" if r["errors"] else ""))
    report(f"venues in {a.config}", len(every), len(venues) - failed - partial,
           {"not selected by --venue": len(every) - len(venues), "failed": failed, "partial read": partial})
    return 1 if failed or partial else 0


if __name__ == "__main__":
    sys.exit(main())
