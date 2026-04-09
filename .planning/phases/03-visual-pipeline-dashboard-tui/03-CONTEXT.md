# Phase 3: Visual Pipeline Dashboard (TUI) - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Terminal-based dashboard showing pipeline by stage, staleness, next actions, and conversion metrics. Replaces reading raw markdown tables. View-only - no data mutations from the dashboard.

</domain>

<decisions>
## Implementation Decisions

### Dashboard Layout
- **D-01:** Full table view - all columns (Company, Role, Stage, Last Updated, Days Stale, Next Action) with rows grouped by collapsible stage sections and counts per stage.
- **D-02:** Header bar contains summary stats (Active count, Interview count, Stale count) and conversion funnel. Full-width table below. No sidebar.
- **D-03:** Rows grouped by stage (Interviewing, Applied, Researching, etc.) with collapsible sections and entry counts per group.

### Interactivity
- **D-04:** View-only dashboard. No data mutations. Actions stay in Claude Code skills (pipe_write.py, todo_write.py). Users exit dashboard to make changes.
- **D-05:** Keyboard navigation: arrow keys to navigate rows, Tab to switch stage groups, 's' to sort within group, '/' to search by company name, 'q' to quit.
- **D-06:** Static snapshot - reads pipeline data on launch, displays it. Relaunch to see updates. No file-watching.

### Staleness & Alerts
- **D-07:** Row color coding for staleness: red background for 7+ days stale, yellow for 3-7 days, default for fresh entries. Uses existing per-stage thresholds from pipe_read.py (Applied: 5d, Interview: 7d, Offer: 3d).
- **D-08:** Within each stage group, stale entries sort to the top. Most actionable items visible first.

### Conversion Funnel
- **D-09:** Header displays stage counts with conversion rates: "24 Applied > 8 Interview (33%) > 2 Offer (25%)". Compact, fits header bar.
- **D-10:** Funnel uses all-time totals including archived/terminal entries (Withdrawn, Rejected) in denominators. Shows true historical conversion rate.

### Claude's Discretion
- Textual widget choices and CSS styling
- Exact key bindings beyond the ones specified above
- How to render collapsible stage sections (TreeView, Collapsible, or custom)
- Footer content (key binding hints, help text)
- Search implementation details (fuzzy vs exact match)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pipeline data parsing
- `tools/pipe_read.py` - Existing pipeline parser with staleness calculation, stage thresholds, attention flags. Outputs structured JSON. Reuse this logic directly.
- `tools/pipeline_staleness.py` - Similar staleness calculation for /standup. Reference for threshold constants and stage distribution.
- `tools/pipe_write.py` - Pipeline mutation script. Dashboard does NOT use this (view-only) but understanding the data format is essential.

### Todo cross-reference
- `tools/todo_write.py` - Todo data format. Dashboard reads job-todos.md for "Next Action" column enrichment.

### Data files
- `data/job-pipeline.md` - Source data file. 8-column markdown table: Company, Role, Stage, Date Updated, Next Action, CV Used, Notes, URL.
- `data/job-todos.md` - Todo items cross-referenced for next actions per application.

### Textual framework
- Textual docs (https://textual.textualize.io/) - TUI framework. Researcher should verify current API for DataTable, Header, Footer, collapsible widgets.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tools/pipe_read.py`: Complete pipeline parser - reads job-pipeline.md, computes staleness per entry, flags attention-needed items, builds stage distribution and company index. Dashboard should call this or reuse its parsing logic directly.
- `tools/pipeline_staleness.py`: Similar staleness engine used by /standup. Shares threshold constants with pipe_read.py.
- Per-stage staleness thresholds already defined: Researching: 7d, Applied: 5d, Phone Screen: 5d, Screening: 5d, Interview: 7d, Offer: 3d, Default: 7d.
- Terminal stages already defined: Withdrawn, Rejected, Accepted, Archived.

### Established Patterns
- Python tools use argparse CLI, JSON to stdout, errors to stderr
- All Python scripts require `PYTHONIOENCODING=utf-8` prefix
- `.env` auto-loading via `_load_dotenv()` pattern (not needed for dashboard but worth noting)
- Data files are markdown tables parsed with regex line-by-line

### Integration Points
- Dashboard reads `data/job-pipeline.md` (same file pipe_read.py parses)
- Dashboard reads `data/job-todos.md` for next-action cross-reference
- No writes - view-only. Users run pipe_write.py or /pipe skill for mutations.
- Launch via `python3 tools/dashboard/app.py` or a `/dashboard` skill wrapper

</code_context>

<specifics>
## Specific Ideas

- The preview mockup with grouped-by-stage rows and header stats was the preferred layout direction.
- Stale-first sorting within stage groups ensures the most actionable items are always visible without scrolling.
- Funnel should show the honest picture - all-time totals including rejected/withdrawn, not just active.

</specifics>

<deferred>
## Deferred Ideas

None - discussion stayed within phase scope

</deferred>

---

*Phase: 03-visual-pipeline-dashboard-tui*
*Context gathered: 2026-04-09*
