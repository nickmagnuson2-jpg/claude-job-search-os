"""Unit tests for the read-only --list mode of tools/fetch_source_email.py.

Only the pure helpers are exercised here: no Gmail API, no credentials, no network.
The mode exists because the tool previously REQUIRED --match, so there was no way to
ask "what is in the label that I have not curated yet?" -- and the intake path went
dormant after a single day of use as a result.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from fetch_source_email import captured_message_ids, partition_unpulled  # noqa: E402


HEADER = """# Email: {subject}

> **From:** {sender}
> **Date:** {date}
> **Message-ID:** {mid}
> **Label:** Example label

## My take (voice-pure)

Placeholder take.
"""


def _write(dirpath, name, mid, subject="Example subject", sender="sender@example.com"):
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        HEADER.format(subject=subject, sender=sender, date="Mon, 01 Jan 2026 00:00:00 +0000", mid=mid),
        encoding="utf-8",
    )
    return p


class TestCapturedMessageIds:
    def test_reads_message_id_from_header(self, tmp_path):
        _write(tmp_path, "a.md", "aaa111")
        assert captured_message_ids(tmp_path) == {"aaa111"}

    def test_collects_across_multiple_files(self, tmp_path):
        _write(tmp_path, "a.md", "aaa111")
        _write(tmp_path, "b.md", "bbb222")
        assert captured_message_ids(tmp_path) == {"aaa111", "bbb222"}

    def test_recurses_into_subdirectories(self, tmp_path):
        _write(tmp_path, "nested/deep/c.md", "ccc333")
        assert captured_message_ids(tmp_path) == {"ccc333"}, (
            "a subdir of curated emails must count, or its messages read as unpulled forever"
        )

    def test_missing_directory_returns_empty_not_error(self, tmp_path):
        assert captured_message_ids(tmp_path / "does-not-exist") == set()

    def test_ignores_non_markdown_files(self, tmp_path):
        (tmp_path / "note.txt").write_text("> **Message-ID:** ttt999\n", encoding="utf-8")
        assert captured_message_ids(tmp_path) == set()

    def test_file_without_message_id_contributes_nothing(self, tmp_path):
        (tmp_path / "bare.md").write_text("# Email: no header here\n", encoding="utf-8")
        assert captured_message_ids(tmp_path) == set()

    def test_undecodable_bytes_do_not_crash_the_scan(self, tmp_path):
        (tmp_path / "bad.md").write_bytes(b"\xff\xfe> **Message-ID:** zzz000\n")
        _write(tmp_path, "good.md", "aaa111")
        assert "aaa111" in captured_message_ids(tmp_path)


class TestPartitionUnpulled:
    def test_splits_on_captured_ids(self):
        metas = [{"message_id": "aaa111"}, {"message_id": "bbb222"}]
        unpulled, pulled = partition_unpulled(metas, {"aaa111"})
        assert [m["message_id"] for m in unpulled] == ["bbb222"]
        assert [m["message_id"] for m in pulled] == ["aaa111"]

    def test_preserves_input_order(self):
        metas = [{"message_id": str(i)} for i in range(6)]
        unpulled, _ = partition_unpulled(metas, {"2", "4"})
        assert [m["message_id"] for m in unpulled] == ["0", "1", "3", "5"]

    def test_everything_captured_yields_no_unpulled(self):
        metas = [{"message_id": "aaa111"}, {"message_id": "bbb222"}]
        unpulled, pulled = partition_unpulled(metas, {"aaa111", "bbb222"})
        assert unpulled == []
        assert len(pulled) == 2

    def test_empty_captured_set_leaves_everything_unpulled(self):
        metas = [{"message_id": "aaa111"}, {"message_id": "bbb222"}]
        unpulled, pulled = partition_unpulled(metas, set())
        assert len(unpulled) == 2
        assert pulled == []

    @pytest.mark.parametrize("mid", [None, "", "   "])
    def test_message_without_usable_id_counts_as_unpulled(self, mid):
        """Fail toward SHOWING the message. Marking an unidentifiable message as already
        captured hides it permanently, which is the exact failure this mode fixes."""
        unpulled, pulled = partition_unpulled([{"message_id": mid}], {"aaa111"})
        assert len(unpulled) == 1
        assert pulled == []

    def test_missing_message_id_key_counts_as_unpulled(self):
        unpulled, pulled = partition_unpulled([{"subject": "no id key"}], {"aaa111"})
        assert len(unpulled) == 1
        assert pulled == []

    def test_id_is_whitespace_trimmed_before_comparison(self):
        unpulled, pulled = partition_unpulled([{"message_id": "  aaa111  "}], {"aaa111"})
        assert unpulled == []
        assert len(pulled) == 1

    def test_empty_input_yields_two_empty_lists(self):
        assert partition_unpulled([], {"aaa111"}) == ([], [])


class TestIdentityKeyIsIdNotSubject:
    def test_same_subject_different_id_is_still_unpulled(self, tmp_path):
        """A newsletter reuses its subject across issues. Keying on subject would mark
        every later issue as already-curated and silently swallow the whole label."""
        _write(tmp_path, "issue1.md", "aaa111", subject="Weekly Digest")
        captured = captured_message_ids(tmp_path)
        metas = [{"message_id": "bbb222", "subject": "Weekly Digest"}]
        unpulled, _ = partition_unpulled(metas, captured)
        assert len(unpulled) == 1


class TestResolveSourceDir:
    def test_explicit_arg_wins(self, tmp_path):
        from fetch_source_email import resolve_source_dir

        assert resolve_source_dir(tmp_path, str(tmp_path / "elsewhere")) == tmp_path / "elsewhere"

    def test_default_is_repo_data_source_emails(self, tmp_path):
        from fetch_source_email import resolve_source_dir

        assert resolve_source_dir(tmp_path, None) == tmp_path / "data" / "source-emails"

    def test_empty_string_falls_back_to_default(self, tmp_path):
        from fetch_source_email import resolve_source_dir

        assert resolve_source_dir(tmp_path, "") == tmp_path / "data" / "source-emails"


class TestBuildListPayload:
    def _metas(self):
        return [
            {"message_id": "aaa111", "subject": "One", "sender": "a@example.com", "date": "D1"},
            {"message_id": "bbb222", "subject": "Two", "sender": "b@example.com", "date": "D2"},
            {"message_id": "ccc333", "subject": "Three", "sender": "c@example.com", "date": "D3"},
        ]

    def test_counts_are_consistent(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("Example label", self._metas(), {"aaa111"}, tmp_path)
        assert p["scanned"] == 3
        assert p["pulled_count"] == 1
        assert p["unpulled_count"] == 2
        assert p["pulled_count"] + p["unpulled_count"] == p["scanned"]

    def test_unpulled_count_matches_list_length(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("Example label", self._metas(), set(), tmp_path)
        assert p["unpulled_count"] == len(p["unpulled"]) == 3

    def test_carries_the_label_through(self, tmp_path):
        from fetch_source_email import build_list_payload

        assert build_list_payload("Example label", [], set(), tmp_path)["label"] == "Example label"

    def test_reports_captured_ids_seen(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("L", self._metas(), {"aaa111", "zzz999"}, tmp_path)
        assert p["captured_ids_seen"] == 2

    def test_flags_a_missing_source_dir(self, tmp_path):
        from fetch_source_email import build_list_payload

        real = build_list_payload("L", [], set(), tmp_path)
        missing = build_list_payload("L", [], set(), tmp_path / "nope")
        assert real["source_dir_exists"] is True
        assert missing["source_dir_exists"] is False, (
            "a typo'd source dir makes everything look unpulled; the payload must say so"
        )

    def test_each_unpulled_entry_carries_all_four_fields(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("L", self._metas(), set(), tmp_path)
        for entry in p["unpulled"]:
            assert set(entry) == {"date", "subject", "sender", "message_id"}
            assert all(entry[k] for k in entry)

    def test_pulled_messages_are_absent_from_unpulled(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("L", self._metas(), {"bbb222"}, tmp_path)
        assert [e["message_id"] for e in p["unpulled"]] == ["aaa111", "ccc333"]

    def test_empty_label_is_not_an_error(self, tmp_path):
        from fetch_source_email import build_list_payload

        p = build_list_payload("L", [], set(), tmp_path)
        assert p["scanned"] == 0 and p["unpulled_count"] == 0 and p["status"] == "ok"

    def test_payload_is_json_serializable(self, tmp_path):
        import json

        from fetch_source_email import build_list_payload

        assert json.loads(json.dumps(build_list_payload("L", self._metas(), set(), tmp_path)))


class TestRenderListText:
    def test_lists_each_unpulled_subject(self, tmp_path):
        from fetch_source_email import build_list_payload, render_list_text

        metas = [{"message_id": "aaa111", "subject": "Alpha", "sender": "a@example.com", "date": "D1"}]
        out = render_list_text(build_list_payload("L", metas, set(), tmp_path))
        assert "Alpha" in out and "a@example.com" in out and "D1" in out

    def test_says_so_when_nothing_is_new(self, tmp_path):
        from fetch_source_email import build_list_payload, render_list_text

        out = render_list_text(build_list_payload("L", [], set(), tmp_path))
        assert "nothing new in the label" in out

    def test_header_reports_both_counts(self, tmp_path):
        from fetch_source_email import build_list_payload, render_list_text

        metas = [
            {"message_id": "aaa111", "subject": "A", "sender": "a@example.com", "date": "D"},
            {"message_id": "bbb222", "subject": "B", "sender": "b@example.com", "date": "D"},
        ]
        out = render_list_text(build_list_payload("L", metas, {"aaa111"}, tmp_path))
        assert "1 unpulled / 2 scanned" in out
        assert "1 already in" in out

    def test_captured_message_is_not_rendered(self, tmp_path):
        from fetch_source_email import build_list_payload, render_list_text

        metas = [{"message_id": "aaa111", "subject": "AlreadyHave", "sender": "a@example.com", "date": "D"}]
        out = render_list_text(build_list_payload("L", metas, {"aaa111"}, tmp_path))
        assert "AlreadyHave" not in out

    def test_empty_marker_is_absent_when_there_are_unpulled_items(self, tmp_path):
        """The 'nothing new' line must be conditional. If it always renders, a label with
        real unread items reads as empty and standup silently stops surfacing them."""
        from fetch_source_email import build_list_payload, render_list_text

        metas = [{"message_id": "aaa111", "subject": "Alpha", "sender": "a@example.com", "date": "D"}]
        out = render_list_text(build_list_payload("L", metas, set(), tmp_path))
        assert "nothing new in the label" not in out


class TestUnreadableEntriesAreSkipped:
    def test_a_directory_named_like_a_markdown_file_is_skipped(self, tmp_path):
        """rglob('*.md') matches directories too. Reading one raises IsADirectoryError
        (an OSError), and the handler must skip to the next entry -- falling through
        would use an unbound `text` and take the whole scan down."""
        (tmp_path / "trap.md").mkdir()
        _write(tmp_path, "real.md", "aaa111")
        assert captured_message_ids(tmp_path) == {"aaa111"}

    def test_scan_continues_past_an_unreadable_entry(self, tmp_path):
        """The unreadable entry must not swallow files discovered after it."""
        _write(tmp_path, "a-first.md", "aaa111")
        (tmp_path / "b-trap.md").mkdir()
        _write(tmp_path, "c-last.md", "ccc333")
        assert captured_message_ids(tmp_path) == {"aaa111", "ccc333"}


class TestStdoutToStderr:
    """--json must emit exactly one parseable document. The Gmail helpers print progress
    to stdout while fetching; without diversion those lines land ahead of the JSON."""

    def test_diverts_stdout_when_active(self, capsys):
        from fetch_source_email import stdout_to_stderr

        with stdout_to_stderr(True):
            print("Fetched 10/17...")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Fetched 10/17..." in captured.err

    def test_leaves_stdout_alone_when_inactive(self, capsys):
        from fetch_source_email import stdout_to_stderr

        with stdout_to_stderr(False):
            print("human readable line")
        captured = capsys.readouterr()
        assert "human readable line" in captured.out
        assert captured.err == ""

    def test_stdout_is_restored_after_the_block(self, capsys):
        from fetch_source_email import stdout_to_stderr

        with stdout_to_stderr(True):
            print("noise")
        print("the json payload")
        assert "the json payload" in capsys.readouterr().out

    def test_stdout_is_restored_even_if_the_block_raises(self, capsys):
        from fetch_source_email import stdout_to_stderr

        with pytest.raises(ValueError):
            with stdout_to_stderr(True):
                raise ValueError("network blew up")
        print("still on stdout")
        assert "still on stdout" in capsys.readouterr().out
