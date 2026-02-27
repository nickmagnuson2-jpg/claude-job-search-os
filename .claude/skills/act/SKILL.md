---
name: act
description: Autonomously execute actionable to-dos (careers checks, company research, article reads) — preview first, then run in parallel with right-sized models
argument-hint: [none]
user-invocable: true
allowed-tools: Read(*), Write(data/job-todos.md), Write(output/**), WebFetch(*), WebSearch, Task, Glob(output/**), Glob(inbox/*), Write(data/job-pipeline.md), Write(data/networking.md), Edit(data/networking.md), Write(data/notes.md), Edit(data/notes.md), Bash(rm inbox/*)
---

# Act — Execute Actionable To-Dos

Scans `data/job-todos.md` for to-dos Claude can autonomously execute (careers page checks, company research dossiers, article reads, resource browses), previews what it found for your approval, then runs everything in parallel and reports results. Marks completed items as Done with a one-sentence result note.

## Instructions

### Step 1: Load Data (parallel)

Read the following in parallel:

1. `data/job-todos.md` — the active to-do list
2. `data/profile.md` — candidate background, target roles, and location (for role-matching in careers checks)
3. `data/job-pipeline.md` — active pipeline (to note when a company is already being tracked)
4. Glob `output/**/*.md` — to detect existing company dossiers and their freshness (catches `output/<slug>/<slug>.md` layout)
5. Glob `inbox/*` — list captured inbox items (filenames only; skip README.md)

**Extract from profile.md:** the candidate's target role types (e.g., Chief of Staff, Strategy & Operations, Head of Operations, Director of Operations, Business Operations, Strategic Finance, Head of Strategy, VP Operations). These are the keywords used to match careers page results.

**Build fresh dossier list:** For each file found under `output/`, read the `Last updated:` date from the file header. A dossier is "fresh" if Last updated is within the last 30 days. Build a map of: company slug → (exists, fresh, date). A file at `output/amae-health/amae-health.md` counts as the Amae Health dossier.

**Flag blocked URLs:** While reading each Pending to-do, check if its Notes field contains the string `"access blocked"` (case-insensitive). Tag those rows as `blocked: true`. These will be pre-filtered in Step 2.

**Flag careers check freshness:** While reading each Pending to-do that would be a careers check, scan the Notes field for the pattern `"Checked YYYY-MM-DD"`. Parse the most recent such date. If it is within 7 days of today, tag the row as `careers_fresh: true` with that date stored.

**Build inbox list:** From the Glob results, collect all files in `inbox/` that are not `README.md`. Read each file to extract its content. Classify each inbox item using these rules **in priority order** (first match wins):

| Priority | Type | Detection rules | Disposition |
|----------|------|-----------------|-------------|
| 1 | **Job ad** | Content contains a URL matching known ATS patterns: `greenhouse.io`, `lever.co`, `linkedin.com/jobs/`, `myworkdayjobs.com`, `jobs.ashbyhq.com`, `smartrecruiters.com` — OR any `/careers/` URL ending in a numeric or UUID job ID | Write to `data/job-pipeline.md` immediately when user confirms |
| 2 | **Contact capture** | Content mentions a person's full name (two capitalized words) alongside context words: "met", "intro", "talk to", "reach out", "connect with", "introduced me to" | Write to `data/networking.md` immediately when user confirms |
| 3 | **Article to read** | Content contains a URL where the domain is a media/editorial source (e.g., techcrunch, forbes, statnews, mobihealthnews, axios, substack), OR the URL path contains `/news/`, `/blog/`, `/article/`, `/post/`, `/insights/` | Add to Bucket A for this session — execute now |
| 4 | **Company to research** | Content mentions a company name alongside: "check out", "research", "look into", "interesting", "target", OR just a company name + company homepage URL (no specific job path) | Add to Bucket A for this session — execute now |
| 5 | **Unclassifiable** | Doesn't match any above pattern | If a company name is identifiable in the content, write to `data/company-notes/<slug>.md` (create if needed). Otherwise write to `data/notes.md`. Both confirmed by user before writing. |

**Company name extraction from job ad URLs:**
- `job-boards.greenhouse.io/[slug]/` → use slug
- `jobs.lever.co/[slug]/` → use slug
- `jobs.ashbyhq.com/[slug]/` → use slug
- Otherwise: use company name from surrounding text; if not findable, leave as "Unknown"

De-slug the company name for display: convert hyphens to spaces, title case each word (e.g., `spring-health` → "Spring Health").

Store the inbox classification results. Article read and company research inbox items will be added to Bucket A. Job ads, contacts, and unclassifiable items will be shown in an "Immediate Routes" table and written when the user confirms.

### Step 2: Scan & Categorize

Filter the Active section of `data/job-todos.md` to rows where **Status = Pending only** (skip Done, Withdrawn, In Progress).

**Pre-filter 1 — Blocked URLs:** Any Pending row tagged `blocked: true` → force to Bucket B (Manual) regardless of task text. Label it "(Previously blocked — check manually)" in the manual table. Do NOT attempt to re-fetch. Skip the remaining categorization rules for this row.

**Pre-filter 2 — Careers check freshness:** Any Pending row tagged `careers_fresh: true` → move to Skipped section with label: `"Checked [date] — re-check after [date + 7 days]"`. Do NOT include in Bucket A or Bucket B. Skip the remaining categorization rules for this row.

Then classify each remaining Pending to-do into one of these buckets:

#### Bucket A — Executable by Claude

| Category | Detection Pattern | Action |
|----------|------------------|--------|
| **Careers check** | Task text contains "Check [Company] careers" or "Check [Company] for [role types]" AND Notes contain a URL | Fetch careers page URL; scan for roles matching candidate's target role keywords |
| **Company research** | Task text contains "Research [Company]", "Deep-dive research [Company]", or Notes explicitly say "Run `/research-company`" | Full research-company pipeline: 5 parallel agents, save dossier to `output/[slug]/[slug].md` |
| **Article/content read** | Task text begins with "Read" or "Review [document/article]" AND Notes contain a specific article or document URL (not a general platform URL) | Fetch URL; return 4-5 key insights and why it matters; save to `output/[slug]/[MMDDYY]-article-[article-slug].md` (if company-associated) or `output/[MMDDYY]-article-[article-slug].md` (if no company) |
| **Resource browse** | Task text begins with "Browse [platform]" AND Notes contain a platform URL AND task specifies a filter criterion | Fetch URL; return top 3-5 results matching the filter criterion |

**Careers check URL extraction:** Pull the URL from the Notes field — it is always the URL in `[text](url)` markdown format or a bare URL in the notes.

**Company research skip check:** Before adding a company research item to Bucket A, check the fresh dossier list from Step 1. If a fresh dossier exists (< 30 days old), move this item to the **Skipped** list instead. If a dossier exists but is stale (≥ 30 days old), include it in Bucket A with a note that it's a refresh.

**Inbox items in Bucket A:** Append any inbox Article read and Company research items to the end of Bucket A (after the regular to-do items). Label them with `(inbox)` in the preview table.

#### Bucket B — Manual Action Required (cannot execute)

| Category | Detection Pattern |
|----------|------------------|
| **Subscriptions** | "Subscribe to", "Join [Slack / community / email list]" |
| **Social actions** | "Follow [person] on LinkedIn", "Connect with [person/company]" |
| **Physical / in-person** | "Visit", "Attend", "Text", "Call" |
| **Alumni network** | "Identify Tuck alumni", "Check Tuck [network]" — requires MyTuck access |
| **Study / learn** | "Learn [topic]", "Study [topic]", "Listen to [podcast]" — no fetchable URL |
| **Outreach** | "Follow up with", "Send email to", "Reach out to" |
| **Previously blocked** | Tagged `blocked: true` from pre-filter 1 |

For each Bucket B item, extract any direct link from the Notes field to display in the manual actions table.

**Priority sort:** After categorizing, sort Bucket A items by Priority column: High → Med → Low → (no priority). Apply the same sort to Bucket B.

#### Skip entirely (don't show)
- Status = Done, Withdrawn, or In Progress
- Items tagged `careers_fresh: true` (shown in the Skipped section instead)
- Items where the company already has a fresh dossier AND the to-do is a research task (shown in Skipped section instead)

### Step 3: Preview Display

Display the full categorized plan. **Do not execute anything yet.** Show all sections:

```markdown
## /act — Preview
> Today: [date] | Found **N items to execute automatically** and **M items requiring your action**.

### Will Execute Automatically

| # | Type | Task | URL / Source |
|---|------|------|-------------|
| 1 | Careers check | Check Playlist-EGYM careers — S&O/Ops roles | playlist.com/careers |
| 2 | Company research | Deep-dive Talkiatry — #2 BH target | 5 agents + dossier (~5 min) |
| 3 | Company research | Deep-dive Headway — #3 BH target | 5 agents + dossier (~5 min) |
| 4 | Company research | Deep-dive Spring Health — #4 BH target | 5 agents + dossier (~5 min) |
| 5 | Company research | Deep-dive Brightside Health | 5 agents + dossier (~5 min) |
| 6 | Article read | Alex Mullin — OUD treatment white paper | mobihealthnews.com |
| 7 | Article read | (inbox) MobiHealthNews article on digital health | mobihealthnews.com |
| 8 | Company research | (inbox) Ripple Foods | 5 agents + dossier |

> Company research tasks run in parallel — all execute simultaneously, not sequentially.
> Careers checks and article reads also run in parallel.

[If more than 3 company research items in Bucket A, insert this warning block:]
> ⚠️ **4 of 8 items are company research tasks** — each spawns 5 parallel agents.
> Type `run fast` to skip research and run only careers checks + article reads.
> Type `run research` to run ONLY the research tasks.

### Skipped — Fresh Dossier Exists (< 30 days)
- **Amae Health** — dossier from 2026-02-18 (`output/amae-health/amae-health.md`)

### Skipped — Careers Page Checked Recently
- **Wellhub** — Checked 2026-02-22 — re-check after 2026-03-01

### Requires Your Action

| # | Task | Direct Link |
|---|------|-------------|
| 9 | Subscribe to BHT Newsletter, Out-Of-Pocket, BHB, Rock Health Weekly | [BHT signup] · [Out-of-Pocket] · [BHB] · [Rock Health] |
| 10 | Follow Solome Tibebu, Nikhil Krishnan on LinkedIn | LinkedIn search |
| 11 | Identify 3–5 Tuck alumni in fitness/wellness | MyTuck network — manual |
| 12 | ~~Check Wellhub careers~~ (Previously blocked — check manually) | wellhub.com/en-us/careers |

### Inbox — Immediate Routes
These will be written to data files when you confirm (no execution needed):

| # | File | Content | Action |
|---|------|---------|--------|
| I3 | greenhouse-job.md | "https://job-boards.greenhouse.io/openai/jobs/123" | → New pipeline entry: OpenAI \| Researching |
| I4 | sarah-chen.md | "met Sarah Chen at SF health event, head of ops at Headway" | → New contact: Sarah Chen \| Headway \| Head of Ops |

> These write automatically when you type `run` or `run [numbers]`. No separate `inbox` command needed.

[If inbox is empty:]
> Inbox clear — nothing to route.

---
**Type `run` to execute all · `run fast` (skip research) · `run research` (research only) · `run 1 2 5` (specific) · `skip 3` (exclude) · `cancel` to abort**
```

Wait for the user's response before proceeding to Step 4.

### Step 4: Parse User Response

Parse the user's reply:

- **`run`** or **`yes`** or **`go`** → execute all items in the "Will Execute Automatically" table; also write all Immediate Route items (job ads → pipeline, contacts → networking, unclassifiable → notes)
- **`run [numbers]`** (e.g., `run 1 3 5`) → execute only those numbered items; include all Immediate Route writes
- **`skip [numbers]`** (e.g., `skip 3 4`) → execute all automated items *except* those numbered; include all Immediate Route writes
- **`run fast`** → build execute list with only Careers Check and Article Read items (including inbox article reads). Company research items — both regular to-dos and inbox ones — stay Pending with no note update. Include all Immediate Route writes.
- **`run research`** → build execute list with only Company Research items (including inbox company research). Careers checks and article reads are skipped. Include all Immediate Route writes.
- **`cancel`** or **`no`** → abort; display "Cancelled — no changes made." Do not write anything, do not delete any inbox files.
- **Unrecognized input** → respond: "I didn't understand that. Type `run` to execute all, `run [numbers]` for specific items, `skip [numbers]` to exclude, `run fast` to skip research, `run research` for research only, or `cancel` to abort." Wait for the user's next response. **Do NOT execute.**

Build the final **execute list** from the parsed response.

**Immediate Route writes (job ads, contacts, unclassifiable) — write in parallel with execution:**

**Job ad → `data/job-pipeline.md`:** Append a new row to the Active Pipeline section:
```
| [Company name] | [Role if extractable from text, else "—"] | Researching | [YYYY-MM-DD] | Run /research-company, then /generate-cv | — | Added from inbox/[filename] | [full URL] |
```

**Contact capture → `data/networking.md`:** Two writes:
1. Append a new row to the Contacts table:
   ```
   | [Name] | [Company if found] | [Role if found] | other | [YYYY-MM-DD] | — |
   ```
2. Append a new interaction log entry under `## Interaction Log`:
   ```
   ### [Name] — [Company or "Unknown"]

   #### [YYYY-MM-DD] | other | Captured from inbox

   > [Raw content of the inbox file]

   **Follow-up:** Review and update relationship type, add outreach if appropriate.
   ```

**Unclassifiable → company `notes.md` or `data/notes.md`:**

First, check if a company name is identifiable in the inbox item content. If yes:
- Write to `data/company-notes/<slug>.md`. If it doesn't exist, create it:
  ```markdown
  # [Company Name] — Notes

  > Running log of raw notes, call prep, and observations.
  > Newest entries at the top. One section per date + context.

  ---
  ```
  Then prepend a new entry at the top of the log:
  ```markdown
  ## [YYYY-MM-DD] | From inbox
  [Raw content of inbox file]
  ```

If no company is identifiable, fall back to `data/notes.md`. If `data/notes.md` doesn't exist, create it first:
```markdown
# Notes

> General notes, decisions, and unroutable captures.
> Managed by /remember and /act.

## Decisions

## Notes

## From Inbox
```
Then append under `## From Inbox`:
```markdown
**[YYYY-MM-DD] — from inbox/[filename]:**
[Raw content of inbox file]
```

**Inbox cleanup — delete source files:** After each inbox item is successfully processed (executed OR immediately written), delete the source file with `Bash(rm inbox/[filename])`. Do NOT delete if: the write or execution failed; the user skipped the item; or `cancel` was typed.

### Step 5: Execute (parallel)

Launch all items in the execute list **simultaneously** using the Task tool. Use right-sized models to control cost:

#### Careers Checks — `model: "haiku"`, `max_turns: 5`

One Task agent per company. Prompt:

```
You are checking [Company]'s careers page for open roles relevant to this candidate.

Target role types (what to match against): [extracted from profile.md — e.g., Chief of Staff,
Strategy & Operations, Head of Operations, Director of Operations, Business Operations,
Strategic Finance, Head of Strategy, VP Operations, Senior Program Manager]

Careers page URL: [URL from notes]

Instructions:
1. Fetch the URL.
2. Scan all listed roles for matches against the target role types above.
   Match on title keywords — do not require exact string match (e.g., "Director, Ops" matches "Head of Operations").
3. Return:
   a. Matching roles: title | team/department | location | application URL (if findable on page)
   b. Approximate total roles on page
   c. "No relevant roles found" if nothing matches

Format as a compact bullet list. No preamble. No explanation of methodology.
```

#### Company Research — `model: "sonnet"`, `max_turns: 15`

One Task agent per company. Prompt:

```
Run a full company research dossier for [Company Name] using parallel web research.

You have access to: WebSearch, WebFetch, Read, Write, Task, Glob.

Research process:
1. Launch 5 parallel sub-agents (using Task tool, model: "haiku", max_turns: 8) covering:
   - Agent 1: Company overview — mission, product, business model, differentiation
   - Agent 2: Funding & financials — raise history, investors, revenue signals, runway
   - Agent 3: Team & culture — founders, key leaders, culture signals, Glassdoor/LinkedIn signals
   - Agent 4: Recent news — last 12 months of press, announcements, milestones
   - Agent 5: Competitive landscape — 3-5 similar companies, how [Company] differentiates

2. After all agents return, synthesize into a dossier with these sections:
   - Executive Summary (3-5 sentences: what they do, funding stage, key differentiators, why relevant)
   - Company Overview
   - Funding & Financials
   - Team & Culture
   - Recent News & Milestones
   - Competitive Landscape & Similar Companies
   - Conversation Starters (3 specific, researched talking points for the candidate to use)
   - Next Steps (suggested: add to pipeline, draft outreach, etc.)

3. Save the completed dossier to: output/[slug]/[slug].md
   File header must include: `Last updated: [today's date in YYYY-MM-DD format]`
   Slug format: lowercase company name with spaces replaced by hyphens (e.g., "Spring Health" → spring-health)
   The directory will be created automatically by the Write tool.

4. Return only the Executive Summary (3-5 sentences) — the full dossier is in the file.

Candidate context:
[paste profile.md content here — name, background, target roles, location]
```

#### Article Reads — `model: "haiku"`, `max_turns: 4`

One Task agent per article. Prompt:

```
Fetch and summarize this article for a job seeker:

URL: [URL from notes]
Article context: [task text — e.g., "Alex Mullin's white paper on digital health and OUD treatment"]

Instructions:

1. Fetch and read the article.

2. Identify the PRIMARY company this article is about. Look for:
   - A single company named as the subject (e.g., "Amae Health announces...", "Alex Mullin from Amae Health")
   - If the article is primarily about one company, set primary_company = that company name
   - If it covers multiple companies equally, or is a general industry piece, set primary_company = null

3. Return in this format:
   **Title:** [article title]
   **Publication:** [pub] | **Author:** [author] | **Date:** [article date if found]
   **Primary company:** [company name or "None — industry article"]

   ## Key Insights
   - [insight 1]
   - [insight 2 — up to 5, specific facts not vague summaries]

   ## Why It Matters
   [1-sentence relevance for this job search]

4. Save the full summary to a file:
   - If primary_company is set: output/[slug]/[MMDDYY]-article-[article-slug].md
     where [slug] = company name lowercased, hyphens for spaces (e.g., "amae-health")
   - If primary_company is null: output/[MMDDYY]-article-[article-slug].md
   - [article-slug] = article title lowercased, spaces to hyphens, max 5 words (e.g., "oud-treatment-digital-health")
   - [MMDDYY] = today's date formatted as MMDDYY (e.g., 022426 for Feb 24, 2026)

   File format:
   # Article Read: [Title]
   > Source: [URL] | Summarized: [YYYY-MM-DD] | Task: [original task text]

   **Publication:** [pub] | **Author:** [author] | **Date:** [article date if found]
   **Associated company:** [company name or "—"]

   ## Key Insights
   - [insights]

   ## Why It Matters
   [relevance]

5. Return the save path along with the summary.

If URL is behind a paywall: report "Paywall / access blocked — [URL]", save nothing.
```

#### Resource Browses — `model: "haiku"`, `max_turns: 4`

One Task agent per task. Prompt:

```
Browse this platform and return results matching the specified filter:

Platform URL: [URL from notes]
Filter criteria: [criteria extracted from task text — e.g., "active SF yoga studio listings for sale"]

Return:
- Top 3-5 results matching the filter
- For each: name/title, price or key detail, brief description, direct link if available

No preamble. Structured list only.
If the page requires login or returns an error, report: "Access blocked — [URL]"
```

### Step 6: Collect Results & Update To-Dos

After all agents return, process results one by one:

**For each successfully executed item:**
- Status → `Done`
- Notes → append ` | Completed [today's date] — [1-sentence result summary]`
  - Careers check: "N relevant roles found" or "No relevant roles found"
  - Company research: "Dossier saved to output/[slug]/[slug].md"
  - Article read: "[first key insight, ~80 chars] — Summary: [save path returned by agent]"
  - Resource browse: "N results found matching filter"

**For each failed/blocked item (404, paywall, access denied):**
- Status remains `Pending`
- Notes → append ` | Checked [today's date] — access blocked`

Write the updated `data/job-todos.md`.

### Step 7: Display Results Report

```markdown
## /act — Results
**[date]** | Executed: N | Marked Done: N | Failed/blocked: N | Manual actions: M

---

### Careers Checks

**Playlist-EGYM** (playlist.com/careers) — 3 relevant roles found
- Head of Strategy & Operations — San Francisco — [Apply](url)
- Director of Business Operations — Remote-friendly — [Apply](url)
- Senior Program Manager — San Francisco — [Apply](url)
> 47 total open roles on page.

**Wellhub** (wellhub.com/en-us/careers) — No relevant roles found
> 89 total open roles — none matched CoS/S&O/Operations profile.

---

### Company Research

**Talkiatry** — dossier saved: `output/talkiatry/talkiatry.md`
> $210M Series D (Feb 2026), 600+ psychiatrists on platform, telehealth-first SMI focus.
> Strategy & Ops team actively hiring. Strong fit for S&O background.
> Run `/research-company "Talkiatry"` for deeper refresh, or `/prep-interview "Talkiatry"` when applying.

**Headway** — dossier saved: `output/headway/headway.md`
> ...

---

### Articles Read

**Alex Mullin — Digital Health and OUD Treatment** (MobiHealthNews)
- Digital health interventions reduce OUD treatment dropout by 40% in pilot studies
- Insurance parity laws are expanding reimbursement for digital therapeutics
- [3 more key insights]
- Why it matters: Provides credibility talking points for the Amae Health coffee chat with Alex.
> Summary saved: `output/amae-health/022426-article-oud-treatment-digital-health.md`

---

### Resource Browse

**BizBuySell — SF Yoga Studios for Sale**
- Yoga Flow SF (3 locations) — asking $850K — [link]
- [2 more listings]

---

### Failed / Blocked
- Xponential Fitness careers page — returned 403 (blocked) — to-do remains Pending, labeled "access blocked" in notes

---

### Inbox Routed

- **OpenAI job ad** → New entry added to `data/job-pipeline.md`
- **Sarah Chen** → New contact added to `data/networking.md`
- **MobiHealthNews article** → Executed as article read (see Articles Read above)
- **Ripple Foods** → Executed as company research (see Company Research above)
- Inbox files deleted: 4

> Run `/todo` to see updated to-do list · Run `/pipe` to see pipeline · Run `/networking` to see contacts

---

### Manual Actions (your queue)
These require your action — links provided for speed:

| Task | Direct Link |
|------|-------------|
| Subscribe to BHT Newsletter | [url] |
| Subscribe to Out-Of-Pocket | [url] |
| Follow Solome Tibebu on LinkedIn | [linkedin.com/in/...] |
| Follow Nikhil Krishnan on LinkedIn | [linkedin.com/in/...] |
| Join Mental Health Startup Community Slack | [url] |
| Identify Tuck alumni in fitness/wellness | [MyTuck network](https://my.tuck.dartmouth.edu) |
| ~~Check Wellhub careers~~ (Previously blocked — check manually) | wellhub.com/en-us/careers |

---

### To-Dos Updated
**N items marked Done** in `data/job-todos.md` · Run `/todo` to see updated list.

### Suggested Next Steps
[Based on what was found — e.g., if Playlist-EGYM has strong roles: "Consider `/pipe add 'Playlist-EGYM' 'Head of Strategy & Operations'` and `/generate-cv [JD url]`"]
```

## Edge Cases

- **No actionable to-dos found:** Display "No actionable to-dos in the current list. All pending items require manual action." then show the manual actions table and stop.
- **Careers page URL missing from notes:** Skip the item, list it in the preview as "⚠️ Skipped — no URL in notes" so the user can add one manually.
- **Fresh dossier already exists (< 30 days):** Show in "Skipped" section of preview. Do not overwrite fresh dossiers automatically.
- **Stale dossier exists (≥ 30 days):** Include in execute list, note "(refresh)" in the preview table.
- **Company research agent fails:** Note failure in results, leave Status as Pending with failure note in Notes field.
- **User runs `/act` with nothing actionable and asks "why?":** Explain the classification rules briefly — which patterns trigger each category.
- **Multiple to-dos for the same company research target:** Deduplicate — run research only once per company, mark all related research to-dos as Done from that one run.
- **Article agent returns no save path (paywall/blocked):** Note "Paywall / access blocked" in the to-do Notes field. Status remains Pending. No file created.

## Model Selection Rationale

| Task type | Model | Why |
|-----------|-------|-----|
| Careers check | Haiku | Simple web fetch + keyword scan — no synthesis needed |
| Article read | Haiku | Fetch + summarize — straightforward extraction |
| Resource browse | Haiku | Fetch + filter — no analysis |
| Company research (sub-agents) | Haiku | Each agent fetches one focused topic — no cross-synthesis |
| Company research (orchestrator) | Sonnet | Must synthesize 5 agent outputs into a coherent dossier |
| Act skill itself (top-level) | Inherits from session | Categorization + report writing is the complex orchestration step |
