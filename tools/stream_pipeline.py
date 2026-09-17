#!/usr/bin/env python3
"""stream_pipeline.py - acquire, transcribe, validate, and chunk a long recorded stream.

Deterministic half of /analyze-stream. The LLM steps (extract, synthesize, verify)
live in .claude/skills/analyze-stream/SKILL.md. Design:
output/analysis/091626-livestream-transcript-analysis-design-v4.md (gitignored).

Custody rule: the compressed source audio is KEPT. Only WAV files are ever deleted
(clean-wav), and WAVs can be regenerated from the source at any time. acquire passes
-k so yt-dlp keeps the downloaded original too.

ALWAYS run with PYTHONIOENCODING=utf-8.

Subcommands (all print one JSON object to stdout):
  acquire     <url> --slug S --date YYYY-MM-DD
  transcribe  --slug S --date D [--model PATH]
  validate    --slug S --date D
  dispose     --slug S --date D --id A3 --as hold_screen|redo|accept [--note TEXT]
  chunk       --slug S --date D
  clean-wav   --slug S --date D
  status      --slug S --date D

Paths:
  media cache  ~/.cache/stream-analysis/<slug>/
  data dir     data/source-transcripts/<date>-<slug>/   (manifest.json, segments/, chunks/)
  transcript   data/source-transcripts/<date>-<slug>.vtt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Tunable thresholds (design v4 section 4). Provisional; recalibrate after each run.
SEG_CORE = 900
SEG_OVERLAP = 30
SEAM_RECOVER_MAX_OVERLAP = 0.8   # a discarded cue is kept unless 80%+ of its 3-grams are in the other decode
REPEAT_RUN = 5          # for cue text of 2+ words
REPEAT_RUN_LONG = 10    # for any cue text, including one word
COV_WINDOW = 600
COV_MIN = 0.5
FILLER_MIN = 6
CHUNK_CORE = 2700
CHUNK_OVERLAP = 300

FILLER_LINES = {"thank you", "bye", "you", "thanks"}
DEFAULT_MODEL = Path.home() / ".cache" / "whisper-cpp" / "ggml-large-v3-turbo.bin"
DISPOSITIONS = ("hold_screen", "redo", "accept")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Quote-matching normalization shared with stream_validate.py."""
    t = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("'", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def fmt_ts(sec: float, vtt: bool = False) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    if vtt:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{h}:{m:02d}:{int(s):02d}"


def parse_ts(ts: str) -> float:
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_vtt(text: str) -> list[dict]:
    cues = []
    for m in re.finditer(
        r"(\d+:\d\d:\d\d(?:\.\d+)?)\s*-->\s*(\d+:\d\d:\d\d(?:\.\d+)?)[^\n]*\n(.*?)(?:\n\s*\n|\Z)",
        text, re.S,
    ):
        body = " ".join(m.group(3).strip().split())
        if body:
            cues.append({"start": parse_ts(m.group(1)), "end": parse_ts(m.group(2)), "text": body})
    return cues


def render_vtt(cues: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for c in cues:
        out.append(f"{fmt_ts(c['start'], True)} --> {fmt_ts(c['end'], True)}")
        out.append(c["text"])
        out.append("")
    return "\n".join(out) + "\n"


def segment_ranges(duration: float, core: int = SEG_CORE, overlap: int = SEG_OVERLAP) -> list[dict]:
    """Expected audio segments: core windows extended by `overlap` on each side."""
    segs = []
    i = 0
    while i * core < duration:
        start = max(0.0, i * core - overlap)
        end = min(duration, (i + 1) * core + overlap)
        segs.append({"id": f"seg-{i:03d}", "index": i, "nominal_start": float(i * core),
                     "start": float(start), "end": float(end)})
        i += 1
    return segs


def _grams(text: str, n: int = 3) -> set:
    w = normalize(text).split()
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def _caught_elsewhere(cue_text: str, other_grams: set, other_text: str) -> bool:
    """True when the other decode's shared-window text already contains this cue's content.
    Cues shorter than 3 words have no 3-grams, so they are matched as a whole phrase."""
    g = _grams(cue_text)
    if g:
        return len(g & other_grams) / len(g) >= SEAM_RECOVER_MAX_OVERLAP
    n = normalize(cue_text)
    return not n or re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", normalize(other_text)) is not None


def _gaps(cues: list[dict], lo: float, hi: float) -> list[tuple[float, float]]:
    win = sorted((c for c in cues if c["end"] > lo and c["start"] < hi), key=lambda c: c["start"])
    gaps, prev_end = [], lo
    for c in win:
        if c["start"] > prev_end:
            gaps.append((prev_end, c["start"]))
        prev_end = max(prev_end, c["end"])
    if prev_end < hi:
        gaps.append((prev_end, hi))
    return gaps


def choose_seam(a_cues: list[dict], t: float, overlap: int = SEG_OVERLAP, b_cues: list[dict] | None = None) -> float:
    """Seam = midpoint of the largest interval where BOTH decodes are silent inside
    [t-overlap, t+overlap]. A gap in only one decode may be speech that decode dropped,
    so it is not trusted. Fallback: the end of an A cue closest to t."""
    lo, hi = t - overlap, t + overlap
    a_gaps = _gaps(a_cues, lo, hi)
    b_gaps = _gaps(b_cues, lo, hi) if b_cues is not None else a_gaps
    best, seam = 0.0, None
    for a0, a1 in a_gaps:
        for b0, b1 in b_gaps:
            s0, s1 = max(a0, b0), min(a1, b1)
            if s1 - s0 > best:
                best, seam = s1 - s0, (s0 + s1) / 2
    if seam is None:
        ends = [c["end"] for c in a_cues if lo < c["end"] < hi]
        seam = min(ends, key=lambda e: abs(e - t)) if ends else t
    return max(lo, min(hi, seam))


def merge_pair(a_cues: list[dict], b_cues: list[dict], t: float, overlap: int = SEG_OVERLAP) -> tuple[list, list, dict]:
    """Merge the boundary between A (earlier) and B (later) around nominal time t.

    Returns (kept_a, kept_b, record). Cues discarded by the seam are recovered when
    fewer than SEAM_RECOVER_MAX_OVERLAP of their 3-grams appear in the other decode's
    shared-window text, so text only one decode caught is not dropped.
    """
    lo, hi = t - overlap, t + overlap
    seam = choose_seam(a_cues, t, overlap, b_cues)
    # compare against everything each decode has up to the far edge of the shared window,
    # not only cues inside it, so a cue that starts early is not "recovered" as new
    a_win_text = " ".join(c["text"] for c in a_cues if c["start"] < hi)
    b_win_text = " ".join(c["text"] for c in b_cues if c["start"] < hi)
    a_grams, b_grams = _grams(a_win_text), _grams(b_win_text)
    kept_a, kept_b, recovered = [], [], []
    for c in a_cues:
        if c["start"] < seam:
            kept_a.append(c)
        else:
            if not _caught_elsewhere(c["text"], b_grams, b_win_text):
                kept_a.append({**c, "seam_recovered": True})
                recovered.append({"from": "A", "start": c["start"], "text": c["text"]})
    for c in b_cues:
        if c["start"] >= seam:
            kept_b.append(c)
        else:
            if not _caught_elsewhere(c["text"], a_grams, a_win_text):
                kept_b.append({**c, "seam_recovered": True})
                recovered.append({"from": "B", "start": c["start"], "text": c["text"]})
    record = {"nominal": t, "seam": round(seam, 3), "kept_a": len(kept_a), "kept_b": len(kept_b),
              "recovered": recovered}
    return kept_a, kept_b, record


def merge_segments(seg_cues: list[list[dict]], core: int = SEG_CORE, overlap: int = SEG_OVERLAP) -> tuple[list[dict], list[dict]]:
    """seg_cues[i] are absolute-time cues for segment i. Returns (merged cues, seam records)."""
    if not seg_cues:
        return [], []
    merged = list(seg_cues[0])
    records = []
    for i in range(1, len(seg_cues)):
        t = float(i * core)
        cut = min([t - overlap] + [c["start"] for c in seg_cues[i]])
        prev_tail = [c for c in merged if c["end"] > cut]
        head = [c for c in merged if c["end"] <= cut]
        kept_a, kept_b, rec = merge_pair(prev_tail, seg_cues[i], t, overlap)
        merged = head + kept_a + kept_b
        records.append(rec)
    merged.sort(key=lambda c: (c["start"], c["end"]))
    return merged, records


def detect_anomalies(cues: list[dict], duration: float, segments: list[dict] | None = None) -> list[dict]:
    anomalies = []

    def add(kind, start, end, detail):
        anomalies.append({"id": f"A{len(anomalies) + 1}", "kind": kind, "start": round(start, 3),
                          "end": round(end, 3), "detail": detail, "disposition": None, "note": ""})

    for s in segments or []:
        if s.get("status") == "failed":
            add("segment_failed", s["start"], s["end"], f"{s['id']} failed after retry")

    i = 0
    while i < len(cues):
        j = i
        key = normalize(cues[i]["text"])
        while j + 1 < len(cues) and normalize(cues[j + 1]["text"]) == key:
            j += 1
        run_len = j - i + 1
        if key and (run_len >= REPEAT_RUN_LONG or (run_len >= REPEAT_RUN and len(key.split()) >= 2)):
            add("repeat_run", cues[i]["start"], cues[j]["end"], f"{run_len}x {cues[i]['text'][:80]!r}")
        i = j + 1

    w = 0.0
    while w < duration:
        we = min(duration, w + COV_WINDOW)
        span = we - w
        in_win = [c for c in cues if c["end"] > w and c["start"] < we]
        covered = sum(min(c["end"], we) - max(c["start"], w) for c in in_win)
        filler = [c for c in in_win if normalize(c["text"]) in FILLER_LINES]
        if span >= COV_WINDOW / 2 and covered / span < COV_MIN:
            add("low_coverage", w, we, f"{100 * covered / span:.0f}% of window covered by cues")
        if len(filler) >= FILLER_MIN:
            add("filler_run", filler[0]["start"], filler[-1]["end"], f"{len(filler)} filler cues")
        w = we
    return anomalies


def build_chunks(cues: list[dict], duration: float, holds: list[tuple[float, float]],
                 core: int = CHUNK_CORE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    def in_hold(t):
        return any(a <= t < b for a, b in holds)

    chunks = []
    k = 0
    start = 0.0
    while start < duration:
        core_end = min(duration, start + core)
        lo, hi = max(0.0, start - overlap), min(duration, core_end + overlap)
        lines = []
        for c in cues:
            if lo <= c["start"] < hi and not in_hold(c["start"]):
                tag = "" if start <= c["start"] < core_end else " [overlap]"
                lines.append(f"[{fmt_ts(c['start'])}]{tag} {c['text']}")
        notes = [f"NOTE: {fmt_ts(a)}-{fmt_ts(b)} was a hold screen; removed." for a, b in holds if a < hi and b > lo]
        header = [f"CHUNK {k:02d}", f"Core window: {fmt_ts(start)} - {fmt_ts(core_end)} (stream time)",
                  f"Overlap: {overlap // 60} min each side; lines marked [overlap] belong to a neighbouring chunk's core.",
                  *notes, ""]
        chunks.append({"id": f"{k:02d}", "core_start": start, "core_end": core_end,
                       "text": "\n".join(header + lines) + "\n",
                       "words": sum(len(l.split()) - 1 for l in lines), "lines": len(lines)})
        k += 1
        start += core
    return chunks


def wav_paths_to_delete(cache_dir: Path) -> list[Path]:
    """Only *.wav files under the slug cache dir. Never the source audio."""
    return sorted(p for p in cache_dir.rglob("*.wav") if p.is_file())


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def paths(slug: str, date: str, root: Path | None = None, cache: Path | None = None) -> dict:
    root = root or Path(os.environ.get("STREAM_ROOT", repo_root()))
    cache = cache or Path(os.environ.get("STREAM_CACHE", Path.home() / ".cache" / "stream-analysis"))
    base = root / "data" / "source-transcripts"
    d = base / f"{date}-{slug}"
    return {"cache": cache / slug, "dir": d, "manifest": d / "manifest.json",
            "segments": d / "segments", "chunks": d / "chunks", "vtt": base / f"{date}-{slug}.vtt"}


def load_manifest(p: dict) -> dict:
    if not p["manifest"].exists():
        emit({"ok": False, "error": f"no manifest at {p['manifest']}; run acquire first"}, 2)
    return json.loads(p["manifest"].read_text(encoding="utf-8"))


def save_manifest(p: dict, m: dict) -> None:
    p["dir"].mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p["dir"], suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(tmp, p["manifest"])


def emit(obj: dict, code: int = 0):
    print(json.dumps(obj, indent=1))
    sys.exit(code)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def duration_of(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


def acquire_cmd(url: str) -> list[str]:
    return ["yt-dlp", "-f", "bestaudio/best", "-x", "-k", "--audio-format", "m4a", "-o", "source.%(ext)s", url]


def cmd_acquire(a):
    p = paths(a.slug, a.date)
    p["cache"].mkdir(parents=True, exist_ok=True)
    r = subprocess.run(acquire_cmd(a.url), cwd=p["cache"], capture_output=True, text=True)
    src = p["cache"] / "source.m4a"
    if r.returncode != 0 or not src.exists():
        err = "yt-dlp failed; if HTTP 402/403, retry with cookies"
        if "audio codec" in r.stderr or "no audio" in r.stderr.lower():
            err = "source has no audio track (video only); there is nothing to transcribe"
        emit({"ok": False, "error": err, "stderr_tail": r.stderr[-800:]}, 1)
    m = {"slug": a.slug, "date": a.date, "source_url": a.url, "media_path": str(src),
         "media_sha256": sha256(src), "media_duration_s": duration_of(src),
         "segments": [], "seams": [], "anomalies": [], "chunks": [], "history": [["acquire", a.url]]}
    save_manifest(p, m)
    emit({"ok": True, "media": str(src), "duration": fmt_ts(m["media_duration_s"])})


def cmd_transcribe(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    src = Path(m["media_path"])
    wav = p["cache"] / "source.wav"
    if not wav.exists():
        r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        if r.returncode != 0:
            emit({"ok": False, "error": "ffmpeg wav conversion failed", "stderr": r.stderr[-800:]}, 1)
    p["segments"].mkdir(parents=True, exist_ok=True)
    segs = segment_ranges(m["media_duration_s"])
    seg_cues = []
    for s in segs:
        seg_wav = p["cache"] / f"{s['id']}.wav"
        out_base = p["segments"] / s["id"]
        status, cues = "failed", []
        for attempt in (1, 2):
            r1 = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(s["start"]),
                      "-t", str(s["end"] - s["start"]), "-i", str(wav), "-c", "copy", str(seg_wav)])
            r2 = run(["whisper-cli", "-m", str(a.model), "-f", str(seg_wav), "-l", "en", "-mc", "0",
                      "-ovtt", "-of", str(out_base), "-np"]) if r1.returncode == 0 else r1
            vtt = out_base.with_suffix(".vtt")
            if r2.returncode == 0 and vtt.exists():
                cues = [{**c, "start": c["start"] + s["start"], "end": c["end"] + s["start"]}
                        for c in parse_vtt(vtt.read_text(encoding="utf-8"))]
                if cues:
                    status = "ok"
                    break
        s.update({"status": status, "attempts": attempt, "cue_count": len(cues)})
        seg_cues.append(cues)
        print(json.dumps({"segment": s["id"], "status": status, "cues": len(cues)}), file=sys.stderr)
    merged, seams = merge_segments(seg_cues)
    p["vtt"].write_text(render_vtt(merged), encoding="utf-8")
    m.update({"segments": segs, "seams": seams, "anomalies": [], "chunks": []})
    m["history"].append(["transcribe", len(segs)])
    save_manifest(p, m)
    emit({"ok": all(s["status"] == "ok" for s in segs), "segments": len(segs),
          "failed": [s["id"] for s in segs if s["status"] != "ok"], "cues": len(merged),
          "seam_recovered_cues": sum(len(r["recovered"]) for r in seams), "vtt": str(p["vtt"])})


def cmd_validate(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    cues = parse_vtt(p["vtt"].read_text(encoding="utf-8"))
    anomalies = detect_anomalies(cues, m["media_duration_s"], m.get("segments"))
    m["anomalies"] = anomalies
    m["history"].append(["validate", len(anomalies)])
    save_manifest(p, m)

    def context(an):
        before = [c for c in cues if an["start"] - 60 <= c["start"] < an["start"]][-5:]
        after = [c for c in cues if an["end"] < c["start"] <= an["end"] + 60][:5]
        return {"before": [f"{fmt_ts(c['start'])} {c['text']}" for c in before],
                "after": [f"{fmt_ts(c['start'])} {c['text']}" for c in after]}

    emit({"ok": True, "anomalies": [{**an, "start": fmt_ts(an["start"]), "end": fmt_ts(an["end"]),
                                     **context(an)} for an in anomalies]})


def cmd_dispose(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    hit = [an for an in m["anomalies"] if an["id"] == a.id]
    if not hit:
        emit({"ok": False, "error": f"no anomaly {a.id}"}, 2)
    an = hit[0]
    if a.disposition == "redo":
        wav = p["cache"] / "source.wav"
        lo, hi = max(0.0, an["start"] - 30), min(m["media_duration_s"], an["end"] + 30)
        span_wav = p["cache"] / f"redo-{an['id']}.wav"
        out_base = p["segments"] / f"redo-{an['id']}"
        r1 = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(lo), "-t", str(hi - lo),
                  "-i", str(wav), "-c", "copy", str(span_wav)])
        r2 = run(["whisper-cli", "-m", str(a.model), "-f", str(span_wav), "-l", "en", "-mc", "0",
                  "-ovtt", "-of", str(out_base), "-np"]) if r1.returncode == 0 else r1
        if r2.returncode != 0:
            emit({"ok": False, "error": "redo transcription failed", "stderr": r2.stderr[-800:]}, 1)
        new = [{**c, "start": c["start"] + lo, "end": c["end"] + lo}
               for c in parse_vtt(out_base.with_suffix(".vtt").read_text(encoding="utf-8"))]
        old = parse_vtt(p["vtt"].read_text(encoding="utf-8"))
        spliced = sorted([c for c in old if not (lo <= c["start"] < hi)] + new, key=lambda c: c["start"])
        p["vtt"].write_text(render_vtt(spliced), encoding="utf-8")
        an["redo_cues"] = len(new)
    an["disposition"] = a.disposition
    an["note"] = a.note or ""
    m["history"].append(["dispose", a.id, a.disposition])
    save_manifest(p, m)
    emit({"ok": True, "anomaly": an, "remaining": [x["id"] for x in m["anomalies"] if not x["disposition"]],
          "hint": "run validate again after redo to re-check the spliced span" if a.disposition == "redo" else ""})


def cmd_chunk(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    open_ = [an["id"] for an in m.get("anomalies", []) if not an.get("disposition")]
    if open_:
        emit({"ok": False, "error": "undisposed anomalies; run dispose first", "open": open_}, 2)
    cues = parse_vtt(p["vtt"].read_text(encoding="utf-8"))
    holds = [(an["start"], an["end"]) for an in m["anomalies"] if an["disposition"] == "hold_screen"]
    chunks = build_chunks(cues, m["media_duration_s"], holds)
    p["chunks"].mkdir(parents=True, exist_ok=True)
    index = []
    for c in chunks:
        (p["chunks"] / f"chunk-{c['id']}.txt").write_text(c["text"], encoding="utf-8")
        index.append({k: c[k] for k in ("id", "core_start", "core_end", "words", "lines")})
    (p["chunks"] / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    m["chunks"] = index
    m["history"].append(["chunk", len(chunks)])
    save_manifest(p, m)
    emit({"ok": True, "chunks": index})


def cmd_clean_wav(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    targets = wav_paths_to_delete(p["cache"])
    freed = 0
    for t in targets:
        freed += t.stat().st_size
        t.unlink()
    m["history"].append(["clean-wav", len(targets)])
    save_manifest(p, m)
    kept = [str(x) for x in p["cache"].iterdir()] if p["cache"].exists() else []
    emit({"ok": True, "deleted": [str(t) for t in targets], "freed_mb": round(freed / 1e6, 1), "kept": kept})


def cmd_status(a):
    p = paths(a.slug, a.date)
    m = load_manifest(p)
    emit({"ok": True, "duration": fmt_ts(m["media_duration_s"]), "segments": len(m["segments"]),
          "failed_segments": [s["id"] for s in m["segments"] if s.get("status") != "ok"],
          "anomalies": len(m["anomalies"]),
          "undisposed": [an["id"] for an in m["anomalies"] if not an.get("disposition")],
          "chunks": len(m["chunks"]), "history": m["history"][-5:]})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--slug", required=True)
        sp.add_argument("--date", required=True)
        sp.add_argument("--model", type=Path, default=DEFAULT_MODEL)

    sp = sub.add_parser("acquire"); sp.add_argument("url"); common(sp); sp.set_defaults(fn=cmd_acquire)
    for name, fn in (("transcribe", cmd_transcribe), ("validate", cmd_validate), ("chunk", cmd_chunk),
                     ("clean-wav", cmd_clean_wav), ("status", cmd_status)):
        sp = sub.add_parser(name); common(sp); sp.set_defaults(fn=fn)
    sp = sub.add_parser("dispose"); common(sp)
    sp.add_argument("--id", required=True)
    sp.add_argument("--as", dest="disposition", required=True, choices=DISPOSITIONS)
    sp.add_argument("--note", default="")
    sp.set_defaults(fn=cmd_dispose)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
