---
phase: 01-granola-mcp-integration-call-analysis-coaching-loop
plan: 01
subsystem: debrief-skill
tags: [granola, mcp, debrief, coaching, filler-tracking, company-notes]
dependency_graph:
  requires: [granola-mcp-configured]
  provides: [debrief-granola-fetch, company-notes-auto-append, enhanced-filler-tracking]
  affects: [coaching/anti-pattern-tracker.md, data/company-notes/]
tech_stack:
  added: [granola-mcp]
  patterns: [mcp-tool-picker, transcript-qa-parsing, company-notes-auto-append]
key_files:
  modified:
    - .claude/skills/debrief/SKILL.md
decisions:
  - "Step 0 inserted before existing Step 1, preserving full backward compatibility with paste-transcript flow"
  - "Company notes use Write (read-then-write) per CLAUDE.md convention for files with potentially long rows"
  - "Filler tracking counts 6 specific words/phrases per D-06 with word-boundary matching"
metrics:
  duration_seconds: 1241
  completed: "2026-04-09T03:49:54Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 1
---

# Phase 01 Plan 01: Granola MCP Integration - Debrief Extension Summary

Extended /debrief skill with Granola MCP transcript fetch (meeting picker + Q&A parsing), company-notes auto-append after each session, and per-word filler tracking for 6 coached anti-pattern fillers.

## Tasks Completed

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Configure Granola MCP server | (pre-completed) | MCP configured via `claude mcp add`, OAuth completed, `list_meetings` verified |
| 2 | Extend /debrief with Granola fetch, company-notes, filler tracking | 3ce94ca | 5 additions to SKILL.md: Step 0, Step 6.5, allowed-tools, no-cheat-sheet fallback, filler specificity |

## What Changed

### SKILL.md Additions

1. **Frontmatter `allowed-tools`** - Added `Write(data/company-notes/**)` to enable company notes writes
2. **Step 0: Acquire Transcript** - New step before Step 1. If no transcript pasted, calls `list_meetings` to show a picker table, then `get_meeting_transcript` to fetch and parse into Q&A pairs. Handles consecutive segment merging, panel interview detection, and CV path derivation from meeting title.
3. **Step 6.5: Update Company Notes** - New step after Step 6. Appends structured call intel (key intel, questions asked, signals, follow-up items) to `data/company-notes/<slug>.md` using Write (read-then-write).
4. **No cheat sheet fallback** - Added to Step 3C. When no cheat sheet exists, skips coached-answer comparison but runs all other analysis. Suggests running `/prep-interview` for future calls.
5. **Enhanced filler tracking** - Added to Step 3D. Counts specific occurrences of: "really", "kind of"/"kinda", "definitely", "to be honest with you", "absolutely", "pretty" (as hedge). Includes Update Log entry format with per-filler counts.

### Preserved

- Steps 1-6 remain exactly as they were
- Paste-transcript flow is unchanged (Step 0 skips to Step 1 when transcript is present)
- Session file naming convention unchanged
- All existing anti-pattern detection logic preserved

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all additions are complete instruction text, not code stubs.

## Self-Check: PASSED
