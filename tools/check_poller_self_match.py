#!/usr/bin/env python3
"""
check_poller_self_match.py — PreToolUse hook for Bash.

Blocks a wait loop whose process poll can match the waiting command itself, so the
loop can never end. Fired 3 times (memory: feedback_a_poller_must_not_match_itself):

  2026-09-06  `while ps aux | grep -q "[m]utation_check"` around a body that ran
              tools/mutation_check.py. ps lists the waiting shell, whose command line
              holds the body, so the poll matched the waiter.
  2026-10-25  two queued waiters, each `while pgrep -f "mut2.sh|mutation_check.py"` with
              mutation_check.py in its body. Neither ever started.

How each poll sees the command, measured 2026-10-25 on macOS:

  * `ps ... | grep PAT`: ps lists every process, the waiting shell and grep itself
    included. If PAT matches anything in the command's text, including PAT as written,
    the poll matches. `[m]utation` is the usual escape: the regex does not match its
    own spelling.
  * `pgrep -f PAT`: pgrep leaves out itself and its ancestors, so one waiter never sees
    itself. A second process carrying the same text does, and two waiters then wait on
    each other. So the test is whether PAT matches the command with PAT's own spelling
    removed: text elsewhere in the command (usually the body, which runs the thing
    being waited for) is what a second copy exposes.

Only the condition of a `while` or `until` loop is judged, since that is where a wrong
answer never ends, and only when the loop waits while the process is PRESENT (`while
pgrep`, `until ! pgrep`): a loop waiting for it to appear is ended early by a self-match,
never held. A one-off `pgrep` returns at once. Not judged: commands built at
run time (eval, variables), and loops inside a script file this command runs.

BLOCK tier (exit 2): the loop cannot end in the states measured above, and the fix is
one change, named in the message. Fails open (exit 0) on an unreadable payload or a
tokenizer error.

Threat model: honest mistakes. Not a barrier against deliberate evasion.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from shell_tokens import tokenize  # noqa: E402

_LOOP = {"while", "until"}
_SKIP = {"!", "command", "builtin", "noglob"}


def _matches(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return pattern in text


def _condition(words: list, i: int) -> list:
    """Tokens of the loop condition after words[i] (`while`/`until`), up to `do`."""
    out = []
    for t in words[i + 1:]:
        if t.kind == "word" and t.text == "do" and not t.quoted:
            break
        out.append(t)
    return out


def _simple_commands(cond: list) -> list:
    """[(words, operator before it)] for each simple command in a condition."""
    commands, current, op_before = [], [], None
    for t in cond:
        if t.kind == "op":
            if current:
                commands.append((current, op_before))
            current, op_before = [], t.text
        else:
            current.append(t)
    if current:
        commands.append((current, op_before))
    return commands


def _polls(cond: list) -> list:
    """(tool, pattern token, negated, inverted patterns) for each `pgrep -f` or
    `ps | grep` poll in a loop condition.

    `negated` is a leading `!` on the pipeline. `inverted` holds the patterns of the
    `grep -v` filters between ps and the poll, and `own` is the poll grep's own command
    as ps lists it. ps shows two lines that can self-match: the waiting shell's, which
    holds the whole command and so every `-v` pattern, and the poll grep's own. A filter
    that drops the grep's line drops both, which is why `ps aux | grep -v grep | grep -q
    PAT` cannot see itself (findings 648.F2, 650.F2, 651.F1), and `grep -v zzqq` can.
    """
    found, in_ps, negated, inverted = [], False, False, []
    for words, op_before in _simple_commands(cond):
        if op_before != "|":                      # a new pipeline starts here
            in_ps, negated, inverted = False, False, []
        k = 0
        while k < len(words) and words[k].text in _SKIP and not words[k].quoted:
            negated = negated or (words[k].text == "!" and op_before != "|")
            k += 1
        if k >= len(words):
            in_ps = False
            continue
        cmd, args = os.path.basename(words[k].text), words[k + 1:]
        operands = [a for a in args if a.quoted or not a.text.startswith("-")]
        flags = "".join(a.text[1:] for a in args
                        if not a.quoted and a.text.startswith("-") and not a.text.startswith("--"))
        if cmd == "pgrep" and "f" in flags and operands:
            found.append(("pgrep", operands[-1], negated, [], ""))
        elif cmd == "grep" and op_before == "|" and in_ps and operands:
            if "v" in flags:
                inverted.append(operands[0].text)
            else:
                own = " ".join(w.text for w in words[k:])
                found.append(("ps|grep", operands[0], negated, list(inverted), own))
        in_ps = cmd == "ps" or (in_ps and cmd == "grep" and "v" in flags)
    return found


def find_violations(command: str) -> list[str]:
    try:
        tokens = tokenize(command)
    except Exception:  # noqa: BLE001 — fail open on anything the tokenizer cannot read
        return []
    # A redirect's target (`> /dev/null`) is not an argument, so it never reads as the
    # pattern.
    words, after_redir = [], False
    for t in tokens:
        if t.kind == "redir":
            after_redir = True
        elif t.kind == "word" and after_redir:
            after_redir = False
        elif t.kind in ("word", "op"):
            words.append(t)
    found = []
    for i, t in enumerate(words):
        if t.kind != "word" or t.quoted or t.text not in _LOOP:
            continue
        for tool, tok, negated, inverted, own in _polls(_condition(words, i)):
            # Only a loop that keeps waiting while the process is PRESENT can hang on a
            # self-match. `until pgrep` waits for it to appear, and a self-match ends
            # that loop early rather than never (finding 647.F2).
            if (t.text == "while") == negated:
                continue
            if any(_matches(x, own) for x in inverted):
                continue
            pat = tok.text
            # pgrep never sees its own ancestors, so the pattern's own spelling is blanked
            # (by position, so the same text elsewhere in the command still counts).
            text = (command if tool == "ps|grep"
                    else command[:tok.start] + " " * (tok.end - tok.start) + command[tok.end:])
            if not _matches(pat, text):
                continue
            why = ("ps also lists this waiting shell, so the poll matches the waiter and "
                   "the loop cannot end"
                   if tool == "ps|grep" else
                   "pgrep skips this shell, so one copy ends. A second command carrying the "
                   "same text (another queued waiter, the 2026-10-25 case) matches it, and "
                   "the two then wait on each other")
            found.append(
                f"The `{t.text}` loop polls `{tool}` for {pat!r}, and that pattern matches "
                f"this command's own text. {why}. Fix: put the "
                f"loop in a script file and run that, so the body is not on the waiting "
                f"command line, or poll for text that appears nowhere else in this "
                f"command. Bracketing one letter ('[m]ytool') hides only the pattern's "
                f"own spelling, not the same text in the loop body."
                + (" For mutation_check, drop the loop: it queues on its own run lock, "
                   "waiting up to `--wait` seconds (default 600) for a running copy, so "
                   "run it directly." if "mutation_check" in command else ""))
    return found


def main() -> int:
    p = read_payload()
    if not p.ok:
        return 0          # fail open: an unreadable payload is not evidence of a bad loop
    problems = find_violations(p.command or "")
    if not problems:
        return 0
    print("BLOCKED: this wait loop can never end (check_poller_self_match.py).", file=sys.stderr)
    for m in problems:
        print(f"  - {m}", file=sys.stderr)
    print("\nIf this block is wrong, log it:\n  PYTHONIOENCODING=utf-8 python3 "
          'tools/friction_log.py append check_poller_self_match.py "FP: <what>"',
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
