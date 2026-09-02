#!/usr/bin/env python3
"""
check_banned_phrase.py — PreToolUse hook for Write|Edit|MultiEdit (CONTENT hook).

WHY THIS EXISTS
---------------
`memory/feedback_no_load_bearing_vocabulary.md` bans the load/bearing compound
"anywhere in output to Nick — prep docs, chat responses, memory writes, agent
prompts, anywhere." It is an LLM-tell metaphor: Nick reads as a calibrated
audience, and so do his interviewers.

The rule was already "promoted" — and the promotion did not hold. `promoted:` on
that file reads:

    PARTIAL -- check_draft_voice.py L190 gates ONE surface
    (tools/.pending-draft.txt via open_draft.py). The rule claims seven.
    2026-08-20: Claude used the phrase 6x across 3 files it authored plus
    repeatedly in chat, with this flag reading yes.

So the regex exists; its *matcher* does not. `check_draft_voice.py` only ever
sees the outgoing-email draft file. Every other surface Nick reads — prep docs in
`output/`, framework docs, memory writes, handoffs — was ungated. This hook is
the matcher the reopen gate asked for: the same phrase, judged on the content of
any markdown/text file being written, not only on one draft path.

WHAT IT MEASURES (a property, not a presence)
---------------------------------------------
It does not check that a voice section exists or that a stamp was filled in. It
checks a fact about the bytes being written: does the load/bearing compound
appear in the new content, on a line that is not itself documenting the ban?
Measured on this repo at build time: 580 occurrences across tracked+untracked
`.md` files — i.e. the phrase does reach Nick's surfaces at volume, and no
existing gate sees any of them.

HOOK TYPE: content hook. Per tools/HOOK_AUTHORING.md, a content hook's
false-positive surface is PATH SCOPE, not command position. Two exemptions:

  1. PATH — `fixtures/` dirs and `test_*` files (a fixture must be allowed to
     carry the bad pattern on purpose, or the suite becomes unrunnable), plus the
     rule file itself and this hook's own docs.
  2. CONTENT — a write that is *about* the ban must be able to name the phrase.
     File-level: any content mentioning the rule slug or this checker is
     documentation of the ban and passes wholesale. Line-level: a line carrying a
     ban marker ("banned", "do not use", "LLM-tell", ...) passes.

The line-level hatch is deliberately trivial to satisfy in good faith and is
therefore also trivial to abuse; that is the accepted cost of not blocking the
rule's own documentation. It is a guard against accidental reflexive use, which
is the failure mode actually observed (6x in 3 files, unnoticed by the author).

Exit codes:
  0 — clean, unparseable payload, non-text target, or exempt (fail open)
  2 — banned phrase in new content; BLOCKED with the replacement table on stderr

Origin: 2026-05-25 ~9:30am pre-case. Nick, reading his own prep docs, found the
phrase ~35 times across active files. 2nd fire 2026-08-20. Origin-era exemplars
still on disk: output/acme/050526-partner-call-prep.md,
output/acme/050626-partner-soft-answers-FINAL.md.
"""
import json
import re
import sys

# The banned compound. Tolerates hyphen, space, underscore, en/em dash, or
# nothing between the halves, and any casing: "load-bearing", "Load Bearing",
# "loadbearing", "load_bearing". Word-bounded so "download bearings" is clean.
BANNED = [
    (
        re.compile(r"\bload[\s\-_–—]?bearing\b", re.IGNORECASE),
        '"load-bearing" — banned LLM-tell metaphor',
    ),
]

# A line that is documenting the ban may name the phrase.
BAN_MARKER = re.compile(
    r"\b(?:ban|bans|banned|banning|forbidden|prohibit(?:ed)?|"
    r"never\s+use|do\s+not\s+use|don'?t\s+use|avoid\s+the\s+phrase|"
    r"llm[\s-]tell|llm\s+term|replacement\s+table)\b",
    re.IGNORECASE,
)

# A file that names the rule or this checker anywhere is ban documentation.
FILE_MARKER = re.compile(
    r"feedback_no_load_bearing_vocabulary|check_banned_phrase|check_draft_voice",
    re.IGNORECASE,
)

# Surfaces Nick reads as prose. Code files are judged by other gates; a .py
# docstring naming the phrase is almost always a checker, not prose to Nick.
TEXT_SUFFIXES = (".md", ".markdown", ".mdx", ".txt")

# Path scope exemptions — fixtures and the rule's own artifacts.
# NOT a blanket `tests/` exemption: the reopen gate asks for tests/ to be covered,
# and only the fixture files inside it must carry the bad pattern on purpose.
EXEMPT_PATH = re.compile(
    r"(?:^|/)(?:fixtures?|__pycache__)/"
    r"|(?:^|/)test_[^/]*$"
    r"|feedback_no_load_bearing_vocabulary\.md$"
    r"|HOOK_AUTHORING\.md$",
    re.IGNORECASE,
)


def new_content(tool_name: str, tool_input: dict) -> str:
    """Only the text being ADDED — never the pre-existing file body.

    Edit sends old_string/new_string; blocking on old_string would make an
    existing violation impossible to edit away.
    """
    if tool_name == "Write":
        return tool_input.get("content", "") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string", "") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        return "\n".join((e or {}).get("new_string", "") or "" for e in edits)
    return ""


def violations(path: str, content: str) -> list[tuple[int, str, str]]:
    """Return (line_no, matched_text, why) for each blocking hit. Empty = clean."""
    if not path or not content:
        return []
    if not path.lower().endswith(TEXT_SUFFIXES):
        return []
    if EXEMPT_PATH.search(path):
        return []
    if FILE_MARKER.search(content):
        return []

    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if BAN_MARKER.search(line):
            continue
        for pattern, why in BANNED:
            m = pattern.search(line)
            if m:
                hits.append((i, m.group(0), why))
                break
    return hits


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name", "") or ""
    tool_input = data.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or ""
    hits = violations(str(path), new_content(tool_name, tool_input))
    if not hits:
        sys.exit(0)

    lines = "\n".join(f"  line {n}: {txt!r} — {why}" for n, txt, why in hits[:10])
    more = "" if len(hits) <= 10 else f"\n  ... and {len(hits) - 10} more\n"
    print(
        f"BLOCKED (banned phrase): {len(hits)} occurrence(s) in {path}\n"
        f"{lines}{more}\n"
        "\n"
        'Nick banned this compound outright: "I don\'t like \'load bearing\', it is a '
        "very LLM term. I don't want that anywhere in your vocabulary, and I want to "
        'say that better, like the thing that matters." (2026-05-25)\n'
        "\n"
        "Rewrite, do not reword around the hyphen:\n"
        "  claim / argument / point  -> the claim that matters\n"
        "  concern / risk            -> the central concern / risk\n"
        "  insight                   -> the key insight\n"
        "  constraint                -> the binding constraint\n"
        "  assumption / prerequisite -> the assumption that holds\n"
        "  sentence / paragraph      -> the key sentence / paragraph\n"
        '  "X is load-bearing"       -> "X matters most" / "X is central"\n'
        '  unsure                    -> "the thing that matters"\n'
        "\n"
        "Reference: memory/feedback_no_load_bearing_vocabulary.md\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
