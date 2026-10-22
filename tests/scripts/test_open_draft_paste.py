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
