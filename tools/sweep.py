#!/usr/bin/env python3
"""sweep.py — the one way to run an ad-hoc "does this token appear anywhere" scan.

WHY THIS EXISTS. Two memory rules reached their promotion gate on 2026-08-13 naming
almost the same structural target, so they get one helper rather than two:

  [[feedback_guard_must_hard_abort_on_empty_input]]  (4 fires) — a scan over an empty
      path list printed "no matches" and was read as a clean result. An empty sweep is
      an ERROR, not a negative finding.
  [[feedback_sampled_check_needs_a_conservation_anchor]]  (3 fires) — a sampling check
      (regex/keyword/heuristic) cannot be its own proof. A verdict with no denominator
      is not a verdict.

They are the same defect from opposite ends: *an empty input passes vacuously* and
*a sampled check validates itself*. Both are fixed by a sweep that always reports the
denominator it actually read, and refuses to report anything at all when that
denominator is zero.

Third failure folded in: [[feedback_replace_all_substring_check]] (3 fires, promoted) —
hand-rolled greps default to SUBSTRING matching, so a short token fires inside unrelated
words (~190 false hits in one 2026-08-12 pre-push sweep). Word boundaries are the default
here; substring is available only behind an explicit flag.

THE FOUR PROPERTIES:

  1. Explicit path list. Never a glob computed in a prior shell call, never a variable
     carrying scope across `Bash` invocations — shell state does not persist between
     tool calls (see [[reference_shell_env_does_not_persist_between_bash_calls]]).
  2. Empty list refused. Nonzero exit, never a clean pass.
  3. Denominator returned and printed: {"scanned": N, "matched": M, "paths": [...]}.
  4. Word-boundary matching by default; `--substring` is opt-in and labelled in output.

  Plus the optional positive control (`--control <token>`): a token known to be present.
  If the control does not match, the mechanism did not really read the files and a
  negative result is not trustworthy — exit 3, and `clean` is never True.

Shape extracted from `tools/check_public_pii.py --scan`, which already got this right;
per [[feedback_prefer_extending_existing_infra_over_new_build]] this generalizes that
convention rather than inventing a second one.

Usage (library):
    from sweep import sweep, EmptyScopeError
    result = sweep(paths, "some_token", control="def ")

Usage (CLI):
    PYTHONIOENCODING=utf-8 python3 tools/sweep.py --pattern TOKEN path/a.py path/b.md
    ls tools/*.py | PYTHONIOENCODING=utf-8 python3 tools/sweep.py --pattern TOKEN --stdin-paths
    ... --substring   # opt in to substring matching, recorded in the output
    ... --regex       # treat --pattern as a regex (no boundary wrapping)
    ... --control tok # assert a known-present token matches first

Exit codes:  0 = ran, no matches · 1 = ran, matches found · 2 = empty/invalid scope
             3 = positive control failed (result untrustworthy)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

BINARY_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".gz", ".tar",
    ".pyc", ".docx", ".xlsx", ".pptx", ".sqlite", ".db", ".woff", ".woff2", ".ttf",
}


class EmptyScopeError(ValueError):
    """Raised when a sweep is handed nothing to scan. Never downgrade this to a warning."""


def compile_pattern(pattern: str, *, substring: bool = False, regex: bool = False,
                    ignore_case: bool = False) -> re.Pattern:
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        return re.compile(pattern, flags)
    if substring:
        return re.compile(re.escape(pattern), flags)
    # Default: whole-word/phrase. Lookarounds rather than \b, and each edge is applied ONLY
    # when that edge is a word character. Wrapping unconditionally makes any token ending in
    # punctuation or a space unmatchable -- `"def "` would compile to a pattern requiring a
    # non-word character after the space, i.e. never a function definition. Found by
    # dogfooding this helper on its own repo, 2026-08-13.
    body = re.escape(pattern)
    left = r"(?<![\w])" if pattern[:1].isalnum() or pattern[:1] == "_" else ""
    right = r"(?![\w])" if pattern[-1:].isalnum() or pattern[-1:] == "_" else ""
    return re.compile(left + body + right, flags)


def _readable(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() not in BINARY_SUFFIXES


def sweep(paths, pattern: str, *, substring: bool = False, regex: bool = False,
          ignore_case: bool = False, control: str | None = None) -> dict:
    """Scan an explicit list of paths. Returns its own denominator; raises on an empty scope.

    `matched` is meaningful ONLY next to `scanned`. Callers that print one without the
    other reintroduce the bug this helper exists to prevent.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise EmptyScopeError(
            "sweep received ZERO paths — an empty scan is an error, not a negative result"
        )

    matcher = compile_pattern(pattern, substring=substring, regex=regex, ignore_case=ignore_case)
    control_matcher = (
        compile_pattern(control, substring=substring, regex=regex, ignore_case=ignore_case)
        if control else None
    )

    scanned: list[str] = []
    skipped: list[dict] = []
    matches: list[dict] = []
    control_hits = 0

    for path in paths:
        if not _readable(path):
            skipped.append({"path": str(path), "why": "missing, a directory, or binary"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            skipped.append({"path": str(path), "why": f"unreadable: {exc}"})
            continue
        scanned.append(str(path))
        for lineno, line in enumerate(text.splitlines(), start=1):
            if matcher.search(line):
                matches.append({"file": str(path), "line": lineno, "text": line.strip()[:200]})
        if control_matcher and control_matcher.search(text):
            control_hits += 1

    if not scanned:
        raise EmptyScopeError(
            f"sweep read ZERO files out of {len(paths)} given path(s) — "
            f"every one was missing, a directory, or unreadable: {skipped}"
        )

    result = {
        "pattern": pattern,
        "match_mode": "regex" if regex else ("substring" if substring else "word-boundary"),
        "ignore_case": ignore_case,
        "given": len(paths),
        "scanned": len(scanned),
        "skipped": skipped,
        "matched": len({m["file"] for m in matches}),
        "match_count": len(matches),
        "matches": matches,
        "paths": scanned,
    }

    if control_matcher:
        result["control"] = control
        result["control_files"] = control_hits
        result["control_ok"] = control_hits > 0
        # A negative result is only trustworthy once the mechanism is proven to have read
        # the files. Without the control passing, `clean` must not be asserted.
        result["clean"] = (not matches) and control_hits > 0
    else:
        result["control_ok"] = None
        result["clean"] = not matches

    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--pattern", required=True, help="token (word-boundary matched by default)")
    ap.add_argument("--substring", action="store_true", help="opt in to substring matching")
    ap.add_argument("--regex", action="store_true", help="treat --pattern as a regex")
    ap.add_argument("-i", "--ignore-case", action="store_true")
    ap.add_argument("--control", default=None,
                    help="a token known to be present; a negative result is untrusted without it")
    ap.add_argument("--stdin-paths", action="store_true", help="read the path list from stdin")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)

    if args.substring and args.regex:
        sys.stdout.write(json.dumps({
            "status": "error", "reason": "--substring and --regex are mutually exclusive",
        }, indent=2) + "\n")
        return 2

    paths = list(args.paths)
    if args.stdin_paths:
        paths += [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]

    try:
        result = sweep(paths, args.pattern, substring=args.substring, regex=args.regex,
                       ignore_case=args.ignore_case, control=args.control)
    except EmptyScopeError as exc:
        sys.stdout.write(json.dumps({"status": "error", "scanned": 0, "reason": str(exc)}, indent=2) + "\n")
        return 2

    result["status"] = "ok"
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if result.get("control_ok") is False:
        return 3
    return 1 if result["matches"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
