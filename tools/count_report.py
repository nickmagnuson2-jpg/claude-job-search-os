#!/usr/bin/env python3
"""count_report.py -- make a count state its own reach.

WHY THIS EXISTS (2026-10-23). The Hard Rule "Name the scope in the same sentence as the
conclusion" fired 38 times, and most fires were instrument reach: a ripgrep-backed `grep`
that skips the gitignored data/ and output/ trees, a non-recursive glob, a search over one
channel. Of 311 "N of M" claims traced to their source over 30 days, about 60 came from
tools/*.py and the rest from Read, one-off python, and shell pipelines. A count printed
without its population cannot be checked, and an empty result is indistinguishable from a
search that never looked. This helper prints one line that carries the population, how many
were included, and how many were excluded and why, and refuses to print it when those do
not add up.

LIBRARY (one-off scripts):

    import sys; sys.path.insert(0, "tools")              # run from the repo root
    import sys; sys.path.insert(0, "<repo-root>/tools")  # run from /private/tmp etc.
    from count_report import report
    report("rows in data/example.md", population=40, included=31,
           excluded={"header rows": 2, "status Withdrawn": 7})
    # scope: rows in data/example.md | population 40 | included 31 | excluded: header rows 2, status Withdrawn 7

    It raises ValueError when population != included + sum(excluded), when the scope is
    empty, or when a count is negative or not an int. A count that does not add up is the
    silent-cap failure, so it is an error, not a warning.

CLI (the commonest shape, counting files):

    PYTHONIOENCODING=utf-8 python3 tools/count_report.py files <root>... \
        [--glob PATTERN] [--newer-than-days N] [--grep REGEX] [--list]

    Walks each root with os.walk. No ripgrep and no git, so gitignored trees ARE seen.
    Always prunes `.git`, `memory/canonical-sidecar` and `memory/global-claude-mirror`
    (backup copies that double every hit); files inside them are counted as a named
    exclusion, never dropped silently. --glob matches the basename, or the path relative
    to the root when the pattern contains '/'. --grep is a Python regex searched in the
    file text (UTF-8, undecodable bytes replaced). Filters apply in the order
    glob, age, regex; each file is excluded under the first filter it fails.
    Symlinks to files count as files; symlinked directories are not followed. So the
    population can exceed `find -type f` by the number of file symlinks.
    Output: the report line (its scope names every root searched), one line per root,
    then the matching paths when --list is given. A missing root exits 2, because a
    search over a path that does not exist reports "found nothing" about nothing.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import time

# Backup copies that duplicate the live memory tier, plus git internals. Matched as path
# suffixes so they are pruned wherever a root places them.
PRUNE_SUFFIXES = (
    os.path.join("memory", "canonical-sidecar"),
    os.path.join("memory", "global-claude-mirror"),
)
PRUNE_NAMES = (".git",)

R_PRUNED = "pruned .git/backup trees"
R_GLOB = "glob mismatch"
R_OLD = "older than {} days"
R_NOMATCH = "no regex match"
R_UNREADABLE = "unreadable"


def _check_count(name: str, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")


def format_line(scope: str, population: int, included: int,
                excluded: dict[str, int] | None = None) -> str:
    """Validate and return the report line without printing it."""
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope is empty: name what was searched, or the count means nothing")
    excluded = dict(excluded or {})
    _check_count("population", population)
    _check_count("included", included)
    for reason, n in excluded.items():
        if not str(reason).strip():
            raise ValueError("an exclusion reason is empty")
        _check_count(f"excluded[{reason!r}]", n)
    total = included + sum(excluded.values())
    if total != population:
        raise ValueError(
            f"counts do not conserve: population {population} != included {included}"
            f" + excluded {sum(excluded.values())} ({total}); something was dropped"
            " or double-counted")
    parts = ", ".join(f"{r} {n}" for r, n in excluded.items()) or "none"
    return (f"scope: {scope.strip()} | population {population} | included {included}"
            f" | excluded: {parts}")


def report(scope: str, population: int, included: int,
           excluded: dict[str, int] | None = None, *, file=None) -> str:
    """Print the report line (to stdout by default) and return it."""
    line = format_line(scope, population, included, excluded)
    print(line, file=file if file is not None else sys.stdout)
    return line


def _is_pruned(dirpath: str, name: str) -> bool:
    if name in PRUNE_NAMES:
        return True
    full = os.path.normpath(os.path.join(dirpath, name))
    return any(full.endswith(os.sep + s) or full == s for s in PRUNE_SUFFIXES)


def _count_tree(path: str) -> int:
    return sum(len(files) for _, _, files in os.walk(path))


def count_files(root: str, glob: str | None = None, newer_than_days: float | None = None,
                grep: str | None = None, now: float | None = None) -> dict:
    """Walk one root. Returns population, included paths and an ordered exclusion dict."""
    rx = re.compile(grep) if grep else None
    cutoff = None
    if newer_than_days is not None:
        cutoff = (now if now is not None else time.time()) - newer_than_days * 86400
    old_label = R_OLD.format(_fmt_days(newer_than_days)) if cutoff is not None else None
    excluded = {R_PRUNED: 0}
    if glob:
        excluded[R_GLOB] = 0
    if old_label:
        excluded[old_label] = 0
    if rx:
        excluded[R_NOMATCH] = 0
    excluded[R_UNREADABLE] = 0
    included: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if _is_pruned(dirpath, d):
                excluded[R_PRUNED] += _count_tree(os.path.join(dirpath, d))
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            reason = _exclusion(path, root, fn, glob, cutoff, old_label, rx)
            if reason is None:
                included.append(path)
            else:
                excluded[reason] += 1
    population = len(included) + sum(excluded.values())
    return {"root": root, "population": population, "included": sorted(included),
            "excluded": excluded}


def _fmt_days(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def _exclusion(path, root, name, glob, cutoff, old_label, rx):
    if glob:
        target = os.path.relpath(path, root) if "/" in glob else name
        if not fnmatch.fnmatch(target, glob):
            return R_GLOB
    try:
        if cutoff is not None and os.stat(path).st_mtime < cutoff:
            return old_label
        if rx:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
            if not rx.search(text):
                return R_NOMATCH
    except OSError:
        return R_UNREADABLE
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("files", help="count files under one or more roots")
    f.add_argument("roots", nargs="+")
    f.add_argument("--glob")
    f.add_argument("--newer-than-days", type=float)
    f.add_argument("--grep")
    f.add_argument("--list", action="store_true", help="print every included path")
    args = ap.parse_args(argv)

    missing = [r for r in args.roots if not os.path.isdir(r)]
    if missing:
        print(f"count_report: root not found or not a directory: {', '.join(missing)}",
              file=sys.stderr)
        return 2
    results = [count_files(r, args.glob, args.newer_than_days, args.grep)
               for r in args.roots]
    totals: dict[str, int] = {}
    for res in results:
        for reason, n in res["excluded"].items():
            totals[reason] = totals.get(reason, 0) + n
    scope = "files under " + ", ".join(args.roots)
    days = None if args.newer_than_days is None else _fmt_days(args.newer_than_days)
    for label, val in (("glob", args.glob), ("newer than days", days), ("regex", args.grep)):
        if val is not None:
            scope += f" [{label} {val}]"
    report(scope, sum(r["population"] for r in results),
           sum(len(r["included"]) for r in results), totals)
    for res in results:
        print(f"  root {res['root']}: population {res['population']}"
              f" | included {len(res['included'])}")
    if args.list:
        for res in results:
            for p in res["included"]:
                print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
