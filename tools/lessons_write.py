#!/usr/bin/env python3
"""lessons_write.py: the sanctioned writer for memory/lessons.md Section 2 rows.

WHY IT EXISTS (B2 of the 2026-11-22 learning-loop build, org job-search-68). A replay of
the Bash calls in every session transcript found that most writes to memory/lessons.md were
inline Python scripts that rewrote a row's Promoted cell or spliced a new row in. Nine
row shapes broken that way had to be repaired by hand on 2026-11-22. Edit is no
alternative: Edit silently fails on rows over 500 characters, and lessons rows routinely
exceed that (CLAUDE.md, Write-Only Files). The write-time hook (check_lessons_write.py)
now blocks script writes to the file and routes them here. Every row this tool writes
passes learning_state.parse_lessons_row (exactly 5 cells) and learning_state.lessons_state
(a valid Promoted token with its pointer, a numeric Occurrences cell, a YYYY-MM-DD Date).

Subcommands (Section 2 only, the "| Pattern | Rule | Occurrences | Promoted | Date |" table):

  set-promoted <row-match> <value> [--expect-sha <sha256 of the file>]
      Replace the Promoted cell of the ONE Section 2 row containing <row-match>. Refused
      when <row-match> is shorter than 12 characters or matches no row or several rows,
      or when the resulting row fails validation. Nothing else in the file changes.
      --expect-sha refuses when the file changed since the caller read it; optional by
      hand, always passed by a scripted apply (77's apply review F2, 2026-11-22).

  append-row --pattern P --rule R --occurrences N --promoted V --date YYYY-MM-DD
      Add a new row at the top of the Section 2 table (newest first, the file's order).

  repair-row --line N --expect-sha <sha256 of the file> --old "<exact row>" --new "<row>"
      STRUCTURAL repair of one Section 2 row that cannot be read (for example text after
      its closing pipe), for the approved cutover repairs (org 72, 2026-11-22). Refused
      unless: CLAUDECODE=1 (a tripwire for sessions, as in memory_write); the file's
      sha256 is --expect-sha; line N is in the Section 2 table and equals --old byte for
      byte; --new passes the row validator; and the TEXT IS PRESERVED: --old and --new are
      identical once every "|" becomes a space and each run of whitespace becomes one
      space. So it may move text across cell boundaries, and never reword or join words
      (Codex confirmation F3: removing ALL whitespace let "a b" become "ab").

  bump <row-match> --evidence "<what happened, YYYY-MM-DD>" [--expect-sha <sha>] [--dry-run]
      Record ONE recurrence of the ONE Section 2 row containing <row-match> (same match
      rule as set-promoted): Occurrences N becomes N+1, the Pattern cell gains
      " **<ordinal> occurrence (<date>):** <evidence>", and the Date cell keeps its first
      date and gains " (<ordinal>: <date>)", the shapes rows already use. <date> is the
      first YYYY-MM-DD in the evidence. Refused when the evidence has no date or holds a
      "|" or a newline, the Occurrences cell is not a non-negative integer, or the new row
      fails the validator. Like memory_write.py bump (a count records a witnessed event,
      Nick's 2026-09-15 decision), the write refuses unless CLAUDECODE=1. --dry-run prints
      the row before and after and writes nothing. Added 2026-11-24 (S4): before it, the
      11-22 cutover left no sanctioned way to record a recurring lesson (row 167).

Cells may not contain "|" or a newline: either would break the table, and the reader
would then see the wrong cell count.

The file is memory/lessons.md of the main checkout; LESSONS_PATH overrides it, as in
tools/draft_capture.py. Output: one JSON object on stdout. Exit 0 written, 1 refused
(nothing written; `reasons` lists why), 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning_state as ls  # noqa: E402
from vault_paths import main_checkout  # noqa: E402
from hook_runtime import claude_project_dir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pointers (a superseded path, a skill_file) resolve against the MAIN checkout: gitignored
# targets (framework/, memory/, data/) exist only there, so a run from a worktree would
# refuse a valid pointer (77's finding, 2026-11-22).
POINTER_ROOT = main_checkout(REPO_ROOT)
SECTION_2 = "## Section 2"
MIN_MATCH = 12
PROMOTED_CELL = ls.LESSONS_COLUMNS.index("Promoted")


class Refused(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons


def memory_dir() -> Path:
    """The live memory folder, where a `merged into <file>` pointer must resolve (Grok r1
    F2). MEMORY_DIR overrides it, for tests and dry runs on a copy."""
    override = os.environ.get("MEMORY_DIR")
    return Path(override) if override else claude_project_dir(main_checkout(REPO_ROOT)) / "memory"


def lessons_path() -> Path:
    override = os.environ.get("LESSONS_PATH")
    return Path(override) if override else main_checkout(REPO_ROOT) / "memory" / "lessons.md"


def section2_bounds(lines: list[str]) -> tuple[int, int]:
    """(first data line, end) of the Section 2 table: end is exclusive. Refused when the
    section or its header and separator rows are missing or not as expected."""
    starts = [i for i, ln in enumerate(lines) if ln.startswith(SECTION_2)]
    if len(starts) != 1:
        raise Refused([f"expected exactly one '{SECTION_2}' heading, found {len(starts)}"])
    i = starts[0] + 1
    while i < len(lines) and not lines[i].startswith("|"):
        if lines[i].startswith("## "):
            raise Refused(["Section 2 has no table"])
        i += 1
    header = [c.strip() for c in lines[i].strip().strip("|").split("|")] if i < len(lines) else []
    if tuple(header) != ls.LESSONS_COLUMNS:
        raise Refused([f"Section 2 header is {header}, expected {list(ls.LESSONS_COLUMNS)}"])
    if i + 1 >= len(lines) or not set(lines[i + 1].replace("|", "").strip()) <= {"-", ":", " "}:
        raise Refused(["Section 2 header has no separator row"])
    first = i + 2
    end = first
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return first, end


def format_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def validate_cells(cells: list[str]) -> list[str]:
    reasons = []
    for name, cell in zip(ls.LESSONS_COLUMNS, cells):
        if "|" in cell or "\n" in cell or "\r" in cell:
            reasons.append(f"{name}: may not contain '|' or a newline")
        if not cell.strip():
            reasons.append(f"{name}: empty")
    if reasons:
        return reasons
    # No cell holds a pipe or a newline, so format_row(cells) is exactly 5 cells and
    # parse_lessons_row would return these cells unchanged: validate them directly.
    _disp, _due, defects = ls.lessons_state(cells, memory_dir=memory_dir(), repo_root=POINTER_ROOT)
    return list(defects)


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and os.replace, keeping the file's permission bits."""
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".lessons-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)          # mkstemp created it; a failed replace leaves it behind
        raise


def read_lines(path: Path) -> tuple[list[str], bool]:
    text = path.read_text(encoding="utf-8")
    return text.split("\n"), text.endswith("\n")


def file_sha(path: Path, expect: str | None) -> str:
    """The file's sha256; Refused when `expect` is given and differs."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if expect is not None and sha != expect:
        raise Refused([f"file changed: sha256 is {sha}, expected {expect}"])
    return sha


def set_promoted(path: Path, match: str, value: str, expect_sha: str | None = None) -> dict:
    if len(match.strip()) < MIN_MATCH:
        raise Refused([f"row-match must be at least {MIN_MATCH} characters"])
    sha = file_sha(path, expect_sha)
    lines, _ = read_lines(path)
    first, end = section2_bounds(lines)
    hits = [i for i in range(first, end) if match in lines[i]]
    if len(hits) != 1:
        raise Refused([f"row-match {match!r} matches {len(hits)} Section 2 rows, expected 1"])
    i = hits[0]
    cells, defect = ls.parse_lessons_row(lines[i])
    if cells is None:
        raise Refused([f"the matched row cannot be read: {defect}"])
    old = cells[PROMOTED_CELL]
    new_cells = list(cells)
    new_cells[PROMOTED_CELL] = value.strip()
    reasons = validate_cells(new_cells)
    if reasons:
        raise Refused(reasons)
    lines[i] = format_row(new_cells)
    atomic_write(path, "\n".join(lines))
    return {"action": "set-promoted", "line": i + 1, "old": old, "new": value.strip(),
            "sha256_before": sha}


def append_row(path: Path, cells: list[str]) -> dict:
    cells = [c.strip() for c in cells]
    lines, _ = read_lines(path)
    first, _end = section2_bounds(lines)
    reasons = validate_cells(cells)
    if reasons:
        raise Refused(reasons)
    lines.insert(first, format_row(cells))
    atomic_write(path, "\n".join(lines))
    return {"action": "append-row", "line": first + 1}


def row_text(row: str) -> str:
    """The row's words in order: pipes count as word boundaries, whitespace runs as one."""
    return " ".join(row.replace("|", " ").split())


def repair_row(path: Path, line: int, expect_sha: str, old: str, new: str) -> dict:
    reasons = []
    if os.environ.get("CLAUDECODE") != "1":
        raise Refused(["CLAUDECODE=1 is not set: repair-row applies an approved repair "
                       "from a session, not from a scheduled job"])
    sha = file_sha(path, expect_sha)
    lines, _ = read_lines(path)
    first, end = section2_bounds(lines)
    if not first <= line - 1 < end:
        raise Refused([f"line {line} is not a Section 2 row (rows are lines {first + 1}-{end})"])
    if lines[line - 1] != old:
        raise Refused([f"line {line} does not equal --old byte for byte"])
    if row_text(old) != row_text(new):
        reasons.append("--new changes the row's text; repair-row may only move cell "
                       "boundaries and whitespace")
    cells, defect = ls.parse_lessons_row(new)
    if cells is None:
        reasons.append(f"--new is not a readable row: {defect or 'not a table row'}")
    else:
        reasons += list(ls.lessons_state(cells, memory_dir=memory_dir(), repo_root=POINTER_ROOT)[2])
    if reasons:
        raise Refused(reasons)
    lines[line - 1] = new
    atomic_write(path, "\n".join(lines))
    return {"action": "repair-row", "line": line, "sha256_before": sha}


DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
OCCURRENCES_CELL = ls.LESSONS_COLUMNS.index("Occurrences")
PATTERN_CELL = ls.LESSONS_COLUMNS.index("Pattern")
DATE_CELL = ls.LESSONS_COLUMNS.index("Date")


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def bump(path: Path, match: str, evidence: str, expect_sha: str | None = None,
         dry_run: bool = False) -> dict:
    evidence = evidence.strip()
    if "|" in evidence or "\n" in evidence or "\r" in evidence:
        raise Refused(["evidence may not contain '|' or a newline"])
    found = DATE_RE.search(evidence)
    if not found:
        raise Refused(["evidence must say what happened and carry its YYYY-MM-DD date"])
    if len(match.strip()) < MIN_MATCH:
        raise Refused([f"row-match must be at least {MIN_MATCH} characters"])
    if not dry_run and os.environ.get("CLAUDECODE") != "1":
        raise Refused(["CLAUDECODE=1 is not set: bump records a recurrence a session "
                       "witnessed, never one a detector or scheduled job inferred"])
    sha = file_sha(path, expect_sha)
    lines, _ = read_lines(path)
    first, end = section2_bounds(lines)
    hits = [i for i in range(first, end) if match in lines[i]]
    if len(hits) != 1:
        raise Refused([f"row-match {match!r} matches {len(hits)} Section 2 rows, expected 1"])
    i = hits[0]
    cells, defect = ls.parse_lessons_row(lines[i])
    if cells is None:
        raise Refused([f"the matched row cannot be read: {defect}"])
    occ = cells[OCCURRENCES_CELL]
    if not occ.isdigit():
        raise Refused([f"Occurrences: {occ!r} is not a non-negative integer to add one to"])
    n = int(occ) + 1
    date, nth = found.group(1), ordinal(n)
    new_cells = list(cells)
    new_cells[OCCURRENCES_CELL] = str(n)
    new_cells[PATTERN_CELL] = f"{cells[PATTERN_CELL]} **{nth} occurrence ({date}):** {evidence}"
    new_cells[DATE_CELL] = f"{cells[DATE_CELL]} ({nth}: {date})"
    reasons = validate_cells(new_cells)
    if reasons:
        raise Refused(reasons)
    before, after = lines[i], format_row(new_cells)
    if not dry_run:
        lines[i] = after
        atomic_write(path, "\n".join(lines))
    return {"action": "dry-run" if dry_run else "bumped", "line": i + 1,
            "occurrences_before": n - 1, "occurrences_after": n,
            "before": before, "after": after, "sha256_before": sha}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lessons_write.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("set-promoted")
    ps.add_argument("match")
    ps.add_argument("value")
    ps.add_argument("--expect-sha", default=None)
    pa = sub.add_parser("append-row")
    for flag in ("--pattern", "--rule", "--occurrences", "--promoted", "--date"):
        pa.add_argument(flag, required=True)
    pr = sub.add_parser("repair-row")
    pr.add_argument("--line", type=int, required=True)
    pr.add_argument("--expect-sha", required=True)
    pr.add_argument("--old", required=True)
    pr.add_argument("--new", required=True)
    pb = sub.add_parser("bump")
    pb.add_argument("match")
    pb.add_argument("--evidence", required=True)
    pb.add_argument("--expect-sha", default=None)
    pb.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    path = lessons_path()
    try:
        if not path.is_file():
            raise Refused([f"no such file: {path}"])
        if args.cmd == "set-promoted":
            out = set_promoted(path, args.match, args.value, args.expect_sha)
        elif args.cmd == "bump":
            out = bump(path, args.match, args.evidence, args.expect_sha, args.dry_run)
        elif args.cmd == "repair-row":
            out = repair_row(path, args.line, args.expect_sha, args.old, args.new)
        else:
            out = append_row(path, [args.pattern, args.rule, args.occurrences,
                                    args.promoted, args.date])
    except Refused as exc:
        print(json.dumps({"action": "refused", "file": str(path), "reasons": exc.reasons},
                         ensure_ascii=False))
        return 1
    out["file"] = str(path)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
