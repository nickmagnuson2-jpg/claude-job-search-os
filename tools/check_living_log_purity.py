#!/usr/bin/env python3
"""
check_living_log_purity.py — PreToolUse hook.

Blocks ANY Write/Edit/MultiEdit tool call targeting the four voice-pure living logs.
These are append-only via tools/living_log_append.py (called by /wispr).
Claude-voice synthesis belongs in the care guide / _themes.md / inbox.md.

Exit 2 = block. Any other path = allow. Fail-open on internal error.

Triggered by .claude/settings.json PreToolUse hook on Write|Edit|MultiEdit.
"""
import json
import sys
from pathlib import Path

LIVING_LOGS = {
    "garden-log.md",
    "practice-log.md",
    "coffee-log.md",
    "farmers-market-log.md",
}


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    if not file_path or Path(file_path).name.strip().lower() not in LIVING_LOGS:
        return

    name = Path(file_path).name
    msg = [
        f"BLOCKED: {name} is a voice-pure living log.",
        "",
        "Claude must never Write/Edit the living logs directly.",
        "They are append-only via the sanctioned script:",
        "",
        "  PYTHONIOENCODING=utf-8 python3 tools/living_log_append.py \\",
        "      <garden|practice|coffee|farmers-market> <YYYY-MM-DD> \\",
        '      "<label>" "<verbatim text>" [source_wikilink]',
        "",
        "/wispr routes verbatim voice chunks through that script.",
        "Claude-voice synthesis belongs in the care guide, _themes.md,",
        "or data/inbox.md — never in a voice-pure living log.",
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"check_living_log_purity.py error (allowing through): {e}",
              file=sys.stderr)
