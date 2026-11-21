#!/usr/bin/env python3
"""ats_board.py - read one company's public job board through the project parsers.

The supported way to look at an Ashby, Greenhouse or Lever board from a session.
`tools/check_ats_raw_fetch.py` blocks hand-rolled curl / urllib fetches of those
APIs and points here, because a hand-rolled fetch loses the parsers' error channel:
a dead slug returns an empty or unparseable body and reads as "no openings".

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/ats_board.py ashby <slug> [--comp] [--title REGEX] [--json]
    PYTHONIOENCODING=utf-8 python3 tools/ats_board.py greenhouse <slug> [--title REGEX] [--json]
    PYTHONIOENCODING=utf-8 python3 tools/ats_board.py lever <slug> [--title REGEX] [--json]

    --comp   Ashby only: include the posting id and the pay summary
             (fetch_ashby_with_compensation).
    --title  Case-insensitive regex; keep only roles whose title matches.
    --json   Print the role dicts as JSON instead of one line per role.

Exit codes:
    0  the board was read (it may legitimately have zero roles)
    1  the fetch failed; the parser's reason is printed. Never a silent zero.
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from tools.career_scanner.ashby import fetch_ashby, fetch_ashby_with_compensation  # noqa: E402
from tools.career_scanner.greenhouse import fetch_greenhouse  # noqa: E402
from tools.career_scanner.lever import fetch_lever  # noqa: E402
from tools.count_report import report  # noqa: E402


def _fetcher(ats: str, comp: bool):
    if ats == "ashby":
        return fetch_ashby_with_compensation if comp else fetch_ashby
    if ats == "greenhouse":
        return fetch_greenhouse
    return fetch_lever


def read_board(ats: str, slug: str, comp: bool = False,
               title: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return (roles, errors). errors is non-empty exactly when the fetch failed."""
    errors: list[dict] = []
    try:
        roles = _fetcher(ats, comp)(slug, errors)
    except Exception as e:  # noqa: BLE001 -- a parser raising past its error channel
        # The parsers catch HTTP and network errors, but a malformed body (invalid
        # JSON, a non-object job entry) raises. That is still a failed read, never
        # "0 roles" (Codex/Grok r1 F3, 2026-11-18).
        errors.append({"reason": f"{type(e).__name__}: {e}"})
        roles = []
    return filter_title(roles, title), errors


def filter_title(roles: list[dict], title: str | None) -> list[dict]:
    """Keep roles whose title matches `title` (case-insensitive regex); all when None."""
    if not title:
        return roles
    pattern = re.compile(title, re.IGNORECASE)
    return [r for r in roles if pattern.search(str(r.get("title", "") or ""))]


def format_line(role: dict) -> str:
    def s(key: str) -> str:
        v = role.get(key, "")
        return "" if v is None else str(v)
    parts = [s("title"), s("location")]
    if "compensation_summary" in role:
        parts.append(s("compensation_summary") or "no pay posted")
    parts.append(s("url"))
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Read one public ATS job board via the project parsers.")
    p.add_argument("ats", choices=["ashby", "greenhouse", "lever"])
    p.add_argument("slug")
    p.add_argument("--comp", action="store_true", help="Ashby only: include id and pay summary")
    p.add_argument("--title", help="case-insensitive regex filter on the title")
    p.add_argument("--json", action="store_true", help="print role dicts as JSON")
    args = p.parse_args(argv)

    if args.comp and args.ats != "ashby":
        print("--comp is Ashby only (Greenhouse and Lever boards publish no pay field here)",
              file=sys.stderr)
        return 2
    if args.title:
        try:
            re.compile(args.title)
        except re.error as e:
            print(f"--title is not a valid regex: {e}", file=sys.stderr)
            return 2

    board, errors = read_board(args.ats, args.slug, comp=args.comp)
    if errors:
        reasons = "; ".join(e.get("reason", "?") for e in errors)
        print(f"FETCH FAILED for {args.ats}/{args.slug}: {reasons}. "
              f"A wrong slug fails this way; it is not an empty board.", file=sys.stderr)
        return 1
    roles = filter_title(board, args.title)
    if args.json:
        print(json.dumps(roles, ensure_ascii=False, indent=1))
    else:
        # The count carries its population (every role on the board) and the title
        # filter as a named exclusion, so "1 role" cannot be read as "1 role posted".
        excluded = {f"title not matching /{args.title}/": len(board) - len(roles)} \
            if args.title else {}
        report(f"roles on the {args.ats}/{args.slug} board", population=len(board),
               included=len(roles), excluded=excluded)
        for r in roles:
            print("- " + format_line(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
