#!/usr/bin/env python3
"""
company_dim.py — the company dimension: canonical names, aliases, and type.

WHY THIS EXISTS
---------------
Company was free text in three files that all need to join on it, and it drifted exactly
as free text does: three spellings of one company in the outreach log, two in networking,
and annotations of the form "Acme (Hiring Partners)" that packed the RECIPIENT'S EMPLOYER and
the TARGET COMPANY into one cell. A matcher clever enough to unpack that produced a false join
-- the annotated cell matched the pipeline row for the RECRUITING FIRM rather than the company
actually being pursued, silently attributing one company's outreach to another -- which is why
the fix is a dimension with human-confirmed entries and not a smarter regex.

`type` is the field that does the analytic work:
  target    -- a company pursued for a role
  recruiter -- a firm or marketplace that sources roles and is never itself a target
  other     -- a networking org, a former employer, a community
  REVIEW    -- unclassified; a human has not ruled yet

A company with an in-house recruiter is a TARGET, not a recruiter. An earlier auto-classifier
got this wrong and flagged six real targets because a contact there had "recruiter" in their
title.

CONTRACT: this module never guesses. resolve() returns None for an unknown string rather than
fuzzy-matching, because a wrong join is worse than a missing one -- a missing join shows up as
a coverage gap, a wrong one silently attributes outreach to the wrong company.

Source of truth: data/companies.yaml (gitignored -- it holds real company names).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/company_dim.py --check      # guard: all strings resolve
  PYTHONIOENCODING=utf-8 python3 tools/company_dim.py --list-type recruiter
"""
import argparse
import sys
from pathlib import Path

import yaml

VALID_TYPES = ("target", "recruiter", "other", "REVIEW")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "companies.yaml"


class Dimension:
    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._by_key: dict[str, dict] = {}
        for e in entries:
            self._by_key[self._key(e["canonical"])] = e
            for a in e.get("aliases") or []:
                self._by_key[self._key(a)] = e

    @staticmethod
    def _key(s: str) -> str:
        """Case- and whitespace-insensitive only. Deliberately NOT a fuzzy normaliser:
        stripping legal suffixes or punctuation is what let 'LLC' collide with ''."""
        return " ".join((s or "").split()).lower()

    def resolve(self, name: str) -> dict | None:
        """Canonical entry for a raw string, or None. Never guesses."""
        return self._by_key.get(self._key(name))

    def canonical(self, name: str) -> str | None:
        e = self.resolve(name)
        return e["canonical"] if e else None

    def type_of(self, name: str) -> str | None:
        e = self.resolve(name)
        return e["type"] if e else None

    def of_type(self, t: str) -> list[str]:
        return sorted(e["canonical"] for e in self.entries if e["type"] == t)

    def __len__(self) -> int:
        return len(self.entries)


def load(path: Path = DEFAULT_PATH) -> Dimension:
    if not path.exists():
        raise FileNotFoundError(f"company dimension not found: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("companies") or []
    if not entries:
        raise ValueError(f"{path} defines no companies; refusing to load an empty dimension")
    seen: dict[str, str] = {}
    for e in entries:
        if "canonical" not in e or "type" not in e:
            raise ValueError(f"entry missing canonical/type: {e}")
        if e["type"] not in VALID_TYPES:
            raise ValueError(f"{e['canonical']}: bad type {e['type']!r}; allowed {VALID_TYPES}")
        for name in [e["canonical"], *(e.get("aliases") or [])]:
            k = Dimension._key(name)
            if k in seen:
                raise ValueError(
                    f"{name!r} claimed by both {seen[k]!r} and {e['canonical']!r}")
            seen[k] = e["canonical"]
    return Dimension(entries)


def unresolved(dim: Dimension, repo_root: Path) -> dict[str, list[str]]:
    """Company strings in the live data files that the dimension does not know."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from datetime import date
    from pipe_read import parse_all_rows, read_file
    from outreach_metrics import parse_rows

    out: dict[str, list[str]] = {}
    pipe = parse_all_rows(read_file(repo_root / "data" / "job-pipeline.md"), date.today())
    out["job-pipeline.md"] = sorted({r["company"].strip() for r in pipe
                                     if r["company"].strip() and not dim.resolve(r["company"])})
    log = parse_rows(read_file(repo_root / "data" / "outreach-log.md"))
    out["outreach-log.md"] = sorted({r["company"].strip() for r in log
                                     if r["company"].strip() not in ("", "—")
                                     and not dim.resolve(r["company"])})
    net = []
    for line in read_file(repo_root / "data" / "networking.md").splitlines():
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) < 4 or c[0] in ("Name", "---") or set(c[0]) <= set("- "):
            continue
        if c[1] and c[1] not in ("", "—"):
            net.append(c[1])
    out["networking.md"] = sorted({c for c in net if not dim.resolve(c)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Company dimension: resolve, list, and guard.")
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true",
                    help="exit 2 if any live company string does not resolve")
    ap.add_argument("--list-type", choices=VALID_TYPES)
    ap.add_argument("--resolve", metavar="NAME")
    args = ap.parse_args(argv)

    try:
        dim = load(args.path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.resolve:
        e = dim.resolve(args.resolve)
        print(f"{args.resolve!r} -> {e['canonical']!r} ({e['type']})" if e
              else f"{args.resolve!r} -> UNRESOLVED")
        return 0 if e else 2

    if args.list_type:
        for c in dim.of_type(args.list_type):
            print(c)
        return 0

    if args.check:
        gaps = unresolved(dim, args.repo_root)
        total = sum(len(v) for v in gaps.values())
        for f, names in gaps.items():
            print(f"{f}: {len(names)} unresolved")
            for n in names:
                print(f"    {n}")
        review = dim.of_type("REVIEW")
        print(f"\ndimension: {len(dim)} companies, {len(review)} still REVIEW")
        return 2 if total else 0

    print(f"{len(dim)} companies loaded from {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
