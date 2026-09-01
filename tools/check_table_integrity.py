#!/usr/bin/env python3
"""
check_table_integrity.py - PostToolUse hook (Bash|Write|Edit|MultiEdit) + standalone scanner.

Catches a markdown-table row whose pipe-delimited field count differs from the rest of
its own table. That is the signature of an unescaped "|" inside a cell: every column
after the stray pipe shifts right by one, so a row's URL lands in Notes, its Notes land
in CV Used, and so on.

WHY THIS IS A HOOK AND NOT A CONVENTION
---------------------------------------
The corruption is silent in every direction:

  1. The file still renders as a table in Obsidian and on GitHub. Nothing looks wrong.
  2. Parsers that read by column INDEX return the wrong value with no error. They do not
     raise, they do not warn, they return a plausible string from the neighbouring cell.
  3. Parsers that read by column NAME (via the header row) silently disagree with the
     index readers, so two tools report different values for the same row.
  4. Nothing downstream ever flags it.

Found 2026-08-31 on a row written months earlier: one pipeline row carried 11 fields
where every other row carried 10, and its URL column held a provenance note. It surfaced
only because a table-integrity check was run by hand while verifying an unrelated write.
The originating bug (a writer interpolating " | " directly into a Notes cell) had been
shipping malformed rows the whole time.

DETECTION RULE - self-calibrating, no hardcoded schema
------------------------------------------------------
Group contiguous runs of lines that start with "|". Inside each run, every row must have
the same field count; the modal count is taken as that table's arity. A run shorter than
MIN_TABLE_ROWS is not treated as a table (a lone "|" line in prose is not a schema
violation). This deliberately avoids a per-file column map: the guard stays correct when
a table gains or loses a column, and needs no update when a new table is added.

Escaped pipes ("\\|") are removed before counting - they are literal content in markdown,
not separators, and counting them would produce false positives on legitimately escaped
rows.

FAIL-OPEN, ALWAYS
-----------------
Any unexpected exception exits 0. A guard that crashes the workflow it protects is worse
than the corruption it looks for, and this one runs on every Bash/Write/Edit call.
Measured cost on the live data files: ~1.2 ms per pass.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Pipe-delimited data tables written by tooling and read by column position.
# Paths are repo-relative. A missing file is skipped, never an error.
GUARDED_FILES = (
    "data/job-pipeline.md",
    "data/networking.md",
    "data/job-todos.md",
)

# A run of fewer than this many "|" lines is prose, not a table. A real markdown table is
# at minimum a header row, a separator row, and one data row.
MIN_TABLE_ROWS = 3

_ESCAPED_PIPE = re.compile(r"\\\|")


def field_count(line: str) -> int:
    """Pipe-delimited field count for one row, ignoring escaped pipes."""
    return len(_ESCAPED_PIPE.sub("", line).split("|"))


def find_malformed_rows(text: str) -> list:
    """
    Return [(line_number, actual_field_count, expected_field_count), ...] for rows whose
    field count differs from the modal count of their own contiguous table run.

    line_number is 1-indexed to match editor and grep output.
    """
    lines = text.split("\n")
    findings = []
    run = []  # (line_index, field_count)

    def flush(current_run):
        if len(current_run) < MIN_TABLE_ROWS:
            return
        counts = Counter(c for _, c in current_run)
        expected, _ = counts.most_common(1)[0]
        for idx, c in current_run:
            if c != expected:
                findings.append((idx + 1, c, expected))

    for i, line in enumerate(lines):
        if line.startswith("|"):
            run.append((i, field_count(line)))
        else:
            flush(run)
            run = []
    flush(run)

    return findings


def scan_files(repo_root: Path) -> dict:
    """Validate every guarded file that exists. Returns {relpath: findings}."""
    out = {}
    for rel in GUARDED_FILES:
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings = find_malformed_rows(text)
        if findings:
            out[rel] = findings
    return out


def _format_report(results: dict) -> str:
    parts = ["BLOCKED: malformed markdown table row(s) detected.", ""]
    for rel, findings in results.items():
        parts.append("  " + rel)
        for line_no, actual, expected in findings:
            parts.append(
                "    line {}: {} pipe-delimited fields, expected {} "
                "(the rest of that table)".format(line_no, actual, expected)
            )
    parts += [
        "",
        "A row with the wrong field count means an unescaped '|' inside a cell. Every",
        "column after it shifts right by one, so tools reading by column position get a",
        "value from the neighbouring cell - silently, with no error anywhere downstream.",
        "",
        "Fix: find the stray '|' on that line and either remove it or fold the fragment",
        "back into the cell it belongs to. When writing cells from code, route the value",
        "through sanitize_cell() in tools/pipe_write.py - the single source of truth for",
        "escaping, which replaces '|' with '/'.",
        "",
        "Re-check: PYTHONIOENCODING=utf-8 python3 tools/check_table_integrity.py --scan",
    ]
    return "\n".join(parts)


def main() -> None:
    try:
        argv = sys.argv[1:]

        if "--scan" in argv:
            root = Path.cwd()
            if "--repo-root" in argv:
                root = Path(argv[argv.index("--repo-root") + 1])
            results = scan_files(root)
            if results:
                print(_format_report(results))
                sys.exit(1)
            checked = [f for f in GUARDED_FILES if (root / f).is_file()]
            print("OK - no malformed rows in {} guarded file(s): {}".format(
                len(checked), ", ".join(checked)))
            sys.exit(0)

        # Hook mode: read the PostToolUse payload, validate, report via exit 2.
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        payload = json.loads(raw)
        root = Path(payload.get("cwd") or Path.cwd())

        results = scan_files(root)
        if results:
            print(_format_report(results), file=sys.stderr)
            sys.exit(2)

    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, always


if __name__ == "__main__":
    main()
