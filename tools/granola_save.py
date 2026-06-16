#!/usr/bin/env python3
"""granola_save.py — Write Granola meeting content to two voice-tier-separated files.

Per the two-tier capture discipline (framework/two-tier-capture.md): the verbatim
transcript and Granola's AI summary are different voice tiers and must NOT co-habit
one file. This tool writes both as a wiki-linked pair:

  <output>.md          — voice-pure (transcript + private_notes if present)
  <output>-summary.md  — cloud-generated (Granola AI summary)

Voice classifications:
  therapy     -> source-of-truth (transcript) | cloud-generated (summary), both sealed
  recruiter   -> mixed-voice (transcript)     | cloud-generated (summary), not sealed
  networking  -> mixed-voice (transcript)     | cloud-generated (summary), not sealed
  general     -> not persisted; surface in chat only

Subcommands:
  write        Create the two-file pair from a JSON payload.

JSON shape (passed via --input file or stdin):
  {
    "meeting_id": "uuid",
    "title": "Provider - Couples Therapy",
    "captured": "2026-05-01 23:05",
    "transcript": "...",                    // verbatim (Me:/Them: or Speaker A/B)
    "summary": "...",                       // Granola's AI summary, markdown
    "private_notes": "..." (optional),       // Notes Nick typed in Granola (his voice)
    "type": "therapy" | "recruiter" | "networking" | "general",
    "session_desc": "...",                  // Free-text label (e.g. therapist name)
    "speaker_note": "..." (optional)         // Free-text re: speaker labels
  }

Example:
  echo '{...}' | PYTHONIOENCODING=utf-8 python3 tools/granola_save.py write \\
    --output ~/Documents/Obsidian/30-projects/personal/data/therapy/2026-05-04-therapy-provider-transcript.md
  # Writes both -transcript.md AND -transcript-summary.md, with wiki-links between them.
"""
import argparse
import json
import os
import sys
from pathlib import Path


SEALED_HEADER_THERAPY = """> **SEALED.** This is sensitive personal therapy material. Per `~/Documents/Obsidian/30-projects/personal/CLAUDE.md`, contents of `personal/data/therapy/` never appear in CVs, cover letters, recruiter prep, voice exports, networking notes, or any external-facing artifact. The verbatim transcript is preserved here as source-of-truth; do not paraphrase or excerpt outside this folder."""

RECRUITER_HEADER = """> **Voice classification:** mixed-voice (Nick + counterpart, verbatim Granola transcript). Per `framework/two-tier-capture.md`. Synthesized debriefs live in `coaching/progress-recruiter/`; this file is the raw tier."""

NETWORKING_HEADER = """> **Voice classification:** mixed-voice (Nick + counterpart, verbatim Granola transcript). Per `framework/two-tier-capture.md`. Synthesis (if any) lives in the relevant `data/company-notes/` or `data/networking.md` entries."""

GENERAL_HEADER = """> **Voice classification:** Granola-captured meeting transcript. Per `framework/two-tier-capture.md`."""

TRANSCRIPT_HEADERS = {
    "therapy": SEALED_HEADER_THERAPY,
    "recruiter": RECRUITER_HEADER,
    "networking": NETWORKING_HEADER,
    "general": GENERAL_HEADER,
}

TRANSCRIPT_VOICE = {
    "therapy": "source-of-truth",
    "recruiter": "mixed-voice",
    "networking": "mixed-voice",
    "general": "mixed-voice",
}

SEALED_FLAG = {
    "therapy": "true",
    "recruiter": "false",
    "networking": "false",
    "general": "false",
}


def write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def transcript_frontmatter(data: dict) -> str:
    type_ = data.get("type", "general")
    return (
        "---\n"
        f"voice: {TRANSCRIPT_VOICE[type_]}\n"
        "source: granola\n"
        f"captured: {data['captured']}\n"
        f"session: {data['session_desc']}\n"
        f"sealed: {SEALED_FLAG[type_]}\n"
        "---\n"
    )


def summary_frontmatter(data: dict, transcript_basename: str) -> str:
    type_ = data.get("type", "general")
    return (
        "---\n"
        "voice: cloud-generated\n"
        "source: granola\n"
        f"captured: {data['captured']}\n"
        f"session: {data['session_desc']}\n"
        f"sealed: {SEALED_FLAG[type_]}\n"
        f"derived_from: {transcript_basename}\n"
        "---\n"
    )


def transcript_body(data: dict, summary_basename: str) -> str:
    type_ = data.get("type", "general")
    parts = [TRANSCRIPT_HEADERS[type_]]
    if data.get("speaker_note"):
        parts.append("")
        parts.append(f"> **Speaker labels:** {data['speaker_note']}")
    parts.append("")
    parts.append(f"# {data['title']} — {data['captured'][:10]}")
    parts.append("")
    parts.append(f"**Granola meeting ID:** `{data['meeting_id']}`")
    parts.append("")
    parts.append("## Verbatim transcript")
    parts.append("")
    parts.append(data["transcript"].strip())
    if data.get("private_notes"):
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## Granola Private Notes")
        parts.append("")
        parts.append("> Notes Nick typed into Granola during or after the session. Voice-pure on Nick's side; preserve verbatim.")
        parts.append("")
        parts.append("```")
        parts.append(data["private_notes"].strip())
        parts.append("```")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(f"*Granola AI summary at [[{summary_basename}]] (separate file per voice-purity).*")
    return "\n".join(parts) + "\n"


def summary_body(data: dict, transcript_basename: str) -> str:
    type_ = data.get("type", "general")
    if type_ == "therapy":
        boundary = (
            "> **SEALED.** This is sensitive personal therapy material. Per "
            "`~/Documents/Obsidian/30-projects/personal/CLAUDE.md`, contents of "
            "`personal/data/therapy/` never appear in CVs, cover letters, recruiter prep, "
            "voice exports, networking notes, or any external-facing artifact."
        )
    else:
        boundary = (
            "> **Voice classification:** cloud-generated synthesis (Granola AI). "
            f"Per `framework/two-tier-capture.md`. The voice-pure raw transcript lives at "
            f"`[[{transcript_basename}]]`."
        )
    parts = [
        boundary,
        "",
        f"> **Companion to** [[{transcript_basename}]] (voice-pure transcript). Granola generated this summary from the transcript above. Synthesis is Granola's, not Nick's voice. The verbatim transcript is the source-of-truth tier per `framework/two-tier-capture.md`.",
        "",
        f"# {data['title']} — Granola AI Summary — {data['captured'][:10]}",
        "",
        f"**Granola meeting ID:** `{data['meeting_id']}`",
        "",
        "## Granola AI Summary",
        "",
        data["summary"].strip(),
    ]
    return "\n".join(parts) + "\n"


def cmd_write(data: dict, output: Path, no_overwrite: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    transcript_basename = output.stem
    summary_path = output.with_name(transcript_basename + "-summary.md")
    summary_basename = summary_path.stem

    if no_overwrite and (output.exists() or summary_path.exists()):
        print(json.dumps({
            "status": "skip",
            "reason": "file(s) already exist and --no-overwrite set",
            "transcript_path": str(output),
            "summary_path": str(summary_path),
            "transcript_existed": output.exists(),
            "summary_existed": summary_path.exists(),
        }))
        return

    # Transcript file
    transcript_content = transcript_frontmatter(data) + "\n" + transcript_body(data, summary_basename)
    write_atomic(output, transcript_content)

    # Summary file
    summary_content = summary_frontmatter(data, transcript_basename) + "\n" + summary_body(data, transcript_basename)
    write_atomic(summary_path, summary_content)

    print(json.dumps({
        "status": "ok",
        "action": "write",
        "transcript_path": str(output),
        "summary_path": str(summary_path),
        "transcript_bytes": len(transcript_content),
        "summary_bytes": len(summary_content),
    }))


def load_input(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser("write", help="Create transcript + summary pair from JSON payload")
    p_write.add_argument("--output", required=True, type=str, help="Path to the transcript file; summary written as <output-stem>-summary.md alongside")
    p_write.add_argument("--input", type=str, default=None, help="JSON file (default: stdin)")
    p_write.add_argument("--no-overwrite", action="store_true", help="Skip if transcript or summary file already exists (idempotent for cron use)")

    args = parser.parse_args()
    data = load_input(args.input)

    if args.cmd == "write":
        cmd_write(data, Path(args.output).expanduser(), no_overwrite=args.no_overwrite)


if __name__ == "__main__":
    main()
