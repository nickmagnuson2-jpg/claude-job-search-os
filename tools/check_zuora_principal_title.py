#!/usr/bin/env python3
"""
check_zuora_principal_title.py — PreToolUse hook (Write|Edit|MultiEdit|Bash).

Blocks the stale rendering of Nick's Zuora principal's title from landing in a
new artifact. The person he was Chief of Staff to is the **Chief
Product and Technology Officer (CPTO)**. "Head of Product and Technology",
"Head of Product & Technology", "Head of Product and Tech", and "Chief of Staff
to the Head of Product" are shorthand Nick uses in casual speech — they are NOT
the title, and they read as a demotion of his own reporting line on a CV, a
cover letter, or in an interview rep.

WHY A HOOK, AND WHAT IT MEASURED
--------------------------------
This is the 3rd-fire promotion named in the rule's own reopen_gate:
"3rd fire -> add 'Head of Product' to a denylist-style pattern or a Stop-hook
string check, since the failure is a literal string that a grep can catch and
prose has now failed twice."

  Fire 1 (2026-06-11, the Origin): the recruiter-channel CV
  `output/<recruiter-slug>/061126-magnuson.md` was generated off a prior-application
  baseline, which rendered "Head of Product and Technology" in 4 places
  (summary, position line, bullet 1, the AI-workflow bullet). It survived a
  full deep review. Nick caught it: "it should be Chief Product and Technology
  officer."

  Fire 2 (2026-08-24): the stale variant was sitting in MEMORY.md Critical
  Context — the always-loaded file — so ~35 min before a live interview loop
  Claude told Nick his own CORRECT answer was wrong, twice, and he had to
  correct Claude from the sidewalk.

Prose failed both times. The failure is a literal string, so a string check is
the enforcement tier that actually converts (CLAUDE.md: "a check a gate reads").

Measured scope before building (2026-08-25, `/usr/bin/grep -rin` over the repo
including the gitignored trees): the Zuora-linked variant appears in 20+ frozen
historical artifacts under `output/` and `inbox/`. Those are past outputs and
sent mail — a hook only sees NEW writes, so they are untouched. Bare "Head of
Product" for OTHER people (another contact's actual title in networking.md, the
generic "Head of Product" persona in data/principles.md) appears far more often
than the Zuora one, which is exactly why this check never fires on bare
"Head of Product" alone.

PROPERTY, NOT PRESENCE
----------------------
The check does not assert that a title field exists or is non-empty. It asserts
a fact about the bytes about to be written: a specific forbidden phrase, in a
construction that can only refer to Nick's Zuora principal, is present. Writing
the section and filling it wrong is precisely what this catches.

DETECTION
---------
Case-insensitive, on the content being written:

  1. head of product {and|&|/} tech[nology]      — the full stale title
  2. chief of staff to [the] [Zuora's] head of product  — the reporting-line form
  3. Zuora's head of product                     — possessive form

Bare "Head of Product" with no Zuora linkage is ALLOWED — other companies'
heads of product are real people with that real title (rule: "Different
companies' 'head of product' (other target companies) correctly untouched").

TWO NARROWED EXCEPTIONS (both from the rule's own dated supplements)
--------------------------------------------------------------------
  (a) Self-correcting line. If the SAME LINE also names the canonical title
      ("Chief Product and Technology Officer" or "CPTO"), the line is discussing
      the error, not asserting it. This is how `framework/application-workflow.md`
      line 95 documents the rule ("inherited 'Head of Product and Technology'
      (wrong; CPTO)") and how the memory rule file itself is written. Without
      this, the hook would block every attempt to write the rule down.

  (b) Frozen-record and meta paths. `memory/**` (the rule, the index shards, the
      archives), `inbox/**` and `data/networking.md` (text of mail Nick ACTUALLY
      SENT — rewriting history is a different defect), `data/job-todos.md`
      (completed tasks), `coaching/pressure-points.md` (line 18 holds the string
      inside a quoted INTERVIEWER OBJECTION — a skeptic's words in a challenge
      script, deliberately left per the 2026-08-24 supplement), and `tests/**`
      (fixtures carry the bad string on purpose; a content hook that judges its
      own fixtures makes the suite unrunnable — HOOK_AUTHORING.md).

BASH BRANCH
-----------
A heredoc or `>` redirect is the most common way a file gets written, and it
bypasses Write/Edit entirely (this is the 2026-08-19 lesson from
check_public_pii.py). Bash commands are split into segments and only a segment
that actually WRITES somewhere is judged — write targets are mined with
check_public_pii.py's `extract_write_targets` (redirects, tee, sed -i, dd of=),
the single source of truth for that parse. A segment with no write target is
never judged, so `grep -rn "head of product and technology" .` — the audit sweep
you run to verify this very rule — is clean.

Exit codes:
  0 — clean, exempt path, self-correcting line, or any parse failure (fail-open)
  2 — stale title detected in a new write; BLOCKED with the canonical title shown

Origin: memory/feedback_zuora_principal_title_is_cpto.md (occurrences: 2,
reopen_gate 3rd fire). Connections: [[feedback_claimed_fix_is_not_a_closed_defect]],
[[verification-umbrella]].
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from check_public_pii import extract_write_targets, split_command_segments
except Exception:  # pragma: no cover - sibling missing: degrade, never crash
    extract_write_targets = None
    split_command_segments = None

CANONICAL = "Chief Product and Technology Officer"

# The three constructions that can only mean Nick's Zuora principal.
STALE_TITLE = re.compile(
    r"head\s+of\s+product\s*(?:and|&|/|\+)\s*tech(?:nology)?\b"      # (1)
    r"|chief\s+of\s+staff\s+to\s+(?:the\s+)?(?:zuora'?s\s+)?head\s+of\s+product"  # (2)
    r"|zuora'?s\s+head\s+of\s+product",                               # (3)
    re.IGNORECASE,
)

# A line that also names the canonical title is documenting the rule, not asserting
# the stale title. Keeps the rule itself, and every doc that quotes the error, writable.
SELF_CORRECTING = re.compile(
    r"chief\s+product\s+and\s+technology\s+officer|\bCPTO\b", re.IGNORECASE
)

# Frozen records + meta surfaces. Matched on path SEGMENTS (works for absolute and
# repo-relative paths alike) so a test fixture in a tmpdir resolves the same way.
EXEMPT_DIRS = ("memory/", "inbox/", "tests/", ".git/")
EXEMPT_FILES = (
    "data/networking.md",          # text of messages Nick actually sent
    "data/job-todos.md",           # completed tasks
    "data/inbox.md",               # raw capture log
    "coaching/pressure-points.md", # quoted interviewer objection, line 18
    "tools/check_zuora_principal_title.py",
)


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/")


def is_exempt(path: str) -> bool:
    """True when this file is a frozen record, a meta surface, or a fixture."""
    p = _norm(path)
    if not p:
        return False
    for d in EXEMPT_DIRS:
        if p.startswith(d) or ("/" + d) in p:
            return True
    for f in EXEMPT_FILES:
        if p == f or p.endswith("/" + f):
            return True
    if os.path.basename(p).startswith("test_"):
        return True
    return False


def offending_lines(content: str) -> list[str]:
    """Lines that assert the stale title (self-correcting lines excluded)."""
    hits = []
    for line in (content or "").splitlines():
        if STALE_TITLE.search(line) and not SELF_CORRECTING.search(line):
            hits.append(line.strip())
    return hits


def judge(content: str, path: str) -> None:
    """Exit 2 if this content, bound for this path, carries the stale title."""
    if is_exempt(path):
        return
    hits = offending_lines(content)
    if not hits:
        return

    shown = "\n".join(f"    {h[:160]}" for h in hits[:4])
    print(
        f"BLOCKED (check_zuora_principal_title.py): stale Zuora title in a write to "
        f"{path or '<unknown path>'}.\n"
        "\n"
        f"{shown}\n"
        "\n"
        f'Nick was "Chief of Staff to the {CANONICAL}" at Zuora.\n'
        '"Head of Product and Technology" / "Head of Product & Technology" /\n'
        '"Chief of Staff to the Head of Product" are casual shorthand, not the\n'
        "title, and they understate his reporting line on anything he sends.\n"
        "\n"
        f"Fix: render it as `Chief of Staff to the {CANONICAL}`.\n"
        "If you are building from a prior CV baseline, re-verify EVERY title\n"
        "against data/projects/zuora.md — baselines propagate label errors.\n"
        "\n"
        "Reference: memory/feedback_zuora_principal_title_is_cpto.md (2 fires:\n"
        "2026-06-11 recruiter-channel CV, 2026-08-24 live interview coaching).\n",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if tool_name == "Write":
        judge(tool_input.get("content", ""), tool_input.get("file_path", ""))
    elif tool_name == "Edit":
        judge(tool_input.get("new_string", ""), tool_input.get("file_path", ""))
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        content = "\n".join(
            e.get("new_string", "") for e in edits if isinstance(e, dict)
        )
        judge(content, tool_input.get("file_path", ""))
    elif tool_name == "NotebookEdit":
        judge(tool_input.get("new_source", ""), tool_input.get("notebook_path", ""))
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        if not command or split_command_segments is None:
            return
        for segment in split_command_segments(command):
            targets = extract_write_targets(segment)
            for target in targets:
                judge(segment, target)
    return


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # pragma: no cover - fail open, never break a session
        print(
            f"check_zuora_principal_title.py error (allowing through): {e}",
            file=sys.stderr,
        )
