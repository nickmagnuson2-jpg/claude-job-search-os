# Phase 3: Visual Pipeline Dashboard (TUI) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md - this log preserves the alternatives considered.

**Date:** 2026-04-09
**Phase:** 03-visual-pipeline-dashboard-tui
**Areas discussed:** Dashboard scope & layout, Interactivity depth, Staleness & alerts, Conversion funnel

---

## Dashboard scope & layout

### Main view

| Option | Description | Selected |
|--------|-------------|----------|
| Full table | All columns grouped by stage with counts. Max info density. | ✓ |
| Compact cards | One card per application. Less tabular, more visual. | |
| Kanban columns | Stage columns side by side (Trello-style). | |

**User's choice:** Full table
**Notes:** User selected the preview with grouped-by-stage rows and header stats.

### Stats placement

| Option | Description | Selected |
|--------|-------------|----------|
| Header stats only | Summary in header bar, full width for table. | ✓ |
| Right sidebar | Funnel + stats in right panel, table takes ~70% width. | |
| Toggle view | Full-width default, press 'f' for funnel overlay. | |

**User's choice:** Header stats only
**Notes:** None

### Row grouping

| Option | Description | Selected |
|--------|-------------|----------|
| Grouped by stage | Collapsible stage sections with counts. | ✓ |
| Flat sortable table | All rows in one table, sortable by any column. | |
| You decide | Claude picks based on Textual widget system. | |

**User's choice:** Grouped by stage
**Notes:** None

---

## Interactivity depth

### Action level

| Option | Description | Selected |
|--------|-------------|----------|
| View-only | Read-only dashboard. Actions via Claude Code skills. | ✓ |
| Light actions | View + open URL, copy company, open notes. No mutations. | |
| Full CRUD | Update stage, add todos, edit next action from dashboard. | |

**User's choice:** View-only
**Notes:** User confirmed the key binding preview (arrows, Tab, 's', '/', 'q').

### Refresh behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Static snapshot | Reads on launch, relaunch to update. | ✓ |
| Auto-refresh | Watch file for changes, re-render automatically. | |
| You decide | Claude picks based on Textual support. | |

**User's choice:** Static snapshot
**Notes:** None

---

## Staleness & alerts

### Highlight style

| Option | Description | Selected |
|--------|-------------|----------|
| Row color coding | Red for 7+ days, yellow for 3-7 days. | ✓ |
| Stale badge/icon | Warning icon next to stale entries. | |
| Both | Color rows AND badge icons. | |

**User's choice:** Row color coding
**Notes:** User selected the preview showing red/yellow/default row backgrounds.

### Sort order within stage

| Option | Description | Selected |
|--------|-------------|----------|
| Stale first | Entries needing attention float to top. | ✓ |
| Chronological | Sorted by date, stale highlighted but in date order. | |
| You decide | Claude picks default sort. | |

**User's choice:** Stale first
**Notes:** None

---

## Conversion funnel

### Metrics format

| Option | Description | Selected |
|--------|-------------|----------|
| Stage counts + rates | "24 Applied > 8 Interview (33%) > 2 Offer (25%)" | ✓ |
| Counts only | Just stage counts without percentages. | |
| Full metrics row | Counts + rates + response rate + avg time-in-stage. | |

**User's choice:** Stage counts + rates
**Notes:** None

### Funnel scope

| Option | Description | Selected |
|--------|-------------|----------|
| All-time totals | Include withdrawn/rejected in denominators. | ✓ |
| Active only | Only currently active entries. | |
| You decide | Claude picks most useful approach. | |

**User's choice:** All-time totals
**Notes:** None

---

## Claude's Discretion

- Textual widget choices and CSS styling
- Exact key bindings beyond specified ones
- Collapsible section implementation approach
- Footer content
- Search implementation details

## Deferred Ideas

None - discussion stayed within phase scope
