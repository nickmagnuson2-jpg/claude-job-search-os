#!/usr/bin/env python3
"""memory_frontmatter.py: the one reader and the one validating writer of memory-rule frontmatter.

WHY ONE MODULE (Nick, 2026-11-22: "I want to codify on one"). Eight tools in tools/ each
carried their own frontmatter parser, and they disagreed: on 1426 rule files the regex
scrapes and the strict reader differed on `promoted` in 498 files (YAML 1.1 reads an
unquoted `no` as a boolean), on `terminal` in 77 (a scrape keeps an inline `# comment` as
part of the value) and on `reopen_gate` in 5. One rule was due and read as promoted because
of a kept comment. This module is extracted from `detector_run.parse_frontmatter`, which
already used the strict reader for the same reason (a scrape returned `\\b` where the writer
meant a word boundary, and 40 of 50 detectors were refused).

Contract:
  * `read(text)` returns `(fields, defect)`. A malformed block is a NAMED defect, never a
    silent `{}`: a reader that cannot tell "no fields" from "unreadable" fails open.
  * The `metadata:` mapping is flattened into the top level. A key present at both levels
    is a defect (`duplicate_key`), because the two readers it replaces each took a
    different copy.
  * One value type per meaning: YAML 1.1 booleans become "yes" / "no", null becomes "",
    dates become ISO strings. So `promoted: no` and `promoted: "no"` read the same.
  * `write(path, updates)` is the only sanctioned machine writer. It edits only the lines of
    the keys it sets, re-reads the result with `read`, refuses unless every update reads
    back as written, no other field changed, and `learning_state.validate_fields` finds no
    defect in the keys that changed. It never writes `occurrences` (the state matrix: a
    count is recorded by the person who saw the fire, never by a machine).

Nothing else in tools/ may split a `---` block or scrape a frontmatter key with a regex:
tests/scripts/test_learning_state_single_source.py fails on any such site.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import tempfile
from pathlib import Path
from typing import NamedTuple

import yaml

# The block must open on the first line and close on a line of exactly three dashes.
_BLOCK = re.compile(r"\A---\n(.*?\n)?---(?:\n|\Z)", re.DOTALL)

NEVER_MACHINE_WRITTEN = frozenset({"occurrences"})


class Defect(NamedTuple):
    """Why a frontmatter block could not be read. `code` is one of DEFECT_CODES."""
    code: str
    detail: str


DEFECT_CODES = ("no_frontmatter", "yaml_error", "not_a_mapping", "duplicate_key")


class WriteRefused(Exception):
    """write() refused; nothing was written. `.reasons` lists every reason."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def split(text: str) -> tuple[str, str] | None:
    """(block, rest) where block is the text between the two `---` lines, or None when the
    file does not open with a frontmatter block."""
    raise NotImplementedError


def normalize(value):
    """One type per meaning: bool -> "yes"/"no", None -> "", date/datetime -> ISO string.
    Lists and dicts are normalized element-wise; everything else is returned unchanged."""
    raise NotImplementedError


def read(text: str) -> tuple[dict, Defect | None]:
    """Strictly parse the frontmatter of `text`.

    Returns (fields, None) on success, with `metadata:` flattened and values normalized, or
    ({}, Defect) when there is no block, the YAML does not parse, the block is not a
    mapping, or a key appears both at the top level and under `metadata:`.
    """
    raise NotImplementedError


def read_file(path: Path) -> tuple[dict, Defect | None]:
    """read() over a file's text (UTF-8)."""
    raise NotImplementedError


def check_text(new_text: str, old_text: str | None = None, *, memory_dir: Path | None = None,
               repo_root: Path | None = None, skills_home: Path | None = None) -> list[str]:
    """Every reason `new_text` must not be written; [] when it may. The check write() runs,
    exposed for the write-time hooks (content not yet on disk).

    A frontmatter defect in new_text is reported as "frontmatter: <code>: <detail>".
    Otherwise learning_state.validate_fields runs on the keys whose value differs from
    old_text (every key when old_text is None or itself unreadable), so an older defect in
    an untouched key does not block an unrelated edit. A change to a key in
    NEVER_MACHINE_WRITTEN is NOT refused here: a person records a fire by hand.
    """
    raise NotImplementedError


def record_fire(path: Path, evidence: str, *, today: _dt.date | None = None) -> int:
    """Record one witnessed fire: occurrences + 1, with its evidence. Returns the new count.

    The ONLY code path that changes `occurrences` (org 68 ruling, 2026-11-22, flagged for
    Nick). Nick's 2026-09-15 decision bars detectors and automatic jobs from writing a
    count; a session recording a fire it witnessed is the existing practice, now validated.

    Refuses (WriteRefused, file untouched) when: CLAUDECODE is not "1" in the environment
    (a tripwire against cron and scripts, not a security boundary: headless Claude jobs
    carry it too); `evidence` is blank or carries no YYYY-MM-DD date; the frontmatter has a
    defect; `occurrences` is missing or not a non-negative int. On success it rewrites only
    the occurrences line and appends one dated line to the end of the body:
    "- Fire <n> recorded <today>: <evidence>".
    """
    raise NotImplementedError


def write(path: Path, updates: dict, *, memory_dir: Path | None = None,
          repo_root: Path | None = None, skills_home: Path | None = None) -> dict:
    """Set `updates` in the frontmatter of `path`, validated, atomically. Returns the new fields.

    An existing key's line (and any continuation lines indented under it) is replaced in
    place, at its own level. A new key goes under `metadata:` when the block has one, else
    at the end of the block. Values are rendered by yaml.safe_dump, so a string that YAML
    would read as a boolean or a number is quoted.

    Refuses (WriteRefused, file untouched) when: the file's frontmatter has a defect; an
    update names a key in NEVER_MACHINE_WRITTEN; the result does not re-read cleanly; any
    update reads back different from what was asked; any field not named in `updates`
    changed; or learning_state.validate_fields reports a defect in an updated key.
    `memory_dir` / `repo_root` are passed to the validator to resolve pointers and paths.
    """
    raise NotImplementedError
