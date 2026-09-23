"""Command-level tests for tools/stream_pipeline.py with fake yt-dlp, ffmpeg, ffprobe, whisper-cli.

The fakes are small Python scripts placed first on PATH for the subprocess only. Their
behavior is controlled by environment variables so each test can force a success, a
one-time failure, or a permanent failure:

  STUB_LOG             file each fake appends its argv to
  STUB_STATE           directory for one-time-failure markers
  STUB_DURATION        seconds ffprobe reports
  STUB_YTDLP_FAIL      "1" -> yt-dlp exits 1 and writes nothing
  STUB_FFMPEG_WAV_FAIL "1" -> the full-file WAV conversion exits 1
  STUB_WHISPER_FAIL_ONCE_AT / STUB_WHISPER_FAIL_ALWAYS_AT
                       segment start second at which whisper fails (once / always)
  STUB_WHISPER_EMPTY_AT  segment start at which whisper exits 0 with an empty VTT
  STUB_WHISPER_PREFIX  cue text prefix (default "speech")

The fake whisper emits one cue every 10 s of absolute stream time inside the segment it
was given, with text "<prefix> at <absolute second>", so offsets and seam merges are
directly checkable.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import stream_pipeline as sp  # noqa: E402

PY = sys.executable

FAKES = {
    "yt-dlp": f'''#!{PY}
import os, sys
if os.environ.get("STUB_READ_STDIN") == "1":
    open(os.environ["STUB_LOG"], "a").write("stdin-read yt-dlp " + str(len(sys.stdin.read())) + "\\n")
open(os.environ["STUB_LOG"], "a").write("yt-dlp " + " ".join(sys.argv[1:]) + "\\n")
if os.environ.get("STUB_YTDLP_FAIL") == "1":
    sys.exit(1)
if os.environ.get("STUB_YTDLP_NO_AUDIO") == "1":
    sys.stderr.write("ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe\\n")
    open("source.mp4", "w").write("video only")
    sys.exit(1)
open("source.m4a", "w").write("audio")
if "-k" in sys.argv:
    open("source.webm", "w").write("original")
''',
    "ffprobe": f'''#!{PY}
import os, sys
if os.environ.get("STUB_READ_STDIN") == "1":
    open(os.environ["STUB_LOG"], "a").write("stdin-read ffprobe " + str(len(sys.stdin.read())) + "\\n")
print(os.environ.get("STUB_DURATION", "2000"))
''',
    "ffmpeg": f'''#!{PY}
import os, sys
if os.environ.get("STUB_READ_STDIN") == "1":
    open(os.environ["STUB_LOG"], "a").write("stdin-read ffmpeg " + str(len(sys.stdin.read())) + "\\n")
a = sys.argv[1:]
open(os.environ["STUB_LOG"], "a").write("ffmpeg " + " ".join(a) + "\\n")
out = a[-1]
if "-ss" in a:
    start = float(a[a.index("-ss") + 1]); dur = float(a[a.index("-t") + 1])
    open(out, "w").write(f"{{start}} {{dur}}")
else:
    if os.environ.get("STUB_FFMPEG_WAV_FAIL") == "1":
        sys.exit(1)
    open(out, "w").write("wav")
''',
    "whisper-cli": f'''#!{PY}
import os, sys
if os.environ.get("STUB_READ_STDIN") == "1":
    open(os.environ["STUB_LOG"], "a").write("stdin-read whisper-cli " + str(len(sys.stdin.read())) + "\\n")
a = sys.argv[1:]
open(os.environ["STUB_LOG"], "a").write("whisper-cli " + " ".join(a) + "\\n")
start, dur = map(float, open(a[a.index("-f") + 1]).read().split())
base = a[a.index("-of") + 1]
state = os.environ["STUB_STATE"]
key = str(int(start))
if os.environ.get("STUB_WHISPER_EMPTY_AT") == key:
    open(base + ".vtt", "w").write("WEBVTT\\n\\n")
    sys.exit(0)
if os.environ.get("STUB_WHISPER_FAIL_ALWAYS_AT") == key:
    sys.exit(1)
if os.environ.get("STUB_WHISPER_FAIL_ONCE_AT") == key:
    marker = os.path.join(state, "failed-" + key)
    if not os.path.exists(marker):
        open(marker, "w").write("x")
        sys.exit(1)
prefix = os.environ.get("STUB_WHISPER_PREFIX", "speech")
def ts(x):
    return f"{{int(x//3600):02d}}:{{int(x%3600//60):02d}}:{{x%60:06.3f}}"
lines = ["WEBVTT", ""]
first = int(-(-start // 10) * 10)
t = first
while t + 5 <= start + dur:
    rel = t - start
    lines += [f"{{ts(rel)}} --> {{ts(rel + 5)}}", f"{{prefix}} number {{t}} words here", ""]
    t += 10
open(base + ".vtt", "w").write("\\n".join(lines) + "\\n")
''',
}


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    (tmp_path / "state").mkdir()
    root, cache = tmp_path / "repo", tmp_path / "cache"
    (root / "data" / "source-transcripts").mkdir(parents=True)
    e = {"PATH": f"{bindir}:{os.environ['PATH']}", "STREAM_ROOT": str(root), "STREAM_CACHE": str(cache),
         "STUB_LOG": str(tmp_path / "stub.log"), "STUB_STATE": str(tmp_path / "state"),
         "STUB_DURATION": "2000", "PYTHONIOENCODING": "utf-8"}
    return {"env": e, "root": root, "cache": cache / "demo", "tmp": tmp_path,
            "dir": root / "data" / "source-transcripts" / "2026-01-01-demo",
            "vtt": root / "data" / "source-transcripts" / "2026-01-01-demo.vtt"}


def run(env, *args, stdin_text=None, **extra):
    r = subprocess.run([PY, str(TOOLS_DIR / "stream_pipeline.py"), *args, "--slug", "demo", "--date", "2026-01-01"],
                       capture_output=True, text=True, input=stdin_text, env={**os.environ, **env["env"], **extra})
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return out, r.returncode


def manifest(env):
    return json.loads((env["dir"] / "manifest.json").read_text())


def cues(env):
    return sp.parse_vtt(env["vtt"].read_text())


def stub_log(env):
    p = env["tmp"] / "stub.log"
    return p.read_text() if p.exists() else ""


def acquired(env, **extra):
    out, code = run(env, "acquire", "https://example.com/stream", **extra)
    assert code == 0, out
    return out


# --- acquire -----------------------------------------------------------------

def test_acquire_records_manifest_and_keeps_original_download(env):
    out = acquired(env)
    m = manifest(env)
    assert out["ok"] is True and out["duration"] == "0:33:20"
    assert m["media_duration_s"] == 2000.0 and len(m["media_sha256"]) == 64
    assert m["history"][0][0] == "acquire"
    assert (env["cache"] / "source.m4a").exists() and (env["cache"] / "source.webm").exists()


def test_acquire_failure_exits_1_and_writes_no_manifest(env):
    out, code = run(env, "acquire", "https://example.com/stream", STUB_YTDLP_FAIL="1")
    assert code == 1 and out["ok"] is False and "yt-dlp failed" in out["error"]
    assert not (env["dir"] / "manifest.json").exists()


def test_acquire_reports_video_only_source_plainly(env):
    out, code = run(env, "acquire", "https://example.com/stream", STUB_YTDLP_NO_AUDIO="1")
    assert code == 1 and out["error"].startswith("source has no audio track")
    assert not (env["dir"] / "manifest.json").exists()


def test_child_processes_never_read_the_callers_stdin(env):
    """2026-09-23: a shell `while read` loop fed a session list to acquire/transcribe, a child
    process inherited stdin and consumed the start of the next line, and the next session ran
    under a slug missing its first three characters. Every external tool must get its own empty stdin."""
    leftover = "demo-two\t2026-01-02\tvideo-id-2\n"
    out, code = run(env, "acquire", "https://example.com/stream", stdin_text=leftover, STUB_READ_STDIN="1")
    assert code == 0, out
    out, code = run(env, "transcribe", stdin_text=leftover, STUB_READ_STDIN="1")
    assert code == 0, out
    reads = [l.split() for l in stub_log(env).splitlines() if l.startswith("stdin-read ")]
    assert {r[1] for r in reads} >= {"yt-dlp", "ffprobe", "ffmpeg", "whisper-cli"}, reads
    assert all(r[2] == "0" for r in reads), reads


def test_commands_without_manifest_exit_2(env):
    out, code = run(env, "status")
    assert code == 2 and "run acquire first" in out["error"]


# --- transcribe --------------------------------------------------------------

def test_transcribe_offsets_segments_and_merges_without_duplicates(env):
    acquired(env)
    out, code = run(env, "transcribe")
    assert code == 0 and out["ok"] is True and out["segments"] == 3 and out["failed"] == []
    got = cues(env)
    for c in got:
        assert c["text"] == f"speech number {int(c['start'])} words here"   # absolute offset applied
    starts = [int(c["start"]) for c in got]
    assert starts == list(range(0, 2000, 10))                              # full coverage, no dupes
    m = manifest(env)
    assert [s["status"] for s in m["segments"]] == ["ok", "ok", "ok"]
    assert [s["cue_count"] for s in m["segments"]] == [93, 96, 23]
    assert len(m["seams"]) == 2 and m["history"][-1] == ["transcribe", 3]
    assert out["cues"] == 200


def test_transcribe_retries_a_failed_segment_once(env):
    acquired(env)
    out, code = run(env, "transcribe", STUB_WHISPER_FAIL_ONCE_AT="870")
    seg = manifest(env)["segments"][1]
    assert code == 0 and out["ok"] is True
    assert seg["status"] == "ok" and seg["attempts"] == 2


def test_transcribe_reports_segment_that_fails_twice_and_validate_flags_it(env):
    acquired(env)
    out, code = run(env, "transcribe", STUB_WHISPER_FAIL_ALWAYS_AT="870")
    assert out["ok"] is False and out["failed"] == ["seg-001"]
    seg = manifest(env)["segments"][1]
    assert seg["status"] == "failed" and seg["attempts"] == 2 and seg["cue_count"] == 0
    val, _ = run(env, "validate")
    assert any(a["kind"] == "segment_failed" for a in val["anomalies"])


def test_transcribe_wav_conversion_failure_exits_1(env):
    acquired(env)
    out, code = run(env, "transcribe", STUB_FFMPEG_WAV_FAIL="1")
    assert code == 1 and "wav conversion failed" in out["error"]
    assert not env["vtt"].exists()


def test_transcribe_reuses_existing_wav(env):
    acquired(env)
    env["cache"].joinpath("source.wav").write_text("wav")
    run(env, "transcribe")
    conversions = [l for l in stub_log(env).splitlines() if l.startswith("ffmpeg") and "-ar" in l]
    assert conversions == []


def test_transcribe_passes_no_context_flag_to_whisper(env):
    acquired(env)
    run(env, "transcribe")
    calls = [l for l in stub_log(env).splitlines() if l.startswith("whisper-cli")]
    assert calls and all(" -mc 0 " in l for l in calls)


# --- validate / dispose / chunk ----------------------------------------------

def transcribed_with_loop(env):
    """Transcribe, then inject a repeated line at 500-560 so validate raises one anomaly."""
    acquired(env)
    run(env, "transcribe")
    cs = [c for c in cues(env) if not (500 <= c["start"] < 560)]
    cs += [{"start": float(t), "end": float(t + 5), "text": "we will be right back soon"} for t in range(500, 560, 10)]
    env["vtt"].write_text(sp.render_vtt(sorted(cs, key=lambda c: c["start"])))
    out, code = run(env, "validate")
    assert code == 0
    return out


def test_validate_records_anomalies_with_context(env):
    out = transcribed_with_loop(env)
    loops = [a for a in out["anomalies"] if a["kind"] == "repeat_run"]
    assert len(loops) == 1 and loops[0]["start"] == "0:08:20"
    assert loops[0]["before"][-1] == "0:08:10 speech number 490 words here"
    assert loops[0]["after"][0] == "0:09:20 speech number 560 words here"
    m = manifest(env)
    assert [a["id"] for a in m["anomalies"]] == ["A1"] and m["history"][-1] == ["validate", 1]


def test_dispose_unknown_id_exits_2(env):
    transcribed_with_loop(env)
    out, code = run(env, "dispose", "--id", "A9", "--as", "accept")
    assert code == 2 and "no anomaly A9" in out["error"]


def test_dispose_records_disposition_note_and_history(env):
    transcribed_with_loop(env)
    out, code = run(env, "dispose", "--id", "A1", "--as", "accept", "--note", "real chant")
    m = manifest(env)
    assert code == 0 and out["remaining"] == [] and out["hint"] == ""
    assert m["anomalies"][0]["disposition"] == "accept" and m["anomalies"][0]["note"] == "real chant"
    assert m["history"][-1] == ["dispose", "A1", "accept"]


def test_dispose_redo_replaces_exactly_the_padded_span(env):
    transcribed_with_loop(env)
    out, code = run(env, "dispose", "--id", "A1", "--as", "redo", STUB_WHISPER_PREFIX="redo")
    assert code == 0 and "run validate again" in out["hint"]
    got = cues(env)
    inside = [c for c in got if 470 <= c["start"] < 585]
    outside = [c for c in got if not (470 <= c["start"] < 585)]
    assert inside and all(c["text"].startswith("redo number") for c in inside)
    assert all(c["text"] == f"speech number {int(c['start'])} words here" for c in outside)
    assert [int(c["start"]) for c in got] == list(range(0, 2000, 10))
    assert manifest(env)["anomalies"][0]["redo_cues"] == len(inside)


def test_dispose_redo_failure_exits_1_and_leaves_transcript_unchanged(env):
    transcribed_with_loop(env)
    before = env["vtt"].read_text()
    out, code = run(env, "dispose", "--id", "A1", "--as", "redo", STUB_WHISPER_FAIL_ALWAYS_AT="470")
    assert code == 1 and "redo transcription failed" in out["error"]
    assert env["vtt"].read_text() == before
    assert manifest(env)["anomalies"][0]["disposition"] is None


def test_chunk_removes_hold_screen_span_but_keeps_accepted_span(env):
    transcribed_with_loop(env)
    run(env, "dispose", "--id", "A1", "--as", "hold_screen")
    out, code = run(env, "chunk")
    text = (env["dir"] / "chunks" / "chunk-00.txt").read_text()
    assert code == 0 and "we will be right back soon" not in text and "hold screen" in text
    assert "speech number 490 words here" in text

    run(env, "dispose", "--id", "A1", "--as", "accept")
    run(env, "chunk")
    assert "we will be right back soon" in (env["dir"] / "chunks" / "chunk-00.txt").read_text()


def test_chunk_writes_index_and_manifest(env):
    transcribed_with_loop(env)
    run(env, "dispose", "--id", "A1", "--as", "accept")
    out, code = run(env, "chunk")
    index = json.loads((env["dir"] / "chunks" / "index.json").read_text())
    m = manifest(env)
    assert code == 0 and index == out["chunks"] == m["chunks"]
    assert index[0]["id"] == "00" and index[0]["lines"] == 200 and m["history"][-1] == ["chunk", 1]


def test_clean_wav_records_history_and_status_reports_state(env):
    transcribed_with_loop(env)
    out, code = run(env, "clean-wav")
    assert code == 0 and out["deleted"] and all(p.endswith(".wav") for p in out["deleted"])
    assert manifest(env)["history"][-1][0] == "clean-wav"
    st, code = run(env, "status")
    assert code == 0 and st["undisposed"] == ["A1"] and st["segments"] == 3 and st["failed_segments"] == []
    assert st["duration"] == "0:33:20" and st["history"][-1][0] == "clean-wav"


def test_segment_with_clean_exit_but_no_cues_counts_as_failed(env):
    acquired(env)
    out, code = run(env, "transcribe", STUB_WHISPER_EMPTY_AT="1770")
    seg = manifest(env)["segments"][2]
    assert out["failed"] == ["seg-002"] and seg["status"] == "failed" and seg["attempts"] == 2


def test_manifest_hash_is_the_sha256_of_the_source_file(env):
    import hashlib
    acquired(env)
    expected = hashlib.sha256((env["cache"] / "source.m4a").read_bytes()).hexdigest()
    assert manifest(env)["media_sha256"] == expected
