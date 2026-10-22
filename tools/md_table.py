#!/usr/bin/env python3
"""md_table.py — shared mechanism for the atomic markdown-table writers.

Until 2026-10-21 these helpers were copied into up to eleven tools (pipe_write,
networking_write, act_apply, remember_apply, todo_write, personal_todo_write,
person_write, my_world_synthesis, granola_save, living_log_append,
migrate_todos_column_drift). The copies were identical, so they live here once.

MECHANISM ONLY. What counts as a data row (which header names to skip) is each
writer's policy, so `table_insert_pos` takes the caller's `is_data_row`. Variants
that differ on purpose stay in their own files: the todo writers read with a
narrower error catch, match section headers exactly rather than by regex, and
emit errors without a code.
"""
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


def out_ok(action: str, summary: str, **extra) -> None:
    d = {"status": "ok", "action": action, "summary": summary}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    d = {"status": "error", "message": message, "code": code}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


def safe_cell(s: str) -> str:
    """Turn a literal "|" into "/" so a value cannot split its row into extra columns
    (the cause of the 2026-07 job-todos.md column drift)."""
    return s.replace("|", "/") if s else s


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
