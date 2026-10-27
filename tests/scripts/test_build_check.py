"""tools/build_check.py: the SCRIPT checks and drain flags over output/**/BUILD-LOG.md.

Pinned behaviors:
1. Zero logs is a warning, never "0 builds".
2. The unfilled template is incomplete; an old log with no v2 header is incomplete, not an error.
3. Each flag (expired, check analytics and Gmail, drain due, workflow edit due) fires on its
   trigger and not without it.
4. Each failed check fires on a broken log and stays quiet on the filled one.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import build_check as bc  # noqa: E402

TODAY = dt.date(2026, 3, 10)

TEMPLATE = """# Build log: <artifact, one line>

Last updated: YYYY-MM-DD
Variant: V<n> | new   adapted_from: <output/<slug>/BUILD-LOG.md or appendix row | none>

## Phase 0 (required for this variant's tier; blank = no verdict)
filled_by: nick | claude   route: Q0 yes (<source:line>) · Q1 <yes/no> · Q2 <yes/no: why not in text> · Q3 <yes/no, count> · Q4 <adapt/blank>
reader: <name, role, owns; theirs to act on: yes/no>   channel: <reader-named>   address_source: <inbound | verified record | guess: <how delivery is confirmed>>
reader_ask: "<verbatim>" (<transcript file:line | sources/<snapshot> | gmail id, date>)   answers_ask: yes/no (re-answered at ship)
external_stake: <the real thing touched | none (then this is a demo)>
time_box: signal "<quoted>" | need_expires YYYY-MM-DD | research <h> | design <h> | review loops <n> | send_date YYYY-MM-DD
value_item: <one sentence the reader can use even if they never reply>   proof_purpose: <stated separately>
not_a_copy (V1): tells them something they lack and the first screen shows it: yes/no   company-facts grep: <0 hits | lines>
prior_outcomes: <variant: sends N, written replies N, conversations N; source: appendix 8.2 row | outreach_status query, date>
prediction: <reply yes/no by YYYY-MM-DD; what would change the method>
lessons_printed: <N rows>   rows_that_change_this_build: <AR ids | none>
frame: <output/<slug>/frame.yaml | none>
verdict: build | reshape (<change>) | don't build (<what would have to be true>)   nick_confirmed: YYYY-MM-DD | pending

## Entries
### YYYY-MM-DD HH:MM | Step <n>: <what> | duration <h:mm> | who: nick | claude
- Done: ...   Tools: ...   Output: <file>   Failed or cut: ...

## Ship
reviewed_text: <file, Entry id>   shipped_text: <file, Entry id>   diff: <0 lines | reviewed again YYYY-MM-DD>
review_dispositions: <N fixed, N signed off: nick YYYY-MM-DD | refusal logged, Entry id>
phone_check: <screenshots>   deploy_verify: <curl results>   analytics: on/off   access: <steps>
message: <capture id>   finding: "<...>"   question: "<...>"   proof: <named | n/a (warm)>   meeting_ask: <one | none>   cost_of_yes: <minutes | data | access>
recipient_equals_reader: yes   defaults_applied: <D ids>
sent: data/outreach-log.md:<line>

## After send
opened: <N visits by YYYY-MM-DD | unknown>   reply: data/networking.md:<line> | none by YYYY-MM-DD   outcome: data/job-pipeline.md:<line> | none by YYYY-MM-DD
parallel_signals: <...>   n_note: one silence is n=1

## Method notes for reuse
Transferred unchanged: ...   Had to change: ...   Do differently: ...

## Drain
ledger_rows: <added N, incremented <AR ids>>   workflow_edit_todo: <todo fragment | none>   drained: YYYY-MM-DD
"""

FILLED = """# Build log: ops review page for Acme

Last updated: 2026-03-01
Variant: V1   adapted_from: none

## Phase 0
filled_by: nick   route: Q0 yes (sources/ask.md:3) · Q1 yes · Q2 yes · Q3 no · Q4 blank
reader: Casey Doe, VP Operations at Acme, owns the review; theirs to act on: yes   channel: email   address_source: inbound message
reader_ask: "how do you run the monthly review" (sources/ask.md:3)   answers_ask: yes
external_stake: Acme's public order data
time_box: signal "next week" | need_expires 2026-03-08 | research 3 | design 2 | review loops 2 | send_date 2026-03-05
value_item: A one-page template for the monthly review.   proof_purpose: shows operating rigor
not_a_copy (V1): tells them something they lack and the first screen shows it: yes   company-facts grep: 0 hits
prior_outcomes: V1: sends 2, written replies 0, conversations 0; source: appendix 8.2 row
prediction: reply yes by 2026-03-12; a no means shorter page
verdict: build   nick_confirmed: 2026-03-01

## Entries
### 2026-03-01 09:00 | Step 1: open log, save brief | duration 0:30 | who: claude
- Done: brief saved.
### 2026-03-01 10:00 | Step 2: sources | duration 2:00 | who: claude
- Done: fetched.
### 2026-03-02 10:00 | Step 4: draft | duration 1:00 | who: nick
- Done: draft.
### 2026-03-02 12:00 | Step 5: design | duration 1:30 | who: claude
- Done: design.
### 2026-03-03 10:00 | Step 6: reader review | duration 0:30 | who: claude
- Done: review.

## Ship
reviewed_text: page.html, 2026-03-03 10:00   shipped_text: page.html, 2026-03-03 10:00   diff: 0 lines
review_dispositions: 3 fixed, 0 signed off
recipient_equals_reader: yes   defaults_applied: D2, D3
reader_review: output/analysis/030926-codex-acme-reader-review.md   entry: 2026-03-09 11:00
sent: data/outreach-log.md:2

## After send
opened: 2 visits by 2026-03-07   reply: none by 2026-03-09   outcome: none by 2026-03-09

## Drain
ledger_rows: added 0   workflow_edit_todo: none   drained: 2026-03-09
"""

OLD = """# Build log: an earlier playbook

Last updated: 2026-02-01

## Entries

### 2026-02-01 12:16 | Step 0: read the framework
- **Done:** read things.

## Method notes for reuse
- notes
"""

LESSONS = """# Lessons

## Section 2 - Email / Outreach Corrections

| Pattern | Rule | Occurrences | Promoted | Date |
|---------|------|-------------|----------|------|
| [artifact] AR4 restated the product | do not | {occ} | {promoted} | 2026-03-01 |
| AR4 not an artifact row | x | 5 | No | 2026-03-01 |
"""


def _repo(tmp_path, logs: dict, outreach_lines=3, lessons=None) -> Path:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "outreach-log.md").write_text(
        "".join(f"| 2026-03-0{i % 9 + 1} | cold-outreach | email | Casey Doe | Acme | x | Sent | |\n"
                for i in range(outreach_lines)), encoding="utf-8")
    (repo / "output").mkdir(exist_ok=True)
    if lessons is not None:
        (repo / "memory").mkdir(exist_ok=True)
        (repo / "memory" / "lessons.md").write_text(lessons, encoding="utf-8")
    for rel, text in logs.items():
        p = repo / "output" / rel / "BUILD-LOG.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return repo


def _check(text, repo, today=TODAY):
    return bc.check_log(bc.parse_log(text), today, repo)


def _edit(text, old, new):
    assert old in text, old
    return text.replace(old, new)


# --- discovery and the zero-log warning -------------------------------------------------

def test_zero_logs_render_a_warning_never_zero_builds(tmp_path):
    out = bc.report(_repo(tmp_path, {}), TODAY)
    assert out["logs"] == 0 and out["builds"] == []
    assert out["warning"].startswith("WARNING") and out["line"] == out["warning"]
    assert "0 builds" not in out["line"].replace("'0 builds'", "")


def test_logs_are_found_recursively_and_build_folders_are_skipped(tmp_path):
    repo = _repo(tmp_path, {"acme": FILLED, "acme/playbook": OLD,
                            "acme/site/node_modules/pkg": OLD, "acme/site/.next": OLD,
                            "acme/site/.vercel": OLD})
    paths = [p.relative_to(repo).as_posix() for p in bc.find_logs(repo)]
    assert paths == ["output/acme/BUILD-LOG.md", "output/acme/playbook/BUILD-LOG.md"]


def test_json_cli_prints_every_log_and_exits_0(tmp_path, capsys):
    repo = _repo(tmp_path, {"acme": FILLED, "activeco": OLD})
    rc = bc.main(["--repo-root", str(repo), "--json", "--today", "2026-03-10"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["logs"] == 2 and out["warning"] == "" and out["open"] == 1
    by = {b["slug"]: b for b in out["builds"]}
    assert by["activeco"]["format"] == "pre-v2" and by["activeco"]["flags"] == ["incomplete"]
    assert by["acme"]["flags"] == [] and by["acme"]["failed_checks"] == []
    assert out["default_runs"] == {"D2": {"runs": 1, "logs": ["output/acme/BUILD-LOG.md"]},
                                   "D3": {"runs": 1, "logs": ["output/acme/BUILD-LOG.md"]}}
    assert out["skipped_checks"]
    assert out["reach"] == (f"scope: {bc.REACH_SCOPE} | population 2 | included 1 | excluded: drained 1")


def test_json_and_open_are_mutually_exclusive_and_one_is_required(tmp_path):
    with pytest.raises(SystemExit):
        bc.main(["--repo-root", str(tmp_path)])
    with pytest.raises(SystemExit):
        bc.main(["--repo-root", str(tmp_path), "--json", "--open", "x"])


def test_default_today_is_the_real_date(tmp_path, capsys):
    bc.main(["--repo-root", str(_repo(tmp_path, {})), "--json"])
    assert json.loads(capsys.readouterr().out)["today"] == dt.date.today().isoformat()


# --- --open -----------------------------------------------------------------------------

def test_open_exits_0_on_a_clean_log_and_1_on_a_flagged_one(tmp_path, capsys):
    repo = _repo(tmp_path, {"acme": FILLED, "activeco": OLD})
    assert bc.main(["--repo-root", str(repo), "--open", "output/acme/BUILD-LOG.md",
                    "--today", "2026-03-10"]) == 0
    assert "flags: none" in capsys.readouterr().out
    assert bc.main(["--repo-root", str(repo), "--open",
                    str(repo / "output" / "activeco" / "BUILD-LOG.md"), "--today", "2026-03-10"]) == 1
    out = capsys.readouterr().out
    assert "flags: incomplete" in out and "missing fields: v2 header" in out


def test_open_exits_1_on_a_failed_check_alone(tmp_path, capsys):
    log = _edit(FILLED, "defaults_applied: D2, D3", "defaults_applied: D9")
    repo = _repo(tmp_path, {"acme": log})
    rc = bc.main(["--repo-root", str(repo), "--open", "output/acme/BUILD-LOG.md", "--today", "2026-03-10"])
    out = capsys.readouterr().out
    assert rc == 1 and "flags: none" in out and "failed checks: defaults_applied names unknown IDs: D9" in out


def test_open_on_a_missing_log_exits_2(tmp_path, capsys):
    assert bc.main(["--repo-root", str(tmp_path), "--open", "nope.md"]) == 2
    assert "no such log" in capsys.readouterr().err


def test_open_on_a_log_outside_output_still_reports(tmp_path, capsys):
    p = tmp_path / "elsewhere" / "BUILD-LOG.md"
    p.parent.mkdir()
    p.write_text(OLD, encoding="utf-8")
    assert bc.main(["--repo-root", str(_repo(tmp_path, {})), "--open", str(p)]) == 1
    assert "pre-v2" in capsys.readouterr().out


# --- parsing ------------------------------------------------------------------------------

def test_parse_reads_header_phase0_entries_and_blocks():
    log = bc.parse_log(FILLED)
    assert log["format"] == "v2" and log["variant"] == "V1" and log["adapted_from"] == "none"
    f = log["fields"]
    assert f["reader"].startswith("Casey Doe, VP Operations at Acme") and f["channel"] == "email"
    assert f["value_item"] == "A one-page template for the monthly review."
    assert f["proof_purpose"] == "shows operating rigor"
    assert f["not_a_copy"].endswith(": yes") and f["company-facts grep"] == "0 hits"
    assert log["time_box"] == {"signal": '"next week"', "need_expires": "2026-03-08", "research": "3",
                               "design": "2", "review loops": "2", "send_date": "2026-03-05"}
    assert [e["step"] for e in log["entries"]] == [1, 2, 4, 5, 6]
    e = log["entries"][3]
    assert e["duration"] == 1.5 and e["who"] == "claude" and e["date"] == "2026-03-02" and e["time"] == "12:00"
    assert log["ship"]["sent"] == "data/outreach-log.md:2"
    assert log["after_send"]["reply"] == "none by 2026-03-09"
    assert log["drain"]["drained"] == "2026-03-09"


def test_template_is_parsed_as_v2_with_every_field_blank(tmp_path):
    log = bc.parse_log(TEMPLATE)
    assert log["format"] == "v2"
    assert all(bc.is_blank(log["fields"].get(k)) for k in bc.ALL_FIELDS)
    assert log["entries"] == []        # the placeholder heading is not an Entry
    r = bc.check_log(log, TODAY, _repo(tmp_path, {}))
    assert r["flags"] == ["incomplete"] and r["failed_checks"] == []
    assert r["missing_fields"][0].startswith("Variant") and set(bc.ALL_FIELDS) <= set(r["missing_fields"])


def test_old_log_parses_without_raising_and_keeps_its_entries():
    log = bc.parse_log(OLD)
    assert log["format"] == "pre-v2" and log["variant"] == ""
    assert log["entries"][0]["step"] == 0 and log["entries"][0]["duration"] is None


def test_blank_detection():
    for v in ("", "  ", None, "<x>", "YYYY-MM-DD", "nick | claude", "on/off", "pending",
              "tells them: yes/no"):
        assert bc.is_blank(v), v
    for v in ("nick", "data/outreach-log.md:2", "none by 2026-03-09", "D2, D3", "yes",
              "advance to the next round, yes/no, by 2026-11-03"):
        assert not bc.is_blank(v), v


def test_hours_and_dates():
    assert bc._hours("1:30") == 1.5 and bc._hours(" 2h") == 2.0 and bc._hours("0.5") == 0.5
    assert bc._hours("x") is None and bc._hours(None) is None
    assert bc._hours("2026-03-02 evening") is None and bc._hours("about 2 min run") is None
    assert bc._date("by 2026-03-05") == dt.date(2026, 3, 5)
    assert bc._date("2026-13-40") is None and bc._date("") is None


def test_an_entry_heading_without_time_or_step_still_parses():
    e = bc.parse_entry("### 2026-03-01 | red team | who: claude")
    assert e["step"] is None and e["time"] == "" and e["who"] == "claude" and e["duration"] is None
    assert bc.parse_entry("### not an entry") is None
    assert bc.parse_entry("### 2026-03-01 09:00 | Step 2: x | who:")["who"] is None


def test_section_lookup_needs_a_whole_word():
    log = bc.parse_log(FILLED.replace("## Ship", "## Shipping notes"))
    assert log["ship"] == {}


# --- tiers --------------------------------------------------------------------------------

@pytest.mark.parametrize("variant,required", [
    ("V1", True), ("new", True), ("V2", False), ("V3", False)])
def test_not_a_copy_is_required_only_for_v1_and_new(tmp_path, variant, required):
    log = _edit(FILLED, "Variant: V1", f"Variant: {variant}")
    log = _edit(log, "first screen shows it: yes", "first screen shows it: yes/no")
    r = _check(log, _repo(tmp_path, {}))
    assert ("not_a_copy" in r["missing_fields"]) is required
    assert ("incomplete" in r["flags"]) is required


def test_prior_outcomes_is_not_required_for_v3(tmp_path):
    log = _edit(FILLED, "prior_outcomes: V1: sends 2, written replies 0, conversations 0; source: appendix 8.2 row",
                "prior_outcomes: <x>")
    repo = _repo(tmp_path, {})
    assert "prior_outcomes" in _check(_edit(log, "Variant: V1", "Variant: V2"), repo)["missing_fields"]
    assert _check(_edit(log, "Variant: V1", "Variant: V3"), repo)["missing_fields"] == []


def test_an_unknown_variant_is_incomplete_and_uses_every_field(tmp_path):
    log = _edit(FILLED, "Variant: V1", "Variant: V4")
    log = _edit(log, "first screen shows it: yes", "first screen shows it: yes/no")
    r = _check(log, _repo(tmp_path, {}))
    assert r["missing_fields"] == ["Variant (V1, V2, V3 or new)", "not_a_copy"]


def test_proof_purpose_blank_is_incomplete(tmp_path):
    log = _edit(FILLED, "proof_purpose: shows operating rigor", "proof_purpose: <stated separately>")
    assert _check(log, _repo(tmp_path, {}))["missing_fields"] == ["proof_purpose"]


# --- flags --------------------------------------------------------------------------------

UNSENT = _edit(_edit(FILLED, "sent: data/outreach-log.md:2", "sent: data/outreach-log.md:<line>"),
               "drained: 2026-03-09", "drained: YYYY-MM-DD")


def test_expired_fires_past_need_expires_when_unsent(tmp_path):
    repo = _repo(tmp_path, {})
    assert "expired" in _check(UNSENT, repo, dt.date(2026, 3, 9))["flags"]
    assert "expired" not in _check(UNSENT, repo, dt.date(2026, 3, 8))["flags"]
    assert "expired" not in _check(FILLED, repo, dt.date(2026, 3, 9))["flags"]


def test_send_date_after_need_expires_fails(tmp_path):
    log = _edit(FILLED, "send_date 2026-03-05", "send_date 2026-03-09")
    assert "send_date 2026-03-09 is after need_expires 2026-03-08" in _check(log, _repo(tmp_path, {}))["failed_checks"]
    log = _edit(FILLED, "send_date 2026-03-05", "send_date 2026-03-08")
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


SENT_NO_AFTER = _edit(_edit(FILLED, "opened: 2 visits by 2026-03-07", "opened: <N visits | unknown>"),
                      "reply: none by 2026-03-09", "reply: data/networking.md:<line> | none by YYYY-MM-DD")


def test_check_analytics_fires_three_days_after_send_with_no_opened_and_no_reply(tmp_path):
    repo = _repo(tmp_path, {})
    assert "check analytics and Gmail" in _check(SENT_NO_AFTER, repo, dt.date(2026, 3, 8))["flags"]
    assert "check analytics and Gmail" not in _check(SENT_NO_AFTER, repo, dt.date(2026, 3, 7))["flags"]
    opened = _edit(SENT_NO_AFTER, "opened: <N visits | unknown>", "opened: unknown")
    assert "check analytics and Gmail" not in _check(opened, repo, dt.date(2026, 3, 8))["flags"]
    replied = _edit(SENT_NO_AFTER, "reply: data/networking.md:<line> | none by YYYY-MM-DD",
                    "reply: data/networking.md:7")
    assert "check analytics and Gmail" not in _check(replied, repo, dt.date(2026, 3, 8))["flags"]
    unsent = _edit(SENT_NO_AFTER, "sent: data/outreach-log.md:2", "sent: <x>")
    assert "check analytics and Gmail" not in _check(unsent, repo, dt.date(2026, 3, 8))["flags"]


UNDRAINED = _edit(FILLED, "drained: 2026-03-09", "drained: YYYY-MM-DD")


def test_drain_due_fires_on_day_14_or_an_outcome_until_drained(tmp_path):
    repo = _repo(tmp_path, {})
    assert "drain due" in _check(UNDRAINED, repo, dt.date(2026, 3, 19))["flags"]
    assert "drain due" not in _check(UNDRAINED, repo, dt.date(2026, 3, 18))["flags"]
    outcome = _edit(UNDRAINED, "outcome: none by 2026-03-09", "outcome: data/job-pipeline.md:12")
    assert "drain due" in _check(outcome, repo, dt.date(2026, 3, 6))["flags"]
    drained = _edit(outcome, "drained: YYYY-MM-DD", "drained: 2026-03-07")
    assert "drain due" not in _check(drained, repo, dt.date(2026, 3, 30))["flags"]
    no_date = _edit(UNDRAINED, "send_date 2026-03-05", "send_date YYYY-MM-DD")
    assert "drain due" not in bc.check_log(bc.parse_log(no_date), dt.date(2026, 4, 30), repo)["flags"]


@pytest.mark.parametrize("occ,promoted,todo,due", [
    (2, "No", "none", True), (3, "No, parked", "<todo fragment | none>", True),
    (1, "No", "none", False), (2, "Yes", "none", False), (2, "No", "PARKED todo 12", False)])
def test_workflow_edit_due(tmp_path, occ, promoted, todo, due):
    repo = _repo(tmp_path, {}, lessons=LESSONS.format(occ=occ, promoted=promoted))
    log = _edit(FILLED, "ledger_rows: added 0   workflow_edit_todo: none",
                f"ledger_rows: added 0, incremented AR4   workflow_edit_todo: {todo}")
    assert ("workflow edit due" in _check(log, repo)["flags"]) is due


def test_workflow_edit_due_needs_a_matching_artifact_row(tmp_path):
    repo = _repo(tmp_path, {}, lessons=LESSONS.format(occ=5, promoted="No"))
    other = _edit(FILLED, "ledger_rows: added 0", "ledger_rows: added 0, incremented AR40")
    assert "workflow edit due" not in _check(other, repo)["flags"]
    no_ids = _edit(FILLED, "ledger_rows: added 0", "ledger_rows: added 1")
    assert "workflow edit due" not in _check(no_ids, repo)["flags"]


def test_ledger_rows_reads_only_artifact_rows_of_section_2(tmp_path):
    lessons = "## Section 1\n| Pattern | Occurrences |\n|---|---|\n| [artifact] AR1 | 9 |\n" + LESSONS.format(occ=2, promoted="No")
    repo = _repo(tmp_path, {}, lessons=lessons)
    rows = bc.ledger_rows(repo)
    assert len(rows) == 1 and rows[0]["occurrences"] == "2" and rows[0]["promoted"] == "No"
    assert bc.ledger_rows(_repo(tmp_path / "b", {})) == []
    nosec = _repo(tmp_path / "c", {}, lessons="## Section 1\n| Pattern | Occurrences | Promoted |\n"
                                             "| [artifact] AR4 | 2 | No |\nlast line\n")
    assert bc.ledger_rows(nosec) == []


def test_workflow_edit_due_returns_a_real_bool(tmp_path):
    repo = _repo(tmp_path, {}, lessons=LESSONS.format(occ=2, promoted="No"))
    due = _edit(FILLED, "ledger_rows: added 0", "ledger_rows: added 0, incremented AR4")
    assert bc.workflow_edit_due(bc.parse_log(due), repo) is True
    assert bc.workflow_edit_due(bc.parse_log(FILLED), repo) is False
    filed = _edit(due, "workflow_edit_todo: none", "workflow_edit_todo: PARKED todo 12")
    assert bc.workflow_edit_due(bc.parse_log(filed), repo) is False
    other = _edit(FILLED, "ledger_rows: added 0", "ledger_rows: added 0, incremented AR40")
    assert bc.workflow_edit_due(bc.parse_log(other), repo) is False


def test_pointer_ok_returns_a_real_bool(tmp_path):
    repo = _repo(tmp_path, {})
    assert bc._pointer_ok(repo, "data/outreach-log.md:1", "data/outreach-log.md") is True
    assert bc._pointer_ok(repo, "elsewhere", "data/outreach-log.md") is False
    assert bc._pointer_ok(repo, "data/outreach-log.md:4", "data/outreach-log.md") is False


def test_the_script_runs_as_a_file_from_any_directory(tmp_path):
    import subprocess
    repo = _repo(tmp_path, {"acme": OLD})
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "build_check.py"),
                           "--repo-root", str(repo), "--json"], capture_output=True, text=True,
                          cwd=str(tmp_path), timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["logs"] == 1


def test_an_entry_part_that_is_neither_duration_nor_who_is_ignored():
    e = bc.parse_entry("### 2026-03-01 09:00 | Step 2: x | extra note")
    assert e["who"] is None and e["duration"] is None


def test_a_variant_line_below_the_header_does_not_make_a_v2_log():
    text = OLD.replace("## Entries", "## Phase 0\nreader: x\n\n## Entries\nVariant: V2")
    assert bc.parse_log(text)["format"] == "pre-v2"


def test_a_research_entry_before_the_brief_is_fine(tmp_path):
    log = _edit(FILLED, "### 2026-03-01 09:00 | Step 1: open log, save brief | duration 0:30 | who: claude\n- Done: brief saved.\n", "")
    log = _edit(log, "- Done: fetched.\n", "- Done: fetched.\n### 2026-03-01 11:00 | Step 1: open log | duration 0:10 | who: claude\n")
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


def test_an_unfilled_dispositions_placeholder_is_not_read(tmp_path):
    log = _edit(FILLED, "review_dispositions: 3 fixed, 0 signed off", "review_dispositions: <2 open, fill at ship>")
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


# --- failed checks ------------------------------------------------------------------------

def test_the_filled_log_passes_every_check(tmp_path):
    r = _check(FILLED, _repo(tmp_path, {}))
    assert r == {"flags": [], "failed_checks": [], "missing_fields": []}


def test_entry_without_duration_or_who_fails(tmp_path):
    repo = _repo(tmp_path, {})
    log = _edit(FILLED, "| Step 2: sources | duration 2:00 | who: claude", "| Step 2: sources | who: claude")
    assert _check(log, repo)["failed_checks"] == [
        "Entry 2026-03-01 10:00 'Step 2: sources': duration missing or not h:mm"]
    log = _edit(FILLED, "| Step 2: sources | duration 2:00 | who: claude", "| Step 2: sources | duration 2:00")
    assert _check(log, repo)["failed_checks"] == ["Entry 2026-03-01 10:00 'Step 2: sources': who missing"]
    log = _edit(FILLED, "### 2026-03-01 10:00 | Step 2: sources | duration 2:00",
                "### 2026-03-01 | Step 2: sources | duration not timed")
    assert _check(log, repo)["failed_checks"] == [
        "Entry 2026-03-01 'Step 2: sources': duration missing or not h:mm"]


def test_a_date_in_a_budget_slot_is_not_read_as_hours(tmp_path):
    log = _edit(FILLED, "design 2", "design 2026-03-02 evening")
    log = _edit(log, "| Step 5: design | duration 1:30", "| Step 5: design | duration 3000:00")
    assert bc.parse_log(log)["time_box"]["design"] == "2026-03-02 evening"
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


@pytest.mark.parametrize("old,new,msg", [
    ("research 3", "research 1.5", "research hours 2 over the budget of 1.5"),
    ("design 2", "design 1", "design hours 1.5 over the budget of 1"),
    ("review loops 2", "review loops 0", "review loops 1 over the budget of 0"),
])
def test_budgets_are_summed_from_entries(tmp_path, old, new, msg):
    assert msg in _check(_edit(FILLED, old, new), _repo(tmp_path, {}))["failed_checks"]


def test_budgets_at_the_limit_pass(tmp_path):
    log = _edit(_edit(_edit(FILLED, "research 3", "research 2"), "design 2", "design 1:30"),
                "review loops 2", "review loops 1")
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


def test_draft_before_brief_fails(tmp_path):
    msg = "no Step 1 (brief) Entry before the first Step 4 (draft) Entry"
    no_brief = _edit(FILLED, "| Step 1: open log, save brief |", "| Step 3: red team |")
    assert msg in _check(no_brief, _repo(tmp_path, {}))["failed_checks"]
    late = FILLED.replace("### 2026-03-01 09:00 | Step 1: open log, save brief | duration 0:30 | who: claude\n- Done: brief saved.\n", "")
    late = late.replace("## Ship", "### 2026-03-04 09:00 | Step 1: brief | duration 0:10 | who: nick\n\n## Ship")
    assert msg in _check(late, _repo(tmp_path, {}))["failed_checks"]


def test_edit_after_the_last_review_fails(tmp_path):
    msg = "no Step 6 (review) Entry after the last edit Entry"
    log = FILLED.replace("## Ship", "### 2026-03-04 09:00 | Step 4: late edit | duration 0:10 | who: nick\n\n## Ship")
    assert msg in _check(log, _repo(tmp_path, {}))["failed_checks"]
    no_review = FILLED.replace("### 2026-03-03 10:00 | Step 6: reader review | duration 0:30 | who: claude\n", "")
    assert msg in _check(no_review, _repo(tmp_path, {}))["failed_checks"]
    unsent_unreviewed = _edit(no_review, "sent: data/outreach-log.md:2", "sent: <x>")
    assert msg not in _check(unsent_unreviewed, _repo(tmp_path, {}))["failed_checks"]


def test_p0_dispositions(tmp_path):
    repo = _repo(tmp_path, {})
    still_open = _edit(FILLED, "3 fixed, 0 signed off", "3 fixed, 1 open")
    assert "1 P0 still open" in _check(still_open, repo)["failed_checks"]
    zero_open = _edit(FILLED, "3 fixed, 0 signed off", "3 fixed, 0 open")
    assert _check(zero_open, repo)["failed_checks"] == []
    unsigned = _edit(FILLED, "3 fixed, 0 signed off", "3 fixed, 1 signed off")
    assert "a signed-off P0 has no 'nick YYYY-MM-DD' line" in _check(unsigned, repo)["failed_checks"]
    signed = _edit(FILLED, "3 fixed, 0 signed off", "3 fixed, 1 signed off: nick 2026-03-04")
    assert _check(signed, repo)["failed_checks"] == []


def test_recipient_must_equal_reader_once_sent(tmp_path):
    repo = _repo(tmp_path, {})
    log = _edit(FILLED, "recipient_equals_reader: yes", "recipient_equals_reader: no")
    assert "sent, but recipient_equals_reader is not yes" in _check(log, repo)["failed_checks"]
    unsent = _edit(log, "sent: data/outreach-log.md:2", "sent: <x>")
    assert _check(unsent, repo)["failed_checks"] == []


@pytest.mark.parametrize("value", ["data/outreach-log.md:9", "data/outreach-log.md:0",
                                   "Sent to Casey Doe 3/5", "data/other.md:2"])
def test_sent_must_point_at_a_real_outreach_log_line(tmp_path, value):
    log = _edit(FILLED, "sent: data/outreach-log.md:2", f"sent: {value}")
    assert any(c.startswith("sent: ") for c in _check(log, _repo(tmp_path, {}))["failed_checks"])


def test_sent_pointer_at_the_last_line_passes(tmp_path):
    log = _edit(FILLED, "sent: data/outreach-log.md:2", "sent: data/outreach-log.md:3")
    assert _check(log, _repo(tmp_path, {}))["failed_checks"] == []


def test_defaults_applied(tmp_path):
    repo = _repo(tmp_path, {})
    for v, applied in (("D1 D6", ["D1", "D6"]), ("none", []), ("<D ids>", [])):
        log = bc.parse_log(_edit(FILLED, "defaults_applied: D2, D3", f"defaults_applied: {v}"))
        assert bc.defaults_applied(log) == applied
        assert bc.check_log(log, TODAY, repo)["failed_checks"] == []
    bad = _edit(FILLED, "defaults_applied: D2, D3", "defaults_applied: D2, D7, X")
    assert "defaults_applied names unknown IDs: D7, X" in _check(bad, repo)["failed_checks"]


# --- the brief line -------------------------------------------------------------------------

def test_brief_line_lists_open_builds_only(tmp_path):
    repo = _repo(tmp_path, {"acme": FILLED, "activeco": UNDRAINED})
    out = bc.report(repo, dt.date(2026, 3, 19))
    assert out["line"] == ("Builds: 1 open · activeco (V1, step 6, sent 14d ago, "
                           "opened: 2 visits by 2026-03-07, flags: drain due, failed checks: 0)")
    unsent = bc.report(_repo(tmp_path / "b", {"acme": OLD}), TODAY)
    assert unsent["line"] == ("Builds: 1 open · acme (no variant, step 0, not sent, "
                              "opened: unknown, flags: incomplete, failed checks: 0)")


def test_summary_fields(tmp_path):
    repo = _repo(tmp_path, {"acme": SENT_NO_AFTER})
    b = bc.summarize(repo / "output" / "acme" / "BUILD-LOG.md", repo, TODAY)
    assert b["sent"] is True and b["sent_days_ago"] == 5 and b["opened"] == "" and b["drained"] is True
    assert b["step"] == 6 and b["variant"] == "V1" and b["defaults_applied"] == ["D2", "D3"]


# --- ask_state: the one reader_ask / waiver reader (the build hook imports it) ---

@pytest.mark.parametrize("text, state", [
    ('reader_ask: "Can you show me the intake?" (sources/jd.md:3)\n', "filled"),
    ('reader_ask: "<verbatim>" (<source>)\n', "missing"),
    ('reader_ask: "" (x)\n', "missing"),
    ("waived: nick 2026-10-26 (a page for family)\n", "waived"),
    ("waived: nick\n", "missing"),
    ('reader_ask: "x y" (s)\nwaived: nick 2026-10-26 (r)\n', "waived"),
    ("# old log\nno fields here\n", "missing"),
])
def test_ask_state(text, state):
    assert bc.ask_state(text) == state


def test_a_waived_log_runs_no_other_check(tmp_path):
    log = bc.parse_log(OLD + "\nwaived: nick 2026-10-26 (not for a reader)\n")
    r = bc.check_log(log, TODAY, _repo(tmp_path, {}))
    assert r == {"flags": ["waived"], "failed_checks": [], "missing_fields": []}


def test_a_filled_reader_ask_does_not_waive_an_old_log(tmp_path):
    log = bc.parse_log(OLD + '\nreader_ask: "the intake" (s)\n')
    assert bc.check_log(log, TODAY, _repo(tmp_path, {}))["flags"] == ["incomplete"]


# --- review evidence, pointer target, the brief line (codex and grok review, 2026-10-26) --

def test_a_sent_log_with_no_reader_review_line_fails(tmp_path):
    log = _edit(FILLED, "reader_review: output/analysis/030926-codex-acme-reader-review.md   entry: 2026-03-09 11:00\n", "")
    assert any("no reader_review report" in c for c in _check(log, _repo(tmp_path, {}))["failed_checks"])


def test_a_review_that_wrote_no_report_fails(tmp_path):
    log = _edit(FILLED, "output/analysis/030926-codex-acme-reader-review.md", "no report written")
    assert any("no reader_review report" in c for c in _check(log, _repo(tmp_path, {}))["failed_checks"])


def test_a_pointer_to_a_row_for_someone_else_fails(tmp_path):
    repo = _repo(tmp_path, {})
    (repo / "data" / "outreach-log.md").write_text(
        "| 2026-03-01 | x | email | Casey Doe | Acme | a | Sent | |\n"
        "| 2026-03-02 | x | email | Jordan Roe | Beta | b | Sent | |\n", encoding="utf-8")
    assert any("does not name the reader" in c for c in _check(FILLED, repo)["failed_checks"])


def test_a_pointer_to_a_header_line_fails(tmp_path):
    repo = _repo(tmp_path, {})
    (repo / "data" / "outreach-log.md").write_text(
        "| 2026-03-01 | x | email | Casey Doe | Acme | a | Sent | |\n"
        "| Date | Skill | Channel | Recipient Casey | Company | S | Status | Artifact |\n",
        encoding="utf-8")
    assert any("does not name the reader" in c for c in _check(FILLED, repo)["failed_checks"])


def test_the_brief_line_counts_failed_checks():
    b = {"slug": "acme", "variant": "V1", "step": 6, "sent_days_ago": None, "opened": "",
         "flags": [], "failed_checks": ["x", "y"], "drained": False}
    assert "failed checks: 2" in bc.brief_line([b], "")

