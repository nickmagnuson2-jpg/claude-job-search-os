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
from pathlib import Path

DATED_REFLECTION = re.compile(r"data/reflections/\d{4}-\d{2}-\d{2}[^/]*\.md$")
LONG_PARA_THRESHOLD = 100


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

    for para in body.split("\n\n"):
        para = para.strip()
        if not para or len(para) <= LONG_PARA_THRESHOLD:
            continue

        lines = [ln for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue

        first = lines[0].lstrip()
        if first.startswith(">"):
            continue
        if first.startswith("#"):
            continue
        if all(ln.lstrip().startswith(("- ", "* ", "1.", "2.", "3.")) for ln in lines):
            continue
        if first.startswith("```") or first.startswith("|"):
            continue

        errors.append(
            f"Prose paragraph >{LONG_PARA_THRESHOLD} chars outside blockquote: "
            f"{para[:80].replace(chr(10), ' ')}..."
        )

    return errors


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")

    if not file_path or not is_voice_pure_target(file_path):
        return

    if tool_name == "Write":
        content = tool_input.get("content", "")
        errs = find_violations(content, check_frontmatter=True)
    elif tool_name == "Edit":
        new_string = tool_input.get("new_string", "")
        errs = find_violations(new_string, check_frontmatter=False)
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
        "  - Headings (#)",
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
