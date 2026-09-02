#!/usr/bin/env python3
"""audit_rule_violations.py — find existing artifacts that already break a rule.

The back-propagation half of the self-improvement loop. A newly-learned rule is
usually enforced only FORWARD: a memory file is written, a hook is built, and
from then on new violations are caught. But the artifacts that ALREADY violate
the rule sit untouched — and a forward-guard over a poisoned source means the
guard trips on every run while the source keeps emitting the bad pattern.

This script is the backward sweep: give it the rule's violation signature (a
regex) and it lists every place in the repo that already breaks it, so they get
fixed in the SAME pass the rule is learned.

Origin: 2026-06-02. `[[project_macos_python3]]` (no bare `python` on macOS) had
existed for weeks, yet 13 skill docs still prescribed `python tools/...`. Nothing
ever swept them; the `check_bare_python.py` hook (built later) then tripped on
every skill run. Had this sweep run when the rule was first written, all 13 would
have been fixed that day.

Usage:
  audit_rule_violations.py --pattern '<regex>' [--root <dir>]
      [--ext .md .py ...] [--exclude <substr> ...]

JSON report (exit 0 always — a report, not a gate):
  {"status":"ok","pattern":...,"violations":[{"file","line","text"}],
   "count":N,"files":M}

Decide from `count`/`files` whether a sweep is needed; feed the file list into
the fix pass. Pair with the loop step in CLAUDE.md ("Back-propagate to existing
artifacts").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories never worth scanning for rule violations.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}

# Default artifact extensions: docs, scripts, config — where prescriptive text lives.
DEFAULT_EXTS = {".md", ".py", ".json", ".txt", ".yaml", ".yml", ".toml", ".cfg"}


def iter_files(root: Path, exts: set[str]):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        yield p


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--pattern", required=True, help="regex violation signature")
    ap.add_argument("--root", default=str(REPO_ROOT), help="directory to scan")
    ap.add_argument("--ext", nargs="*", default=None,
                    help="file extensions to scan (default: common text artifacts)")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="skip files whose path contains any of these substrings")
    args = ap.parse_args(argv)

    try:
        rx = re.compile(args.pattern)
    except re.error as e:
        sys.stdout.write(json.dumps({"status": "error", "message": f"bad regex: {e}"}) + "\n")
        sys.exit(2)

    root = Path(args.root).resolve()
    exts = {e if e.startswith(".") else "." + e for e in args.ext} if args.ext else DEFAULT_EXTS

    violations = []
    files_scanned = 0
    for p in iter_files(root, exts):
        rel = str(p.relative_to(root))
        if any(x in rel for x in args.exclude):
            continue
        files_scanned += 1
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                violations.append({"file": rel, "line": i, "text": line.strip()[:200]})

    files = sorted({v["file"] for v in violations})
    sys.stdout.write(json.dumps({
        "status": "ok",
        "pattern": args.pattern,
        "root": str(root),
        "violations": violations,
        "count": len(violations),
        "files": len(files),
        # Scope, stated next to the conclusion (2026-08-19). `--ext ""` normalizes to
        # {"."} and matches nothing, turning 588 violations into count:0 status:ok.
        # NOTE: `files` above counts files WITH violations, so it is 0 on a genuine
        # clean sweep too -- before this change the output carried NO signal that
        # separated "scanned 215 files, found nothing" from "scanned nothing at all".
        # `files_scanned` is that signal. This tool is prescribed at CLAUDE.md:86 as a
        # mandatory step, so a false clean here silently skips a back-propagation.
        # Additive only: status and exit codes are unchanged.
        "files_scanned": files_scanned,
        "exts_applied": sorted(exts),
        "exts_defaulted": args.ext is None,
        "file_list": files,
    }) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
