"""Tests for open_draft.py's paste paths (infrastructure-defects D2).

A LinkedIn draft (no TO, no SUBJECT) used to fall into Gmail compose mode, open a window,
put nothing on the clipboard while the previous draft was still there, and consume itself
anyway. Three things now hold: such a draft opens nothing; a copy only counts if the
clipboard reads back the exact body; and a paste-mode draft is consumed only after that.
"""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import open_draft as od  # noqa: E402


class _Run:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout = rc, out


def _fake_clipboard(monkeypatch, pbcopy_rc=0, readback=None):
    """pbcopy stores the text; pbpaste returns `readback` if given, else what was stored."""
    store = {}

    def run(cmd, input=None, **kw):
        if cmd == ["pbcopy"]:
            store["text"] = input
            return _Run(pbcopy_rc)
        return _Run(0, store.get("text", "") if readback is None else readback)
    monkeypatch.setattr(od.subprocess, "run", run)


def test_a_copy_that_reads_back_exactly_counts(monkeypatch):
    _fake_clipboard(monkeypatch)
    assert od.copy_text_to_clipboard("Hi Casey\n") is True


def test_a_copy_that_reads_back_different_text_does_not_count(monkeypatch):
    """The live failure: the clipboard still held the previous draft."""
    _fake_clipboard(monkeypatch, readback="the previous email")
    assert od.copy_text_to_clipboard("Hi Casey\n") is False


def test_a_failed_pbcopy_does_not_count(monkeypatch):
    _fake_clipboard(monkeypatch, pbcopy_rc=1)
    assert od.copy_text_to_clipboard("Hi Casey\n") is False


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """Run main() on a draft; report what it opened, recorded and consumed."""
    def go(text, copied=True):
        draft = tmp_path / "pending.txt"
        draft.write_text(text, encoding="utf-8")
        seen = {"opened": [], "recorded": 0, "consumed": 0}
        monkeypatch.setattr(od, "DRAFT_FILE", str(draft))
        monkeypatch.setattr(od.webbrowser, "open", seen["opened"].append)
        monkeypatch.setattr(od, "copy_text_to_clipboard", lambda body: copied)
        monkeypatch.setattr(od, "record_draft", lambda: seen.__setitem__("recorded", 1))
        monkeypatch.setattr(od, "consume_draft", lambda: seen.__setitem__("consumed", 1))
        od.main()
        return seen
    return go


LINKEDIN = "TO:\nSUBJECT:\nBODY:\nHi Casey, saw your post on pricing.\n"
REPLY = "TO: casey@example.com\nSUBJECT: Re: Following up\nBODY:\nThanks Casey\n"


def test_a_draft_with_no_recipient_and_no_subject_opens_nothing(run_main, capsys):
    seen = run_main(LINKEDIN)
    assert seen["opened"] == []
    assert seen["recorded"] == 1 and seen["consumed"] == 1
    assert "PASTE MODE" in capsys.readouterr().out


@pytest.mark.parametrize("text", [LINKEDIN, REPLY])
def test_a_paste_draft_is_kept_when_the_clipboard_does_not_hold_it(run_main, capsys, text):
    seen = run_main(text, copied=False)
    assert seen["consumed"] == 0 and seen["recorded"] == 0
    out = capsys.readouterr().out
    assert "NOT consumed" in out and "Body below" in out


@pytest.mark.parametrize("head", ["TO: casey@example.com\nSUBJECT:\n",
                                  "TO:\nCC: casey@example.com\nSUBJECT:\n"])
def test_a_draft_with_a_recipient_or_cc_still_opens_compose(run_main, head):
    seen = run_main(head + "BODY:\nHi Casey\n")
    assert len(seen["opened"]) == 1 and seen["consumed"] == 1


def test_an_empty_body_never_counts_as_copied(monkeypatch):
    """An empty clipboard 'matches' an empty body exactly; that is not a delivery."""
    _fake_clipboard(monkeypatch)
    assert od.copy_text_to_clipboard("") is False
    assert od.copy_text_to_clipboard("  \n") is False


# A reply cannot be put into a Gmail thread by URL, so reply mode used to open
# nothing and only print instructions. On 2026-10-28 that read as "the draft is not
# in Gmail". Now it opens a Gmail search for the thread once the body is on the
# clipboard, and says plainly that nothing is in Drafts.

def test_a_reply_opens_a_search_for_its_thread_not_compose(run_main, capsys):
    seen = run_main(REPLY)
    assert len(seen["opened"]) == 1
    url = seen["opened"][0]
    assert url.startswith("https://mail.google.com/mail/u/0/#search/")
    assert "view=cm" not in url
    from urllib.parse import unquote
    q = unquote(url.split("#search/", 1)[1])
    assert '"Following up"' in q and "casey@example.com" in q
    out = capsys.readouterr().out
    assert "not in Gmail Drafts" in out
    assert seen["consumed"] == 1


def test_a_reply_without_a_recipient_searches_by_subject_alone(run_main):
    seen = run_main("TO:\nSUBJECT: Re: Congrats on the launch\nBODY:\nHi\n")
    from urllib.parse import unquote
    q = unquote(seen["opened"][0].split("#search/", 1)[1])
    assert '"Congrats on the launch"' in q and "to:" not in q


def test_a_reply_whose_body_did_not_reach_the_clipboard_opens_nothing(run_main):
    seen = run_main(REPLY, copied=False)
    assert seen["opened"] == []


@pytest.mark.parametrize("to, expected", [
    ("Doe, Jane <jane@example.com>, b@example.com", "jane@example.com"),
    ("Jane Doe <jane@example.com>", "jane@example.com"),
    ("casey@example.com, b@example.com", "casey@example.com"),
])
def test_the_thread_search_narrows_to_the_first_parsed_address(to, expected):
    from urllib.parse import unquote
    q = unquote(od.thread_search_url(to, "Re: Thanks").split("#search/", 1)[1])
    assert f"(from:{expected} OR to:{expected})" in q
    assert "Doe" not in q and "<" not in q


def test_a_recipient_with_no_address_is_not_used_to_narrow():
    from urllib.parse import unquote
    q = unquote(od.thread_search_url("Jane Doe", "Re: Thanks").split("#search/", 1)[1])
    assert q == 'subject:"Thanks"'


def test_a_quoted_subject_keeps_every_word():
    from urllib.parse import unquote
    q = unquote(od.thread_search_url("", 'Re: He said "yes" today').split("#search/", 1)[1])
    assert q == "subject:(He said yes today)"


@pytest.mark.parametrize("subject", ["Re: Re: Thanks for today", "RE: Thanks for today",
                                     "Fwd: Thanks for today", "re : Thanks for today"])
def test_reply_and_forward_prefixes_are_stripped(subject):
    from urllib.parse import unquote
    q = unquote(od.thread_search_url("", subject).split("#search/", 1)[1])
    assert q == 'subject:"Thanks for today"'
