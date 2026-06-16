#!/usr/bin/env python3
"""
check_data_projects.py — PreToolUse hook on Write|Edit to data/projects/*.md.

Surfaces overclaim risk in Key Achievements sections. Per
memory/feedback_project_files_overclaim_vector.md: data/projects/*.md propagates
to CVs, cover letters, LinkedIn, and STAR stories. Solo-agency verbs (Built,
Designed, Architected, Led, Owned, Drove) on team-delivered work fails the
ex-colleague-readability bar (memory/feedback_ex_colleague_readability_bar.md).

This hook does NOT block. It surfaces a warning with the matched lines so Nick
can decide. Blocking would create false positives on genuinely-led work; warn-only
keeps the gate informational.

Exit 0 always (warn-only). Exit 2 only on infrastructure errors (which we still
treat as fail-open, so still exit 0).

Override: PROJECTS_OVERRIDE=1 to silence.
"""
import json
import os
import re
import sys
from pathlib import Path

TARGET = re.compile(r"data/projects/[^/]+\.md$")

# Solo-agency verbs at line-start (bullet items). These read as "I alone did this."
# Match "- Built X" / "* Built X" / "Built X" at start of line.
OVERCLAIM_VERBS = re.compile(
    r"^\s*(?:[-*]|\d+\.)?\s*(Built|Designed|Architected|Created|Founded|Owned|Led|Drove|Established|Pioneered|Spearheaded|Launched|Invented)\s+",
    re.IGNORECASE | re.MULTILINE,
)

# Headers where the rule applies. We only flag inside Key Achievements / Highlights / Impact.
SCOPE_HEADERS = re.compile(
    r"^(##|###)\s*(Key Achievements?|Highlights?|Impact|Achievements?|Results?|Outcomes?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_target_path() -> tuple[str, str] | None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return None
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if not file_path or not content:
        return None
    return file_path, content


def find_key_achievements_block(content: str) -> str | None:
    """Extract the Key Achievements section (until next H2/H3 or EOF)."""
    headers = list(SCOPE_HEADERS.finditer(content))
    if not headers:
        return None
    start = headers[0].end()
    # Find next section header to bound the block
    next_header = re.search(r"^(##|###)\s+\S", content[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(content)
    return content[start:end]


def main():
    if os.environ.get("PROJECTS_OVERRIDE") == "1":
        sys.exit(0)

    result = extract_target_path()
    if result is None:
        sys.exit(0)
    file_path, content = result

    norm = file_path.replace("\\", "/")
    if not TARGET.search(norm):
        sys.exit(0)

    block = find_key_achievements_block(content)
    if block is None:
        sys.exit(0)

    matches = list(OVERCLAIM_VERBS.finditer(block))
    if not matches:
        sys.exit(0)

    # Warn-only: print to stderr but exit 0 so the write proceeds.
    lines = []
    for m in matches:
        # Locate the line in original content to give accurate line number
        block_start_in_content = content.find(block)
        global_pos = block_start_in_content + m.start()
        line_no = content[:global_pos].count("\n") + 1
        line_text = block.splitlines()[block[:m.start()].count("\n")].strip()
        lines.append(f'  Line {line_no}: "{line_text[:120]}"')

    msg = (
        f"\nWARNING (write proceeds): {file_path} Key Achievements uses solo-agency verbs.\n"
        f"Per memory/feedback_project_files_overclaim_vector.md, these propagate to CVs/LinkedIn/STAR stories.\n"
        f"Apply the ex-colleague-readability bar: would peers read these and think 'yeah' vs 'what?'\n\n"
        + "\n".join(lines)
        + "\n\nIf the work was genuinely solo-led, ignore this warning.\n"
        f"If team-delivered, soften to co-led / partnered / contributed to / drove with [partner].\n"
        f"Silence: PROJECTS_OVERRIDE=1.\n"
    )
    print(msg, file=sys.stderr)
    # Warn-only — write proceeds.
    sys.exit(0)


if __name__ == "__main__":
    main()
