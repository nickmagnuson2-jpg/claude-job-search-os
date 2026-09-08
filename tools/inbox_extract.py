#!/usr/bin/env python3
"""
inbox_extract.py — per-block extractor for data/inbox.md, oracle-checked.

Emits one JSONL row per LIVE `## ` block with a stable id, the header text, the
line range, size, a bounded preview, and the producer class. Never prints the
corpus: the inbox is ~7,000 lines and reading it into a session is what forced
the 2026-09-08 handoff. Slice-read the output file instead.

The producer classification is NOT re-implemented here. `inbox_census.py` is the
oracle (spec section 3.8) and this module imports its comment-span scanner and
its BLOCK_RULES so the two cannot drift. After extraction the per-producer
counts are asserted against a live `census()` call; a mismatch aborts rather
than emitting rows that disagree with the oracle.

Comment-span interiors are non-block territory, same rule as the census: their
headers are not blocks and their lines are excluded from every body.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/inbox_extract.py --repo-root . \
      --out output/career-scan/inbox-blocks.jsonl

Output: JSON summary to stdout; one JSON object per line to --out.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inbox_census import (  # noqa: E402
    BLOCK_RULES,
    INBOX_REL,
    census,
    find_comment_spans,
    in_any_span,
)

SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slug(header_text: str, maxlen: int = 48) -> str:
    s = SLUG_STRIP.sub("-", header_text.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "untitled"


def producer_of(header_line: str) -> str:
    """Producer class for a live header, using the census rules verbatim.

    First match wins and the rule ORDER is load-bearing: an agent-drip header
    also matches the bare dated-capture pattern.
    """
    for key, rx in BLOCK_RULES:
        if rx.match(header_line):
            return "career_scan_live" if key == "career_scan" else key
    return "unrecognised"


def preview(body_lines: list[str], n: int) -> list[str]:
    out = []
    for line in body_lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        out.append(s[:300])
        if len(out) >= n:
            break
    return out


def extract(text: str, preview_lines: int = 6) -> list[dict]:
    lines = text.splitlines()
    spans = find_comment_spans(lines)

    heads = [(i, line) for i, line in enumerate(lines, start=1)
             if line.startswith("## ") and not in_any_span(i, spans)]

    blocks: list[dict] = []
    for n, (start, header_line) in enumerate(heads):
        end = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines)
        # Excluding span lines matters most for the LAST block: the commented
        # headers that would have bounded it are deliberately absent from
        # `heads`, so without this it swallows a trailing comment span.
        body_lines = [lines[k - 1] for k in range(start + 1, end + 1)
                      if not in_any_span(k, spans)]
        header_text = header_line[3:].strip()
        blocks.append({
            "block_id": f"L{start:05d}-{slug(header_text)}",
            "line_start": start,
            "line_end": end,
            "lines": end - start + 1,
            "body_chars": len("\n".join(body_lines)),
            "header": header_text,
            "producer": producer_of(header_line),
            "preview": preview(body_lines, preview_lines),
            "sha256": hashlib.sha256(
                "\n".join([header_line] + body_lines).encode("utf-8")).hexdigest(),
        })
    return blocks


def fail(message: str, code: str, **extra) -> None:
    print(json.dumps({"status": "error", "message": message, "code": code, **extra},
                     ensure_ascii=False))
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-block extractor for data/inbox.md.")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preview-lines", type=int, default=6)
    args = ap.parse_args()

    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    inbox = root / INBOX_REL
    if not inbox.is_file():
        fail(f"inbox not found: {inbox}", "file_not_found")

    text = inbox.read_text(encoding="utf-8")
    oracle = census(text)
    blocks = extract(text, args.preview_lines)

    if not blocks:
        fail("extractor found zero live blocks — a parse failure, not an empty "
             "inbox. Refusing to emit rows nothing can be asserted against.",
             "empty_extraction")

    # Oracle assertion. These counts come from two independent walks of the same
    # rules; if they disagree, one of them is wrong and neither may be used.
    got = {"career_scan_live": 0, "call_debrief": 0, "dated_captures": 0,
           "agent_drips": 0, "unrecognised": 0}
    for b in blocks:
        got[b["producer"]] += 1
    want = {k: oracle[k] for k in got}
    if got != want or len(blocks) != oracle["live_h2_headers"]:
        fail("extractor disagrees with the census oracle", "oracle_mismatch",
             extractor=got, oracle=want,
             extractor_total=len(blocks), oracle_total=oracle["live_h2_headers"])

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for b in blocks:
            fh.write(json.dumps({**b, "corpus_sha256": oracle["source_sha256"]},
                                ensure_ascii=False) + "\n")
    tmp.replace(out_path)

    print(json.dumps({
        "status": "ok",
        "out": str(out_path),
        "blocks": len(blocks),
        "by_producer": got,
        "corpus_sha256": oracle["source_sha256"],
        "total_lines": oracle["total_lines"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
