# Job Search OS — Platform Improvements

## What This Is

Enhancement milestone for an existing AI-powered job search operating system. Adding automation, visualization, and call intelligence capabilities inspired by competitive analysis of Career-Ops, Proficiently, and other open-source job search tools.

## Core Value

Close the automation and UX gaps without sacrificing the system's depth advantage in coaching, networking, and research.

## Requirements

### Validated

- ✓ 27 Claude Code skills covering full job search lifecycle — existing
- ✓ Atomic write scripts for pipeline, networking, todos — existing
- ✓ n8n background automation (Gmail fetch, staleness, nudges) — existing
- ✓ Interview coaching with anti-pattern tracking and voice simulation — existing
- ✓ Company/industry research with evidence tiers and freshness — existing
- ✓ Networking tracking with outreach log and follow-up sequences — existing
- ✓ Self-improvement loop (lessons.md, style-guidelines voice capture) — existing

### Active

- [ ] Browser automation for job discovery (Playwright scanning target company career pages)
- [x] Visual pipeline dashboard (terminal TUI for pipeline status, staleness, next actions) — Validated in Phase 3
- [ ] ATS form auto-fill (Greenhouse/Lever/Workday submission from profile data)
- [ ] Granola MCP integration for call analysis and coaching improvement

### Out of Scope

- Role archetype auto-detection — useful but lower impact, defer to next milestone
- Live resume preview (React-PDF) — nice DX but not blocking outcomes
- Mobile access via Telegram — defer until core improvements ship
- Network scan (LinkedIn contacts' employers) — /scan-contacts partially covers this
- Batch parallel evaluation — not needed at current application volume
- Open source packaging — requires significant effort to strip personal data, defer

## Context

- Existing brownfield codebase: Python tools, Claude Code skills, markdown data files, n8n workflows
- Competitive analysis completed 2026-04-08 (see output/040826-competitive-analysis.md)
- System scores 4.40/5.00 on weighted framework; weakest dimensions are Automation (3/5) and UX (3/5)
- Career-Ops (19K stars) is the primary benchmark for automation and UX
- Granola MCP (https://mcp.granola.ai/mcp) is connected and available

## Constraints

- **Tech stack**: Python 3.8+ for tools, Claude Code skills for workflows. No Node.js runtime dependency beyond Claude Code itself.
- **Data**: All data stays local in markdown/flat files. No external databases.
- **Playwright**: Will need `pip install playwright && playwright install` for browser automation.
- **Granola**: Depends on Granola MCP API surface — needs discovery before planning.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Top 3 + Granola only for this milestone | Focus on highest-impact gaps; remaining 6 improvements deferred | — Pending |
| Python Rich/Textual for TUI | Stays in Python ecosystem, no Go dependency | — Pending |
| Playwright for browser automation | Same tool Career-Ops uses, proven at scale | — Pending |

---
*Last updated: 2026-04-09 after Phase 3 completion*
