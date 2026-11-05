"""Tests for tools/eic_review.py, its opening step in tools/draft_capture.py, and the
tools/check_eic_review.py gate.

WHAT THIS PROTECTS. A post sent to the EIC for review must not be recorded as published until
her reply has been fetched and her edits logged. That needs: a sent email to an EIC address
opens a review (and nothing else does); a review closes only with a real Gmail id plus an
EIC-tagged lessons row or a stated no-edits reason; and the gate blocks the published record,
by every write path it claims, exactly while a review is open.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import check_eic_review as gate  # noqa: E402
import draft_capture as dc  # noqa: E402
import eic_review as er  # noqa: E402
from eic_reply_stub import her_reply  # noqa: E402

EIC = "editor@example.com"
TAG = "EDITOR NOTE"
REPLY = "0123456789abcdef"
PUBLISHED = "output/linkedin-profile/voice-sources/103026-linkedin-post-published.md"
TODAY = time.strftime("%Y-%m-%d")


def draft(to: str) -> str:
    return f"TO: {to}\nSUBJECT: LinkedIn post for review\nBODY:\nHi,\n\nCan you edit this post?\n\nNick\n"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Throwaway config, review store, capture store and lessons log for every test."""
    cfg = tmp_path / "eic.txt"
    cfg.write_text(f"# the EIC\n{EIC}\ntag: {TAG}\n", encoding="utf-8")
    monkeypatch.setenv("EIC_REVIEWERS_PATH", str(cfg))
    monkeypatch.setenv("EIC_REVIEW_DIR", str(tmp_path / "reviews"))
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(tmp_path / "captures"))
    lessons = tmp_path / "lessons.md"
    lessons.write_text(
        "| Pattern | Rule | Occ | Promoted | Date |\n|---|---|---|---|---|\n"
        f"| **{TAG}.** She reordered the hook. | Lead with the shared thing. | 1 | No | {TODAY} |\n"
        f"| **NICK EDIT.** He linked a word. | Link a word, not a URL. | 1 | No | {TODAY} |\n",
        encoding="utf-8")
    monkeypatch.setenv("LESSONS_PATH", str(lessons))
    # close() looks her reply and its thread up in Gmail (tested in
    # test_eic_review_hardening.py); here it is always her reply in the thread of the post
    # just sent. One second after, not an hour: the lessons row must be dated on or after her
    # reply's day, and an hour crosses midnight after 23:00 (Fable r5 F1).
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply())
    return tmp_path


def sent_to(to: str, outcome: str = "unchanged") -> dict:
    cid = dc.capture(draft(to), skill="draft-email")
    return dc.confirm(cid, outcome, sent_text=draft(to) if outcome == "edited" else None,
                      no_lesson="test" if outcome == "edited" else None)


# --- opening ---------------------------------------------------------------------

def test_a_sent_email_to_the_eic_opens_exactly_one_review():
    meta = sent_to(EIC)
    assert meta["eic_review"] == "opened"
    rows = er.reviews()
    assert [(r["capture_id"], r["status"], r["subject"]) for r in rows] == [
        (meta["id"], "open", "LinkedIn post for review")]
    assert [r["capture_id"] for r in er.open_reviews()] == [meta["id"]]


def test_the_eic_is_matched_inside_a_display_name_and_case_insensitively():
    meta = sent_to(f"Someone <{EIC.upper()}>, other@example.com")
    assert meta.get("eic_review") == "opened"


def test_an_email_to_anyone_else_opens_nothing():
    meta = sent_to("casey@example.com")
    assert "eic_review" not in meta and er.reviews() == []


def test_a_draft_that_was_not_sent_opens_nothing():
    cid = dc.capture(draft(EIC))
    meta = dc.confirm(cid, "not-sent")
    assert "eic_review" not in meta and er.reviews() == []


def test_an_edited_send_to_the_eic_also_opens_one():
    assert sent_to(EIC, outcome="edited")["eic_review"] == "opened"


def test_no_config_file_means_no_eic_and_nothing_opens(env, monkeypatch):
    monkeypatch.setenv("EIC_REVIEWERS_PATH", str(env / "missing.txt"))
    assert "eic_review" not in sent_to(EIC) and er.reviews() == []


def test_reopening_never_erases_a_close():
    meta = sent_to(EIC)
    er.close(meta["id"], REPLY, no_edits="she approved it as is")
    assert not er.open_for(dc.read_meta(meta["id"]))
    assert er.read(meta["id"])["status"] == "closed"


def test_config_reads_comments_and_tag_and_defaults_the_tag(env):
    (env / "eic.txt").write_text(f"\n# a comment line\n\n{EIC}  # trailing comment\n   \n",
                                 encoding="utf-8")
    assert er.load_config() == ({EIC}, "EIC EDIT", "LinkedIn post")


# --- closing ---------------------------------------------------------------------

def test_close_with_a_tagged_lessons_row_records_the_reply_and_the_row():
    cid = sent_to(EIC)["id"]
    rec = er.close(cid, REPLY, lesson_ref="She reordered the hook")
    assert (rec["status"], rec["reply_id"], rec["lesson_ref"]) == (
        "closed", REPLY, "She reordered the hook")
    assert er.open_reviews() == []


@pytest.mark.parametrize("reply", ["", "not-an-id", "0123456789ABCDEF", "0123456789abcde", "0123456789abcdefx"])
def test_close_refuses_anything_but_a_gmail_message_id(reply):
    cid = sent_to(EIC)["id"]
    with pytest.raises(er.ReviewError, match="Gmail message id"):
        er.close(cid, reply, no_edits="none")
    assert er.read(cid)["status"] == "open"


def test_close_refuses_a_row_without_the_eic_tag():
    cid = sent_to(EIC)["id"]
    with pytest.raises(er.ReviewError, match="tagged"):
        er.close(cid, REPLY, lesson_ref="He linked a word")
    assert er.read(cid)["status"] == "open"


def test_close_refuses_text_that_is_only_outside_a_table_row(env):
    (env / "lessons.md").write_text(f"{TAG}: she reordered the hook (prose, not a row)\n",
                                    encoding="utf-8")
    cid = sent_to(EIC)["id"]
    with pytest.raises(er.ReviewError):
        er.close(cid, REPLY, lesson_ref="she reordered the hook")


def test_close_needs_exactly_one_of_lesson_or_no_edits():
    cid = sent_to(EIC)["id"]
    with pytest.raises(er.ReviewError, match="exactly one"):
        er.close(cid, REPLY)
    with pytest.raises(er.ReviewError, match="exactly one"):
        er.close(cid, REPLY, lesson_ref="She reordered the hook", no_edits="x")
    with pytest.raises(er.ReviewError, match="reason"):
        er.close(cid, REPLY, no_edits="   ")
    with pytest.raises(er.ReviewError, match="at least"):
        er.close(cid, REPLY, lesson_ref="hook")


def test_close_refuses_twice_and_an_unknown_capture():
    cid = sent_to(EIC)["id"]
    er.close(cid, REPLY, no_edits="approved as is")
    with pytest.raises(er.ReviewError, match="already closed"):
        er.close(cid, REPLY, no_edits="again")
    with pytest.raises(er.ReviewError, match="no EIC review"):
        er.close("19990101-000000-abcdef", REPLY, no_edits="x")


def test_an_unreadable_record_counts_as_open(env):
    (env / "reviews").mkdir()
    (env / "reviews" / "broken.json").write_text("{not json", encoding="utf-8")
    assert [r["capture_id"] for r in er.open_reviews()] == ["broken"]


# --- the CLI ---------------------------------------------------------------------

def test_cli_list_and_close_report_json(capsys):
    cid = sent_to(EIC)["id"]
    run = lambda *a: subprocess.run([sys.executable, str(TOOLS / "eic_review.py"), *a],
                                    capture_output=True, text=True, env=os.environ.copy())
    closed_first = sent_to(EIC)["id"]
    er.close(closed_first, REPLY, no_edits="approved as is")
    out = run("list")
    listed = json.loads(out.stdout)
    assert listed["open"] == 1
    assert [r["capture_id"] for r in listed["reviews"]] == [cid, closed_first]
    assert "population 2" in out.stderr and "included 1" in out.stderr and "closed 1" in out.stderr
    bad = run("close", cid, "--reply-id", "nope", "--no-edits", "x")
    assert bad.returncode == 1 and json.loads(bad.stdout)["status"] == "error"
    # In-process, so the Gmail lookup is the fixture's stub and the test never calls Gmail.
    # A second reply: one reply id closes one review (Fable r9 49.F1).
    assert er.main(["close", cid, "--reply-id", "fedcba9876543210", "--lesson",
                    "She reordered the hook"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "closed"


def test_cli_open_backfills_a_confirmed_capture_and_refuses_others():
    run = lambda *a: subprocess.run([sys.executable, str(TOOLS / "eic_review.py"), *a],
                                    capture_output=True, text=True, env=os.environ.copy())
    cid = dc.capture(draft(EIC))
    dc.confirm(cid, "unchanged")
    for path in (Path(os.environ["EIC_REVIEW_DIR"])).glob("*.json"):
        path.unlink()
    opened = json.loads(run("open", cid).stdout)
    assert opened["opened"] is True and opened["status"] == "open"
    other = dc.capture(draft("casey@example.com"))
    dc.confirm(other, "unchanged")
    refused = json.loads(run("open", other).stdout)
    assert refused["status"] == "error" and "not a sent email to an EIC address" in refused["message"]


def test_a_failed_review_open_is_reported_and_the_send_is_still_recorded(monkeypatch):
    def broken(meta, now=None):
        raise OSError("disk full")
    monkeypatch.setattr(dc.eic_review, "open_for", broken)
    cid = dc.capture(draft(EIC))
    meta = dc.confirm(cid, "unchanged")
    assert meta["eic_review"] == "ERROR: could not update the EIC review: disk full"
    assert meta["status"] == "unchanged" and dc.read_meta(cid)["status"] == "unchanged"
