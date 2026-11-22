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
      identical once every "|" and all whitespace are removed. So it may move text across
      cell boundaries and never reword.

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


_NOT_TEXT = re.compile(r"[|\s]")


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
    if _NOT_TEXT.sub("", old) != _NOT_TEXT.sub("", new):
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
    args = p.parse_args(argv)
    path = lessons_path()
    try:
        if not path.is_file():
            raise Refused([f"no such file: {path}"])
        if args.cmd == "set-promoted":
            out = set_promoted(path, args.match, args.value, args.expect_sha)
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
