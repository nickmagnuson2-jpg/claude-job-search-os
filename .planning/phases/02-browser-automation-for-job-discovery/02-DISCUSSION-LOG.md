# Phase 2: Browser Automation for Job Discovery - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md - this log preserves the alternatives considered.

**Date:** 2026-04-08
**Phase:** 02-browser-automation-for-job-discovery
**Areas discussed:** ATS parser strategy, Scoring & fit logic, Target list & discovery, Output & routing

---

## ATS Parser Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| API-first | Use HTTP requests for Greenhouse/Lever/Ashby JSON APIs, Playwright only for custom pages | ✓ |
| Playwright for everything | Use Playwright for all career pages including API-backed ones | |
| You decide | Claude picks best approach per platform | |

**User's choice:** API-first
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Playwright + heuristics | Load page with Playwright, extract by common HTML patterns | ✓ |
| Skip unknown ATS | Only scan Greenhouse/Lever/Ashby, flag others as manual check | |
| You decide | Claude picks based on career page format | |

**User's choice:** Playwright + heuristics for generic fallback
**Notes:** None

---

## Scoring & Fit Logic

| Option | Description | Selected |
|--------|-------------|----------|
| Title match | Role title vs goals.md targets | ✓ |
| Seniority match | Seniority level alignment | ✓ |
| Industry/domain match | Company industry vs goals.md | ✓ |
| Keyword overlap | Profile skills in job description | ✓ |

**User's choice:** All four dimensions selected
**Notes:** Multi-select question

| Option | Description | Selected |
|--------|-------------|----------|
| Surface all, sort by score | Show everything, sorted by fit. No filtering. | ✓ |
| Filter below threshold | Only surface 5+ scores | |
| Two tiers | 7+ prominent, 4-6 also found, below 4 dropped | |

**User's choice:** Surface all, sort by score
**Notes:** None

---

## Target List & Discovery

| Option | Description | Selected |
|--------|-------------|----------|
| Markdown table | Simple markdown table in data/scan-targets.md | |
| YAML/JSON config | Structured config file with nested fields | ✓ |
| You decide | Claude picks format | |

**User's choice:** YAML/JSON config
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-derive from goals.md | Script reads goals.md and generates initial list | |
| Manual curation | User hand-picks 20-30 companies | |
| Pipeline + goals hybrid | Seed from pipeline companies plus goals.md criteria | ✓ |

**User's choice:** Pipeline + goals hybrid
**Notes:** None

---

## Output & Routing

| Option | Description | Selected |
|--------|-------------|----------|
| Inbox | Write to data/inbox.md, review during /standup | ✓ |
| Dedicated scan-results.md | Separate file with full history, inbox gets summary | |
| Direct to pipeline | High scores auto-added to pipeline | |

**User's choice:** Inbox (follows Phase 1 pattern)
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Company + title fuzzy match | Exact company + ~80% title similarity | ✓ |
| Company + title + URL | More precise but may miss renamed postings | |
| You decide | Claude picks dedup strategy | |

**User's choice:** Company + title fuzzy match
**Notes:** None
