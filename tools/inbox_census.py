#!/usr/bin/env python3
"""
inbox_census.py — comment-span-aware census of data/inbox.md.

This is the ORACLE for every later inbox step (spec section 3.8). It is the only
number in the system that does not come from the code under test, which is what
turns "self-consistent" into "correct".

Why it exists: a naive `line.startswith("## ")` scan reported 150 live headers and
34 career-scan blocks. A 372-line HTML comment span wraps three of those headers.
The true split is 147 live / 3 commented and 31 live career-scan blocks. That
contaminated arithmetic was load-bearing for extraction and removal — removal would
have keyed on a block list that did not match the file.

Comment interiors are NON-BLOCK TERRITORY: excluded from the census, from
extraction, and from removal, and reported separately.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/inbox_census.py --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/inbox_census.py --repo-root . --write

Output: JSON to stdout.
  Success: {"status": "ok", "live_h2_headers": N, ...}
  Failure: {"status": "error", "message": "...", "code": "..."}

Exits non-zero on error, including the fail-open guards: unbalanced comment markers
and an empty census both abort rather than reporting a clean zero.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

INBOX_REL = "data/inbox.md"

# Block classification, FIRST MATCH WINS. Order is load-bearing: an agent-drip
# header (`## 2026-08-10 | Agent drip: lane-a (company)`) also matches the bare
# dated-capture pattern, so it must be tested first or it is double-counted.
# Keyed on header TEXT, not on the `<!-- review-gated -->` comment marker, because
# those markers have already drifted in the live file (orphaned copies at lines 5-7,
# strays at 2656-2657).
BLOCK_RULES = [
    ("agent_drips",    re.compile(r"^##\s+.*Agent drip:", re.I)),
    ("career_scan",    re.compile(r"^##\s+Career Scan Results\s*$", re.I)),
    ("call_debrief",   re.compile(r"^##\s+Call Debrief\b", re.I)),
    ("dated_captures", re.compile(r"^##\s+\d{4}-\d{2}-\d{2}")),
]


def out_ok(**fields) -> None:
    print(json.dumps({"status": "ok", **fields}, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    print(json.dumps({"status": "error", "message": message, "code": code, **extra},
                     ensure_ascii=False))
    sys.exit(1)


def find_comment_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Return 1-indexed inclusive [start, end] line ranges of multi-line HTML comments.

    A `<!-- ... -->` that opens and closes on one line is NOT a span; treating it as
    one would swallow the rest of the file (the live inbox is full of single-line
    `<!-- voice: ... -->` and `<!-- review-gated: ... -->` markers).
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for i, line in enumerate(lines, start=1):
        if open_at is None:
            idx = line.find("<!--")
            if idx != -1 and "-->" not in line[idx:]:
                open_at = i
        else:
            if "-->" in line:
                spans.append((open_at, i))
                open_at = None
    if open_at is not None:
        out_error(
            f"unbalanced HTML comment: '<!--' opened at line {open_at} is never "
            f"closed. Every downstream line range is untrustworthy until this is "
            f"fixed by hand.",
            "unbalanced_comment", open_at=open_at,
        )
    return spans


def in_any_span(lineno: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= lineno <= e for s, e in spans)


def census(text: str) -> dict:
    lines = text.splitlines()
    spans = find_comment_spans(lines)

    counts = {k: 0 for k, _ in BLOCK_RULES}
    counts["unrecognised"] = 0
    live_headers = 0
    commented_headers = 0
    commented_scan = 0

    for i, line in enumerate(lines, start=1):
        if not line.startswith("## "):
            continue
        if in_any_span(i, spans):
            commented_headers += 1
            if BLOCK_RULES[1][1].match(line):   # career_scan
                commented_scan += 1
            continue
        live_headers += 1
        for key, rx in BLOCK_RULES:
            if rx.match(line):
                counts[key] += 1
                break
        else:
            counts["unrecognised"] += 1

    span_bytes = sum(
        len("\n".join(lines[s - 1:e]).encode("utf-8")) for s, e in spans
    )

    return {
        "taken": date.today().strftime("%Y-%m-%d"),
        "live_h2_headers": live_headers,
        "commented_h2_headers": commented_headers,
        "career_scan_live": counts["career_scan"],
        "commented_scan_headers": commented_scan,
        "comment_spans": [list(s) for s in spans],
        "comment_span_bytes": span_bytes,
        "call_debrief": counts["call_debrief"],
        "dated_captures": counts["dated_captures"],
        "agent_drips": counts["agent_drips"],
        "unrecognised": counts["unrecognised"],
        "total_lines": len(lines),
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Comment-aware census of data/inbox.md.")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--write", action="store_true",
                    help="Persist the census to output/career-scan/.census-<date>.json")
    args = ap.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    inbox = repo_root / INBOX_REL
    if not inbox.is_file():
        out_error(f"inbox not found: {inbox}", "file_not_found")

    text = inbox.read_text(encoding="utf-8")
    result = census(text)

    # Fail-open guard, per the trim-context-file precedent: a census that finds no
    # blocks at all is a broken parse, not a clean file. Never report that as ok.
    if result["live_h2_headers"] == 0:
        out_error(
            "census found zero live '## ' headers — this is a parse failure, not an "
            "empty inbox. Refusing to emit an oracle that every later step would "
            "assert against.",
            "empty_census", total_lines=result["total_lines"],
        )

    if args.write:
        out_dir = repo_root / "output" / "career-scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f".census-{result['taken']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
        result["census_path"] = str(path)

    out_ok(**result)


if __name__ == "__main__":
    main()
