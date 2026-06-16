#!/usr/bin/env python3
"""
check_edit_safety.py — PostToolUse hook for Claude Code.

Warns when the Edit tool is used on a markdown file that contains rows >500 chars.
Edit silently fails on such files; Write (atomic) should be used instead.

Triggered by .claude/settings.json PostToolUse hook on every Edit call.
Never exits non-zero — never blocks workflow.
"""
import json
import sys
from pathlib import Path

# Files that must always use Write, never Edit
WRITE_ONLY_FILES = {
    "job-todos.md",
    "job-pipeline.md",
}

LONG_LINE_THRESHOLD = 500


def main():
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path.endswith(".md"):
            return

        path = Path(file_path)
        filename = path.name

        # Hard-stop warning for known write-only files
        if filename in WRITE_ONLY_FILES:
            print(
                f"⚠️  Edit used on {filename} — this file must use Write (not Edit). "
                f"Long table rows cause silent Edit failures. Re-read then Write the full file."
            )
            return

        # Soft warning for any .md file with long rows
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8", errors="ignore")
        long_lines = [
            i + 1
            for i, line in enumerate(content.splitlines())
            if len(line) > LONG_LINE_THRESHOLD
        ]

        if long_lines:
            sample = long_lines[:3]
            suffix = "..." if len(long_lines) > 3 else ""
            print(
                f"⚠️  {filename} has {len(long_lines)} rows >{LONG_LINE_THRESHOLD} chars "
                f"(lines {sample}{suffix}). Verify the Edit was applied — "
                f"if not, use Write instead."
            )

    except Exception:
        pass  # Never block workflow


if __name__ == "__main__":
    main()
