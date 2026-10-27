"""Tests for tools/duet_snapshot.py (daily job-search snapshot for a morning-brief agent).

Made-up fixtures only: this file is public. Target date throughout is 2026-03-10 (Tue).
"""
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import duet_snapshot as ds  # noqa: E402

TODAY = date(2026, 3, 10)

PIPELINE = """\
# Job Application Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
|---------|------|-------|--------------|-------------|---------|-------|-----|
| Acme AI | Deployment Lead | Interviewing | 2026-03-08 | Panel interview Thu 2026-03-12 2pm; email casey@acme.example re $95K-110K | cv.pdf | n | https://acme.example/job |
| Globex | Ops Manager | Applied | 2026-03-01 | — | cv.pdf | n | u |
| Initech | Strategy | To Apply | 2026-03-02 | Read output/initech/initech.md first | — | n | u |
| Umbrella | BizOps | Closed - they passed | 2026-02-01 | — | — | n | u |

## Archived

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
|---------|------|-------|--------------|-------------|---------|-------|-----|
| Hooli | PM | Rejected | 2026-01-01 | — | — | n | u |
"""

TODOS = """\
# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|------|----------|-----|--------|-------|
| Follow up: Jordan Lee @ Acme AI — Wait for reply on the 3/2 note | Med | 2026-03-12 | Pending | — |
| Follow up: Jordan Lee — Ball on Jordan. If no reply by 3/9, nudge | Med | 2026-03-09 | Pending | — |
| Follow up: Casey Doe — Send the case study, call 415-555-0134 | Med | 2026-03-11 | Pending | — |
| Follow up: Alex Example — Wait for reply | Med | 2026-03-17 | Pending | — |
| Follow up: Late Person — Wait for reply | Med | 2026-03-18 | Pending | — |
| Follow up: Recent Overdue — Send the deck | Med | 2026-02-24 | Pending | — |
| Follow up: Stale Overdue — Send the deck | Med | 2026-02-23 | Pending | — |
| Follow up: Dropped Contact — Wait for reply | Med | 2026-03-11 | Withdrawn 2026-03-01 (role closed) | — |
| Follow up: Finished Contact — Wait for reply | Med | 2026-03-11 | Done 2026-03-05 | — |
| Follow up: Closed Loop — — (loop closed; no action owed) | Med | 2026-03-11 | Pending | — |
| Prep recruiter call with Sam Sample Thu 3/12 10am | High | 2026-03-11 | Pending | — |
| Pitch drill before any call | High | 2026-03-11 | Pending | — |
| Build an internal hook | High | 2026-03-11 | Pending | — |

## Completed

| Task | Priority | Completed | Notes |
|------|----------|-----------|-------|
"""

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
|------|---------|------|--------------|-------|------------------|-------|
| Alex Example | Initech | Director | warm | 2026-02-01 | 2026-03-03 | alex@initech.example |
| Jordan Lee | Acme AI | Recruiter | warm | 2026-02-01 | 2026-03-02 | jordan@acme.example |
| Late Person | Future Co | VP | cold | 2026-02-01 | 2026-03-20 | — |

## Interaction Log
"""


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "job-pipeline.md").write_text(textwrap.dedent(PIPELINE), encoding="utf-8")
    (d / "job-todos.md").write_text(textwrap.dedent(TODOS), encoding="utf-8")
    (d / "networking.md").write_text(textwrap.dedent(NETWORKING), encoding="utf-8")
    return tmp_path


@pytest.fixture
def snap(repo):
    return ds.build_snapshot(repo, TODAY)


def _section(text, n):
    parts = text.split("\n## ")
    return next(p for p in parts if p.startswith(f"{n}."))


# --- 1. Active pipeline ------------------------------------------------------------

def test_active_pipeline_includes_in_process_and_excludes_terminal(snap):
    s1 = _section(snap, 1)
    assert "(2 in process)" in s1
    assert "- Acme AI - Deployment Lead | stage: Interviewing" in s1
    assert "- Globex - Ops Manager | stage: Applied | next: none noted" in s1
    assert "Umbrella" not in snap        # terminal stage in the Active table
    assert "Hooli" not in snap           # archived section


def test_backlog_row_goes_on_the_radar_not_in_process(snap):
    s1 = _section(snap, 1)
    in_process, radar = s1.split("On the radar")
    assert "Initech" not in in_process
    assert "(1):\n- Initech - Strategy | stage: To Apply" in radar


def test_terminal_guard_drops_a_row_the_bucket_calls_active():
    # "Dead lead" is unknown to the bucket map (defaults to active) but terminal per
    # stage_vocab, so the stage_vocab guard must remove it.
    content = textwrap.dedent(PIPELINE).replace("Interviewing", "Dead lead")
    in_process, radar = ds.build_pipeline(content, TODAY)
    assert [e["company"] for e in in_process] == ["Globex"]
    assert [r["company"] for r in radar] == ["Initech"]


def test_in_process_carries_next_action_from_pipe_read(repo):
    content = (repo / "data" / "job-pipeline.md").read_text(encoding="utf-8")
    in_process, _ = ds.build_pipeline(content, TODAY)
    assert in_process[0]["next_action"].startswith("Panel interview Thu 2026-03-12")


def test_in_process_is_metrics_bucket_minus_stage_vocab_terminal(repo):
    # todo_daily_metrics buckets unknown stages as active, so "Closed - they passed"
    # lands in its active_process; the stage_vocab guard is what removes it.
    from todo_daily_metrics import parse_pipeline
    content = (repo / "data" / "job-pipeline.md").read_text(encoding="utf-8")
    bucket = [e["company"] for e in parse_pipeline(content)["active_process"]]
    in_process, _ = ds.build_pipeline(content, TODAY)
    assert bucket == ["Acme AI", "Globex", "Umbrella"]
    assert [e["company"] for e in in_process] == ["Acme AI", "Globex"]


# --- 2. Due window -----------------------------------------------------------------

def test_seven_day_window_and_fourteen_day_overdue_cutoff(snap):
    s2 = _section(snap, 2)
    assert "2026-02-24 (Tue) OVERDUE follow up with Recent Overdue: Send the deck" in s2
    assert "Stale Overdue" not in snap          # 15 days late: stale backlog
    assert "Not shown: 1 older overdue" in s2
    s3 = _section(snap, 3)
    assert "Alex Example (Initech)" in s3       # due today + 7: inside
    assert "Late Person" not in snap            # due today + 8: outside


def test_pipeline_dated_next_action_appears_in_due_section(snap):
    assert "- 2026-03-12 (Thu) [pipeline] Acme AI: Panel interview" in _section(snap, 2)


def test_dated_call_task_included_undated_chores_excluded(snap):
    s2 = _section(snap, 2)
    assert "Prep recruiter call with Sam Sample Thu 3/12 10am" in s2
    assert "Pitch drill" not in snap            # mentions a call, names no date
    assert "internal hook" not in snap          # not a follow-up or a call


def test_withdrawn_done_and_loop_closed_rows_are_excluded(repo, snap):
    for name in ("Dropped Contact", "Finished Contact", "Closed Loop"):
        assert name not in snap
    _, active, _ = ds.parse_todos((repo / "data" / "job-todos.md").read_text(encoding="utf-8"), TODAY)
    todos = ds.classify_todos(active, TODAY, {})
    assert todos["closed_skipped"] == 3


def test_due_follow_up_that_is_not_a_wait_shows_with_contact_company(snap):
    s2 = _section(snap, 2)
    assert "2026-03-11 (Wed) follow up with Casey Doe: Send the case study, call" in s2


# --- 3. Waiting on -----------------------------------------------------------------

def test_waiting_on_merges_duplicate_rows_per_contact(snap):
    s3 = _section(snap, 3)
    assert "Waiting on replies (2)" in s3
    assert s3.count("Jordan Lee") == 1
    line = next(l for l in s3.splitlines() if "Jordan Lee" in l)
    # company from the "@" row, since from the roster, earliest nudge wins with its text
    assert line.startswith("- Jordan Lee (Acme AI) | since 2026-03-02 | "
                           "next nudge 2026-03-09 (Mon) OVERDUE | Ball on Jordan")


def test_waiting_on_since_falls_back_to_unknown():
    active = [{"task": "Follow up: Pat Q — Wait for reply", "due": "2026-03-11",
               "status": "Pending"}]
    w = ds.classify_todos(active, TODAY, {})["waiting"]["pat q"]
    assert w["since"] is None and w["company"] == ""
    roster = {"pat q": {"company": "Acme AI", "last_interaction": "2026-03-20"}}
    w = ds.classify_todos(active, TODAY, roster)["waiting"]["pat q"]
    assert w["since"] is None                    # after the snapshot date: ignored
    assert w["company"] == "Acme AI"


@pytest.mark.parametrize("task,expected", [
    ("Follow up: Jordan Lee @ Acme AI - check for a reply",
     {"name": "Jordan Lee", "company": "Acme AI", "action": "check for a reply"}),
    ("Follow up: Sam Lee-Smith — Wait for reply",
     {"name": "Sam Lee-Smith", "company": "", "action": "Wait for reply"}),
    ("Follow up: Casey Doe (Globex) - send notes",
     {"name": "Casey Doe", "company": "Globex", "action": "send notes"}),
    ("Follow up: Alex Example — —",
     {"name": "Alex Example", "company": "", "action": ""}),
    ("Email Casey Doe", None),
])
def test_parse_follow_up(task, expected):
    assert ds.parse_follow_up(task) == expected


def test_priority_lists_in_header(snap):
    head = snap.split("\n## ")[0]
    assert "Priority companies: Acme AI, Globex, Initech" in head
    assert "Priority people: Alex Example, Casey Doe, Jordan Lee, Recent Overdue" in head
    assert "Draft replies only" in head and "America/Los_Angeles" in head


# --- privacy -------------------------------------------------------------------------

def test_no_emails_money_phones_urls_paths_or_em_dashes(snap):
    assert "@" not in snap
    assert "$" not in snap
    assert "555-0134" not in snap
    assert "http" not in snap and "acme.example" not in snap
    assert "output/" not in snap and ".md" not in snap and ".pdf" not in snap
    assert "—" not in snap and "–" not in snap


@pytest.mark.parametrize("raw,clean", [
    ("mail a.b@x.example now", "mail now"),
    ("comp $95K-$110K base", "comp base"),
    ("asked 90k floor", "asked floor"),
    ("ring (415) 555-0134 today", "ring today"),
    ("see https://x.example/a) ok", "see) ok"),
    ("see jobs.board.com/acme ok", "see ok"),
    ("read data/notes.md and ~/x/y first", "read and first"),
    ("open plan.json", "open"),
    ("reaction/comment stays", "reaction/comment stays"),
    ("A — B – C", "A - B - C"),
    ("via (https://x.example, -) done", "via done"),
    ("CV in /Users/casey/Downloads/cv-v3 now", "CV in now"),
    ("log at /private/tmp/run ok", "log at ok"),
    ("offer €120k base", "offer base"),
    ("offer £95,000 base", "offer base"),
    ("asked 150,000 USD floor", "asked floor"),
    ("asked USD 150k floor", "asked floor"),
    ("band 140,000 to 160,000 noted", "band to noted"),
    ("ring +44 20 7946 0958 today", "ring today"),
    ("ring +1 (415) 555 0134 today", "ring today"),
    ("ring 020 7946 0958 today", "ring today"),
    ("CV at C:\\Users\\casey\\cv.pdf ok", "CV at ok"),
    ("share \\\\host\\share\\x ok", "share ok"),
    ("get ftp://x.example/a ok", "get ok"),
    ("join zoom.us/j/123456789 now", "join now"),
    ("join meet.example.us now", "join now"),
    ("offer ₽100000 base", "offer base"),
    ("offer ₹12,00,000 base", "offer base"),
    ("offer CHF 95000 base", "offer base"),
    ("offer 95000 CHF base", "offer base"),
    ("ref 4815162342 noted", "ref noted"),
    ("loop 2026-03-11 then 2026-03-12", "loop 2026-03-11 then 2026-03-12"),
    ("PST 2pm call", "PST 2pm call"),
    ("call 2026-03-11 415 555 0134 ok", "call 2026-03-11 ok"),
    ("ring 415 555 0134 2026-03-11 ok", "ring 2026-03-11 ok"),
    ("band 150-180k noted", "band noted"),
    ("band 150k-180k noted", "band noted"),
    ("fee USD 950 ok", "fee ok"),
    ("fee 950 EUR ok", "fee ok"),
    ("see private/notes/today ok", "see ok"),
    ("notes/drafts stays", "notes/drafts stays"),
    ("rooms 12 34 56 booked", "rooms 12 34 56 booked"),
    ("logs in /tmp now", "logs in now"),
    ("see /etc ok", "see ok"),
    ("bonus 9K noted", "bonus noted"),
    ("bonus 1.5K noted", "bonus noted"),
    ("fee usd 950 ok", "fee ok"),
    ("open 24/7 ok", "open 24/7 ok"),
    ("reach out via /cold-outreach or /follow-up", "reach out via /cold-outreach or /follow-up"),
    ("file /Users/casey/My Drive/Job Notes/plan now", "file now"),
    ("file C:\\Users\\casey\\My Docs\\plan now", "file now"),
    ("open budget.xlsx and deck.pptx now", "open and now"),
    ("see photo.JPG ok", "see ok"),
    ("offer 950€ base", "offer base"),
    ("offer 120 000 € base", "offer base"),
    ("offer 95k€ base", "offer base"),
    ("ref urn:isbn:0451450523 ok", "ref ok"),
    ("img data:image/png;base64,AAAA ok", "img ok"),
    ("call tel:+14155550134 ok", "call ok"),
    ("offer €120 000 base", "offer base"),
    ("offer €120\u202f000 base", "offer base"),
    ("offer 120\u00a0000 € base", "offer base"),
    ("asked EUR 120 000 floor", "asked floor"),
    ("asked 120 000 EUR floor", "asked floor"),
    ("band €120 000-€130 000 noted", "band noted"),
    ("band CHF 120'000 - 130'000 noted", "band noted"),
    ("met 3 people at 2pm", "met 3 people at 2pm"),
    ("bonus 1000K noted", "bonus noted"),
    ("bonus 1,000k noted", "bonus noted"),
    ("see output/My Drive/plan ok", "see ok"),
    ("see ~/My Drive/plan ok", "see ok"),
    ("call 3/12 415 555 0134 ok", "call 3/12 ok"),
    ("offer RMB 15 000 base", "offer base"),
    ("offer XYZ 1 000 base", "offer base"),
    ("offer 15 000 RMB base", "offer base"),
    ("offer RMB 1.5M base", "offer base"),
    ("asked 120 000 floor", "asked floor"),
    ("asked 120\u202f000 floor", "asked floor"),
    ("asked 1.200.000 floor", "asked floor"),
    ("asked 120'000 floor", "asked floor"),
    ("call at 10.30 ok", "call at 10.30 ok"),
    ("asked 120\u2009000 floor", "asked floor"),
    ("asked €120\u2007000 floor", "asked floor"),
    ("fee USD  950 base", "fee base"),
    ("fee USD\t950 base", "fee base"),
    ("ring 415  555  0134 now", "ring now"),
    ("ring +44 20/7946 0958 now", "ring now"),
    ("ring 415·555·1212 now", "ring now"),
    ("mail o'connor@example.com now", "mail now"),
    ("mail casey@[192.0.2.1] now", "mail now"),
    ("mail casey@localhost now", "mail now"),
    ("open 24/7 ok", "open 24/7 ok"),
    ("send resume.odt and logo.svg ok", "send and ok"),
    ("note memo.mp3 and backup.tar.gz ok", "note and ok"),
    ("call Thu 3/12 w/ Casey", "call Thu 3/12 w/ Casey"),
    ("and/or later", "and/or later"),
])
def test_sanitize(raw, clean):
    assert ds.sanitize(raw) == clean


def test_sanitize_truncates_on_a_word():
    out = ds.sanitize("word " * 50, limit=20)
    assert out == "word word word word..."


# --- CLI ---------------------------------------------------------------------------

def test_cli_refuses_out_inside_data(repo, capsys):
    target = repo / "data" / "job-pipeline.md"
    before = target.read_text(encoding="utf-8")
    assert ds.main(["--date", "2026-03-10", "--repo-root", str(repo), "--out", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == before
    assert "refusing --out" in capsys.readouterr().err
    assert ds.main(["--date", "2026-03-10", "--repo-root", str(repo),
                    "--out", str(repo / "data")]) == 2


def test_closed_row_with_a_dated_next_action_is_not_upcoming(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    pipe = PIPELINE.replace(
        "| Umbrella | BizOps | Closed - they passed | 2026-02-01 | — |",
        "| Umbrella | BizOps | Closed - they passed | 2026-02-01 | Call recruiter Wed 2026-03-11 |")
    assert "Call recruiter Wed 2026-03-11" in pipe
    (d / "job-pipeline.md").write_text(pipe, encoding="utf-8")
    (d / "job-todos.md").write_text(textwrap.dedent(TODOS), encoding="utf-8")
    (d / "networking.md").write_text(textwrap.dedent(NETWORKING), encoding="utf-8")
    out = ds.build_snapshot(tmp_path, TODAY)
    assert "Call recruiter" not in out and "Umbrella" not in out
    assert "Panel interview" in out          # an open row's event still shows


def test_a_closed_duplicate_of_a_live_key_is_not_upcoming(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    pipe = PIPELINE.replace(
        "| Globex | Ops Manager | Applied | 2026-03-01 | — |",
        "| Globex | Ops Manager | Applied | 2026-03-01 | — | cv.pdf | n | u |\n"
        "| Globex | Ops Manager | Rejected | 2026-03-09 | Closed screen 2026-03-11 |")
    assert "Closed screen 2026-03-11" in pipe
    (d / "job-pipeline.md").write_text(pipe, encoding="utf-8")
    (d / "job-todos.md").write_text(textwrap.dedent(TODOS), encoding="utf-8")
    (d / "networking.md").write_text(textwrap.dedent(NETWORKING), encoding="utf-8")
    assert "Closed screen" not in ds.build_snapshot(tmp_path, TODAY)


def test_an_archived_only_file_shows_nothing_in_process():
    pipe = textwrap.dedent("""\
        ## Archived

        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme AI | Lead | Interviewing | 2025-01-01 | Old step | — | n | u |
        """)
    in_process, on_radar = ds.build_pipeline(pipe, TODAY)
    assert in_process == [] and on_radar == []


def test_a_closed_duplicate_of_a_live_key_is_not_in_process():
    pipe = textwrap.dedent("""\
        ## Active Pipeline

        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme AI | Lead | Applied | 2026-03-08 | Live step | — | n | u |
        | Acme AI | Lead | Closed - they passed | 2026-03-01 | — | — | n | u |
        """)
    in_process, _ = ds.build_pipeline(pipe, TODAY)
    assert [e["stage"] for e in in_process] == ["Applied"]


def test_stage_decides_when_pipe_read_has_no_matching_row(monkeypatch):
    monkeypatch.setattr(ds, "parse_all_rows", lambda content, today: [])
    content = textwrap.dedent(PIPELINE).replace("Interviewing", "Dead lead")
    in_process, _ = ds.build_pipeline(content, TODAY)
    assert [e["company"] for e in in_process] == ["Globex"]


def test_an_archived_duplicate_does_not_replace_the_active_next_action():
    pipe = textwrap.dedent("""\
        ## Active Pipeline

        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme AI | Lead | Interviewing | 2026-03-08 | Live next step | — | n | u |

        ## Archived

        | Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
        |---------|------|-------|--------------|-------------|---------|-------|-----|
        | Acme AI | Lead | Rejected | 2025-01-01 | Stale old step | — | n | u |
        """)
    in_process, _ = ds.build_pipeline(pipe, TODAY)
    assert [e["next_action"] for e in in_process] == ["Live next step"]


def test_cli_prints_and_writes_out(repo, tmp_path):
    out = tmp_path / "o" / "snap.md"
    r = subprocess.run([sys.executable, str(TOOLS / "duet_snapshot.py"), "--date", "2026-03-10",
                        "--repo-root", str(repo), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    assert r.stdout.startswith("# Job search snapshot - 2026-03-10 (Tue)")
    assert out.read_text(encoding="utf-8") == r.stdout


def test_main_defaults_to_cwd_and_today(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ds.main([]) == 0
    assert f"snapshot - {date.today().isoformat()}" in capsys.readouterr().out


# --- mutation-driven gaps ------------------------------------------------------------

def _row(task, due, status="Pending"):
    return {"task": task, "due": due, "status": status}


def test_undated_follow_up_is_skipped_not_crashing():
    todos = ds.classify_todos([_row("Follow up: Pat Q — Wait for reply", "—")], TODAY, {})
    assert todos["waiting"] == {} and todos["due_soon"] == [] and todos["stale_count"] == 0


def test_earliest_nudge_wins_regardless_of_row_order():
    rows = [_row("Follow up: Pat Q — Wait for reply A", "2026-03-09"),
            _row("Follow up: Pat Q @ Acme AI — Wait for reply B", "2026-03-12")]
    w = ds.classify_todos(rows, TODAY, {})["waiting"]["pat q"]
    assert (w["next_nudge"], w["action"], w["company"]) == (
        date(2026, 3, 9), "Wait for reply A", "Acme AI")


def test_waiting_rows_are_not_repeated_in_due_section(snap):
    assert "Jordan Lee" not in _section(snap, 2)


def test_due_soon_is_sorted_by_date():
    rows = [_row("Follow up: B Person — Send notes", "2026-03-12"),
            _row("Follow up: A Person — Send notes", "2026-03-11")]
    due = ds.classify_todos(rows, TODAY, {})["due_soon"]
    assert [e["due"] for e in due] == [date(2026, 3, 11), date(2026, 3, 12)]


def test_empty_repo_renders_explicit_empty_states(tmp_path):
    out = ds.build_snapshot(tmp_path, TODAY)
    assert "## 1. Active pipeline (0 in process)\n\n- none\n" in out
    assert "On the radar" not in out
    assert "- nothing due\n" in out
    assert "## 3. Waiting on replies (0)\n\n- none\n" in out
    assert "Priority companies: none" in out and "Priority people: none" in out


def test_non_empty_sections_carry_no_empty_markers(snap):
    assert "- none\n" not in snap and "- nothing due" not in snap
