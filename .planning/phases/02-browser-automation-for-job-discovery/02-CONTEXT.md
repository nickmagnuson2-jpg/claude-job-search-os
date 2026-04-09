# Phase 2: Browser Automation for Job Discovery - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Playwright-based scanning of target company career pages that auto-scores roles against profile and surfaces matches in /standup. Covers ATS-specific parsers (Greenhouse, Lever, Ashby), generic fallback for custom pages, scoring engine, target company list, deduplication, and n8n scheduled execution.

</domain>

<decisions>
## Implementation Decisions

### ATS Parser Strategy
- **D-01:** API-first approach. Use lightweight HTTP requests (urllib/requests) for ATS platforms with public JSON APIs (Greenhouse, Lever, Ashby). Reserve Playwright browser automation only for custom career pages without an API.
- **D-02:** Generic fallback parser uses Playwright + heuristics for companies without a known ATS. Extract job listings by common HTML patterns (h2/h3 titles, links containing 'apply', structured lists). Best-effort, may miss some.
- **D-03:** Each ATS platform gets its own parser module in `tools/career_scanner/`. Parsers share a common interface returning standardized role objects.

### Scoring & Fit Logic
- **D-04:** Four scoring dimensions, all weighted: title match, seniority match, industry/domain match, keyword overlap. Score each role 1-10 against `data/profile.md` and `data/goals.md`.
- **D-05:** Surface all discovered roles sorted by fit score. No filtering threshold - user decides what's worth pursuing. No roles hidden.

### Target List & Discovery
- **D-06:** Target company list stored as YAML/JSON config file (not markdown table). More machine-readable for the scanner scripts.
- **D-07:** Initial target list seeded from pipeline + goals hybrid: companies already in `data/job-pipeline.md` plus new ones derived from `data/goals.md` industry/size targeting criteria.
- **D-08:** Config includes per-company fields: name, ATS platform, careers URL, role title filters, active flag.

### Output & Routing
- **D-09:** Scan results written to `data/inbox.md` following the Phase 1 pattern. User reviews during /standup and routes to pipeline. Consistent with auto-debrief flow.
- **D-10:** Deduplication by company name (exact match) + role title (fuzzy, ~80% similarity). If already in pipeline, skip silently.

### Claude's Discretion
- Specific JSON API endpoint URLs and response parsing for each ATS platform
- Scoring weight distribution across the four dimensions
- Playwright heuristic patterns for generic career page parsing
- n8n scheduling interval (suggest daily or twice-daily)
- YAML vs JSON format choice for scan-targets config

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing scanner infrastructure
- `tools/linkedin-scanner/scan.py` - Existing Playwright-based LinkedIn scanner. Reference for browser automation patterns, .env loading, CLI structure.
- `tools/linkedin-scanner/src/Scraper.py` - Playwright browser management patterns

### Existing scan skill
- `.claude/skills/scan-jobs/SKILL.md` - Existing manual job portal scanning skill. Different purpose (on-demand portal scan) but scoring/matching logic is relevant.

### Profile & goals
- `data/profile.md` - Candidate profile for scoring (skills, experience, preferences)
- `data/goals.md` - Targeting criteria (industries, roles, seniority, geography)
- `data/job-pipeline.md` - Existing pipeline entries for deduplication

### Automation patterns
- `tools/granola_fetch.py` - Phase 1 REST API client pattern (stdlib HTTP, .env loading, state file for incremental runs)
- `tools/granola_auto_debrief.py` - Phase 1 n8n automation pattern (fetch + analyze + inbox write)
- `CLAUDE.md` section "Background Automation (n8n)" - Existing n8n workflow patterns

### ATS platform APIs
- Greenhouse: `boards.greenhouse.io/{company}/jobs.json` (public, no auth needed)
- Lever: `api.lever.co/v0/postings/{company}` (public, no auth needed)
- Ashby: `jobs.ashbyhq.com/api/...` (needs discovery - researcher should verify endpoint)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tools/linkedin-scanner/`: Playwright setup, browser management, .env loading. Can reuse Playwright patterns for generic fallback parser.
- `tools/granola_fetch.py`: HTTP client pattern with .env auto-loading, rate limit handling, state file for incremental runs. Good template for ATS API clients.
- `tools/granola_auto_debrief.py`: n8n automation orchestrator pattern (fetch + process + inbox write). Scanner automation should follow same structure.
- `/scan-jobs` skill: Scoring/matching logic against profile. May be reusable or at least reference for scoring engine.

### Established Patterns
- All automation results go to `data/inbox.md` (Phase 1 pattern: D-09, D-10, D-11)
- Python scripts in `tools/` use stdlib where possible, CLI via argparse, JSON to stdout, errors to stderr
- `.env` auto-loading via `_load_dotenv()` pattern from granola_fetch.py
- State files in `tools/.cache/` for incremental processing

### Integration Points
- Scanner writes to `data/inbox.md` (same as auto-debrief)
- Dedup reads from `data/job-pipeline.md` (pipe_write.py for mutations)
- Target list reads from `data/goals.md` and `data/job-pipeline.md` for seeding
- n8n workflow triggers scanner on schedule (same pattern as Granola auto-debrief)

</code_context>

<specifics>
## Specific Ideas

- Greenhouse and Lever have well-documented public JSON APIs that return structured job data. No API key needed. This should be fast and reliable.
- The LinkedIn scanner already handles Playwright setup and browser lifecycle. Can reference but not necessarily import directly (different purpose).
- Existing `/scan-jobs` skill handles on-demand portal scanning with WebFetch. The new scanner is different: headless, scheduled, multi-company, with scoring.

</specifics>

<deferred>
## Deferred Ideas

None - discussion stayed within phase scope

</deferred>

---

*Phase: 02-browser-automation-for-job-discovery*
*Context gathered: 2026-04-08*
