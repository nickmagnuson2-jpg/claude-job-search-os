---
name: scan-jobs
description: Scan a job portal for matching roles. The model fetches and copies listings; tools/scan_jobs.py dedupes, gates, scores and records them deterministically
argument-hint: <portal-url-or-name> [search query]
user-invocable: true
allowed-tools: WebFetch(*), mcp__exa__web_fetch_exa, mcp__exa__web_search_exa, Read(*), Edit(.claude/skills/scan-jobs/cache.md), Write(.claude/skills/scan-jobs/cache.md), Read(data/job-pipeline.md), Write(output/scan-jobs/**), Bash(PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py:*)
---

# Scan Job Portal

Scan a job portal for projects/roles matching the candidate's profile, deduplicate against a persistent cache of already-evaluated ads, assess fit for new listings, and output a ranked table with links.

## Arguments

- `$ARGUMENTS`: `<portal> [search query]`
  - **Portal** (required): Domain or URL of the job portal to scan (e.g. `upwork.com`, `indeed.com`, or a full search URL)
  - **Search query** (optional): Search terms. If omitted, derive a default query from the candidate's top skills.

Examples:
- `/scan-jobs upwork.com` — scan with auto-derived query
- `/scan-jobs upwork.com "project manager" OR "product owner"`
- `/scan-jobs indeed.com "data engineer" remote`
- `/scan-jobs https://www.example.com/jobs?q=frontend&type=contract` — full search URL

## Instructions

## Who decides what

**You fetch and copy. `tools/scan_jobs.py` decides.** Dedupe, the location gate, the fit score, the status, the cache write and the pipeline proposals are all in that script, so the same listings always get the same verdict. Do not score, rank, or re-label a role yourself, and do not edit the cache by hand during a scan. The score is the career scanner's own scorer (`tools/career_scanner/scorer.py`), the same one the nightly scan uses. Origin: finding 45.F1 (2026-09-14) and Nick's 2026-10-22 decision to make this skill as deterministic as possible.

### Step 1: Query

If no search query was given, build one from the target titles in `data/scan-targets.yaml` (its `x-lane-filters`), joined with OR.

### Step 2: Lookback window

Read `Last updated:` at the top of `.claude/skills/scan-jobs/cache.md`. Lookback = days since then + 1, clamped to 1-14; 7 if missing. Use it for any date filter the portal supports. (Reading the cache for dedupe is the script's job, not yours.)

### Step 3: Navigate the Portal

#### Step 3a: If the board is ATS-hosted, use the ATS API — do NOT fetch the page

**Check this FIRST, before any WebFetch or Exa call.** Most company job boards are
hosted by an ATS that renders listings in JavaScript. A fetch of the rendered page
returns a title-only shell or a partial list, and **that result is indistinguishable
from a genuinely empty board** — which is how a scan records "no roles at X" when the
board actually has fifty.

**This repo already has parsers for the three major ATSes.** Do not write new fetch
logic and do not reach for WebFetch on these hosts — import or shell out to:

| Host in the URL | Parser | Endpoint it uses |
|---|---|---|
| `jobs.ashbyhq.com/<slug>` | `tools/career_scanner/ashby.py` → `fetch_ashby(slug)` | `api.ashbyhq.com/posting-api/job-board/<slug>` |
| `boards.greenhouse.io` / `job-boards.greenhouse.io/<slug>` | `tools/career_scanner/greenhouse.py` → `fetch_greenhouse(slug)` | `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` |
| `jobs.lever.co/<slug>` | `tools/career_scanner/lever.py` → `fetch_lever(slug)` | `api.lever.co/v0/postings/<slug>?mode=json` |

These return full JSON: title, department, location, complete description, and comp
band where posted. That is strictly more than a page fetch can give you, and it
removes the separate detail-fetch in Step 4b for these listings.

Add `?includeCompensation=true` to the Ashby endpoint when calling it directly by
curl — the comp band is otherwise omitted.

**The slug is not always the company name.** When a direct slug guess 404s or returns
`[]`, the board is often embedded as an iframe on the company's own careers page.
Recover the real slug rather than concluding the board is empty:

1. Open the company's careers/jobs page in Chrome (`mcp__claude-in-chrome__navigate`).
2. Read the iframe sources:
   `[...document.querySelectorAll('iframe')].map(f=>f.src)`
3. The ATS slug is in that URL.

Worked shape (a real case, company generalised): `example.com/careers` is a culture
page carrying no listings at all, and the obvious slug guess (`.../example`) 404s. The
real board is `jobs.ashbyhq.com/examplelabs` — a different slug — embedded as an iframe
on `example.com/jobs`, with dozens of roles behind it. Two fetch-based passes had recorded that
company as "inconclusive, JS-rendered." Note the two separate traps: the careers page is
not the board, and the slug is not the company name.

**A slug that returns `[]` is NOT evidence of an empty board.** Per `data/scan-targets.yaml`'s
own warning: a wrong slug returns `[]` and looks exactly like an empty board. Say
"could not enumerate the board" — never "no roles found" — until a parser has
returned rows.

> **Origin, 2026-09-14.** A target company's careers check was run three times across
> three weeks and each time recorded as "a handful of roles, all engineering; the
> non-engineering filter categories are empty." The ATS API returned an order of magnitude
> more, including several in-lane seats. The board was never empty; it was never being read.
> The parsers above already existed in this repo the whole time and were not used.
> Two failure modes compounded: reaching for a generic fetch tool before checking for
> existing infrastructure, and recording an unreadable result as a negative finding.

#### Step 3b: Everything else

**If a full search URL was provided:** Fetch it directly.

**If only a portal domain was provided:**
1. Fetch the portal's job search or browse page
2. Identify the URL structure and search parameters
3. Construct a search URL applying relevant filters:
   - Contract type: match the candidate's preferences from `data/profile.md` (e.g. freelance, contract, permanent — if the portal supports this filter)
   - Remote work: match the candidate's work-model preference from `data/profile.md` (remote, hybrid, onsite — if the portal supports this filter)
   - Region: match the candidate's profile location/target market
   - Recency: filter to the lookback window if the portal supports date filtering
   - Query: use the provided or auto-derived search terms

**WebFetch prompt — use this exact prompt to maximise data extraction:**
```
Extract ALL project/job listings from this page as a structured list. For each listing extract:
1. EXACT project title
2. Company/intermediary name
3. Start date
4. Duration
5. Location and remote percentage
6. ALL skills/technologies mentioned
7. Daily/hourly rate if shown
8. Industry/sector
9. The href URL path for the project detail page (look for <a> tags linking to detail pages)

Number each listing. Extract every single one on the page.
```

If WebFetch returns a 403, an empty result, or a bot-gate/login wall (common on job boards), retry the same URL with `mcp__exa__web_fetch_exa` before giving up; Exa's fetch gets past gates that block WebFetch (per the Exa A/B verdict, 2026-05-19, reconfirmed 2026-06-01). If the portal still cannot be parsed or requires authentication even via Exa, inform the user and suggest they provide a direct search URL with results visible.

### Step 4b: Fetch Detail Pages

**Skip this step for any listing already retrieved via Step 3a** — the ATS parsers return the complete description, so there is nothing further to fetch and a second call would only re-render the same content worse.

For every other new (non-cached) ad, fetch its detail page to read the **actual listing text**. Do NOT assess fit based only on the search result tags — the detail page contains the real requirements, which often differ from the tag summary.

**Fetch all detail pages in parallel** (they are independent). Use this WebFetch prompt:
```
Extract the COMPLETE listing text in the original language. Include every section: description, tasks/responsibilities, requirements/qualifications, nice-to-haves, location/remote details, and any other content. Do not summarize — reproduce the full text. Also extract: contract type, start date, duration, rate if shown, and language requirements.
```

If a detail URL was not extracted from the search results, skip that ad's detail fetch — assess it from search results only (note this limitation in the assessment).

### Step 5: Copy each listing into the ad schema

One JSON object per listing. **Copy, never summarize or judge**: `description` is the full listing text from Step 3a or 4b, verbatim.

```json
[{"title": "...", "company": "...", "location": "...", "description": "...", "url": "...", "ad_date": "YYYY-MM-DD", "department": "", "team": ""}]
```

`title` and `company` are required; leave any other field `""` if the page does not show it (the ATS parsers return `department` and `team`; copy them). Write the array to `output/scan-jobs/ads.json` (gitignored).

### Step 6: Assess (deterministic)

```bash
PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py assess < output/scan-jobs/ads.json
```

It returns `found`, `cached` (already evaluated, skipped), `new` (each with `score` 1-10, `status`, `reason`), `propose_for_pipeline`, and the `cutoffs` it used. Status cutoffs: **shortlisted at 7+, maybe at 5-6, skip at 4 and below**, calibrated 2026-10-22 on 72 labeled pipeline roles (see the script's docstring for the numbers and their limits). A role whose location fails the nightly scanner's location rule (outside the Bay, or on the Peninsula / South Bay) is `skip` whatever its score; an unknown location is not. Report the numbers exactly as returned. If `new` is empty, say so and stop.

### Step 7: Output

```markdown
## Job Scan — [portal] — [date]
Query: [query] | Period: last N days
Found: X | Already evaluated: Z | **New: N**

| # | Role | Company | Location | Score | Status | Reason | Link |
|---|------|---------|----------|-------|--------|--------|------|
```

One row per `new` entry, sorted by score, highest first. Every value comes from the script's output.

### Step 8: Record

```bash
PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py record < output/scan-jobs/new.json
```

where `output/scan-jobs/new.json` holds the `new` array from Step 6. It appends the rows and stamps `Last updated`; it refuses an ad that is already cached. A status change Nick asks for later ("mark X as applied") is the one hand edit of the cache this skill makes.

### Step 9: Pipeline proposals

For each entry in `propose_for_pipeline`, ask: `[Company] — [Role] (score N/10) — add to pipeline? (Y/N)`. If the entry has `possible_match`, say so first: `([Company] may be [possible_match], already in the pipeline)`. On Y:

```bash
PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . add "<Company>" "<Role>" --url "<url>" --stage Researching
```

### Step 10: Summary

New ads found, how many shortlisted, and which were added to the pipeline. No trend commentary unless the lookback spans several days and N is large enough to mean something.
