"""Tests for tools/stream_pipeline.py (synthetic fixtures; real transcripts are gitignored)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import stream_pipeline as sp  # noqa: E402


def cue(start, end, text):
    return {"start": float(start), "end": float(end), "text": text}


# --- segment ranges ---------------------------------------------------------

def test_segments_cover_duration_with_overlap_on_both_sides():
    segs = sp.segment_ranges(2000, core=900, overlap=30)
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 930.0), (870.0, 1830.0), (1770.0, 2000.0)]
    for a, b in zip(segs, segs[1:]):
        assert a["end"] - b["start"] == 60.0  # full 2 x overlap shared, not half


def test_segments_first_starts_at_zero_last_ends_at_duration():
    segs = sp.segment_ranges(901, core=900, overlap=30)
    assert segs[0]["start"] == 0.0 and segs[-1]["end"] == 901.0 and len(segs) == 2


# --- seam merge -------------------------------------------------------------

def test_seam_is_placed_in_largest_gap():
    a = [cue(868, 885, "one two three"), cue(885.5, 890, "four five six"), cue(898, 932, "seven eight nine")]
    assert sp.choose_seam(a, 900, 30) == pytest.approx(894.0)


def test_seam_is_not_placed_in_a_gap_only_one_decode_has():
    # A dropped speech at 893-899 (a false "silence"); B has words there. Both are silent at 881-883.
    a = [cue(865, 881, "one two three four"), cue(883, 893, "five six seven eight"), cue(899, 935, "nine ten eleven")]
    b = [cue(865, 881, "one two three four"), cue(883, 893, "five six seven eight"),
         cue(893, 899, "who want to attend the spring workshop"), cue(899, 935, "nine ten eleven")]
    assert sp.choose_seam(a, 900, 30, b) == pytest.approx(882.0)


def test_words_only_the_later_decode_caught_before_the_seam_survive_the_merge():
    # Failure shape seen in a real run: A's cue shares some words with B's longer cue, B's extra words must not be lost.
    a = [cue(880, 884, "we need to find businesses or individuals"), cue(897, 901, "but also people we can hire")]
    b = [cue(870, 872, "earlier speech in segment b"), cue(880, 890, "we need to find businesses or individuals who want to attend the spring workshop"),
         cue(897, 901, "but also people we can hire")]
    merged, _ = sp.merge_segments([a, b], core=900, overlap=30)
    joined = " ".join(c["text"] for c in merged)
    assert "want to attend the spring workshop" in joined


def test_identical_decodes_merge_without_duplicates_or_loss():
    base = [cue(t, t + 4, f"sentence number {t} is spoken here") for t in range(0, 1800, 5)]
    a = [c for c in base if c["start"] < 930]
    b = [c for c in base if c["start"] >= 870]
    merged, recs = sp.merge_segments([a, b], core=900, overlap=30)
    assert [(c["start"], c["text"]) for c in merged] == [(c["start"], c["text"]) for c in base]
    assert recs[0]["recovered"] == []


def test_short_cues_identical_in_both_decodes_are_not_duplicated():
    a = [cue(880, 881, "Yeah."), cue(895, 896, "Okay."), cue(905, 906, "Cool.")]
    b = [cue(880, 881, "Yeah."), cue(895, 896, "Okay."), cue(905, 906, "Cool.")]
    merged, _ = sp.merge_segments([a, b], core=900, overlap=30)
    assert [c["text"] for c in merged] == ["Yeah.", "Okay.", "Cool."]


def test_text_only_one_decode_caught_is_recovered_across_the_seam():
    # A caught a phrase after the seam that B dropped; B is otherwise the same.
    a = [cue(880, 884, "alpha bravo charlie delta"), cue(890, 894, "echo foxtrot golf hotel"),
         cue(906, 909, "the unique phrase only decode a heard")]
    b = [cue(880, 884, "alpha bravo charlie delta"), cue(890, 894, "echo foxtrot golf hotel"),
         cue(915, 920, "india juliet kilo lima")]
    merged, recs = sp.merge_segments([a, b], core=900, overlap=30)
    texts = [c["text"] for c in merged]
    assert "the unique phrase only decode a heard" in texts
    assert recs[0]["recovered"] and recs[0]["recovered"][0]["from"] == "A"
    assert texts.count("alpha bravo charlie delta") == 1


# --- anomaly detectors ------------------------------------------------------

def test_repeat_run_flags_multiword_loop_of_five():
    cues = [cue(i, i + 1, "we will be right back") for i in range(5)]
    kinds = [a["kind"] for a in sp.detect_anomalies(cues, 5)]
    assert "repeat_run" in kinds


def test_repeat_run_ignores_short_filler_word_repeats_below_long_threshold():
    cues = [cue(i, i + 1, "so") for i in range(8)] + [cue(8, 600, "long real speech continues here")]
    assert [a for a in sp.detect_anomalies(cues, 600) if a["kind"] == "repeat_run"] == []


def test_repeat_run_flags_single_word_at_long_threshold():
    cues = [cue(i, i + 1, "Bye.") for i in range(10)]
    assert any(a["kind"] == "repeat_run" for a in sp.detect_anomalies(cues, 10))


def test_low_coverage_flags_sparse_window_but_not_dense_one():
    dense = [cue(t, t + 9, "real words spoken here") for t in range(0, 600, 10)]
    sparse = [cue(600 + t, 600 + t + 2, "real words spoken here") for t in range(0, 600, 60)]
    an = sp.detect_anomalies(dense + sparse, 1200)
    low = [a for a in an if a["kind"] == "low_coverage"]
    assert len(low) == 1 and low[0]["start"] == 600.0


def test_filler_run_flags_hold_screen_pattern():
    cues = [cue(t, t + 2, "Thank you.") for t in range(0, 300, 30)]
    assert any(a["kind"] == "filler_run" for a in sp.detect_anomalies(cues, 600))


def test_failed_segment_becomes_anomaly():
    segs = [{"id": "seg-001", "start": 870.0, "end": 1830.0, "status": "failed"}]
    an = sp.detect_anomalies([cue(0, 1800, "x y z")], 1800, segs)
    assert any(a["kind"] == "segment_failed" for a in an)


# --- chunks -----------------------------------------------------------------

def test_chunks_tag_overlap_and_remove_hold_screen():
    cues = [cue(t, t + 5, f"line {t}") for t in range(0, 5400, 60)]
    chunks = sp.build_chunks(cues, 5400, holds=[(1200.0, 1500.0)], core=2700, overlap=300)
    assert len(chunks) == 2
    c0 = chunks[0]["text"]
    assert "line 1260" not in c0  # inside hold
    assert "[0:47:00] [overlap] line 2820" in c0
    assert "hold screen" in c0
    assert "[0:47:00] [overlap]" not in chunks[1]["text"]


# --- command-level: gates and deletion scope --------------------------------

def run_cmd(*args, env):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "stream_pipeline.py"), *args],
                       capture_output=True, text=True, env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"})
    return json.loads(r.stdout), r.returncode


@pytest.fixture
def stream_env(tmp_path):
    root, cache = tmp_path / "repo", tmp_path / "cache"
    d = root / "data" / "source-transcripts" / "2026-01-01-demo"
    d.mkdir(parents=True)
    (cache / "demo").mkdir(parents=True)
    vtt = root / "data" / "source-transcripts" / "2026-01-01-demo.vtt"
    vtt.write_text(sp.render_vtt([cue(t, t + 5, f"line {t}") for t in range(0, 600, 10)]), encoding="utf-8")
    manifest = {"slug": "demo", "date": "2026-01-01", "media_path": str(cache / "demo" / "source.m4a"),
                "media_duration_s": 600.0, "segments": [], "seams": [], "chunks": [], "history": [],
                "anomalies": [{"id": "A1", "kind": "repeat_run", "start": 10.0, "end": 20.0, "detail": "",
                               "disposition": None, "note": ""}]}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"env": {"STREAM_ROOT": str(root), "STREAM_CACHE": str(cache)}, "dir": d, "cache": cache / "demo"}


def test_chunk_refuses_while_anomaly_undisposed(stream_env):
    out, code = run_cmd("chunk", "--slug", "demo", "--date", "2026-01-01", env=stream_env["env"])
    assert code == 2 and out["open"] == ["A1"]
    assert not (stream_env["dir"] / "chunks").exists()


def test_chunk_runs_after_disposition(stream_env):
    run_cmd("dispose", "--slug", "demo", "--date", "2026-01-01", "--id", "A1", "--as", "accept", env=stream_env["env"])
    out, code = run_cmd("chunk", "--slug", "demo", "--date", "2026-01-01", env=stream_env["env"])
    assert code == 0 and (stream_env["dir"] / "chunks" / "chunk-00.txt").exists()


def test_clean_wav_deletes_only_wav_and_keeps_source_audio(stream_env):
    c = stream_env["cache"]
    for name in ("source.m4a", "source.mp4", "source.wav", "seg-000.wav", "notes.txt"):
        (c / name).write_bytes(b"x" * 10)
    out, code = run_cmd("clean-wav", "--slug", "demo", "--date", "2026-01-01", env=stream_env["env"])
    assert code == 0
    assert sorted(p.name for p in c.iterdir()) == ["notes.txt", "source.m4a", "source.mp4"]


def test_acquire_command_keeps_original_download():
    cmd = sp.acquire_cmd("https://example.com/stream")
    assert "-k" in cmd and "-x" in cmd


# --- survivors from the mutation run: transcript-correctness paths ------------

def test_vtt_round_trip_preserves_times_and_text_and_skips_empty_cues():
    cues = [cue(0.5, 2.25, "first line"), cue(3661.125, 3662.0, "an hour in")]
    text = sp.render_vtt(cues) + "00:00:05.000 --> 00:00:06.000\n\n"
    assert sp.render_vtt(cues).startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.250\nfirst line\n")
    assert sp.parse_vtt(text) == cues


def test_parse_ts_accepts_minutes_seconds_form():
    assert sp.parse_ts("2:05") == 125.0 and sp.parse_ts("1:02:03") == 3723.0


def test_fmt_ts_display_and_vtt_forms():
    assert sp.fmt_ts(3723.5) == "1:02:03" and sp.fmt_ts(3723.5, vtt=True) == "01:02:03.500"


def test_gaps_include_trailing_silence_at_window_edge():
    assert sp._gaps([cue(880, 890, "a b c")], 870, 930) == [(870, 880), (890, 930)]


def test_seam_falls_back_to_cue_end_nearest_t_when_no_shared_silence():
    a = [cue(860, 895, "a b c d"), cue(895, 940, "e f g h")]
    b = [cue(860, 897, "a b c d"), cue(897, 940, "e f g h")]
    assert sp.choose_seam(a, 900, 30, b) == 895.0


def test_merge_output_is_time_ordered_when_recovered_cues_interleave():
    a = [cue(880, 884, "alpha bravo charlie delta"), cue(905, 907, "zulu yankee xray whiskey")]
    b = [cue(870, 872, "kilo lima mike november"), cue(880, 884, "alpha bravo charlie delta"), cue(920, 925, "oscar papa quebec romeo")]
    merged, _ = sp.merge_segments([a, b], core=900, overlap=30)
    starts = [c["start"] for c in merged]
    assert starts == sorted(starts)


def test_merge_records_recoveries_from_both_sides():
    a = [cue(860, 868, "shared opening words here"), cue(906, 909, "only a heard this sentence")]
    b = [cue(860, 868, "shared opening words here"), cue(875, 878, "only b heard this other sentence"), cue(920, 925, "later words in b")]
    _, recs = sp.merge_segments([a, b], core=900, overlap=30)
    assert sorted(r["from"] for r in recs[0]["recovered"]) == ["A", "B"]


def test_merge_of_no_segments_is_empty():
    assert sp.merge_segments([]) == ([], [])


def test_healthy_segment_is_not_flagged():
    segs = [{"id": "seg-000", "start": 0.0, "end": 930.0, "status": "ok"}]
    dense = [cue(t, t + 9, "real words spoken here") for t in range(0, 930, 10)]
    assert [a for a in sp.detect_anomalies(dense, 930, segs) if a["kind"] == "segment_failed"] == []


def test_grams_are_word_trigrams():
    assert sp._grams("One, two three four") == {("one", "two", "three"), ("two", "three", "four")}
    assert sp._grams("two words") == set()


def test_short_cue_is_matched_as_whole_phrase_not_substring():
    assert sp._caught_elsewhere("Okay.", set(), "well okay then") is True
    assert sp._caught_elsewhere("Ok.", set(), "a broken token here") is False   # "ok" inside "broken" must not count
    assert sp._caught_elsewhere("Yes sir.", set(), "no one said it") is False


def test_gaps_skip_touching_and_overlapping_cues():
    cs = [cue(870, 880, "a"), cue(880, 890, "b"), cue(885, 895, "c"), cue(900, 931, "d")]
    assert sp._gaps(cs, 870, 930) == [(895, 900)]


def test_gaps_have_no_trailing_gap_when_last_cue_runs_past_window():
    assert sp._gaps([cue(860, 940, "long cue")], 870, 930) == []


def test_near_identical_long_cue_counts_as_caught_by_trigram_share():
    other = "one two three four five six seven eight nine changed"
    cue_text = "one two three four five six seven eight nine ten"
    assert sp._caught_elsewhere(cue_text, sp._grams(other), other) is True


def test_default_data_root_is_the_repository(monkeypatch):
    monkeypatch.delenv("STREAM_ROOT", raising=False)
    p = sp.paths("demo", "2026-01-01")
    assert p["dir"] == Path(sp.__file__).resolve().parent.parent / "data" / "source-transcripts" / "2026-01-01-demo"
