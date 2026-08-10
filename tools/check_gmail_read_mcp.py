#!/usr/bin/env python3
"""
check_gmail_read_mcp.py — PreToolUse hook for Claude Code.

Blocks the Gmail *read* MCP tools and points at the script read-path instead:
  tools/gmail_fetch.py --search "<gmail query>" [--all-mail] [--body]

Why (tier escalation):
  Preferring gmail_fetch.py --search over the Gmail MCP connector for READS is a
  documented rule (memory/feedback_prefer_gmail_fetch_script_over_mcp.md). It has
  fired 4+ times at the behavioral tier — most recently 2026-07-24 with no
  external forcing function (Claude just reached for mcp__claude_ai_Gmail__
  search_threads; Nick: "I told you only to use the Python script"). Behavioral
  self-policing failed repeatedly, so the rule moves to the enforcement tier.

Why BLOCK (exit 2), not WARN (exit 0):
  Per tools/HOOK_AUTHORING.md, a PreToolUse exit-0+stderr "warn" is NOT surfaced
  by Claude Code — it is invisible and would be a no-op. (The parked design in
  the memory file said "WARN exit 0"; that was corrected 2026-07-24 after this
  was confirmed building check_draft_pleasantry.py.) The block is SCOPED to the
  three read tools only; every Gmail write/label/draft tool passes through
  untouched, because the script genuinely cannot do those.

The script advantage: reuses the existing OAuth token (no interactive reauth),
full Gmail search syntax, read-only. The MCP read tools can silently go stale
mid-session and cost a turn discovering it.

Exit codes:
  0 — not a gated tool / fail-open on bad input
  2 — a Gmail read MCP tool was invoked (blocks; points at the script)

Override (rare — a read the script genuinely can't express):
  set GMAIL_MCP_OVERRIDE=1 in the hook process env.
"""
import json
import os
import sys

# Gmail MCP tools that READ content — the script (gmail_fetch.py --search)
# fully replaces these. Full connector-qualified names.
GATED_READ_TOOLS = {
    "mcp__claude_ai_Gmail__search_threads",
    "mcp__claude_ai_Gmail__get_message",
    "mcp__claude_ai_Gmail__get_thread",
}


def read_tool_name() -> "str | None":
    """Read the PreToolUse JSON from stdin and return tool_name, or None if
    stdin is unreadable (caller fails open)."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    return data.get("tool_name")


def should_block(tool_name: "str | None") -> bool:
    """Pure decision: block only the gated Gmail READ tools."""
    return tool_name in GATED_READ_TOOLS


def main():
    if os.environ.get("GMAIL_MCP_OVERRIDE") == "1":
        sys.exit(0)

    tool_name = read_tool_name()
    if not should_block(tool_name):
        sys.exit(0)  # write/label/draft tools + unknowns + bad input: allow

    short = tool_name.split("__")[-1]
    msg = (
        f"BLOCKED: {short} is a Gmail READ tool. Use the script read-path instead:\n\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --search \"<gmail query>\" --all-mail --body\n\n"
        "  The script reuses the OAuth token (no interactive reauth), supports full\n"
        "  Gmail search syntax, and is read-only. The Gmail MCP read tools can silently\n"
        "  go stale mid-session.\n\n"
        "  Reserve the Gmail MCP for actions the script CAN'T do (drafting/sending,\n"
        "  labels). Rule: memory/feedback_prefer_gmail_fetch_script_over_mcp.md (4th fire).\n"
        "  Override (rare, a read the script can't express): GMAIL_MCP_OVERRIDE=1."
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
