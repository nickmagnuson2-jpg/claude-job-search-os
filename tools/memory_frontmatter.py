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
import difflib
import hashlib
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


def _comment_lines(block: str) -> dict[int, int]:
    """{line index: index in block of the line's first comment "#"}, by PyYAML's own
    scanner: a "#" outside every token is a comment, one inside a token is value text. A
    regex cannot tell "PR #61" inside quotes from a comment, and misses a comment glued to
    a quoted value ("no"# waits); both were found on 2026-11-22 (org 72, Grok r2)."""
    spans = [(t.start_mark.index, t.end_mark.index)
             for t in yaml.scan(block, Loader=yaml.SafeLoader)]
    out: dict[int, int] = {}
    for i, ch in enumerate(block):
        if ch == "#" and not any(a <= i < b for a, b in spans):
            out.setdefault(block.count("\n", 0, i), i)
    return out


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
    the line after metadata's last child (or None), and metadata's child indent. A key
    written twice maps to its last span (repair() reads every span via _all_key_spans)."""
    found, meta_end, child_indent = _all_key_spans(block)
    return {k: (a, b, ind) for k, a, b, ind in found}, meta_end, child_indent


def _all_key_spans(block: str) -> tuple[list, int | None, int]:
    """Every (key, first line, end line, indent), in order, repeats included; found by line
    structure alone, so it also works on a block the strict reader refuses."""
    lines = block.split("\n")
    spans, meta_end, child_indent, in_meta = [], None, 2, False
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^( *)([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
        indent = len(m.group(1)) if m else None
        if m and indent == 0:
            in_meta = m.group(2) == "metadata" and not line.split(":", 1)[1].strip()
            if in_meta:
                # the first child line, past blank and comment lines (Codex learning-repair
                # r1 F3: a blank line after metadata: made a valid 4-space block unwritable)
                k = i + 1
                while k < len(lines) and (not lines[k].strip()
                                          or lines[k].lstrip().startswith("#")):
                    k += 1
                nxt = re.match(r"^( +)\S", lines[k]) if k < len(lines) else None
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
            spans.append((m.group(2), i, j, indent))
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
        block = "\n".join(lines)
        spans, meta_end, child_indent = _key_spans(block)
        commented = _comment_lines(block)  # per key: an earlier replacement shifts lines
        if key in spans:
            a, b, indent = spans[key]
            if set(commented) & set(range(a, b)):
                # A comment is invisible to read(), so the consistency check cannot see it
                # go; refuse rather than delete it (Codex r2 F1, P0).
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
        raise WriteRefused([f"occurrences: {occ!r} is not a non-negative integer to add one to"])
    n = occ + 1
    new_text = _set_keys(text, {"occurrences": n})
    _refuse_unless_consistent(fields, new_text, {"occurrences": n})
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += f"- Fire {n} recorded {(today or _dt.date.today()).isoformat()}: {evidence}\n"
    _atomic_write(path, new_text)
    return n


def repair(path: Path, *, expect_sha256: str, new_block: str, intended: dict,
           body_note: str | None = None, memory_dir: Path | None = None,
           repo_root: Path | None = None, skills_home: Path | None = None) -> dict:
    """The one sanctioned path for a STRUCTURAL frontmatter repair (org 72, 2026-11-22: Nick
    ruled nothing is hand-edited). write() cannot do these: it cannot read a malformed
    block, and it refuses a line that carries a comment. Returns the new fields.

    `new_block` is the whole text between the two --- lines. `intended` names every key the
    repair touches, with the value it must read back as. The body is kept byte for byte;
    `body_note`, when given, is one dated line inserted directly after the closing ---.

    Refuses (WriteRefused, file untouched) when: CLAUDECODE is not "1"; the path is a
    symlink; the file's sha256 is not expect_sha256; the file has no ---/--- block; the new
    block fails the strict read; an intended key reads back different; a line outside the
    intended keys' spans is changed, added or removed (spans come from line structure, so
    an unparseable old block still works, and every key not named keeps its exact bytes);
    `occurrences` is intended (record_fire is its only writer); learning_state finds a
    defect in an intended key; a removed line's YAML comment is not carried in body_note;
    or body_note is blank, undated, multi-line, or already in the file.
    """
    if os.environ.get("CLAUDECODE") != "1":
        raise WriteRefused(["repair runs only inside a Claude Code session (CLAUDECODE=1)"])
    path = Path(path)
    data = path.read_bytes()  # a symlink is refused by _atomic_write, before any write
    actual = hashlib.sha256(data).hexdigest()
    if actual != expect_sha256:
        raise WriteRefused([f"sha256 of {path.name} is {actual}, not the proposal's "
                            f"{expect_sha256}: the file changed since the proposal"])
    text = data.decode("utf-8")
    m = _BLOCK.match(text)
    if not m or m.group(1) is None:
        raise WriteRefused(["frontmatter: the file has no ---/--- block to repair"])
    old_block, closing, rest = m.group(1), text[m.end(1):m.end()], text[m.end():]
    new_block = new_block if new_block.endswith("\n") else new_block + "\n"
    reasons = [f"{k}: never machine-written (use record_fire)" for k in intended
               if k in NEVER_MACHINE_WRITTEN]
    new, defect = read("---\n" + new_block + "---\n")
    if defect:
        raise WriteRefused(reasons + [f"new block: {defect.code}: {defect.detail}"])
    reasons += [f"{k}: reads back as {new.get(k)!r}, not {v!r}" for k, v in intended.items()
                if new.get(k) != normalize(v)]
    reasons += _outside_intended(old_block, new_block, set(intended))
    reasons += _absorbed_keys(old_block, set(intended))
    reasons += _dropped_comments(old_block, new_block, body_note)
    note = None if body_note is None else body_note.strip()
    if note is not None:
        if not note:
            reasons.append("body_note is blank")
        elif "\n" in note:
            reasons.append("body_note must be one line")
        elif not re.search(r"\b\d{4}-\d{2}-\d{2}\b", note):
            reasons.append("body_note carries no YYYY-MM-DD date")
        elif note in text:
            reasons.append("body_note is already in the file")
    folder = Path(memory_dir) if memory_dir is not None else path.parent
    reasons += _validate(new, set(intended), folder, repo_root, skills_home)
    if reasons:
        raise WriteRefused(reasons)
    new_text = "---\n" + new_block + closing + (f"\n{note}\n" if note else "") + rest
    _atomic_write(path, new_text)
    return new


def _outside_intended(old_block: str, new_block: str, keys: set) -> list[str]:
    """Every changed, added or removed line that lies outside the spans of `keys` (in the
    old block for what left, in the new block for what arrived)."""
    old_lines, new_lines = old_block.split("\n"), new_block.split("\n")
    allowed_old = {ln for k, a, b, _ in _all_key_spans(old_block)[0] if k in keys
                   for ln in range(a, b)}
    allowed_new = {ln for k, a, b, _ in _all_key_spans(new_block)[0] if k in keys
                   for ln in range(a, b)}
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines,
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        stray = ([f"old line {i + 1}" for i in range(i1, i2) if i not in allowed_old]
                 + [f"new line {j + 1}" for j in range(j1, j2) if j not in allowed_new])
        if stray:
            out.append(f"a change outside the intended keys: {', '.join(stray)}")
    return out


_BLOCK_SCALAR_HEADER = re.compile(r":\s*[|>][-+0-9]*\s*(?:#.*)?$")
_KEY_SHAPED = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:(?:\s|$)")


def _absorbed_keys(old_block: str, keys: set) -> list[str]:
    """A key-shaped line inside an intended key's span, in an old block that may not parse,
    is most likely another key that malformed indentation pulled in; replacing the span
    would delete it (Codex learning-repair r1 F1, P0). Refused unless the span opens a
    block scalar (its lines are text) or that key is itself intended."""
    lines = old_block.split("\n")
    out = []
    for k, a, b, _ in _all_key_spans(old_block)[0]:
        if k not in keys or _BLOCK_SCALAR_HEADER.search(lines[a]):
            continue
        for ln in range(a + 1, b):
            m = _KEY_SHAPED.match(lines[ln])
            if m and m.group(1) not in keys:
                out.append(f"old line {ln + 1} looks like key {m.group(1)!r} inside {k}'s "
                           "span; name it in intended or keep its line outside the span")
    return out


def _dropped_comments(old_block: str, new_block: str, body_note: str | None) -> list[str]:
    """A YAML comment on a line the repair removes must travel verbatim in body_note."""
    kept = set(new_block.split("\n"))
    lines = old_block.split("\n")
    note = body_note or ""
    return [f"old line {ln + 1}: its YAML comment {text!r} would be dropped; carry it in "
            f"body_note after a colon, at the end or before a ';'"
            for ln, text in _old_comments(old_block).items()
            if lines[ln] not in kept and text and not _carries(note, text)]


def _carries(note: str, text: str) -> bool:
    """The note carries a comment only as ': <text>' ending the note or followed by ';'. A
    bare substring test let "no" or "2026" be satisfied by the note's own dated
    boilerplate (Grok learning-repair r1 F1)."""
    return re.search(r":\s*" + re.escape(text) + r"\s*(?:;|$)", note) is not None


def _old_comments(block: str) -> dict[int, str]:
    """{line index: comment text}. For a block that reads strictly, PyYAML's scanner decides.
    For one that does not, the scanner's view no longer matches what the author wrote (an
    unterminated quote swallows the next lines, and a "#" in them becomes scalar text), so
    every "#" counts: over-detection only asks for a note (Codex learning-repair r1 F2)."""
    if read("---\n" + block + "---\n")[1] is None:
        return {ln: block[i + 1:].split("\n", 1)[0].strip()
                for ln, i in _comment_lines(block).items()}
    out = {}
    for ln, line in enumerate(block.split("\n")):
        m = re.search(r"#(.*)$", line)
        if m:
            out[ln] = m.group(1).strip()
    return out


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
