# Phase 2: Browser Automation for Job Discovery - Research

**Researched:** 2026-04-08
**Domain:** ATS API integration, browser automation, job scoring
**Confidence:** HIGH

## Summary

Phase 2 builds a multi-company career page scanner that fetches open roles, scores them against the user's profile, deduplicates against the existing pipeline, and writes matches to `data/inbox.md`. The key insight from CONTEXT.md decision D-01 is that three major ATS platforms (Greenhouse, Lever, Ashby) expose public JSON APIs requiring no authentication, making HTTP requests the primary extraction method. Playwright is only needed for the generic fallback parser handling custom career pages.

All three ATS APIs were verified working during this research. Greenhouse returns structured job data at `boards-api.greenhouse.io/v1/boards/{company}/jobs`, Lever at `api.lever.co/v0/postings/{company}?mode=json`, and Ashby at `api.ashbyhq.com/posting-api/job-board/{company}`. Response formats differ but all return job title, location, URL, and department/team data. The project already has established patterns for HTTP clients (urllib in `granola_fetch.py`), inbox writing (`granola_auto_debrief.py`), and pipeline reading (`pipe_read.py`) that this phase should follow.

**Primary recommendation:** Build `tools/career_scanner/` as a Python package with per-ATS parser modules sharing a common interface, a scoring engine reading profile.md/goals.md, and an orchestrator script following the `granola_auto_debrief.py` pattern for n8n integration.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** API-first approach. Use lightweight HTTP requests (urllib/requests) for ATS platforms with public JSON APIs (Greenhouse, Lever, Ashby). Reserve Playwright browser automation only for custom career pages without an API.
- **D-02:** Generic fallback parser uses Playwright + heuristics for companies without a known ATS. Extract job listings by common HTML patterns (h2/h3 titles, links containing 'apply', structured lists). Best-effort, may miss some.
- **D-03:** Each ATS platform gets its own parser module in `tools/career_scanner/`. Parsers share a common interface returning standardized role objects.
- **D-04:** Four scoring dimensions, all weighted: title match, seniority match, industry/domain match, keyword overlap. Score each role 1-10 against `data/profile.md` and `data/goals.md`.
- **D-05:** Surface all discovered roles sorted by fit score. No filtering threshold - user decides what's worth pursuing. No roles hidden.
- **D-06:** Target company list stored as YAML/JSON config file (not markdown table). More machine-readable for the scanner scripts.
- **D-07:** Initial target list seeded from pipeline + goals hybrid: companies already in `data/job-pipeline.md` plus new ones derived from `data/goals.md` industry/size targeting criteria.
- **D-08:** Config includes per-company fields: name, ATS platform, careers URL, role title filters, active flag.
- **D-09:** Scan results written to `data/inbox.md` following the Phase 1 pattern. User reviews during /standup and routes to pipeline.
- **D-10:** Deduplication by company name (exact match) + role title (fuzzy, ~80% similarity). If already in pipeline, skip silently.

### Claude's Discretion
- Specific JSON API endpoint URLs and response parsing for each ATS platform
- Scoring weight distribution across the four dimensions
- Playwright heuristic patterns for generic career page parsing
- n8n scheduling interval (suggest daily or twice-daily)
- YAML vs JSON format choice for scan-targets config

### Deferred Ideas (OUT OF SCOPE)
None - discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| R2.1 | Playwright scanner that navigates to a company's careers page and extracts open roles | Only needed for generic fallback (D-01). ATS APIs handle Greenhouse/Lever/Ashby. Playwright install verified needed. |
| R2.2 | Configurable target company list (company name, careers URL, role filters) | YAML config file (D-06). PyYAML 6.0.3 verified available. Per-company schema documented below. |
| R2.3 | Auto-score each discovered role against profile.md and goals.md (1-10 fit score) | Four dimensions (D-04). Existing /scan-jobs skill has scoring patterns to reference. |
| R2.4 | Deduplication against existing job-pipeline.md entries | pipe_read.py already parses pipeline into company_index. difflib.SequenceMatcher for fuzzy title match. |
| R2.5 | Output new matches to data/inbox.md with scores | granola_auto_debrief.py pattern: prepend after header. |
| R2.6 | /scan-companies skill that runs the scanner on demand | Skill definition in .claude/skills/scan-companies/. Follows existing skill patterns. |
| R2.7 | n8n workflow that runs the scanner on a schedule | Follow n8n_dossier_nudge.py pattern. Execute Command node. |
| R2.8 | Support for at least 3 career page formats: Greenhouse, Lever, Ashby | All three public APIs verified working. Response formats documented below. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| urllib.request (stdlib) | Python 3.14 | HTTP requests for ATS APIs | Project convention from granola_fetch.py. No external dependency. [VERIFIED: codebase pattern] |
| PyYAML | 6.0.3 | Parse scan-targets config file | Already installed. Machine-readable config per D-06. [VERIFIED: python3 import] |
| difflib (stdlib) | Python 3.14 | Fuzzy string matching for dedup | SequenceMatcher handles ~80% similarity threshold per D-10. No install needed. [VERIFIED: python3 import] |
| json (stdlib) | Python 3.14 | Parse ATS API responses, CLI output | Project convention: JSON to stdout. [VERIFIED: codebase pattern] |
| playwright | latest | Browser automation for generic fallback | Only for D-02 generic parser. Not needed for Greenhouse/Lever/Ashby. [ASSUMED: pip install playwright needed] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| re (stdlib) | Python 3.14 | Title/keyword matching in scorer | Pattern matching for title and keyword overlap dimensions |
| html (stdlib) | Python 3.14 | Unescape HTML entities in API responses | Greenhouse returns HTML-encoded content field |
| pathlib (stdlib) | Python 3.14 | File path handling | Project convention from granola_fetch.py |
| argparse (stdlib) | Python 3.14 | CLI interface | Project convention: all tools/*.py use argparse |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| urllib.request | requests | External dependency. Project convention is stdlib urllib. Only add if needed for complex cases. |
| difflib.SequenceMatcher | thefuzz/fuzzywuzzy | External dependency with C extension. difflib is stdlib and sufficient for title comparison. |
| YAML config | JSON config | JSON requires no extra dependency. But YAML is more human-readable for manual editing. PyYAML already installed. Recommend YAML. |

**Installation:**
```bash
pip install playwright && playwright install chromium
```
Note: Only needed for the generic fallback parser (D-02). The three ATS API parsers use stdlib only.

## Architecture Patterns

### Recommended Project Structure
```
tools/career_scanner/
    __init__.py           # Package init, shared types
    greenhouse.py         # Greenhouse API parser
    lever.py              # Lever API parser
    ashby.py              # Ashby API parser
    generic.py            # Playwright fallback parser
    scorer.py             # Scoring engine (4 dimensions)
    dedup.py              # Deduplication against pipeline
    scanner.py            # Main orchestrator (fetch all -> score -> dedup -> output)
    cli.py                # CLI entrypoint (argparse)
data/
    scan-targets.yaml     # Target company list (D-06)
tools/
    n8n_career_scan.py    # n8n wrapper (like n8n_dossier_nudge.py)
.claude/skills/
    scan-companies/
        SKILL.md          # Skill definition for /scan-companies
```

### Pattern 1: Standardized Role Object
**What:** All parsers return a common dict structure regardless of ATS platform.
**When to use:** Every parser must conform to this interface.
**Example:**
```python
# Source: designed for this phase based on ATS API field analysis
role = {
    "title": "Senior Product Manager",
    "company": "Ramp",
    "department": "Product",
    "team": "Growth",            # optional
    "location": "New York, NY",
    "remote": True,              # bool
    "employment_type": "FullTime",
    "url": "https://jobs.ashbyhq.com/ramp/abc123",
    "apply_url": "https://jobs.ashbyhq.com/ramp/abc123/application",
    "published_at": "2026-04-07",
    "description_plain": "...",  # plain text, for keyword scoring
    "ats": "ashby",              # source ATS
}
```

### Pattern 2: HTTP Client (follow granola_fetch.py)
**What:** Use stdlib urllib.request with _load_dotenv(), JSON parsing, error handling.
**When to use:** All ATS API calls.
**Example:**
```python
# Source: tools/granola_fetch.py pattern
import urllib.request
import json

def fetch_greenhouse_jobs(board_token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("jobs", [])
```

### Pattern 3: Inbox Writer (follow granola_auto_debrief.py)
**What:** Prepend formatted entries to data/inbox.md after the header block.
**When to use:** Writing scan results.
**Example:**
```python
# Source: tools/granola_auto_debrief.py lines 196-225
# Read existing content, find insertion point after header + comments,
# prepend new entries, write full file (per CLAUDE.md conventions)
```

### Pattern 4: n8n Automation Script
**What:** Thin wrapper that calls the scanner, writes inbox items, outputs JSON summary.
**When to use:** Scheduled execution via n8n.
**Example:**
```python
# Source: tools/n8n_dossier_nudge.py pattern
# 1. Parse --repo-root arg
# 2. Call scanner.scan_all(repo_root)
# 3. Format results as inbox entries
# 4. Write to data/inbox.md
# 5. Print JSON summary to stdout
```

### Anti-Patterns to Avoid
- **Using Playwright for ATS pages:** Greenhouse, Lever, and Ashby all have public JSON APIs. Using a headless browser is slower, flakier, and unnecessary. Reserve Playwright only for generic fallback.
- **Hardcoding company slugs:** The target list must be configurable per D-06. Never embed company names in parser code.
- **External dependencies for simple tasks:** This project uses stdlib urllib, not requests. Use difflib, not thefuzz. Follow existing patterns.
- **Writing directly to pipeline:** Scanner writes to inbox only (D-09). User routes to pipeline during /standup.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fuzzy string matching | Custom edit-distance | `difflib.SequenceMatcher` | Stdlib, handles ~80% threshold well, no install |
| Pipeline parsing | Custom markdown parser | Import from `tools/pipe_read.py` | Already handles all column mapping and company_index |
| Inbox writing | Custom file writer | Follow `granola_auto_debrief.py` pattern | Handles header detection, insertion point, full-file write |
| HTML unescaping | Regex replacements | `html.unescape()` | Stdlib, handles all HTML entities |
| YAML parsing | Custom config parser | `yaml.safe_load()` | PyYAML already installed |

**Key insight:** This project has strong conventions for HTTP clients, inbox writing, and pipeline reading. Reuse these patterns rather than inventing new approaches.

## ATS API Reference

### Greenhouse Public API
**Endpoint:** `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` [VERIFIED: live API test]
**With content:** Add `?content=true` to get job descriptions, departments, offices
**Auth:** None required (public API)
**Response structure:**
```json
{
  "jobs": [
    {
      "id": 8289766002,
      "title": "Account Executive - Japan",
      "absolute_url": "https://job-boards.greenhouse.io/discord/jobs/8289766002",
      "location": {"name": "Japan"},
      "updated_at": "2026-04-02T14:11:22-04:00",
      "first_published": "...",
      "departments": [{"id": 123, "name": "Sales"}],
      "offices": [...],
      "content": "<div>HTML job description</div>"
    }
  ],
  "meta": {"total": 84}
}
```
**Pagination:** All jobs returned in single response (no pagination needed for typical board sizes). [VERIFIED: Discord returns 84 jobs in one call]
**Board token:** The company slug used in their Greenhouse URL. Example: `discord` for Discord.
**Note:** `content` field contains HTML that needs unescaping for plain text scoring.

### Lever Public API
**Endpoint:** `https://api.lever.co/v0/postings/{company}?mode=json` [VERIFIED: live API test]
**Auth:** None required (public API)
**Response structure:**
```json
[
  {
    "id": "33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
    "text": "Senior Software Engineer",
    "hostedUrl": "https://jobs.lever.co/company/33538a2f...",
    "applyUrl": "https://jobs.lever.co/company/33538a2f.../apply",
    "categories": {
      "commitment": "Regular Full Time (Salary)",
      "department": "Engineering",
      "location": "San Francisco, CA",
      "team": "Backend",
      "allLocations": ["San Francisco, CA", "Remote"]
    },
    "createdAt": 1553186035299,
    "descriptionPlain": "Full job description in plain text...",
    "descriptionBody": "HTML description of the role",
    "descriptionBodyPlain": "Plain text version of body"
  }
]
```
**Pagination:** Returns all postings in single response (array). [VERIFIED: leverdemo returns 387 jobs]
**Company slug:** The company identifier in their Lever URL. Finding the right slug requires knowing the company's Lever account name.
**Note:** `createdAt` is a Unix timestamp in milliseconds. `text` is the job title field (not `title`).
**Gotcha:** Returns `{"ok": false, "error": "Document not found"}` for invalid/non-existent company slugs. Must handle gracefully.

### Ashby Public API
**Endpoint:** `https://api.ashbyhq.com/posting-api/job-board/{company}` [VERIFIED: live API test]
**Auth:** None required (public API)
**Response structure:**
```json
{
  "jobs": [
    {
      "id": "34413f8d-26bf-4bbc-8ade-eb309a0e2245",
      "title": "Security Engineer, Cloud",
      "department": "Engineering",
      "team": "Backend",
      "employmentType": "FullTime",
      "location": "New York, NY (HQ)",
      "isRemote": false,
      "workplaceType": "...",
      "secondaryLocations": [...],
      "publishedAt": "2026-04-07T17:12:35.753+00:00",
      "jobUrl": "https://jobs.ashbyhq.com/ramp/34413f8d...",
      "applyUrl": "https://jobs.ashbyhq.com/ramp/34413f8d.../application",
      "descriptionPlain": "Full description in plain text",
      "descriptionHtml": "<div>HTML description</div>"
    }
  ],
  "apiVersion": 1
}
```
**Pagination:** All jobs in single response. [VERIFIED: Ramp returns 132 jobs]
**Company slug:** The company identifier in their Ashby URL.
**Richest data:** Ashby returns the most structured data including `isRemote`, `workplaceType`, `employmentType`, `secondaryLocations`, and both plain text and HTML descriptions.

### Field Mapping Summary

| Standard Field | Greenhouse | Lever | Ashby |
|----------------|------------|-------|-------|
| title | `title` | `text` | `title` |
| url | `absolute_url` | `hostedUrl` | `jobUrl` |
| apply_url | (derive from url) | `applyUrl` | `applyUrl` |
| department | `departments[0].name` | `categories.department` | `department` |
| team | N/A | `categories.team` | `team` |
| location | `location.name` | `categories.location` | `location` |
| remote | (infer from location) | (infer from location) | `isRemote` |
| employment_type | N/A | `categories.commitment` | `employmentType` |
| published | `first_published` | `createdAt` (ms timestamp) | `publishedAt` (ISO) |
| description | `content` (HTML, needs `?content=true`) | `descriptionPlain` | `descriptionPlain` |

## Scoring Engine Design

### Four Dimensions (D-04)

**Recommended weights:** [ASSUMED - Claude's discretion per CONTEXT.md]

| Dimension | Weight | How to Score | Data Source |
|-----------|--------|-------------|-------------|
| Title match | 35% | Fuzzy match of role title against target titles in goals.md | `data/goals.md` target roles |
| Seniority match | 25% | Extract seniority from title (Senior, Lead, VP, etc.), compare to goals.md seniority | `data/goals.md` seniority level |
| Industry/domain match | 20% | Match company industry or role department against goals.md industries | `data/goals.md` target industries |
| Keyword overlap | 20% | Count matching keywords between job description and profile.md skills | `data/profile.md` skills + `data/goals.md` keywords |

**Scoring approach:**
1. Each dimension scores 0-10 independently
2. Final score = weighted average, rounded to nearest integer
3. Use simple keyword matching and difflib for fuzzy comparisons
4. No ML/NLP - keep it deterministic and fast

**Title match specifics:**
- Extract target role titles from goals.md (e.g., "Chief of Staff", "Head of Operations")
- Use `difflib.SequenceMatcher.ratio()` against each target title
- Score = best match ratio * 10

**Seniority match specifics:**
- Map seniority keywords: {"intern": 1, "junior": 2, "mid": 3, "senior": 5, "staff": 6, "principal": 7, "lead": 7, "director": 8, "vp": 9, "c-suite": 10}
- Extract from job title, compare to target seniority in goals.md
- Score inversely proportional to distance between levels

## Deduplication Strategy

### Implementation (D-10)

```python
# Source: stdlib difflib, pattern from /scan-jobs cache dedup
from difflib import SequenceMatcher

def is_duplicate(role_title: str, role_company: str,
                 pipeline_entries: list[dict]) -> bool:
    """Check if a role is already in the pipeline.
    
    Args:
        role_title: Title of the discovered role
        role_company: Company name from the role listing
        pipeline_entries: From pipe_read.py company_index
    
    Returns:
        True if duplicate (should skip)
    """
    company_lower = role_company.lower().strip()
    
    # Exact company match
    for entry in pipeline_entries:
        entry_company = entry["company"].lower().strip()
        if entry_company != company_lower:
            continue
        
        # Fuzzy title match (~80% threshold per D-10)
        ratio = SequenceMatcher(
            None,
            role_title.lower(),
            entry["role"].lower()
        ).ratio()
        if ratio >= 0.80:
            return True
    
    return False
```

**Pipeline data access:** Use `pipe_read.py` output - it already provides `company_index` mapping company names to roles. Can import `parse_pipeline()` directly or shell out to the script.

## Target List Config (D-06, D-08)

### Recommended: YAML format

```yaml
# data/scan-targets.yaml
# Target companies for career page scanning
# Seeded from data/job-pipeline.md + data/goals.md

companies:
  - name: Ramp
    ats: ashby
    slug: ramp
    active: true
    role_filters:
      - "product"
      - "operations"
      - "chief of staff"

  - name: Discord
    ats: greenhouse
    slug: discord
    active: true
    role_filters:
      - "strategy"
      - "operations"

  - name: Example Corp
    ats: generic        # Uses Playwright fallback
    careers_url: "https://example.com/careers"
    active: true
    role_filters: []    # Empty = show all roles

  - name: Paused Company
    ats: lever
    slug: paused-company
    active: false       # Temporarily disabled
    role_filters:
      - "engineering"
```

**Schema per company (D-08):**
- `name` (required): Display name
- `ats` (required): One of `greenhouse`, `lever`, `ashby`, `generic`
- `slug` (required for ATS): Company identifier in ATS URL
- `careers_url` (required for generic): Full URL to careers page
- `role_filters` (optional): List of keywords. If non-empty, only roles whose title contains at least one keyword are included. Empty list = all roles.
- `active` (required): Boolean flag to enable/disable scanning

**YAML over JSON rationale:** YAML supports comments (useful for noting why a company was paused), is more readable for manual editing, and PyYAML 6.0.3 is already available. [VERIFIED: PyYAML installed]

## Common Pitfalls

### Pitfall 1: Company Slug Discovery
**What goes wrong:** User adds a company to scan-targets but doesn't know its ATS slug. The API returns an error or empty results.
**Why it happens:** ATS slugs aren't always obvious. "Example Corp" might be "examplecorp", "example-corp", "example", or something entirely different.
**How to avoid:** Include clear error messages when a slug returns no results. Document how to find the slug (visit careers page, inspect URL). Consider a validation mode that tests all slugs.
**Warning signs:** `{"ok": false, "error": "Document not found"}` (Lever) or empty jobs array.

### Pitfall 2: Lever Timestamp Format
**What goes wrong:** Lever's `createdAt` is a Unix timestamp in milliseconds (e.g., `1553186035299`), not seconds. Dividing by 1 instead of 1000 produces dates in the year 51000+.
**Why it happens:** Inconsistent timestamp conventions across APIs.
**How to avoid:** Always `datetime.fromtimestamp(created_at / 1000)` for Lever timestamps.
**Warning signs:** Dates that are obviously wrong.

### Pitfall 3: Greenhouse HTML Content
**What goes wrong:** Greenhouse `content` field contains HTML entities (`&lt;`, `&amp;`, etc.). Scoring against raw HTML produces garbage keyword matches.
**Why it happens:** The API returns HTML-encoded descriptions.
**How to avoid:** Use `html.unescape()` then strip HTML tags (simple regex or `re.sub(r'<[^>]+>', '', text)`) before keyword scoring.
**Warning signs:** Score contains `lt`, `gt`, `amp` as matched keywords.

### Pitfall 4: Rate Limiting / Blocking
**What goes wrong:** Scanning 20-30 companies in rapid succession triggers rate limits or IP blocks.
**Why it happens:** Even public APIs have rate limits. Playwright requests to custom pages may trigger bot detection.
**How to avoid:** Add a small delay between API calls (0.5-1s). For Playwright, use realistic user-agent and timeouts.
**Warning signs:** HTTP 429 responses, empty responses that should have data.

### Pitfall 5: PYTHONIOENCODING
**What goes wrong:** Script crashes on Unicode characters in job titles or descriptions.
**Why it happens:** Windows/macOS terminal encoding issues. Project CLAUDE.md explicitly warns about this.
**How to avoid:** Always run with `PYTHONIOENCODING=utf-8` prefix. Include encoding parameter in all file operations.
**Warning signs:** `UnicodeEncodeError` on stdout writes.

### Pitfall 6: Edit Tool on Inbox
**What goes wrong:** Using Edit tool on `data/inbox.md` silently fails or corrupts content.
**Why it happens:** CLAUDE.md lists inbox.md adjacent files as requiring Write (full-file write). The PostToolUse hook warns but damage may be done.
**How to avoid:** Always use full-file write pattern (read -> modify -> write) for `data/inbox.md`. Never use Edit.
**Warning signs:** PostToolUse safety hook warning.

## Code Examples

### Greenhouse Parser
```python
# Source: Verified against live API (boards-api.greenhouse.io)
import json
import urllib.request
import html
import re

def fetch_greenhouse(slug: str) -> list[dict]:
    """Fetch all jobs from a Greenhouse board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Greenhouse error for {slug}: HTTP {e.code}", file=sys.stderr)
        return []
    
    roles = []
    for job in data.get("jobs", []):
        # Strip HTML from content for plain text
        content_html = job.get("content", "")
        content_plain = re.sub(r'<[^>]+>', ' ', html.unescape(content_html))
        
        roles.append({
            "title": job["title"],
            "company": job.get("company_name", slug),
            "department": (job.get("departments") or [{}])[0].get("name", ""),
            "team": "",
            "location": job.get("location", {}).get("name", ""),
            "remote": "remote" in job.get("location", {}).get("name", "").lower(),
            "employment_type": "",
            "url": job["absolute_url"],
            "apply_url": job["absolute_url"] + "#app",
            "published_at": job.get("first_published", ""),
            "description_plain": content_plain.strip(),
            "ats": "greenhouse",
        })
    return roles
```

### Fuzzy Dedup
```python
# Source: stdlib difflib, designed for D-10 requirements
from difflib import SequenceMatcher

def fuzzy_match_title(title_a: str, title_b: str, threshold: float = 0.80) -> bool:
    """Check if two role titles are similar enough to be duplicates."""
    return SequenceMatcher(None, title_a.lower(), title_b.lower()).ratio() >= threshold
```

### Inbox Entry Format
```python
# Source: follows granola_auto_debrief.py pattern
def format_scan_result(roles: list[dict]) -> str:
    """Format scan results as an inbox entry."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"## Career Scan Results",
        f"",
        f"**Scanned:** {now_str}",
        f"**New roles found:** {len(roles)}",
        f"",
    ]
    for role in sorted(roles, key=lambda r: r.get("score", 0), reverse=True):
        score = role.get("score", 0)
        lines.append(
            f"- **[{score}/10]** {role['title']} at {role['company']} "
            f"({role['location']}) - [View]({role['url']})"
        )
    lines.append(f"")
    lines.append(f"*Source: /scan-companies | {now_str}*")
    return "\n".join(lines)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Scraping career pages with Selenium/Playwright | Using public ATS JSON APIs | Always available | 10x faster, no browser dependency, more reliable |
| LinkedIn scanner (Selenium) in this project | Playwright (if browser needed) | D-01 decision | Playwright is lighter, async-capable, better for headless |
| requests library | stdlib urllib.request | Project convention | No external dependency, consistent with granola_fetch.py |

**Deprecated/outdated:**
- The LinkedIn scanner uses Selenium + webdriver-manager. New browser automation in this project should use Playwright per ROADMAP.md. [VERIFIED: linkedin-scanner/requirements.txt shows selenium]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Scoring weights (35/25/20/20) are a good starting distribution | Scoring Engine Design | Low - weights are adjustable, can tune after first real scan |
| A2 | Playwright is the right choice for generic fallback (vs Selenium already in project) | Standard Stack | Low - decision D-01/D-02 locks Playwright. If install issues arise, fallback is optional. |
| A3 | 0.5-1s delay between API calls is sufficient to avoid rate limiting | Pitfalls | Medium - if scanning 30+ companies, may need longer delays or batch scheduling |
| A4 | Seniority keyword mapping covers most job title patterns | Scoring Engine Design | Low - can expand the mapping as edge cases emerge |

## Open Questions (RESOLVED)

1. **Generic parser scope for initial release** - RESOLVED: Build as best-effort, focus testing on three ATS parsers. Generic parser built in Plan 01 Task 2.
   - What we know: D-02 specifies Playwright + heuristics for custom career pages
   - Resolution: Build the generic parser but mark it as best-effort. Focus testing on the three ATS parsers first. If most targets use Greenhouse/Lever/Ashby, the generic parser may rarely be invoked.

2. **Scoring calibration** - RESOLVED: Include --dry-run mode for calibration after first real scan. Implemented in Plan 03 CLI.
   - What we know: Four dimensions, 1-10 scale, weighted average
   - Resolution: Include a `--dry-run` mode that shows scores without writing to inbox. Let user calibrate weights after first real scan.

3. **ATS platform detection** - RESOLVED: Manual config in scan-targets.yaml for now. Auto-detection deferred.
   - What we know: User specifies ATS in config file
   - Resolution: Manual config for now. Auto-detection is a nice-to-have for later. Most companies are identifiable by their careers URL pattern.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All scripts | Yes | 3.14.3 | -- |
| PyYAML | Config parsing | Yes | 6.0.3 | Use JSON config instead |
| difflib (stdlib) | Fuzzy dedup | Yes | 3.14.3 | -- |
| urllib (stdlib) | API calls | Yes | 3.14.3 | -- |
| Playwright | Generic fallback only | No | -- | Skip generic parser initially, install when needed |
| n8n | Scheduled scanning | Yes (assumed) | -- | Manual /scan-companies only |
| pip | Installing playwright | Yes | 26.0 | -- |

**Missing dependencies with no fallback:**
- None. The three ATS parsers work with stdlib only.

**Missing dependencies with fallback:**
- Playwright: Not installed. Only needed for generic fallback parser (D-02). Install with `pip install playwright && playwright install chromium` when generic parser is implemented. The three primary ATS parsers (Greenhouse, Lever, Ashby) work without it.

## Project Constraints (from CLAUDE.md)

- All Python scripts require `PYTHONIOENCODING=utf-8` prefix for Windows/macOS compatibility
- `data/inbox.md` must use full-file Write, never Edit
- All tools/*.py use argparse, JSON to stdout, errors to stderr
- Follow `_load_dotenv()` pattern from granola_fetch.py for .env loading
- State files go in `tools/.cache/`
- n8n scripts follow n8n_dossier_nudge.py pattern (subprocess + inbox write)
- No em dashes in any output
- Profile Guard: skills that generate content must verify data/profile.md and data/goals.md exist

## Sources

### Primary (HIGH confidence)
- Greenhouse API: `boards-api.greenhouse.io/v1/boards/discord/jobs?content=true` - live tested, response structure verified [VERIFIED: live API test 2026-04-08]
- Lever API: `api.lever.co/v0/postings/leverdemo?mode=json` - live tested, response structure verified [VERIFIED: live API test 2026-04-08]
- Ashby API: `api.ashbyhq.com/posting-api/job-board/ramp` - live tested, response structure verified [VERIFIED: live API test 2026-04-08]
- Codebase: `tools/granola_fetch.py` - HTTP client pattern [VERIFIED: codebase read]
- Codebase: `tools/granola_auto_debrief.py` - inbox writing pattern [VERIFIED: codebase read]
- Codebase: `tools/pipe_read.py` - pipeline parsing + company_index [VERIFIED: codebase read]
- Codebase: `.claude/skills/scan-jobs/SKILL.md` - scoring/dedup patterns [VERIFIED: codebase read]

### Secondary (MEDIUM confidence)
- [Lever Postings API documentation](https://github.com/lever/postings-api) - endpoint structure and query parameters [CITED: github.com/lever/postings-api]
- PyYAML availability - verified via Python import [VERIFIED: python3 import]
- difflib availability - verified via Python import [VERIFIED: python3 import]

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries verified installed or stdlib, ATS APIs tested live
- Architecture: HIGH - follows established project patterns from Phase 1
- ATS APIs: HIGH - all three endpoints tested with real data, response structures documented
- Scoring: MEDIUM - design is sound but weights are assumed, needs calibration
- Pitfalls: HIGH - based on verified API behavior and project CLAUDE.md rules

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (ATS APIs are stable public endpoints, unlikely to change)
