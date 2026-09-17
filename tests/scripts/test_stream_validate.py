"""Tests for tools/stream_validate.py (synthetic fixtures)."""
import sys

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import stream_validate as sv  # noqa: E402

CHUNK = """CHUNK 01
Core window: 0:10:00 - 0:20:00 (stream time)
Overlap: 5 min each side; lines marked [overlap] belong to a neighbouring chunk's core.

[0:08:00] [overlap] this line belongs to the previous chunk
[0:10:05] So the bot has its own computer, which is isolated.
[0:12:30] I don't know why that happened.
[0:15:00] Nobody said no to that request.
"""


def form(**items):
    base = {k: [] for k in ("claims", "demos", "friction", "numbers", "techniques", "audience_questions")}
    base.update(items)
    return base


def test_form_accepts_exact_quote_in_core_with_close_ts():
    f = form(claims=[{"ts": "0:10:05", "quote": "the bot has its own computer, which is isolated"}])
    r = sv.check_form(f, CHUNK)
    assert r["failed"] == 0 and f["claims"][0]["verified"] is True


@pytest.mark.parametrize("quote,reason", [
    ("the bot has its own computer which is fully isolated", "not_found"),   # word inserted
    ("I do not know why that happened", "not_found"),                        # contraction expanded
    ("this line belongs to the previous chunk", "outside_core"),             # overlap line
])
def test_form_rejects_edits_and_overlap_items(quote, reason):
    f = form(claims=[{"ts": "0:10:05", "quote": quote}])
    r = sv.check_form(f, CHUNK)
    assert r["failed"] == 1 and r["failures"][0]["reason"] == reason
    assert f["claims"][0]["verified"] is False


def test_form_rejects_timestamp_beyond_tolerance():
    f = form(friction=[{"ts": "0:17:00", "quote": "I don't know why that happened"}])
    r = sv.check_form(f, CHUNK)
    assert r["failures"][0]["reason"] == "ts_off"


def test_form_checks_every_quote_field():
    f = form(demos=[{"evidence_ts": "0:10:05", "evidence_quote": "not in the chunk at all"}],
             audience_questions=[{"ts": "0:10:05", "question_quote": "also missing here"}])
    assert sv.check_form(f, CHUNK)["failed"] == 2


def test_short_quote_matches_whole_words_only():
    tt = sv.TimedText([(0.0, "I know it"), (5.0, "the answer is no")])
    assert tt.match_times("no") == [5.0]


# --- doc checks -------------------------------------------------------------

TRANSCRIPT = [
    {"start": 600.0, "end": 605.0, "text": "So the bot has its own computer, which is isolated."},
    {"start": 750.0, "end": 755.0, "text": "Thanks, Priya Shah, for joining us."},
]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "notes.md").write_text("The goal is a deployment seat. Working with Contoso.", encoding="utf-8")
    return tmp_path


def test_doc_passes_with_tagged_quotes_and_known_names(root):
    doc = ('# T\n\nThe host said "its own computer, which is isolated" [transcript 0:10:00] and '
           'Priya Shah joined.\nNotes say "a deployment seat" [file notes.md].\n')
    r = sv.check_doc(doc, TRANSCRIPT, root)
    assert r["failed"] == 0, r["failures"]


def test_doc_rejects_untagged_quote(root):
    r = sv.check_doc('Text "its own computer" here.\n', TRANSCRIPT, root)
    assert [f["check"] for f in r["failures"]] == ["quote_untagged"]


def test_doc_rejects_transcript_tag_on_quote_that_exists_only_in_file(root):
    r = sv.check_doc('He said "a deployment seat" [transcript 0:10:00].\n', TRANSCRIPT, root)
    assert [f["check"] for f in r["failures"]] == ["quote_not_in_transcript"]


def test_doc_rejects_file_tag_on_quote_that_exists_only_in_transcript(root):
    r = sv.check_doc('Notes say "its own computer" [file notes.md].\n', TRANSCRIPT, root)
    assert [f["check"] for f in r["failures"]] == ["quote_not_in_file"]


def test_doc_rejects_transcript_quote_with_wrong_timestamp(root):
    r = sv.check_doc('"its own computer" [transcript 0:30:00]\n', TRANSCRIPT, root)
    assert [f["check"] for f in r["failures"]] == ["quote_ts_off"]


def test_doc_rejects_file_path_outside_root(root):
    r = sv.check_doc('"x y" [file ../../etc/passwd]\n', TRANSCRIPT, root)
    assert r["failures"][0]["check"] == "file_missing"


def test_doc_rejects_invented_name(root):
    r = sv.check_doc("The session was led by Wendel Okonkwo-Brightwater and others.\n", TRANSCRIPT, root)
    assert any(f["check"] == "name_unresolved" for f in r["failures"])


def test_doc_rejects_invented_single_surname_mid_sentence(root):
    r = sv.check_doc("The talk by Zorblatt covered pricing.\n", TRANSCRIPT, root)
    assert [f["name"] for f in r["failures"] if f["check"] == "name_unresolved"] == ["Zorblatt"]


def test_doc_accepts_agenda_marked_name(root):
    r = sv.check_doc("The session host Zorblatt (from the agenda) covered pricing.\n", TRANSCRIPT, root)
    assert r["failed"] == 0, r["failures"]


def test_doc_name_resolves_through_tagged_file_only(root):
    untagged = sv.check_doc("We compared this with Contoso.\n", TRANSCRIPT, root)
    tagged = sv.check_doc('We compared this with Contoso. "a deployment seat" [file notes.md]\n', TRANSCRIPT, root)
    assert any(f["check"] == "name_unresolved" for f in untagged["failures"])
    assert tagged["failed"] == 0, tagged["failures"]


def test_doc_name_matches_across_spacing(root):
    cues = [{"start": 1.0, "end": 2.0, "text": "the note bot drafts it"}]
    r = sv.check_doc("The NoteBot drafted it.\n", cues, root)
    assert r["failed"] == 0, r["failures"]


def test_doc_ignores_code_spans_and_rejects_em_dash(root):
    r = sv.check_doc("Run `Zorblatt \"x\"` now — ok.\n", TRANSCRIPT, root)
    assert [f["check"] for f in r["failures"]] == ["em_dash"]


# --- CLI exit codes (the skill gates on these) --------------------------------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402


def run_cli(*args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "stream_validate.py"), *map(str, args)],
                       capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return json.loads(r.stdout), r.returncode


def test_cli_doc_exits_3_on_failure_and_0_on_pass(root, tmp_path):
    vtt = tmp_path / "t.vtt"
    vtt.write_text("WEBVTT\n\n00:10:00.000 --> 00:10:05.000\nSo the bot has its own computer, which is isolated.\n\n", encoding="utf-8")
    bad, good = tmp_path / "bad.md", tmp_path / "good.md"
    bad.write_text('He said "its own computer" here.\n', encoding="utf-8")
    good.write_text('He said "its own computer" [transcript 0:10:00].\n', encoding="utf-8")
    out, code = run_cli("doc", bad, "--transcript", vtt, "--root", root)
    assert code == 3 and out["ok"] is False
    out, code = run_cli("doc", good, "--transcript", vtt, "--root", root)
    assert code == 0 and out["ok"] is True


def test_cli_form_exits_3_and_writes_verified_flags(tmp_path):
    fp, cp = tmp_path / "f.json", tmp_path / "c.txt"
    fp.write_text(json.dumps(form(claims=[{"ts": "0:10:05", "quote": "not in the chunk"}])), encoding="utf-8")
    cp.write_text(CHUNK, encoding="utf-8")
    out, code = run_cli("form", fp, "--chunk", cp)
    assert code == 3 and json.loads(fp.read_text())["claims"][0]["verified"] is False


# --- extraction details ---------------------------------------------------------

def test_quotes_inside_fenced_code_blocks_are_ignored(root):
    doc = 'Introduction.\n```\nexample "untagged quote in code" here\n```\n'
    assert sv.check_doc(doc, TRANSCRIPT, root)["failed"] == 0


def test_names_inside_fenced_code_blocks_are_ignored(root):
    assert sv.check_doc("Introduction.\n```\nThe host Zorblatt spoke\n```\n", TRANSCRIPT, root)["failed"] == 0


def test_acronyms_and_hyphenated_tokens_are_not_names(root):
    assert sv.check_doc("The team uses QXZV and the Post-Wibble flow daily.\n", TRANSCRIPT, root)["failed"] == 0


def test_inflected_dictionary_words_are_not_names(root):
    # "Touches" and "Claimed" are inflections of dictionary words; they lead a bullet label.
    assert sv.check_doc("- Touches nothing. Claimed by the vendor.\n", TRANSCRIPT, root)["failed"] == 0


def test_heading_lines_are_not_scanned_for_names(root):
    assert sv.check_doc("# Report on Zorblatt\n", TRANSCRIPT, root)["failed"] == 0


def test_unresolved_name_is_reported_once_per_name(root):
    r = sv.check_doc("The talk by Zorblatt covered pricing. Later Zorblatt left.\n", TRANSCRIPT, root)
    assert [f["name"] for f in r["failures"]] == ["Zorblatt"]


# --- survivors from the mutation run ------------------------------------------

def test_quote_spanning_an_empty_cue_still_matches():
    tt = sv.TimedText([(0.0, "end of one"), (1.0, "♪"), (2.0, "start of next")])
    assert tt.match_times("one start of") == [0.0]


def test_punctuation_only_quote_matches_nothing():
    tt = sv.TimedText([(0.0, "anything at all")])
    assert tt.match_times("...") == []


def test_chunk_without_core_window_header_raises():
    with pytest.raises(ValueError, match="Core window"):
        sv.parse_chunk("[0:00:01] no header here\n")


@pytest.mark.parametrize("item", [{"ts": "0:10:05", "quote": ""}, {"ts": "0:10:05", "quote": "..."}, {"ts": "0:10:05"}])
def test_form_item_with_missing_or_empty_quote_fails(item):
    f = form(claims=[item])
    r = sv.check_form(f, CHUNK)
    assert r["failed"] == 1 and r["failures"][0]["reason"] == "empty_quote" and f["claims"][0]["verified"] is False


def test_quote_on_a_fence_line_itself_is_ignored(root):
    # the closing fence line is the one that is reached after the toggle; test both lines
    doc = 'Introduction.\n``` "untagged on the opening fence"\ncode\n``` "untagged on the closing fence"\n'
    assert sv.check_doc(doc, TRANSCRIPT, root)["failed"] == 0


def test_name_on_a_fence_line_itself_is_ignored():
    assert sv.extract_name_candidates("Introduction.\n``` Zorblatt\ncode\n``` Quimbleton\n") == []


def test_missing_wordlist_yields_empty_dictionary(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "DICT_PATH", tmp_path / "no-such-wordlist")
    monkeypatch.setattr(sv, "_DICT", None)
    assert sv.dictionary() == set()


def test_adjacent_capitalized_words_form_one_name_candidate():
    names = [c["name"] for c in sv.extract_name_candidates("The talk by Zorblatt Quimby covered pricing.\n")]
    assert names == ["Zorblatt Quimby"]


def test_punctuation_only_quote_in_doc_is_not_evidence_and_does_not_fail(root):
    assert sv.check_doc('He paused "..." and moved on.\n', TRANSCRIPT, root)["failed"] == 0
