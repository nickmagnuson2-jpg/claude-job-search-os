# ROADMAP — Job Search OS Platform Improvements

> Milestone: v2.0 — Automation, Visualization & Call Intelligence
> Created: 2026-04-08

---

## Phase 1: Granola MCP Integration — Call Analysis & Coaching Loop

**Goal:** Connect Granola meeting transcripts to the coaching system so every call automatically feeds anti-pattern tracking, coached answer refinement, and interview debrief.

**Why first:** Lowest technical risk, highest immediate value. You're doing calls NOW. Every unrecorded call is lost coaching data.

**Plans:** 3 plans

Plans:
- [ ] 01-01-PLAN.md — MCP setup + extend /debrief skill with Granola fetch, company-notes append, filler tracking
- [ ] 01-02-PLAN.md — Python scripts: call_analyzer.py (transcript analysis) + granola_fetch.py (REST API client)
- [ ] 01-03-PLAN.md — n8n automation: auto-debrief orchestrator + scheduled workflow

### Requirements
- [ ] R1.1: Discover Granola MCP API surface (list tools, understand data model)
- [ ] R1.2: Build a `/debrief-call` skill that pulls a Granola transcript by meeting title or date
- [ ] R1.3: Auto-extract filler word counts ("really", "kind of", "definitely") from transcripts
- [ ] R1.4: Auto-extract STAR story usage — which stories were told, how long, any fumbles
- [ ] R1.5: Compare transcript against coached answers — what was practiced vs. what was actually said
- [ ] R1.6: Append call analysis to `data/company-notes/<slug>.md` with structured intel
- [ ] R1.7: Update `coaching/anti-pattern-tracker.md` with per-call filler counts and trends
- [ ] R1.8: Generate post-call improvement recommendations (what to practice next)

### Key Tasks
1. **API Discovery** — Call Granola MCP tools to understand available endpoints (list meetings, get transcript, search). Document the data model.
2. **Transcript Fetcher** — Python script `tools/granola_fetch.py` that retrieves transcript by meeting title/date via MCP or direct API.
3. **Call Analyzer** — Python script `tools/call_analyzer.py` that takes a transcript and returns structured JSON: filler counts, story mapping, key topics, caller talk ratio, question quality.
4. **Coaching Integrator** — Skill `/debrief-call` that orchestrates: fetch transcript → analyze → update company notes → update anti-pattern tracker → generate recommendations.
5. **Trend Tracking** — Append filler counts to `coaching/anti-pattern-tracker.md` with date, company, and counts so trends are visible over time.

### Dependencies
- Granola MCP must be authenticated and returning data
- Granola must have recorded the calls (user needs to have Granola running during calls)

### Success Criteria
- [ ] Can pull any Granola transcript from the last 7 days by meeting title
- [ ] Filler word count matches manual count within 10% accuracy
- [ ] Anti-pattern tracker shows trend data across 3+ calls
- [ ] Company notes auto-populated with call intel after running /debrief-call
- [ ] Time from call end to full debrief: under 5 minutes

### Effort Estimate
- Discovery: 1 hour
- Scripts: 3-4 hours
- Skill + integration: 2 hours
- **Total: ~1 day**

---

## Phase 2: Browser Automation for Job Discovery

**Goal:** Playwright-based scanning of target company career pages that auto-scores roles against profile and surfaces matches in /standup.

**Why second:** Closes the biggest gap vs. Career-Ops. Currently job discovery is manual; this makes it passive.

### Requirements
- [ ] R2.1: Playwright scanner that navigates to a company's careers page and extracts open roles
- [ ] R2.2: Configurable target company list in `data/scan-targets.md` (company name, careers URL, role filters)
- [ ] R2.3: Auto-score each discovered role against `data/profile.md` and `data/goals.md` (1-10 fit score)
- [ ] R2.4: Deduplication against existing `data/job-pipeline.md` entries
- [ ] R2.5: Output new matches to `data/inbox.md` or a dedicated `data/scan-results.md` with scores
- [ ] R2.6: `/scan-companies` skill that runs the scanner on demand
- [ ] R2.7: n8n workflow that runs the scanner on a schedule (daily or twice-daily)
- [ ] R2.8: Support for at least 3 career page formats: Greenhouse, Lever, Ashby

### Key Tasks
1. **Playwright Setup** — `pip install playwright && playwright install chromium`. Test basic navigation.
2. **Career Page Parsers** — Python module `tools/career_scanner/` with parsers per ATS platform:
   - `greenhouse.py` — Parse Greenhouse board pages (JSON API available at `boards.greenhouse.io/company/jobs`)
   - `lever.py` — Parse Lever pages (`jobs.lever.co/company`)
   - `ashby.py` — Parse Ashby pages (`jobs.ashbyhq.com/company`)
   - `generic.py` — Fallback HTML parser for custom career pages
3. **Scoring Engine** — `tools/career_scanner/scorer.py` that reads profile + goals and scores each role on: title match, seniority match, industry match, location match, keyword overlap.
4. **Target List** — Create `data/scan-targets.md` with initial 20-30 companies from goals.md targeting criteria.
5. **Deduplication** — Compare discovered roles against pipeline by company + title fuzzy match.
6. **Skill** — `/scan-companies` that runs the full pipeline and presents results.
7. **n8n Integration** — Workflow that triggers the scanner on schedule and writes results to inbox.

### Dependencies
- Phase 1 not required (independent)
- Playwright + Chromium installed
- `data/goals.md` must have targeting criteria for scoring

### Success Criteria
- [ ] Scanner successfully extracts roles from Greenhouse, Lever, and Ashby career pages
- [ ] Scoring produces meaningful differentiation (not all 5s or all 8s)
- [ ] Deduplication correctly skips roles already in pipeline
- [ ] /scan-companies runs end-to-end in under 3 minutes for 20 companies
- [ ] At least one new relevant role discovered in first real scan that wasn't found manually

### Effort Estimate
- Playwright setup + ATS parsers: 4-5 hours
- Scoring engine: 2-3 hours
- Skill + dedup + n8n: 3-4 hours
- **Total: ~2 days**

---

## Phase 3: Visual Pipeline Dashboard (TUI)

**Goal:** Terminal-based dashboard showing pipeline by stage, staleness, next actions, and conversion metrics. Replaces reading raw markdown tables.

**Why third:** Improves daily UX but doesn't change outcomes directly. Builds on Phase 2 (scanner results feed the dashboard).

### Requirements
- [ ] R3.1: Terminal dashboard using Python Textual or Rich library
- [ ] R3.2: Pipeline view: applications grouped by stage (Applied, Interview, Offer, Closed) with counts
- [ ] R3.3: Staleness highlighting: red if no activity in 7+ days, yellow if 3-7 days
- [ ] R3.4: Next actions column showing upcoming tasks per application
- [ ] R3.5: Conversion funnel: Applied → Interview → Offer rates
- [ ] R3.6: Quick filters: by stage, by staleness, by date added
- [ ] R3.7: Keyboard navigation and search
- [ ] R3.8: Reads directly from `data/job-pipeline.md` and `data/job-todos.md` — no separate database

### Key Tasks
1. **Library Selection** — Evaluate Textual vs. Rich for TUI. Textual preferred (reactive, widget-based, better for dashboards).
2. **Data Parser** — `tools/dashboard/pipeline_parser.py` that reads `job-pipeline.md` markdown table into structured data. Reuse logic from existing pipe_write.py.
3. **Staleness Calculator** — Compute days since last activity per entry. Cross-reference with `data/job-todos.md` for pending actions.
4. **Dashboard Layout** — `tools/dashboard/app.py` using Textual:
   - Header: pipeline summary stats (total active, interviews, staleness alerts)
   - Main: table with Company, Role, Stage, Last Activity, Days Stale, Next Action
   - Footer: key bindings, filter status
   - Sidebar: conversion funnel visualization
5. **Filters & Sort** — Stage filter (tabs or key bindings), staleness sort, date sort, search by company name.
6. **Launch Script** — `python tools/dashboard/app.py` or alias to a skill `/dashboard`.
7. **Integration** — Cross-reference todo items and networking log for "next action" column.

### Dependencies
- Phase 1 and 2 not required (reads existing data files)
- `pip install textual` (or `rich` if Textual is too heavy)

### Success Criteria
- [ ] Dashboard launches in under 2 seconds
- [ ] All pipeline entries render correctly from job-pipeline.md
- [ ] Staleness highlighting accurately reflects days since last activity
- [ ] Can filter by stage with a single keypress
- [ ] Conversion funnel shows accurate Applied → Interview → Offer rates
- [ ] Keyboard-navigable — no mouse required

### Effort Estimate
- Textual setup + data parser: 2-3 hours
- Dashboard layout + widgets: 4-5 hours
- Filters, search, polish: 2-3 hours
- **Total: ~2 days**

---

## Phase 4: ATS Form Auto-Fill

**Goal:** Automate Greenhouse/Lever/Workday application form submission using profile data and generated CVs.

**Why last:** Highest technical complexity, most fragile (ATS forms change). But removes the most tedious step in applying.

### Requirements
- [ ] R4.1: Playwright-based form filler that reads `data/profile.md` for field values
- [ ] R4.2: Support Greenhouse application forms (name, email, phone, resume upload, LinkedIn, cover letter)
- [ ] R4.3: Support Lever application forms
- [ ] R4.4: Support Workday application forms (most complex — multi-page)
- [ ] R4.5: Resume upload from most recent generated PDF in `output/<slug>/`
- [ ] R4.6: Cover letter upload if available
- [ ] R4.7: Preview mode — fills form but does NOT submit, waits for manual review and submit
- [ ] R4.8: `/apply-form <url>` skill that runs the form filler

### Key Tasks
1. **Profile Field Mapper** — Map `data/profile.md` fields to common ATS form fields (name, email, phone, LinkedIn URL, location, work authorization).
2. **Greenhouse Filler** — `tools/ats_filler/greenhouse.py`:
   - Navigate to application URL
   - Fill standard fields from profile
   - Upload resume PDF
   - Upload cover letter if exists
   - Pause before submit button — screenshot for review
3. **Lever Filler** — `tools/ats_filler/lever.py`: Similar pattern, different DOM structure.
4. **Workday Filler** — `tools/ats_filler/workday.py`: Multi-page flow, login wall handling, most complex.
5. **Preview Mode** — All fillers stop before final submit. Take screenshot, display to user, wait for confirmation.
6. **Skill** — `/apply-form <url>` detects ATS platform from URL, routes to correct filler, runs in preview mode.
7. **Error Handling** — Graceful failure if form structure has changed. Log what worked and what didn't.

### Dependencies
- Phase 2 (Playwright already installed and configured)
- Generated CV must exist for the target role
- Profile.md must have complete contact info

### Success Criteria
- [ ] Greenhouse form filled correctly with all standard fields in under 30 seconds
- [ ] Lever form filled correctly
- [ ] Resume PDF uploaded successfully
- [ ] Preview mode shows filled form before submission — user confirms or aborts
- [ ] Workday basic flow works (multi-page navigation + field fill)
- [ ] No accidental submissions — preview mode is the default and only mode

### Effort Estimate
- Profile mapper + Greenhouse: 3-4 hours
- Lever: 2-3 hours
- Workday: 4-6 hours (most complex)
- Skill + preview mode: 2 hours
- **Total: ~3 days**

---

## Phase Summary

| Phase | Goal | Effort | Dependencies |
|-------|------|--------|-------------|
| 1. Granola Integration | Call analysis → coaching loop | ~1 day | Granola MCP connected |
| 2. Browser Automation | Auto-discover roles from career pages | ~2 days | Playwright installed |
| 3. Pipeline Dashboard | Visual TUI for pipeline management | ~2 days | Textual installed |
| 4. ATS Auto-Fill | Form submission from profile data | ~3 days | Phase 2 (Playwright) |

**Total estimated effort: ~8 days**

**Recommended execution order:** 1 → 2 → 3 → 4 (Phase 1 is independent and highest immediate value; Phase 4 depends on Phase 2's Playwright setup)

---
*Last updated: 2026-04-08*
