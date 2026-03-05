# Self-Improving Data Framework

How the 8 behavioral patterns in the global CLAUDE.md snippet map to this project's specific implementations. This is a reference for understanding the architecture — not instructions for Claude.

## Pattern → Implementation Map

### 1. Data Handling

**Global rule:** Additive-only updates, approval-gated, no fabrication.

**Project implementation:**
- `framework/data-enrichment.md` (lines 40-42) — "Wait for the candidate to confirm before writing anything"
- `.claude/skills/import-cv/SKILL.md` (lines 17-21) — merge logic for repeated imports
- Every skill that writes to `data/` files follows this pattern inline

### 2. Post-Session Enrichment

**Global rule:** Scan for new information after working sessions, present for approval.

**Project implementation:**
- `framework/data-enrichment.md` — full 64-line procedure with 6 scan categories
- `framework/interview-workflow.md` (line 18) — "Follow the procedure in `framework/data-enrichment.md`"
- `.claude/skills/debrief/SKILL.md` (line 193) — enrichment pass after voice simulation debriefs

### 3. Research Freshness

**Global rule:** Staleness checks with `Last updated:` headers, refresh deltas.

**Project implementation:**
- `.claude/skills/research-company/SKILL.md` (lines 82-91) — 14-day freshness check, auto-refresh, "What Changed" section
- `.claude/skills/act/SKILL.md` (lines 26-29) — builds fresh dossier map from `Last updated:` headers
- `.claude/skills/weekly-review/SKILL.md` (lines 74-78) — freshness reporting in weekly retrospectives

### 4. Longitudinal Logging

**Global rule:** Append-only logs, newest-first, trend computation.

**Project implementation:**
- `.claude/skills/weekly-review/SKILL.md` — append-only with "Do not delete entries"
- `.claude/skills/checkout/SKILL.md` — daily log snapshots to `data/job-todos-daily-log.md` (absorbed from `/todo daily` 2026-02-26)
- `coaching/progress-recruiter/_summary.md` and `coaching/progress-interview/_summary.md` — session scorecards
- `coaching/anti-pattern-tracker.md` — Update Log section

### 5. Note Routing

**Global rule:** Classify notes by type, route to correct file, multi-destination.

**Project implementation:**
- `tools/remember_classify.py` — deterministic classifier: 8-priority rule engine matching contact names, company names, dossier slugs, and keyword patterns; outputs `destinations[]` with file + entity + type
- `.claude/skills/remember/SKILL.md` — calls `remember_classify.py`, routes to networking/pipeline/profile/company-notes/inbox/notes; supports multi-destination writes
- `tools/act_classify.py` — inbox classification: job ads → pipeline, contacts → networking, articles/research → bucket_a, unclassifiable → notes

### 6. Task Management

**Global rule:** Cross-referencing, auto-completion, priority sorting, velocity tracking.

**Project implementation:**
- `.claude/skills/todo/SKILL.md` — full task management with pipeline/contact cross-references, velocity metrics
- `.claude/skills/standup/SKILL.md` — morning briefing that reads 5 data files and surfaces cross-connections
- `.claude/skills/act/SKILL.md` — auto-executes eligible tasks with parallel agents

### 7. Writing & Tone

**Global rule:** Voice matching, quality gates, outreach anti-patterns.

**Project implementation:**
- `framework/outreach-guide.md` — comprehensive outreach reference (frameworks, quality gates, metrics, anti-patterns, tone matching)
- `.claude/skills/cold-outreach/SKILL.md` — 9-step process with waterfall personalization and three-question quality gate
- `.claude/skills/follow-up/SKILL.md` — sequence-aware follow-ups with value-add logic
- `.claude/skills/draft-email/SKILL.md` — type-detected email drafting with channel constraints
- `framework/style-guidelines.md` — tone, language conventions, CV format options

### 8. Graceful Degradation

**Global rule:** Handle missing data, first-run scenarios.

**Project implementation:**
- `CLAUDE.md` Profile Guard (lines 42-49) — stops generative skills if profile/goals are missing, with specific remediation instructions
- `.claude/skills/standup/SKILL.md` — works with whatever data exists, notes gaps
- `.claude/skills/weekly-review/SKILL.md` — generates baseline metrics on first run

## Design Principle

The global CLAUDE.md encodes the **behavioral pattern** (what to do). Project-level skills encode the **implementation** (how to do it with specific files, formats, and workflows). This separation means the patterns are portable across any project while the skills add domain-specific detail.
