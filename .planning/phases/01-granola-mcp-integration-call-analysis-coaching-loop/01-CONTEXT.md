# Phase 1: Granola MCP Integration — Call Analysis & Coaching Loop - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Connect Granola meeting transcripts to the existing coaching system. Extend `/debrief` to pull transcripts from Granola MCP, analyze calls for anti-patterns and coaching improvements, and set up scheduled auto-debrief via n8n. Every call should automatically feed anti-pattern tracking, coached answer refinement, and interview debrief.

</domain>

<decisions>
## Implementation Decisions

### Transcript Source
- **D-01:** Extend the existing `/debrief` skill to support Granola as an optional input source. Do NOT create a separate skill.
- **D-02:** Two input modes: (1) pasted transcript (existing behavior, unchanged) or (2) Granola fetch via MCP. If no transcript is pasted, default to Granola picker.

### Call Matching
- **D-03:** Interactive picker showing recent Granola meetings. User selects which call to debrief.
- **D-04:** The picker should show meeting title, date, and duration so the user can identify the right call.

### Analysis Scope
- **D-05:** Full debrief depth — same as current `/debrief` skill: filler word counts, coached-answer comparison, Q&A pair grading, anti-pattern detection, session logging to `coaching/progress-recruiter/`.
- **D-06:** Auto-extract filler counts for tracked words: "really", "kind of", "definitely", "to be honest with you", "absolutely", "pretty".
- **D-07:** Compare what was said against cheat sheet / coached answers for the relevant company.
- **D-08:** Update `coaching/anti-pattern-tracker.md` with per-call data and trend tracking.

### Coaching Loop — Scheduled Automation
- **D-09:** n8n workflow checks Granola periodically for new unprocessed calls.
- **D-10:** Process ALL calls — no filtering by pipeline match. Cast wide net, review from inbox.
- **D-11:** Auto-debrief results written to `data/inbox.md` for review, not directly to coaching files. User routes from inbox after reviewing.
- **D-12:** DEPENDENCY: n8n is not installed on current machine. n8n setup is a prerequisite for D-09 through D-11. The `/debrief` extension (D-01 through D-08) works without n8n.

### Claude's Discretion
- Granola MCP API discovery — tool names, data model, authentication flow
- Transcript parsing format (how to split into Q&A pairs from Granola's raw format)
- n8n workflow scheduling interval (suggest every 2-4 hours)
- How to handle calls where no cheat sheet exists (general coaching analysis without coached-answer comparison)

</decisions>

<specifics>
## Specific Ideas

- Today's practice session surfaced "really" 5 times in a straight-through run. Sarah (Outset HM) also noticed the filler. The system needs to track this per-call and show trends over time.
- The anti-pattern tracker already has a rich schema (Status, Occurrences, Last Seen, Trend, Notes). Granola integration should append to this, not replace it.
- Session logs in `coaching/progress-recruiter/` follow a template at `framework/templates/recruiter-session.md`. Granola debriefs should produce the same format.
- Existing `/debrief` skill uses cheat sheets from `output/<slug>/` and coached answers from `coaching/coached-answers.md`. Granola integration should use the same sources.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing debrief skill
- `.claude/skills/debrief/SKILL.md` — Current debrief skill definition, analysis steps, output format
- `framework/templates/recruiter-session.md` — Session log template that debrief produces
- `framework/templates/interview-session.md` — Alternative session template for non-recruiter calls

### Coaching infrastructure
- `coaching/anti-pattern-tracker.md` — Current anti-pattern schema, tracked patterns, update log
- `coaching/coached-answers.md` — Coached answer bank for comparison
- `coaching/progress-recruiter/_summary.md` — Session summary format

### Granola MCP
- Granola MCP endpoint: `https://mcp.granola.ai/mcp` (configured in project .claude.json)
- API surface needs discovery — researcher should call MCP tools to understand available endpoints

### Automation
- `tools/run_n8n.bat` — Existing n8n startup script (Windows — needs Mac equivalent)
- CLAUDE.md § "Background Automation (n8n)" — Existing workflow patterns

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `/debrief` skill: Full transcript analysis pipeline (parse → grade → anti-pattern detect → log). Extend, don't rebuild.
- `tools/remember_apply.py`: Routes notes to 8 destinations. Could be used for inbox routing of auto-debrief results.
- `coaching/anti-pattern-tracker.md`: Rich schema already tracking 11 patterns with occurrence counts and trends.

### Established Patterns
- All coaching writes use `Write` tool to `coaching/**` paths (skill has `Write(coaching/**)` permission)
- Company notes follow `data/company-notes/<slug>.md` convention
- Session logs go to `coaching/progress-recruiter/YYYY-MM-DD-HHMM-<company>-<type>.md`
- Atomic write scripts (todo_write.py, pipe_write.py, networking_write.py) handle data file mutations

### Integration Points
- `/debrief` skill needs new Granola fetch logic before Step 2 (Parse Transcript)
- Anti-pattern tracker append needs to match existing table format
- n8n workflow needs a Python script to orchestrate (similar to existing `gmail_fetch.py`)

</code_context>

<deferred>
## Deferred Ideas

- Auto-generate practice scripts from call analysis (identify weak answers → generate targeted practice questions) — future enhancement
- Voice tone analysis (confidence, pace, energy) — requires audio analysis beyond transcript text
- Cross-call trend dashboard (visual chart of filler counts over time) — could be part of Phase 3 (Pipeline Dashboard)

</deferred>

---

*Phase: 01-granola-mcp-integration-call-analysis-coaching-loop*
*Context gathered: 2026-04-08*
