"""W3 — deterministic detection of domain exclusions in a call transcript.

The incident: a prep doc bound one proof as "do not substitute". Mid-call the
counterpart excluded that whole domain and called its central deliverable
commoditizable. The follow-up led with it anyway.

Two tests carry most of the weight:
  * test_trailing_notes_are_not_transcript_body — typed notes must never be reported
    as something the counterpart said out loud
  * test_speaker_filter_defaults_to_counterpart — surfacing Nick's own "I would never"
    as an interviewer exclusion would block a valid proof, inverting the defect

All identities are placeholders. This file is public.
"""
import json

from conftest import FIXTURES_DIR, run_script_raw

W3 = FIXTURES_DIR / "w3"

COVERAGE = ("literal phrase list v1; paraphrased exclusions are NOT detected — "
            "read the counterpart's turns")


def scan(fixture: str, *args) -> tuple[int, dict]:
    proc = run_script_raw("transcript_exclusions.py", "--transcript", str(W3 / fixture), *args)
    assert proc.stdout.strip(), f"no stdout; stderr={proc.stderr}"
    return proc.returncode, json.loads(proc.stdout)


def sentences(report: dict) -> str:
    return " | ".join(h["sentence"] for h in report["hits"]).casefold()


# --------------------------------------------------------------- detection


def test_every_phrase_form_is_detected():
    code, report = scan("all-phrases.md")
    assert code == 0
    matched = {h["matched_phrase"] for h in report["hits"]}
    # 12 declared phrases; the commoditization family is one pattern covering
    # "gets / is / will get commoditized".
    assert len(matched) == 10, sorted(matched)
    text = sentences(report)
    for expected in ("i would never", "i'd never", "we would never", "we will never",
                     "we don't do", "we do not do", "we're not doing",
                     "we are not doing", "not what we do"):
        assert expected in text, expected
    commoditized = [h for h in report["hits"] if "commoditi" in h["matched_phrase"]]
    assert len(commoditized) == 3, "gets / is / will get commoditized must all fire"


def test_near_misses_do_not_fire():
    """"never mind" / "I've never seen" are conversation, not exclusions."""
    code, report = scan("near-misses.md")
    assert code == 0
    assert report["hit_count"] == 0, sentences(report)


def test_speaker_filter_defaults_to_counterpart():
    code, report = scan("both-speakers.md")
    assert code == 0
    assert report["hit_count"] == 1
    assert report["hits"][0]["speaker_label"] == "Them"

    code, wide = scan("both-speakers.md", "--include-self")
    assert code == 0
    assert wide["hit_count"] == 2
    assert {h["speaker_label"] for h in wide["hits"]} == {"Me", "Them"}


def test_speaker_names_resolve_when_the_note_is_present():
    code, report = scan("all-phrases.md")
    assert code == 0
    assert report["hits"][0]["speaker_name"] == "Jane Doe"


def test_missing_speaker_note_does_not_fail_the_run():
    code, report = scan("no-labels.md")
    assert code == 0
    assert report["hit_count"] == 1
    assert report["hits"][0]["speaker_name"] is None
    assert report["hits"][0]["speaker_label"] == "Them"


def test_microphone_speaker_markers_are_understood():
    """Part of the corpus labels turns Microphone:/Speaker: instead of Me:/Them:."""
    code, report = scan("mic-speaker.md")
    assert code == 0
    assert report["hit_count"] == 1
    assert report["hits"][0]["speaker_label"] == "Them"


def test_char_offsets_increase_and_index_into_the_body():
    code, report = scan("all-phrases.md")
    assert code == 0
    offsets = [h["char_offset"] for h in report["hits"]]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)
    assert offsets[0] >= 0


# --------------------------------------------------------------- body boundary


def test_trailing_notes_are_not_transcript_body():
    """A phrase in `## Granola Private Notes` is Nick's TYPED text, not a spoken turn.

    The body ends at the next `##` heading or `---` rule. Taking "everything after the
    transcript heading" swallows the notes into the final speaker segment and reports
    typed text as an interviewer exclusion.
    """
    code, report = scan("trailing-notes.md")
    assert code == 0
    assert report["hit_count"] == 0, sentences(report)


def test_missing_transcript_section_exits_1():
    code, report = scan("no-section.md")
    assert code == 1
    assert "Verbatim transcript" in report["error"]


# --------------------------------------------------------------- honesty gate


def test_coverage_string_is_present_on_every_successful_output():
    for fixture in ("zero-hits.md", "all-phrases.md"):
        code, report = scan(fixture)
        assert code == 0
        assert report["coverage"] == COVERAGE, fixture


def test_zero_hits_is_a_valid_answer_not_a_clearance():
    code, report = scan("zero-hits.md")
    assert code == 0
    assert report["hit_count"] == 0
    assert "NOT detected" in report["coverage"]


def test_wide_candidates_are_never_merged_into_hits():
    code, narrow = scan("paraphrase.md")
    assert code == 0
    assert narrow["hit_count"] == 0
    assert narrow["candidates"] == []

    code, wide = scan("paraphrase.md", "--wide")
    assert code == 0
    assert wide["hit_count"] == 0, "a paraphrase is a candidate, never a hit"
    assert any("table stakes" in c["sentence"].casefold() for c in wide["candidates"])


# --------------------------------------------------------------- dedup


def test_dedup_unions_two_recordings_of_one_call():
    code, report = scan("dedup-a.md", "--dedup-with", str(W3 / "dedup-b.md"))
    assert code == 0
    text = sentences(report)
    assert text.count("i would never do that piece") == 1, "the shared sentence must collapse"
    assert "six product suite" in text
    assert report["hit_count"] == 2
    assert len(report["deduped_from"]) == 2


# --------------------------------------------------------------- CLI


def test_bare_validate_local_parses():
    proc = run_script_raw("transcript_exclusions.py", "--validate-local")
    assert proc.returncode != 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert "status" in payload
    if payload["status"] == "SKIPPED":
        assert "absent" in payload["reason"]


# --- named-channel labels: `System audio (Name):` (2026-10-01) --------------
# The scan matched Me|Them|Microphone|Speaker only. On a named-channel transcript the
# default counterpart filter therefore read ZERO counterpart text and returned
# hit_count 0 with speaker_markers_found true, a null that looked like a clearance.

def test_named_system_audio_channel_is_scanned_as_counterpart():
    code, report = scan("named-channel.md")
    assert code == 0
    assert report["hit_count"] == 1
    assert report["hits"][0]["speaker_label"] == "Them"
    assert report["hits"][0]["sentence"] == "I would never do that piece."


def test_named_channel_owner_sentence_is_not_attributed_to_counterpart():
    """The owner's own "I would never" sits in a Microphone turn directly before the
    counterpart's. It must appear only with --include-self, labelled Me."""
    code, report = scan("named-channel.md", "--include-self")
    assert code == 0
    assert report["hit_count"] == 2
    assert [h["speaker_label"] for h in report["hits"]] == ["Me", "Them"]


def test_exclusion_scan_uses_the_shared_label_vocabulary():
    """Single source of truth. A second copy of the speaker-label regex in this tool is
    how it kept reading Me|Them|Microphone|Speaker after the shared splitter moved on."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "tools" / "transcript_exclusions.py"
           ).read_text(encoding="utf-8")
    assert "from meeting_vocab import" in src
    banned = [r"^\s*_SPEAKER_ALIASES\s*=", r"^\s*_SEGMENT_SPLIT_RE\s*=", r"^\s*_SEGMENT_HEAD_RE\s*="]
    offenders = [p for p in banned if re.search(p, src, re.M)]
    assert not offenders, f"transcript_exclusions.py re-defines label parsing locally: {offenders}"


def test_exclusion_scan_and_shared_splitter_agree_on_counterpart_text():
    """Cross-tool parity on every channel-label form: the text this scan treats as the
    counterpart's must be the text the shared splitter attributes to the counterpart."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import meeting_vocab
    import transcript_exclusions as tx
    body = ("Me: one mine\nThem: one theirs\n"
            "Microphone: two mine\nSpeaker: two theirs\n"
            "Microphone (John Smith): three mine\nSystem audio (Jane Doe): three theirs\n"
            "System audio: four theirs\n")
    owner, other = meeting_vocab.split_transcript_turns(body)
    assert other == ["one theirs", "two theirs", "three theirs", "four theirs"]
    segments = tx.split_segments(body)
    assert [t.strip() for label, t, _ in segments if label == "Them"] == other
    assert [t.strip() for label, t, _ in segments if label == "Me"] == owner


def test_text_before_the_first_label_is_kept_as_an_unlabelled_segment():
    """Pre-existing behaviour, pinned when the splitter moved to meeting_vocab: text ahead
    of the first speaker label is scanned under --speaker all with no speaker attached,
    never dropped and never attributed to either side."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import transcript_exclusions as tx
    segments = tx.split_segments("an untagged opening line\nMe: mine\nThem: theirs")
    assert segments[0] == (None, "an untagged opening line\n", 0)
    assert [label for label, _, _ in segments] == [None, "Me", "Them"]


def test_no_unlabelled_segment_when_the_body_opens_on_a_label():
    """The unlabelled leading segment exists only when there is leading text. Blank space
    ahead of the first label must not become an empty segment of its own."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import transcript_exclusions as tx
    segments = tx.split_segments("\n\nMe: mine\nThem: theirs")
    assert [label for label, _, _ in segments] == ["Me", "Them"]


def test_undiarized_body_is_one_unlabelled_segment():
    """No speaker labels at all: the whole body is one segment with no speaker, so
    --speaker all can still scan it and the counterpart filter correctly reads nothing."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import transcript_exclusions as tx
    body = "prose with no speaker markers at all"
    assert tx.split_segments(body) == [(None, body, 0)]
    assert tx.split_segments("  \n ") == []


def test_tool_imports_as_a_module_from_the_repo_root():
    """`python3 -m tools.transcript_exclusions` and `import tools.transcript_exclusions`
    worked before this file gained a sibling import and must keep working. Run in a
    fresh interpreter so the tools/ path that conftest adds cannot mask a failure."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, "-c", "import tools.transcript_exclusions"],
                          cwd=root, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
