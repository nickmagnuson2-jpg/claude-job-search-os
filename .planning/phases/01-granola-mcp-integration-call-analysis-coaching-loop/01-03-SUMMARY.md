---
phase: 01-granola-mcp-integration-call-analysis-coaching-loop
plan: 03
subsystem: tools
tags: [granola, auto-debrief, n8n, inbox-automation, orchestrator]
dependency_graph:
  requires:
    - phase: 01-02
      provides: granola_fetch.py and call_analyzer.py headless scripts
  provides:
    - auto-debrief orchestrator chaining fetch + analyze + inbox write
    - macOS n8n launcher script
  affects: [data/inbox.md, n8n-workflows]
tech_stack:
  added: []
  patterns: [orchestrator-script, inbox-prepend-write, dry-run-cli]
key_files:
  created:
    - tools/granola_auto_debrief.py
    - tools/run_n8n.sh
  modified: []
key_decisions:
  - "All calls processed without pipeline filtering (D-10) - user reviews and routes from inbox"
  - "Inbox-only writes (D-11) - no automated coaching file mutations"
  - "Hours override bypasses state file for manual testing windows"
metrics:
  duration_seconds: 497
  completed: "2026-04-09T05:12:23Z"
  tasks_completed: 1
  tasks_total: 2
  files_modified: 2
  checkpoint_at: task-2
---

# Phase 01 Plan 03: Auto-Debrief Orchestrator & n8n Launcher Summary

**Orchestrator script chaining granola_fetch + call_analyzer into inbox-prepend workflow, plus macOS n8n launcher matching existing Windows bat pattern**

## Performance

- **Duration:** 8 min
- **Started:** 2026-04-09T05:04:06Z
- **Completed:** 2026-04-09T05:12:23Z
- **Tasks:** 1 of 2 (Task 2 is checkpoint:human-action)
- **Files created:** 2

## Accomplishments

- granola_auto_debrief.py with format_inbox_entry (structured call summary) and auto_debrief_new_calls (fetch -> analyze -> inbox write orchestrator)
- CLI with --dry-run (print instead of write), --hours N (override fetch window), --repo-root (portable path resolution)
- run_n8n.sh macOS launcher with NODES_EXCLUDE=[] matching run_n8n.bat pattern
- Imports verified: both granola_fetch and call_analyzer import successfully

## Task Commits

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Create auto-debrief orchestrator and n8n launcher | 78cf0a6 | tools/granola_auto_debrief.py, tools/run_n8n.sh |

## Files Created

- `tools/granola_auto_debrief.py` - Orchestrator: format_inbox_entry + auto_debrief_new_calls + CLI with --dry-run/--hours/--repo-root
- `tools/run_n8n.sh` - macOS n8n launcher (chmod +x), sets NODES_EXCLUDE=[], runs n8n start

## Decisions Made

- Process ALL calls without pipeline filtering (D-10) - cast wide net, user routes from inbox during standup
- Write to data/inbox.md only (D-11) - no automated writes to coaching/ files
- Hours override makes direct API calls bypassing state file, useful for manual testing

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - both scripts are complete implementations.

## Checkpoint: Task 2 Pending

Task 2 (checkpoint:human-action) requires user to:
1. Install n8n globally (`npm install -g n8n`)
2. Create Granola Personal API Key (Granola Settings > API)
3. Set GRANOLA_API_KEY environment variable
4. Test pipeline end-to-end
5. Configure n8n workflow with 3-hour cron schedule

## Self-Check: PASSED
