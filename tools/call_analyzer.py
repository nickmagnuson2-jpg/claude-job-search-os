#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
call_analyzer.py - Transcript analysis engine for interview call debriefs.

Analyzes Granola transcript segments for filler words, Q&A pair structure,
talk ratio, and word counts. Consumed by granola_auto_debrief.py (launchd) and
standalone CLI use.

Functions:
  parse_granola_text(text) - Labelled transcript string to segment dicts
  count_fillers(text) - Count filler words using regex patterns from D-06
  parse_qa_pairs(transcript_segments) - Convert segments into Q&A pairs
  analyze_transcript(transcript_segments) - Full analysis returning structured dict
  load_segments(data) - Decoded JSON input to segments; ValueError on a bad shape

CLI:
  python3 tools/call_analyzer.py <transcript.json>
  python3 tools/call_analyzer.py --stdin

Output: JSON to stdout. Errors to stderr.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# granola_auto_debrief.py imports this file as `tools.call_analyzer` with only the repo
# root on the path, so the sibling import needs tools/ added. Pinned by a
# fresh-interpreter test.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from meeting_vocab import channel_turns, segment_channel  # noqa: E402  single source of truth

_SOURCE_BY_CHANNEL = {"Me": "microphone", "Them": "speaker"}
# Speaker value for text no label attributes. Any string that is not a channel label
# works; meeting_vocab.segment_channel maps it to None.
UNATTRIBUTED = "Unattributed"

# Filler patterns from D-06 tracked word list.
# "pretty" only counts when followed by a qualifying word (hedge usage).
FILLER_PATTERNS = {
    "really": r'\breally\b',
    "kind of": r'\bkind\s+of\b|\bkinda\b',
    "definitely": r'\bdefinitely\b',
    "to be honest with you": r'\bto\s+be\s+honest\s+with\s+you\b',
    "absolutely": r'\babsolutely\b',
    "pretty": r'\bpretty\s+(?:much|good|well|big|bad|sure|clear|easy|hard|tough|close|far)',
}


def parse_granola_text(transcript_text: str) -> list:
    """Convert a labelled Granola transcript string into segment dicts.

    Granola returns a transcript as one string with a speaker label ahead of each
    turn. Label decoding is meeting_vocab.channel_turns, shared with
    filler_baseline.py, granola_save.py and transcript_exclusions.py, so every
    form it knows is handled here: `Me:` / `Them:`, `Microphone:` / `Speaker:`,
    `System audio:` and the named forms `System audio (Name):` /
    `Microphone (Name):`. Turns with no words are dropped.

    Text the labels do not attribute is kept, as a segment whose speaker is
    UNATTRIBUTED: text ahead of the first label, anonymous diarization turns
    (`Speaker A:`), and a transcript with no labels at all. analyze_transcript counts
    those words as unattributed, so such a transcript does not read as a silent call.

    Args:
        transcript_text: Raw string like "Me: hello Them: hi Me: ..."

    Returns:
        List of {"speaker": {"source": "microphone"|"speaker"} | UNATTRIBUTED, "text": "..."}
    """
    if not transcript_text or not transcript_text.strip():
        return []

    turns = channel_turns(transcript_text) or [(None, transcript_text, 0)]
    segments = []
    for label, body, _ in turns:
        text = body.strip()
        if not text:
            continue
        speaker = {"source": _SOURCE_BY_CHANNEL[label]} if label else UNATTRIBUTED
        segments.append({"speaker": speaker, "text": text})
    return segments


def count_fillers(text: str) -> dict:
    """Count filler words in text using regex patterns.

    Case-insensitive matching. Returns only fillers with count > 0.

    Args:
        text: Raw text to scan for fillers.

    Returns:
        Dict mapping filler name to occurrence count (only non-zero entries).
    """
    text_lower = text.lower()
    counts = {}
    for filler, pattern in FILLER_PATTERNS.items():
        matches = re.findall(pattern, text_lower)
        if matches:
            counts[filler] = len(matches)
    return counts


def _attributed_turns(transcript_segments) -> tuple[list, int]:
    """Reduce raw segments to [(channel, text)] plus the unattributed word count.

    The one place the analysis decides who said what. Attribution is
    meeting_vocab.segment_channel, the same rule the persist path and every transcript
    reader use. Segments with no words are dropped; a segment whose speaker cannot be
    attributed is left out of the turns and its words are counted, so a transcript the
    analysis cannot read reports that instead of looking like a quiet call.
    """
    turns, unattributed = [], 0
    for seg in transcript_segments or []:
        if not isinstance(seg, dict):
            continue
        text = seg.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        channel = segment_channel(seg)
        if channel is None:
            unattributed += len(text.split())
        else:
            turns.append((channel, text))
    return turns, unattributed


def _pair_turns(turns: list) -> list:
    """Q&A pairs from [(channel, text)]: merge same-speaker runs, then pair."""
    merged = []
    for channel, text in turns:
        if merged and merged[-1][0] == channel:
            merged[-1][1] += " " + text
        else:
            merged.append([channel, text])

    pairs = []
    current_question = None
    for channel, text in merged:
        if channel == "Them":
            current_question = text
        elif current_question is not None:
            pairs.append({"question": current_question, "answer": text})
            current_question = None
        else:
            # Candidate speaking without preceding question
            pairs.append({"question": "(unprompted / opening)", "answer": text})
    return pairs


def parse_qa_pairs(transcript_segments: list) -> list:
    """Convert Granola transcript segments into Q&A pairs.

    First merges consecutive segments from the same speaker, then pairs each
    counterpart (interviewer) block with the following owner (candidate) block.
    Segments with no words or no attributable speaker take no part.

    Args:
        transcript_segments: List of segment dicts with "speaker" and "text".

    Returns:
        List of {"question": str, "answer": str} dicts.
    """
    return _pair_turns(_attributed_turns(transcript_segments)[0])


def analyze_transcript(transcript_segments: list) -> dict:
    """Run full analysis on transcript segments.

    Args:
        transcript_segments: List of Granola transcript segment dicts.

    Returns:
        Dict with keys: filler_counts, qa_pairs, total_questions,
        candidate_word_count, interviewer_word_count, unattributed_word_count,
        talk_ratio. talk_ratio is the candidate's share of ATTRIBUTED words.
    """
    turns, unattributed_word_count = _attributed_turns(transcript_segments)
    candidate_text = " ".join(text for channel, text in turns if channel == "Me")
    interviewer_text = " ".join(text for channel, text in turns if channel == "Them")

    candidate_word_count = len(candidate_text.split())
    interviewer_word_count = len(interviewer_text.split())
    total_words = candidate_word_count + interviewer_word_count
    qa_pairs = _pair_turns(turns)

    return {
        "filler_counts": count_fillers(candidate_text),
        "qa_pairs": qa_pairs,
        "total_questions": len(qa_pairs),
        "candidate_word_count": candidate_word_count,
        "interviewer_word_count": interviewer_word_count,
        "unattributed_word_count": unattributed_word_count,
        "talk_ratio": round(candidate_word_count / total_words, 2) if total_words else 0.0,
    }


def load_segments(data) -> list:
    """Segments from decoded JSON input. Raises ValueError on a shape it cannot use.

    Accepts a segment array, an object with a `transcript` that is an array or a
    labelled string, or a bare labelled string. Strict about array contents: a list of
    strings is the wrong file, and analysing it would print a clean all-zero report.
    """
    if isinstance(data, dict) and "transcript" in data:
        data = data["transcript"]
        if not isinstance(data, (str, list)):
            raise ValueError("unexpected transcript format")
    if isinstance(data, str):
        return parse_granola_text(data)
    if isinstance(data, list):
        if not all(isinstance(seg, dict) for seg in data):
            raise ValueError("transcript segments must be objects with 'speaker' and 'text'")
        return data
    raise ValueError("input must be a JSON array, object with 'transcript', or string")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze interview transcript for fillers, Q&A pairs, and talk ratio."
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to JSON file containing transcript segments array",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON from stdin instead of file",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits on --help (0) and on usage errors (2). Return the code so
        # main() never raises; argparse has already printed its message.
        return exc.code if isinstance(exc.code, int) else 2

    if not args.file and not args.stdin:
        parser.print_help(sys.stderr)
        return 1
    if args.file and args.stdin:
        print("Error: give a file or --stdin, not both", file=sys.stderr)
        return 1

    try:
        if args.stdin:
            raw = sys.stdin.read()
        else:
            with open(args.file, "r", encoding="utf-8") as f:
                raw = f.read()
        result = analyze_transcript(load_segments(json.loads(raw)))
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON - {e}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: file not found - {args.file}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
