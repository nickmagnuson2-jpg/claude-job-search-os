# Baseline Snapshots

Golden reference outputs captured on 2026-02-28 against the live repo data.

Used to verify that new preprocessing scripts produce consistent output and
that existing scripts haven't regressed after refactors.

## Files

| File | Script | Captured |
|------|--------|---------|
| `pipeline_staleness.json` | `tools/pipeline_staleness.py` | 2026-02-28 |
| `dossier_freshness.json` | `tools/dossier_freshness.py` | 2026-02-28 |
| `outreach_pending.json` | `tools/outreach_pending.py` | 2026-02-28 |
| `networking_followup.json` | `tools/networking_followup.py` | 2026-02-28 |
| `todo_daily_metrics.json` | `tools/todo_daily_metrics.py` | 2026-02-28 |

## Usage

These snapshots are **reference-only** — they are not loaded by the test suite automatically.
They serve as a manual sanity check after major refactors:

```bash
PYTHONIOENCODING=utf-8 python tools/pipeline_staleness.py --target-date 2026-02-28 --repo-root . | diff - tests/fixtures/baseline/pipeline_staleness.json
```

Note: date-sensitive fields (days_since_update, days_old) will differ when run on a later date.
