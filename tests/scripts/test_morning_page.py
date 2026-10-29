"""Tests for tools/morning_page.py, the plain morning page (D5), and its launchd plist.

Demo mode reads only tests/fixtures/morning_page/, which are placeholders; one test runs
those fixtures through the public PII denylist scan.
"""
import json
import plistlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import morning_page as mp  # noqa: E402

NOW = datetime(2026, 1, 7, 9, 0)
FIX = REPO / "tests" / "fixtures" / "morning_page"
PLIST = TOOLS / "launchd" / "disarmed" / "com.nickmagnuson.jobsearch.morning-page.plist"


def demo_html(now=NOW):
    return mp.render(mp.build_view(mp.gather_demo(), now), now, demo=True)


def _section(page, ident):
    m = re.search(rf'<(section|div)[^>]*id="{ident}"[^>]*>(.*?)</\1>', page, re.S)
    assert m, ident
    return m.group(2)


# --- bands and staleness ----------------------------------------------------------------

def test_demo_has_the_demo_band_and_never_the_live_one():
    page = demo_html()
    assert mp.DEMO_BAND in page and mp.LIVE_BAND not in page


def test_live_render_has_the_fixed_live_private_band():
    page = mp.render(mp.build_view(mp.gather_demo(), NOW), NOW, demo=False)
    assert '<div class="band live">LIVE, PRIVATE</div>' in page and mp.DEMO_BAND not in page


def test_the_page_embeds_generated_at_and_a_30_minute_stale_check():
    page = demo_html()
    assert 'data-generated-at="2026-01-07T09:00:00"' in page
    assert "var lim=30*60*1000" in page and "STALE" in page
    assert mp.STALE_MINUTES == 30


# --- content, in Nick's order -----------------------------------------------------------

def test_blocks_are_in_nicks_order():
    page = demo_html()
    pos = [page.index(f'id="b{i}"') for i in range(1, 7)]
    assert pos == sorted(pos)
    assert "Placeholder until the funnel codify run" in _section(page, "b3")


def test_must_dos_keep_calendar_apart_from_todos():
    v = mp.build_view(mp.gather_demo(), NOW)
    assert v["cal_today"] == ["10:30  Screen with Beta Inc"]
    assert v["cal_soon"] == ["Thu  13:30  Coffee with Alex Example", "Fri  all day  Private appointment"]
    assert v["todos"][0].endswith("(2d overdue)") and v["todos"][1].endswith("(due today)")
    assert v["todos"][2].endswith("(in 2d)") and v["todos"][3].endswith("(no date)")


def test_an_unavailable_calendar_says_why():
    src = {**mp.gather_demo(), "calendar": {"error": "token expired"}}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Calendar unavailable: token expired" in _section(page, "b1")


def test_done_so_far_today_shows_the_day_check_counts_and_the_weekly_target():
    b5 = _section(demo_html(), "b5")
    assert "Sends" in b5 and ">3<" in b5 and "week 8" in b5
    assert "week 1 of 5 a week" in b5


def test_an_unknown_count_prints_unknown_not_zero():
    src = mp.gather_demo()
    src["day"]["counts"]["sends"] = {"today": None, "week": None}
    src["collector"]["results"].pop("networking_followup")
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "unknown" in _section(page, "b5") and "unknown" in _section(page, "b2")


def test_machine_room_m4_against_the_floor():
    v = mp.build_view(mp.gather_demo(), NOW)
    assert v["machine"]["m4"] == {"last_7": 9, "floor": 7.0, "ok": True}
    src = mp.gather_demo()
    src["collector"]["results"]["conversations_metric"]["data"]["outreach_sent"]["counts"]["7d"] = 5
    assert mp.build_view(src, NOW)["machine"]["m4"]["ok"] is False


def test_failed_sources_are_named():
    src = mp.gather_demo()
    src["collector"]["failed"] = ["role_queue"]
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Sources failed: role_queue" in _section(page, "b4")


def test_commonplace_shows_one_prompt():
    b6 = _section(demo_html(), "b6")
    assert "An essay on keeping a commonplace book" in b6 and "agree, disagree, or so what" in b6
    assert "First chair" in b6


# --- Claude's read: no digits ------------------------------------------------------------

def test_claudes_read_holds_no_digit():
    assert not re.search(r"\d", _section(demo_html(), "claude-read"))


def test_a_read_with_a_number_is_withheld():
    src = {**mp.gather_demo(), "claude_read": {"text": "You sent 7 emails.", "note": None}}
    block = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "claude-read")
    assert "Withheld" in block and not re.search(r"\d", block)


def test_claude_read_file(tmp_path):
    d = tmp_path / mp.OUT_REL
    d.mkdir(parents=True)
    assert mp.claude_read(tmp_path, NOW.date())["note"] == "No read from /standup today."
    (d / "claude-read-2026-01-07.json").write_text(json.dumps({"text": "Steady day."}))
    assert mp.claude_read(tmp_path, NOW.date()) == {"text": "Steady day.", "note": None}
    (d / "claude-read-2026-01-07.json").write_text("{not json")
    assert mp.claude_read(tmp_path, NOW.date())["text"] is None


# --- commonplace selection -----------------------------------------------------------------

@pytest.mark.parametrize("body,empty", [
    ("## My take (voice-pure)\n\n<!-- voice: pure-voice -->\n\n## Claude's read\nx\n", True),
    ("## My take (voice-pure)\n\n> I disagree, here is why.\n", False),
    ("## BLUF\nno take section\n", False),
])
def test_empty_take(body, empty):
    assert mp.empty_take(body) is empty


def test_commonplace_rotates_one_a_day(tmp_path):
    d = tmp_path / "data" / "source-articles"
    d.mkdir(parents=True)
    for i in range(3):
        (d / f"2026010{i}-a.md").write_text(f"# Title {i}\n\n## My take\n\n")
    (d / "20260109-done.md").write_text("# Done\n\n## My take\n\n> mine\n")
    picks = {mp.commonplace(tmp_path, datetime(2026, 1, day).date())["pick"]["title"]
             for day in (1, 2, 3)}
    assert picks == {"Title 0", "Title 1", "Title 2"}
    assert mp.commonplace(tmp_path, NOW.date())["waiting"] == 3
    assert mp.commonplace(tmp_path / "nothing", NOW.date()) == {"waiting": 0, "pick": None}


# --- public safety -----------------------------------------------------------------------

def test_demo_fixtures_pass_the_pii_denylist_scan():
    files = sorted(str(p) for p in FIX.glob("*.json"))
    assert files, "no fixtures"
    r = subprocess.run([sys.executable, str(TOOLS / "check_public_pii.py"), "--scan", *files],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout)
    assert out["hits"] == [] and out["scanned"] == len(files), out


def test_demo_mode_reads_only_the_fixture_folder(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("demo mode touched a live source")
    monkeypatch.setattr(mp, "gather_live", boom)
    out = tmp_path / "demo.html"
    assert mp.main(["--demo", "--out", str(out), "--now", "2026-01-07T09:00"]) == 0
    assert mp.DEMO_BAND in out.read_text()


# --- the CLI and the archive copy ------------------------------------------------------------

def test_live_main_writes_the_page_and_the_dated_copy_after_19(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "gather_live", lambda root, now: mp.gather_demo())
    out = tmp_path / "index.html"
    assert mp.main(["--repo-root", str(tmp_path), "--out", str(out), "--now", "2026-01-07T18:45"]) == 0
    assert not (tmp_path / "2026-01-07.html").exists()
    assert mp.main(["--repo-root", str(tmp_path), "--out", str(out), "--now", "2026-01-07T19:00"]) == 0
    assert (tmp_path / "2026-01-07.html").read_text() == out.read_text()
    assert mp.LIVE_BAND in out.read_text()


def test_bad_now_exits_2(capsys):
    assert mp.main(["--demo", "--now", "nope"]) == 2
    assert "--now must be" in capsys.readouterr().err


# --- the plist: written, not installed ----------------------------------------------------

def test_plist_is_disarmed_and_runs_every_15_minutes_from_7_to_19():
    assert not (TOOLS / "launchd" / PLIST.name).exists(), "must not be in the standing set"
    p = plistlib.loads(PLIST.read_bytes())
    assert p["Label"] == "com.nickmagnuson.jobsearch.morning-page"
    assert p["ProgramArguments"][1].endswith("/tools/morning_page.py")
    slots = [(s["Hour"], s["Minute"]) for s in p["StartCalendarInterval"]]
    assert slots[0] == (7, 0) and slots[-1] == (19, 0) and len(slots) == 49
    assert all(m in (0, 15, 30, 45) for _, m in slots) and len(set(slots)) == 49
    assert p["RunAtLoad"] is False
    assert p["EnvironmentVariables"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_page_fits_a_laptop_without_scrolling():
    page = demo_html()
    assert "overflow:hidden" in page and "font:20px" in page
    v = mp.build_view(mp.gather_demo(), NOW)
    assert len(v["todos"]) <= mp.MAX_ITEMS and len(v["waiting_names"]) < mp.MAX_ITEMS


def test_machine_room_shows_the_learning_ledger_due_count():
    v = mp.build_view(mp.gather_demo(), NOW)
    assert v["machine"]["learning_due"] == 6
    assert "Learning lanes due" in _section(demo_html(), "b4")
