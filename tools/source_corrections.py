#!/usr/bin/env python3
"""source_corrections.py — surface the dated corrections attached to source-project bullets.

Source project files carry their honesty history in HTML comments pinned to the bullet they
correct:

    - Facilitated rhythm-of-business ... <!-- "Facilitated" not "Stood up": the processes
      existed; the CoS role was coordinating them. Lesson #17. -->

That is the right place for it -- the correction sits with the claim. But it is also invisible
in exactly the moment it matters: while paraphrasing the bullet into a CV line, a cover-letter
sentence, or an application answer, the eye reads the claim and skips the comment. It has
happened twice, and both times the file had been read in full in the same session.

So the check stops being an act of attention and becomes a command. Run this against every
source file you are about to draft from, BEFORE writing prose, and reconcile each correction
against what you are about to say.

Exit codes: 0 ran | 2 empty scope (no readable file) -- an error, never a clean result.
Empty scope is a hard failure for the same reason it is in sweep.py: a scan that read nothing
must never be reportable as a scan that found nothing.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Markers seen in the live corpus (2026-08-13: zuora.md, espn.md, mckinsey.md). This list is
# advisory -- it drives the `marker` label only. EVERY HTML comment is reported regardless,
# because an unlabelled correction is still a correction, and a whitelist of marker words is
# exactly the kind of filter that silently drops the one case nobody anticipated.
KNOWN_MARKERS = [
    "HONESTY CORRECTION", "CORRECTED", "CORRECTION", "SCOPE FLAG",
    "ENRICHED", "OVERSTATED", "NEEDS REF", "NICK'S CALL", "ADDED", "Lesson #",
]

# Template scaffolding from framework/templates/project.md -- an unfilled prompt, not a
# correction. Separated in the output rather than dropped: it is still a gap in the source
# file, and a tool that silently swallows 6 of its 20 hits has taught you to trust a number
# that is not the number.
SCAFFOLD_RE = re.compile(r"^\s*TODO\b", re.I)

COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")


def label(comment: str) -> str | None:
    upper = comment.upper()
    for m in KNOWN_MARKERS:
        if m.upper() in upper:
            return m
    return None


def scan_file(path: Path) -> list[dict]:
    """Every HTML comment in the file, with the bullet it annotates when there is one."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Offset -> line number, so a correction can be cited as file:line.
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1

    def line_no(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    out = []
    for m in COMMENT_RE.finditer(text):
        n = line_no(m.start())
        # The bullet is whatever bullet text precedes the comment ON ITS OWN LINE. A comment on
        # its own line (a section header like HONESTY CORRECTIONS) annotates the whole section,
        # not one claim, and gets bullet=None rather than being wrongly pinned to a neighbour.
        raw = lines[n - 1] if 0 < n <= len(lines) else ""
        before = raw[: m.start() - starts[n - 1]] if 0 < n <= len(lines) else ""
        bm = BULLET_RE.match(before)
        bullet = bm.group(1).strip() if bm and bm.group(1).strip() else None
        body = " ".join(m.group(1).split())
        out.append({
            "file": str(path),
            "line": n,
            "marker": label(m.group(1)),
            "comment": body,
            "bullet": bullet,
            "kind": "scaffold" if SCAFFOLD_RE.match(body) else "correction",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Surface dated corrections in source project files before drafting from them.")
    ap.add_argument("paths", nargs="+", help="source project markdown files")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    corrections, scanned, unreadable = [], 0, []
    for raw in args.paths:
        p = Path(raw)
        if not p.is_file():
            unreadable.append(raw)
            continue
        scanned += 1
        corrections.extend(scan_file(p))

    if scanned == 0:
        payload = {"error": "empty scope: no readable file among the given paths",
                   "unreadable": unreadable, "scanned": 0}
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    real = [c for c in corrections if c["kind"] == "correction"]
    scaffold = [c for c in corrections if c["kind"] == "scaffold"]

    payload = {
        "scanned": scanned,
        "unreadable": unreadable,
        "correction_count": len(corrections),
        "real_correction_count": len(real),
        "scaffold_count": len(scaffold),
        "corrections": corrections,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not corrections:
        print(f"No corrections found. Scanned {scanned} file(s) — "
              f"that is a real zero, against a real denominator.")
        return 0

    print(f"{len(real)} correction(s) across {scanned} file(s)"
          + (f", plus {len(scaffold)} unfilled template TODO(s) listed after." if scaffold else ".")
          + "\nReconcile each against the prose you are about to write.\n")
    for c in real + scaffold:
        tag = f"[{c['marker']}] " if c["marker"] else ""
        print(f"{Path(c['file']).name}:{c['line']} {tag}")
        if c["bullet"]:
            print(f"    CLAIM     {c['bullet'][:160]}")
        else:
            print("    CLAIM     (section-level — applies to the whole block)")
        print(f"    CORRECTION {c['comment'][:300]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
