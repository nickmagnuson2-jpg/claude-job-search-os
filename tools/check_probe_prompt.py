#!/usr/bin/env python3
"""
check_probe_prompt.py — structural checker for voice-sim probe prompt FILES.

WHY THIS EXISTS
    An eight-property pre-handover gate for persona/sim prompts shipped 2026-08-20 as
    Step 7 of `.claude/skills/voice-export/SKILL.md`. That surface is COLD: the last
    /voice-export-shaped artifact is dated 2026-08-04, and every probe prompt written
    since was hand-authored straight into a probes directory. Correct content, cold
    surface. This is the same gate on the path actually in use — a file you can run
    before a rep.

    Origin rule: memory/feedback_persona_sim_prompts_need_structural_role_binding.md
    (4 fires). Each fire was a structural defect that read fine as prose.

WHAT IT CHECKS — SEVEN OF EIGHT PROPERTIES
    1 FIRST_WORDS   the exact opening utterance, literal, near the top, delivery
                    constrained ("and nothing else"), and the file does NOT end on an
                    ambiguous who-speaks line ("Say Start to begin").
    2 ROLE_BOUND    the role is bound to the speaker BY NAME, not to "a candidate".
    3 PROHIBITION   forbidden moves enumerated AND paired with a redirect the partner
                    can apply before speaking ("do not correct; ask the follow-up").
    4 SIZE          under ~6KB per paste. Necessary, not sufficient.
    5 STANDALONE    no back-reference to another paste; role binding, prohibition and
                    first-words are all re-stated in THIS file.
    6 TASK_SHAPE    exactly one probe, unless the file explicitly declares CASE MODE.
    7 HANDSHAKE     partner replies `Ready.`, says nothing else, delivers only after `go`.

WHAT IT CANNOT CHECK — AND THIS IS THE IMPORTANT PART
    Property 8, POSITION: the durable half belongs in the host's persistent instructions
    field, the variable half as message one of a fresh chat. That is a fact about where
    the text was PASTED, not about the text, so no file checker can see it. It is also
    the property that broke FOUR CONSECUTIVE REPS while every content property above was
    satisfied. A clean run here does not mean the rep is safe; it means seven of eight.
    The output says so, every time, on pass and on fail.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/check_probe_prompt.py <file> [<file> ...]
        [--speaker NAME]     operator's name for the role binding (default: Nick)
        [--max-bytes N]      size ceiling (default: 6144)
        [--json]             machine-readable report

EXIT CODES
    0  every file passed all seven mechanical properties
    1  at least one file failed at least one property
    2  usage error (no files, unreadable file)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_SPEAKER = "Nick"
DEFAULT_MAX_BYTES = 6144

# The probe marker in the hand-authored dialect: a line whose first word is PROBE (or
# "Opening probe"), carrying a colon. "SINGLE PROBE." cannot match — the prefix class
# holds no letters, so the line must START with the word probe.
PROBE_MARKER = re.compile(r"(?im)^[\s>*_#\-]*(?:opening\s+)?probe\b[^\n]*:")
CASE_MODE = re.compile(r"(?i)case\s+mode")
SINGLE_PROBE_DECL = re.compile(r"(?im)^[\s>*_#\-]*single\s+probe\b")
HANDSHAKE_HEAD = re.compile(r"(?im)^[\s>*_#\-]*handshake\b")
READY_TOKEN = re.compile(r"(?i)\bone word\b[^\n]{0,20}\bReady\.")
GO_GATE = re.compile(r"(?i)says?\s+[\"“']go[\"”']")
TERMINATOR = re.compile(r"(?i)end of simulation")
VERBATIM_CONSTRAINT = re.compile(r"(?i)verbatim[^\n]{0,40}nothing else")
AMBIGUOUS_TAIL = re.compile(
    r"(?i)\b(say\s+[\"“']?start[\"”']?\s+to\s+begin"
    r"|begin\s+when\s+(you\s+are|you're)\s+ready"
    r"|let me know when you('re| are) ready)\b"
)
PROHIBITION = re.compile(
    r"(?i)\bdo\s+not\s+(correct|coach|teach|evaluate|score|grade|suggest|explain|define|reward)\b"
)
REDIRECT = re.compile(
    r"(?i)(ask (the|a|an|another) follow[- ]up|delete it and ask|ask something harder"
    r"|move on instead|instead\b)"
)
BACKREFS = (
    "same rules as before",
    "as established earlier",
    "rules from part",
    "as in part one",
    "as in part 1",
    "continued from",
    "see the previous prompt",
    "(continued)",
    "carry over the rules",
)


class Result:
    """One property verdict. `ok` plus a reason when it is not."""

    def __init__(self, num: int, name: str, ok: bool, reason: str = ""):
        self.num, self.name, self.ok, self.reason = num, name, ok, reason

    def line(self) -> str:
        return (f"  {self.num} {self.name:<12} ok"
                if self.ok else
                f"  {self.num} {self.name:<12} FAIL - {self.reason}")

    def as_dict(self) -> dict:
        return {"property": self.num, "name": self.name,
                "ok": self.ok, "reason": self.reason}


def _nonempty_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def check_first_words(text: str) -> Result:
    m = PROBE_MARKER.search(text)
    if not m:
        return Result(1, "FIRST_WORDS", False,
                      "no literal probe block (expected a line starting `PROBE ...:`)")
    if text and m.start() / len(text) > 0.6:
        return Result(1, "FIRST_WORDS", False,
                      "probe block is buried in the back half of the file, not near the top")
    after = _nonempty_lines(text[m.end():])
    if not after or not after[0].lstrip().startswith(('"', "“", "'")):
        return Result(1, "FIRST_WORDS", False,
                      "probe block is not followed by a quoted verbatim utterance")
    # Tied to the delivery instruction, not a bare substring: the handshake already
    # says "Say nothing else", so a whole-file search passes a prompt whose PROBE
    # delivery is unconstrained.
    if not VERBATIM_CONSTRAINT.search(text):
        return Result(1, "FIRST_WORDS", False,
                      "delivery is not constrained - no 'verbatim ... and nothing else'")
    tail = _nonempty_lines(text)
    last = tail[-1] if tail else ""
    if AMBIGUOUS_TAIL.search(last):
        return Result(1, "FIRST_WORDS", False,
                      "ends on an ambiguous who-speaks handshake line")
    # Tail region, not strictly the last line: the terminator instruction is long enough
    # to wrap in some authors' hands, and wrapping is not a defect.
    if not TERMINATOR.search("\n".join(tail[-3:])):
        return Result(1, "FIRST_WORDS", False,
                      "does not close on the scripted terminator ('End of simulation.')")
    return Result(1, "FIRST_WORDS", True)


def check_role_bound(text: str, speaker: str) -> Result:
    name = re.escape(speaker)
    hits = len(re.findall(rf"(?i)\b{name}\b", text))
    if hits == 0:
        return Result(2, "ROLE_BOUND", False,
                      f"speaker is never named - '{speaker}' does not appear")
    # Deliberately tight. A loose "<name> ... says" window matches ordinary prose
    # ("Sam. Not case mode unless this file says otherwise") and would pass a file where
    # the name is present but bound to nothing.
    binding = re.search(
        rf"(?i)(\b(his|her|their) name is {name}\b"
        rf"|\bthe person (speaking|talking) to you is [^\n]{{0,60}}{name}\b"
        rf"|\b{name}\s+says?\s+[\"\u201c']"
        rf"|\b{name}\s+(turns|hands|is|has|will|goes|wants|asks)\b)",
        text,
    )
    if not binding:
        return Result(2, "ROLE_BOUND", False,
                      f"'{speaker}' appears but is never bound to the person speaking")
    if hits < 2:
        return Result(2, "ROLE_BOUND", False,
                      f"'{speaker}' is named only once - binding is not repeated")
    return Result(2, "ROLE_BOUND", True)


def check_prohibition(text: str) -> Result:
    for m in PROHIBITION.finditer(text):
        window = text[m.start():m.start() + 220]
        if REDIRECT.search(window):
            return Result(3, "PROHIBITION", True)
    if PROHIBITION.search(text):
        return Result(3, "PROHIBITION", False,
                      "forbidden moves listed with no pre-send redirect ('ask the follow-up instead')")
    return Result(3, "PROHIBITION", False, "no prohibition section")


def check_size(raw: bytes, max_bytes: int) -> Result:
    n = len(raw)
    if n > max_bytes:
        return Result(4, "SIZE", False, f"{n} bytes, ceiling is {max_bytes}")
    return Result(4, "SIZE", True)


def check_standalone(text: str, deps: list[Result]) -> Result:
    low = text.lower()
    for phrase in BACKREFS:
        if phrase in low:
            return Result(5, "STANDALONE", False,
                          f"back-references another paste: {phrase!r}")
    missing = [str(d.num) for d in deps if not d.ok]
    if missing:
        return Result(5, "STANDALONE", False,
                      "does not re-state everything a fresh chat needs "
                      f"(properties {', '.join(missing)} failed)")
    return Result(5, "STANDALONE", True)


def check_task_shape(text: str) -> Result:
    declares_case_mode = any(
        CASE_MODE.search(ln) and "not case mode" not in ln.lower()
        for ln in text.splitlines()
    )
    if declares_case_mode:
        return Result(6, "TASK_SHAPE", True)
    count = len(PROBE_MARKER.findall(text))
    if count != 1:
        return Result(6, "TASK_SHAPE", False,
                      f"{count} probes and no declared CASE MODE - a multi-probe paste "
                      "invites the partner to develop it into a case")
    head = "\n".join(_nonempty_lines(text)[:3])
    if not SINGLE_PROBE_DECL.search(head):
        return Result(6, "TASK_SHAPE", False,
                      "task shape is not declared up top ('SINGLE PROBE. Not case mode.')")
    return Result(6, "TASK_SHAPE", True)


def check_handshake(text: str) -> Result:
    if not HANDSHAKE_HEAD.search(text):
        return Result(7, "HANDSHAKE", False, "no HANDSHAKE section")
    if not READY_TOKEN.search(text):
        return Result(7, "HANDSHAKE", False,
                      "does not require a one-word 'Ready.' reply")
    if not GO_GATE.search(text):
        return Result(7, "HANDSHAKE", False,
                      "does not gate delivery on the operator saying \"go\"")
    return Result(7, "HANDSHAKE", True)


POSITION_NOTE = (
    "  8 POSITION     NOT CHECKED - a fact about where the text was pasted, not about "
    "the text.\n"
    "                 Durable half belongs in the host's persistent instructions field; "
    "variable\n"
    "                 half is message one of a FRESH chat. This is the property that "
    "broke four\n"
    "                 consecutive reps while every property above was satisfied. "
    "Verify it by hand."
)


def check_file(path: Path, speaker: str = DEFAULT_SPEAKER,
               max_bytes: int = DEFAULT_MAX_BYTES) -> list[Result]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    r1 = check_first_words(text)
    r2 = check_role_bound(text, speaker)
    r3 = check_prohibition(text)
    r4 = check_size(raw, max_bytes)
    r5 = check_standalone(text, [r1, r2, r3])
    r6 = check_task_shape(text)
    r7 = check_handshake(text)
    return [r1, r2, r3, r4, r5, r6, r7]


def render(path: Path, results: list[Result]) -> str:
    failed = [r for r in results if not r.ok]
    head = f"{path}"
    body = "\n".join(r.line() for r in results)
    if failed:
        verdict = (f"  VERDICT       NOT READY - {len(failed)} of 7 mechanical "
                   "properties failed.")
    else:
        verdict = ("  VERDICT       7 of 7 mechanical properties pass. This is NOT a "
                   "safety verdict:\n"
                   "                POSITION (8) is unverified and is the one that "
                   "actually broke reps.")
    return "\n".join([head, body, POSITION_NOTE, verdict, ""])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Structural checker for voice-sim probe prompt files "
                    "(7 of 8 properties; POSITION is not file-checkable).")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER,
                    help=f"operator's name for the role binding (default: {DEFAULT_SPEAKER})")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"size ceiling per paste (default: {DEFAULT_MAX_BYTES})")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    reports, any_failed = [], False
    for path in args.files:
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 2
        results = check_file(path, args.speaker, args.max_bytes)
        failed = any(not r.ok for r in results)
        any_failed = any_failed or failed
        if args.json:
            reports.append({
                "file": str(path),
                "passed": not failed,
                "properties": [r.as_dict() for r in results],
                "position_property_8": "not checkable from the file; verify by hand",
            })
        else:
            print(render(path, results))
    if args.json:
        print(json.dumps(reports, indent=2))
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
