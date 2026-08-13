#!/usr/bin/env python3
"""
prep_doc_parse.py — Shared locator + parser for /prep-interview prep docs.

Three consumers need to agree on two questions: "where is the prep doc for this
company?" and "what proofs did it bind?" — /prep-interview (writes them), /follow-up
Step 3e (reads them before deploying a pre-bound proof), and check_prep_doc.py
(validates them). Three private globs and three private regexes would drift, and the
drift is silent: /follow-up would report "no prep doc" for a doc that exists, and the
safety gate it guards would quietly never fire.

The proof-line regex is frozen HERE and imported everywhere else.

Stdlib only.
"""
import json
import re
from pathlib import Path

# Frozen. /prep-interview emits exactly this shape; check_prep_doc.py validates against
# it; /follow-up reads the reserve slot out of it. Changing it means changing all three.
PROOF_LINE_RE = re.compile(
    r"^\*\*(?P<slot>Primary|Reserve) proof\*\* \(domain: (?P<tag>[a-z-]+)\):\s*(?P<proof>.+)$"
)

# Prep docs live at output/<company-slug>/<MMDDYY>-<descriptor>.md per the repo's
# output convention. The date prefix is the ordering key; mtime is only a tie-break.
_DATE_PREFIX_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})-")


def _sort_key(path: Path) -> tuple:
    """Newest-first ordering key: MMDDYY prefix as (YY, MM, DD), then mtime."""
    m = _DATE_PREFIX_RE.match(path.name)
    if m:
        month, day, year = m.groups()
        stamp = (int(year), int(month), int(day))
    else:
        # An undated file sorts below every dated one rather than winning by accident.
        stamp = (-1, -1, -1)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (stamp, mtime, path.name)


def find_prep_doc(company_slug: str, repo_root: Path | str = ".") -> str | None:
    """Newest prep doc for a company slug, or None. Returns a repo-relative path."""
    root = Path(repo_root)
    directory = root / "output" / company_slug
    if not directory.is_dir():
        return None
    candidates = [p for p in directory.glob("*prep*.md") if p.is_file()]
    if not candidates:
        return None
    newest = max(candidates, key=_sort_key)
    try:
        return str(newest.relative_to(root))
    except ValueError:
        return str(newest)


def parse_proofs(path: Path | str) -> dict:
    """Extract the Primary/Reserve proof lines from a prep doc on disk."""
    return parse_proofs_text(Path(path).read_text(encoding="utf-8"))


def parse_proofs_text(text: str) -> dict:
    """Extract the Primary/Reserve proof lines from prep-doc TEXT.

    Split out from parse_proofs so a PreToolUse hook can check content that has not been
    written to disk yet (a Write payload, or an Edit's new_string) without a second copy
    of the frozen regex.

    Returns {"primary": {...}|None, "reserve": {...}|None}, each entry carrying the raw
    tag (NOT canonicalized — that is check_prep_doc's job, so the checker can report the
    tag the author actually wrote), the proof text, and the 1-indexed line number.
    """
    found: dict = {"primary": None, "reserve": None}
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = PROOF_LINE_RE.match(line.strip())
        if not m:
            continue
        slot = m.group("slot").lower()
        if found[slot] is None:  # first occurrence wins; a duplicate is not a new binding
            found[slot] = {
                "slot": slot,
                "tag": m.group("tag"),
                "proof": m.group("proof").strip(),
                "line": lineno,
            }
    return found


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Locate and parse /prep-interview prep docs.")
    parser.add_argument("--company-slug", dest="company_slug")
    parser.add_argument("--doc")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    if args.company_slug:
        print(json.dumps({"doc": find_prep_doc(args.company_slug, args.repo_root)}))
        return 0

    if args.doc:
        doc = Path(args.doc)
        if not doc.is_file():
            print(json.dumps({"error": f"cannot read prep doc: {args.doc}"}))
            return 2
        print(json.dumps({"doc": args.doc, "proofs": parse_proofs(doc)}, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
