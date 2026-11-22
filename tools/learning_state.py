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
# not-friction: rows retagged "misattributed: not friction" on 2026-11-22 (org 68).
FRICTION_KINDS = ("friction", "block-as-designed", "harness", "false-positive-block",
                  "not-friction")
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
    m = re.match(r"\s*([A-Za-z][A-Za-z/-]*)", "" if value is None else str(value))
    return m.group(1).lower().rstrip("-") if m else ""


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
    text = "" if value is None else str(value).strip()
    if not text:
        return Disposition("open", "", None)
    tok = promoted_token(text)
    if tok in OPEN_TOKENS:
        return Disposition("open", tok, None)
    if tok in PARTIAL_TOKENS:
        return Disposition("partial", tok, None)
    if tok in CLOSED_TOKENS:
        return Disposition("closed", tok, None)
    if tok in POINTER_TOKENS:
        why = _pointer_defect(tok, text, memory_dir, repo_root)
        return Disposition("defect", tok, why) if why else Disposition("closed", tok, None)
    return Disposition("defect", tok, f"token {tok!r} in {text[:40]!r} is not one of "
                                      f"{'|'.join(PROMOTED_TOKENS)}")


_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_PATH = re.compile(r"[\w.~-]*(?:/[\w.~-]+)*\.(?:md|py|sh|yaml|yml|json|jsonl|js|txt)\b")
_POINTER = {"superseded": re.compile(r"\bby\s+(\S.*)$", re.I | re.S),
            "merged": re.compile(r"\binto\s+(\S+)", re.I),
            "parked": re.compile(r"\bby\s+(\S.*)$", re.I | re.S)}


def _pointer_defect(tok: str, text: str, memory_dir: Path | None,
                    repo_root: Path | None) -> str | None:
    """None when the pointer token carries a valid pointer, else the reason."""
    m = _POINTER[tok].search(text[len(tok):])
    if not m:
        word = "into" if tok == "merged" else "by"
        return f"{tok} needs a pointer: '{tok} {word} <ref>'"
    ref = m.group(1).strip()
    if tok == "merged":
        name = ref.rstrip(".,;)")
        name = name if name.endswith(".md") else name + ".md"
        if not re.fullmatch(r"[a-z0-9_]+\.md", name):
            return f"merged pointer {ref!r} is not a memory file name"
        if memory_dir is not None and not (Path(memory_dir) / name).is_file():
            return f"merged pointer {name} does not exist in the memory folder"
        return None
    if tok == "parked":
        return None if _DATE.search(ref) else f"parked pointer {ref!r} carries no YYYY-MM-DD ruling date"
    if _DATE.search(ref):
        return None
    pm = _PATH.search(ref)
    if not pm:
        return f"superseded pointer {ref!r} names neither a dated ruling nor a path"
    roots = [Path(r).resolve() for r in (repo_root, memory_dir) if r is not None]
    if roots and not any(_inside(r, pm.group(0)) for r in roots):
        return f"superseded pointer {pm.group(0)} does not exist under the repo or memory folder"
    return None


def _inside(root: Path, rel: str) -> bool:
    """rel exists and, with every .. and symlink resolved, stays under root (Codex r1 F3)."""
    target = (root / rel).resolve()
    return target.exists() and target.is_relative_to(root)


def in_backlog(d: Disposition) -> bool:
    """open, partial and defect stay in the backlog; only closed leaves it."""
    return d.state != "closed"


def claims_gate(d: Disposition) -> bool:
    """True when the value says some enforcement LANDED (closed by a non-pointer token, or
    partial). I8 asks such a record for its evidence. A partial is in the backlog AND
    claims a gate: two predicates, on purpose."""
    return d.state == "partial" or (d.state == "closed" and d.token not in POINTER_TOKENS)


def is_retired(d: Disposition) -> bool:
    """Closed by superseded / merged / parked: listed, never dropped."""
    return d.state == "closed" and d.token in POINTER_TOKENS


# ---------------------------------------------------------------- due

def due_state(occurrences, reopen_at=None, *, lane: str = "memory") -> str:
    """One definition of due. Returns one of DUE_STATES.

    occurrences must be a non-negative int (or a string of digits); anything else is
    bad_occurrences. In a ladder lane the threshold is LADDER_THRESHOLD and reopen_at must
    be None. In the memory lane reopen_at is the threshold: absent or "" is no_number
    (never due); not a positive int (a bool included) is bad_reopen_at. Due iff
    occurrences >= threshold. No prose is read.
    """
    if lane in LADDER_LANES:
        if reopen_at is not None:
            raise ValueError(f"lane {lane!r} has no per-record gate; reopen_at must be None")
        threshold = LADDER_THRESHOLD
    elif lane in GATED_LANES:
        if reopen_at is None or reopen_at == "":
            threshold = None
        elif type(reopen_at) is int and reopen_at > 0:
            threshold = reopen_at
        else:
            return "bad_reopen_at"
    else:
        raise ValueError(f"unknown lane {lane!r}")
    occ = _count(occurrences)
    if occ is None:
        return "bad_occurrences"
    if threshold is None:
        return "no_number"
    return "due" if occ >= threshold else "not_due"


def _count(value) -> int | None:
    """A non-negative int from an int or a string of digits; None otherwise (bool included)."""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
        return int(value)
    return None


# ---------------------------------------------------------------- memory frontmatter

def is_terminal(fields: dict) -> bool:
    """terminal reads "yes" after memory_frontmatter.normalize (or the string "true")."""
    return str(fields.get("terminal", "")).strip().lower() in ("yes", "true")


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
    check = set(fields) if keys is None else set(keys)
    out = []
    if "promoted" in check:
        d = disposition(fields.get("promoted"), memory_dir=memory_dir, repo_root=repo_root)
        if d.state == "defect":
            out.append(f"promoted: {d.defect}")
    exit_path = fields.get("exit_path", "")
    if "exit_path" in check and exit_path not in ("",) + EXIT_PATHS:
        out.append(f"exit_path: {exit_path!r} is not one of {'|'.join(EXIT_PATHS)}")
    if exit_path == "skill" and check & {"exit_path", "skill_file", "skill_step"}:
        out.extend(_skill_defects(fields, repo_root, skills_home))
    if "reopen_at" in check and "reopen_at" in fields:
        r = fields["reopen_at"]
        if not (type(r) is int and r > 0):
            out.append(f"reopen_at: {r!r} is not a positive integer")
    if "terminal" in check and "terminal" in fields and fields["terminal"] not in ("yes", "no"):
        out.append(f"terminal: {fields['terminal']!r} is not yes or no")
    return out


def _skill_defects(fields: dict, repo_root, skills_home) -> list[str]:
    out = []
    rel = str(fields.get("skill_file", "") or "").strip()
    if not rel:
        out.append("skill_file: exit_path skill needs the skill or framework file")
    else:
        roots = [Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1],
                 Path(skills_home) if skills_home is not None else SKILLS_HOME]
        if not any((r / rel).is_file() for r in roots):
            out.append(f"skill_file: {rel} exists neither in the repo nor under the skills folder")
    if not str(fields.get("skill_step", "") or "").strip():
        out.append("skill_step: exit_path skill needs the step that carries the rule")
    return out


def memory_state(fields: dict, *, memory_dir: Path | None = None,
                 repo_root: Path | None = None) -> MemoryState:
    """Disposition, terminal, due and every defect of one rule, from read() fields.
    A rule in the backlog with no reopen_at is a no_number defect and never due."""
    d = disposition(fields.get("promoted"), memory_dir=memory_dir, repo_root=repo_root)
    terminal = is_terminal(fields)
    defects = []
    if d.state == "defect":
        defects.append(f"promoted: {d.defect}")
    due = "-"
    if in_backlog(d) and not terminal:
        due = due_state(fields.get("occurrences", 0), fields.get("reopen_at"))
        if due == "no_number":
            defects.append("reopen_at: missing; the rule can never be due")
        elif due == "bad_reopen_at":
            defects.append(f"reopen_at: {fields.get('reopen_at')!r} is not a positive integer")
        elif due == "bad_occurrences":
            defects.append(f"occurrences: {fields.get('occurrences')!r} is not a count")
    return MemoryState(d, terminal, due, tuple(defects))


# ---------------------------------------------------------------- friction

def is_resolved(fix) -> bool:
    """A friction row's Fix cell opens with RESOLVED_PREFIX (written by friction_log resolve)."""
    return ("" if fix is None else str(fix)).strip().startswith(RESOLVED_PREFIX)


def counts(kind) -> bool:
    """True for a kind that counts toward occurrences and the ladder. ValueError on a kind
    outside FRICTION_KINDS."""
    if kind not in FRICTION_KINDS:
        raise ValueError(f"friction kind {kind!r} is not one of {'|'.join(FRICTION_KINDS)}")
    return kind in COUNTING_KINDS


def friction_state(kind, fix, occurrences) -> str:
    """One of FRICTION_STATES. Unknown kind: defect_kind. Then resolved, then not_counting
    (a block as designed or a harness event), then due / open by the ladder."""
    if kind not in FRICTION_KINDS:
        return "defect_kind"
    if is_resolved(fix):
        return "resolved"
    if not counts(kind):
        return "not_counting"
    state = due_state(occurrences, lane="friction")
    return {"due": "due", "not_due": "open"}.get(state, state)


# ---------------------------------------------------------------- lessons.md Section 2

def parse_lessons_row(line: str) -> tuple[list | None, str | None]:
    """(cells, None) for a data row of exactly 5 cells; (None, None) for the header, the
    separator, or a line that does not open with a pipe; (None, defect) for a line that
    opens with a pipe and cannot be read as 5 cells: a wrong cell count (a literal pipe
    inside a cell) or text after the closing pipe. Never a silent drop: rows 224-228 were
    dropped that way until 2026-11-22."""
    s = line.strip()
    if not s.startswith("|"):
        return None, None
    if not s.endswith("|") or len(s) == 1:
        return None, "text after the closing pipe: the row cannot be read as cells"
    cells = [c.strip() for c in s[1:-1].split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
        return None, None
    if cells and cells[0] == LESSONS_COLUMNS[0]:
        return None, None
    if len(cells) != len(LESSONS_COLUMNS):
        return None, (f"cell count {len(cells)}, expected {len(LESSONS_COLUMNS)} "
                      f"({' | '.join(LESSONS_COLUMNS)}); a literal pipe in a cell splits it")
    return cells, None


def lessons_state(cells: list, *, memory_dir: Path | None = None,
                  repo_root: Path | None = None) -> tuple[Disposition, str, list[str]]:
    """(disposition of the Promoted cell, due by the ladder, defects). Defects: a bad
    Promoted cell, an Occurrences cell that is not a number, a Date cell that does not open
    with YYYY-MM-DD (a later fire may follow it: "2026-07-24 (6th: 2026-09-22)")."""
    _, _, occ, promoted, date = cells
    d = disposition(promoted, memory_dir=memory_dir, repo_root=repo_root)
    defects = []
    if d.state == "defect":
        defects.append(f"promoted: {d.defect}")
    due = due_state(occ, lane="lessons-2") if in_backlog(d) else "-"
    if due == "bad_occurrences":
        defects.append(f"occurrences: {occ!r} is not a count")
    if not re.match(r"\d{4}-\d{2}-\d{2}\b", date):
        defects.append(f"date: {date!r} does not open with YYYY-MM-DD")
    return d, due, defects


# ---------------------------------------------------------------- kinds and freshness

def kind_of(row: dict | None) -> str | None:
    """The kind from a memory-classes.jsonl row, or None: a row from an untrusted source,
    or a class outside KIND_OF_CLASS, has no kind."""
    if not row or row.get("source") not in TRUSTED_KIND_SOURCES:
        return None
    return KIND_OF_CLASS.get(str(row.get("kind", "")).upper())


def reconfirms(session_breadth: int) -> bool:
    """True when a context entry from a session of this breadth re-confirms a rule."""
    return session_breadth < AUDIT_BREADTH


def freshness_state(kind, last_entry: _dt.date | None, window_start: _dt.date,
                    window_end: _dt.date) -> str:
    """One of FRESHNESS_STATES. H comes from the kind (HALF_LIFE_DAYS), never a parameter.

    kind None: unknown_no_kind. An entry within H of window_end: current. An entry older
    than H: unverified. No entry and window_end - window_start >= H: unverified (no
    re-confirmation observed within H; never "unused"). Otherwise unknown_not_observed.
    The window start is never returned or used as a date. last_entry outside
    [window_start, window_end]: ValueError.
    """
    if kind is None:
        return "unknown_no_kind"
    if kind not in HALF_LIFE_DAYS:
        raise ValueError(f"kind {kind!r} is not one of {'|'.join(HALF_LIFE_DAYS)}")
    h = HALF_LIFE_DAYS[kind]
    if last_entry is not None:
        if not window_start <= last_entry <= window_end:
            raise ValueError(f"last entry {last_entry} is outside the window "
                             f"{window_start}..{window_end}")
        return "current" if (window_end - last_entry).days <= h else "unverified"
    return "unverified" if (window_end - window_start).days >= h else "unknown_not_observed"
