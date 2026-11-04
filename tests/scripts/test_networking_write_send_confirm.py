"""A drafted message counts as a send only once Nick confirms he sent it (8.F1, 4.F3, 5.F3).

THE DEFECT (review round 4, 2026-11-03). /follow-up Step 9 logged its entry as
`(outbound)` when the draft was approved, before anything was sent. That entry closed the
contact's open reply, so a second /follow-up run that got a Yes about the reply already
logged wrote it again with no refusal, and day_check counted two replies (8.F1). /draft-email
logged plain `email` for good, so its real sends never closed a reply, and the contact's next
genuine reply was refused as a possible duplicate (4.F3, 5.F3).

NICK'S DECISION (2026-11-04, option a). The drafting skills log the entry PLAIN at draft
time, linked to the draft's capture (`networking_write.py log --direction none --capture
<id>`). `draft_capture.py confirm <id> unchanged|edited` marks that entry outbound through
`networking_write.py mark-sent`; `not-sent` leaves it plain. A confirm that cannot find the
one entry it should mark fails and records nothing.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import write_fixture, TOOLS_DIR, REPO_ROOT

sys.path.insert(0, str(TOOLS_DIR))
import draft_capture as dc  # noqa: E402
import reply_classify as rc  # noqa: E402

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jane Doe — Acme Corp

"""

OUTREACH = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-18 | follow-up | email | Jane Doe | Acme Corp | Re: intro | Sent |
"""

DRAFT = "TO: jane@example.com\nSUBJECT: Re: intro\nBODY:\nHi Jane,\n\nThanks for the note.\n\nNick\n"
SUMMARY = "Follow-up #1 — thanks for the reply"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Captures and lessons in a throwaway place; the subprocess inherits the env."""
    d = tmp_path / "captures"
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(d))
    monkeypatch.setenv("LESSONS_PATH", str(tmp_path / "lessons.md"))
    write_fixture(tmp_path, "data/networking.md", NETWORKING)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH)
    return d


def nw(tmp_path, *args):
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
         *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    try:
        return json.loads(r.stdout), r.returncode
    except ValueError:
        return {"status": "error", "code": "no_json", "stderr": r.stderr}, r.returncode or 1


def log_reply(tmp_path, summary="Jane replied", date="2026-06-19", *extra):
    return nw(tmp_path, "log", "Jane Doe", "--date", date, "--type", "email",
              "--summary", summary, "--direction", "in", "--no-todo", *extra)


def log_draft(tmp_path, cid, direction="none", summary=SUMMARY, date="2026-06-20"):
    return nw(tmp_path, "log", "Jane Doe", "--date", date, "--type", "email",
              "--summary", summary, "--direction", direction, "--capture", cid, "--no-todo")


def text(tmp_path):
    return (tmp_path / "data/networking.md").read_text(encoding="utf-8")


def headers(tmp_path):
    return [ln for ln in text(tmp_path).splitlines() if ln.startswith("#### ")]


def open_reply(tmp_path):
    return rc.open_reply(rc.contact_lines(text(tmp_path).splitlines(), "Jane Doe"))


def drafted(tmp_path):
    """A reply is logged, then the follow-up draft opens and Step 9 logs it."""
    r, c = log_reply(tmp_path)
    assert c == 0, r
    cid = dc.capture(DRAFT, skill="follow-up")
    r, c = log_draft(tmp_path, cid)
    assert c == 0, r
    return cid


# --- draft time: plain, linked, and the reply stays open ------------------------------

def test_a_draft_time_log_is_plain_and_leaves_the_reply_open(tmp_path):
    cid = drafted(tmp_path)
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email | {SUMMARY}"
    assert open_reply(tmp_path)[2] == "Jane replied"
    link = dc.read_meta(cid)["networking"]
    assert {k: link[k] for k in ("name", "date", "type", "summary")} == {
        "name": "Jane Doe", "date": "2026-06-20", "type": "email", "summary": SUMMARY}


def test_the_8f1_double_log_is_refused_after_a_draft(tmp_path):
    """The second /follow-up run gets a Yes about the reply already logged."""
    drafted(tmp_path)
    before = text(tmp_path)
    r, c = log_reply(tmp_path, "Jane replied (again)", "2026-06-21")
    assert c != 0 and r["code"] == "possible_duplicate_reply", r
    assert r["existing"]["summary"] == "Jane replied"
    assert text(tmp_path) == before


@pytest.mark.parametrize("direction", ["in", "out", "both"])
def test_capture_takes_only_a_plain_log(tmp_path, direction):
    cid = dc.capture(DRAFT)
    before = text(tmp_path)
    r, c = log_draft(tmp_path, cid, direction=direction)
    assert c != 0 and r["code"] == "capture_needs_plain", r
    assert text(tmp_path) == before and "networking" not in dc.read_meta(cid)


def test_an_unknown_capture_is_refused_and_writes_nothing(tmp_path):
    before = text(tmp_path)
    r, c = log_draft(tmp_path, "20990101-000000-abcdef")
    assert c != 0 and r["code"] == "capture_not_found", r
    assert text(tmp_path) == before


def test_a_capture_is_linked_to_one_entry_only(tmp_path):
    cid = drafted(tmp_path)
    before = text(tmp_path)
    r, c = log_draft(tmp_path, cid, summary="the same draft logged again")
    assert c != 0 and r["code"] == "capture_already_linked", r
    assert text(tmp_path) == before
    assert dc.read_meta(cid)["networking"]["summary"] == SUMMARY


def test_a_not_sent_capture_cannot_be_logged(tmp_path):
    cid = dc.capture(DRAFT)
    dc.confirm(cid, "not-sent")
    before = text(tmp_path)
    r, c = log_draft(tmp_path, cid)
    assert c != 0 and r["code"] == "capture_not_sent", r
    assert text(tmp_path) == before


@pytest.mark.parametrize("outcome", ["unchanged", "edited"])
def test_a_capture_confirmed_sent_before_the_log_is_logged_outbound(tmp_path, outcome):
    """The deferred order (/draft-email multi-round edits): confirm first, then log."""
    cid = dc.capture(DRAFT)
    kw = ({"sent_text": DRAFT.replace("Thanks", "Many thanks"), "no_lesson": "typo only"}
          if outcome == "edited" else {})
    dc.confirm(cid, outcome, **kw)
    r, c = log_draft(tmp_path, cid)
    assert c == 0, r
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email (outbound) | {SUMMARY}"


def test_dry_run_checks_the_capture_and_writes_nothing(tmp_path):
    cid = dc.capture(DRAFT)
    before = text(tmp_path)
    r, c = nw(tmp_path, "--dry-run", "log", "Jane Doe", "--date", "2026-06-20", "--type",
              "email", "--summary", SUMMARY, "--direction", "none", "--capture", "nope-id")
    assert c != 0 and r["code"] == "capture_not_found", r
    r, c = nw(tmp_path, "--dry-run", "log", "Jane Doe", "--date", "2026-06-20", "--type",
              "email", "--summary", SUMMARY, "--direction", "none", "--capture", cid)
    assert c == 0, r
    assert text(tmp_path) == before and "networking" not in dc.read_meta(cid)


# --- confirm: sent marks it outbound, not-sent leaves it plain ------------------------

@pytest.mark.parametrize("outcome", ["unchanged", "edited"])
def test_confirming_a_send_marks_the_entry_outbound_and_closes_the_reply(tmp_path, outcome):
    cid = drafted(tmp_path)
    kw = ({"sent_text": DRAFT.replace("Thanks", "Many thanks"), "no_lesson": "typo only"}
          if outcome == "edited" else {})
    meta = dc.confirm(cid, outcome, **kw)
    assert meta["status"] == outcome
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email (outbound) | {SUMMARY}"
    assert open_reply(tmp_path) is None
    r, c = log_reply(tmp_path, "Jane answered the follow-up", "2026-06-22")
    assert c == 0, r


def test_not_sent_leaves_the_entry_plain(tmp_path):
    cid = drafted(tmp_path)
    before = text(tmp_path)
    assert dc.confirm(cid, "not-sent")["status"] == "not-sent"
    assert text(tmp_path) == before
    assert open_reply(tmp_path)[2] == "Jane replied"


def test_a_forced_not_sent_after_a_send_puts_the_entry_back_to_plain(tmp_path):
    cid = drafted(tmp_path)
    dc.confirm(cid, "unchanged")
    dc.confirm(cid, "not-sent", force=True)
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email | {SUMMARY}"
    assert open_reply(tmp_path)[2] == "Jane replied"


def test_a_forced_reconfirm_of_a_send_keeps_one_outbound_suffix(tmp_path):
    cid = drafted(tmp_path)
    dc.confirm(cid, "unchanged")
    dc.confirm(cid, "edited", sent_text=DRAFT.replace("Thanks", "Many thanks"),
               no_lesson="typo only", force=True)
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email (outbound) | {SUMMARY}"


def test_a_capture_with_no_link_confirms_as_before(tmp_path):
    cid = dc.capture(DRAFT)
    before = text(tmp_path)
    assert dc.confirm(cid, "unchanged")["status"] == "unchanged"
    assert text(tmp_path) == before


# --- confirm fails loudly when it cannot find the one entry ---------------------------

def test_confirm_errors_when_the_linked_entry_is_gone(tmp_path):
    cid = drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path).replace(f"| email | {SUMMARY}", "| email | renamed by hand"),
                 encoding="utf-8")
    with pytest.raises(dc.CaptureError, match="entry_not_found"):
        dc.confirm(cid, "unchanged")
    assert dc.read_meta(cid)["status"] == "opened"


def test_confirm_errors_when_the_linked_entry_is_ambiguous(tmp_path):
    cid = drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path) + f"#### 2026-06-20 | email | {SUMMARY}\n\n", encoding="utf-8")
    before = text(tmp_path)
    with pytest.raises(dc.CaptureError, match="ambiguous_entry"):
        dc.confirm(cid, "unchanged")
    assert dc.read_meta(cid)["status"] == "opened" and text(tmp_path) == before


def test_confirm_errors_when_the_entry_now_records_a_reply(tmp_path):
    cid = drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path).replace(f"| email | {SUMMARY}", f"| email (inbound) | {SUMMARY}"),
                 encoding="utf-8")
    with pytest.raises(dc.CaptureError, match="direction_conflict"):
        dc.confirm(cid, "unchanged")
    assert dc.read_meta(cid)["status"] == "opened"


def test_the_cli_confirm_reports_the_failure(tmp_path, capsys):
    cid = drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path).replace(f"| email | {SUMMARY}", "| email | gone"), encoding="utf-8")
    assert dc.main(["confirm", cid, "unchanged"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


# --- mark-sent, the one writer of the change -----------------------------------------

def mark(tmp_path, *extra, summary=SUMMARY, date="2026-06-20", kind="email", name="Jane Doe"):
    return nw(tmp_path, "mark-sent", name, "--date", date, "--type", kind,
              "--summary", summary, *extra)


def test_mark_sent_changes_only_the_matching_header(tmp_path):
    drafted(tmp_path)
    before = text(tmp_path)
    r, c = mark(tmp_path)
    assert c == 0 and r["changed"] is True, r
    after = text(tmp_path)
    assert after == before.replace(f"#### 2026-06-20 | email | {SUMMARY}",
                                   f"#### 2026-06-20 | email (outbound) | {SUMMARY}")


def test_mark_sent_is_idempotent_and_undo_reverts(tmp_path):
    drafted(tmp_path)
    mark(tmp_path)
    once = text(tmp_path)
    r, c = mark(tmp_path)
    assert c == 0 and r["changed"] is False and text(tmp_path) == once, r
    r, c = mark(tmp_path, "--undo")
    assert c == 0 and r["changed"] is True, r
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email | {SUMMARY}"
    r, c = mark(tmp_path, "--undo")
    assert c == 0 and r["changed"] is False, r


@pytest.mark.parametrize("field", ["date", "type", "summary", "name"])
def test_mark_sent_matches_every_field(tmp_path, field):
    drafted(tmp_path)
    before = text(tmp_path)
    wrong = {"date": "2026-06-21", "kind": "linkedin", "summary": SUMMARY + " x",
             "name": "Sam Example"}
    key = "kind" if field == "type" else field
    r, c = mark(tmp_path, **{key: wrong[key]})
    assert c != 0 and r["code"] == "entry_not_found", r
    assert text(tmp_path) == before


def test_mark_sent_ignores_another_contacts_identical_entry(tmp_path):
    drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path) + f"### Sam Example\n\n#### 2026-06-20 | email | {SUMMARY}\n\n",
                 encoding="utf-8")
    r, c = mark(tmp_path)
    assert c == 0, r
    assert headers(tmp_path)[-1] == f"#### 2026-06-20 | email | {SUMMARY}"


def test_mark_sent_finds_the_entry_in_an_archived_section(tmp_path):
    p = tmp_path / "data/networking.md"
    p.write_text(NETWORKING + f"### [ARCHIVED] Jane Doe — Acme Corp\n\n"
                 f"#### 2026-06-20 | email | {SUMMARY}\n\n", encoding="utf-8")
    r, c = mark(tmp_path)
    assert c == 0 and r["changed"] is True, r
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email (outbound) | {SUMMARY}"


def test_undo_refuses_an_entry_that_records_a_reply(tmp_path):
    p = tmp_path / "data/networking.md"
    p.write_text(NETWORKING + f"#### 2026-06-20 | email (two-way) | {SUMMARY}\n\n", encoding="utf-8")
    before = text(tmp_path)
    r, c = mark(tmp_path, "--undo")
    assert c != 0 and r["code"] == "direction_conflict", r
    assert text(tmp_path) == before


def test_mark_sent_on_a_missing_file_errors(tmp_path):
    (tmp_path / "data/networking.md").unlink()
    r, c = mark(tmp_path)
    assert c != 0 and r["status"] == "error", r


# --- review of the mutation run (2026-11-04) --------------------------------------

def test_link_networking_refuses_a_second_link_directly(tmp_path):
    """The CLI checks first, but the function itself must also refuse: one entry per draft."""
    cid = dc.capture(DRAFT)
    dc.link_networking(cid, {"name": "Jane Doe", "date": "2026-06-20", "type": "email",
                             "summary": "first", "repo_root": str(tmp_path)})
    with pytest.raises(dc.CaptureError, match="already linked"):
        dc.link_networking(cid, {"name": "Jane Doe", "date": "2026-06-20", "type": "email",
                                 "summary": "second", "repo_root": str(tmp_path)})
    assert dc.read_meta(cid)["networking"]["summary"] == "first"


def test_not_sent_on_a_never_sent_capture_does_not_need_the_entry(tmp_path):
    """Nothing was marked, so nothing needs undoing: a missing entry is no error here."""
    cid = drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    p.write_text(text(tmp_path).replace(f"| email | {SUMMARY}", "| email | gone"), encoding="utf-8")
    assert dc.confirm(cid, "not-sent")["status"] == "not-sent"


def test_a_link_that_cannot_be_written_is_reported(tmp_path, store):
    cid = dc.capture(DRAFT)
    d = store / cid
    d.chmod(0o500)
    try:
        r, c = log_draft(tmp_path, cid)
    finally:
        d.chmod(0o700)
    assert c != 0 and r["code"] == "link_failed", r
    assert headers(tmp_path)[0] == f"#### 2026-06-20 | email | {SUMMARY}"
    assert "networking" not in dc.read_meta(cid)


def test_mark_sent_dry_run_reports_and_writes_nothing(tmp_path):
    drafted(tmp_path)
    before = text(tmp_path)
    r, c = nw(tmp_path, "--dry-run", "mark-sent", "Jane Doe", "--date", "2026-06-20",
              "--type", "email", "--summary", SUMMARY)
    assert c == 0 and r["changed"] is True and r["dry_run"] is True, r
    assert r["type"] == "email (outbound)"
    assert text(tmp_path) == before


def test_mark_sent_with_nothing_to_change_leaves_the_file_untouched(tmp_path):
    drafted(tmp_path)
    p = tmp_path / "data/networking.md"
    mark(tmp_path)
    p.write_text(text(tmp_path).replace("\n", "\r\n"), encoding="utf-8")
    raw = p.read_bytes()
    r, c = mark(tmp_path)
    assert c == 0 and r["changed"] is False, r
    assert p.read_bytes() == raw
