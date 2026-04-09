#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
call_analyzer.py - Transcript analysis engine for interview call debriefs.

Analyzes Granola transcript segments for filler words, Q&A pair structure,
talk ratio, and word counts. Designed for headless n8n automation and
standalone CLI use.

Functions:
  count_fillers(text) - Count filler words using regex patterns from D-06
  parse_qa_pairs(transcript_segments) - Convert segments into Q&A pairs
  analyze_transcript(transcript_segments) - Full analysis returning structured dict

CLI:
  python tools/call_analyzer.py <transcript.json>
  python tools/call_analyzer.py --stdin

Output: JSON to stdout. Errors to stderr.
"""
import argparse
import json
import re
import sys

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
    """Convert Granola's 'Me: ... Them: ...' string format into segment dicts.

    Granola's get_meeting_transcript returns a single string with 'Me:' and
    'Them:' labels. This converts it into the structured segment list that
    parse_qa_pairs and analyze_transcript expect.

    Args:
        transcript_text: Raw string like "Me: hello Them: hi Me: ..."

    Returns:
        List of {"speaker": {"source": "microphone"|"speaker"}, "text": "..."}
    """
    if not transcript_text or not transcript_text.strip():
        return []

    # Split on Me: or Them: labels, keeping the label
    parts = re.split(r'\b(Me:|Them:)\s*', transcript_text.strip())
    segments = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if part == "Me:":
            if i + 1 < len(parts):
                text = parts[i + 1].strip()
                if text:
                    segments.append({"speaker": {"source": "microphone"}, "text": text})
                i += 2
            else:
                i += 1
        elif part == "Them:":
            if i + 1 < len(parts):
                text = parts[i + 1].strip()
                if text:
                    segments.append({"speaker": {"source": "speaker"}, "text": text})
                i += 2
            else:
                i += 1
        else:
            i += 1

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


def parse_qa_pairs(transcript_segments: list) -> list:
    """Convert Granola transcript segments into Q&A pairs.

    First merges consecutive segments from the same speaker source,
    then pairs each "speaker" (interviewer) block with the following
    "microphone" (candidate) block.

    Args:
        transcript_segments: List of dicts with "speaker.source" and "text".
            source "speaker" = interviewer, "microphone" = candidate.

    Returns:
        List of {"question": str, "answer": str} dicts.
    """
    if not transcript_segments:
        return []

    # Step 1: Merge consecutive segments from same speaker source
    merged = []
    for seg in transcript_segments:
        source = seg["speaker"]["source"]
        text = seg["text"].strip()
        if merged and merged[-1]["source"] == source:
            merged[-1]["text"] += " " + text
        else:
            merged.append({"source": source, "text": text})

    # Step 2: Pair speaker blocks with following microphone blocks
    pairs = []
    current_question = None
    for block in merged:
        if block["source"] == "speaker":
            current_question = block["text"]
        elif block["source"] == "microphone":
            if current_question is not None:
                pairs.append({
                    "question": current_question,
                    "answer": block["text"],
                })
                current_question = None
            else:
                # Candidate speaking without preceding question
                pairs.append({
                    "question": "(unprompted / opening)",
                    "answer": block["text"],
                })

    return pairs


def analyze_transcript(transcript_segments: list) -> dict:
    """Run full analysis on transcript segments.

    Args:
        transcript_segments: List of Granola transcript segment dicts.

    Returns:
        Dict with keys: filler_counts, qa_pairs, total_questions,
        candidate_word_count, interviewer_word_count, talk_ratio.
    """
    if not transcript_segments:
        return {
            "filler_counts": {},
            "qa_pairs": [],
            "total_questions": 0,
            "candidate_word_count": 0,
            "interviewer_word_count": 0,
            "talk_ratio": 0.0,
        }

    # Collect text by source
    candidate_texts = []
    interviewer_texts = []
    for seg in transcript_segments:
        source = seg["speaker"]["source"]
        text = seg["text"].strip()
        if source == "microphone":
            candidate_texts.append(text)
        elif source == "speaker":
            interviewer_texts.append(text)

    candidate_text = " ".join(candidate_texts)
    interviewer_text = " ".join(interviewer_texts)

    candidate_word_count = len(candidate_text.split()) if candidate_text else 0
    interviewer_word_count = len(interviewer_text.split()) if interviewer_text else 0
    total_words = candidate_word_count + interviewer_word_count

    filler_counts = count_fillers(candidate_text)
    qa_pairs = parse_qa_pairs(transcript_segments)

    talk_ratio = round(candidate_word_count / total_words, 2) if total_words > 0 else 0.0

    return {
        "filler_counts": filler_counts,
        "qa_pairs": qa_pairs,
        "total_questions": len(qa_pairs),
        "candidate_word_count": candidate_word_count,
        "interviewer_word_count": interviewer_word_count,
        "talk_ratio": talk_ratio,
    }


def main():
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

    args = parser.parse_args()

    if not args.file and not args.stdin:
        parser.print_help(sys.stderr)
        sys.exit(1)

    try:
        if args.stdin:
            raw = sys.stdin.read()
        else:
            with open(args.file, "r", encoding="utf-8") as f:
                raw = f.read()

        data = json.loads(raw)
        if isinstance(data, list):
            segments = data
        elif isinstance(data, dict) and "transcript" in data:
            transcript = data["transcript"]
            if isinstance(transcript, str):
                segments = parse_granola_text(transcript)
            elif isinstance(transcript, list):
                segments = transcript
            else:
                print("Error: unexpected transcript format", file=sys.stderr)
                sys.exit(1)
        elif isinstance(data, str):
            segments = parse_granola_text(data)
        else:
            print("Error: input must be a JSON array, object with 'transcript', or string", file=sys.stderr)
            sys.exit(1)

        result = analyze_transcript(segments)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON - {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: file not found - {args.file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
