"""Tests for tools/draft_capture.py and the capture step in tools/open_draft.py.

WHAT THIS PROTECTS. The only question these records exist to answer is "how much did Nick
change this draft before sending it". That needs three things to be true and nothing else:
the first draft is kept exactly and never overwritten, "opened" is never mistaken for
"sent", and the change measure means what it says. Each test below is one of those.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import draft_capture as dc  # noqa: E402

DRAFT = ("TO: casey@example.com\nSUBJECT: Following up\nBODY:\nHi Casey,\n\n"
         "Wanted to follow up on the role we discussed last week.\n\nBest,\nNick\n")


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Every test writes to a throwaway store, never to output/draft-captures/."""
    d = tmp_path / "captures"
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(d))
    monkeypatch.setenv("LESSONS_PATH", str(tmp_path / "lessons.md"))
    return d


# --- the first draft is kept exactly ---------------------------------------------

def test_the_draft_is_stored_byte_for_byte_with_what_is_known_about_it(store):
    cid = dc.capture(DRAFT, skill="follow-up")
    assert (store / cid / "draft-v0.txt").read_text(encoding="utf-8") == DRAFT
    meta = dc.read_meta(cid)
    assert meta["id"] == cid and meta["kind"] == "email" and meta["skill"] == "follow-up"
    assert meta["to"] == "casey@example.com" and meta["subject"] == "Following up"
    assert meta["status"] == "opened"
    assert "confirmed_at" not in meta and "changed_word_ratio" not in meta


def test_two_drafts_in_the_same_second_are_both_kept(store):
    """One staging file overwritten by the next draft is the defect this replaces."""
    first = dc.capture(DRAFT, now=1_800_000_000)
    second = dc.capture(DRAFT, now=1_800_000_000)
    third = dc.capture(DRAFT.replace("last week", "yesterday"), now=1_800_000_000)
    assert len({first, second, third}) == 3
    assert (store / first / "draft-v0.txt").read_text(encoding="utf-8") == DRAFT
    assert "yesterday" in (store / third / "draft-v0.txt").read_text(encoding="utf-8")


def test_header_lines_inside_the_body_are_not_read_as_headers(store):
    cid = dc.capture("SUBJECT: Real\nBODY:\nTO: not-a-recipient@example.com\nSUBJECT: fake\n")
    meta = dc.read_meta(cid)
    assert meta["to"] == "" and meta["subject"] == "Real"


# --- opened is not sent -----------------------------------------------------------

def test_a_capture_stays_pending_until_someone_says_what_happened(store):
    a = dc.capture(DRAFT, now=1_800_000_000)
    b = dc.capture(DRAFT, now=1_800_000_060)
    assert [m["id"] for m in dc.pending()] == [a, b]
    dc.confirm(a, "unchanged")
    assert [m["id"] for m in dc.pending()] == [b]


def test_pending_can_be_limited_to_one_kind(store):
    email = dc.capture(DRAFT, now=1_800_000_000)
    cv = dc.capture("cv:\n  name: x\n", kind="cv", now=1_800_000_060)
    assert [m["id"] for m in dc.pending(kind="email")] == [email]
    assert [m["id"] for m in dc.pending(kind="cv")] == [cv]


@pytest.mark.parametrize("outcome, ratio", [("unchanged", 0.0), ("not-sent", None)])
def test_unchanged_and_not_sent_are_recorded_without_a_sent_text(store, outcome, ratio):
    cid = dc.capture(DRAFT)
    meta = dc.confirm(cid, outcome)
    assert meta["status"] == outcome and meta["changed_word_ratio"] == ratio
    assert meta["sent_source"] is None and "confirmed_at" in meta
    assert not (store / cid / "sent.txt").exists()
    assert dc.read_meta(cid) == meta, "what was returned is not what was written"


def test_an_edited_send_keeps_the_sent_text_and_measures_the_change(store):
    cid = dc.capture(DRAFT)
    sent = "Hi Casey,\n\nFollowing up on the role we discussed last week.\n\nBest,\nNick\n"
    meta = dc.confirm(cid, "edited", sent_text=sent, no_lesson="test: wording only")
    assert (store / cid / "sent.txt").read_text(encoding="utf-8") == sent
    assert (store / cid / "draft-v0.txt").read_text(encoding="utf-8") == DRAFT
    assert meta["status"] == "edited" and meta["sent_source"] == "pasted"
    # Draft body is 15 words; "Wanted to follow up" became "Following up": 12 words kept.
    assert meta["draft_words"] == 15 and meta["sent_words"] == 13
    assert meta["changed_word_ratio"] == 0.2


def test_a_screenshot_marks_the_sent_text_as_a_transcription(store):
    """A transcription of an image is weaker evidence than a paste; the record must say."""
    cid = dc.capture(DRAFT)
    meta = dc.confirm(cid, "edited", sent_text="Hi Casey,\n\nShort note.\n",
                      screenshots=["/shots/a.png", "/shots/b.png"], no_lesson="test")
    assert meta["sent_source"] == "screenshot-transcription"
    assert meta["screenshots"] == ["/shots/a.png", "/shots/b.png"]


@pytest.mark.parametrize("kwargs, needle", [
    ({"outcome": "edited"}, "needs the text"),
    ({"outcome": "edited", "sent_text": "   \n"}, "needs the text"),
    ({"outcome": "unchanged", "sent_text": "Hi"}, "use `edited`"),
    ({"outcome": "sent"}, "outcome must be one of"),
])
def test_a_confirmation_that_contradicts_itself_is_refused_and_changes_nothing(
        store, kwargs, needle):
    cid = dc.capture(DRAFT)
    before = dc.read_meta(cid)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(cid, **kwargs)
    assert needle in str(exc.value)
    assert dc.read_meta(cid) == before and not (store / cid / "sent.txt").exists()


def test_a_confirmed_draft_is_not_silently_reconfirmed(store):
    cid = dc.capture(DRAFT)
    dc.confirm(cid, "edited", sent_text="Hi Casey,\n\nRewritten entirely.\n", no_lesson="test")
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(cid, "unchanged")
    assert "already confirmed" in str(exc.value)
    assert dc.read_meta(cid)["status"] == "edited"


def test_forcing_a_new_outcome_removes_the_sent_text_it_contradicts(store):
    cid = dc.capture(DRAFT)
    dc.confirm(cid, "edited", sent_text="Hi Casey,\n\nRewritten entirely.\n", no_lesson="test")
    meta = dc.confirm(cid, "not-sent", force=True)
    assert meta["status"] == "not-sent" and "sent_words" not in meta
    assert not (store / cid / "sent.txt").exists()


def test_latest_means_the_newest_unconfirmed_draft(store):
    old = dc.capture(DRAFT, now=1_800_000_000)
    new = dc.capture(DRAFT, now=1_800_000_060)
    assert dc.confirm("latest", "unchanged")["id"] == new
    assert dc.confirm("latest", "not-sent")["id"] == old
    with pytest.raises(dc.CaptureError):
        dc.confirm("latest", "unchanged")


def test_an_unknown_id_is_an_error_not_a_new_record(store):
    with pytest.raises(dc.CaptureError):
        dc.confirm("20260101-000000-abcdef", "unchanged")
    assert dc.captures() == []


def test_a_directory_with_no_readable_record_is_skipped(store):
    cid = dc.capture(DRAFT)
    (store / "stray").mkdir()
    (store / "broken").mkdir()
    (store / "broken" / "meta.json").write_text("{not json", encoding="utf-8")
    assert [m["id"] for m in dc.captures()] == [cid]


def test_no_store_yet_means_no_captures(store):
    assert dc.captures() == [] and dc.pending() == []


# --- the change measure means what it says -----------------------------------------

@pytest.mark.parametrize("draft, sent, ratio", [
    ("one two three four", "one two three four", 0.0),
    ("one two three four", "one  two\nthree\n\nfour", 0.0),        # layout is not an edit
    ("one two three four", "five six seven eight", 1.0),
    ("one two three four", "one two three", 0.25),                  # a deletion
    ("one two three", "one two three four", 0.25),                  # an addition
    ("one two three four", "one two THREE four", 0.25),             # a replacement
    ("", "", 0.0),
    ("one two", "", 1.0),
])
def test_the_changed_word_ratio(draft, sent, ratio):
    assert dc.changed_word_ratio(draft, sent) == ratio


def test_the_measure_compares_bodies_not_headers(store):
    """A sent text pasted without TO and SUBJECT lines is not a rewrite of the headers."""
    cid = dc.capture(DRAFT)
    body_only = dc.body_of(DRAFT)
    assert dc.confirm(cid, "edited", sent_text=body_only)["changed_word_ratio"] == 0.0


# --- the command line ---------------------------------------------------------------

def test_the_command_line_confirms_from_a_file_and_reports_errors_by_exit_code(
        store, tmp_path, capsys):
    cid = dc.capture(DRAFT)
    sent = tmp_path / "sent.txt"
    sent.write_text("Hi Casey,\n\nRewritten entirely.\n", encoding="utf-8")
    assert dc.main(["confirm", cid, "edited", "--body-file", str(sent),
                    "--screenshot", "/shots/a.png", "--no-lesson", "test"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "edited" and out["sent_source"] == "screenshot-transcription"

    assert dc.main(["confirm", cid, "unchanged"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert dc.main(["confirm", "latest", "edited", "--body-file", str(tmp_path / "no")]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"

    assert dc.main(["confirm", cid, "not-sent", "--force"]) == 0
    capsys.readouterr()
    assert dc.read_meta(cid)["status"] == "not-sent"


def test_pending_on_the_command_line_lists_unconfirmed_captures(store, capsys):
    cid = dc.capture(DRAFT)
    assert dc.main(["pending"]) == 0
    assert [m["id"] for m in json.loads(capsys.readouterr().out)["pending"]] == [cid]


def test_a_cv_first_draft_is_captured_whole_from_its_file(store, tmp_path, capsys):
    cv = tmp_path / "100626-magnuson.content.yaml"
    cv.write_text("cv:\n  name: Sample Person\n  sections: {}\n", encoding="utf-8")
    assert dc.main(["cv", str(cv)]) == 0
    cid = json.loads(capsys.readouterr().out)["id"]
    assert (store / cid / "draft-v0.txt").read_text(encoding="utf-8") == cv.read_text(encoding="utf-8")
    meta = dc.read_meta(cid)
    assert meta["kind"] == "cv" and meta["skill"] == "generate-cv"
    assert meta["source"] == str(cv.resolve())
    assert dc.main(["cv", str(tmp_path / "absent.yaml")]) == 1


def test_a_file_rewritten_in_place_is_captured_the_first_time_only(store, tmp_path):
    """The content file is corrected at the same path; only its first version is the draft."""
    cv = tmp_path / "100626-magnuson.content.yaml"
    cv.write_text("cv:\n  name: First version\n", encoding="utf-8")
    first, was_new = dc.capture_file_once(cv, kind="cv", skill="generate-cv")
    assert was_new is True
    cv.write_text("cv:\n  name: Corrected after review\n", encoding="utf-8")
    again, was_new_again = dc.capture_file_once(cv, kind="cv", skill="generate-cv")
    assert (again, was_new_again) == (first, False)
    assert len(dc.captures()) == 1
    assert "First version" in (store / first / "draft-v0.txt").read_text(encoding="utf-8")
    other = tmp_path / "100726-magnuson.content.yaml"
    other.write_text("cv:\n  name: Another application\n", encoding="utf-8")
    assert dc.capture_file_once(other, kind="cv", skill="generate-cv")[1] is True
    # The same path captured as a different kind is a different record.
    assert dc.capture_file_once(cv, kind="email", skill="x")[1] is True


def test_building_a_cv_captures_its_first_content_and_only_its_first(store, tmp_path, capsys):
    """Every generated CV goes through the theme merge, so that is where the copy is taken."""
    spec = importlib.util.spec_from_file_location("cv_merge_under_test", TOOLS / "cv_merge_theme.py")
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    content = tmp_path / "100626-magnuson.content.yaml"
    content.write_text("cv:\n  name: Sample Person\n", encoding="utf-8")
    theme = tmp_path / "theme.yaml"
    theme.write_text("design:\n  theme: classic\n", encoding="utf-8")
    args = ["--content", str(content), "--theme", str(theme), "--out", str(tmp_path / "out.yaml"), "--json"]

    assert merge.main(args) == 0
    cid = json.loads(capsys.readouterr().out)["first_draft_capture"]
    assert (store / cid / "draft-v0.txt").read_text(encoding="utf-8") == content.read_text(encoding="utf-8")
    assert dc.read_meta(cid)["status"] == "opened"

    content.write_text("cv:\n  name: Sample Person\n  headline: corrected\n", encoding="utf-8")
    assert merge.main(args) == 0
    assert json.loads(capsys.readouterr().out)["first_draft_capture"] is None
    assert len(dc.captures()) == 1 and "headline" not in (store / cid / "draft-v0.txt").read_text(encoding="utf-8")

    assert merge.main(args[:-1]) == 0
    assert "captured" not in capsys.readouterr().out, "announced a capture that did not happen"


def test_building_a_cv_without_json_output_says_how_to_confirm_it(store, tmp_path, capsys):
    """That line is how the model learns the capture exists and what to do when the CV is sent."""
    spec = importlib.util.spec_from_file_location("cv_merge_under_test", TOOLS / "cv_merge_theme.py")
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    content = tmp_path / "c.content.yaml"
    content.write_text("cv:\n  name: Sample Person\n", encoding="utf-8")
    theme = tmp_path / "theme.yaml"
    theme.write_text("design:\n  theme: classic\n", encoding="utf-8")
    assert merge.main(["--content", str(content), "--theme", str(theme),
                       "--out", str(tmp_path / "out.yaml")]) == 0
    out = capsys.readouterr().out
    cid = dc.captures()[0]["id"]
    assert f"confirm {cid} edited" in out and str(content) in out


def test_the_suite_never_points_at_the_real_store():
    """Set once in tests/conftest.py for every test and every process a test starts."""
    assert os.environ.get("DRAFT_CAPTURE_DIR"), "a tool started by a test would write real records"
    assert os.environ.get("LESSONS_PATH"), "a test would read the real lessons log"
    assert dc.store_dir() != dc.REPO_ROOT / "output" / "draft-captures"


def test_a_cv_is_still_built_when_the_capture_cannot_be_written(store, tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("cv_merge_under_test", TOOLS / "cv_merge_theme.py")
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    content = tmp_path / "c.content.yaml"
    content.write_text("cv:\n  name: Sample Person\n", encoding="utf-8")
    theme = tmp_path / "theme.yaml"
    theme.write_text("design:\n  theme: classic\n", encoding="utf-8")
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(blocked))
    out = tmp_path / "out.yaml"
    assert merge.main(["--content", str(content), "--theme", str(theme), "--out", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["first_draft_capture"] is None
    assert "design" in out.read_text(encoding="utf-8")


def test_run_as_a_script_it_reports_failure_through_the_exit_status(store, tmp_path):
    """Skills and the model read the exit status and the JSON, not a Python return value."""
    import subprocess
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DRAFT_CAPTURE_DIR": str(store)}
    bad = subprocess.run([sys.executable, str(TOOLS / "draft_capture.py"), "confirm",
                          "20260101-000000-abcdef", "unchanged"],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 1 and json.loads(bad.stdout)["status"] == "error"
    good = subprocess.run([sys.executable, str(TOOLS / "draft_capture.py"), "pending"],
                          capture_output=True, text=True, env=env)
    assert good.returncode == 0 and json.loads(good.stdout) == {"status": "ok", "pending": []}


# --- open_draft.py records the draft before it leaves ---------------------------------

@pytest.fixture
def opener(tmp_path, monkeypatch):
    """open_draft loaded fresh, pointed at a throwaway staging file, with no browser."""
    spec = importlib.util.spec_from_file_location("open_draft_under_test", TOOLS / "open_draft.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    staging = tmp_path / ".pending-draft.txt"
    monkeypatch.setattr(mod, "DRAFT_FILE", str(staging))
    monkeypatch.setattr(mod, "SOURCE_MARKER", str(tmp_path / ".pending-draft.source"))
    opened = []
    monkeypatch.setattr(mod.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(mod, "copy_text_to_clipboard", lambda text: True)
    mod.opened_urls, mod.staging = opened, staging
    return mod


def test_opening_a_draft_captures_it_and_does_not_call_it_sent(opener, store, tmp_path, capsys):
    opener.staging.write_text(DRAFT, encoding="utf-8")
    (tmp_path / ".pending-draft.source").write_text("follow-up\n2026-10-06T12:00:00\n",
                                                    encoding="utf-8")
    opener.main()
    out = capsys.readouterr().out
    assert len(opener.opened_urls) == 1
    recorded = dc.captures()
    assert len(recorded) == 1 and recorded[0]["status"] == "opened"
    assert recorded[0]["skill"] == "follow-up"
    cid = recorded[0]["id"]
    assert (store / cid / "draft-v0.txt").read_text(encoding="utf-8") == DRAFT
    # This output is how the model learns what to do next, so all three outcomes are named.
    for outcome in ("unchanged", "edited", "not-sent"):
        assert f"confirm {cid} {outcome}" in out
    assert "screenshot" in out
    assert not opener.staging.exists()
    assert Path(str(opener.staging) + ".opened").exists()
    assert not Path(str(opener.staging) + ".sent").exists(), "opening compose is not sending"


def test_a_reply_is_captured_too(opener, store, capsys):
    opener.staging.write_text(DRAFT.replace("Following up", "Re: Following up"), encoding="utf-8")
    opener.main()
    capsys.readouterr()
    assert opener.opened_urls == [], "a reply is pasted into the thread, not opened as compose"
    assert [m["subject"] for m in dc.captures()] == ["Re: Following up"]
    assert Path(str(opener.staging) + ".opened").exists()


def test_a_capture_that_fails_does_not_stop_the_draft_from_opening(opener, store, monkeypatch, capsys):
    opener.staging.write_text(DRAFT, encoding="utf-8")

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(opener.draft_capture, "capture", broken)
    opener.main()
    out = capsys.readouterr().out
    assert len(opener.opened_urls) == 1
    assert "NOT captured" in out and "disk full" in out
    assert dc.captures() == []


def test_earlier_unconfirmed_drafts_are_mentioned_when_the_next_one_opens(opener, store, capsys):
    """The confirmation is voluntary, so the reminder arrives at the next moment it can."""
    opener.staging.write_text(DRAFT, encoding="utf-8")
    opener.main()
    assert "still unconfirmed" not in capsys.readouterr().out
    opener.staging.write_text(DRAFT, encoding="utf-8")
    opener.main()
    assert "1 earlier draft(s) are still unconfirmed" in capsys.readouterr().out



# --- what open_draft.py puts in front of Nick ------------------------------------------
# These decide which draft opens, addressed to whom, with what text. The tool had no test
# of any of it before 2026-10-06.

def test_the_compose_window_gets_the_recipient_subject_and_body_from_the_draft(opener, store, capsys):
    import urllib.parse
    opener.staging.write_text("TO: casey@example.com\nCC: second@example.com\nSUBJECT: Hello there\n"
                              "BODY: First line\n\nSecond line & more\n\n\n", encoding="utf-8")
    opener.main()
    capsys.readouterr()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(opener.opened_urls[0]).query)
    assert opener.opened_urls[0].startswith("https://mail.google.com/mail/?")
    assert query["to"] == ["casey@example.com"] and query["cc"] == ["second@example.com"]
    assert query["su"] == ["Hello there"]
    assert query["body"] == ["First line\n\nSecond line & more"], "trailing blank lines are trimmed"


def test_fields_the_draft_does_not_set_are_left_out_of_the_compose_link(opener, store, capsys):
    import urllib.parse
    opener.staging.write_text("SUBJECT: Only a subject\nBODY:\nText\n", encoding="utf-8")
    opener.main()
    capsys.readouterr()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(opener.opened_urls[0]).query)
    assert "to" not in query and "cc" not in query
    assert query["su"] == ["Only a subject"] and query["body"] == ["Text"]


def test_header_words_inside_the_body_stay_in_the_body(opener):
    opener.staging.write_text("TO: casey@example.com\nSUBJECT: S\nATTACH: /tmp/cv.pdf\nATTACH:\n"
                              "BODY:\nTO: is a word here\nSUBJECT: so is this\n", encoding="utf-8")
    to, cc, subject, attachments, body = opener.parse_draft(str(opener.staging))
    assert (to, cc, subject) == ("casey@example.com", "", "S")
    assert attachments == ["/tmp/cv.pdf"], "an empty ATTACH line is not an attachment"
    assert body == "TO: is a word here\nSUBJECT: so is this"


def test_a_stale_draft_is_refused_and_nothing_opens_or_is_captured(opener, store, capsys):
    """A draft left over from an earlier session must not be the one that opens."""
    opener.staging.write_text(DRAFT, encoding="utf-8")
    old = opener.staging.stat().st_mtime - 10 * 60
    os.utime(opener.staging, (old, old))
    with pytest.raises(SystemExit) as exc:
        opener.main()
    capsys.readouterr()
    assert exc.value.code == 2
    assert opener.opened_urls == [] and dc.captures() == [] and opener.staging.exists()


def test_no_pending_draft_exits_one_and_opens_nothing(opener, store, capsys):
    with pytest.raises(SystemExit) as exc:
        opener.main()
    capsys.readouterr()
    assert exc.value.code == 1 and opener.opened_urls == [] and dc.captures() == []


@pytest.mark.parametrize("subject, reply", [
    ("Re: Thanks", True), ("RE: thanks", True), ("  re : spaced", True),
    ("Regarding the role", False), ("Thanks", False),
])
def test_a_reply_is_recognised_by_its_subject(opener, subject, reply):
    assert opener.is_reply(subject) is reply


def test_a_reply_puts_the_body_on_the_clipboard_and_not_in_a_new_compose(opener, store, monkeypatch, capsys):
    copied = []
    monkeypatch.setattr(opener, "copy_text_to_clipboard", lambda text: copied.append(text) or True)
    opener.staging.write_text("TO: casey@example.com\nSUBJECT: Re: Thanks\nBODY:\nReply text\n",
                              encoding="utf-8")
    opener.main()
    capsys.readouterr()
    assert copied == ["Reply text"] and opener.opened_urls == []


# --- a revised email is not recorded as sent until its edits are learned from ---------
# 2026-10-22 audit: of last week's sends, 5 with real edits had no row in lessons.md, and the
# only session that wrote one did so because Nick asked. These tests hold the gate.

SENT_EDIT = "Hi Casey,\n\nFollowing up on the role we discussed last week.\n\nThanks!\nNick\n"
ROW = ("| Claude signed a recruiter note with Best; Nick sent Thanks! **Casey Doe / Acme, "
       "2026-10-22.** | Sign an information ask with Thanks. | 1 | No | 2026-10-22 |")


def _lessons(store, text=ROW):
    path = Path(os.environ["LESSONS_PATH"])
    path.write_text("## Section 2\n\n| Pattern | Rule | Occurrences | Promoted | Date |\n"
                    "|---|---|---|---|---|\n" + text + "\n", encoding="utf-8")


def test_an_edited_email_with_no_lesson_is_refused_and_changes_nothing(store):
    cid = dc.capture(DRAFT)
    before = dc.read_meta(cid)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(cid, "edited", sent_text=SENT_EDIT)
    assert "--lesson" in str(exc.value) and "--no-lesson" in str(exc.value)
    assert dc.read_meta(cid) == before and not (store / cid / "sent.txt").exists()


def test_a_lesson_reference_must_be_a_real_row(store):
    _lessons(store)
    cid = dc.capture(DRAFT)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(cid, "edited", sent_text=SENT_EDIT, lesson_ref="Jordan Sample / Nowhere")
    assert "not found" in str(exc.value)
    meta = dc.confirm(cid, "edited", sent_text=SENT_EDIT, lesson_ref="Casey Doe / Acme")
    assert meta["lesson_ref"] == "Casey Doe / Acme" and "no_lesson_reason" not in meta


def test_a_lesson_reference_must_be_long_enough_to_mean_one_row(store):
    _lessons(store)
    cid = dc.capture(DRAFT)
    with pytest.raises(dc.CaptureError):
        dc.confirm(cid, "edited", sent_text=SENT_EDIT, lesson_ref="| ")


def test_the_lesson_must_be_a_table_row_not_text_elsewhere(store):
    _lessons(store, "Casey Doe / Acme is mentioned in prose, not in a row")
    cid = dc.capture(DRAFT)
    with pytest.raises(dc.CaptureError):
        dc.confirm(cid, "edited", sent_text=SENT_EDIT, lesson_ref="Casey Doe / Acme")


def test_a_blank_no_lesson_reason_is_not_a_reason(store):
    cid = dc.capture(DRAFT)
    with pytest.raises(dc.CaptureError):
        dc.confirm(cid, "edited", sent_text=SENT_EDIT, no_lesson="   ")
    meta = dc.confirm(cid, "edited", sent_text=SENT_EDIT, no_lesson="sign-off only, already a rule")
    assert meta["no_lesson_reason"] == "sign-off only, already a rule"


def test_an_unchanged_send_after_replaced_drafts_was_still_revised(store):
    """Edits made in chat before compose opened leave earlier drafts to the same thread."""
    first = dc.capture(DRAFT, now=1_800_000_000)
    dc.confirm(first, "not-sent")
    second = dc.capture(DRAFT.replace("SUBJECT: Following up", "SUBJECT: Re: Following up")
                        .replace("last week", "on Tuesday"), now=1_800_000_600)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(second, "unchanged")
    assert "1 earlier draft" in str(exc.value)
    assert dc.confirm(second, "unchanged", no_lesson="date fix only")["revised_versions"] == 1


def test_a_revision_captured_in_the_same_second_is_still_earlier(store):
    """opened_at is to the second; opened_ns orders inside it (619.F2/625.F1)."""
    first = dc.capture(DRAFT, now=1_800_000_000.1)
    dc.confirm(first, "not-sent")
    second = dc.capture(DRAFT.replace("last week", "on Tuesday"), now=1_800_000_000.7)
    with pytest.raises(dc.CaptureError):
        dc.confirm(second, "unchanged")
    assert dc.confirm(second, "unchanged", no_lesson="date fix only")["revised_versions"] == 1


def test_an_unchanged_single_draft_needs_nothing(store):
    cid = dc.capture(DRAFT)
    other = dc.capture(DRAFT.replace("casey@example.com", "jordan@example.com"))
    meta = dc.confirm(cid, "unchanged")
    assert meta["revised_versions"] == 0 and "lesson_ref" not in meta
    assert other != cid


def test_a_layout_only_edit_needs_nothing(store):
    cid = dc.capture(DRAFT)
    assert dc.confirm(cid, "edited", sent_text=dc.body_of(DRAFT) + "\n\n")["changed_word_ratio"] == 0.0


def test_a_cv_is_not_held_by_the_lessons_gate(store):
    cid = dc.capture("cv:\n  name: x\n", kind="cv")
    assert dc.confirm(cid, "edited", sent_text="cv:\n  name: y\n")["status"] == "edited"


def test_unlearned_lists_revised_sends_with_no_lesson(store, capsys):
    a = dc.capture(DRAFT, now=1_800_000_000)
    dc.confirm(a, "edited", sent_text=SENT_EDIT, no_lesson="x")
    b = dc.capture(DRAFT.replace("casey", "jordan"), now=1_800_000_100)
    meta = dc.read_meta(b)
    meta.update({"status": "edited", "changed_word_ratio": 0.3})   # confirmed before the gate
    (store / b / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert [m["id"] for m in dc.unlearned()] == [b]
    assert dc.main(["unlearned"]) == 0
    assert [m["id"] for m in json.loads(capsys.readouterr().out)["unlearned"]] == [b]


def test_the_command_line_carries_the_lesson_flags(store, tmp_path, capsys):
    _lessons(store)
    cid = dc.capture(DRAFT)
    sent = tmp_path / "sent.txt"
    sent.write_text(SENT_EDIT, encoding="utf-8")
    assert dc.main(["confirm", cid, "edited", "--body-file", str(sent)]) == 1
    assert "--lesson" in json.loads(capsys.readouterr().out)["message"]
    assert dc.main(["confirm", cid, "edited", "--body-file", str(sent),
                    "--lesson", "Casey Doe / Acme"]) == 0
    assert json.loads(capsys.readouterr().out)["lesson_ref"] == "Casey Doe / Acme"


def test_reopening_the_same_text_is_not_a_revision(store):
    first = dc.capture(DRAFT, now=1_800_000_000)
    again = dc.capture(DRAFT, now=1_800_000_060)
    assert dc.confirm(again, "unchanged")["revised_versions"] == 0
    assert dc.read_meta(first)["status"] == "opened"


def test_a_replaced_draft_from_another_day_or_thread_is_not_counted(store):
    other_thread = dc.capture(DRAFT.replace("Following up", "Something else")
                              .replace("last week", "today"), now=1_800_000_000)
    other_day = dc.capture(DRAFT.replace("last week", "today"), now=1_800_000_000 - 86_400)
    cid = dc.capture(DRAFT, now=1_800_000_600)
    assert dc.confirm(cid, "unchanged")["revised_versions"] == 0
    assert {other_thread, other_day}.isdisjoint({cid})


def test_the_refusal_says_what_was_revised_and_nothing_else(store):
    a = dc.capture(DRAFT, now=1_800_000_000)
    b = dc.capture(DRAFT.replace("last week", "Tuesday"), now=1_800_000_060)
    c = dc.capture(DRAFT.replace("last week", "Monday"), now=1_800_000_120)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(c, "unchanged")
    msg = str(exc.value)
    assert "2 earlier drafts to this thread" in msg and "words changed" not in msg
    d = dc.capture(DRAFT.replace("casey@", "jordan@"), now=1_800_000_180)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(d, "edited", sent_text=SENT_EDIT)
    msg = str(exc.value)
    assert "of the words changed at send" in msg and "earlier draft" not in msg
    assert {a, b}.isdisjoint({c})


def test_drafts_with_no_recipient_are_never_matched_as_one_thread(store):
    dc.capture(DRAFT.replace("casey@example.com", ""), now=1_800_000_000)
    cid = dc.capture(DRAFT.replace("casey@example.com", "").replace("last week", "today"),
                     now=1_800_000_060)
    assert dc.confirm(cid, "unchanged")["revised_versions"] == 0


def test_an_earlier_draft_whose_text_is_lost_is_not_counted(store):
    old = dc.capture(DRAFT.replace("last week", "today"), now=1_800_000_000)
    (store / old / "draft-v0.txt").unlink()
    cid = dc.capture(DRAFT, now=1_800_000_060)
    (store / cid / "draft-v0.txt").write_text("", encoding="utf-8")
    assert dc.confirm(cid, "unchanged")["revised_versions"] == 0


def test_unlearned_skips_clean_sends_and_catches_pre_gate_chat_revisions(store):
    clean = dc.capture(DRAFT, now=1_800_000_000)
    dc.confirm(clean, "unchanged")
    old = dc.capture(DRAFT.replace("casey", "jordan").replace("last week", "today"),
                     now=1_800_000_100)
    pre_gate = dc.capture(DRAFT.replace("casey", "jordan"), now=1_800_000_200)
    meta = dc.read_meta(pre_gate)
    meta.update({"status": "unchanged", "changed_word_ratio": 0.0})  # no revised_versions key
    (store / pre_gate / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert [m["id"] for m in dc.unlearned()] == [pre_gate]
    assert old != pre_gate


# --- the diff Claude shows for an edited send comes from the script, not from recall ---
# 2026-09-23 and 2026-10-21: a draft-vs-sent diff was described by eye, once only after Nick
# asked for it. The edits are now computed here, so a recalled diff has nothing to replace.

EDIT_DRAFT = ("TO: casey@example.com\nSUBJECT: Re: Intro\nBODY:\nHi Casey,\n\n"
              "At this stage, I am focused on product roles.\n\nThanks!\nNick\n")
EDIT_SENT = ("Hi Casey,\n\nAt the seed stage (where I am looking), I am focused on "
             "product roles.\n\nThanks!\nNick\n")
EDIT = {"op": "replace", "draft": "this stage,", "sent": "the seed stage (where I am looking),"}


def test_edited_confirm_reports_word_level_edits(store):
    cid = dc.capture(EDIT_DRAFT)
    meta = dc.confirm(cid, "edited", sent_text=EDIT_SENT, no_lesson="test")
    assert meta["edits"] == [EDIT]
    assert dc.read_meta(cid)["edits"] == [EDIT]
    assert dc.word_edits("a b c", "a c d") == [{"op": "delete", "draft": "b", "sent": ""},
                                                {"op": "insert", "draft": "", "sent": "d"}]


def test_lesson_refusal_names_the_edits(store):
    cid = dc.capture(EDIT_DRAFT)
    with pytest.raises(dc.CaptureError) as exc:
        dc.confirm(cid, "edited", sent_text=EDIT_SENT)
    assert json.dumps([EDIT], ensure_ascii=False) in str(exc.value)


def test_layout_only_edit_has_no_edits(store):
    cid = dc.capture(EDIT_DRAFT)
    reflowed = dc.body_of(EDIT_DRAFT).replace("\n\n", "\n").replace(" I am", "\nI am") + "\n\n"
    assert dc.confirm(cid, "edited", sent_text=reflowed)["edits"] == []


def test_a_confirmation_that_is_not_an_edit_carries_no_edits(store):
    cid = dc.capture(EDIT_DRAFT)
    dc.confirm(cid, "edited", sent_text=EDIT_SENT, no_lesson="test")
    assert "edits" not in dc.confirm(cid, "not-sent", force=True)



@pytest.mark.parametrize("subject", ["Re: Re: Intro", "RE: Fwd: Intro", "Fw: re:  Intro", "Intro"])
def test_thread_subject_strips_every_reply_prefix(subject):
    """Final review 596.F3: "Re: Re:" kept one prefix, so a revised reply looked like a
    different thread and escaped the lesson requirement."""
    assert dc._thread_subject(subject) == "intro"


def test_a_forced_reconfirmation_clears_the_old_lesson_provenance(store):
    """Final review 615.F2: a --force replacement kept the superseded outcome's
    lesson_ref / no_lesson_reason, so the record claimed provenance it no longer had."""
    cid = dc.capture(DRAFT)
    dc.confirm(cid, "edited", sent_text="Hi Casey,\n\nRewritten entirely.\n",
               no_lesson="first outcome")
    meta = dc.confirm(cid, "not-sent", force=True)
    assert "no_lesson_reason" not in meta and "lesson_ref" not in meta
    meta = dc.confirm(cid, "edited", sent_text="Hi Casey,\n\nRewritten again.\n",
                      no_lesson="second outcome", force=True)
    assert meta["no_lesson_reason"] == "second outcome"
    assert "lesson_ref" not in meta
