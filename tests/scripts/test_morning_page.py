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
# Armed by Nick 2026-10-29 (install.sh arm morning-page): in the standing set, not disarmed/.
PLIST = TOOLS / "launchd" / "com.nickmagnuson.jobsearch.morning-page.plist"


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
    # Nick 2026-11-17 (funnel-intake.md step 1): the funnel first, the machine room off the page.
    pos = [page.index(f'id="{i}"') for i in ("b3", "b1", "b2", "theirs", "b5", "b6")]
    assert pos == sorted(pos)
    assert 'id="b4"' not in page and "Machine room" not in page
    assert "Placeholder" not in page


def test_must_dos_keep_calendar_apart_from_todos():
    v = mp.build_view(mp.gather_demo(), NOW)
    assert v["cal_today"] == ["10:30  Screen with Beta Inc"]
    assert v["cal_soon"] == ["Thu  13:30  Coffee with Alex Example", "Fri  all day  Private appointment"]
    assert v["todos"][0].startswith("(2d overdue) ") and v["todos"][1].startswith("(due today) ")
    assert v["todos"][2].startswith("(in 2d) ") and v["todos"][3].startswith("(no date) ")


def test_an_unavailable_calendar_says_why():
    src = {**mp.gather_demo(), "calendar": {"error": "token expired"}}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Calendar unavailable: token expired" in _section(page, "b1")


def test_done_so_far_today_shows_the_day_check_counts_and_the_weekly_target():
    b5 = _section(demo_html(), "b5")
    assert '<th class="n">Today</th><th class="n">This week</th>' in b5
    assert "Sends" in b5 and ">3<" in b5 and '<td class="n">8</td>' in b5
    assert '<td class="n">1 <span class="muted">/ 5 a week</span></td>' in b5
    assert "week 8" not in b5


def test_an_unknown_count_prints_unknown_not_zero():
    src = mp.gather_demo()
    src["day"]["counts"]["sends"] = {"today": None, "week": None}
    src["collector"]["results"].pop("networking_followup")
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "unknown" in _section(page, "b5") and "unknown" in _section(page, "b2")




def test_failed_sources_are_named():
    src = mp.gather_demo()
    src["collector"]["failed"] = ["role_queue"]
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Sources failed: role_queue" in page and 'id="sources-failed"' in page


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

@pytest.mark.skipif(not (TOOLS / ".pii-denylist.txt").exists(),
                    reason="no PII denylist in this checkout (gitignored; run tools/gen_pii_denylist.py)")
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


def test_demo_on_any_day_shows_the_sample_calendar_moved_to_that_day(tmp_path):
    # The fixtures are dated 2026-01-07; rendered on 2026-10-30 the events move by the same gap.
    out = tmp_path / "demo.html"
    assert mp.main(["--demo", "--out", str(out), "--now", "2026-10-30T09:00"]) == 0
    b1 = _section(out.read_text(), "b1")
    assert "10:30  Screen with Beta Inc" in b1
    assert "Sat  13:30  Coffee with Alex Example" in b1
    assert "Sun  all day  Private appointment" in b1


def test_the_demo_shift_keeps_times_and_offsets_and_leaves_other_sections_alone():
    shifted = mp.gather_demo(today=datetime(2026, 10, 30).date())
    ev = shifted["calendar"]["events"]
    assert [e["start"] for e in ev] == ["2026-10-30T10:30:00-08:00", "2026-10-31T13:30:00-08:00",
                                        "2026-11-01"]
    assert [e["end"] for e in ev] == ["2026-10-30T11:00:00-08:00", "2026-10-31T14:00:00-08:00",
                                      "2026-11-02"]
    plain = mp.gather_demo()
    assert plain["calendar"]["events"][0]["start"] == "2026-01-07T10:30:00-08:00"
    assert {k: v for k, v in shifted.items() if k != "calendar"} == \
        {k: v for k, v in plain.items() if k != "calendar"}


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


# --- the plist: armed 2026-10-29 ----------------------------------------------------

def test_the_docstring_names_the_armed_plist():
    assert "tools/launchd/com.nickmagnuson.jobsearch.morning-page.plist" in mp.__doc__
    assert "disarmed" not in mp.__doc__


def test_plist_is_armed_and_runs_every_15_minutes_from_7_to_19():
    assert not (TOOLS / "launchd" / "disarmed" / PLIST.name).exists(), "armed: must not also sit in disarmed/"
    p = plistlib.loads(PLIST.read_bytes())
    assert p["Label"] == "com.nickmagnuson.jobsearch.morning-page"
    assert p["ProgramArguments"][1].endswith("/tools/morning_page.py")
    slots = [(s["Hour"], s["Minute"]) for s in p["StartCalendarInterval"]]
    assert slots[0] == (7, 0) and slots[-1] == (19, 0) and len(slots) == 49
    assert all(m in (0, 15, 30, 45) for _, m in slots) and len(set(slots)) == 49
    assert p["RunAtLoad"] is False
    assert p["EnvironmentVariables"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_page_keeps_large_type_and_few_items():
    page = demo_html()
    assert "font:20px" in page
    v = mp.build_view(mp.gather_demo(), NOW)
    assert len(v["todos"]) <= mp.MAX_ITEMS and len(v["waiting_names"]) < mp.MAX_ITEMS




# --- the sources, each failure shape -------------------------------------------------------

class _P:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_calendar_events_shapes(tmp_path, monkeypatch):
    calls = []

    def run(argv, **k):
        calls.append((argv, k["env"].get("PYTHONDONTWRITEBYTECODE")))
        return result
    monkeypatch.setattr(mp.subprocess, "run", run)
    result = _P(0, '{"events": []}')
    assert mp.calendar_events(tmp_path) == {"events": []}
    assert calls[0][0][1:] == ["tools/calendar_fetch.py", "--days", "4"] and calls[0][1] == "1"
    result = _P(1, "", "Traceback\nERROR: token expired\n")
    assert mp.calendar_events(tmp_path) == {"error": "ERROR: token expired"}
    result = _P(1, "", "")
    assert mp.calendar_events(tmp_path) == {"error": "calendar_fetch failed"}
    result = _P(0, "not json")
    assert mp.calendar_events(tmp_path) == {"error": "calendar_fetch printed no JSON"}

    def boom(*a, **k):
        raise OSError("no python")
    monkeypatch.setattr(mp.subprocess, "run", boom)
    assert mp.calendar_events(tmp_path)["error"].startswith("calendar_fetch could not run")


def test_commonplace_skips_missing_dirs_and_unreadable_files(tmp_path):
    d = tmp_path / "data" / "source-emails"
    d.mkdir(parents=True)
    (d / "a.md").write_text("# A\n\n## My take\n\n")
    (d / "bad.md").write_bytes(b"\xff\xfe# not utf8")
    out = mp.commonplace(tmp_path, NOW.date())
    assert out == {"waiting": 1, "pick": {"title": "A", "path": "data/source-emails/a.md"}}
    (d / "notitle.md").write_text("## My take\n\n")
    titles = {mp.commonplace(tmp_path, datetime(2026, 1, x).date())["pick"]["title"] for x in (1, 2)}
    assert titles == {"A", "notitle"}




def test_gather_live_uses_the_read_only_collector(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(mp.standup_collect, "collect",
                        lambda root, d, read_only: seen.setdefault("ro", read_only) and {"results": {}})
    monkeypatch.setattr(mp.day_check, "collect", lambda root, d: {"day": d.isoformat()})
    monkeypatch.setattr(mp, "calendar_events", lambda root: {"events": []})
    monkeypatch.setattr(mp, "waiting_on_them", lambda root: {"error": None, "rows": []})
    monkeypatch.setattr(mp.funnel_state, "collect", lambda root, now: {"root": str(root)})
    monkeypatch.setattr(mp, "decisions_waiting", lambda root: {"error": None, "open": []})
    src = mp.gather_live(tmp_path, NOW)
    assert seen["ro"] is True
    assert src["day"] == {"day": "2026-01-07"} and src["funnel"] == {"root": str(tmp_path)}
    assert set(src) == set(mp.SOURCES) == {"collector", "day", "calendar", "commonplace",
                                           "claude_read", "waiting_on_them", "funnel",
                                           "decisions"}


def test_event_day_parses_dates_and_datetimes():
    assert mp._event_day({"start": "2026-01-07"}) == NOW.date()
    assert mp._event_day({"start": "2026-01-07T10:00:00-08:00"}) == NOW.date()
    assert mp._event_day({"start": "nope"}) is None


# --- the view and the render, each branch ----------------------------------------------------



def test_a_read_without_digits_is_shown():
    v = mp.build_view(mp.gather_demo(), NOW)
    assert v["claude_read"]["text"].startswith("Outreach is ahead")
    assert "Outreach is ahead" in _section(mp.render(v, NOW, demo=True), "claude-read")


def test_list_states():
    assert mp._list(None, "why") == '<p class="muted">why</p>'
    assert mp._list([], "x") == '<p class="muted">Nothing.</p>'
    assert mp._list(["a", "b"], "x") == "<ul><li>a</li><li>b</li></ul>"


def test_render_branches_without_optional_content():
    src = mp.gather_demo()
    src["collector"]["results"]["networking_followup"]["data"]["followup_overdue"] = []
    src["commonplace"] = {"waiting": 0, "pick": None}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Oldest follow-ups" not in page
    assert "No saved article is waiting for a take." in page
    assert "Sources failed" not in page
    assert "Calendar unavailable" not in page
    full = demo_html()
    assert "Oldest follow-ups" in full and "Blair Sample" in full


def test_write_page_creates_the_folder(tmp_path):
    out = tmp_path / "a" / "b" / "index.html"
    mp.write_page("x", out)
    assert out.read_text() == "x" and not (out.parent / "index.tmp").exists()


def test_main_prints_the_path_and_the_script_runs(tmp_path, capsys):
    out = tmp_path / "d.html"
    assert mp.main(["--demo", "--out", str(out), "--now", "2026-01-07T09:00"]) == 0
    assert capsys.readouterr().out.strip() == str(out)
    r = subprocess.run([sys.executable, str(TOOLS / "morning_page.py"), "--demo", "--out",
                        str(tmp_path / "e.html"), "--now", "bad"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2


# --- 9e: waiting on them ----------------------------------------------------------------

PIPE = """# Job Application Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Corp | Ops | Applied | 2026-01-05 | Await their reply; nudge Fri | — | | |
| Beta Inc | BizOps | Take-home presented; awaiting read | 2026-01-06 | Send deck to the panel | — | | |
| Gamma LLC | Strategy | Interview done, awaiting decision | 2026-01-04 | — | — | | |
| Delta Co | Ops | Recruiter call | 2026-01-07 | Call Thu 1:30pm (she calls) | — | | |
| Epsilon | Ops | Rejected | 2026-01-03 | Await nothing | — | | |
| Zeta | Ops | Researching | 2026-01-02 | Research the team | — | | |
| Short | Ops | Applied | 2026-01-02 |
"""


def test_waiting_on_them_reads_the_next_action_first(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(PIPE)
    got = mp.waiting_on_them(tmp_path)
    assert got["error"] is None
    assert [r["company"] for r in got["rows"]] == ["Acme Corp", "Gamma LLC", "Delta Co"]
    assert got["rows"][1]["next"] == "Interview done, awaiting decision"
    assert mp.waiting_on_them(tmp_path / "none") == {"error": "file not found", "rows": None}


def test_the_page_shows_waiting_on_them_in_its_own_section():
    b3 = _section(demo_html(), "theirs")
    assert "Waiting on them" in b3 and "Acme Corp: Await their read" in b3
    src = mp.gather_demo()
    src["waiting_on_them"] = {"error": "file not found", "rows": None}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "Pipeline unavailable: file not found" in _section(page, "theirs")
    src["waiting_on_them"] = {"error": None, "rows": [{"company": f"Co{i}", "next": "Await", "updated": ""} for i in range(6)]}
    page = mp.render(mp.build_view(src, NOW), NOW, demo=True)
    assert "and 2 more" in _section(page, "theirs")
    src.pop("waiting_on_them")
    assert "Pipeline unavailable: not read" in _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "theirs")


@pytest.mark.parametrize("nxt,theirs", [
    ("Will follow up Monday", False), ("I will send the deck", False), ("Nick will call back", False),
    ("Casey will reply by Thu", True), ("Casey to reply by EOD", True), ("Await Sam's times", True),
    ("Call to be scheduled", False), ("Intro scheduled once Nick sends times", False),
    ("team will reply soon", False), ("Need to reply by Fri", False), ("they will send it", False),
])
def test_will_needs_a_named_other_person(nxt, theirs):
    assert bool(mp.THEIRS.search(nxt)) is theirs


def test_no_window_height_clips_or_overlaps_a_section():
    # Codex 2026-10-30 F1: a height breakpoint only moved the failure to the next pixel.
    # Rows size to content at every height; only a list item clamps (to two lines).
    css = mp.CSS
    assert "grid-template-rows" not in css and "max-height" not in css
    assert "overflow:hidden" not in css.replace("-webkit-line-clamp:2;overflow:hidden", "")


def test_a_todo_line_leads_with_its_due_tag_so_the_two_line_clamp_cannot_hide_it():
    b1 = _section(demo_html(), "b1")
    assert "<li>(2d overdue) " in b1 and "(2d overdue)</li>" not in b1


def test_an_unavailable_todo_list_renders_instead_of_crashing():
    view = {**mp.build_view(mp.gather_demo(), NOW), "todos": None}
    assert "<h3>Top to-dos</h3>" in _section(mp.render(view, NOW, demo=True), "b1")





# --- the funnel (funnel-intake.md step 1, Nick 2026-11-17) ----------------------------------

def test_the_funnel_shows_the_week_against_the_target():
    b3 = _section(demo_html(), "b3")
    assert ('<tr><td>Applications this week</td><td class="n warn">1 <span class="muted">/ 5</span>'
            in b3)
    assert '<tr><td>Sends this week</td><td class="n">8</td></tr>' in b3
    assert '<tr><td>Replies this week</td><td class="n">2</td></tr>' in b3
    assert '<tr><td>Live processes</td><td class="n">2</td></tr>' in b3
    assert '<tr><td>Watched companies</td><td class="n">4</td></tr>' in b3
    assert 'never engaged</td><td class="n">1 / 2 / 1</td>' in b3
    assert 'list last grew</td><td class="n">2025-12-01</td>' in b3


def test_applications_at_or_over_target_are_not_red():
    src = mp.gather_demo()
    src["day"]["counts"]["applications"]["week"] = 5
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert '<td class="n">5 <span class="muted">/ 5</span>' in b3
    src["day"]["targets"] = {}
    src["day"]["counts"]["applications"]["week"] = None
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert '<tr><td>Applications this week</td><td class="n">unknown</td></tr>' in b3


def test_the_funnel_lists_passed_roles_with_scores_links_and_live_processes():
    b3 = _section(demo_html(), "b3")
    assert "Passed the title list, unread or still open, by score: 2" in b3
    # apply_url when the posting has one, else the posting URL.
    assert ('<li><strong>8</strong> <a href="https://jobs.example.com/gamma/1/apply" target="_blank" '
            'rel="noopener">Gamma LLC: Deployment Strategist</a></li>') in b3
    assert ('<li><strong>6</strong> <a href="https://jobs.example.com/beta/2" target="_blank" '
            'rel="noopener">Beta Inc: Chief of Staff</a> <span class="muted">'
            '(in pipeline: Applied (Ops))</span></li>') in b3
    assert "<li>Zeta: Recruiter screen</li>" in b3
    assert "plus 1 open rows not yet in a process" in b3


def test_the_roles_to_act_on_come_first_in_the_funnel():
    b3 = _section(demo_html(), "b3")
    order = [b3.index(x) for x in ('id="cuts-head"', "Passed the title list", "This week",
                                   "Live processes</h3>", "Your rulings", "<details>")]
    assert order == sorted(order)


def test_the_short_list_holds_only_new_cuts_worth_a_look_with_links():
    b3 = _section(demo_html(), "b3")
    head = b3[b3.index('id="cuts-head"'):b3.index("Passed the title list")]
    assert "new, in a location the scan accepts, cut only because the title is not on the list: 1</h3>" in head
    assert ('<code>aaaa1111</code> <a href="https://jobs.example.com/acme/1" target="_blank" '
            'rel="noopener">Acme Corp: Head of Operations Planning</a>') in head
    # not worth a look (engineer, location) or not new: only in the full list
    for key in ("aaaa2222", "bbbb1111", "aaaa3333"):
        assert key not in head
    assert "tools/title_cut_ruling.py mark --key KEY --reason" in head
    src = mp.gather_demo()
    for c in src["funnel"]["cuts"]["cuts"]:
        c["new"] = False
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "None new in this scan." in b3


def test_every_cut_is_in_the_full_list_with_key_reason_and_history_grouped_by_company():
    b3 = _section(demo_html(), "b3")
    full = b3[b3.index("<details>"):]
    assert ("<summary>Every cut in the scan of " in full
            and ": 4 (2 in an accepted location and cut only by the title list)</summary>" in full)
    assert full.index("<h3>Acme Corp: 3</h3>") < full.index("<h3>Beta Inc: 1</h3>")
    assert ('<code>aaaa2222</code> <a href="https://jobs.example.com/acme/2" target="_blank" '
            'rel="noopener">Senior Software Engineer</a> <span class="muted">(engineer title)') in full
    assert "Strategy Lead</a> <span class=\"muted\">(location: excluded; London)" in full
    assert ("(title not in the target&#x27;s role_filters; you passed 01/02 (Business Operations))"
            in full)


def test_a_ruled_cut_is_marked_first_in_its_group_and_listed_in_the_rulings():
    b3 = _section(demo_html(), "b3")
    assert "Your rulings in the log: 1</h3>" in b3
    assert "<li>Acme Corp: Head of Operations Planning. ops planning is in lane" in b3
    full = b3[b3.index("<details>"):]
    acme = full[full.index("<h3>Acme Corp: 3</h3>"):full.index("<h3>Beta Inc")]
    assert acme.index("aaaa1111") < acme.index("aaaa2222") < acme.index("aaaa3333")
    assert '<span class="ruled">RULED should have shown: ops planning is in lane</span>' in acme
    assert acme.count("RULED") == 1


def test_a_link_is_only_made_from_an_http_url():
    assert mp._link("javascript:alert(1)", "x") == "x"
    assert mp._link(None, "a<b") == "a&lt;b"
    assert mp._link("http://e.example/x?a=1&b=2", "t") == (
        '<a href="http://e.example/x?a=1&amp;b=2" target="_blank" rel="noopener">t</a>')


def test_a_scan_store_error_is_shown():
    src = mp.gather_demo()
    src["funnel"]["scans_error"] = "cut-scan store unreadable, every cut shown as new: x"
    assert "cut-scan store unreadable" in _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")


def test_a_late_scan_is_flagged_and_an_on_time_one_is_not():
    assert "has not run since" not in _section(demo_html(), "b3")
    src = mp.gather_demo()
    src["funnel"]["scan"]["age_hours"] = 27.2
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "(27.2 hours ago); it runs daily at 8:30." in b3
    src["funnel"]["scan"]["age_hours"] = 26
    assert "has not run since" not in _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")


def test_the_funnel_degrades_source_by_source():
    src = mp.gather_demo()
    src["funnel"]["cuts"] = {"error": "no role queue at x", "scanned_at": None, "cuts": None}
    src["funnel"]["passed"] = {"error": "unreadable role queue: y", "roles": None}
    src["funnel"]["live"] = {"error": "file not found", "rows": None, "other_open": None}
    src["funnel"]["watched"] = {"error": "data/scan-targets.yaml not found"}
    src["funnel"]["rulings"] = {"error": "unreadable rulings log: z", "rulings": None, "bad_lines": 0}
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "Cuts unavailable: no role queue at x" in b3
    assert "unreadable role queue: y" in b3 and "Pipeline unavailable: file not found" in b3
    assert '<tr><td>Live processes</td><td class="n">unknown</td></tr>' in b3
    assert '<tr><td>Watched companies</td><td class="n">unknown</td></tr>' in b3
    assert "Watched list: data/scan-targets.yaml not found" in b3
    src["funnel"]["cuts"] = mp.gather_demo()["funnel"]["cuts"]
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "Rulings log unreadable: unreadable rulings log: z" in b3
    v = mp.build_view({k: v for k, v in mp.gather_demo().items() if k != "funnel"}, NOW)
    assert v["funnel"]["cut_groups"] is None and v["funnel"]["cuts_error"] == "not read"
    assert v["funnel"]["to_look"] is None and v["funnel"]["cut_count"] is None
    assert "Cuts unavailable: not read" in mp.render(v, NOW, demo=True)


def test_bad_rulings_lines_are_reported_and_empty_lists_say_so():
    src = mp.gather_demo()
    src["funnel"]["rulings"]["bad_lines"] = 2
    src["funnel"]["passed"]["roles"] = []
    src["funnel"]["live"]["rows"] = []
    src["funnel"]["live"]["other_open"] = 0
    src["funnel"]["watched"]["open"] = None
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert '<p class="warn">2 unreadable lines in the log</p>' in b3
    assert "Nothing passed." in b3 and "<h3>Live processes</h3><p class=\"muted\">None.</p>" in b3
    assert "not yet in a process" not in b3 and "never engaged" not in b3


def test_the_rulings_list_shows_the_newest_few():
    src = mp.gather_demo()
    src["funnel"]["rulings"]["rulings"] = [{"key": f"k{i}", "company": f"Co{i}", "title": "T",
                                            "reason": "r", "ruled_at": f"2026-01-0{i}T00:00"}
                                           for i in range(1, 7)]
    v = mp.build_view(src, NOW)
    assert [r["key"] for r in v["funnel"]["rulings"]] == ["k6", "k5", "k4", "k3"]
    assert v["funnel"]["ruling_count"] == 6
    assert "(2026-01-06)" in mp.render(v, NOW, demo=True)


def test_when_formats_local_time_and_passes_garbage_through():
    t = datetime.fromisoformat("2026-01-06T16:30:00+00:00").astimezone()
    want = f"{t.strftime('%a %b')} {t.day}, {t.strftime('%I:%M %p').lstrip('0')}"
    assert mp._when("2026-01-06T16:30:00+00:00") == want
    assert mp._when("nope") == "nope"


def test_the_funnel_spans_the_page_and_the_cut_list_never_clips():
    assert ".funnel{grid-column:1/-1}" in mp.CSS and "ul.cuts{columns:2" in mp.CSS
    assert "<details open>" not in demo_html()  # the full list is one click away, not in the way


def test_only_nicks_rulings_count_or_mark_a_cut():
    # Codex F2, 2026-11-18: a Claude entry was counted as "Your rulings" and marked its cut.
    assert mp._by({"by": "nick"}) == "" and mp._by({}) == "" and mp._by({"by": None}) == ""
    assert mp._by({"by": "claude"}) == " (by claude)"
    src = mp.gather_demo()
    src["funnel"]["rulings"]["rulings"][0]["by"] = "claude"
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "RULED" not in b3 and "Your rulings in the log: 0</h3>" in b3
    assert "1 entries in the log not made by you are not counted and mark nothing." in b3
    assert "ops planning is in lane" not in b3
    assert "not made by you" not in _section(demo_html(), "b3")


def test_empty_funnel_values_render_nothing_not_a_placeholder():
    b3 = _section(demo_html(), "b3")
    assert '<p class="warn"></p>' not in b3              # no scan-store error
    assert "Watched list:" not in b3                       # no watched-list error
    assert "unreadable lines" not in b3                    # no bad log lines
    assert "None)" not in b3 and "; )" not in b3 and "(; " not in b3
    src = mp.gather_demo()
    src["funnel"]["rulings"]["rulings"] = []
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "<ul></ul>" not in b3 and "Your rulings in the log: 0</h3>" in b3


def test_an_unreadable_watched_list_shows_no_watched_rows():
    src = mp.gather_demo()
    src["funnel"]["watched"] = {"error": "data/scan-targets.yaml not found"}
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "list last grew" not in b3 and "never engaged" not in b3


def test_unlisted_standing_roles_are_named_not_dropped():
    assert "more standing roles" not in _section(demo_html(), "b3")
    src = mp.gather_demo()
    src["funnel"]["passed"]["unlisted"] = 6
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert '<p class="warn">plus 6 more standing roles the queue does not store</p>' in b3



def test_fetch_failures_and_stale_goals_are_stated():
    assert "failed to fetch" not in _section(demo_html(), "b3")
    assert "goals.md is stale" not in _section(demo_html(), "b3")
    src = mp.gather_demo()
    src["funnel"]["fetch_failures"] = 3
    src["funnel"]["passed"]["goals_stale"] = True
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "3 boards failed to fetch in this scan; their roles are missing from both lists." in b3
    assert "goals.md is stale, so the scan scored nothing" in b3


def test_rulings_join_on_the_full_posting_identity_not_the_short_key():
    # Fable F4: two postings sharing an 8-hex key must not both read RULED.
    src = mp.gather_demo()
    for c in src["funnel"]["cuts"]["cuts"]:
        c["key"] = "samekey1"
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert b3.count("RULED should have shown") == _section(demo_html(), "b3").count("RULED should have shown") == 2


def test_funnel_rows_are_not_clamped_to_two_lines():
    assert ".funnel li{display:block}" in mp.CSS



def test_a_ruling_with_a_non_text_role_key_is_skipped_not_a_crash():
    src = mp.gather_demo()
    src["funnel"]["rulings"]["rulings"].append({"key": "x", "role_key": ["a"], "by": "nick"})
    src["funnel"]["rulings"]["rulings"].append({"key": "y", "role_key": {"a": 1}, "by": "nick"})
    b3 = _section(mp.render(mp.build_view(src, NOW), NOW, demo=True), "b3")
    assert "Your rulings in the log: 3</h3>" in b3
