#!/usr/bin/env python3
"""Migrate the 7 existing Granola-pulled files from one-file (transcript+summary+notes)
to the two-file pattern (transcript+notes file, summary file). Adds wiki-links.

Voice-purity rule: AI summary is cloud-generated, transcript is source-of-truth or
mixed-voice. They should not co-habit one file.

Files to migrate (paths relative to vault root or job-search root):
  personal/data/therapy/<date>-therapy-<who>-transcript.md  (sealed, source-of-truth)
  data/voice-corpus/granola/<date>-<slug>.md                 (mixed-voice, recruiter)
"""
import re
import sys
from pathlib import Path

VAULT = Path.home() / "Documents/Obsidian/30-projects"
JOB = VAULT / "job-search"

FILES = [
    VAULT / "personal/data/therapy/2026-04-21-therapy-provider-psychiatrist-transcript.md",
    VAULT / "personal/data/therapy/2026-04-22-therapy-provider-transcript.md",
    VAULT / "personal/data/therapy/2026-04-23-therapy-provider-transcript.md",
    VAULT / "personal/data/therapy/2026-04-29-therapy-provider-transcript.md",
    VAULT / "personal/data/therapy/2026-05-01-therapy-provider-couples-transcript.md",
    VAULT / "personal/data/therapy/2026-05-04-therapy-provider-transcript.md",
    JOB / "data/voice-corpus/granola/2026-04-28-1400-example-ventures-jane-recruiter.md",
]


def split_one(path: Path) -> None:
    if not path.exists():
        print(f"  [SKIP] {path.name}: not found")
        return
    text = path.read_text(encoding="utf-8")

    if "## Granola AI Summary" not in text:
        print(f"  [SKIP] {path.name}: no Granola AI Summary section to migrate")
        return

    # Find the boundaries
    summary_marker = "## Granola AI Summary"
    private_marker = "## Granola Private Notes"

    # Split at the summary marker
    before_summary, _, rest = text.partition(summary_marker)
    # Strip the trailing "---\n\n" that preceded the summary
    before_summary = re.sub(r"\n\n---\n\n*$", "\n", before_summary)

    # Now rest = "## Granola AI Summary..." Find optional private_notes
    if private_marker in rest:
        summary_part, _, private_part = rest.partition(private_marker)
        # Strip the trailing "---\n\n" that preceded private notes
        summary_part = re.sub(r"\n\n---\n\n*$", "\n", summary_part)
        # private_part needs the marker prepended back
        private_section = private_marker + private_part
    else:
        summary_part = rest
        private_section = ""

    summary_body = summary_marker + summary_part  # full summary section

    # Determine summary file path + sealed status from the transcript's frontmatter
    is_sealed = "sealed: true" in text
    is_therapy = "personal/data/therapy/" in str(path)

    # Build summary file
    summary_path = path.with_name(path.stem + "-summary.md")
    transcript_basename = path.stem  # for wiki-link
    summary_basename = summary_path.stem

    summary_voice = "cloud-generated"
    summary_sealed = "true" if is_sealed else "false"
    summary_session = ""
    captured = ""
    # Pull captured + session from original frontmatter
    fm_match = re.search(r"^---\n(.*?)\n---", text, flags=re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if line.startswith("captured:"):
                captured = line.split("captured:", 1)[1].strip()
            if line.startswith("session:"):
                summary_session = line.split("session:", 1)[1].strip()

    # Sealed boundary header for therapy summary
    if is_therapy:
        sealed_header = (
            "> **SEALED.** This is sensitive personal therapy material. Per "
            "`<personal-vault>/CLAUDE.md`, contents of "
            "`personal/data/therapy/` never appear in CVs, cover letters, recruiter prep, "
            "voice exports, networking notes, or any external-facing artifact.\n\n"
        )
    else:
        sealed_header = (
            "> **Voice classification:** cloud-generated synthesis (Granola AI). "
            "Per `framework/two-tier-capture.md`. The voice-pure raw transcript lives at "
            f"`[[{transcript_basename}]]`.\n\n"
        )

    summary_file_content = (
        "---\n"
        f"voice: {summary_voice}\n"
        "source: granola\n"
        f"captured: {captured}\n"
        f"session: {summary_session}\n"
        f"sealed: {summary_sealed}\n"
        f"derived_from: {transcript_basename}\n"
        "---\n\n"
        + sealed_header
        + f"> **Companion to** [[{transcript_basename}]] (voice-pure transcript). "
        + "Granola generated this summary from the transcript above. Synthesis is "
        + "Granola's, not Nick's voice. The verbatim transcript is the source-of-truth tier "
        + "per `framework/two-tier-capture.md`.\n\n"
        + summary_body.rstrip()
        + "\n"
    )

    # Write summary file
    summary_path.write_text(summary_file_content, encoding="utf-8")
    print(f"  [WRITE] {summary_path.name} ({len(summary_file_content)} bytes)")

    # Build new transcript file: original up to summary marker, then optional private_notes
    new_transcript = before_summary.rstrip() + "\n"
    if private_section:
        new_transcript = new_transcript.rstrip() + "\n\n---\n\n" + private_section.rstrip() + "\n"

    # Add wiki-link footer to transcript pointing at summary
    wiki_footer = f"\n---\n\n*Granola AI summary at [[{summary_basename}]] (separate file per voice-purity).*\n"
    new_transcript = new_transcript.rstrip() + wiki_footer

    path.write_text(new_transcript, encoding="utf-8")
    print(f"  [REWRITE] {path.name} ({len(new_transcript)} bytes)")


def main() -> None:
    for f in FILES:
        print(f"---\n{f.name}")
        split_one(f)
    print("\nDone.")


if __name__ == "__main__":
    main()
