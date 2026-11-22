#!/usr/bin/env python3
"""learning_state.py: the one definition of every learning-loop state.

Spec: output/memory-hygiene/112226-state-matrix.md (org 68, 2026-11-22). Nick's rulings it
implements (data/workstreams/memory-hygiene.md, 2026-11-22):
  1. Due = occurrences reach the number the record's own gate names. A lane with no gates
     (friction, email lessons) uses its ladder, 2. A record with no number is a defect,
     reported separately and never due. "I only want to have 1 definition of due."
  2. A hook block as designed is not friction.
  3. Half-life by kind: MACHINERY 60d, FACT 180d, lesson (PRINCIPLE + HYBRID) 240d.
  4. exit_path gains `skill`; promoted gains `superseded`, `merged`, `parked`, each with a
     pointer.

What lives here, and nowhere else in tools/ (tests/scripts/test_learning_state_single_source.py):
  * the promoted vocabulary and its two predicates (backlog and gate claim);
  * the exit paths;
  * due, from the typed `reopen_at` threshold. The prose `reopen_gate` is history and is
    never parsed for state: on 2026-11-22 two parsers read the same live gates two ways
    ("TRIPPED at fire 5 ... the gate named 3"), and no grammar reads that prose reliably;
  * the friction event kinds and resolution;
  * the lessons.md Section 2 row shape;
  * kinds, half-lives and freshness.

Unknown is never filled with a value. An unrecognised token, a missing pointer, a missing
`reopen_at` and an untrusted kind each come back as a named state, and a record in a defect
state stays open.

Nothing imports this module yet. The reader cutover is one later commit, bound to the
hash-checked dry-run report Nick approves (design v4 section 8).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------- promoted vocabulary
# Every token in live use on 2026-11-22 (promotion_schema.VALID_PROMOTED_TOKENS), plus the
# three closing tokens Nick added that day. A token maps to a disposition; the pointer
# tokens are closed only with a resolvable pointer.
OPEN_TOKENS = ("no", "not", "false")
PARTIAL_TOKENS = ("partial",)
CLOSED_TOKENS = ("yes", "skill", "hook", "principle", "hard-rule", "framework", "script",
                 "doc", "schema")
POINTER_TOKENS = ("superseded", "merged", "parked")
PROMOTED_TOKENS = OPEN_TOKENS + PARTIAL_TOKENS + CLOSED_TOKENS + POINTER_TOKENS

DISPOSITIONS = ("open", "partial", "closed", "defect")

EXIT_PATHS = ("exit1", "exit2", "skill", "terminal")
SKILLS_HOME = Path.home() / ".claude" / "skills"

# ---------------------------------------------------------------- due
LADDER_THRESHOLD = 2               # lanes with no per-record gate
LADDER_LANES = ("friction", "lessons-2")
GATED_LANES = ("memory",)
DUE_STATES = ("due", "not_due", "no_number", "bad_reopen_at", "bad_occurrences")

# ---------------------------------------------------------------- friction
RESOLVED_PREFIX = "RESOLVED"
FRICTION_KINDS = ("friction", "block-as-designed", "harness", "false-positive-block")
COUNTING_KINDS = ("friction", "false-positive-block")
FRICTION_STATES = ("resolved", "not_counting", "due", "open", "defect_kind",
                   "bad_occurrences")

# ---------------------------------------------------------------- lessons.md Section 2
LESSONS_COLUMNS = ("Pattern", "Rule", "Occurrences", "Promoted", "Date")

# ---------------------------------------------------------------- kinds and freshness
HALF_LIFE_DAYS = {"MACHINERY": 60, "FACT": 180, "LESSON": 240}
# The 6/12 classes, with PRINCIPLE and HYBRID merged (Nick, 2026-11-22, on a calibration
# where independent full reads agreed on that boundary only 65-73% of the time).
KIND_OF_CLASS = {"MACHINERY": "MACHINERY", "FACT": "FACT", "LESSON": "LESSON",
                 "PRINCIPLE": "LESSON", "HYBRID": "LESSON"}
# Only full-read labels are a kind. Description-only labels (P1a) are not.
TRUSTED_KIND_SOURCES = frozenset({"061226 (P+H merged)", "112226 full-read re-check"})
# A session that brings 20+ distinct memory files into context is an audit; its entries
# are kept as provenance and do not re-confirm (transcript analysis, 2026-11-22).
AUDIT_BREADTH = 20
FRESHNESS_STATES = ("current", "unverified", "unknown_not_observed", "unknown_no_kind")


class Disposition(NamedTuple):
    """state: one of DISPOSITIONS. token: the leading token, lowercased ('' when none).
    defect: why the value is not valid, or None."""
    state: str
    token: str
    defect: str | None


class MemoryState(NamedTuple):
    """The full state of one memory rule. due is '-' when the rule is not in the backlog
    or is terminal. defects lists every named defect, including the disposition's."""
    disposition: Disposition
    terminal: bool
    due: str
    defects: tuple


# ---------------------------------------------------------------- promoted

def promoted_token(value) -> str:
    """The leading word of a promoted value, lowercased; '' when the value opens with no
    letter. "n/a" stays one token so it cannot pass for "n"."""
    raise NotImplementedError


def disposition(value, *, memory_dir: Path | None = None,
                repo_root: Path | None = None) -> Disposition:
    """The disposition of a promoted value (memory frontmatter or a lessons.md Promoted cell).

    Missing or empty: open. An open token: open. partial: partial. A closed token: closed.
    A pointer token: closed when its pointer is valid, else defect:
      superseded by <ref>  ref names a dated ruling (YYYY-MM-DD) or a path that exists
                           under repo_root or memory_dir (format only when neither given)
      merged into <file>   a memory rule file name; it must exist in memory_dir when given
      parked by <ref>      ref carries a dated ruling (YYYY-MM-DD)
    Any other token, or a value that opens with no token: defect. A defect stays in the
    backlog: an unknown token never reads as promoted.
    """
    raise NotImplementedError


def in_backlog(d: Disposition) -> bool:
    """open, partial and defect stay in the backlog; only closed leaves it."""
    raise NotImplementedError


def claims_gate(d: Disposition) -> bool:
    """True when the value says some enforcement LANDED (closed by a non-pointer token, or
    partial). I8 asks such a record for its evidence. A partial is in the backlog AND
    claims a gate: two predicates, on purpose."""
    raise NotImplementedError


def is_retired(d: Disposition) -> bool:
    """Closed by superseded / merged / parked: listed, never dropped."""
    raise NotImplementedError


# ---------------------------------------------------------------- due

def due_state(occurrences, reopen_at=None, *, lane: str = "memory") -> str:
    """One definition of due. Returns one of DUE_STATES.

    occurrences must be a non-negative int (or a string of digits); anything else is
    bad_occurrences. In a ladder lane the threshold is LADDER_THRESHOLD and reopen_at must
    be None. In the memory lane reopen_at is the threshold: absent or "" is no_number
    (never due); not a positive int (a bool included) is bad_reopen_at. Due iff
    occurrences >= threshold. No prose is read.
    """
    raise NotImplementedError


# ---------------------------------------------------------------- memory frontmatter

def is_terminal(fields: dict) -> bool:
    """terminal reads "yes" after memory_frontmatter.normalize (or the string "true")."""
    raise NotImplementedError


def validate_fields(fields: dict, *, keys=None, memory_dir: Path | None = None,
                    repo_root: Path | None = None,
                    skills_home: Path | None = None) -> list[str]:
    """Every defect in `fields` as "key: reason" strings; [] when valid. The validator the
    writer and the write-time hooks share.

    `keys` limits the check to those keys (the ones a write changed), so a write is not
    blocked by an older defect elsewhere in the file. Checks:
      promoted     disposition() is not defect
      exit_path    one of EXIT_PATHS
      skill        exit_path skill needs skill_file (exists under repo_root or skills_home)
                   and a non-empty skill_step
      reopen_at    a positive int when present
      terminal     "yes" or "no" when present
    """
    raise NotImplementedError


def memory_state(fields: dict, *, memory_dir: Path | None = None,
                 repo_root: Path | None = None) -> MemoryState:
    """Disposition, terminal, due and every defect of one rule, from read() fields.
    A rule in the backlog with no reopen_at is a no_number defect and never due."""
    raise NotImplementedError


# ---------------------------------------------------------------- friction

def is_resolved(fix) -> bool:
    """A friction row's Fix cell opens with RESOLVED_PREFIX (written by friction_log resolve)."""
    raise NotImplementedError


def counts(kind) -> bool:
    """True for a kind that counts toward occurrences and the ladder. ValueError on a kind
    outside FRICTION_KINDS."""
    raise NotImplementedError


def friction_state(kind, fix, occurrences) -> str:
    """One of FRICTION_STATES. Unknown kind: defect_kind. Then resolved, then not_counting
    (a block as designed or a harness event), then due / open by the ladder."""
    raise NotImplementedError


# ---------------------------------------------------------------- lessons.md Section 2

def parse_lessons_row(line: str) -> tuple[list | None, str | None]:
    """(cells, None) for a data row of exactly 5 cells; (None, None) for the header, the
    separator, or a line that is not a table row; (None, defect) for a table row with the
    wrong cell count. A literal pipe inside a cell is a wrong cell count."""
    raise NotImplementedError


def lessons_state(cells: list, *, memory_dir: Path | None = None,
                  repo_root: Path | None = None) -> tuple[Disposition, str, list[str]]:
    """(disposition of the Promoted cell, due by the ladder, defects). Defects: a bad
    Promoted cell, an Occurrences cell that is not a number, a Date cell that is not
    YYYY-MM-DD."""
    raise NotImplementedError


# ---------------------------------------------------------------- kinds and freshness

def kind_of(row: dict | None) -> str | None:
    """The kind from a memory-classes.jsonl row, or None: a row from an untrusted source,
    or a class outside KIND_OF_CLASS, has no kind."""
    raise NotImplementedError


def reconfirms(session_breadth: int) -> bool:
    """True when a context entry from a session of this breadth re-confirms a rule."""
    raise NotImplementedError


def freshness_state(kind, last_entry: _dt.date | None, window_start: _dt.date,
                    window_end: _dt.date) -> str:
    """One of FRESHNESS_STATES. H comes from the kind (HALF_LIFE_DAYS), never a parameter.

    kind None: unknown_no_kind. An entry within H of window_end: current. An entry older
    than H: unverified. No entry and window_end - window_start >= H: unverified (no
    re-confirmation observed within H; never "unused"). Otherwise unknown_not_observed.
    The window start is never returned or used as a date. last_entry outside
    [window_start, window_end]: ValueError.
    """
    raise NotImplementedError
