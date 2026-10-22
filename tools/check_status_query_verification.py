#!/usr/bin/env python3
"""check_status_query_verification.py -- UserPromptSubmit hook, absence-assertion reminder.

WHY THIS EXISTS
---------------
Claude asserts that a file is untouched, a workstream outstanding, or a task not started,
without running a check in the current session. That is already a CLAUDE.md Hard Rule, and
the Hard Rule does not hold: it fired again on 2026-07-31, in a fresh context, *while Claude
was authoring a framework document about verification discipline*. Salience is not
enforcement.

THE KEY INSIGHT -- WHY THIS TRIGGER
-----------------------------------
You cannot hook the assertion itself. Hooks fire on tool calls and lifecycle events; there is
no gate on assistant text mid-generation. Nothing can stop Claude from saying "that file is
untouched."

But the failures are not uniformly distributed. Both recorded instances happened while
answering the same shape of question -- a status / gap / readiness query ("is there anything
else I'm missing before I clear?"). That is a narrow, high-precision trigger, and it arrives
as USER INPUT, which is hookable. So this does not try to catch the claim; it injects the
reminder at the moment the risky question arrives, before Claude answers.

WHY EXIT 0 IS CORRECT HERE, AND ONLY HERE
-----------------------------------------
On `UserPromptSubmit`, stdout context injection IS the delivery mechanism -- the hook's output
reaches the model by design, so exit 0 delivers. Do NOT generalise this to a PreToolUse hook,
where exit 0 + stderr is not surfaced by Claude Code at all and a WARN reaches nobody. See
memory/feedback_warn_vs_block_hook_design.md (whose pre-2026-05-28 "default to WARN" form is
superseded) and tools/HOOK_AUTHORING.md L77.

PRECISION OVER RECALL
---------------------
A miss costs nothing -- the Hard Rule still applies and the reminder is redundant when Claude
was going to check anyway. A false positive on every message is worse than nothing: it trains
the reader to ignore the block, at which point the hook is decoration that still costs a
process spawn. Hence the deliberate narrowness below, and hence the clean-case half of the
test suite being the load-bearing half.

PRIOR ART, CHECKED 2026-09-07 (spec section 6 required this before writing anything):
tools/check_no_confabulation.py is a PreToolUse Write|Edit hook about placeholder tokens in
shipped prep artifacts. Different event, different surface, different failure. No overlap;
extending it would have been the wrong shape.

Spec: tools/HOOK_SPEC_status_query_verification.md
BOUNDED, NOT "NEVER BLOCKS". An earlier version of this docstring claimed the hook
never blocks. That was false and an external review reproduced it: `sys.stdin.read()`
waits for EOF, so a producer that writes and retains the pipe stalled the hook until
the host's 30-second timeout. The honest and now-provable contract is BOUNDED: this
hook reads stdin for at most `hook_runtime.STDIN_DEADLINE_S` seconds (and at most
`MAX_STDIN_BYTES`), then proceeds regardless. The bounded reader was written here first
and moved to hook_runtime, which every other hook uses; this hook adopted it 2026-10-21.
Do not restore the absolute claim; assert the deadline instead, which
`test_a_retained_open_stdin_does_not_stall_past_the_deadline` does.

Exit codes:
    0  always, and within the deadline -- including malformed input, a retained-open
       stdin, and a stdout whose consumer has gone away
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings import as `tools.X` too
from hook_runtime import read_payload

# Curly apostrophes reach here constantly: macOS smart quotes, and Wispr dictation, which is
# how a large share of this user's prompts are written. Matching only U+0027 would make the
# hook silently miss its own primary input channel.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "´": "'"})

# Each pattern is anchored to a QUESTION-SHAPED or IMPERATIVE-SHAPED construction rather than
# a bare keyword. "missing" alone matches "the file is missing a header"; "ready" alone
# matches "rename the ready flag". The surrounding words are what separate a status query
# from ordinary work, so they are part of the pattern, not stripped from it.
# A trailing "not followed by a word char or hyphen" guard. `\b` alone succeeds
# between "left" and "-", so `\bleft\b` matched "left-padding" (codex F3).
_NOT_WORD = r"(?![\w-])"

# "the thing this phrase is doing" is decided by what FOLLOWS it. A status query
# ends, punctuates, or takes a preposition; the same words used as a noun phrase
# or an implementation question are followed by more sentence. This lookahead is
# what separates "status update on the memory work" from "the status update
# field a timestamp", and "where are we" from "where are we adding the config".
_TERMINAL = r"(?=\s*(?:$|[?.,;!]|on\b|for\b|with\b|please\b))"

_PATTERNS = [
    r"anything\s+(?:else\s+)?(?:i'?m\s+|im\s+|we'?re\s+|were\s+)?missing",
    r"what'?s?\s+(?:is\s+)?(?:still\s+)?(?:left|outstanding|open|remaining)" + _NOT_WORD,
    r"is\s+(?:there\s+)?anything\s+(?:else\s+)?(?:i\s+|we\s+)?(?:need|should)\b",
    r"\b(?:are\s+we|am\s+i|is\s+it)\s+ready\b",
    # NARROWED 2026-09-07 (codex F3). This originally carried ship|commit|push,
    # which the spec did NOT list -- they were added during the build and are the
    # direct cause of "Before I commit, add the missing tests" firing. Those three
    # verbs name an ACTION being performed; clear/go/leave/wrap/start name the end
    # of a working session, which is the status-review moment this hook is for.
    r"\bbefore\s+(?:i|we)\s+(?:clear|go|start|leave|wrap)\b",
    r"\bis\s+(?:the\s+|that\s+|this\s+|it\s+)?[\w\s/.\-]{0,40}?\b(?:done|finished|complete|ready)\b\s*\??$",
    r"what\s+(?:do|should)\s+(?:i|we)\s+(?:still\s+)?(?:need\s+to\s+|have\s+to\s+)?do\b",
    r"\bstatus\s+(?:check|update)" + _NOT_WORD + _TERMINAL,
    # NARROWED THEN RE-WIDENED, 2026-09-07. The first fix demanded a terminal
    # (end/punctuation/preposition) after "where are we", which killed codex's
    # "Where are we adding the new function?" and ALSO killed "Where are we at
    # here?" -- a real status query, asked by the user in the same session. The
    # distinguishing feature is not what terminates the phrase but whether a
    # GERUND follows: "where are we adding/putting/writing X" is an
    # implementation question, "where are we at/on X" is a status question.
    r"\bwhere\s+are\s+we" + _NOT_WORD + r"(?!\s+\w+ing\b)",
    r"\bwhere\s+do\s+we\s+stand\b",
    r"\bdid\s+(?:i|we)\s+(?:miss|forget)\b",
]

_TRIGGER = re.compile("|".join(_PATTERNS), re.IGNORECASE)

# A status query is a short question. A long prose prompt that happens to contain one of these
# constructions is a work request with a coincidence in it, and firing on it is the noise this
# hook exists to avoid.
_MAX_PROMPT_CHARS = 400

REMINDER = """\
STATUS-QUERY DETECTED -- absence-assertion risk is highest here.

Before asserting that any file, workstream, or task is untouched, outstanding,
not started, or missing:
  - Run `ls -la <file>` or `git log -1 --format=%ci <file>` THIS SESSION.
    Memory and prior-session knowledge are not sufficient; they decay.
  - Name the scope you checked, in the same sentence as the conclusion, AND
    why that scope would have contained the thing if it existed.
  - Recency claims decay faster than existence claims. A file that existed
    yesterday probably still exists; a file untouched yesterday may have been
    rewritten an hour ago.
  - `grep` here is ripgrep-backed and skips gitignored trees. Name `data/` and
    `output/` explicitly or the search is blind.
  - Then ask whether the work happened in a form you did not expect, before
    calling it unstarted.
"""


def is_status_query(prompt) -> bool:
    """True when the prompt is the narrow question-shape that precedes the failure."""
    if not isinstance(prompt, str):
        return False
    text = prompt.translate(_APOSTROPHES).strip()
    if not text or len(text) > _MAX_PROMPT_CHARS:
        return False
    return bool(_TRIGGER.search(text))


def extract_prompt(data) -> str:
    """Pull the user's text out of the hook payload.

    Accepts several plausible key names rather than one. The UserPromptSubmit payload shape is
    the single thing in this build that could not be verified offline, and a hook that reads
    the wrong key is indistinguishable from a hook that never matches -- the exact
    false-negative shape this repo has shipped before. Trying the alternatives costs nothing
    and removes that failure mode.
    """
    if not isinstance(data, dict):
        return ""
    for key in ("prompt", "user_prompt", "userPrompt", "message", "text"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _emit(text: str) -> None:
    """Write the reminder, surviving a consumer that has gone away.

    WHY THE FLUSH IS EXPLICIT (codex F2, 2026-09-07). `sys.stdout.write` is buffered,
    so a closed downstream pipe often does not raise at the call site -- it raises
    during interpreter shutdown, OUTSIDE every try block in this module, producing
    exit 120 and a BrokenPipeError on stderr. That falsified two contracts at once:
    "exit 0 on every path", and "stderr is not this hook's channel", which the gate
    auditor relies on.

    Flushing inside the guarded region moves the failure somewhere catchable, and
    redirecting the fd to devnull afterwards stops the shutdown flush from finding a
    broken pipe at all.
    """
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except Exception:
            pass


def main() -> int:
    p = read_payload()
    if not p.ok:
        return 0

    if is_status_query(extract_prompt(p.data)):
        # stdout, not stderr: on UserPromptSubmit stdout is the injection channel.
        _emit(REMINDER)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # Fail open, always. A reminder hook must never be able to break a session.
        sys.exit(0)
