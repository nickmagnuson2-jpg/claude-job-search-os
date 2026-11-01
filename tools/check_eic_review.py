"""PreToolUse BLOCK: no published-post record while an EIC review is open.

WHY THIS EXISTS (2026-10-30). A post sent to the editor-in-chief (EIC) for review came back
edited, and the session went straight to publishing without fetching her reply or logging
her changes. The written rule for exactly this had fired before. `tools/eic_review.py` turns
the obligation into a record; this hook is the gate that reads it: while any review is open,
nothing may write `output/linkedin-profile/voice-sources/<anything>-published.md`, the record
that a post went live. Close the review first:

    PYTHONIOENCODING=utf-8 python3 tools/eic_review.py close <capture-id> \
        --reply-id <gmail id> --lesson "<text from her EIC-tagged lessons row>"

WHY THAT PATH. Writing the published record is the one step every published post passes
through after the EIC returns it, and it happens through a tool call this hook can see.
Copying the post to the clipboard was the other candidate; it was rejected as noisier and
easier to route around (Nick, 2026-10-30, agreeing to the lean).

COVERAGE. Write/Edit/MultiEdit by `file_path`; Bash by the shared write-target extraction
(`check_handoff_producer.all_write_targets`: redirects, tee, sed -i, dd, cp/mv/rsync/install
destinations). RESIDUAL, stated: a programmatic writer (`python -c` opening the path) is not
intercepted, the same deliberate-evasion bar as the handoff gate.

Fails open on an unreadable payload, like the other hooks. Fails CLOSED on an unreadable
review record: `eic_review.reviews()` reports it as open.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_handoff_producer import all_write_targets, relativize  # noqa: E402
from hook_runtime import read_payload  # noqa: E402
import eic_review  # noqa: E402

PUBLISHED = re.compile(r"(?:^|/)output/linkedin-profile/voice-sources/[^/]*-published\.md$")

BLOCK = """BLOCKED check_eic_review: {n} EIC review(s) still open, so {rel} cannot be written.

The post went to the EIC for review. Before recording it as published:
  1. Fetch her reply:  PYTHONIOENCODING=utf-8 python3 tools/gmail_fetch.py --all-mail --search "<her address> after:<date>" --body
  2. Log each of her changes as an EIC-tagged row in memory/lessons.md Section 2.
  3. Close the review:
     PYTHONIOENCODING=utf-8 python3 tools/eic_review.py close <capture-id> --reply-id <gmail id> --lesson "<text from that row>"
     (or --no-edits "<why>" if her reply changed nothing)

Open: {ids}
"""


def targets(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name == "Bash":
        return all_write_targets(tool_input.get("command", "") or "")
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return [path] if path else []


def decide(tool_name: str, tool_input: dict) -> str | None:
    hits = [relativize(t) for t in targets(tool_name, tool_input)]
    hits = [h for h in hits if PUBLISHED.search(h)]
    if not hits:
        return None
    still_open = eic_review.open_reviews()
    if not still_open:
        return None
    return BLOCK.format(n=len(still_open), rel=hits[0],
                        ids=", ".join(r["capture_id"] for r in still_open))


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)  # fail open on an unreadable payload, like every other hook here
    message = decide(p.tool_name, p.tool_input)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
