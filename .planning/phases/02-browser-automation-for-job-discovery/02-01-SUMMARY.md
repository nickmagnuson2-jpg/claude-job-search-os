---
phase: 02-browser-automation-for-job-discovery
plan: 01
subsystem: career-scanner
tags: [ats-parser, api-client, playwright, job-discovery]
dependency_graph:
  requires: []
  provides: [career_scanner_package, greenhouse_parser, lever_parser, ashby_parser, generic_parser, role_schema]
  affects: [tools/career_scanner/]
tech_stack:
  added: [playwright]
  patterns: [stdlib-urllib-api-client, heuristic-css-extraction, standardized-role-dict]
key_files:
  created:
    - tools/career_scanner/__init__.py
    - tools/career_scanner/greenhouse.py
    - tools/career_scanner/lever.py
    - tools/career_scanner/ashby.py
    - tools/career_scanner/generic.py
    - tools/career_scanner/test_parsers.py
  modified: []
decisions:
  - Used User-Agent header on all HTTP requests (Ashby returns 403 without it)
  - Lever invalid slug detection via {"ok": false} response pattern
  - Generic parser uses 8 heuristic CSS selectors with URL deduplication
metrics:
  duration_seconds: 211
  completed: 2026-04-09T13:01:13Z
---

# Phase 2 Plan 01: ATS Parser Modules Summary

Four ATS parser modules (Greenhouse, Lever, Ashby, generic Playwright fallback) returning standardized 12-key Role dicts, with 24 passing integration tests against live APIs.

## What Was Built

### career_scanner package (tools/career_scanner/)

- **__init__.py** - ROLE_KEYS set (12 fields), validate_role() function, PARSERS registry mapping ATS names to fetch functions
- **greenhouse.py** - fetch_greenhouse(slug) fetches from boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true, strips HTML from content field via html.unescape + re.sub
- **lever.py** - fetch_lever(slug) fetches from api.lever.co/v0/postings/{slug}?mode=json, converts createdAt millisecond timestamps to ISO dates, handles {"ok": false} error responses
- **ashby.py** - fetch_ashby(slug) fetches from api.ashbyhq.com/posting-api/job-board/{slug}, maps isRemote boolean directly
- **generic.py** - fetch_generic(careers_url, company_name) uses Playwright headless Chromium with 8 heuristic CSS selectors to extract job links from arbitrary career pages
- **test_parsers.py** - 24 integration tests: schema conformance, field mapping verification, invalid slug error handling, HTML stripping validation

### Standardized Role Schema

All parsers return dicts with identical keys: title, company, department, team, location, remote, employment_type, url, apply_url, published_at, description_plain, ats.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| User-Agent header on all API requests | Ashby returns HTTP 403 without a User-Agent header; added to all three API parsers for consistency |
| Lazy Playwright import in generic.py | Avoids requiring Playwright installation for users who only need the three API parsers |
| 8 heuristic CSS selectors in priority order | Covers apply links, job/position/career/opening links, heading links, and container-scoped list links |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Ashby API requires User-Agent header**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Ashby API returns HTTP 403 Forbidden when no User-Agent header is set (urllib default is "Python-urllib/3.14")
- **Fix:** Added User-Agent header to all three API parsers for robustness
- **Files modified:** greenhouse.py, lever.py, ashby.py
- **Commit:** dddb0b0

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| dfb3352 | test | Add failing tests for ATS parsers (TDD RED) |
| dddb0b0 | feat | Implement three ATS API parsers with test suite (TDD GREEN) |
| 30fbfd3 | feat | Add Playwright generic fallback parser |

## Verification Results

- All four parser modules importable: PASS
- Greenhouse returns non-empty list for "discord": PASS (84+ jobs)
- Lever returns non-empty list for "leverdemo": PASS (387+ jobs)
- Ashby returns non-empty list for "ramp": PASS (132+ jobs)
- All returned dicts have exactly 12 ROLE_KEYS: PASS
- 24/24 tests pass: PASS
- Generic parser import succeeds: PASS

## Self-Check: PASSED

All 7 files found. All 3 commits found.
