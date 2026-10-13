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
  PYTHONIOENCODING=utf-8 python3 tools/company_dim.py --sync       # add every unknown string

SYNC (added 2026-10-13). pipe_write.py and networking_write.py call sync_after_write()
after every write, so a company added through them reaches the dimension in the same
command. Until then the live guard test failed each time any session added a company,
and stayed red until someone edited the YAML by hand. Sync adds the string AS WRITTEN as
a new canonical entry: `target` when it is in the pipeline (that is what the type means),
`REVIEW` otherwise. It never merges a new spelling into an existing company, because
that is the guess this module refuses to make; such an entry is a duplicate until a
human makes it an alias, and `added_by: sync <date>` marks the entries to look at.
The outreach log has no writer script, so a company that appears only there is picked
up by the next pipeline or networking write, or by --sync.
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

import yaml

VALID_TYPES = ("target", "recruiter", "other", "REVIEW")
# A company cell holding only a dash is an absent value. The hyphen is here because
# the writers stopped emitting em dashes; before it was listed, one such row made
# `unresolved` report "-" as an unknown company.
PLACEHOLDERS = ("", "—", "-")
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
                                     if r["company"].strip() not in PLACEHOLDERS
                                     and not dim.resolve(r["company"])})
    net = []
    for line in read_file(repo_root / "data" / "networking.md").splitlines():
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) < 4 or c[0] in ("Name", "---") or set(c[0]) <= set("- "):
            continue
        if c[1] not in PLACEHOLDERS:
            net.append(c[1])
    out["networking.md"] = sorted({c for c in net if not dim.resolve(c)})
    return out


SOURCE_OF = {"job-pipeline.md": "pipeline", "outreach-log.md": "outreach",
             "networking.md": "networking"}


def sync(repo_root: Path, path: Path | None = None, today: str | None = None) -> list[dict]:
    """Add every company string the dimension does not know. Returns the new entries.

    No dimension file means nothing to keep in step: returns [] and writes nothing.
    """
    repo_root = Path(repo_root)
    path = Path(path) if path else repo_root / "data" / "companies.yaml"
    if not path.exists():
        return []
    dim = load(path)
    seen: dict[str, list[str]] = {}
    for file, names in unresolved(dim, repo_root).items():
        for name in names:
            seen.setdefault(name, []).append(SOURCE_OF[file])
    if not seen:
        return []
    stamp = "sync " + (today or date.today().isoformat())
    added = [{"canonical": name,
              "type": "target" if "pipeline" in where else "REVIEW",
              "seen_in": where, "added_by": stamp}
             for name, where in sorted(seen.items())]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["companies"] = list(doc["companies"]) + added
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
                   encoding="utf-8")
    load(tmp)                       # two new strings that differ only in case collide here
    os.replace(tmp, path)
    return added


def sync_after_write(data_file: Path) -> None:
    """Called by the writers of data/*.md after a successful write. Never raises: the
    write it follows has already landed, and a failure here must not report it as lost."""
    try:
        added = sync(Path(data_file).resolve().parent.parent)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: company dimension not updated ({type(exc).__name__}: {exc}). "
              "Run: python3 tools/company_dim.py --sync", file=sys.stderr)
        return
    for e in added:
        print(f"company dimension: added {e['canonical']!r} as {e['type']}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Company dimension: resolve, list, and guard.")
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true",
                    help="exit 2 if any live company string does not resolve")
    ap.add_argument("--sync", action="store_true",
                    help="add every live company string the dimension does not know")
    ap.add_argument("--list-type", choices=VALID_TYPES)
    ap.add_argument("--resolve", metavar="NAME")
    args = ap.parse_args(argv)

    try:
        dim = load(args.path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.sync:
        try:
            added = sync(args.repo_root, args.path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        for e in added:
            print(f"added {e['canonical']!r} as {e['type']} (seen in {', '.join(e['seen_in'])})")
        print(f"{len(added)} added")
        return 0

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
