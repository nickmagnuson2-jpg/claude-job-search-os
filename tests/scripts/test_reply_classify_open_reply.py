"""reply_classify.open_reply: is the contact's newest directional entry an unanswered reply?

Origin 2026-11-02: one reply could be logged twice (by /follow-up, then /networking or
/remember, or by a second /follow-up run), and day_check counted it twice. Both writers ask
open_reply before logging a reply. Fixture entries are placeholders.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from reply_classify import open_reply  # noqa: E402


def test_an_unanswered_reply_is_returned():
    assert open_reply(["#### 2026-10-02 | email (inbound) | Replied", "", "> text"]) == \
        ("2026-10-02", "email (inbound)", "Replied")


def test_a_send_or_two_way_after_it_closes_it():
    reply = "#### 2026-10-02 | email (inbound) | Replied"
    assert open_reply(["#### 2026-10-03 | email (outbound) | Answered", reply]) is None
    assert open_reply(["#### 2026-10-03 | call (two-way) | Talked", reply]) is None


def test_plain_entries_are_skipped_not_treated_as_sends():
    reply = "#### 2026-10-02 | email (inbound) | Replied"
    assert open_reply(["#### 2026-10-03 | coffee | Met", reply]) == \
        ("2026-10-02", "email (inbound)", "Replied")


def test_newest_is_by_date_not_position():
    lines = ["#### 2026-10-01 | email (outbound) | Old send, listed first",
             "#### 2026-10-05 | email (inbound) | Newer reply, listed second"]
    assert open_reply(lines) == ("2026-10-05", "email (inbound)", "Newer reply, listed second")


def test_on_the_same_date_the_entry_nearer_the_top_is_newest():
    lines = ["#### 2026-10-05 | email (inbound) | Top",
             "#### 2026-10-05 | email (outbound) | Below"]
    assert open_reply(lines) == ("2026-10-05", "email (inbound)", "Top")
    assert open_reply(list(reversed(lines))) is None


def test_no_directional_entry_means_nothing_open():
    assert open_reply([]) is None
    assert open_reply(["#### 2026-10-05 | coffee | Met", "not an entry"]) is None


def test_the_legacy_bare_inbound_type_counts_as_a_reply():
    assert open_reply(["#### 2026-10-05 | inbound | Old style"]) == ("2026-10-05", "inbound", "Old style")
