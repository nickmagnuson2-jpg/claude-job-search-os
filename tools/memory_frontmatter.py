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
  * The `metadata:` mapping is flattened into the top level. A key present at both levels,
    or twice in one mapping, is a defect (`duplicate_key`): the readers it replaces each
    took a different copy, and plain safe_load silently keeps the last.
  * One value type per meaning: YAML 1.1 booleans become "yes" / "no", null becomes "",
    dates become ISO strings. So `promoted: no` and `promoted: "no"` read the same.
  * `write(path, updates)` is the only sanctioned machine writer. It edits only the lines of
    the keys it sets (refusing a key whose lines carry a YAML comment, and a symlink path), re-reads the result with `read`, refuses unless every update reads
    back as written, no other field changed, and `learning_state.validate_fields` finds no
    defect in the keys that changed. It never writes `occurrences` (the state matrix: a
    count is recorded by the person who saw the fire, never by a machine).

Nothing else in tools/ may split a `---` block or scrape a frontmatter key with a regex:
tests/scripts/test_learning_state_single_source.py fails on any such site.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import NamedTuple

import yaml

# The block must open on the first line and close on a line of exactly three dashes.
_BLOCK = re.compile(r"\A---\n(.*?\n)?---(?:\n|\Z)", re.DOTALL)

NEVER_MACHINE_WRITTEN = frozenset({"occurrences"})
_COMMENT = re.compile(r"(?:^|\s)#")


class _DuplicateKey(Exception):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load, except a key repeated in one mapping is an error. Plain safe_load keeps the
    LAST value, so `promoted: no` then `promoted: yes` read closed with no defect (Codex r1
    F1, P0, 2026-11-22). SafeLoader's mapping constructor calls construct_mapping, so the
    override is reached without registering a constructor."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise _DuplicateKey(f"{key} appears twice in one mapping (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)



class Defect(NamedTuple):
    """Why a frontmatter block could not be read. `code` is one of DEFECT_CODES."""
    code: str
    detail: str


DEFECT_CODES = ("no_frontmatter", "yaml_error", "not_a_mapping", "duplicate_key")


class WriteRefused(Exception):
    """write() refused; nothing was written. `.reasons` lists every reason."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons

    def __str__(self) -> str:
        return "; ".join(self.reasons)


def split(text: str) -> tuple[str, str] | None:
    """(block, rest) where block is the text between the two `---` lines, or None when the
    file does not open with a frontmatter block."""
    m = _BLOCK.match(text)
    return (m.group(1) or "", text[m.end():]) if m else None


def normalize(value):
    """One type per meaning: bool -> "yes"/"no", None -> "", date/datetime -> ISO string.
    Lists and dicts are normalized element-wise; everything else is returned unchanged."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    return value


def read(text: str) -> tuple[dict, Defect | None]:
    """Strictly parse the frontmatter of `text`.

    Returns (fields, None) on success, with `metadata:` flattened and values normalized, or
    ({}, Defect) when there is no block, the YAML does not parse, the block is not a
    mapping, or a key appears both at the top level and under `metadata:`.
    """
    m = _BLOCK.match(text)
    if not m:
        return {}, Defect("no_frontmatter", "the file does not open with a ---/--- block")
    try:
        loaded = yaml.load(m.group(1) or "", Loader=_UniqueKeyLoader)  # noqa: S506 (SafeLoader subclass)
    except _DuplicateKey as e:
        return {}, Defect("duplicate_key", str(e))
    except yaml.YAMLError as e:
        return {}, Defect("yaml_error", " ".join(str(e).split())[:300])
    if not isinstance(loaded, dict):
        return {}, Defect("not_a_mapping", f"the block is {type(loaded).__name__}, not key: value")
    fields = {k: v for k, v in loaded.items() if k != "metadata" or not isinstance(v, dict)}
    meta = loaded.get("metadata")
    if isinstance(meta, dict):
        both = sorted(str(k) for k in meta if k in fields)
        if both:
            return {}, Defect("duplicate_key", "at the top level and under metadata: " + ", ".join(both))
        fields.update(meta)
    return {k: normalize(v) for k, v in fields.items()}, None


def read_file(path: Path) -> tuple[dict, Defect | None]:
    """read() over a file's text (UTF-8)."""
    return read(Path(path).read_text(encoding="utf-8"))


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
    fields, defect = read(new_text)
    if defect:
        return [f"frontmatter: {defect.code}: {defect.detail}"]
    keys = None
    if old_text is not None:
        old, _ = read(old_text)  # unreadable old -> {} -> every key differs -> all checked
        keys = {k for k in set(fields) | set(old) if fields.get(k) != old.get(k)}
    return _validate(fields, keys, memory_dir, repo_root, skills_home)


def _validate(fields, keys, memory_dir, repo_root, skills_home) -> list[str]:
    import learning_state  # same folder; imported here so neither module loads the other at import
    return learning_state.validate_fields(fields, keys=keys, memory_dir=memory_dir,
                                          repo_root=repo_root, skills_home=skills_home)


def _key_spans(block: str) -> tuple[dict, int | None, int]:
    """{key: (first line, end line, indent)} for top-level keys and keys under metadata:,
    the line after metadata's last child (or None), and metadata's child indent."""
    lines = block.split("\n")
    spans, meta_end, child_indent, in_meta = {}, None, 2, False
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^( *)([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
        indent = len(m.group(1)) if m else None
        if m and indent == 0:
            in_meta = m.group(2) == "metadata" and not line.split(":", 1)[1].strip()
            if in_meta:  # the block ends in a newline, so a line i + 1 always exists
                nxt = re.match(r"^( +)\S", lines[i + 1])
                child_indent = len(nxt.group(1)) if nxt else 2
        if m and indent == 0 and in_meta:
            i += 1  # its children are keys of their own, not continuation lines
            continue
        if m and (indent == 0 or (in_meta and indent == child_indent)):
            j = i + 1
            while j < len(lines) and (not lines[j].strip()
                                      or len(lines[j]) - len(lines[j].lstrip(" ")) > indent):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            spans[m.group(2)] = (i, j, indent)
            if in_meta and indent == child_indent:
                meta_end = j
            i = j
            continue
        i += 1
    return spans, meta_end, child_indent


def _render(key: str, value, indent: int) -> list[str]:
    dumped = yaml.safe_dump({key: value}, default_flow_style=False, allow_unicode=True,
                            width=10 ** 9, sort_keys=False)
    return [" " * indent + x for x in dumped.rstrip("\n").split("\n")]


def _set_keys(text: str, updates: dict) -> str:
    """text with each key in updates set, every other line unchanged."""
    m = _BLOCK.match(text)
    lines = (m.group(1) or "").split("\n")
    for key, value in updates.items():
        spans, meta_end, child_indent = _key_spans("\n".join(lines))
        if key in spans:
            a, b, indent = spans[key]
            if any(_COMMENT.search(line) for line in lines[a:b]):
                # A comment is invisible to read(), so the consistency check cannot see it
                # go; refuse rather than delete it (Codex r2 F1, P0). Over-refuses a "#"
                # inside a quoted value, which costs a hand edit, never data.
                raise WriteRefused([f"{key}: its lines carry a YAML comment that a rewrite "
                                    "would delete; edit this key by hand"])
            lines[a:b] = _render(key, value, indent)
        elif meta_end is not None:
            lines[meta_end:meta_end] = _render(key, value, child_indent)
        else:
            at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
            lines[at:at] = _render(key, value, 0)
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


def _refuse_unless_consistent(old: dict, new_text: str, updates: dict) -> dict:
    new, defect = read(new_text)
    if defect:
        raise WriteRefused([f"the result does not re-read: {defect.code}: {defect.detail}"])
    bad = [f"{k}: reads back as {new.get(k)!r}, not {v!r}" for k, v in updates.items()
           if new.get(k) != normalize(v)]
    bad += [f"{k}: changed although not updated" for k in set(old) | set(new)
            if k not in updates and old.get(k) != new.get(k)]
    if bad:
        raise WriteRefused(bad)
    return new


def _atomic_write(path: Path, text: str) -> None:
    """tmp + os.replace, keeping the file's permission bits. Refuses a symlink: replacing
    it would swap the link for a regular file and leave the target unchanged (Codex r2 F2) (mkstemp creates 0600; Codex r1
    F4). Owner and extended attributes are not copied: the writer runs as the file's owner."""
    path = Path(path)
    if path.is_symlink():
        raise WriteRefused([f"{path.name} is a symlink; write the file it points to"])
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


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
    if os.environ.get("CLAUDECODE") != "1":
        raise WriteRefused(["record_fire runs only inside a Claude Code session (CLAUDECODE=1); "
                            "a detector or scheduled job never records a fire"])
    evidence = (evidence or "").strip()
    if not evidence or not re.search(r"\b\d{4}-\d{2}-\d{2}\b", evidence):
        raise WriteRefused(["evidence must say what fired and carry its YYYY-MM-DD date"])
    text = Path(path).read_text(encoding="utf-8")
    fields, defect = read(text)
    if defect:
        raise WriteRefused([f"frontmatter: {defect.code}: {defect.detail}"])
    occ = fields.get("occurrences")
    if type(occ) is not int or occ < 0:
        raise WriteRefused([f"occurrences: {occ!r} is not a count to add one to"])
    n = occ + 1
    new_text = _set_keys(text, {"occurrences": n})
    _refuse_unless_consistent(fields, new_text, {"occurrences": n})
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += f"- Fire {n} recorded {(today or _dt.date.today()).isoformat()}: {evidence}\n"
    _atomic_write(path, new_text)
    return n


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
    `memory_dir` (default: the file's own folder) and `repo_root` are passed to the
    validator to resolve pointers and paths.
    """
    reasons = [f"{k}: never machine-written (use record_fire for a witnessed fire)"
               for k in updates if k in NEVER_MACHINE_WRITTEN]
    reasons += [f"{k}: {v!r} is not a string or an integer" for k, v in updates.items()
                if isinstance(v, bool) or not isinstance(v, (str, int))]
    if not updates:
        reasons.append("nothing to write")
    if reasons:
        raise WriteRefused(reasons)
    text = Path(path).read_text(encoding="utf-8")
    fields, defect = read(text)
    if defect:
        raise WriteRefused([f"frontmatter: {defect.code}: {defect.detail}"])
    new_text = _set_keys(text, updates)
    new = _refuse_unless_consistent(fields, new_text, updates)
    folder = Path(memory_dir) if memory_dir is not None else Path(path).parent
    invalid = _validate(new, set(updates), folder, repo_root, skills_home)
    if invalid:
        raise WriteRefused(invalid)
    _atomic_write(path, new_text)
    return new
