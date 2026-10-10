#!/usr/bin/env python3
"""records_lookup.py - is this person or company anywhere in my records?

WHY THIS EXISTS
    "Is X in my records?" was being answered with an ad-hoc grep, and an ad-hoc grep can
    return nothing for reasons that have nothing to do with the records. Origin, 2026-10-09:
    a lookup for a two-word name was written as a regex with an optional space, run without
    -E, and so could only match a literal question mark. It returned nothing, the absence was
    reported as fact, and the name was in the roster. Third occurrence of the same family (an
    empty search read as a statement about the world instead of a statement about the pattern).

WHAT IT DOES
    Matching ignores case, spaces and punctuation ON BOTH SIDES and folds accented, stroked
    and ligature Latin letters to plain ASCII (in other scripts, accents that Unicode can separate from
    their letter are removed too, and letters are never mapped to Latin ones), so "north wind", "Northwind", "North-Wind" and
    "north ?wind" are the same query and all find the same lines. There is no regex engine,
    so there is no pattern syntax to get wrong. Punctuation in a query is not matched
    literally; it is dropped.

    Every result carries its scope as an INCLUSION list: the sources that were searched and
    how many files each contributed. Nothing outside that list was searched. A zero-hit
    result is a claim about those files only, in a `citable_claim` string meant to be quoted
    as-is.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/records_lookup.py "north wind"
    PYTHONIOENCODING=utf-8 python3 tools/records_lookup.py "Jordan Lee" "Northwind" --deep
    PYTHONIOENCODING=utf-8 python3 tools/records_lookup.py --repo-root . "northwind"

    Default sources: the roster, pipeline, to-dos, inbox, outreach log, per-person dossiers,
    company and industry notes, each company dossier (output/<slug>/<slug>.md), and the names
    of the folders under output/.
    --deep adds every .md under output/, coaching/ and inbox/.
    The sealed folder is never searched, by name or through a symlink, and a symlink that
    points outside the repository is not followed.

EXIT CODES
    0  at least one term had a hit
    1  no term had a hit (read `citable_claim` for the scope before saying "not found")
    2  usage error (a term that normalizes to fewer than 3 characters would match noise,
       and an empty one would match nothing and look like a clean negative)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

MIN_TERM_CHARS = 3
EXCERPT_CHARS = 220
SEALED_PARTS = ("project-background",)

# Letters NFKD does not decompose and whose Unicode name does not carry a base letter.
# Letters named "LATIN SMALL LETTER X WITH <mark>" are handled by rule in _fold_letter.
TRANSLITERATE = str.maketrans({
    "ð": "d", "ı": "i", "æ": "ae", "œ": "oe", "ß": "ss", "þ": "th",
})
_STROKED = re.compile(r"LATIN SMALL LETTER ([A-Z]) WITH .+")

# (label, repo-relative glob, is_core). A missing CORE source is reported loudly:
# a file that is not there must never silently shrink the scope of a negative.
DEFAULT_SOURCES = [
    ("roster", "data/networking.md", True),
    ("pipeline", "data/job-pipeline.md", True),
    ("todos", "data/job-todos.md", True),
    ("inbox", "data/inbox.md", False),
    ("outreach log", "data/outreach-log.md", False),
    ("person dossiers", "data/people/*.md", False),
    ("company notes", "data/company-notes/*.md", False),
    ("industry notes", "data/industry-notes/*.md", False),
    ("company dossiers", "output/*/*.md", False),  # filtered to <slug>/<slug>.md below
]
DEEP_SOURCES = [
    ("all output files", "output/**/*.md", False),
    ("coaching files", "coaching/**/*.md", False),
    ("inbox files", "inbox/**/*.md", False),
]
FOLDER_SOURCE = "output folder names"


def _fold_letter(ch: str) -> str:
    """Map a Latin letter carrying a stroke, hook or bar to its base letter (z-stroke -> z).

    Without this, a record spelled with the letter and a query spelled without it never
    meet, and the miss is silent. Every other character is returned unchanged.
    """
    match = _STROKED.fullmatch(unicodedata.name(ch, ""))
    return match.group(1).lower() if match else ch


def normalize(text: str) -> str:
    """Lowercase, fold Latin letters to ASCII, drop everything that is not a letter or digit."""
    folded = text.casefold().translate(TRANSLITERATE)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(_fold_letter(ch) for ch in decomposed if ch.isalnum())


def _is_sealed(path: Path) -> bool:
    return any(part in SEALED_PARTS for part in path.parts)


def _allowed(root: Path, path: Path) -> bool:
    """Reject a sealed path, and a symlink whose real target is sealed or outside the repo."""
    real = path.resolve()
    return (not _is_sealed(path) and not _is_sealed(real)
            and real.is_relative_to(root.resolve()))


def _resolve(root: Path, label: str, pattern: str) -> list[Path]:
    paths = sorted(p for p in root.glob(pattern) if p.is_file())
    if label == "company dossiers":
        paths = [p for p in paths if p.stem == p.parent.name]
    return [p for p in paths if _allowed(root, p)]


def collect_sources(root: Path, deep: bool) -> tuple[list[dict], list[Path]]:
    """Return (per-source report, file list de-duplicated by physical file).

    `files` in the report is what the source ADDED after de-duplication, so the per-source
    counts sum to the total. `matched` is how many paths its pattern reached.
    """
    report, files, seen = [], [], set()
    for label, pattern, core in DEFAULT_SOURCES + (DEEP_SOURCES if deep else []):
        paths = _resolve(root, label, pattern)
        added = 0
        for p in paths:
            stat = p.stat()  # follows symlinks; identical for hard links to one file
            identity = (stat.st_dev, stat.st_ino)
            if identity not in seen:
                seen.add(identity)
                files.append(p)
                added += 1
        report.append({"source": label, "pattern": pattern, "files": added,
                       "matched": len(paths), "core": core, "missing": len(paths) == 0})
    return report, files


def _excerpt(line: str) -> str:
    line = line.strip()
    return line if len(line) <= EXCERPT_CHARS else line[:EXCERPT_CHARS] + "..."


def _records(root: Path, files: list[Path]):
    """Yield (kind, where, line_number, text) for every searchable record."""
    for path in files:
        rel = str(path.relative_to(root))
        # An unreadable file raises: skipping it would shrink the scope without saying so.
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, line in enumerate(lines, 1):
            yield "line", rel, lineno, line
    output_dir = root / "output"
    if output_dir.is_dir():
        for child in sorted(output_dir.iterdir()):
            if child.is_dir() and _allowed(root, child):
                yield "folder", f"output/{child.name}/", 0, child.name


def search(root: Path, terms: list[str], deep: bool = False, max_hits: int = 20) -> dict:
    root = Path(root)
    needles: list[tuple[str, str]] = []
    for term in terms:
        norm = normalize(term)
        if len(norm) < MIN_TERM_CHARS:
            raise ValueError(
                f"term {term!r} normalizes to {norm!r} ({len(norm)} characters); "
                f"need at least {MIN_TERM_CHARS}")
        if norm not in [n for _, n in needles]:  # the same query twice is one query
            needles.append((term, norm))

    source_report, files = collect_sources(root, deep)
    hits: dict[str, list[dict]] = {norm: [] for _, norm in needles}
    folder_count = 0
    for kind, where, lineno, text in _records(root, files):
        folder_count += kind == "folder"
        norm_text = normalize(text)
        for _, norm in needles:
            if norm in norm_text:
                hits[norm].append({"kind": kind, "file": where, "line": lineno,
                                   "text": where if kind == "folder" else _excerpt(text)})

    missing_core = [s["pattern"] for s in source_report if s["core"] and s["missing"]]
    searched = ", ".join(f'{s["source"]} ({s["files"]})' for s in source_report)
    scope_text = (f"searched exactly {len(files)} file(s) [{searched}] and {folder_count} "
                  f"{FOLDER_SOURCE}; nothing else was searched")
    if not deep:
        scope_text += "; --deep adds every .md under output/, coaching/ and inbox/"
    if missing_core:
        scope_text += f"; CORE SOURCE MISSING, scope is incomplete: {', '.join(missing_core)}"

    term_reports = []
    for term, norm in needles:
        found = hits[norm]
        line_hits = [h for h in found if h["kind"] == "line"]
        folder_hits = [h for h in found if h["kind"] == "folder"]
        files_with_hits = sorted({h["file"] for h in line_hits})
        if found:
            claim = (f'"{norm}" matches {len(line_hits)} line(s) in {len(files_with_hits)} '
                     f"file(s) and {len(folder_hits)} output folder name(s)")
        else:
            claim = f'no match for "{norm}"'
        claim += (" (case, spaces and punctuation ignored; Latin accents and ligatures folded); "
                  + scope_text)
        term_reports.append({
            "term": term, "normalized": norm, "hit_count": len(found),
            "line_hits": len(line_hits), "folder_hits": len(folder_hits),
            "files_with_hits": files_with_hits,
            "folders_with_hits": [h["file"] for h in folder_hits],
            "hits": found[:max_hits], "hits_truncated": len(found) > max_hits,
            "citable_claim": claim,
        })

    return {
        "status": "ok",
        "terms": term_reports,
        "scope": {"files_searched": len(files), "output_folders_searched": folder_count,
                  "deep": deep, "sources": source_report, "missing_core_sources": missing_core},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find a person or company across the records, ignoring case, spaces and "
                    "punctuation. Every result states exactly what was searched.")
    parser.add_argument("terms", nargs="+", help="one or more names to look up")
    parser.add_argument("--repo-root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--deep", action="store_true",
                        help="also search every .md under output/, coaching/ and inbox/")
    parser.add_argument("--max-hits", type=int, default=20,
                        help="hits shown per term (hit_count is always the full count)")
    args = parser.parse_args(argv)

    try:
        result = search(Path(args.repo_root), args.terms, deep=args.deep, max_hits=args.max_hits)
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2

    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if any(t["hit_count"] for t in result["terms"]) else 1


if __name__ == "__main__":
    sys.exit(main())
