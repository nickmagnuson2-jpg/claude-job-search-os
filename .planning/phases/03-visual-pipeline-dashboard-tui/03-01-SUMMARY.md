---
phase: 03-visual-pipeline-dashboard-tui
plan: 01
subsystem: tools/dashboard
tags: [tui, textual, pipeline, dashboard, visualization]
dependency_graph:
  requires: [tools/pipe_read.py, data/job-pipeline.md, data/job-todos.md]
  provides: [tools/dashboard/app.py, tools/dashboard/pipeline_data.py]
  affects: [requirements.txt]
tech_stack:
  added: [textual-8.2.3, rich]
  patterns: [textual-app, collapsible-sections, datatable, staleness-coloring]
key_files:
  created:
    - tools/dashboard/__init__.py
    - tools/dashboard/pipeline_data.py
    - tools/dashboard/app.py
    - tools/dashboard/app.tcss
  modified:
    - requirements.txt
decisions:
  - "Used 'slash' binding name instead of '/' for Textual key binding compatibility"
  - "Stage order: Offer > Interviewing > Interview > Phone Screen > Screening > Applied > Researching"
  - "Rich Text objects with inline styles for cell-level staleness coloring"
metrics:
  duration_seconds: 148
  completed: "2026-04-09T22:09:40Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 1
---

# Phase 03 Plan 01: Core Pipeline Dashboard Summary

Textual TUI dashboard reading job-pipeline.md with stage-grouped collapsible tables, staleness coloring (red/yellow), stale-first sorting, and todo cross-reference in Next Action column.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Install Textual and create data loading layer | 7767e0c | pipeline_data.py, requirements.txt |
| 2 | Build Textual dashboard app with stage-grouped tables | bc8c804 | app.py, app.tcss |

## Implementation Details

### Data Layer (pipeline_data.py)

- `load_dashboard_data(repo_root)` - Loads pipeline and todo data, groups by stage, enriches with todo cross-references
- `get_staleness_level(entry)` - Returns "stale"/"warning"/"fresh" for coloring decisions
- Reuses `pipe_read.parse_pipeline()` for active entries, adds separate parser for all entries (including terminal stages for future funnel view)
- Todo matching: case-insensitive company name match against pending task text

### App Layer (app.py)

- `PipelineDashboard(App)` - Textual app with Header, Footer, funnel summary bar, hidden search input
- Collapsible sections per stage with entry count in title
- DataTable per stage with columns: Company, Role, Date Updated, Days Stale, Next Action
- Staleness coloring via Rich Text: `white on dark_red` (stale), `black on yellow` (warning)
- Stale entries sort to top within each stage group
- View-only: no write imports (D-04 compliant)
- Data loaded once on mount (D-06 compliant)

### Bindings

- `q` - Quit
- `/` (slash) - Toggle search input

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Restored accidentally deleted planning files**
- **Found during:** Task 1 commit
- **Issue:** git reset --soft staged deletion of planning files from parent commit
- **Fix:** Restored files from parent commit 3db9d20
- **Commit:** 056b911

## Threat Flags

None - dashboard is read-only, local-only TUI with no network access or data modification.

## Known Stubs

None - all data sources are wired to live pipeline/todo files.

## Self-Check: PASSED

All 4 created files verified on disk. All 3 commits (7767e0c, 056b911, bc8c804) verified in git log.
