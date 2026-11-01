"""Gmail API-layer tests for tools/gmail_fetch.py.

Covers fetch_new_messages, fetch_labeled_messages, search_messages, _seed_history_id,
write_messages, gmail_label_id and _mailbox_marker against an in-memory fake of the
Gmail service. Nothing here touches the network, a token, or a credential file.
"""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import gmail_fetch  # noqa: E402


# ── Fake Gmail service ─────────────────────────────────────────────────────────


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


def _serve(queue):
    def run():
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return run


class _History:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.history_calls.append(dict(kw))
        return _Req(_serve(self.svc.history_pages))


class _Messages:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.list_calls.append(dict(kw))
        return _Req(_serve(self.svc.list_pages))

    def get(self, **kw):
        self.svc.get_calls.append(dict(kw))
        msg_id = kw["id"]

        def run():
            if msg_id in self.svc.fail_ids:
                raise RuntimeError("transient 500")
            if msg_id in self.svc.messages_by_id:
                return self.svc.messages_by_id[msg_id]
            return {"id": msg_id, "labelIds": list(self.svc.default_labels)}
        return _Req(run)


class FakeGmail:
    """users().history().list / users().messages().list serve queued pages in order
    (an Exception item is raised); users().messages().get returns messages_by_id[id],
    else a stub carrying default_labels; users().getProfile returns `profile`."""

    def __init__(self, history_pages=(), list_pages=(), messages_by_id=None,
                 fail_ids=(), default_labels=("L1",), profile=None):
        self.history_pages = list(history_pages)
        self.list_pages = list(list_pages)
        self.messages_by_id = dict(messages_by_id or {})
        self.fail_ids = set(fail_ids)
        self.default_labels = list(default_labels)
        self.profile = profile
        self.history_calls = []
        self.list_calls = []
        self.get_calls = []
        self.profile_calls = 0

    def users(self):
        return self

    def history(self):
        return _History(self)

    def messages(self):
        return _Messages(self)

    def getProfile(self, userId):
        self.profile_calls += 1
        return _Req(lambda: self.profile)


class ExplodingGmail:
    """Any API access is a test failure."""

    def users(self):
        raise AssertionError("Gmail service must not be called")


def _added(*ids):
    return {"messagesAdded": [{"message": {"id": i}} for i in ids]}


def _label_added(msg_id, *labels):
    return {"labelsAdded": [{"message": {"id": msg_id}, "labelIds": list(labels)}]}


def _b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


def _full_msg(msg_id, labels=("SENT",), body="hello", sender="a@example.com",
              subject="Hi", date="Mon, 1 Jan 2026 09:00:00 +0000", snippet="snip"):
    headers = [{"name": "From", "value": sender},
               {"name": "Date", "value": date}]
    if subject is not None:
        headers.append({"name": "Subject", "value": subject})
    return {"id": msg_id, "labelIds": list(labels), "snippet": snippet,
            "internalDate": "1767258000000",
            "payload": {"headers": headers, "mimeType": "text/plain",
                        "body": {"data": _b64(body)}}}


# ── gmail_label_id ─────────────────────────────────────────────────────────────


@pytest.fixture
def labels_conf(tmp_path, monkeypatch):
    conf = tmp_path / ".gmail-labels.conf"
    monkeypatch.setattr(gmail_fetch, "GMAIL_LABELS_CONF", conf)
    for k in ("JOB_SEARCH", "PERSONAL", "#JOB_SEARCH"):
        monkeypatch.delenv(f"GMAIL_{k}_LABEL_ID", raising=False)
    return conf


def test_label_id_reads_conf_and_skips_comment_lines(labels_conf):
    labels_conf.write_text("# a comment\n\njob_search=Label_A\npersonal = Label_B \n",
                           encoding="utf-8")
    assert gmail_fetch.gmail_label_id("job_search") == "Label_A"
    assert gmail_fetch.gmail_label_id("personal") == "Label_B"


def test_a_commented_line_never_configures_a_key_even_one_spelled_with_hash(labels_conf):
    # The comment skip is what stops "#job_search=X" from answering a lookup for the key
    # "#job_search"; without it the partition would match and return the commented value.
    labels_conf.write_text("#job_search=Label_OLD\n", encoding="utf-8")
    assert gmail_fetch.gmail_label_id("#job_search") is None


def test_label_id_env_wins_over_conf(labels_conf, monkeypatch):
    labels_conf.write_text("job_search=Label_A\n", encoding="utf-8")
    monkeypatch.setenv("GMAIL_JOB_SEARCH_LABEL_ID", "Label_ENV")
    assert gmail_fetch.gmail_label_id("job_search") == "Label_ENV"


def test_label_id_missing_conf_is_none(labels_conf):
    assert gmail_fetch.gmail_label_id("job_search") is None


# ── _seed_history_id ───────────────────────────────────────────────────────────


def test_seed_uses_newest_message_history_id():
    svc = FakeGmail(list_pages=[{"messages": [{"id": "m9"}, {"id": "m8"}]}],
                    messages_by_id={"m9": {"id": "m9", "historyId": 4242}},
                    profile={"historyId": "1111"})
    assert gmail_fetch._seed_history_id(svc, count=20) == "4242"
    assert svc.list_calls == [{"userId": "me", "maxResults": 20}]
    assert svc.get_calls == [{"userId": "me", "id": "m9", "format": "metadata"}]
    assert svc.profile_calls == 0


def test_seed_falls_back_to_profile_on_empty_mailbox():
    svc = FakeGmail(list_pages=[{}], profile={"historyId": 777})
    assert gmail_fetch._seed_history_id(svc) == "777"
    assert svc.profile_calls == 1
    assert svc.get_calls == []


def test_seed_returns_one_and_warns_on_error(capsys):
    svc = FakeGmail(list_pages=[RuntimeError("boom")])
    assert gmail_fetch._seed_history_id(svc) == "1"
    assert "could not seed historyId: boom" in capsys.readouterr().err


# ── fetch_new_messages ─────────────────────────────────────────────────────────


def test_no_history_id_returns_empty_list_without_calling_gmail():
    got = gmail_fetch.fetch_new_messages(ExplodingGmail(), {})
    assert got == [] and got is not None


def test_unscoped_sync_requests_only_message_added_and_no_label():
    svc = FakeGmail(history_pages=[{"history": [_added("m1")], "historyId": "200"}])
    gmail_fetch.fetch_new_messages(svc, {"historyId": "100"})
    call = svc.history_calls[0]
    assert call["historyTypes"] == ["messageAdded"]
    assert "labelId" not in call
    assert call["maxResults"] == gmail_fetch.HISTORY_PAGE_SIZE


def test_scoped_sync_requests_label_added_and_label_filter():
    svc = FakeGmail(history_pages=[{"historyId": "200"}])
    gmail_fetch.fetch_new_messages(svc, {"historyId": "100"}, label_id="L1")
    call = svc.history_calls[0]
    assert call["historyTypes"] == ["messageAdded", "labelAdded"]
    assert call["labelId"] == "L1"


def test_expired_history_id_takes_the_new_cursor_from_the_newest_message(capsys):
    svc = FakeGmail(history_pages=[RuntimeError("HttpError 404 Not Found")],
                    list_pages=[{"messages": [{"id": "m5"}]}, {"messages": []}],
                    messages_by_id={"m5": {"id": "m5", "historyId": "9000"}})
    state = {"historyId": "100"}
    got = gmail_fetch.fetch_new_messages(svc, state)
    assert got == [] and got is not None
    assert state["historyId"] == "9000"
    assert svc.list_calls[0]["maxResults"] == 20
    assert "historyId expired" in capsys.readouterr().err


def test_other_history_errors_propagate():
    svc = FakeGmail(history_pages=[RuntimeError("500 backend")])
    with pytest.raises(RuntimeError):
        gmail_fetch.fetch_new_messages(svc, {"historyId": "100"})


def test_duplicate_message_added_ids_are_fetched_once():
    svc = FakeGmail(history_pages=[{"history": [_added("m1", "m1"), _added("m1", "m2")],
                                    "historyId": "200"}])
    state = {"historyId": "100", "retry_ids": {"m2": 1}}
    got = gmail_fetch.fetch_new_messages(svc, state)
    assert [m["id"] for m in got] == ["m2", "m1"]
    assert [c["id"] for c in svc.get_calls] == ["m2", "m1"]


def test_label_added_with_target_label_is_fetched():
    svc = FakeGmail(history_pages=[{"history": [_label_added("m7", "L1")],
                                    "historyId": "200"}])
    got = gmail_fetch.fetch_new_messages(svc, {"historyId": "100"}, label_id="L1")
    assert [m["id"] for m in got] == ["m7"]


def test_label_added_for_another_label_is_not_fetched():
    # The message carries L1 in format=full, so only the labelsAdded filter keeps it out.
    svc = FakeGmail(history_pages=[{"history": [_label_added("m7", "OTHER")],
                                    "historyId": "200"}])
    got = gmail_fetch.fetch_new_messages(svc, {"historyId": "100"}, label_id="L1")
    assert got == []
    assert svc.get_calls == []


def test_label_added_duplicates_are_fetched_once():
    svc = FakeGmail(history_pages=[{"history": [
        _added("m1"),
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["L1"]},
                         {"message": {"id": "m3"}, "labelIds": ["L1"]},
                         {"message": {"id": "m3"}, "labelIds": ["L1"]}]},
    ], "historyId": "200"}])
    got = gmail_fetch.fetch_new_messages(svc, {"historyId": "100"}, label_id="L1")
    assert [m["id"] for m in got] == ["m1", "m3"]
    assert [c["id"] for c in svc.get_calls] == ["m1", "m3"]


def test_missing_history_id_in_response_keeps_cursor():
    svc = FakeGmail(history_pages=[{"history": [_added("m1")]}])
    state = {"historyId": "100"}
    gmail_fetch.fetch_new_messages(svc, state)
    assert state["historyId"] == "100"


def test_empty_history_returns_empty_list():
    svc = FakeGmail(history_pages=[{"historyId": "200"}])
    state = {"historyId": "100"}
    got = gmail_fetch.fetch_new_messages(svc, state)
    assert got == [] and got is not None
    assert state["historyId"] == "200"


def test_message_without_target_label_is_dropped_and_its_retry_cleared():
    svc = FakeGmail(history_pages=[{"history": [_added("m1", "m2")], "historyId": "200"}],
                    messages_by_id={"m1": {"id": "m1", "labelIds": ["L1"]},
                                    "m2": {"id": "m2", "labelIds": ["INBOX"]}})
    state = {"historyId": "100", "retry_ids": {"m2": 2}}
    got = gmail_fetch.fetch_new_messages(svc, state, label_id="L1")
    assert [m["id"] for m in got] == ["m1"]
    assert state["retry_ids"] == {}


def test_failed_fetch_warns(capsys):
    svc = FakeGmail(history_pages=[{"history": [_added("m2")], "historyId": "200"}],
                    fail_ids={"m2"})
    state = {"historyId": "100"}
    assert gmail_fetch.fetch_new_messages(svc, state) == []
    assert "could not fetch message m2: transient 500" in capsys.readouterr().err
    assert state["retry_ids"] == {"m2": 1}


# ── write_messages ─────────────────────────────────────────────────────────────


def test_write_messages_reports_written_and_senders(tmp_path, capsys):
    state = {"retry_ids": {"m1": 1}}
    written, senders = gmail_fetch.write_messages(
        [_full_msg("m1", sender="Pat <pat@example.com>")], state, tmp_path, dry_run=False)
    assert written == 1
    assert senders == ["Pat <pat@example.com>"]
    assert state["retry_ids"] == {}
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert f"  Wrote: {files[0].name}" in capsys.readouterr().out


def test_write_messages_dry_run_writes_and_counts_nothing(tmp_path, capsys):
    state = {"retry_ids": {"m1": 1}}
    written, senders = gmail_fetch.write_messages(
        [_full_msg("m1")], state, tmp_path, dry_run=True)
    assert (written, senders) == (0, [])
    assert state["retry_ids"] == {"m1": 1}
    assert list(tmp_path.glob("*.md")) == []
    assert "Wrote:" not in capsys.readouterr().out


def test_write_messages_failure_warns(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(gmail_fetch, "write_inbox_file", boom)
    gmail_fetch.write_messages([_full_msg("m4")], {}, tmp_path, dry_run=False)
    assert "could not process message m4: disk full" in capsys.readouterr().err


# ── fetch_labeled_messages ─────────────────────────────────────────────────────


def test_backfill_reads_every_page_and_fetches_full_content(capsys):
    svc = FakeGmail(list_pages=[{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t2"},
                                {"messages": [{"id": "c"}]}])
    got = gmail_fetch.fetch_labeled_messages(svc, "L1")
    assert [m["id"] for m in got] == ["a", "b", "c"]
    assert svc.list_calls[0] == {"userId": "me", "labelIds": ["L1"], "maxResults": 100}
    assert svc.list_calls[1]["pageToken"] == "t2"
    assert [c["format"] for c in svc.get_calls] == ["full"] * 3
    out = capsys.readouterr().out
    assert "Backfill: found 3 message(s) with label." in out


def test_backfill_since_date_becomes_gmail_query():
    svc = FakeGmail(list_pages=[{"messages": []}])
    gmail_fetch.fetch_labeled_messages(svc, "L1", since_date="2026-03-04")
    assert svc.list_calls[0]["q"] == "after:2026/03/04"


def test_backfill_max_messages_caps_and_stops_paging():
    svc = FakeGmail(list_pages=[{"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                                 "nextPageToken": "t2"},
                                {"messages": [{"id": "d"}]}])
    got = gmail_fetch.fetch_labeled_messages(svc, "L1", max_messages=2)
    assert [m["id"] for m in got] == ["a", "b"]
    assert len(svc.list_calls) == 1


def test_backfill_under_cap_keeps_paging():
    svc = FakeGmail(list_pages=[{"messages": [{"id": "a"}], "nextPageToken": "t2"},
                                {"messages": [{"id": "b"}]}])
    got = gmail_fetch.fetch_labeled_messages(svc, "L1", max_messages=5)
    assert [m["id"] for m in got] == ["a", "b"]
    assert len(svc.list_calls) == 2


def test_backfill_progress_lines_every_ten_and_at_end(capsys):
    ids = [{"id": f"m{i}"} for i in range(12)]
    svc = FakeGmail(list_pages=[{"messages": ids}])
    gmail_fetch.fetch_labeled_messages(svc, "L1")
    progress = [l for l in capsys.readouterr().out.splitlines() if "Fetched" in l]
    assert progress == ["  Fetched 10/12...", "  Fetched 12/12..."]


def test_backfill_skips_failed_message_and_warns(capsys):
    svc = FakeGmail(list_pages=[{"messages": [{"id": "a"}, {"id": "b"}]}], fail_ids={"a"})
    got = gmail_fetch.fetch_labeled_messages(svc, "L1")
    assert [m["id"] for m in got] == ["b"]
    assert "could not fetch message a: transient 500" in capsys.readouterr().err


# ── _mailbox_marker ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("labels,expected", [
    (["DRAFT", "SENT"], "DRAFT — NOT SENT"),
    (["SENT", "INBOX"], "SENT"),
    (["INBOX", "UNREAD"], "INBOX"),
    (["SPAM"], "SPAM"),
    (["TRASH"], "TRASH"),
    (["CATEGORY_UPDATES"], "unknown"),
    ([], "unknown"),
    (None, "unknown"),
])
def test_mailbox_marker(labels, expected):
    assert gmail_fetch._mailbox_marker(labels) == expected


# ── search_messages ────────────────────────────────────────────────────────────


def test_search_no_results_returns_zero(capsys):
    svc = FakeGmail(list_pages=[{}])
    n = gmail_fetch.search_messages(svc, "from:x", None, 5, show_body=False)
    assert n == 0 and n is not None
    assert capsys.readouterr().out == "Gmail search: q='from:x' in all mail — 0 result(s)\n\n"
    assert svc.list_calls == [{"userId": "me", "q": "from:x", "maxResults": 5}]


def test_search_metadata_output_is_exact(capsys):
    svc = FakeGmail(list_pages=[{"messages": [{"id": "m1"}, {"id": "m2"}]}],
                    messages_by_id={
                        "m1": _full_msg("m1", labels=["SENT"], sender="a@example.com",
                                        subject="Hello", date="D1", snippet="first"),
                        "m2": _full_msg("m2", labels=["DRAFT"], sender="b@example.com",
                                        subject=None, date="D2", snippet="x" * 250),
                    })
    n = gmail_fetch.search_messages(svc, "hello", "L1", 10, show_body=False)
    assert n == 2
    assert svc.list_calls[0]["labelIds"] == ["L1"]
    assert svc.get_calls[0] == {"userId": "me", "id": "m1", "format": "metadata",
                                "metadataHeaders": ["From", "To", "Subject", "Date"]}
    expected = (
        "Gmail search: q='hello' in label L1 — 2 result(s)\n\n"
        "  id:      m1\n"
        "  Mailbox: SENT\n"
        "  Date:    D1\n"
        "  From:    a@example.com\n"
        "  Subject: Hello\n"
        "  Snippet: first\n"
        "\n"
        "  id:      m2\n"
        "  Mailbox: DRAFT — NOT SENT\n"
        "  Date:    D2\n"
        "  From:    b@example.com\n"
        "  Subject: (no subject)\n"
        f"  Snippet: {'x' * 200}\n"
        "\n"
    )
    assert capsys.readouterr().out == expected


def test_search_show_body_prints_body_and_truncation(capsys):
    long_body = "line one\n" + "y" * (gmail_fetch.MAX_BODY_CHARS + 50)
    svc = FakeGmail(list_pages=[{"messages": [{"id": "m1"}, {"id": "m2"}]}],
                    messages_by_id={
                        "m1": _full_msg("m1", body="short\nsecond", date="D1",
                                        snippet="s1"),
                        "m2": _full_msg("m2", body=long_body, date="D2", snippet="s2"),
                    })
    n = gmail_fetch.search_messages(svc, "q", None, 10, show_body=True)
    assert n == 2
    assert svc.get_calls[0] == {"userId": "me", "id": "m1", "format": "full"}
    out = capsys.readouterr().out
    first, second = out.split("  id:      m2\n")
    assert first.endswith(
        "  Snippet: s1\n  --- body ---\n  short\n  second\n\n")
    assert "truncated" not in first
    truncated_line = f"  ... [truncated at {gmail_fetch.MAX_BODY_CHARS} chars]\n"
    assert second.endswith(truncated_line + "\n")
    assert "  --- body ---\n  line one\n" in second
    assert f"  {'y' * (gmail_fetch.MAX_BODY_CHARS - len('line one') - 1)}\n" in second


# ── expired cursor recovers the gap; writes are idempotent by Gmail id (2026-10-31) ──
# Codex review 1.F1 and 1.F2 on fixes-1030. Before this, an expired cursor jumped to the
# newest message and returned nothing, so the mail in between was never written; and a run
# killed after writing files but before saving state rewrote the same messages, suffixed.

from datetime import datetime, timedelta  # noqa: E402


def _expired_gmail(gap_pages, gap_msgs):
    """History raises 404. The first messages.list is the reseed (newest message, whose
    historyId becomes the new cursor); the rest are the gap listing."""
    by_id = {"newest": {"id": "newest", "historyId": "9000"}}
    by_id.update({m["id"]: m for m in gap_msgs})
    return FakeGmail(history_pages=[RuntimeError("HttpError 404 Not Found")],
                     list_pages=[{"messages": [{"id": "newest"}]}, *gap_pages],
                     messages_by_id=by_id)


def _after(call):
    return int(call["q"].removeprefix("after:"))


def test_an_expired_cursor_returns_the_mail_in_the_gap(capsys):
    last = datetime.now().replace(microsecond=0) - timedelta(hours=5)
    svc = _expired_gmail(
        [{"messages": [{"id": "g1"}], "nextPageToken": "p2"}, {"messages": [{"id": "g2"}]}],
        [_full_msg("g1", labels=("L1",)), _full_msg("g2", labels=("L1",))])
    state = {"historyId": "100", "last_refresh": last.isoformat()}
    got = gmail_fetch.fetch_new_messages(svc, state, label_id="L1")
    assert [m["id"] for m in got] == ["g1", "g2"]
    assert state["historyId"] == "9000"
    gap = svc.list_calls[1:]
    assert len(gap) == 2
    assert _after(gap[0]) == int((last - timedelta(hours=1)).timestamp())
    assert gap[0]["labelIds"] == ["L1"]
    assert gap[1]["pageToken"] == "p2"
    assert "recovering mail since" in capsys.readouterr().err


def _days_ago(n):
    # The same local-calendar arithmetic as _gap_start, so a DST change inside the window
    # does not move the expected value by an hour (Fable round 2, F3/F4).
    return int((datetime.now() - timedelta(days=n)).timestamp())


def test_the_gap_looks_back_at_most_30_days_and_says_so(capsys):
    svc = _expired_gmail([{"messages": []}], [])
    state = {"historyId": "100",
             "last_refresh": (datetime.now() - timedelta(days=90)).isoformat()}
    assert gmail_fetch.fetch_new_messages(svc, state) == []
    assert abs(_after(svc.list_calls[1]) - _days_ago(30)) < 60
    assert "labelIds" not in svc.list_calls[1]
    assert "older mail is not recovered" in capsys.readouterr().err


def test_without_a_last_run_time_the_gap_is_7_days_and_says_so(capsys):
    svc = _expired_gmail([{"messages": []}], [])
    state = {"historyId": "100"}
    gmail_fetch.fetch_new_messages(svc, state)
    assert abs(_after(svc.list_calls[1]) - _days_ago(7)) < 60
    assert "no last_refresh" in capsys.readouterr().err


def test_an_id_already_written_is_not_rewritten_after_its_file_is_gone(tmp_path, capsys):
    state = {"written_ids": {"m1": datetime.now().isoformat()}}
    written, _ = gmail_fetch.write_messages([_full_msg("m1")], state, tmp_path, dry_run=False)
    assert written == 0
    assert list(tmp_path.glob("*.md")) == []
    assert "Skipped m1: already written" in capsys.readouterr().out


def test_a_repeat_within_one_batch_is_written_once(tmp_path):
    state = {}
    written, senders = gmail_fetch.write_messages(
        [_full_msg("m1"), _full_msg("m1")], state, tmp_path, dry_run=False)
    assert written == 1 and senders == ["a@example.com"]
    assert len(list(tmp_path.glob("*.md"))) == 1
    assert list(state["written_ids"]) == ["m1"]


def test_written_ids_drops_entries_older_than_30_days(tmp_path):
    now = datetime.now()
    keep = (now - timedelta(days=29)).isoformat()
    state = {"written_ids": {"old": (now - timedelta(days=31)).isoformat(), "new": keep,
                             "junk": "not a date"}}
    gmail_fetch.write_messages([], state, tmp_path, dry_run=False)
    assert state["written_ids"] == {"new": keep}


def test_a_gap_id_already_queued_or_listed_twice_is_fetched_once():
    svc = _expired_gmail([{"messages": [{"id": "g1"}], "nextPageToken": "p2"},
                          {"messages": [{"id": "g1"}, {"id": "g2"}]}],
                         [_full_msg("g1", labels=("L1",)), _full_msg("g2", labels=("L1",))])
    state = {"historyId": "100", "last_refresh": datetime.now().isoformat(),
             "retry_ids": {"g2": 1}}
    got = gmail_fetch.fetch_new_messages(svc, state, label_id="L1")
    assert [m["id"] for m in got] == ["g2", "g1"]
    assert [c["id"] for c in svc.get_calls if c["id"] != "newest"] == ["g2", "g1"]


def test_a_written_message_is_recorded_with_the_time_it_was_written(tmp_path):
    before = datetime.now()
    state = {}
    gmail_fetch.write_messages([_full_msg("m1")], state, tmp_path, dry_run=False)
    assert list(state["written_ids"]) == ["m1"]
    assert before <= datetime.fromisoformat(state["written_ids"]["m1"]) <= datetime.now()


def test_a_message_without_an_id_is_written_but_never_recorded(tmp_path):
    msg = _full_msg("x")
    del msg["id"]
    state = {}
    written, _ = gmail_fetch.write_messages([msg], state, tmp_path, dry_run=False)
    assert written == 1
    assert state["written_ids"] == {}


def test_a_skipped_message_leaves_the_retry_queue(tmp_path):
    state = {"retry_ids": {"m1": 2}, "written_ids": {"m1": datetime.now().isoformat()}}
    gmail_fetch.write_messages([_full_msg("m1")], state, tmp_path, dry_run=False)
    assert state["retry_ids"] == {}


def test_a_dry_run_records_nothing_for_an_id_found_in_the_inbox(tmp_path, capsys):
    gmail_fetch.write_messages([_full_msg("m1")], {}, tmp_path, dry_run=False)
    capsys.readouterr()
    state = {}
    written, _ = gmail_fetch.write_messages([_full_msg("m1")], state, tmp_path, dry_run=True)
    assert written == 0
    assert "Skipped m1: already written" in capsys.readouterr().out
    assert state["written_ids"] == {}


def test_an_unreadable_inbox_entry_is_skipped_and_the_rest_are_read(tmp_path):
    (tmp_path / "0-first.md").mkdir()  # sorts first; reading it raises IsADirectoryError
    (tmp_path / "1-mail.md").write_text("# Email\n\n> **Message-ID:** m7\n", encoding="utf-8")
    assert gmail_fetch._existing_message_ids(tmp_path) == {"m7"}


class _HttpErrorLike(Exception):
    """Shaped like googleapiclient's HttpError: a .resp with .status, and text that includes
    the request URL."""

    def __init__(self, status, text):
        super().__init__(text)
        self.resp = type("Resp", (), {"status": status})()


def test_a_500_whose_url_contains_404_is_not_taken_for_an_expired_cursor():
    err = _HttpErrorLike(500, "<HttpError 500 when requesting .../history?startHistoryId=140472"
                              "&maxResults=500 returned Backend Error>")
    svc = FakeGmail(history_pages=[err])
    state = {"historyId": "140472"}
    with pytest.raises(_HttpErrorLike):
        gmail_fetch.fetch_new_messages(svc, state)
    assert state == {"historyId": "140472"}
    assert svc.list_calls == []


def test_an_http_error_with_status_404_is_recovered_without_404_in_its_text():
    svc = _expired_gmail([{"messages": []}], [])
    svc.history_pages = [_HttpErrorLike(404, "<HttpError when requesting .../history>")]
    state = {"historyId": "100", "last_refresh": datetime.now().isoformat()}
    assert gmail_fetch.fetch_new_messages(svc, state) == []
    assert state["historyId"] == "9000"


def test_a_non_utf8_inbox_file_is_skipped_by_the_scan_and_by_cleanup(tmp_path):
    bad = tmp_path / "0-drop.md"
    bad.write_bytes(b"\xff\xfe not utf-8")
    (tmp_path / "1-mail.md").write_text("> **Message-ID:** m7\n", encoding="utf-8")
    assert gmail_fetch._existing_message_ids(tmp_path) == {"m7"}
    assert gmail_fetch.cleanup_old_inbox_files(tmp_path, hours=0) == 0
    assert bad.exists()
