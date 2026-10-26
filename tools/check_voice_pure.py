#!/usr/bin/env python3
"""
check_voice_pure.py — PreToolUse hook.

Blocks Write/Edit to data/reflections/YYYY-MM-DD*.md when content includes
prose paragraphs outside blockquotes. Dated reflections must be Nick's
verbatim words; Claude-voice synthesis belongs in _themes.md.

Exit 2 = block. Any other path = allow.

Triggered by .claude/settings.json PreToolUse hook on Write|Edit.
"""
import json
import re
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from pathlib import Path

DATED_REFLECTION = re.compile(r"data/reflections/\d{4}-\d{2}-\d{2}[^/]*\.md$")
LONG_PARA_THRESHOLD = 100
# A "## Notes" / "## Analysis" section is the tell for Claude synthesis: its
# heading and short bullets are each exempt from the long-paragraph check, so
# it is caught by name. Same pattern as the rule's detector_signature.
SYNTHESIS_HEADING = re.compile(r"(?m)^[ ]{0,3}#{2,3}[ ]+(Notes|Analysis)[ ]*$")


def is_voice_pure_target(file_path: str) -> bool:
    return bool(DATED_REFLECTION.search(file_path))


def find_violations(content: str, check_frontmatter: bool) -> list[str]:
    errors: list[str] = []

    if check_frontmatter:
        stripped = content.lstrip()
        if not stripped.startswith("---"):
            errors.append("Missing frontmatter — voice-pure files must declare 'voice: pure-voice'")
        else:
            parts = stripped.split("---", 2)
            if len(parts) < 3:
                errors.append("Malformed frontmatter (no closing ---)")
            elif "voice: pure-voice" not in parts[1]:
                errors.append("Frontmatter must contain 'voice: pure-voice' (no other voice labels permitted on dated reflections)")

    body = content
    if content.lstrip().startswith("---"):
        parts = content.lstrip().split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    for m in SYNTHESIS_HEADING.finditer(body):
        errors.append(
            f"Claude synthesis heading in voice-pure reflection ('{m.group(0).strip()}'): "
            "move meta to the header > blockquote, synthesis to _themes.md"
        )

    prev_was_blockquote = False
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue          # every branch below reads lines[0], which a blank one lacks
        if len(para) <= LONG_PARA_THRESHOLD:
            # Still track blockquote-continuation state through short paragraphs
            # (e.g. a short quoted aside between two long quoted paragraphs)
            # so a later long paragraph doesn't lose its "lazy continuation"
            # context just because a short one sat between them.
            lines = [ln for ln in para.splitlines() if ln.strip()]
            prev_was_blockquote = lines[0].lstrip().startswith(">")
            continue

        lines = [ln for ln in para.splitlines() if ln.strip()]

        first = lines[0].lstrip()
        if first.startswith(">"):
            prev_was_blockquote = True
            continue
        if first.startswith("#"):
            prev_was_blockquote = False
            continue
        if all(ln.lstrip().startswith(("- ", "* ", "1.", "2.", "3.")) for ln in lines):
            prev_was_blockquote = False
            continue
        if first.startswith("```") or first.startswith("|"):
            prev_was_blockquote = False
            continue
        if prev_was_blockquote:
            # Lazy continuation: a paragraph immediately following a
            # blockquote paragraph, with no "> " of its own, is treated as
            # part of the same quote rather than new unquoted prose. Real
            # Markdown blockquotes require "> " on every line to render
            # correctly, but dictated/appended verbatim text commonly drops
            # it on the ONE paragraph directly after the anchor — the exact
            # shape of the reported bug (memory/friction-log.md 2026-06-04):
            # an Edit append anchored on the tail of a prior "> " line, where
            # new_string's own next paragraph loses that leading marker.
            #
            # Bounded to exactly this one paragraph: prev_was_blockquote is
            # reset to False here, NOT left True. An earlier version left it
            # True indefinitely, which let a single short "> ok" line unlock
            # an UNBOUNDED run of unquoted paragraphs after it — confirmed via
            # direct execution to let arbitrary-length Claude-authored prose
            # through a gate whose entire purpose is blocking exactly that.
            # A genuine multi-paragraph verbatim quote is expected to carry
            # "> " on each of its own paragraphs (or the short-paragraph
            # branch above re-arms this for one more) rather than lean on
            # indefinite lazy continuation.
            prev_was_blockquote = False
            continue

        errors.append(
            f"Prose paragraph >{LONG_PARA_THRESHOLD} chars outside blockquote: "
            f"{para[:80].replace(chr(10), ' ')}..."
        )
        prev_was_blockquote = False

    return errors


def main():
    p = read_payload()
    if not p.ok:
        return
    # The typed fields, not p.data: they raise on an unreadable payload, which is what
    # makes the `.ok` check above observable (tools/HOOK_AUTHORING.md).
    tool_name = p.tool_name
    tool_input = p.tool_input
    file_path = p.file_path

    if not file_path or not is_voice_pure_target(file_path):
        return

    if tool_name == "Write":
        content = tool_input.get("content", "")
        errs = find_violations(content, check_frontmatter=True)
    elif tool_name == "Edit":
        errs = find_violations(p.content, check_frontmatter=False)
    elif tool_name == "MultiEdit":
        # Judge each edit on its own. Joined text would let a blockquote that ends one
        # edit lend lazy continuation to unquoted prose that opens the next, even
        # though the two land in different places in the file (605.F3/610.F3/611.F3).
        errs = [e for edit in (tool_input.get("edits") or [])
                if isinstance(edit, dict)
                for e in find_violations(edit.get("new_string") or "",
                                         check_frontmatter=False)]
    else:
        return

    if not errs:
        return

    name = Path(file_path).name
    msg = [
        f"BLOCKED: {name} is a voice-pure reflection (dated file in data/reflections/).",
        "",
        "Voice-pure rule: dated reflections contain Nick's verbatim words only.",
        "Claude paraphrase, summary, or synthesis is not permitted.",
        "",
        "Allowed paragraph shapes:",
        "  - Blockquotes ('> ...') containing Nick's verbatim text",
        "  - Headings (#), except '## Notes' / '## Analysis' (Claude synthesis sections)",
        "  - Wiki-link or bullet lists",
        "  - Short context notes (<=100 chars)",
        "",
        "Violations:",
    ]
    for e in errs:
        msg.append(f"  - {e}")
    msg += [
        "",
        "Fix options:",
        "  1. Wrap Nick's verbatim text in '> ' blockquote markers.",
        "  2. Move Claude-voice synthesis to data/reflections/_themes.md instead.",
        "  3. If Nick truly authored the prose (typed/dictated), prefix the paragraph with '> '.",
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"check_voice_pure.py error (allowing through): {e}", file=sys.stderr)
