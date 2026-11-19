"""The career scan's once-a-day guard and its launchd schedule (scan-timing-1118).

Nick's rulings, 2026-11-18 (data/workstreams/funnel-intake.md, "Nick's rulings on the
decision packet"): R0c "Move the career scan" to before the 07:00 morning page, and R11
"yes" to catch-up start times 09:00-12:00 with a once-a-day guard in cli.py. The guard is
what keeps the catch-up slots from rescanning on a day that already has a good scan.
"""
from __future__ import annotations

import json
import os
import plistlib
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.career_scanner import cli  # noqa: E402
from tools.career_scanner.scanner import role_queue_path  # noqa: E402

PLIST_DIR = REPO / "tools" / "launchd"
SCAN_PLIST = PLIST_DIR / "com.nickmagnuson.jobsearch.career-scan.plist"
PAGE_PLIST = PLIST_DIR / "com.nickmagnuson.jobsearch.morning-page.plist"

PST = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=PST)


def _queue(root: Path, **fields) -> Path:
    path = role_queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": 0,
               "new": [], "standing": []}
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- the guard -------------------------------------------------------------------------

def test_clean_scan_today_counts_as_done(tmp_path):
    _queue(tmp_path)   # 06:30 PST, 0 failures
    reason = cli.scanned_today(tmp_path, now=NOW)
    assert reason and "2026-11-18" in reason


def test_scan_yesterday_is_not_done(tmp_path):
    _queue(tmp_path, scanned_at="2026-11-17T16:30:24+00:00")   # 11-17 08:30 PST
    assert cli.scanned_today(tmp_path, now=NOW) is None


@pytest.mark.parametrize("failures", [1, 25])
def test_scan_today_with_fetch_failures_is_not_done(tmp_path, failures):
    # A 06:30 scan right after wake can run before the network is up; a catch-up slot
    # must still run then.
    _queue(tmp_path, fetch_failures=failures)
    assert cli.scanned_today(tmp_path, now=NOW) is None


def test_today_is_the_local_date_not_the_utc_date(tmp_path):
    # 2026-11-18T03:00Z is 11-17 19:00 PST: yesterday locally, today in UTC.
    _queue(tmp_path, scanned_at="2026-11-18T03:00:00+00:00")
    assert cli.scanned_today(tmp_path, now=NOW) is None
    # 2026-11-19T06:00Z is 11-18 22:00 PST: today locally, tomorrow in UTC.
    late = datetime(2026, 11, 18, 23, 0, tzinfo=PST)
    _queue(tmp_path, scanned_at="2026-11-19T06:00:00+00:00")
    assert cli.scanned_today(tmp_path, now=late)


def test_naive_scanned_at_is_read_as_utc(tmp_path):
    # 2026-11-18T03:00 with no offset is 11-17 19:00 PST if read as UTC (the writer's
    # zone), but 11-18 if read as local: reading it as local would skip a needed scan.
    _queue(tmp_path, scanned_at="2026-11-18T03:00:00")
    assert cli.scanned_today(tmp_path, now=NOW) is None


@pytest.fixture
def los_angeles(monkeypatch):
    """The process time zone set to America/Los_Angeles, restored afterwards."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_fall_back_day_uses_zone_rules_not_todays_offset(tmp_path, los_angeles):
    # 2026-11-01 is the fall-back day. 07:30Z is 00:30 PDT on 11-01; read with the 09:00
    # PST offset it would be 23:30 on 10-31 and the 09:00 slot would scan again (Codex r1 F1).
    _queue(tmp_path, scanned_at="2026-11-01T07:30:00+00:00")
    assert cli.scanned_today(tmp_path, now=datetime(2026, 11, 1, 9, 0))


def test_naive_now_is_local_wall_time(tmp_path, los_angeles):
    # 2026-11-18T03:00Z is 11-17 19:00 in Los Angeles: yesterday for a 09:00 slot.
    _queue(tmp_path, scanned_at="2026-11-18T03:00:00+00:00")
    assert cli.scanned_today(tmp_path, now=datetime(2026, 11, 18, 9, 0)) is None
    _queue(tmp_path, scanned_at="2026-11-18T14:30:00+00:00")
    assert cli.scanned_today(tmp_path, now=datetime(2026, 11, 18, 9, 0))


def test_future_scanned_at_is_not_done(tmp_path):
    # Same local date, but 14 hours after `now`: that scan has not happened (Codex r1 F2).
    _queue(tmp_path, scanned_at="2026-11-18T23:00:00-08:00")
    assert cli.scanned_today(tmp_path, now=NOW) is None


def test_small_clock_skew_still_counts(tmp_path):
    # Written 2 minutes "after" now: within the 5-minute slack.
    _queue(tmp_path, scanned_at="2026-11-18T17:02:00+00:00")
    assert cli.scanned_today(tmp_path, now=NOW)


@pytest.mark.parametrize("content", [
    None,                                   # no queue file
    "{not json",                            # unreadable
    "[]",                                   # wrong shape
    json.dumps({"fetch_failures": 0}),      # no scanned_at
    json.dumps({"scanned_at": "", "fetch_failures": 0}),
    json.dumps({"scanned_at": None, "fetch_failures": 0}),
    json.dumps({"scanned_at": "yesterday", "fetch_failures": 0}),
    json.dumps({"scanned_at": "0001-01-01T00:00:00+00:00", "fetch_failures": 0}),
    json.dumps({"scanned_at": "9999-12-31T23:59:59-08:00", "fetch_failures": 0}),
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00"}),                    # no count
    # Everything the guard reads is fine, but /standup's reader refuses it (Grok r1 F1).
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": 0}),
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": 0, "new": {}}),
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": "0"}),
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": False}),
    json.dumps({"scanned_at": "2026-11-18T14:30:24+00:00", "fetch_failures": None}),
])
def test_unusable_queue_means_scan(tmp_path, content, los_angeles):
    path = role_queue_path(tmp_path)
    if content is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert cli.scanned_today(tmp_path, now=NOW) is None
    # The guard only reads: a corrupt queue is left for the scan itself to handle.
    if content is not None:
        assert path.read_text(encoding="utf-8") == content
        assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


# --- main() ---------------------------------------------------------------------------

def _run(monkeypatch, capsys, root: Path, *flags: str):
    calls = []

    def fake_scan(repo_root, dry_run=False):
        calls.append({"repo_root": repo_root, "dry_run": dry_run})
        return {"roles": [], "new_roles": 0}

    import tools.career_scanner.scanner as scanner
    monkeypatch.setattr(scanner, "scan_all_targets", fake_scan)
    monkeypatch.setattr(sys, "argv", ["cli", "--repo-root", str(root), *flags])
    code = cli.main()
    return code, calls, capsys.readouterr().out


def test_once_a_day_skips_when_today_is_done(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, scanned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    code, calls, out = _run(monkeypatch, capsys, tmp_path, "--once-a-day")
    assert code == 0
    assert calls == []
    assert json.loads(out)["status"] == "skipped"


def test_once_a_day_scans_when_today_is_not_done(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, scanned_at=(datetime.now(timezone.utc) - timedelta(days=2))
           .isoformat(timespec="seconds"))
    code, calls, out = _run(monkeypatch, capsys, tmp_path, "--once-a-day")
    assert code == 0
    assert len(calls) == 1 and calls[0]["dry_run"] is False
    assert "status" not in json.loads(out)


def test_manual_scan_is_never_skipped(tmp_path, monkeypatch, capsys):
    # /scan-companies runs the cli with no flag; the guard is opt-in for launchd only.
    _queue(tmp_path, scanned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    code, calls, _ = _run(monkeypatch, capsys, tmp_path)
    assert code == 0 and len(calls) == 1


def test_dry_run_is_never_skipped(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, scanned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    code, calls, _ = _run(monkeypatch, capsys, tmp_path, "--once-a-day", "--dry-run")
    assert code == 0
    assert len(calls) == 1 and calls[0]["dry_run"] is True


# --- the schedule ---------------------------------------------------------------------

def _slots(plist: Path) -> list[tuple[int, int]]:
    cal = plistlib.loads(plist.read_bytes())["StartCalendarInterval"]
    cal = cal if isinstance(cal, list) else [cal]
    return sorted((e["Hour"], e.get("Minute", 0)) for e in cal)


def test_every_scan_slot_names_exactly_an_hour_and_a_minute():
    # launchd reads an omitted key as a wildcard: no Minute fires every minute of that
    # hour, and an added Weekday limits the slot to one day. _slots cannot see either,
    # so pin the keys themselves (Grok r1 F2).
    cal = plistlib.loads(SCAN_PLIST.read_bytes())["StartCalendarInterval"]
    assert isinstance(cal, list)
    for entry in cal:
        assert set(entry) == {"Hour", "Minute"}, entry
        assert all(type(v) is int for v in entry.values()), entry


def test_first_scan_slot_is_before_the_first_morning_page_render():
    scan_first = _slots(SCAN_PLIST)[0]
    page_first = _slots(PAGE_PLIST)[0]
    first = scan_first[0] * 60 + scan_first[1]
    page = page_first[0] * 60 + page_first[1]
    # 30 minutes: a measured scan takes about 25 s (funnel-intake.md, scan-timing-1118);
    # the margin covers boards that time out.
    assert page - first >= 30, (scan_first, page_first)


def test_schedule_is_exactly_0630_then_hourly_09_to_12():
    # The whole list, not a subset: an extra slot (07:00, 08:30, 13:00) is a change to
    # Nick's ruled schedule and must fail here (Codex r3 F1).
    assert _slots(SCAN_PLIST) == [(6, 30), (9, 0), (10, 0), (11, 0), (12, 0)]


def test_launchd_runs_the_scan_with_the_guard():
    data = plistlib.loads(SCAN_PLIST.read_bytes())
    assert "--once-a-day" in data["ProgramArguments"]
    assert data["RunAtLoad"] is False
