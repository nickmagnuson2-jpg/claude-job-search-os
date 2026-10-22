#!/usr/bin/env python3
"""md_table.py — shared mechanism for the atomic markdown-table writers.

Until 2026-10-21 these helpers were copied into up to eleven tools (pipe_write,
networking_write, act_apply, remember_apply, todo_write, personal_todo_write,
person_write, my_world_synthesis, granola_save, living_log_append,
migrate_todos_column_drift). Each group of identical copies lives here once. Three
differences from what they replaced: table_insert_pos takes the caller's is_data_row as
a fourth argument; safe_cell is todo_write's former _safe_cell; and write_atomic creates
a missing parent folder, which the copies in todo_write, personal_todo_write,
granola_save and living_log_append did not.

MECHANISM ONLY. What counts as a data row (which header names to skip) is each
writer's policy, so `table_insert_pos` takes the caller's `is_data_row`. Variants
that differ on purpose stay in their own files: the todo writers read with a
narrower error catch, match section headers exactly rather than by regex, and
emit errors without a code.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


def read_file(path: Path) -> str:
    """File contents, or "" when the file cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def write_atomic(path: Path, content: str) -> None:
    """Write via a sibling temp file and an atomic rename, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def load_lines(path: Path) -> tuple[str, list[str]]:
    """(content, lines without line endings), or the file_not_found error JSON and exit 1."""
    content = read_file(path)
    if not content:
        out_error(f"File not found or empty: {path}", "file_not_found")
    lines = [ln.rstrip("\n").rstrip("\r") for ln in content.splitlines(keepends=True)]
    return content, lines


def out_ok(action: str, summary: str, **extra) -> None:
    d = {"status": "ok", "action": action, "summary": summary}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    d = {"status": "error", "message": message, "code": code}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors also print the error JSON on stdout.

    A stock argparse rejection writes only to stderr, so a caller parsing stdout got
    an empty string and a JSONDecodeError that hid the real usage message (96 fires
    on pipe_write.py alone, 2026-06 to 2026-10). Exit code stays 2 and stderr is
    unchanged. Subparsers inherit the class from add_subparsers().
    """

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        print(json.dumps({"status": "error", "code": "usage",
                          "message": f"{message}. {self.format_usage().strip()}"},
                         ensure_ascii=False))
        sys.exit(2)


def safe_cell(s: str) -> str:
    """Turn a literal "|" into "/" so a value cannot split its row into extra columns
    (the cause of the 2026-07 job-todos.md column drift)."""
    return s.replace("|", "/") if s else s


def merge_notes(current: str, prepend: str | None = None, append: str | None = None) -> str:
    """A Notes cell with text added at either end. "" and "—" count as empty.

    Both ends apply when both are given. todo_write's inline copy built the append from
    the ORIGINAL notes, so `--notes-prepend A --notes-append B` silently dropped A.
    """
    notes = "" if current.strip() in ("", "—") else current
    if prepend:
        notes = f"{prepend} {notes}".strip()
    if append:
        notes = f"{notes} {append}".strip()
    return notes


def is_sep_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|[-: |]+\|\s*$", line))


def parse_cols(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def find_section(lines: list, pattern: str) -> tuple:
    """Find the section whose header matches a regex. Returns (start_idx, end_idx),
    end exclusive, or (-1, -1)."""
    start = -1
    for i, line in enumerate(lines):
        if re.match(pattern, line, re.I):
            start = i
            break
    if start == -1:
        return (-1, -1)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return (start, end)


def table_insert_pos(lines: list, sec_start: int, sec_end: int, is_data_row) -> int:
    """Index at which to insert a new row: after the last data row, else after the
    separator, else after the first pipe row, else at the section end."""
    last = -1
    for i in range(sec_start, sec_end):
        if is_data_row(lines[i]):
            last = i
    if last != -1:
        return last + 1
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|") and "---" in lines[i]:
            return i + 1
    for i in range(sec_start, sec_end):
        if lines[i].startswith("|"):
            return i + 1
    return sec_end
