#!/usr/bin/env python3
"""
schema_guard.py — Shared column-schema guard for positional markdown-table parsers.

Why this exists (2026-06-08 incident): `pipeline_staleness.py` read the staleness
date from the wrong column (`cols[4]` — Next Action prose — instead of `cols[3]` —
Date Updated) after the live `data/job-pipeline.md` header (written by
`tools/pipe_write.py`) drifted from what the parser's `cols[N]` indices assumed.
Unit tests stayed green because the test fixtures encoded the SAME stale header the
parser expected, not the live file's actual header — the fixtures mirrored the bug
instead of catching it. `days_since_update` silently returned `null` for every real
row for an unknown duration; nothing ever flagged stalled and /standup silently
under-surfaced. See memory/feedback_verify_parser_against_live_schema.md.

Usage — call once per file, before the parse loop, with the file's actual header
line and the columns the parser's `cols[N]` indices assume:

    from schema_guard import assert_schema, SchemaDriftError, find_header_line

    header = find_header_line(content, "| Company |")
    assert_schema(header, ["Company", "Role", "Stage", "Date Updated",
                            "Next Action", "CV Used", "Notes", "URL"])

A drifted schema raises `SchemaDriftError` naming exactly what changed (missing
column, reordered column, unexpected new column) instead of the parser silently
mis-reading `cols[N]` into the wrong field.
"""
from __future__ import annotations


class SchemaDriftError(Exception):
    """Raised when a live file's table header no longer matches a parser's assumed schema."""


def _split_header(header_line: str) -> list[str]:
    return [c.strip() for c in header_line.strip().strip("|").split("|")]


def find_header_line(content: str, starts_with: str) -> str | None:
    """Return the first line in `content` that starts with `starts_with` (the raw
    header row, e.g. "| Company |"). Returns None if not found — callers should
    treat that as "file empty / no rows yet" rather than a drift error, since an
    empty or freshly-scaffolded file has no header to check yet."""
    for line in content.splitlines():
        if line.strip().startswith(starts_with):
            return line
    return None


def assert_schema(header_line: str | None, expected_columns: list[str]) -> None:
    """Compare a live table header against the parser's expected column order.

    Raises SchemaDriftError naming exactly what changed:
      - missing column(s) the parser relies on
      - unexpected new column(s) the parser doesn't account for
      - reordered columns (same set, different positions — the dangerous silent case,
        since cols[N] then reads a different field with no error)

    Passes silently (no return value) when header_line is None (nothing to check yet)
    or when the live columns exactly match expected_columns in name and order.
    """
    if header_line is None:
        return

    actual_columns = _split_header(header_line)

    if actual_columns == expected_columns:
        return

    expected_set = set(expected_columns)
    actual_set = set(actual_columns)

    missing = [c for c in expected_columns if c not in actual_set]
    unexpected = [c for c in actual_columns if c not in expected_set]

    if missing or unexpected:
        parts = []
        if missing:
            parts.append(f"missing column(s) {missing}")
        if unexpected:
            parts.append(f"unexpected new column(s) {unexpected}")
        raise SchemaDriftError(
            f"Schema drift detected: {', '.join(parts)}. "
            f"Expected columns {expected_columns}, got {actual_columns}. "
            f"The parser's cols[N] indices no longer match the live file's header — "
            f"fix the parser (or the writer) before trusting its output."
        )

    # Same set of column names, different order — the silent-misparse case.
    raise SchemaDriftError(
        f"Schema drift detected: columns reordered. "
        f"Expected order {expected_columns}, got {actual_columns}. "
        f"cols[N] indices will silently read the wrong field for every row — "
        f"fix the parser's column indices to match the live order before trusting its output."
    )


if __name__ == "__main__":
    # Minimal self-check when run directly (not the full test suite — see
    # tools/test_schema_guard.py for the real regression tests).
    ok_header = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
    assert_schema(ok_header, ["Company", "Role", "Stage", "Date Updated",
                              "Next Action", "CV Used", "Notes", "URL"])
    try:
        assert_schema(
            "| Company | Role | Stage | Date Added | Date Updated | CV Used | URL | Notes |",
            ["Company", "Role", "Stage", "Date Updated", "Next Action", "CV Used", "Notes", "URL"],
        )
        raise SystemExit("expected SchemaDriftError, got none")
    except SchemaDriftError:
        pass
    print("schema_guard self-check OK")
