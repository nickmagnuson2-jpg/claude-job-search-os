#!/usr/bin/env python3
"""
check_email_via_skill.py — PreToolUse hook for Write|Edit.

Forces all email drafting through the /draft-email skill by blocking writes
of email-shaped content to disk outside the official skill flow.

Why this exists: prevents Claude from drafting emails inline (in chat or to
arbitrary files), which silently bypasses voice-reference matching, the
37-email corpus, and the check_draft_voice.py PreToolUse hook on
.pending-draft.txt. Memory: feedback_gold_standard_warm_thread_reengagement.

Allowlisted destinations (always allowed through):
  - tools/.pending-draft.txt           (skill staging file — voice-checked separately)
  - output/<slug>/MMDDYY-{draft-email|follow-up|cold-outreach|cover-letter}-<recipient>.md  (skill archive files)
  - data/networking.md                 (interaction logs may quote email bodies)
  - data/outreach-log.md               (audit log)
  - inbox/**/*.md                      (Gmail-fetched emails)

Detection: file content matches email-body shape — at least 2 of the
following patterns:
  - "Subject:" header line
  - "To:" header line with @
  - "Hi <Name>," opener line
  - "Best,\\nNick" or "Thanks,\\nNick" signature
  - Explicit "BODY:" marker

This narrow signature avoids false positives on docs that quote individual
emails for reference (e.g. company-notes citing a verbatim launch post).

Override: set EMAIL_VIA_SKILL_OVERRIDE=1 in environment to bypass for
intentional cases (e.g. saving a manually-drafted email for archive that
Claude didn't generate).

Hook input: JSON via stdin from Claude Code.
  {"tool_input": {"file_path": "...", "content": "..." | "new_string": "..."}}

Exit codes:
  0 — clean / non-email content / allowlisted destination
  2 — email content detected outside allowlist, blocks the tool call
  0 — JSON parse error or any other infrastructure issue (fail-open, never
       block on hook-internal problems)
"""
import json
import os
import re
import sys

ALLOWLIST_PATTERNS = [
    re.compile(r"/tools/\.pending-draft\.txt$"),
    re.compile(r"/output/[^/]+/\d{6}-(draft-email|follow-up|cold-outreach|cover-letter)-[a-z0-9-]+\.md$"),
    re.compile(r"/data/networking\.md$"),
    re.compile(r"/data/outreach-log\.md$"),
    re.compile(r"/inbox/"),
    # Voice-corpus exemplar homes: these legitimately CONTAIN verbatim email bodies as
    # reference material (not send paths). The skill itself reads voice-reference.md as its
    # 37-email corpus; curating it must not be blocked. (Added 2026-07-28, exemplar re-anchoring.)
    re.compile(r"/framework/voice-reference\.md$"),
    re.compile(r"/data/voice-corpus/"),
]

EMAIL_SHAPE_PATTERNS = [
    (re.compile(r"^Subject:\s+\S", re.MULTILINE), "Subject: header"),
    (re.compile(r"^To:\s+\S+@\S+", re.MULTILINE), "To: header with email address"),
    (re.compile(r"^Hi [A-Z][a-zA-Z'-]+,?\s*$", re.MULTILINE), "Hi <Name>, opener"),
    (re.compile(r"\n(?:Best|Thanks)[,!]?\s*\nNick\b", re.MULTILINE), "Best/Thanks + Nick signature"),
    (re.compile(r"^BODY:\s*$", re.MULTILINE), "BODY: marker"),
]

THRESHOLD = 2


def is_allowlisted(file_path: str) -> bool:
    return any(p.search(file_path) for p in ALLOWLIST_PATTERNS)


def detect_email_shape(content: str) -> list[str]:
    return [name for pattern, name in EMAIL_SHAPE_PATTERNS if pattern.search(content)]


def main() -> None:
    if os.environ.get("EMAIL_VIA_SKILL_OVERRIDE") == "1":
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content") or tool_input.get("new_string") or ""

    if not file_path or not content:
        sys.exit(0)

    if is_allowlisted(file_path):
        sys.exit(0)

    matches = detect_email_shape(content)
    if len(matches) < THRESHOLD:
        sys.exit(0)

    print(
        f"BLOCKED: email-shaped content detected in {file_path}\n"
        f"\nMatched {len(matches)}/{len(EMAIL_SHAPE_PATTERNS)} email-shape patterns:\n"
        + "\n".join(f"  • {m}" for m in matches)
        + "\n\n"
        f"All email drafting must go through the /draft-email skill, which:\n"
        f"  • reads framework/voice-reference.md (37-email corpus)\n"
        f"  • pulls 2-3 verbatim exemplars (rules + exemplars beats rules alone)\n"
        f"  • voice-checks via tools/check_draft_voice.py on the staging file\n"
        f"  • opens the draft via tools/open_draft.py\n"
        f"\n"
        f"To draft an email: invoke `/draft-email <recipient> <purpose>`.\n"
        f"\n"
        f"Allowlisted destinations (where email-shape writes are expected):\n"
        f"  • tools/.pending-draft.txt          (skill staging)\n"
        f"  • output/<slug>/MMDDYY-{{draft-email|follow-up|cold-outreach|cover-letter}}-<recipient>.md   (skill archive)\n"
        f"  • data/networking.md                (interaction logs)\n"
        f"  • data/outreach-log.md              (audit log)\n"
        f"  • inbox/**/*.md                     (Gmail-fetched emails)\n"
        f"\n"
        f"To bypass intentionally: EMAIL_VIA_SKILL_OVERRIDE=1 in environment.\n"
        f"\n"
        f"Reference: memory/feedback_gold_standard_warm_thread_reengagement.md",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
