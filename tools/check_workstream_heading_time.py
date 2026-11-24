#!/usr/bin/env python3
"""check_workstream_heading_time.py -- PreToolUse (Write|Edit|MultiEdit|Bash). BLOCKS a dated
section heading written into data/workstreams/*.md whose TIME was typed into the tool call.

Rule: feedback_log_timestamps_from_date_not_estimates (exit1, promoted: partial). Origin:
2026-11-21 18:47, a session wrote "## 2026-11-21 18:12 ..." into data/workstreams/hooks.md;
the clock read 18:47. Replay (2026-11-21..23, 190 transcripts): 203 dated-heading writes to
workstream files; 200 took the time from the clock at run time, 3 typed it, 1 of those wrong.

WHAT IT READS: the tool call, not the file. A literal time in the command was typed by
definition, even when it happens to be right; a file cannot tell a typed 18:12 from a stamped
one, which is why a content check over the file was rejected (Codex redundancy note, S5 brief).

  Bash   A heading line (`## YYYY-MM-DD ...` at the start of a line, e.g. a heredoc body, or
         right after `echo "` / `printf '`) in a command that writes a workstream file
         (redirect or tee onto it, sed/perl -i on it, or an interpreter given its path).
         Blocked when the time after the date is a literal (`18:12`, `~18:1x`, `9:0x`), or a
         $VAR / ${VAR} assigned a literal time EARLIER IN THE SAME COMMAND (`T=18:12; ...$T`),
         or the date slot itself is such a variable. Allowed: `$(date ...)`, a backtick date,
         a variable assigned from either, a variable not assigned in this command, and a
         heading with no time at all.
  Write / Edit / MultiEdit
         Any ADDED dated heading carrying a time in a workstream file is blocked: a tool call
         of this kind can only carry a literal.

ALLOWED WRITE PATH: tools/workstream_write.py add-section (stamps from the clock), or
`$(date +%H:%M)` in the heading.

KNOWN LIMITS (tripwire, not a wall): a literal assigned in an EARLIER separate Bash call and
reused via an exported variable; a script file that writes the heading; cp/mv of a prepared
file onto a workstream. Times typed in messages, briefs or boards outside data/workstreams/
are out of scope (7 of the rule's 10 fires were there).

STALE NEXT (Nick, 2026-11-23, codify #5): also BLOCKED, any of the above tools adding a dated
heading whose date is newer than the target file's newest json-next item. That is the shape
that failed S3's 07:59 suite. The newest date comes from workstream_write.newest_next_date,
which reads the block with workstream_state's own parser, so the guard, the writer and the
check cannot disagree. A run-time stamp ($(date ...), a variable assigned from it) is dated
today. A Write/Edit/MultiEdit is judged on the file as it would be after the edit, so bumping
the next block in the same edit passes. Files with no valid next block are not judged.
Replay since 2026-11-21: 24 of 210 heading writes, 7 (file, date) pairs, each cleared by one
add-next.

Exit 2 = block, with both fixes named. Fail open (exit 0) on a bad payload or internal error.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from workstream_write import newest_next_date  # noqa: E402

WS_PATH = re.compile(r"data/workstreams/[^\s/'\"<>|;&()]+\.md")
WS_FILE = re.compile(r"(?:^|/)data/workstreams/[^/]+\.md$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A typed time: 18:12, 9:05, ~18:15, 18:1x, 21:1X, 08:2?, 08:xx, ~8
TIME_LIT = re.compile(r"~?[ \t]*\d{1,2}:(?:\d[\dxX?]?|[xX?]{1,2})|~\d{1,2}\b")
# Heading start in a Bash command: line start (heredoc body, multi-line string), or straight
# after `echo "` / `printf '` (optionally with flags), or after a literal \n inside one.
# A quoted grep pattern is not a heading.
HEAD_BASH = re.compile(
    r"(?:^[ \t]*|\b(?:echo|printf)(?:[ \t]+-\w+)*[ \t]+[\"']|\\n)##[ \t]+(?P<rest>[^\n]*)",
    re.M)
HEAD_TEXT = re.compile(r"^[ \t]*##[ \t]+(?P<rest>[^\n]*)", re.M)
VAR_REF = re.compile(r"\$(?:\{(?P<b>\w+)\}|(?P<n>[A-Za-z_]\w*))")
REDIRECT = re.compile(
    r"(?<![<>&])\d?>>?\|?[ \t]*(?P<t>\"[^\"]*\"|'[^']*'|[^\s;&|<>()]+)")
TEE = re.compile(r"\btee\b(?P<args>[^\n;&|]*)")
# sed/perl -i: the edit script can span lines and contain `|` (`$a\\` then "## ... | x"), so
# no end of the match can be read reliably. The -i flag on the sed/perl line plus a workstream
# path anywhere in the command counts as a write to that path.
IN_PLACE = re.compile(r"\b(?:sed|perl)\b[^\n]*\s-i\b")
INTERP = re.compile(r"(?:^|[\s;&|(])(?:\w+=\S+\s+)*(?:python3?|perl|ruby|node)\b")
ASSIGN = re.compile(r"(?:^|[\s;&|(])(?:export[ \t]+|local[ \t]+)?([A-Za-z_]\w*)="
                    r"(\"[^\"]*\"|'[^']*'|\$\([^)]*\)|`[^`]*`|[^\s;&|]*)")

BLOCK_MSG = """BLOCKED (check_workstream_heading_time): a dated heading for {target} carries a TYPED time: {heading!r}
Rule feedback_log_timestamps_from_date_not_estimates: a time in a log heading comes from the clock, never from the keyboard (2026-11-21: "18:12" typed at 18:47).
Why: {why}.
Fix, either one:
  1. PYTHONIOENCODING=utf-8 python3 tools/workstream_write.py add-section {target} --title "<X>: <what>" --body-file - <<'EOF' ... EOF
     (stamps '## YYYY-MM-DD HH:MM by `date` | <X>: <what>' itself)
  2. Keep cat >>, with the stamp produced at run time: ## $(date '+%Y-%m-%d %H:%M') by `date` | <X>: <what>
A Write or Edit tool call can only carry a literal time, so dated workstream headings never go through Write/Edit."""


STALE_MSG = """BLOCKED (check_workstream_heading_time): stale next block in {target}: the newest next item is dated {newest}, and this adds a section dated {heading_date}.
That is the shape that failed S3's 07:59 suite on 2026-11-23 (the header's next date went stale behind the log).
Fix: run `tools/workstream_write.py add-next {target} --action ... --done-when ...` first, then write the section."""


def _today() -> str:
    return _date.today().isoformat()


def heading_date(rest: str, assigns: dict[str, list[str]]) -> str | None:
    """The date a heading's text (after '## ') carries: a literal date, today for a run-time
    stamp, the date inside a literal assigned in this command, or None (not judged)."""
    rest = rest.lstrip()
    m = DATE.match(rest)
    if m:
        return m.group(0)
    if rest.startswith("$(") or rest.startswith("`"):
        return _today()
    v = VAR_REF.match(rest)
    if not v:
        return None
    vals = assigns.get(v.group("b") or v.group("n"), [])
    if not vals:
        return None
    val = vals[-1]
    if "$(" in val or "`" in val:
        return _today()
    d = DATE.search(val)
    return d.group(0) if d else None


def _newest(path: Path, text: str | None = None) -> str | None:
    try:
        text = path.read_text(encoding="utf-8") if text is None else text
    except OSError:
        return None
    return newest_next_date(text, _date.today())


def _resolve(target: str, base: Path) -> Path:
    p = Path(target)
    return p if p.is_absolute() else base / p


def assignments(cmd: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in ASSIGN.finditer(cmd):
        out.setdefault(m.group(1), []).append(m.group(2))
    return out


def literal_time_value(value: str) -> bool:
    """True if an assignment's value is a literal carrying a time (no run-time substitution)."""
    if "$" in value or "`" in value:
        return False
    return bool(TIME_LIT.search(value))


def _typed_var(rest: str, assigns: dict[str, list[str]]) -> str | None:
    v = VAR_REF.match(rest)
    if not v:
        return None
    name = v.group("b") or v.group("n")
    if any(literal_time_value(val) for val in assigns.get(name, [])):
        return f"${name} is assigned a typed time earlier in this command"
    return None


def typed_time_in_rest(rest: str, assigns: dict[str, list[str]]) -> str | None:
    """Why the heading text `rest` (after '## ') carries a typed time, or None."""
    rest = rest.lstrip()
    m = DATE.match(rest)
    if not m:
        # The date slot is a variable standing for date + time, or this is not dated.
        return _typed_var(rest, assigns)
    after = rest[m.end():].lstrip()
    # `$(date ...)` and a backtick date fall through to None: neither is a $NAME reference
    # nor starts with digits.
    if after.startswith("$"):
        return _typed_var(after, assigns)
    t = TIME_LIT.match(after)
    if t:
        return f"the time {t.group(0).strip()!r} is typed into the tool call"
    return None


def writes_workstream(cmd: str) -> str | None:
    """The workstream path this command writes, if it writes one."""
    paths = WS_PATH.findall(cmd)
    if not paths:
        return None
    assigns = assignments(cmd)
    for m in REDIRECT.finditer(cmd):
        t = m.group("t").strip("\"'")
        hit = WS_PATH.search(t)
        if hit:
            return hit.group(0)
        v = VAR_REF.match(t)
        if v:
            name = v.group("b") or v.group("n")
            for val in assigns.get(name, []):
                hit = WS_PATH.search(val)
                if hit:
                    return hit.group(0)
    for m in TEE.finditer(cmd):
        hit = WS_PATH.search(m.group(0))
        if hit:
            return hit.group(0)
    if IN_PLACE.search(cmd) or INTERP.search(cmd):
        return paths[0]
    return None


def _full_target(cmd: str, tail: str) -> str:
    """The whole path token that ends in `tail` (an absolute path keeps its prefix)."""
    m = re.search(r"[^\s'\"<>|;&()=]*" + re.escape(tail), cmd)
    return m.group(0) if m else tail


def check_bash(cmd: str, base: Path) -> tuple | None:
    target = writes_workstream(cmd)
    if not target:
        return None
    assigns = assignments(cmd)
    heads = [m.group("rest") for m in HEAD_BASH.finditer(cmd)]
    for rest in heads:
        why = typed_time_in_rest(rest, assigns)
        if why:
            return target, "## " + rest.strip()[:80], why
    dates = [d for d in (heading_date(r, assigns) for r in heads) if d]
    if dates:
        newest = _newest(_resolve(_full_target(cmd, target), base))
        if newest is not None and max(dates) > newest:
            return ("stale", target, newest, max(dates))
    return None


def timed_headings(text: str) -> set[str]:
    return {m.group("rest").strip() for m in HEAD_TEXT.finditer(text)
            if typed_time_in_rest(m.group("rest"), {})}


def _dated_headings(text: str) -> set[str]:
    return {m.group(0) for m in (DATE.match(h.group("rest").lstrip())
                                 for h in HEAD_TEXT.finditer(text)) if m}


def check_write_edit(tool: str, inp: dict) -> tuple | None:
    path = (inp.get("file_path") or "").replace("\\", "/")
    if not WS_FILE.search(path):
        return None
    try:
        current = Path(path).read_text(encoding="utf-8")
    except OSError:
        current = ""
    if tool == "Write":
        pairs = [(current, inp.get("content") or "")]
        after = inp.get("content") or ""
    elif tool == "Edit":
        pairs = [(inp.get("old_string") or "", inp.get("new_string") or "")]
    else:  # MultiEdit
        pairs = [(e.get("old_string") or "", e.get("new_string") or "")
                 for e in (inp.get("edits") or []) if isinstance(e, dict)]
    if tool != "Write":
        after = current
        for old, new in pairs:
            if old and old in after:
                after = after.replace(old, new, 1)
    for old, new in pairs:
        added = timed_headings(new) - timed_headings(old)
        if added:
            return (path, "## " + sorted(added)[0][:80],
                    f"a {tool} tool call adds it, and a {tool} call can only carry a "
                    "literal time")
    added_dates = set().union(*[_dated_headings(new) - _dated_headings(old)
                                for old, new in pairs]) if pairs else set()
    if added_dates:
        newest = newest_next_date(after, _date.today())
        if newest is not None and max(added_dates) > newest:
            return ("stale", path, newest, max(added_dates))
    return None


def evaluate(tool: str, inp: dict, base: Path | None = None) -> tuple | None:
    if tool == "Bash":
        return check_bash(inp.get("command") or "", base or Path.cwd())
    if tool in ("Write", "Edit", "MultiEdit"):
        return check_write_edit(tool, inp)
    return None


def main() -> int:
    p = read_payload()
    if not p.ok:
        return 0  # fail open: no payload, nothing to judge
    data = p.data if isinstance(p.data, dict) else {}
    inp = data.get("tool_input")
    base = Path(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    hit = evaluate(data.get("tool_name", ""), inp if isinstance(inp, dict) else {}, base)
    if not hit:
        return 0
    if hit[0] == "stale":
        _, target, newest, heading_date_ = hit
        print(STALE_MSG.format(target=target, newest=newest, heading_date=heading_date_),
              file=sys.stderr)
        return 2
    target, heading, why = hit
    print(BLOCK_MSG.format(target=target, heading=heading, why=why), file=sys.stderr)
    return 2


def run() -> int:
    """main() with the fail-open policy for an internal error: allow, and say so."""
    try:
        return main()
    except Exception as e:
        print(f"check_workstream_heading_time.py error (allowing through): {e}",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(run())
