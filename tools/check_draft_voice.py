#!/usr/bin/env python3
"""
check_draft_voice.py — PreToolUse hook for Claude Code.

Content-gate that scans tools/.pending-draft.txt for known voice anti-patterns
before open_draft.py opens the draft in Gmail compose.

Failure modes caught (drawn from memory/lessons.md Section 2 + framework/voice-reference.md):
  - Filler adverbs: "genuinely", "truly", "honestly", "structurally" (Claude tells)
  - Em dashes (—)
  - Semicolons (;)
  - "Just wanted to" / "circling back" / "just checking in"
  - Stacked "really" (>1 in single email body)
  - "load-bearing" (banned LLM-tell metaphor)
  - "if you have a sec/minute" performative-casual scaffold
  - Meta-narration labels ("Quick reframe", "To summarize", "Here's where I'm headed")
  - "not as X, but as Y" rhetorical contrast (drop the contrast; name the artifact)

Cleanly-detectable voice anti-patterns only. Semantic ones (coined hyphenated
compounds, speculative framing of the recipient's work, temporal jump-cuts,
dev-jargon lists to non-technical recipients) can't be regex'd without false
positives, so they stay behavioral in framework/voice-reference.md, which the
drafting skills load at draft time.

Triggered by .claude/settings.json PreToolUse hook matching Bash calls to open_draft.py.

Exit codes:
  0 — clean, no violations
  2 — violations found, blocks the tool call (Claude Code convention for PreToolUse)
  0 — file not found or other error (fail-open, never block on infrastructure issues)

Provenance gate: job-search emails must be drafted via /draft-email, /cold-outreach,
or /follow-up (proven by a fresh .pending-draft.source marker). Personal recipients
listed in .personal-recipients.txt are EXEMPT from this gate — personal correspondence
has no drafting skill. Voice/content checks still run for them. A draft mixing a
personal contact with a job-search recipient stays gated (must be entirely personal).

Override: set DRAFT_OVERRIDE=1 in environment to bypass (for rare legitimate cases).
Note: DRAFT_OVERRIDE only works when the hook process inherits it (e.g. exported in
the harness env), not from an inline prefix on the blocked Bash command.

Usage:
  python3 tools/check_draft_voice.py        # scans .pending-draft.txt
  DRAFT_OVERRIDE=1 python3 tools/check_draft_voice.py  # bypass
"""
import json
import os
import re
import sys
from pathlib import Path

# Shared literal-context stripping (quoted spans + heredoc bodies). Single source
# of truth — never re-implement per hook (that drift caused the command-position
# family's repeat fires). See tools/HOOK_AUTHORING.md.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# open_draft.py is always run as a script argument to a python interpreter
# (PYTHONIOENCODING=utf-8 python3 tools/open_draft.py). Match THAT shape, not a
# bare substring — so the token inside a grep pattern, commit message, or JSON
# payload string is not a false trigger. The [^|;&\n]* keeps python and the
# script on the same command segment (a separate `grep open_draft.py` won't pair
# with an earlier `python`). Run on strip_literals(command), not the raw string.
OPEN_DRAFT_INVOKE = re.compile(r"\bpython[0-9.]*\b[^|;&\n]*\bopen_draft\.py\b")


def read_hook_command() -> "str | None":
    """Read hook stdin (Claude Code PreToolUse JSON) once and return the Bash
    command, or None if stdin is unreadable (caller should fail conservative)."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    tool_input = data.get("tool_input", {}) or {}
    return tool_input.get("command", "") or ""


def is_open_draft_invocation(command: "str | None") -> bool:
    """
    Check whether this Bash call is invoking open_draft.py. The Bash matcher in
    settings.json fires for every Bash tool use; we only care about
    open_draft.py calls.

    Returns True if the command actually invokes open_draft.py (python interpreter
    + the script, after stripping quoted/heredoc literals), or if command is None
    (fail-safe: when we can't tell, run the check to be conservative). A bare
    mention of the token (grep pattern, commit message, JSON payload) is NOT an
    invocation and must not trigger the gate.
    """
    if command is None:
        return True
    return bool(OPEN_DRAFT_INVOKE.search(strip_literals(command)))


# A command writing the marker file (heredoc/printf/echo redirect) in the SAME
# Bash call that invokes open_draft.py. PreToolUse fires BEFORE the command
# executes, so this write is invisible to check_provenance() — the hook can
# only ever see the marker's state from a PRIOR, already-completed tool call.
# SKILL.md already prescribes writing .pending-draft.source via the Write tool
# (a separate, already-executed tool call) before the Bash call that runs
# open_draft.py, which sidesteps this entirely. This pattern exists only to
# give a diagnostic, actionable message when that instruction wasn't followed
# and a Bash printf/heredoc was used instead — it does NOT grant provenance;
# the file-based check below is unchanged and still authoritative.
# Origin: 2026-07-08 friction-log audit, 13 occurrences of this exact race
# (see memory/reference_pending_draft_marker_before_open_draft.md).
#
# Requires an actual write indicator (>, >>, tee) targeting the marker path,
# not just the bare substring — a plain mention (e.g. `cat
# tools/.pending-draft.source && python3 tools/open_draft.py`, debugging why
# the marker is missing) is a READ, and showing the "this command appears to
# write it" hint there would misdirect the fix (splitting into two calls does
# nothing when there's no write to move). This only affects the diagnostic
# TEXT — check_provenance()'s actual gate is unchanged and file-based either
# way. Tightened same-session (code review) after this exact false-positive
# was confirmed on a read-only command.
_INLINE_MARKER_WRITE_RE = re.compile(
    r"(?:>>?\s*\S*|\btee\s+(?:-a\s+)?\S*)\.pending-draft\.source\b"
)


def command_attempts_inline_marker_write(command: "str | None") -> bool:
    return bool(command) and bool(_INLINE_MARKER_WRITE_RE.search(command))

DRAFT_FILE = Path(__file__).parent / ".pending-draft.txt"
SOURCE_FILE = Path(__file__).parent / ".pending-draft.source"
PERSONAL_RECIPIENTS_FILE = Path(__file__).parent / ".personal-recipients.txt"
ALLOWED_SKILLS = {"draft-email", "cold-outreach", "follow-up"}
SOURCE_MAX_AGE_SECONDS = 300  # 5 min

# Patterns to block. Each tuple: (compiled_regex, human-readable description, fix suggestion)
PATTERNS = [
    (
        re.compile(r"\bgenuinely\b", re.IGNORECASE),
        '"genuinely" — Claude tell projecting sincerity',
        "drop the word, or replace with nothing",
    ),
    (
        re.compile(r"\btruly\b", re.IGNORECASE),
        '"truly" — Claude tell',
        "drop the word",
    ),
    (
        re.compile(r"\bhonestly\b", re.IGNORECASE),
        '"honestly" — Claude tell',
        "drop the word",
    ),
    (
        re.compile(r"\bstructurally\b", re.IGNORECASE),
        '"structurally" — Claude tell',
        "use plain alternative or drop",
    ),
    (
        re.compile(r"—"),
        "em-dash (—) — never in Nick's voice (0/37 in corpus)",
        "replace with comma, period, or restructure",
    ),
    (
        re.compile(r";"),
        "semicolon (;) — never in Nick's voice (0/37 in corpus)",
        "split into two sentences or use comma",
    ),
    (
        re.compile(r"\bjust wanted to\b", re.IGNORECASE),
        '"Just wanted to..." — absent from corpus, hedges intent',
        'state the reason directly: "Wanted to..." or just say what you want',
    ),
    (
        re.compile(r"\bcircl(?:ing|e)\s+back\b", re.IGNORECASE),
        '"circle back" / "circling back" — absent from corpus',
        'use "Wanted to follow up here" or give a concrete reason',
    ),
    (
        re.compile(r"\bjust checking in\b", re.IGNORECASE),
        '"just checking in" — absent from corpus, no reason given',
        "give a reason every time you reach out",
    ),
    (
        re.compile(r"\bshape\s+of\s+(?:the|this|that|your|our|my|a|an)\s+(role|seat|work|job|gig|position|opportunity)\b", re.IGNORECASE),
        '"shape of the role/seat/work" — hard-banned euphemism (memory/feedback_no_shape_of_role_phrasing)',
        'use direct noun ("the role," "the seat," "what you described"); permitted: "same shape as," "X-shaped" adjectival',
    ),
    (
        re.compile(r"\bthe\s+shape\s+(?:i|that\s+i|i\'d)\s+(like|want|prefer|enjoy)\b", re.IGNORECASE),
        '"the shape I like/want" — hard-banned euphemism',
        'name the actual attribute (team-structure, role mix, stage, etc.)',
    ),
    # --- T10 voice-anchor bundle (2026-06-01): cleanly-detectable patterns only ---
    (
        re.compile(r"\bload[\s-]?bearing\b", re.IGNORECASE),
        '"load-bearing" — banned LLM-tell metaphor (memory/feedback_no_load_bearing_vocabulary)',
        'use "the thing that matters" / "the central X" / "the X that holds"',
    ),
    (
        re.compile(r"\b(?:if|when)\s+you(?:'ve)?\s+(?:have|get|got)\s+a\s+(?:sec|second|minute|min|moment|chance)\b", re.IGNORECASE),
        '"if/when you have a sec/minute" — banned performative-casual scaffold (memory/feedback_no_if_you_have_a_sec_scaffold)',
        "state the ask directly without the softener",
    ),
    (
        re.compile(r"\b(?:quick\s+re-?frame|quick\s+note\s+on|to\s+summarize|here'?s\s+where\s+(?:i'?m|i\s+am)\s+headed)\b", re.IGNORECASE),
        'meta-narration label — announces what the sentence will do instead of doing it (voice-reference.md, 5/21)',
        "lead with the substance; cut the announcement clause",
    ),
    (
        re.compile(r"\bnot\s+as\b[^.?!\n]{1,50}?\bbut\s+as\b", re.IGNORECASE),
        '"not as X, but as Y" — performs the contrast instead of stating substance (voice-reference.md, 5/21)',
        "drop the contrast and name the built artifact (content-rules B7)",
    ),
]

# Body-only checks (count-based). Run after isolating the BODY section.
def check_stacked_really(body: str) -> list[str]:
    matches = re.findall(r"\breally\b", body, re.IGNORECASE)
    if len(matches) > 1:
        return [
            f'"really" appears {len(matches)} times in body — '
            f'corpus rule: max 1 per email (filler creep above 1)'
        ]
    return []


def extract_body(content: str) -> str:
    """
    Pull out the BODY: section. Format:
      TO: ...
      SUBJECT: ...
      ATTACH: ...
      BODY:
      <body text>
    """
    marker = "BODY:"
    idx = content.find(marker)
    if idx == -1:
        return content
    return content[idx + len(marker):].strip()


def load_personal_recipients() -> set:
    """
    Read .personal-recipients.txt — addresses exempt from the skill-provenance gate.

    Personal correspondence (family, friends) has no job-search drafting skill, so it
    never produces a provenance marker. Returns a lowercased set. Missing/unreadable
    file → empty set (fail-safe: no file means nothing is exempt, gate stays on).
    """
    if not PERSONAL_RECIPIENTS_FILE.exists():
        return set()
    try:
        lines = PERSONAL_RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return set()
    return {
        line.strip().lower()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }


def extract_to(content: str) -> str:
    """Pull the TO: line from a pending-draft file (empty string if absent)."""
    for line in content.splitlines():
        if line.startswith("TO:"):
            return line[3:].strip()
    return ""


def recipients_all_personal(to_field: str, allowlist: set) -> bool:
    """
    True only when TO has at least one address AND every address is on the allowlist.

    A draft mixing a personal contact with a job-search recipient stays gated — the
    skill-provenance requirement only relaxes when the entire send is personal.
    """
    if not allowlist:
        return False
    addrs = [a.strip().lower() for a in to_field.split(",") if a.strip()]
    return bool(addrs) and all(a in allowlist for a in addrs)


def check_provenance(command: "str | None" = None) -> list[str]:
    """
    Verify .pending-draft.source marker exists, is fresh, and names an allowed skill.

    The marker is written by /draft-email, /cold-outreach, /follow-up at draft time.
    Its presence proves an email-drafting skill ran (rather than inline drafting that
    bypasses tone-matching, voice-reference loading, and quality structure).

    `command` is used ONLY to diagnose one specific failure mode (see
    command_attempts_inline_marker_write docstring) and never grants provenance
    by itself — the file-based check below is unchanged.

    Returns a list of violations (empty list = clean).
    """
    if not SOURCE_FILE.exists():
        inline_hint = ""
        if command_attempts_inline_marker_write(command):
            inline_hint = (
                "\n  This command appears to write .pending-draft.source itself — but "
                "PreToolUse fires BEFORE the command runs, so that write is invisible "
                "here. Use the Write tool for tools/.pending-draft.source (a separate, "
                "already-executed tool call), THEN invoke open_draft.py in a SEPARATE "
                "Bash call. See [[reference_pending_draft_marker_before_open_draft]]."
            )
        return [
            "No skill provenance marker (tools/.pending-draft.source)."
            + inline_hint + "\n"
            "  Job-search emails must be drafted via /draft-email, /cold-outreach, or /follow-up.\n"
            "  These skills load voice-reference.md, prior tone with this contact, and the right structure.\n"
            "  Inline drafts bypass that.\n"
            "  Pick the right skill and re-run:\n"
            "    /draft-email     — replies, thank-yous, intro requests, expressions of interest\n"
            "    /cold-outreach   — first-contact messages to strangers\n"
            "    /follow-up       — re-engaging existing contacts"
        ]

    import time
    age = time.time() - SOURCE_FILE.stat().st_mtime
    if age > SOURCE_MAX_AGE_SECONDS:
        return [
            f"Stale provenance marker (last written {int(age)}s ago, max {SOURCE_MAX_AGE_SECONDS}s).\n"
            "  Re-invoke the drafting skill to refresh the marker."
        ]
    if age < -10:  # 10s grace for clock skew; beyond that, future mtime is tampering
        return [
            f"Provenance marker has a future timestamp ({int(-age)}s ahead).\n"
            "  Re-invoke the drafting skill to refresh the marker."
        ]

    try:
        first_line = SOURCE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except Exception:
        return ["Unreadable provenance marker. Re-invoke the drafting skill."]

    if first_line not in ALLOWED_SKILLS:
        return [
            f'Provenance marker names unknown skill "{first_line}".\n'
            f"  Allowed: {sorted(ALLOWED_SKILLS)}"
        ]

    return []


def main():
    if os.environ.get("DRAFT_OVERRIDE") == "1":
        sys.exit(0)

    # Only fire when this Bash call is actually invoking open_draft.py.
    # PreToolUse(Bash) fires for every Bash tool use; without this gate the
    # provenance check blocks unrelated commands as long as .pending-draft.txt
    # exists.
    command = read_hook_command()
    if not is_open_draft_invocation(command):
        sys.exit(0)

    if not DRAFT_FILE.exists():
        sys.exit(0)  # fail-open: no draft to check

    try:
        content = DRAFT_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        sys.exit(0)  # fail-open on read errors

    # Personal correspondence (family, friends on the allowlist) has no job-search
    # drafting skill, so it has no provenance marker. Skip the provenance gate for a
    # fully-personal recipient list — but keep the content/voice checks below, which
    # apply to any email Nick sends (em-dash, semicolon).
    is_personal = recipients_all_personal(extract_to(content), load_personal_recipients())

    # Provenance check first — wrong path is louder than wrong content.
    if not is_personal:
        provenance_violations = check_provenance(command)
        if provenance_violations:
            msg = (
                "BLOCKED: tools/.pending-draft.txt was created outside an approved drafting skill.\n\n"
                + "\n\n".join(provenance_violations)
            )
            print(msg, file=sys.stderr)
            sys.exit(2)

    body = extract_body(content)
    if not body.strip():
        sys.exit(0)

    violations = []

    # Pattern checks (run on body only — headers have legitimate uses of some terms)
    for regex, desc, fix in PATTERNS:
        for m in regex.finditer(body):
            line_no = body[:m.start()].count("\n") + 1
            snippet_start = max(0, m.start() - 25)
            snippet_end = min(len(body), m.end() + 25)
            snippet = body[snippet_start:snippet_end].replace("\n", " ").strip()
            violations.append(
                f'  • Line {line_no}: {desc}\n'
                f'    Match: "...{snippet}..."\n'
                f'    Fix: {fix}'
            )

    # Count-based checks
    for v in check_stacked_really(body):
        violations.append(f"  • {v}")

    if violations:
        msg = (
            "BLOCKED: tools/.pending-draft.txt contains voice anti-patterns.\n\n"
            + "\n\n".join(violations)
            + "\n\nFix the draft, then re-run open_draft.py.\n"
            "To bypass intentionally: DRAFT_OVERRIDE=1 python3 tools/open_draft.py\n"
            "Reference: framework/voice-reference.md, memory/lessons.md Section 2."
        )
        print(msg, file=sys.stderr)
        sys.exit(2)  # PreToolUse: exit 2 blocks the tool call

    sys.exit(0)


if __name__ == "__main__":
    main()
