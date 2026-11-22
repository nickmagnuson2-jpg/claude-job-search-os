#!/usr/bin/env python3
"""check_lessons_write.py -- PreToolUse hook (Write|Edit|MultiEdit, and Bash).

BLOCKS a write to memory/lessons.md (this repo's main checkout) that would add or change a
Section 2 row that fails validation, or that changes the file in a way the hook cannot
read. Built 2026-11-22 as B2 of the learning-loop build (org job-search-68).
WIRING: this hook runs only once .claude/settings.json registers it (PreToolUse,
Write|Edit|MultiEdit|Bash); tests/scripts/test_b2_hooks_registered.py checks that.

WHY. Section 2 ("| Pattern | Rule | Occurrences | Promoted | Date |") is read by the
learning ledger. Nine broken row shapes had to be repaired by hand on 2026-11-22: wrong
cell counts and Promoted values outside the vocabulary. The replay of every session
transcript (output/memory-hygiene/112226-b2-replay-corpus.jsonl) found most writes to the
file were inline Python scripts, which nothing validated.

WHAT IT CHECKS (policy; the row validator is learning_state.parse_lessons_row +
lessons_state):
  Write / Edit / MultiEdit   the file as it would be after the call. Every Section 2 row
                             that is NEW or CHANGED compared with the current file must be
                             valid; a row that was already broken and is untouched does not
                             block. Edit fails silently on rows over 500 characters
                             (CLAUDE.md), so prefer lessons_write.py for those.
  Bash
    append (`>>`, heredoc / echo / printf)  every table row appended must be valid.
    anything else                           blocked: an overwrite (`>`), `sed -i`, `tee`,
                                            a redirect from a program, cp/mv onto the file,
                                            or an interpreter script that names the file
                                            and contains a write call.
                                            Route: tools/lessons_write.py (set-promoted,
                                            append-row) or a full-file Write.
  A script FILE under this repo's tools/ (lessons_write.py among them) is not judged here.

ADVERSARY. The agent; see check_memory_frontmatter_write.py. A tripwire, not a boundary.
Fail policy: an unreadable payload exits 0 (open); a payload truncated at the intake cap
exits 2 when its raw text names memory/lessons.md.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
import learning_state as ls  # noqa: E402
import check_memory_frontmatter_write as cm  # noqa: E402  (shared Bash write walk)

NAME = "check_lessons_write"
ROUTE = ("route: `lessons_write.py set-promoted <row-match> <value>` or "
         "`lessons_write.py append-row --pattern ... --date YYYY-MM-DD`, or a full-file Write")
SECTION_2 = "## Section 2"


def lessons_path() -> Path:
    override = os.environ.get("LESSONS_HOOK_PATH")          # tests only
    return Path(override) if override else cm.REPO_ROOT / "memory" / "lessons.md"


def in_scope(path: str, cwd: str = "") -> bool:
    return bool(path) and Path(cm._norm(path, cwd)) == lessons_path()


def names_scope(text: str) -> bool:
    """Script text names the lessons file: its absolute path, or `memory/lessons.md`
    as a whole path literal (not `reusable-lessons.md`)."""
    return (str(lessons_path()) in text
            or re.search(r"(?<![\w.\-/])(?:\./)?memory/lessons\.md\b", text) is not None)


def section2_rows(text: str) -> list[str]:
    """The table lines of Section 2 (header and separator included; parse skips them)."""
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.startswith(SECTION_2)]
    if not starts:
        return []
    out = []
    for ln in lines[starts[0] + 1:]:
        if ln.startswith("## "):
            break
        if ln.startswith("|"):
            out.append(ln)
    return out


def row_problems(rows: list[str]) -> list[str]:
    reasons = []
    for row in rows:
        cells, defect = ls.parse_lessons_row(row)
        if cells is None:
            if defect:
                reasons.append(f"{defect}: {row[:90]}")
            continue
        _disp, _due, defects = ls.lessons_state(cells, memory_dir=cm.memory_dir(),
                                                repo_root=cm.REPO_ROOT)
        reasons += [f"{d}: {row[:90]}" for d in defects]
    return reasons


def judge_text(new: str, old: str | None) -> list[str]:
    """Problems with the Section 2 rows this write ADDS. Rows are compared as a multiset:
    each existing copy of a row is grandfathered once, so a write that turns a valid row
    into a second copy of an old broken row is still judged (Codex r1 F4)."""
    old_rows = section2_rows(old) if old is not None else []
    if old_rows and not section2_rows(new):
        return ["the write removes the Section 2 table"]
    remaining = Counter(old_rows)
    added = []
    for row in section2_rows(new):
        if remaining[row]:
            remaining[row] -= 1
        else:
            added.append(row)
    return row_problems(added)


def judge_bash(command: str, cwd: str) -> list[str]:
    reasons = []
    for a in cm.bash_actions(command, cwd, in_scope, names_scope):
        if a.shape.startswith("blocked:"):
            reasons.append(f"lessons.md: {a.shape[len('blocked:'):]}")
        elif a.shape == "create":
            reasons.append("lessons.md: an overwrite from Bash (`>`)")
        else:
            rows = [ln for ln in a.content.split("\n") if ln.startswith("|")]
            if not rows:
                reasons.append("lessons.md: an append that adds no table row")
            reasons += [f"lessons.md: {r}" for r in row_problems(rows)]
    return reasons


def judge(p) -> list[str]:
    """Every reason to block this call; [] to allow. One exit (see check_memory_frontmatter_write)."""
    tool, ti = p.tool_name, p.tool_input
    reasons: list[str] = []
    if tool == "Bash":
        reasons = judge_bash(p.command, p.cwd)
    elif tool in ("Write", "Edit", "MultiEdit") and in_scope(p.file_path, p.cwd):
        current = cm._read(lessons_path())
        new = cm.text_after(tool, ti, current)
        if new is not None:
            reasons = [f"lessons.md: {r}" for r in judge_text(new, current)]
    return reasons


def main() -> None:
    p = read_payload()
    if not p.ok:
        if p.truncated and names_scope(p.raw):
            sys.stderr.write(f"BLOCKED by {NAME}: the payload was cut off at the intake cap, "
                             "so a write to memory/lessons.md could not be checked\n")
            sys.exit(2)
        sys.exit(0)          # unreadable payload: fail open (policy)
    reasons = judge(p)
    if reasons:
        sys.stderr.write(f"BLOCKED by {NAME}: memory/lessons.md Section 2\n"
                         + "".join(f"  - {r}\n" for r in reasons) + f"  {ROUTE}\n")
        sys.exit(2)
    # Falling off the end exits 0 (see check_memory_frontmatter_write.main).


if __name__ == "__main__":
    main()
