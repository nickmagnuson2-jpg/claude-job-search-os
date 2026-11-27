#!/usr/bin/env python3
"""facts.py -- one dated record per fact about the owner, one resolve(), one sweep.

KB pilot builds 1 and 2 (output/kb-research/112626-KB-S5-vs-S5B-comparison.md section 3).

WHY
---
A ruling from the owner updates one store; the copies are never swept, so a stale title
reaches a CV or an outreach email. The measured cases: 4 of 29 multi-store facts had a
disagreeing copy, each left behind by a ruling, and one stale form reached a sent email.
This tool gives each keyed fact ONE record at its home, a resolve() every reader imports,
and a sweep that lists the copies and artifacts still stating an old or banned form.

THE RECORD (one line, opened and closed on that line, under the field it describes)
-----------------------------------------------------------------------------------
    <!-- fact: <key> | as_of: YYYY-MM-DD | source: nick "<words>" | forms: cv=<v>; prose=<v>
         | rule: <text> | banned: "<form>"; "<form>" generic
         | supersedes: "<old>" until cv YYYY-MM-DD, prose YYYY-MM-DD, source nick -->

(shown wrapped here; a real record is a single line). Parts are separated by " | ".

- source is one of three disjoint kinds: `nick "<verbatim words>"`, `nick_file <path>`
  (copied from a file the owner wrote), `agent [<detail>]` (an extraction or inference).
- forms: one or more `audience=value`, separated by `;` before the next `audience=`.
- banned: forms that were never correct. `generic` after a form marks one other people
  also hold (a title contacts have), which counts only on a line anchored to the owner.
- supersedes: forms that were correct until a date, per audience or for all (`until DATE`).

RESOLUTION (the owner's ruling, 2026-11-26 14:09: "Your words first, then date")
-------------------------------------------------------------------------------
Highest source rank wins (nick > nick_file > agent); within a rank, the latest as_of. An
agent value never displaces an owner value, however new. Two records of the same rank and
date with different values are a conflict: get refuses (exit 2) rather than pick one.

HOMES are read at call time, never cached: data/projects/*.md, data/profile.md and
data/goals.md. `--records <file>` reads records from one other file instead (tests, and a
real-data run before any record is written into a home).

EXIT CODES (absence is never printed as a value)
------------------------------------------------
  get:      0 value printed | 3 NO RECORD (not evidence the fact is false)
            | 2 error: a malformed or conflicting record, or a home unreadable or sealed
  validate: 0 every record parses and none conflicts | 1 a malformed or conflicting record
            (listed in its JSON) | 2 a home or the records file is unreadable or sealed
  sweep:    0 no stale form | 1 stale forms listed | 2 a scope is empty, unreadable or
            sealed, no records, or a malformed or conflicting record

Every real key and value lives in gitignored files; this file holds only generic code.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_corrections import COMMENT_RE  # noqa: E402  (one comment pattern, by import)

SOURCE_RANK = {"agent": 0, "nick_file": 1, "nick": 2}
HOME_GLOBS = ("data/projects/*.md",)
HOME_FILES = ("data/profile.md", "data/goals.md")

RECORD_START = re.compile(r"<!--\s*fact\s*:", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AUD_SPLIT = re.compile(r";\s*(?=[A-Za-z_]+\s*=)")
QUOTED_ITEM = re.compile(r'"([^"]+)"([^"]*)')
OVERRIDE_RE = re.compile(r"<!--\s*fact-ok\s*:\s*(.*?)\s*-->", re.I)


class Unreadable(Exception):
    """A home or scope cannot be read. Callers fail closed (exit 2)."""


class Malformed(Exception):
    """A record line does not parse."""


class Conflict(Exception):
    """Two records of the same source rank and date disagree."""


@dataclass
class Superseded:
    text: str
    until: dict            # audience -> date; "*" means every audience
    source: str


@dataclass
class Record:
    key: str
    as_of: date
    source_kind: str
    source_detail: str
    forms: dict
    rule: str = ""
    banned: list = field(default_factory=list)       # [(text, generic: bool)]
    supersedes: list = field(default_factory=list)   # [Superseded]
    path: str = ""
    line: int = 0

    def value(self, audience: str | None = None) -> str | None:
        if audience:
            return self.forms.get(audience)
        return next(iter(self.forms.values()), None)


# ---------------------------------------------------------------- parsing

def _date(s: str, what: str) -> date:
    s = s.strip()
    if not DATE_RE.match(s):
        raise Malformed(f"{what}: not YYYY-MM-DD: {s!r}")
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise Malformed(f"{what}: {e}") from e


def _source(s: str) -> tuple[str, str]:
    s = s.strip()
    kind, _, rest = s.partition(" ")
    rest = rest.strip()
    if kind == "nick":
        m = re.fullmatch(r'"(.+)"', rest)
        if not m or not m.group(1).strip():
            raise Malformed('source nick needs the owner\'s words in quotes')
        return kind, m.group(1)
    if kind == "nick_file":
        if not rest:
            raise Malformed("source nick_file needs the path of the owner's file")
        return kind, rest
    if kind == "agent":
        return kind, rest
    raise Malformed(f"source must be nick, nick_file or agent, not {kind!r}")


def _forms(s: str) -> dict:
    out = {}
    for part in AUD_SPLIT.split(s.strip()):
        aud, eq, val = part.partition("=")
        if not eq or not aud.strip() or not val.strip():
            raise Malformed(f"form is not audience=value: {part!r}")
        out[aud.strip()] = val.strip().rstrip(";").strip()
    if not out:
        raise Malformed("forms is empty")
    return out


def _banned(s: str) -> list:
    items = QUOTED_ITEM.findall(s)
    if not items:
        raise Malformed(f'banned needs quoted forms: {s!r}')
    return [(text, "generic" in attrs.lower()) for text, attrs in items]


def _supersedes(s: str) -> list:
    items = QUOTED_ITEM.findall(s)
    if not items:
        raise Malformed(f'supersedes needs quoted forms: {s!r}')
    out = []
    for text, attrs in items:
        attrs = attrs.strip().lstrip(";").strip()
        m = re.match(r"until\s+(.*?)(?:,\s*source\s+(\w+))?\s*;?\s*$", attrs)
        if not m:
            raise Malformed(f"supersedes {text!r} needs 'until <date>'")
        until = {}
        for chunk in m.group(1).split(","):
            bits = chunk.split()
            if len(bits) == 1:
                until["*"] = _date(bits[0], f"until for {text!r}")
            elif len(bits) == 2:
                until[bits[0]] = _date(bits[1], f"until for {text!r}")
            else:
                raise Malformed(f"until for {text!r}: {chunk!r}")
        src = m.group(2) or ""
        if src and src not in SOURCE_RANK:
            raise Malformed(f"supersedes {text!r}: unknown source {src!r}")
        out.append(Superseded(text, until, src))
    return out


def parse_record(body: str, path: str = "", line: int = 0) -> Record:
    """Parse the text inside one fact comment (everything after `fact:`)."""
    parts = [p.strip() for p in body.split(" | ")]
    key = parts[0].strip()
    if not key or " " in key:
        raise Malformed(f"bad key {key!r}")
    fields = {}
    for p in parts[1:]:
        name, colon, val = p.partition(":")
        name = name.strip().lower()
        if not colon or name not in {"as_of", "source", "forms", "rule", "banned", "supersedes"}:
            raise Malformed(f"unknown part {p[:40]!r}")
        if name in fields:
            raise Malformed(f"part {name!r} given twice")
        fields[name] = val.strip()
    for need in ("as_of", "source", "forms"):
        if need not in fields:
            raise Malformed(f"record {key!r} has no {need}")
    kind, detail = _source(fields["source"])
    return Record(
        key=key, as_of=_date(fields["as_of"], "as_of"), source_kind=kind,
        source_detail=detail, forms=_forms(fields["forms"]), rule=fields.get("rule", ""),
        banned=_banned(fields["banned"]) if fields.get("banned") else [],
        supersedes=_supersedes(fields["supersedes"]) if fields.get("supersedes") else [],
        path=path, line=line)


def scan_text(text: str, path: str = "") -> tuple[list, list]:
    """(records, errors) for every fact comment in a text. A record must open and close on
    one line: a multi-line one would be skipped by the CV field parser, so it is an error."""
    records, errors = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        if not RECORD_START.search(raw):
            continue
        comments = [m.group(1) for m in COMMENT_RE.finditer(raw)
                    if RECORD_START.match("<!--" + m.group(1))]
        # Every record start on the line must close on it: a valid record followed by an
        # unclosed one is still an error (Codex r1 F2).
        if len(comments) < len(RECORD_START.findall(raw)):
            errors.append(f"{path}:{i}: fact record does not close on its line")
            continue
        for c in comments:
            body = re.sub(r"^\s*fact\s*:", "", c, flags=re.I).strip()
            try:
                records.append(parse_record(body, path, i))
            except Malformed as e:
                errors.append(f"{path}:{i}: {e}")
    return records, errors


def home_paths(root: Path) -> list[Path]:
    """The home files. Fails closed when a named home or the projects folder is missing."""
    root = Path(root)
    paths = []
    for g in HOME_GLOBS:
        found = sorted(root.glob(g))
        if not found:
            raise Unreadable(f"no home matches {g}")
        paths += found
    for f in HOME_FILES:
        p = root / f
        if not p.is_file():
            raise Unreadable(f"home missing: {f}")
        paths.append(p)
    return paths


def load(root: Path, records_file: Path | None = None) -> tuple[list, list]:
    """(records, errors) from the homes, or from records_file when given."""
    root = Path(root)
    paths = [Path(records_file)] if records_file else home_paths(root)
    _guard_sealed(paths, root)   # a records file or a symlinked home is a read too (Fable F3)
    records, errors = [], []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise Unreadable(f"{p}: {e}") from e
        rel = str(p.relative_to(root)) if p.is_absolute() and root in p.parents else str(p)
        r, e = scan_text(text, rel)
        records += r
        errors += e
    return records, errors


def resolve(candidates: list) -> Record | None:
    """The current record among candidates for one key: source rank first, then as_of.
    Raises Conflict when the top rank and date hold two different values."""
    if not candidates:
        return None
    best = max(candidates, key=lambda r: (SOURCE_RANK[r.source_kind], r.as_of))
    ties = [r for r in candidates
            if (SOURCE_RANK[r.source_kind], r.as_of) == (SOURCE_RANK[best.source_kind], best.as_of)]
    if any(r.forms != best.forms for r in ties):
        raise Conflict(f"{best.key}: {len(ties)} records of source {best.source_kind}, "
                       f"as_of {best.as_of}, disagree")
    return best


def current(records: list) -> dict:
    """key -> resolved record, for every key."""
    keys = {}
    for r in records:
        keys.setdefault(r.key, []).append(r)
    return {k: resolve(v) for k, v in keys.items()}


# ---------------------------------------------------------------- judging text

GUARDRAIL = re.compile(
    r"\b(?:don'?t|do\s+not|never|not|no|avoid|banned|wrong|stale|instead\s+of|rather\s+than"
    r"|supersed\w*)\b|\bwas\s+\"", re.I)
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me)\b")
ROLE_FIELD = re.compile(r"^\s*-?\s*(?:position|title|role|label)\s*:", re.I)
VOICE_SURFACE = re.compile(
    r"cover-letter|cold-outreach|follow-up|draft-email|outreach-brief|coached-answers|\.ya?ml$", re.I)


def _form_re(text: str) -> re.Pattern:
    words = [re.escape(w) for w in text.split()]
    return re.compile(r"(?<![\w-])" + r"\s+".join(words) + r"(?![\w-])", re.I)


def entity(key: str) -> str:
    """The part of a key after its first dot (`role_title.acme` -> `acme`); the key itself
    when it has none. Records sharing an entity correct one another's lines."""
    return key.split(".", 1)[1] if "." in key else key


def is_guardrail(line: str, rec: Record, related: list | None = None) -> bool:
    """A line naming a wrong form in order to ban it, or that also states a current form of
    this record or of a record about the same entity (it is documenting the correction, as
    check_zuora_principal_title.py's self-correcting exception does). The caller removes
    the stale form from the line first, so a current name INSIDE the stale form (an old
    title that names the right principal) does not excuse it."""
    if GUARDRAIL.search(line):
        return True
    forms = [v for r in [rec, *(related or [])] for v in r.forms.values()]
    return any(_form_re(v).search(line) for v in forms)


def anchored(line: str, path: str, owner: str) -> bool:
    """The line is about the owner: a CV role field, the owner's name, or first person on a
    surface written in the owner's voice."""
    if ROLE_FIELD.match(line) and VOICE_SURFACE.search(path or ""):
        return True
    if owner and any(_form_re(n).search(line) for n in owner.split() if len(n) > 2):
        return True
    return bool(FIRST_PERSON.search(line) and VOICE_SURFACE.search(path or ""))


def _until(s: Superseded, audience: str) -> date | None:
    """The date a superseded form stopped being correct for this audience, or None when the
    record gives none for it: then the form is not stale for this audience. A date is never
    borrowed from another audience (Codex r1 F4)."""
    if audience in s.until:
        return s.until[audience]
    return s.until.get("*")


def judge_text(text: str, records: dict, *, path: str, audience: str, as_at: date,
               owner: str = "") -> list[dict]:
    """Every line that states a banned form, or a superseded form past its until date as at
    `as_at`, for the resolved records. Each hit: line, key, form, kind, and `held` (why it
    does not count: guardrail, override, unanchored generic) or None when it counts."""
    best: dict = {}
    live = sorted((r for r in records.values() if r is not None), key=lambda r: r.key)
    related = {r.key: [o for o in live if o is not r and entity(o.key) == entity(r.key)]
               for r in live}
    for i, line in enumerate((text or "").splitlines(), 1):
        for rec in live:
            checks = [(t, "banned", g) for t, g in rec.banned]
            checks += [(s.text, "superseded", False) for s in rec.supersedes
                       if _until(s, audience) is not None and as_at > _until(s, audience)]
            for form, kind, generic in checks:
                if not _form_re(form).search(line):
                    continue
                held = None
                ov = OVERRIDE_RE.search(line)
                if ov is not None:
                    held = "override" if ov.group(1).strip() else None
                if held is None and is_guardrail(_form_re(form).sub(" ", line), rec, related[rec.key]):
                    held = "guardrail"
                if held is None and generic and not anchored(line, path, owner):
                    held = "unanchored-generic"
                hit = {"path": path, "line": i, "key": rec.key, "form": form,
                       "kind": kind, "held": held, "text": line.strip()[:200]}
                # One hit per line and form. Every record naming the form is judged first;
                # a counted hit beats a held one, so record order cannot hide a violation
                # (Codex r1 F1). Records are taken in key order, so a tie is deterministic.
                slot = (i, form.lower())
                if slot not in best or (best[slot]["held"] is not None and held is None):
                    best[slot] = hit
    return list(best.values())


def owner_name(root: Path) -> str:
    """The owner's name from data/profile.md's Name line ('' when absent). Raises
    Unreadable when the profile resolves under a sealed tree: it is read in --records mode
    too, where the homes are not loaded and guarded (Codex follow-up F1, P0)."""
    path = Path(root) / "data/profile.md"
    _guard_sealed([path], Path(root))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    m = re.search(r"^\s*-\s*\*\*Name:\*\*\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- sweep (build 2)

# Coached answers are judged once, in the artifact scope (Fable F7).
COPY_SCOPE = ("data/profile.md", "data/project-index.md", "data/company-notes/*.md")
ARTIFACT_NAMES = ("*cover-letter*", "*prep*", "*cheat-sheet*", "*soft-answers*",
                  "*outreach-brief*", "*cold-outreach*", "*follow-up*", "*draft-email*")
ARTIFACT_EXCLUDE = ("output/analysis/",)
LIVE_ARTIFACTS = ("coaching/coached-answers/",)   # rehearsed text, not a dated snapshot
ARTIFACT_SUFFIXES = (".md", ".yaml", ".yml", ".txt")
MMDDYY = re.compile(r"^(\d{2})(\d{2})(\d{2})-")


_HOOKS: dict = {}


def _sealed_hook(root: Path):
    """tools/check_sealed_read.py, loaded read-only with SEAL_REPO_ROOT set to `root`, so its
    own module-level resolution (the in-repo tree, the -sealed sibling, the vault folders and
    every fail-closed fallback, judged from the main checkout) is computed for this root.
    One source of truth: facts.py keeps no copy of those rules (org dc ruling; Codex/Fable
    7.F2, 9.F1, 10.F1, 10.F2 were all drift between a copy and the hook). The guard file is
    imported, never edited. Cached per root."""
    key = str(root)
    if key not in _HOOKS:
        import importlib.util
        path = Path(__file__).resolve().parent / "check_sealed_read.py"
        old = os.environ.get("SEAL_REPO_ROOT")
        os.environ["SEAL_REPO_ROOT"] = key
        try:
            spec = importlib.util.spec_from_file_location(f"_facts_sealed_{len(_HOOKS)}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            # _root() reads the variable on every call, so take it inside the same window
            # the module-level constants were computed in.
            base = Path(mod._root())
        finally:
            if old is None:
                os.environ.pop("SEAL_REPO_ROOT", None)
            else:
                os.environ["SEAL_REPO_ROOT"] = old
        _HOOKS[key] = (mod, base)
    return _HOOKS[key]


def sealed_roots(root: Path) -> list[Path]:
    """Paths no scope may reach, exactly as check_sealed_read.py computes them for this root:
    its in-repo sealed tree and -sealed sibling (both of the main checkout) and its vault
    folders, including the vault's data/ whenever the hook fails closed. Raises Unreadable
    when the hook cannot be loaded: no sealed set means nothing may be read."""
    try:
        hook, base = _sealed_hook(Path(root).resolve())
        roots =[base / hook.SEALED_REL, base.parent / hook.SEALED_HOME]
        roots += [base / d for d in hook.VAULT_DIRS]
    except Exception as e:  # noqa: BLE001 -- fail closed
        raise Unreadable(f"cannot load the sealed-root rules from check_sealed_read.py: {e}")
    return sorted({p.resolve() for p in roots})


def _under(path: Path, root: Path) -> bool:
    """path is root or inside it, compared case-insensitively: this filesystem folds case,
    and so does check_sealed_read.py, so a case-variant spelling is the same folder
    (Fable 7.F1). Folding can only over-block, never let a sealed path through."""
    p, r = str(path).casefold(), str(root).casefold().rstrip("/")
    return p == r or p.startswith(r + "/")


def _guard_sealed(paths: list[Path], root: Path) -> None:
    blocked = sealed_roots(root)
    for p in paths:
        rp = p.resolve()
        if any(_under(rp, b) for b in blocked):
            raise Unreadable(f"scope reaches a sealed path: {p}")


def _audience(path: Path) -> str:
    return "cv" if path.suffix in (".yaml", ".yml") else "prose"


def file_date(path: Path) -> date:
    m = MMDDYY.match(path.name)
    if m:
        try:
            return date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def copy_scope(root: Path, extra: list | None = None) -> list[Path]:
    root = Path(root)
    out = []
    for g in COPY_SCOPE:
        out += sorted(p for p in root.glob(g) if p.is_file())
    out += [Path(e) if Path(e).is_absolute() else root / e for e in (extra or [])]
    return out


def artifact_scope(root: Path, owner: str) -> list[Path]:
    """Search-facing files under output/ by name, plus coached answers. A CV is a YAML
    whose name carries the owner's surname; dossiers are output/<slug>/<slug>.md."""
    root = Path(root)
    surname = owner.split()[-1].lower() if owner else ""
    out = []
    base = root / "output"
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/") + "/"
        if any(rel_dir.startswith(x) for x in ARTIFACT_EXCLUDE):
            dirnames[:] = []
            continue
        for name in filenames:
            p = Path(dirpath) / name
            low = name.lower()
            if p.suffix.lower() not in ARTIFACT_SUFFIXES:
                continue
            is_cv = surname and surname in low and p.suffix.lower() in (".yaml", ".yml")
            is_dossier = p.parent.parent == base and p.stem == p.parent.name
            if is_cv or is_dossier or any(fnmatch.fnmatch(low, g) for g in ARTIFACT_NAMES):
                out.append(p)
    out += sorted((root / "coaching/coached-answers").glob("*.md"))
    return out


def sweep(root: Path, today: date, records_file: Path | None = None,
          extra_copies: list | None = None) -> dict:
    """Both scopes against the resolved records. Raises Unreadable on an empty scope, a
    sealed path in a scope, or unreadable records."""
    root = Path(root)
    records, errors = load(root, records_file)
    if errors:
        raise Unreadable("malformed records: " + "; ".join(errors[:5]))
    cur = current(records)
    if not cur:
        raise Unreadable("no fact records: nothing to check against")
    owner = owner_name(root)
    scopes = {"copies": copy_scope(root, extra_copies), "artifacts": artifact_scope(root, owner)}
    for name, paths in scopes.items():
        if not paths:
            raise Unreadable(f"scope {name} is empty")
        _guard_sealed(paths, root)
    report = {"today": today.isoformat(), "keys": sorted(cur), "scopes": {}}
    for name, paths in scopes.items():
        counted, held, would_block, unreadable = [], [], set(), []
        for p in paths:
            try:   # strict decode: a file judged over replaced bytes is not checked (Codex r2 F1)
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as e:
                unreadable.append(f"{p}: {e}")
                continue
            rel = os.path.relpath(p, root)
            aud = _audience(p)
            # Defects: a dated artifact is judged as at its own date (a form correct when it
            # was written is not a defect). Copies and coached answers are live documents,
            # judged as at today (Fable narrow 5.F1).
            live = name == "copies" or rel.replace(os.sep, "/").startswith(LIVE_ARTIFACTS)
            as_at = today if live else file_date(p)
            for h in judge_text(text, cur, path=rel, audience=aud, as_at=as_at, owner=owner):
                (counted if h["held"] is None else held).append(h)
            # Whole-artifact rule at write time: would the next edit of this file block?
            if any(h["held"] is None for h in judge_text(text, cur, path=rel, audience=aud,
                                                          as_at=today, owner=owner)):
                would_block.add(rel)
        report["scopes"][name] = {
            "files": len(paths), "unreadable": unreadable, "hits": counted,
            "held": held, "would_block_on_next_edit": sorted(would_block)}
        if unreadable:   # any unread file fails the run, not only an all-unread scope (Codex r1 F3)
            raise Unreadable(f"scope {name}: {len(unreadable)} of {len(paths)} file(s) could "
                             f"not be read; first: {unreadable[0]}")
    report["drift"] = any(s["hits"] for s in report["scopes"].values())
    return report


def brief(report: dict, keep: int = 10) -> dict:
    """A sweep report cut to counts plus the first `keep` counted hits per scope, for the
    nightly queue (the full report is the CLI's)."""
    out = {k: v for k, v in report.items() if k != "scopes"}
    out["scopes"] = {
        name: {"files": s["files"], "hits": len(s["hits"]), "held": len(s["held"]),
               "unreadable": len(s["unreadable"]),
               "would_block_on_next_edit": len(s["would_block_on_next_edit"]),
               "first_hits": [{k: h[k] for k in ("path", "line", "key", "kind")}
                              for h in s["hits"][:keep]]}
        for name, s in report.get("scopes", {}).items()}
    return out


def nightly(root: Path, today: date, records_file: Path | None = None,
            short: bool = False) -> dict:
    """For the nightly scan: never raises. An unreadable or empty scope is recorded as drift,
    in target_drift.nightly's shape. `short` returns brief(report)."""
    try:
        report = sweep(root, today, records_file)
        return brief(report) if short else report
    except Unreadable as e:
        return {"drift": True, "problems": [f"unreadable input: {e}"],
                "today": today.isoformat(), "error": str(e)}
    except Exception as e:  # noqa: BLE001 -- the nightly scan must still write its queue
        return {"drift": True, "problems": [f"fact sweep failed: {type(e).__name__}: {e}"],
                "today": today.isoformat(), "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- CLI

def _cmd_get(args, root) -> int:
    records, errors = load(root, args.records)
    cands = [r for r in records if r.key == args.key]
    if errors:   # fail closed: a malformed record may be the one for this key
        print(f"ERROR: {len(errors)} malformed record(s); first: {errors[0]}", file=sys.stderr)
        return 2
    try:
        rec = resolve(cands)
    except Conflict as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if rec is None:
        where = args.records or "the homes (" + ", ".join(HOME_GLOBS + HOME_FILES) + ")"
        print(f"NO RECORD for {args.key}: absence here is not evidence the fact is false; "
              f"read {where} or ask the owner", file=sys.stderr)
        return 3
    value = rec.value(args.audience)
    if not value:
        print(f"NO RECORD for {args.key} in audience {args.audience!r}; forms held: "
              f"{', '.join(rec.forms)}", file=sys.stderr)
        return 3
    out = {"key": rec.key, "value": value, "audience": args.audience, "rule": rec.rule,
           "as_of": rec.as_of.isoformat(), "source": rec.source_kind,
           "home": f"{rec.path}:{rec.line}"}
    print(json.dumps(out, indent=2) if args.json else value)
    return 0


def _cmd_validate(args, root) -> int:
    records, errors = load(root, args.records)
    conflicts = []
    keys = {}
    for r in records:
        keys.setdefault(r.key, []).append(r)
    for k, v in keys.items():
        try:
            resolve(v)
        except Conflict as e:
            conflicts.append(str(e))
    print(json.dumps({"records": len(records), "keys": sorted(keys), "errors": errors,
                      "conflicts": conflicts}, indent=2))
    return 1 if errors or conflicts else 0


def _cmd_sweep(args, root) -> int:
    today = date.fromisoformat(args.today) if args.today else date.today()
    report = sweep(root, today, args.records, args.copy)
    print(json.dumps(report, indent=2))
    return 1 if report["drift"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--records", type=Path, help="read records from this file, not the homes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("key")
    g.add_argument("--audience")
    g.add_argument("--json", action="store_true")
    sub.add_parser("validate")
    s = sub.add_parser("sweep")
    s.add_argument("--today")
    s.add_argument("--copy", action="append", default=[],
                   help="an extra copy file to check (repeatable), e.g. a memory-tier file")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        return {"get": _cmd_get, "validate": _cmd_validate, "sweep": _cmd_sweep}[args.cmd](args, root)
    except (Unreadable, Conflict, ValueError) as e:   # a conflict is an error (Fable F4); validate reports one as exit 1
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
