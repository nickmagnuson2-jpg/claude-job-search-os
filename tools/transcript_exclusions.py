#!/usr/bin/env python3
"""
transcript_exclusions.py — Find sentences where the counterpart ruled a domain OUT.

WHY. A prep doc pre-bound a single proof and told the reader not to substitute it.
Partway into the call the counterpart ruled that entire domain out of scope and called
the proof's central deliverable commoditizable. The follow-up, drafted from the full
transcript, led with it anyway. Nobody re-read the counterpart's turns looking for
exclusions, because nothing asked them to.

SPLIT OF LABOR. Detecting *literal* exclusion phrasings is deterministic and belongs
here. Judging whether a detected exclusion overlaps the bound proof's domain is
semantic and stays with the model. **This tool is a floor, not a clearance** — which is
why every output carries a `coverage` string saying so, and why `hit_count: 0` is not
permission to deploy a pre-bound proof.

Exit codes: 0 on a readable transcript (zero hits is a valid answer) | 1 if the file is
missing or has no `## Verbatim transcript` section.
"""
import argparse
import json
import re
from pathlib import Path

LOCAL_VALIDATORS = "tools/.local-validators.json"

COVERAGE = ("literal phrase list v1; paraphrased exclusions are NOT detected — "
            "read the counterpart's turns")

PHRASE_LIST_VERSION = 1
# Anchored with \b and matched case-insensitively. Deliberately literal: these are
# phrasings observed in real calls, not a general "negation" detector.
PHRASES = [
    r"I would never",
    r"I'd never",
    r"we would never",
    r"we will never",
    r"we don't do",
    r"we do not do",
    r"we're not doing",
    r"we are not doing",
    r"not what we do",
    # The commoditization family is matched on the word itself, not on an enumerated
    # set of auxiliaries. The spec's v1 list had `gets/is/would get commoditized`, and
    # the sentence that motivated this tool used "will get" — matching none of them.
    # Enumerating auxiliaries is a losing game; a counterpart calling any part of the
    # work commoditized is always worth surfacing.
    r"commoditi[sz]ed",
]
# Near-misses that must NOT fire. "never mind" / "I've never seen" are conversational,
# not exclusions; matching them would train Nick to ignore the output.
_PHRASE_RES = [(re.compile(rf"\b{p}\b", re.I), p) for p in PHRASES]

# --wide: low-precision second pass, kept in a SEPARATE array. Never merged into hits.
_WIDE_RE = re.compile(r"never|not\s|don't|won't|no longer|commoditi|table stakes", re.I)

# Granola writes Me:/Them: for most sessions and Microphone:/Speaker: for others
# (26 vs 6 of the 57 transcript-bearing files as of 2026-08-12). Microphone is always
# the note creator, i.e. Nick, so it maps to Me.
_SPEAKER_ALIASES = {"me": "Me", "them": "Them", "microphone": "Me", "speaker": "Them"}
_SEGMENT_SPLIT_RE = re.compile(r"(?=\b(?:Me|Them|Microphone|Speaker):)", re.I)
_SEGMENT_HEAD_RE = re.compile(r"^(Me|Them|Microphone|Speaker):\s*", re.I)

_TRANSCRIPT_HEADING_RE = re.compile(r"^##\s+Verbatim transcript\s*$", re.I)
# The body ENDS at the next heading or horizontal rule. Verified against a live file:
# the transcript is followed by `---` and a `## Granola Private Notes` section holding
# Nick's TYPED notes, which carry no speaker markers. "Everything after the heading"
# swallows them into the final speaker segment and can report typed text as something
# the interviewer said out loud.
_BODY_END_RE = re.compile(r"^(##\s|---\s*$)")

_SPEAKER_LABELS_RE = re.compile(r"^>\s*\*\*Speaker labels:\*\*\s*(?P<note>.+)$", re.I)


class TranscriptError(Exception):
    """Missing file or no transcript section (exit 1)."""


def extract_body(text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _TRANSCRIPT_HEADING_RE.match(line.strip()):
            start = i + 1
            break
    if start is None:
        raise TranscriptError("no '## Verbatim transcript' section")
    end = len(lines)
    for j in range(start, len(lines)):
        if _BODY_END_RE.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def parse_speaker_labels(text: str) -> dict:
    """Resolve Me/Them to real names from the `> **Speaker labels:**` note, if present.

    The note is free prose written by a human, so this is best-effort by design: an
    unresolvable note yields speaker_name None and never fails the run.
    """
    note = None
    for line in text.splitlines():
        m = _SPEAKER_LABELS_RE.match(line.strip())
        if m:
            note = m.group("note")
            break
    if not note:
        return {}

    names = {}
    for label, canon in (("me", "Me"), ("them", "Them"),
                         ("microphone", "Me"), ("speaker", "Them")):
        # Matches "Me = Nick", "Them: = Jane Doe", "'Microphone' = Nick".
        m = re.search(rf"['\"`]?{label}['\"`]?:?\s*=\s*([^.;,]+)", note, re.I)
        if m and canon not in names:
            names[canon] = m.group(1).strip().strip("*`'\" ")
    return names


def split_segments(body: str) -> list[tuple[str, str, int]]:
    """(speaker_label, segment_text, char_offset) triples, offsets from the body start."""
    segments, offset = [], 0
    for chunk in _SEGMENT_SPLIT_RE.split(body):
        if not chunk:
            continue
        head = _SEGMENT_HEAD_RE.match(chunk)
        if head:
            label = _SPEAKER_ALIASES[head.group(1).casefold()]
            text_start = offset + head.end()
            segments.append((label, chunk[head.end():], text_start))
        elif chunk.strip():
            # Leading text before any marker (or an undiarized transcript).
            segments.append((None, chunk, offset))
        offset += len(chunk)
    return segments


def _sentences(segment: str) -> list[tuple[str, int]]:
    """(sentence, offset-within-segment). ASR is punctuation-sparse: no terminator
    means the whole segment is one sentence."""
    out, pos = [], 0
    for part in re.split(r"(?<=[.?!])\s+", segment):
        if part.strip():
            out.append((part.strip(), segment.find(part, pos)))
        pos += len(part)
    return out


def scan(path: Path, speaker_filter: str = "Them", wide: bool = False) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript: {exc}") from exc

    body = extract_body(text)
    names = parse_speaker_labels(text)
    segments = split_segments(body)

    hits, candidates = [], []
    for label, segment, seg_offset in segments:
        if speaker_filter != "all" and label != speaker_filter:
            continue
        for sentence, rel in _sentences(segment):
            entry = {
                "sentence": sentence,
                "speaker_label": label,
                "speaker_name": names.get(label),
                "char_offset": seg_offset + max(rel, 0),
            }
            matched = next((p for regex, p in _PHRASE_RES if regex.search(sentence)), None)
            if matched:
                hits.append({**entry, "matched_phrase": matched})
            elif wide and _WIDE_RE.search(sentence):
                candidates.append(entry)

    return {
        "transcript": str(path),
        "speaker_filter": speaker_filter,
        "hits": hits,
        "hit_count": len(hits),
        "candidates": candidates,
        "deduped_from": [],
        "speaker_markers_found": any(label for label, _, _ in segments),
        "coverage": COVERAGE,
    }


def dedup(primary: dict, secondary: dict) -> dict:
    """Union two scans of the same call by normalized sentence, keeping earliest offset.

    The same conversation is often recorded twice minutes apart; without this the
    reviewer sees each exclusion twice and starts skimming.
    """
    def key(hit):
        return re.sub(r"\s+", " ", hit["sentence"]).casefold()

    merged = {}
    for hit in primary["hits"] + secondary["hits"]:
        existing = merged.get(key(hit))
        if existing is None or hit["char_offset"] < existing["char_offset"]:
            merged[key(hit)] = hit

    out = dict(primary)
    out["hits"] = sorted(merged.values(), key=lambda h: h["char_offset"])
    out["hit_count"] = len(out["hits"])
    out["candidates"] = primary["candidates"] + secondary["candidates"]
    out["deduped_from"] = [primary["transcript"], secondary["transcript"]]
    return out


def validate_local() -> tuple[int, dict]:
    vpath = Path(LOCAL_VALIDATORS)
    if not vpath.exists():
        return 0, {"status": "SKIPPED", "reason": f"{LOCAL_VALIDATORS} absent"}

    cases = json.loads(vpath.read_text(encoding="utf-8")).get("w3", [])
    if not cases:
        return 0, {"status": "SKIPPED", "reason": f"no 'w3' cases in {LOCAL_VALIDATORS}"}

    results, failed = [], 0
    for case in cases:
        path = Path(case["transcript"])
        if not path.is_file():
            results.append({"transcript": case["transcript"], "pass": False,
                            "reason": "transcript not found"})
            failed += 1
            continue
        result = scan(path, "Them", wide=True)
        blob = " ".join(re.sub(r"\s+", " ", h["sentence"]) for h in result["hits"]).casefold()
        missing = [
            want for want in case.get("must_surface", [])
            if re.sub(r"\s+", " ", want).casefold() not in blob
        ]
        failed += bool(missing)
        results.append({
            "transcript": case["transcript"],
            "hit_count": result["hit_count"],
            "candidate_count": len(result["candidates"]),
            "matched_phrases": sorted({h["matched_phrase"] for h in result["hits"]}),
            "pass": not missing,
            "missing": missing,
        })

    return (1 if failed else 0), {
        "status": "FAIL" if failed else "PASS",
        "cases": len(results), "failed": failed, "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a transcript for domain exclusions.")
    # --transcript is optional so a bare --validate-local does not die in argparse.
    parser.add_argument("--transcript")
    parser.add_argument("--speaker", default="Them", choices=["Me", "Them", "all"])
    parser.add_argument("--include-self", action="store_true", dest="include_self",
                        help="alias for --speaker all")
    parser.add_argument("--dedup-with", dest="dedup_with")
    parser.add_argument("--wide", action="store_true",
                        help="add a low-precision candidates[] pass; never merged into hits")
    parser.add_argument("--validate-local", action="store_true", dest="validate_local")
    args = parser.parse_args()

    if args.validate_local:
        code, payload = validate_local()
        print(json.dumps(payload, ensure_ascii=False))
        return code

    if not args.transcript:
        print(json.dumps({"error": "--transcript is required (or use --validate-local)"}))
        return 1

    speaker = "all" if args.include_self else args.speaker
    try:
        result = scan(Path(args.transcript), speaker, args.wide)
        if args.dedup_with:
            result = dedup(result, scan(Path(args.dedup_with), speaker, args.wide))
    except TranscriptError as exc:
        print(json.dumps({"transcript": args.transcript, "error": str(exc)}))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
