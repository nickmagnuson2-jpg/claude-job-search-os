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
