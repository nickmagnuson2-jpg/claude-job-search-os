#!/usr/bin/env python3
"""
check_plan_partner_critique.py — PostToolUse hook on Write|Edit.

Surfaces a partner-critique reminder when Claude writes a plan-shaped doc
whose total estimated effort is >10 hours. Per
memory/feedback_partner_pressuretest_above_10hr: any plan costing >10 hrs of
search time gets a mckinsey-critical-advisor critique BEFORE committing.

Hooks can't directly invoke subagents. This hook is warn-only — it prints a
STRONG reminder asking Claude to spawn the agent before treating the plan as
committed.

In scope:
  - data/workbooks/_morning-starter-*.md
  - data/workbooks/foundation-plan*.md
  - data/workbooks/foundation-*-plan*.md
  - output/**/*-plan*.md
  - output/**/*-roadmap*.md
  - .planning/**/PLAN.md (if .planning/ exists)

Detection heuristic:
  - Scan body for time-estimate tokens: "(2hr)", "(3 hrs)", "2 hours", "3h",
    "(half day)", "(1 day)", "(2 days)"
  - Convert to hours: hr/h = 1, day = 8
  - Sum across the document. If sum > 10 → warn.

Override: PARTNER_CRITIQUE_OVERRIDE=1 to silence.
"""
import json
import os
import re
import sys
from pathlib import Path

IN_SCOPE_PATTERNS = [
    re.compile(r"data/workbooks/_morning-starter-.+\.md$"),
    re.compile(r"data/workbooks/foundation-.*plan.*\.md$", re.IGNORECASE),
    re.compile(r"output/.+/.*-plan.*\.md$", re.IGNORECASE),
    re.compile(r"output/.+/.*-roadmap.*\.md$", re.IGNORECASE),
    re.compile(r"\.planning/.+/PLAN\.md$"),
]

# Time-estimate regexes — capture the number and unit
TIME_PATTERNS = [
    # "(2hr)" "(2 hrs)" "(2 hours)" "2hr" "2 hours" — bare or parenthesized
    (re.compile(r"(?:\(|\b)(\d+(?:\.\d+)?)\s*(?:hr|hrs|hour|hours)\b", re.IGNORECASE), "h"),
    # "2h" — short form (be specific to avoid catching "2h2" hex strings)
    (re.compile(r"(?:\(|\s)(\d+(?:\.\d+)?)h\b", re.IGNORECASE), "h"),
    # "(1 day)" "1 day" "2 days"
    (re.compile(r"(?:\(|\b)(\d+(?:\.\d+)?)\s*(?:day|days)\b", re.IGNORECASE), "d"),
    # "(half day)" — count as 4h
    (re.compile(r"(?:\(|\b)(half)\s*(?:day|days)\b", re.IGNORECASE), "halfd"),
]

THRESHOLD_HOURS = 10


def extract_target() -> tuple[str, str] | None:
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


def in_scope(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(pat.search(norm) for pat in IN_SCOPE_PATTERNS)


def estimate_hours(content: str) -> tuple[float, list[str]]:
    """Sum hour estimates in the document. Returns (total_hours, matches_for_display)."""
    total = 0.0
    matches = []
    for regex, unit in TIME_PATTERNS:
        for m in regex.finditer(content):
            raw = m.group(1)
            if unit == "halfd":
                hours = 4.0
                display = m.group(0)
            else:
                try:
                    n = float(raw)
                except ValueError:
                    continue
                if unit == "h":
                    hours = n
                    display = f"{m.group(0)}"
                elif unit == "d":
                    hours = n * 8
                    display = f"{m.group(0)} → {hours}h"
                else:
                    continue
            total += hours
            line_no = content[:m.start()].count("\n") + 1
            matches.append(f"  Line {line_no}: {display}")
    return total, matches


def main():
    if os.environ.get("PARTNER_CRITIQUE_OVERRIDE") == "1":
        sys.exit(0)

    result = extract_target()
    if result is None:
        sys.exit(0)
    file_path, content = result

    if not in_scope(file_path):
        sys.exit(0)

    total, matches = estimate_hours(content)
    if total <= THRESHOLD_HOURS:
        sys.exit(0)

    msg = (
        f"\n⚠️  PARTNER-CRITIQUE REMINDER: {file_path}\n"
        f"   Total estimated effort: {total:.1f}h (threshold: {THRESHOLD_HOURS}h)\n"
        f"   Per memory/feedback_partner_pressuretest_above_10hr.md, plans >10h get a\n"
        f"   mckinsey-critical-advisor critique BEFORE committing. ~5 min agent invocation;\n"
        f"   saves 10x in misallocated time.\n\n"
        f"   Detected time estimates:\n"
        + "\n".join(matches[:10])
        + (f"\n   ... and {len(matches) - 10} more" if len(matches) > 10 else "")
        + "\n\n   Recommended action: spawn mckinsey-critical-advisor agent on this plan before\n"
        f"   Nick treats it as committed. The plan write proceeded — this is a closing reminder.\n"
        f"   Silence: PARTNER_CRITIQUE_OVERRIDE=1.\n"
    )
    print(msg, file=sys.stderr)
    # Warn-only: write proceeds.
    sys.exit(0)


if __name__ == "__main__":
    main()
