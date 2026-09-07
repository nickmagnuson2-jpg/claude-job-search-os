#!/usr/bin/env python3
"""
db_sync.py — Push the markdown pipeline into Postgres (Neon) as a DERIVED read model.

Markdown stays canonical. This is a one-way projection: data/job-pipeline.md -> Postgres,
so a hosted read-only console can query what Claude Code writes to disk. Nothing here ever
writes back to markdown, and nothing downstream may treat the database as a source of truth.

Parsing is delegated to pipe_read.parse_all_rows(), which delegates classification to
stage_vocab. This file adds no domain logic of its own on purpose.

Safety: the sync is a full replace inside one transaction. Because a full replace can
destroy the table, an empty or suspiciously small parse HARD ABORTS rather than truncating
(see --min-rows). A projection that silently empties itself looks identical to an empty
pipeline in the UI.

Connection string comes from $JOB_OS_DATABASE_URL. It is never read from, or written to,
any file in this repo.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/db_sync.py [--repo-root PATH] [--dry-run] [--min-rows N]

Exit codes:
  0  synced (or dry-run rendered)
  1  usage / connection / parse failure
  2  refused: parse produced fewer than --min-rows rows
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipe_read import parse_all_rows, read_file  # noqa: E402

ENV_VAR = "JOB_OS_DATABASE_URL"
DEFAULT_MIN_ROWS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline (
    id                SERIAL PRIMARY KEY,
    company           TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT '',
    stage             TEXT NOT NULL DEFAULT '',
    date_updated      DATE,
    next_action       TEXT NOT NULL DEFAULT '',
    cv_used           TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL DEFAULT '',
    days_since_update INTEGER,
    archived          BOOLEAN NOT NULL DEFAULT FALSE,
    stale             BOOLEAN NOT NULL DEFAULT FALSE,
    missing_action    BOOLEAN NOT NULL DEFAULT FALSE,
    needs_attention   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS pipeline_archived_idx ON pipeline (archived);
CREATE INDEX IF NOT EXISTS pipeline_company_idx  ON pipeline (lower(company));

CREATE TABLE IF NOT EXISTS sync_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    synced_at   TIMESTAMPTZ NOT NULL,
    row_count   INTEGER NOT NULL,
    source_file TEXT NOT NULL
);
"""

INSERT = """
INSERT INTO pipeline (company, role, stage, date_updated, next_action, cv_used,
                      notes, url, days_since_update, archived, stale,
                      missing_action, needs_attention)
VALUES (%(company)s, %(role)s, %(stage)s, %(date_updated)s, %(next_action)s,
        %(cv_used)s, %(notes)s, %(url)s, %(days_since_update)s, %(archived)s,
        %(stale)s, %(missing_action)s, %(needs_attention)s)
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Sync markdown pipeline to Postgres (derived read model).")
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument("--dry-run", action="store_true", help="Parse and print JSON; never connect.")
    p.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS,
                   help=f"Refuse to replace the table below this many parsed rows (default {DEFAULT_MIN_ROWS}).")
    p.add_argument("--target-date", help="YYYY-MM-DD, for deterministic staleness in tests.")
    return p.parse_args(argv)


def normalize(row: dict) -> dict:
    """Coerce markdown placeholders ('—', '') into DB-shaped values."""
    out = dict(row)
    for key in ("role", "stage", "next_action", "cv_used", "notes", "url"):
        val = (out.get(key) or "").strip()
        out[key] = "" if val == "—" else val
    raw = (out.get("date_updated") or "").strip()
    try:
        out["date_updated"] = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        out["date_updated"] = None
    return out


def load_rows(repo_root: Path, today: date) -> list[dict]:
    source = repo_root / "data" / "job-pipeline.md"
    if not source.exists():
        raise FileNotFoundError(f"pipeline not found: {source}")
    return [normalize(r) for r in parse_all_rows(read_file(source), today)]


def sync(rows: list[dict], dsn: str, source_file: str) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("DELETE FROM pipeline")
            cur.executemany(INSERT, rows)
            cur.execute(
                """
                INSERT INTO sync_meta (id, synced_at, row_count, source_file)
                VALUES (1, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                  SET synced_at = EXCLUDED.synced_at,
                      row_count = EXCLUDED.row_count,
                      source_file = EXCLUDED.source_file
                """,
                (datetime.now(timezone.utc), len(rows), source_file),
            )
        conn.commit()
    return len(rows)


def main(argv=None) -> int:
    args = parse_args(argv)
    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    try:
        rows = load_rows(args.repo_root, today)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Hard abort, never a warning: a full replace on a bad parse silently empties the console.
    if len(rows) < args.min_rows:
        print(
            f"REFUSED: parsed {len(rows)} row(s), below --min-rows={args.min_rows}. "
            "Refusing to replace the table. Check data/job-pipeline.md before retrying.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(json.dumps(
            {"row_count": len(rows),
             "active": sum(1 for r in rows if not r["archived"]),
             "archived": sum(1 for r in rows if r["archived"]),
             "stale": sum(1 for r in rows if r["stale"]),
             "sample": [{k: str(v)[:60] for k, v in rows[0].items()}] if rows else []},
            indent=2, default=str))
        return 0

    dsn = os.environ.get(ENV_VAR, "").strip()
    if not dsn:
        print(f"ERROR: ${ENV_VAR} is not set. Export the Neon connection string and retry.",
              file=sys.stderr)
        return 1

    try:
        n = sync(rows, dsn, "data/job-pipeline.md")
    except Exception as exc:  # noqa: BLE001 - surface any driver/network failure verbatim
        print(f"ERROR: sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Synced {n} rows to Postgres "
          f"({sum(1 for r in rows if not r['archived'])} active, "
          f"{sum(1 for r in rows if r['archived'])} archived).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
