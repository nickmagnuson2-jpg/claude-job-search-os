"""Tests for the 2026-11-01 hardening of the EIC review gate (fixes B-G and J).

Each test names the fix it covers. The fixes and why they exist:
  B  close checks her reply in Gmail: from an EIC address, inbound, dated after the review
     opened. Before, any 16 hex characters closed a review.
  C  the gate matches a published-post record anywhere under output/, not only voice-sources/.
  D  the lessons row used to close must be dated on or after the day the review opened.
  E  re-confirming a capture as not sent cancels its review.
  F  only an email whose subject carries the configured filter (default "LinkedIn post")
     opens a review, so personal mail to the same address does not block publishing.
  G  a malformed record or an unreadable store blocks instead of crashing the hook (a crash
     exits 1, which Claude Code treats as allow).
  J  a relative write target is resolved against the shell's working directory: a `cd`
     earlier in the command, or the session cwd the payload carries. Shared by
     check_eic_review and check_handoff_producer (check_public_pii already tracked it).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import check_eic_review as gate  # noqa: E402
import check_handoff_producer as handoff  # noqa: E402
import draft_capture as dc  # noqa: E402
import eic_review as er  # noqa: E402
from eic_reply_stub import her_reply  # noqa: E402

EIC = "editor@example.com"
TAG = "EDITOR NOTE"
REPLY = "0123456789abcdef"
SUBJECT = "LinkedIn post for review"
VOICE = "output/linkedin-profile/voice-sources"
PUBLISHED = f"{VOICE}/110126-linkedin-post-published.md"
TODAY = time.strftime("%Y-%m-%d")


def draft(to: str, subject: str = SUBJECT) -> str:
    return f"TO: {to}\nSUBJECT: {subject}\nBODY:\nHi,\n\nCan you edit this post?\n\nNick\n"


def good_reply(reply_id: str) -> dict:
    """Her reply in the thread of the post sent just now (eic_reply_stub.her_reply)."""
    return her_reply()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "eic.txt"
    cfg.write_text(f"{EIC}\ntag: {TAG}\n", encoding="utf-8")
    monkeypatch.setenv("EIC_REVIEWERS_PATH", str(cfg))
    monkeypatch.setenv("EIC_REVIEW_DIR", str(tmp_path / "reviews"))
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(tmp_path / "captures"))
    lessons = tmp_path / "lessons.md"
    lessons.write_text(
        "| Pattern | Rule | Occ | Promoted | Date |\n|---|---|---|---|---|\n"
        f"| **{TAG}.** She cut the second hook. | One hook. | 1 | No | {TODAY} |\n"
        f"| **{TAG}.** She reordered the opener. | Lead with the shared thing. | 1 | No | 2026-01-05 |\n",
        encoding="utf-8")
    monkeypatch.setenv("LESSONS_PATH", str(lessons))
    # raising=False: the seam does not exist before fix B, and these tests must fail on
    # behaviour, not on a missing attribute in the fixture.
    monkeypatch.setattr(er, "fetch_reply", good_reply, raising=False)
    return tmp_path


def sent_to(to: str = EIC, subject: str = SUBJECT) -> dict:
    cid = dc.capture(draft(to, subject), skill="draft-email")
    return dc.confirm(cid, "unchanged")


def run_hook(name: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / name)], input=json.dumps(payload),
                          capture_output=True, text=True, env=os.environ.copy())


# --- B: the reply is checked in Gmail ----------------------------------------------

def test_B_her_reply_in_the_thread_of_the_sent_post_closes_it():
    cid = sent_to()["id"]
    assert er.close(cid, REPLY, no_edits="approved as is")["status"] == "closed"


# Each must leave the review open. Built inside the test, after the capture, so the sent
# post is never dated before the draft by accident.
@pytest.mark.parametrize("bend,why", [
    ({"sender": "casey@example.com"}, "not from an EIC address"),
    ({"labels": ("SENT",)}, "a message Nick sent"),
    ({"labels": ("DRAFT",)}, "a draft"),
    # Round 5 (Codex 19.F1 P0, Fable 22.F1): her reply must sit in the thread of THIS post.
    ({"subject": "LinkedIn post for review: the next one"}, "her reply to another post"),
    ({"to": "casey@example.com"}, "a thread where the post went to someone else"),
])
def test_B_close_refuses_a_message_that_is_not_her_reply_to_this_post(monkeypatch, bend, why):
    cid = sent_to()["id"]
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(**bend), raising=False)
    with pytest.raises(er.ReviewError):
        er.close(cid, REPLY, no_edits="approved as is")
    assert er.read(cid)["status"] == "open", why


def test_B_a_reply_in_a_thread_with_no_sent_post_is_refused(monkeypatch):
    cid = sent_to()["id"]
    lone = her_reply()
    lone["thread"] = [m for m in lone["thread"] if "SENT" not in m["labels"]]
    monkeypatch.setattr(er, "fetch_reply", lambda rid: lone, raising=False)
    with pytest.raises(er.ReviewError, match="not in the thread"):
        er.close(cid, REPLY, no_edits="approved as is")


def test_B_a_reply_dated_before_the_post_was_sent_is_refused(monkeypatch):
    cid = sent_to()["id"]
    now = time.time()
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(sent=now, epoch=now - 30),
                        raising=False)
    with pytest.raises(er.ReviewError, match="before the post was sent"):
        er.close(cid, REPLY, no_edits="approved as is")


def test_B_an_earlier_send_of_the_same_subject_does_not_count(monkeypatch):
    # The thread holds only an older send of a post with this subject, from before this
    # draft was opened: her reply answers that one (13.F10).
    t0 = _noon(2)
    cid = dc.confirm(dc.capture(draft(EIC), skill="draft-email", now=t0), "unchanged")["id"]
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(sent=t0 - 86400), raising=False)
    with pytest.raises(er.ReviewError, match="not in the thread"):
        er.close(cid, REPLY, no_edits="approved as is")


def test_B_a_send_after_the_confirm_does_not_count(monkeypatch):
    t0 = _noon(2)
    cid = dc.confirm(dc.capture(draft(EIC), skill="draft-email", now=t0), "unchanged",
                     now=t0 + 600)["id"]
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(sent=t0 + 86400), raising=False)
    with pytest.raises(er.ReviewError, match="not in the thread"):
        er.close(cid, REPLY, no_edits="approved as is")


def test_B_a_resend_or_a_re_subject_in_the_thread_still_counts(monkeypatch):
    for bend in ({"subject": "Re: " + SUBJECT}, {"extra_sent": ((time.time() + 5, SUBJECT),)}):
        cid = sent_to()["id"]
        monkeypatch.setattr(er, "fetch_reply", lambda rid, b=bend: her_reply(**b), raising=False)
        assert er.close(cid, REPLY, no_edits="approved as is")["status"] == "closed", bend


# Round 4 (Fable 17.F1): her reply can arrive before Nick confirms the send. Times are at
# noon of a past day, so no run can cross midnight between the send and the lessons row
# (Fable r5 F1, F4).

def _noon(days_ago: int) -> float:
    import datetime
    d = datetime.date.today() - datetime.timedelta(days=days_ago)
    return time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1))


def _sent_at(t0: float) -> str:
    """A capture opened at t0 and confirmed now; the post went out a minute after t0."""
    return dc.confirm(dc.capture(draft(EIC), skill="draft-email", now=t0), "unchanged")["id"]


def _lessons_dated(monkeypatch, tmp_path, day: str) -> None:
    lessons = tmp_path / f"lessons-{day}.md"
    lessons.write_text("| Pattern | Rule | Occ | Promoted | Date |\n|---|---|---|---|---|\n"
                       f"| **{TAG}.** She cut the sign-off. | No sign-off. | 1 | No | {day} |\n",
                       encoding="utf-8")
    monkeypatch.setenv("LESSONS_PATH", str(lessons))


def test_round4_a_reply_between_the_send_and_the_confirm_closes_with_a_row_that_day(
        monkeypatch, tmp_path):
    t0 = _noon(1)
    cid = _sent_at(t0)
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(sent=t0 + 60, epoch=t0 + 120),
                        raising=False)
    _lessons_dated(monkeypatch, tmp_path, time.strftime("%Y-%m-%d", time.localtime(t0)))
    assert er.close(cid, REPLY, lesson_ref="She cut the sign-off")["status"] == "closed"


def test_round4_a_lesson_row_dated_before_her_reply_is_refused(monkeypatch, tmp_path):
    # A lessons row records her edits, so it cannot predate her reply: a row from the days
    # between drafting and her reply could be about another post (47 on r4 F1).
    t0 = _noon(3)
    cid = _sent_at(t0)
    monkeypatch.setattr(er, "fetch_reply",
                        lambda rid: her_reply(sent=t0 + 60, epoch=t0 + 2 * 86400), raising=False)
    _lessons_dated(monkeypatch, tmp_path, time.strftime("%Y-%m-%d", time.localtime(t0 + 86400)))
    with pytest.raises(er.ReviewError, match="before her reply"):
        er.close(cid, REPLY, lesson_ref="She cut the sign-off")
    assert er.read(cid)["status"] == "open"


def test_round4_a_record_without_the_draft_time_falls_back_to_when_the_review_opened(monkeypatch):
    cid = sent_to()["id"]
    record = er.read(cid)
    record.pop("draft_opened_at", None)
    er._write(record)
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply(sent=time.time() - 600),
                        raising=False)
    with pytest.raises(er.ReviewError, match="not in the thread"):
        er.close(cid, REPLY, no_edits="approved as is")


def test_B_close_fails_closed_when_gmail_cannot_be_reached(monkeypatch):
    cid = sent_to()["id"]

    def offline(rid):
        raise OSError("network is unreachable")
    monkeypatch.setattr(er, "fetch_reply", offline, raising=False)
    with pytest.raises(er.ReviewError, match="Gmail"):
        er.close(cid, REPLY, no_edits="approved as is")
    assert er.read(cid)["status"] == "open"


def test_B_the_seam_asks_gmail_for_the_id_it_was_given(monkeypatch):
    asked = []
    monkeypatch.setattr(er, "fetch_reply", lambda rid: asked.append(rid) or good_reply(rid),
                        raising=False)
    er.close(sent_to()["id"], REPLY, no_edits="approved as is")
    assert asked == [REPLY]


# --- C: a published record anywhere under output/ ------------------------------------

@pytest.mark.parametrize("path", [
    "output/acme/101626-linkedin-post-published.md",
    "output/example-conference/101926-linkedin-post-published.md",
    "output/x/y/z/110126-post-published.md",
])
def test_C_the_gate_blocks_a_published_record_outside_voice_sources(path):
    sent_to()
    assert gate.decide("Write", {"file_path": path, "content": "x"}) is not None


@pytest.mark.parametrize("path", [
    "tests/fixtures/110126-post-published.md",
    "data/110126-post-published.md",
    "output/acme/101626-linkedin-post-draft.md",
    "output/acme/101626-published-notes.md",
])
def test_C_the_gate_still_ignores_paths_that_are_not_a_published_record(path):
    sent_to()
    assert gate.decide("Write", {"file_path": path, "content": "x"}) is None


# --- D: the lessons row must be new ----------------------------------------------

def test_D_a_lessons_row_dated_before_the_review_opened_cannot_close_it():
    cid = sent_to()["id"]
    with pytest.raises(er.ReviewError, match="dated"):
        er.close(cid, REPLY, lesson_ref="She reordered the opener")
    assert er.read(cid)["status"] == "open"


def test_D_a_lessons_row_dated_today_closes_it():
    cid = sent_to()["id"]
    assert er.close(cid, REPLY, lesson_ref="She cut the second hook")["status"] == "closed"


def test_D_a_row_with_no_readable_date_cannot_close_it(env):
    with open(env / "lessons.md", "a", encoding="utf-8") as f:
        f.write(f"| **{TAG}.** She trimmed the close. | Short close. | 1 | No | soon |\n")
    cid = sent_to()["id"]
    with pytest.raises(er.ReviewError, match="dated"):
        er.close(cid, REPLY, lesson_ref="She trimmed the close")


# --- E: a send corrected to not-sent cancels its review ------------------------------

def test_E_reconfirming_as_not_sent_cancels_the_review():
    cid = sent_to()["id"]
    assert [r["capture_id"] for r in er.open_reviews()] == [cid]
    dc.confirm(cid, "not-sent", force=True)
    assert er.open_reviews() == []
    assert er.read(cid)["status"] == "cancelled"
    assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is None


def test_E_a_closed_review_is_not_reopened_or_cancelled_by_a_reconfirm():
    cid = sent_to()["id"]
    er.close(cid, REPLY, no_edits="approved as is")
    dc.confirm(cid, "not-sent", force=True)
    assert er.read(cid)["status"] == "closed"


# --- F: the subject filter ---------------------------------------------------------

def test_F_mail_to_her_without_the_filter_text_opens_nothing():
    meta = sent_to(subject="Dinner on Saturday")
    assert "eic_review" not in meta and er.reviews() == []


def test_F_the_default_filter_is_LinkedIn_post_matched_case_insensitively():
    assert sent_to(subject="Draft linkedin POST, can you look?").get("eic_review") == "opened"


def test_F_a_subject_line_in_the_config_replaces_the_default(env):
    (env / "eic.txt").write_text(f"{EIC}\ntag: {TAG}\nsubject: for your edit\n",
                                 encoding="utf-8")
    assert "eic_review" not in sent_to(subject="LinkedIn post")
    assert sent_to(subject="Something For Your Edit").get("eic_review") == "opened"


# --- G: malformed state blocks, never crashes ---------------------------------------

@pytest.mark.parametrize("content", ["[]", '"open"', "17", "null",
                                     # Codex r2 F2: an object with no usable capture_id
                                     # crashed the block message with KeyError (exit 1).
                                     "{}", '{"capture_id": 7, "status": "open"}'])
def test_G_a_record_that_is_valid_json_but_not_an_object_counts_as_open(env, content):
    (env / "reviews").mkdir()
    (env / "reviews" / "odd.json").write_text(content, encoding="utf-8")
    assert [r["capture_id"] for r in er.open_reviews()] == ["odd"]
    assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is not None


def test_G_the_hook_process_blocks_on_such_a_record_instead_of_exiting_1(env):
    (env / "reviews").mkdir()
    (env / "reviews" / "odd.json").write_text("[]", encoding="utf-8")
    out = run_hook("check_eic_review.py",
                   {"tool_name": "Write", "tool_input": {"file_path": PUBLISHED, "content": "x"}})
    assert out.returncode == 2, out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 folder")
def test_G_an_unreadable_store_folder_blocks(env):
    store = env / "reviews"
    store.mkdir()
    (store / "a.json").write_text('{"capture_id": "a", "status": "closed"}', encoding="utf-8")
    store.chmod(0)
    try:
        assert er.open_reviews() != []
        assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is not None
    finally:
        store.chmod(0o755)


# --- J: the shell's working directory -----------------------------------------------

@pytest.mark.parametrize("command", [
    f"cd {VOICE} && echo post > 110126-x-published.md",
    f"cd {VOICE}; echo post | tee 110126-x-published.md",
    f"cd output/linkedin-profile && cp /tmp/p.md voice-sources/110126-x-published.md",
    f"cd {REPO}/{VOICE} && echo post > 110126-x-published.md",
    # Codex r2 F1: a cd inside a ( ) or { } group, which the segment parser keeps whole.
    f"(cd {VOICE}; echo post > 110126-x-published.md)",
    f"{{ cd {VOICE}; echo post > 110126-x-published.md; }}",
    f"(cd output && (cd linkedin-profile/voice-sources && echo post > 110126-x-published.md))",
    f"{{ cd {VOICE}; }}; echo post > 110126-x-published.md",
])
def test_J_the_eic_gate_follows_a_cd_in_the_command(command):
    sent_to()
    assert gate.decide("Bash", {"command": command}) is not None


def test_J_the_eic_gate_follows_the_session_cwd_in_the_payload():
    sent_to()
    out = run_hook("check_eic_review.py", {
        "tool_name": "Bash", "cwd": str(REPO / VOICE),
        "tool_input": {"command": "echo post > 110126-x-published.md"}})
    assert out.returncode == 2 and "BLOCKED check_eic_review" in out.stderr


@pytest.mark.parametrize("command", [
    "cd /tmp && echo post > 110126-x-published.md",
    f"cd {VOICE} && cat 110126-x-published.md",
])
def test_J_a_cd_elsewhere_or_a_read_is_still_allowed(command):
    sent_to()
    assert gate.decide("Bash", {"command": command}) is None


def test_J_the_handoff_gate_follows_a_cd_in_the_command(tmp_path):
    (tmp_path / "output" / "analysis").mkdir(parents=True)
    msg = handoff.decide(
        "Bash", {"command": "cd output/analysis && cat > 110126-next-session.md <<'EOF'\nx\nEOF"},
        root=tmp_path)
    assert msg is not None and "output/analysis/110126-next-session.md" in msg


def test_J_the_handoff_gate_follows_a_cd_inside_a_group(tmp_path):
    (tmp_path / "output" / "analysis").mkdir(parents=True)
    msg = handoff.decide("Bash", {"command": "(cd output/analysis; echo x > 110126-next-session.md)"},
                         root=tmp_path)
    assert msg is not None and "output/analysis/110126-next-session.md" in msg


def test_J_the_handoff_gate_follows_the_session_cwd_in_the_payload():
    name = "110126-next-session-cwd-probe.md"
    assert not (REPO / "output" / "analysis" / name).exists()
    out = run_hook("check_handoff_producer.py", {
        "tool_name": "Bash", "cwd": str(REPO / "output" / "analysis"),
        "tool_input": {"command": f"echo x > {name}"}})
    assert out.returncode == 2, out.stderr


def test_J_the_handoff_gate_still_allows_a_cd_into_a_company_folder(tmp_path):
    (tmp_path / "output" / "acme").mkdir(parents=True)
    assert handoff.decide("Bash", {"command": "cd output/acme && echo x > 110126-handoff.md"},
                          root=tmp_path) is None


# --- added after the first mutation run on the fixes (2026-11-01) --------------------

def test_G_the_gate_blocks_when_reading_the_reviews_raises(monkeypatch):
    def broken():
        raise RuntimeError("store exploded")
    monkeypatch.setattr(er, "open_reviews", broken)
    msg = gate.decide("Write", {"file_path": PUBLISHED, "content": "x"})
    assert msg is not None and "could not be read" in msg and "store exploded" in msg


def test_E_cancel_for_reports_false_when_there_is_nothing_to_cancel():
    cid = dc.capture(draft(EIC), skill="draft-email")
    unsent = dc.confirm(cid, "not-sent")
    assert er.cancel_for(unsent) is False                    # no review was ever opened
    closed = sent_to()
    er.close(closed["id"], REPLY, no_edits="approved as is")
    assert er.cancel_for({**closed, "status": "not-sent"}) is False   # closed stays closed
    still_sent = sent_to()
    assert er.cancel_for(still_sent) is False                # the send was not corrected


def test_E_the_confirm_result_reports_the_cancel():
    cid = sent_to()["id"]
    assert dc.confirm(cid, "not-sent", force=True)["eic_review"] == "cancelled"


def test_B_fetch_reply_reads_the_reply_and_its_thread_from_gmail(monkeypatch):
    import types
    import gmail_fetch
    seen = {}

    def message(labels, epoch, sender, to, subject):
        return {"labelIds": labels, "internalDate": epoch,
                "payload": {"headers": [{"name": "Subject", "value": subject},
                                        {"name": "From", "value": sender},
                                        {"name": "To", "value": to}]}}
    reply = {**message(["INBOX", "UNREAD"], "1793000000123", f"Editor <{EIC}>",
                       "Nick <nick@example.com>", "Re: post"), "threadId": "t1"}
    sent = message(["SENT"], "1792999000000", "Nick <nick@example.com>", f"Editor <{EIC}>",
                   "post")

    class Call:
        def __init__(self, kind, **kw):
            self.kind = kind
            seen[kind] = kw

        def execute(self):
            return reply if self.kind == "message" else {"messages": [sent, reply]}

    service = types.SimpleNamespace(users=lambda: types.SimpleNamespace(
        messages=lambda: types.SimpleNamespace(get=lambda **kw: Call("message", **kw)),
        threads=lambda: types.SimpleNamespace(get=lambda **kw: Call("thread", **kw))))
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *a, **kw: service
    monkeypatch.setitem(sys.modules, "googleapiclient", types.ModuleType("googleapiclient"))
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setattr(gmail_fetch, "get_or_refresh_creds", lambda tools_dir: object())
    # The autouse fixture stubs er.fetch_reply; load a separate copy of the module so the
    # real function runs, against the fake Gmail service above.
    import importlib.util
    spec = importlib.util.spec_from_file_location("eic_review_fresh", TOOLS / "eic_review.py")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    got = fresh.fetch_reply(REPLY)
    mine = {"from": f"Editor <{EIC}>", "to": "Nick <nick@example.com>", "subject": "Re: post",
            "labels": ["INBOX", "UNREAD"], "epoch": 1793000000.123}
    assert got == {**mine, "thread": [
        {"from": "Nick <nick@example.com>", "to": f"Editor <{EIC}>", "subject": "post",
         "labels": ["SENT"], "epoch": 1792999000.0}, mine]}
    assert seen["message"]["id"] == REPLY and seen["message"]["format"] == "metadata"
    assert seen["thread"]["id"] == "t1" and seen["thread"]["userId"] == "me"


def test_B_close_refuses_a_record_that_is_not_an_object(env):
    (env / "reviews").mkdir()
    (env / "reviews" / "odd.json").write_text("[]", encoding="utf-8")
    with pytest.raises(er.ReviewError, match="not a JSON object"):
        er.close("odd", REPLY, no_edits="approved as is")


def test_a_no_edits_close_records_the_reason_and_no_lesson():
    cid = sent_to()["id"]
    er.close(cid, REPLY, no_edits="approved as is")
    rec = er.read(cid)
    assert rec["no_edits_reason"] == "approved as is" and "lesson_ref" not in rec
    cid2 = sent_to()["id"]
    er.close(cid2, REPLY, lesson_ref="She cut the second hook")
    rec2 = er.read(cid2)
    assert rec2["lesson_ref"] == "She cut the second hook" and "no_edits_reason" not in rec2


def test_E_a_send_reconfirmed_after_a_not_sent_correction_reopens_the_review():
    """Fable r3 F5: open_for refused when any record existed, so a cancelled review never
    came back when the send was confirmed again, and the published record went unblocked."""
    cid = sent_to()["id"]
    dc.confirm(cid, "not-sent", force=True)
    assert er.read(cid)["status"] == "cancelled"
    dc.confirm(cid, "unchanged", force=True)
    assert er.read(cid)["status"] == "open"
    assert [r["capture_id"] for r in er.open_reviews()] == [cid]
    assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is not None


def test_G_a_record_that_is_not_json_refuses_close_and_cancel_without_a_traceback(env):
    # Round 5 (Fable 22.F2, raised in rounds 1, 3 and 4 too): read() let JSONDecodeError
    # through, so the close the gate prescribes died with a traceback, and cancel_for did
    # inside draft_capture.confirm after the capture meta was already written.
    (env / "reviews").mkdir()
    (env / "reviews" / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(er.ReviewError, match="unreadable"):
        er.close("bad", REPLY, no_edits="approved as is")
    assert er.cancel_for({"id": "bad", "status": "not-sent"}) is False
    assert [r["capture_id"] for r in er.open_reviews()] == ["bad"]     # still blocks
