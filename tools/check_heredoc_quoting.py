#!/usr/bin/env python3
"""
check_heredoc_quoting.py - PreToolUse hook for Bash.

Fires when a Bash command opens an UNQUOTED heredoc (<<EOF) whose body contains a
BACKTICK. An unquoted delimiter enables shell expansion for the whole document, so every
backticked token inside runs as a command and is replaced by its output, which is almost
always empty. The heredoc still writes, the script still exits 0, and the file is
silently missing the very identifiers the backticks were marking.

WHY THIS IS A BLOCK AND NOT A WARN. The failure is invisible at every downstream check.
On 2026-09-16 three memory files were written this way. The write succeeded, the script
printed "wrote ...", and the obvious integrity check -- grepping for an empty backtick
PAIR -- returned ZERO, because the substitution leaves nothing at all rather than an
empty pair. The corruption was caught only by reading the rendered lines by eye. The
shell had printed three errors of its own ("no such file or directory: slug",
"permission denied: tools/gmail_fetch.py") and they were scrolled past.

Per memory/feedback_warn_vs_block_hook_design.md: a PreToolUse WARN is exit 0 + stderr,
which Claude Code does not surface. For an unambiguous defect with one known correction,
BLOCK (exit 2) is the only tier that reaches anyone.

DETECTION, deliberately narrow so it does not fire on legitimate expansion:
  - Heredocs are found by tools/shell_tokens.py, which reads the command the way the
    shell does: only a real `<<` or `<<-` operator opens one, any delimiter word counts,
    and several on one line each get their own body.
  - A quoted or backslash-escaped delimiter DISABLES expansion. That is the safe form
    and is never flagged.
  - An unquoted delimiter is flagged ONLY IF its body contains a live (unescaped)
    backtick. Unquoted heredocs carrying $VAR are frequently intentional (that is the
    reason to leave the delimiter bare) and are NOT flagged; $ alone is far too common
    to block on.
  - <<< is a here-STRING, not a heredoc. Skipped.

REBUILT ON THE TOKENIZER 2026-09-30. The regex version found an "opener" anywhere in the
text, including inside a QUOTED heredoc's body and inside quoted strings, then scanned
the following lines as a body. It blocked three safe writes in one session, each a
quoted heredoc carrying Python test code whose string data mentioned a heredoc marker.

THE FIX IS ALWAYS THE SAME, which is what makes this blockable: quote the delimiter and
pass anything you needed interpolated as an argument instead.

Hook input: JSON via stdin from Claude Code.  {"tool_input": {"command": "..."}}

Exit codes:
  0 - clean, or no heredoc, or parse failure (fail-open: never block on a bad parse)
  2 - unquoted heredoc whose body holds a backtick; BLOCKED with the correction shown

Origin: 2026-09-16. Corrupted three memory files mid-write during a lessons-learned pass,
with $M interpolation as the reason the delimiter had been left bare.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from shell_tokens import simple_commands, tokenize  # noqa: E402
from check_public_pii import _SHELLS, shell_script_variants  # noqa: E402

BT = chr(96)


def _has_live_backtick(text: str) -> bool:
    """True if a backtick in an expanding heredoc body would run. A backslash escapes
    the next character, so parity decides: in \\\\` the backslashes escape each other
    and the backtick is live (Codex review of 68cf50a)."""
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == BT:
            return True
        i += 1
    return False


def offenders(command: str, _depth: int = 0) -> list[str]:
    """Delimiters of unquoted heredocs whose body contains a live backtick, including
    heredocs inside command strings that run (`sh -c '...'`, `eval`, `$( )`): a
    heredoc there corrupts its output the same way (Codex coverage run, 2026-10-01)."""
    bad: list[str] = []
    if _depth < 8:
        for cmd in simple_commands(command):
            for inner in cmd.subs:
                bad.extend(offenders(inner, _depth + 1))
            for i, w in enumerate(cmd.words):
                base = os.path.basename(w)
                if base in _SHELLS:
                    for text in shell_script_variants(cmd.words[i + 1:]):
                        bad.extend(offenders(text, _depth + 1))
                elif base == "eval":
                    bad.extend(offenders(" ".join(cmd.words[i + 1:]), _depth + 1))
    return bad + _direct_offenders(command)


def _direct_offenders(command: str) -> list[str]:
    toks = tokenize(command)
    delims: list[str] = []          # delimiter words, in the order their bodies arrive
    bad: list[str] = []
    for k, t in enumerate(toks):
        if (t.kind == "redir" and t.text.lstrip("0123456789") in ("<<", "<<-")
                and k + 1 < len(toks) and toks[k + 1].kind == "word"):
            delims.append(toks[k + 1].text)
        elif t.kind == "heredoc":
            delim = delims.pop(0) if delims else "EOF"
            # t.quoted: the delimiter was quoted or escaped, so the body is literal.
            if not t.quoted and _has_live_backtick(t.text):
                bad.append(delim)
    return bad


def main() -> int:
    # read_payload, NOT json.load(sys.stdin). A bare read blocks forever on a retained
    # pipe, and this hook is wired into EVERY Bash call, so a hang here stops all work --
    # strictly worse than the corruption it exists to prevent. hook_runtime bounds both
    # the deadline and the byte count. Found by cross-model review (grok F2, 2026-09-16).
    p = read_payload()
    if not p.ok:
        return 0                      # fail-open: a bad parse must never block work
    command = (p.data.get("tool_input", {}) or {}).get("command", "") or ""
    if not command:
        return 0

    bad = offenders(command)
    if not bad:
        return 0

    names = ", ".join(f"<<{d}" for d in dict.fromkeys(bad))
    print(
        f"BLOCKED: unquoted heredoc ({names}) whose body contains a backtick.\n\n"
        "An unquoted delimiter turns on shell expansion for the WHOLE document, so every\n"
        "backticked token runs as a command and is replaced by its output (usually\n"
        "nothing). The write succeeds, the script exits 0, and the file is silently missing\n"
        "those identifiers. Grepping for an empty backtick PAIR does NOT find it: the\n"
        "substitution leaves nothing at all.\n\n"
        "Fix: quote the delimiter.\n"
        f"    <<\x27{bad[0]}\x27      # expansion OFF, content written literally\n\n"
        "If you left it bare to interpolate a variable, pass it as an argument instead:\n"
        "    python3 - \"$MY_PATH\" <<\x27PY\x27\n"
        "    import sys; p = sys.argv[1]\n"
        "    PY\n\n"
        "Heredocs carrying $VAR and no backticks are NOT blocked; this fires only on the\n"
        "combination that corrupts content.\n\n"
        "Origin: 2026-09-16, three memory files corrupted mid-write this exact way.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
