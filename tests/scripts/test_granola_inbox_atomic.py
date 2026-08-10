"""Tests for prepend_inbox_entries in tools/granola_auto_debrief.py.

The auto-debrief cron prepends debrief snippets to data/inbox.md. Before the
fable-audit Theme 3 fix it used a plain write_text, so a crash mid-write could
truncate the user's inbox. The write must now be atomic (temp + os.replace).
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.granola_auto_debrief import prepend_inbox_entries

INBOX_HEADER = (
    "# Inbox\n\n"
    "<!-- Items captured via /remember. Review and route to appropriate files periodically. -->\n"
)


def test_prepends_after_header(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(INBOX_HEADER + "\n- existing item\n", encoding="utf-8")

    prepend_inbox_entries(inbox, ["- NEW entry"])

    text = inbox.read_text(encoding="utf-8")
    assert "# Inbox" in text
    # New entry sits above the pre-existing item.
    assert text.index("- NEW entry") < text.index("- existing item")
    # Header comment is preserved and still above the new entry.
    assert text.index("Items captured via") < text.index("- NEW entry")


def test_creates_inbox_when_absent(tmp_path):
    inbox = tmp_path / "inbox.md"
    prepend_inbox_entries(inbox, ["- first ever entry"])
    text = inbox.read_text(encoding="utf-8")
    assert text.startswith("# Inbox")
    assert "- first ever entry" in text


def test_no_tmp_orphan_after_success(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(INBOX_HEADER, encoding="utf-8")
    prepend_inbox_entries(inbox, ["- entry"])
    # The .tmp sibling must be gone (renamed onto the target, not left behind).
    assert not (tmp_path / "inbox.md.tmp").exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_crash_mid_write_preserves_original(tmp_path, monkeypatch):
    """If os.replace fails (simulating a crash between temp-write and rename),
    the original inbox must be untouched — the whole point of atomicity."""
    inbox = tmp_path / "inbox.md"
    original = INBOX_HEADER + "\n- precious existing data\n"
    inbox.write_text(original, encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        prepend_inbox_entries(inbox, ["- entry that should not land"])

    # Original content intact — no truncation, no partial write.
    assert inbox.read_text(encoding="utf-8") == original
