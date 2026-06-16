#!/usr/bin/env python3
"""
check_replace_all_safety.py — PreToolUse hook for Claude Code.

Blocks an `Edit`/`MultiEdit` call with `replace_all: true` when the `old_string`
is a SHORT TOKEN that appears glued inside a longer word in the target file.
`replace_all` is character-level, not word-level, so such a call silently
corrupts the longer words.

Origin (memory/feedback_replace_all_substring_check):
  a name spelling-fix via replace_all "Ann" -> "Anne" on a file already containing
  "Anne" turned the existing "Anne" into "Annee" everywhere. The replace succeeds;
  you only catch it later via grep. Mechanical, avoidable, costs a turn.

The fire condition is deliberately precise (this hook fires on every Edit, so a
false BLOCK is costly):
  - replace_all is true, AND
  - old_string is a single short token (no whitespace, <= DANGER_MAXLEN chars), AND
  - old_string occurs in the file with a word char (\\w) immediately before or
    after the match — i.e. embedded inside a longer word.

Punctuation boundaries do NOT count as embedding: "color" inside
"background-color" is hyphen-bounded, so replace_all color->colour is allowed.
Word boundaries are the only danger signal.

NOTE: this is an Edit-INPUT hook (it reads tool_input.old_string + the target
file), NOT a Bash-command hook. The command-position / strip_literals machinery
in HOOK_AUTHORING.md is for scanning raw shell commands and does not apply here.

Exit codes:
  0 — clean / not applicable / any internal error (fail-open, never block on infra)
  2 — unsafe replace_all, blocks the tool call

Override: REPLACE_ALL_OVERRIDE=1 bypasses (for confirmed-intentional cases).
"""
import json
import os
import sys
from pathlib import Path

# Tokens longer than this are rarely embedded-by-accident and usually intentional;
# the documented danger is short tokens (origin rule: "<= 6 chars roughly").
DANGER_MAXLEN = 12


def is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


def embedded_words(text: str, old: str) -> list[str]:
    """
    Return the distinct longer words in `text` that contain `old` with a word
    char immediately adjacent (i.e. occurrences replace_all would silently alter).
    Empty list means every occurrence is standalone — safe.
    """
    found = []
    n = len(old)
    start = 0
    while True:
        i = text.find(old, start)
        if i == -1:
            break
        before = text[i - 1] if i > 0 else ""
        after = text[i + n] if i + n < len(text) else ""
        if is_word_char(before) or is_word_char(after):
            # expand to the full surrounding token for a readable message
            lo = i
            while lo > 0 and is_word_char(text[lo - 1]):
                lo -= 1
            hi = i + n
            while hi < len(text) and is_word_char(text[hi]):
                hi += 1
            word = text[lo:hi]
            if word not in found:
                found.append(word)
        start = i + 1  # allow overlapping matches
    return found


def collect_edits(data: dict):
    """Return (file_path, [(old_string, replace_all), ...]) for Edit/MultiEdit."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    edits = []
    if tool_name == "Edit":
        edits.append((tool_input.get("old_string", "") or "",
                      bool(tool_input.get("replace_all", False))))
    elif tool_name == "MultiEdit":
        for e in (tool_input.get("edits", []) or []):
            edits.append((e.get("old_string", "") or "",
                          bool(e.get("replace_all", False))))
    return file_path, edits


def qualifies(old: str) -> bool:
    """Only short single tokens are checkable; long / multi-word strings are safe."""
    if not old or len(old) > DANGER_MAXLEN:
        return False
    if any(c.isspace() for c in old):
        return False
    return True


def main():
    if os.environ.get("REPLACE_ALL_OVERRIDE") == "1":
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail-open on bad/empty stdin

    file_path, edits = collect_edits(data)
    if not file_path:
        sys.exit(0)

    targets = [old for (old, ra) in edits if ra and qualifies(old)]
    if not targets:
        sys.exit(0)

    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        sys.exit(0)  # can't read target (new file, etc.) — can't check, allow

    violations = []
    for old in targets:
        words = embedded_words(text, old)
        if words:
            shown = ", ".join(f'"{w}"' for w in words[:6])
            more = "" if len(words) <= 6 else f" (+{len(words) - 6} more)"
            violations.append(
                f'  - replace_all "{old}" would also alter: {shown}{more}'
            )

    if not violations:
        sys.exit(0)

    msg = [
        "BLOCKED: unsafe replace_all — old_string is embedded inside longer words.",
        "",
        "replace_all is character-level, so it silently changes those longer words too",
        '(the origin bug: "Ann" -> "Anne" expanded an existing "Anne" to "Annee").',
        "",
        *violations,
        "",
        "Fix one of:",
        "  - Use unique old_string-with-surrounding-context Edits (replace_all: false).",
        "  - Make old_string longer/more specific so it isn't a substring.",
        "  - If altering those longer words IS intended, bypass:",
        "      REPLACE_ALL_OVERRIDE=1 (set in env for the Edit call)",
        "",
        "Reference: memory/feedback_replace_all_substring_check.md",
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # Never block on an internal hook error.
        print(f"check_replace_all_safety.py error (allowing through): {e}",
              file=sys.stderr)
        sys.exit(0)
