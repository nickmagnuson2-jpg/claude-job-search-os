#!/usr/bin/env python3
"""check_guard_edit_approval.py — PreToolUse hook (Write|Edit|MultiEdit).

BLOCKS an edit to guard infrastructure — `tools/check_*.py`, the shared
`tools/hook_command_lint.py`, and `.claude/settings.json` — so that changing a
guard is always a deliberate, approved act rather than something that happens in
the same motion as being blocked by one.

WHY THIS EXISTS (2 fires, gate tripped 2026-08-14):
  2026-07-28  `check_email_via_skill.py` blocked an Edit to voice-reference.md.
              The allowlist gap was real; Claude added the entry, then surfaced it
              after. Nick: "keep it but never again."
  2026-08-14  `check_todo_write_kwargs.py` blocked the new `update` subcommand's
              flags. Claude edited the guard in the same turn and reported after,
              reasoning that the guard's CONTRACT had legitimately changed because
              Nick had just approved building `update`. That is a better argument
              than 7/28's "the fix is obviously correct" -- and it is still not
              approval. It is also more dangerous, because a genuinely-changed
              contract makes the edit feel like maintenance rather than
              self-unblocking.

  The mechanical tell both times: editing a `tools/check_*.py` in the same turn
  that one blocked you. Nothing else about the situation matters, which is exactly
  what makes it hook-able.

WHAT THIS HOOK CAN AND CANNOT DO -- read before trusting it:
  It CANNOT detect intent. A legitimate guard build and a self-unblocking edit are
  byte-identical at the tool-call layer. What it does is convert a silent act into
  a deliberate one: the block forces a stop, and the only way past is an override
  that the agent must consciously set, which is the moment to ask Nick instead.

  It is therefore bypassable by the actor it constrains. That is a real weakness
  and it is NOT a reason to skip it: per feedback_llm_self_policing_fails the tier
  ladder is memory < skill < hook < independent reviewer, and this moves the rule
  up one rung from a memory file that had already failed twice. An override used
  without asking is itself a rule violation (see the rule's "How to apply" step 3),
  not a loophole the design endorses.

BLOCK tier (exit 2), per feedback_warn_vs_block_hook_design: a PreToolUse exit-0
WARN is never surfaced by Claude Code, so a WARN here would warn into a void. The
memory file's originally-named WARN target was stale and is explicitly superseded.

FALSE-POSITIVE SURFACE (content hook => PATH SCOPE, per tools/HOOK_AUTHORING.md):
  - `tests/` is excluded. A hook that judges its own fixtures makes the suite
    unrunnable (origin: check_prep_doc_format.py, 2026-08-12).
  - `tests/scripts/test_check_*.py` are tests, not guards. Excluded by the same rule.
  - Docs about hooks (`tools/HOOK_AUTHORING.md`) are not guards. Not matched.
  - Creating a NEW guard is still matched, deliberately. Building a guard is normal
    work, but it is work Nick should know is happening, and the override makes it
    one keystroke rather than a debate.

Override (only after explicit approval in this session):
  GUARD_EDIT_APPROVED=1
"""
import json
import os
import re
import sys

# Guard infrastructure. Repo-relative, matched against the tail of the path so an
# absolute path from the tool payload still resolves.
GUARD_PATTERNS = (
    re.compile(r"(^|/)tools/check_[A-Za-z0-9_]+\.py$"),
    re.compile(r"(^|/)tools/hook_command_lint\.py$"),
    re.compile(r"(^|/)\.claude/settings(\.local)?\.json$"),
)

# Path scope exclusions. A content hook that judges its own fixtures cannot be tested.
EXCLUDE_PATTERNS = (
    re.compile(r"(^|/)tests/"),
    re.compile(r"(^|/)fixtures?/"),
)

OVERRIDE_ENV = "GUARD_EDIT_APPROVED"

MESSAGE = """BLOCKED: this edits guard infrastructure ({path}).

Changing a guard is the guard owner's call, not the actor's. Two prior fires
(2026-07-28, 2026-08-14) were both "the fix is correct" -- and both were correct,
and both were still the wrong sequence.

If a guard just blocked you and you are here to fix it, STOP and do this instead:
  1. Tell Nick what was blocked, why the hook fired, and whether it is a true
     positive or a genuine gap in the guard's contract.
  2. Name the proposed change.
  3. WAIT for his yes.
  4. Then re-run with {env}=1 prefixed.

A guard's contract legitimately changing (because Nick approved something the
guard does not know about yet) is a reason to ask FASTER, not a reason to skip
asking -- that shape is exactly the 2026-08-14 fire.

If this is a false positive -- you are editing something that is not a guard --
log it so the FP is visible, since PreToolUse blocks are invisible to the
auto-logger:
  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append \\
      check_guard_edit_approval.py "FP: <what got wrongly blocked>"

Rule: memory/feedback_never_modify_guard_hook_to_unblock_self.md (2 fires)
"""


def is_guarded(path: str) -> bool:
    """True when `path` is guard infrastructure and not a test/fixture."""
    if not path:
        return False
    p = path.replace("\\", "/")
    if any(x.search(p) for x in EXCLUDE_PATTERNS):
        return False
    return any(g.search(p) for g in GUARD_PATTERNS)


def main() -> None:
    # Fail open on anything malformed: a guard that crashes blocks all work.
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if os.environ.get(OVERRIDE_ENV):
        sys.exit(0)

    if data.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "") or ""

    if not is_guarded(path):
        sys.exit(0)

    print(MESSAGE.format(path=path, env=OVERRIDE_ENV), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
