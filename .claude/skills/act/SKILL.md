---
name: act
description: Autonomously execute actionable to-dos (careers checks, company research, article reads) — preview first, then run in parallel with right-sized models
argument-hint: [none]
user-invocable: true
allowed-tools: Read(*), Edit(data/job-todos.md), Write(data/job-todos.md), Write(data/company-research/*), Edit(data/company-research/*), WebFetch(*), WebSearch, Task, Glob(data/company-research/*), Glob(inbox/*), Write(data/job-pipeline.md), Edit(data/job-pipeline.md), Write(data/networking.md), Edit(data/networking.md), Write(data/notes.md), Edit(data/notes.md), Bash(rm inbox/*)
---

# Act — Execute Actionable To-Dos

Scans `data/job-todos.md` for to-dos Claude can autonomously execute (careers page checks, company research dossiers, article reads, resource browses), previews what it found for your approval, then runs everything in parallel and reports results. Marks completed items as Done with a one-sentence result note.

## Instructions

### Step 1: Load Data (parallel)

Read the following in parallel:

1. `data/job-todos.md` — the active to-do list
2. `data/profile.md` — candidate background, target roles, and location (for role-matching in careers checks)
3. `data/job-pipeline.md` — active pipeline (to note when a company is already being tracked)
4. Glob `data/company-research/*.md` — to detect existing dossiers and their freshness
5. Glob `inbox/*` — list captured inbox items (filenames only; skip README.md)

**Extract from profile.md:** the candidate's target role types (e.g., Chief of Staff, Strategy & Operations, Head of Operations, Director of Operations, Business Operations, Strategic Finance, Head of Strategy, VP Operations). These are the keywords used to match careers page results.

**Build fresh dossier list:** For each file found in `data/company-research/`, read the `Last updated:` date from the file header. A dossier is "fresh" if Last updated is within the last 30 days. Build a map of: company slug → (exists, fresh, date).

**Build inbox list:** From the Glob results, collect all files in `inbox/` that are not `README.md`. Read each file to extract its content (company name, URL, or note). Classify each inbox item into a proposed routing destination:
- Company name to research → suggest `/research-company` or add to Bucket A as company research
- URL to read → suggest add to Bucket A as article read or resource browse
- Contact to look up → suggest `/networking add`
- Job ad link → suggest `/pipe add` + `/generate-cv`
- Unclassifiable → route to `data/notes.md`

### Step 2: Scan & Categorize

Filter the Active section of `data/job-todos.md` to rows where **Status = Pending only** (skip Done, Withdrawn, In Progress).

Classify each Pending to-do into one of these buckets:

#### Bucket A — Executable by Claude

| Category | Detection Pattern | Action |
|----------|------------------|--------|
| **Careers check** | Task text contains "Check [Company] careers" or "Check [Company] for [role types]" AND Notes contain a URL | Fetch careers page URL; scan for roles matching candidate's target role keywords |
| **Company research** | Task text contains "Research [Company]", "Deep-dive research [Company]", or Notes explicitly say "Run `/research-company`" | Full research-company pipeline: 5 parallel agents, save dossier to `data/company-research/<slug>.md` |
| **Article/content read** | Task text begins with "Read" or "Review [document/article]" AND Notes contain a specific article or document URL (not a general platform URL) | Fetch URL; return 4-5 key insights and why it matters |
| **Resource browse** | Task text begins with "Browse [platform]" AND Notes contain a platform URL AND task specifies a filter criterion (e.g., "for active SF yoga studio listings") | Fetch URL; return top 3-5 results matching the filter criterion |

**Careers check URL extraction:** Pull the URL from the Notes field — it is always the URL in `[text](url)` markdown format or a bare URL in the notes.

**Company research skip check:** Before adding a company research item to Bucket A, check the fresh dossier list from Step 1. If a fresh dossier exists (< 30 days old), move this item to the **Skipped** list instead. If a dossier exists but is stale (≥ 30 days old), include it in Bucket A with a note that it's a refresh.

#### Bucket B — Manual Action Required (cannot execute)

| Category | Detection Pattern |
|----------|------------------|
| **Subscriptions** | "Subscribe to", "Join [Slack / community / email list]" |
| **Social actions** | "Follow [person] on LinkedIn", "Connect with [person/company]" |
| **Physical / in-person** | "Visit", "Attend", "Text", "Call" |
| **Alumni network** | "Identify Tuck alumni", "Check Tuck [network]" — requires MyTuck access |
| **Study / learn** | "Learn [topic]", "Study [topic]", "Listen to [podcast]" — no fetchable URL |
| **Outreach** | "Follow up with", "Send email to", "Reach out to" |

For each Bucket B item, extract any direct link from the Notes field to display in the manual actions table.

#### Skip entirely (don't show)
- Status = Done, Withdrawn, or In Progress
- Items where the company already has a fresh dossier AND the to-do is a research task (show these in the Skipped section of the preview instead)

### Step 3: Preview Display

Display the full categorized plan. **Do not execute anything yet.** Show all three sections:

```markdown
## /act — Preview
> Today: [date] | Found **N items to execute automatically** and **M items requiring your action**.

### Will Execute Automatically

| # | Type | Task | URL / Source |
|---|------|------|-------------|
| 1 | Careers check | Check Playlist-EGYM careers — S&O/Ops roles | playlist.com/careers |
| 2 | Careers check | Check Wellhub careers — BD/strategy roles | wellhub.com/en-us/careers |
| 3 | Company research | Deep-dive Talkiatry — #2 BH target | 5 agents + dossier (~5 min) |
| 4 | Company research | Deep-dive Headway — #3 BH target | 5 agents + dossier (~5 min) |
| 5 | Article read | Alex Mullin — OUD treatment white paper | mobihealthnews.com |
| 6 | Resource browse | Browse BizBuySell — active SF yoga studio listings | bizbuysell.com |

> Company research tasks run in parallel — all execute simultaneously, not sequentially.
> Careers checks and article reads also run in parallel.

### Skipped — Fresh Dossier Exists (< 30 days)
- **Amae Health** — dossier from 2026-02-18 (`data/company-research/amae-health.md`)

### Requires Your Action

| # | Task | Direct Link |
|---|------|-------------|
| 7 | Subscribe to BHT Newsletter, Out-Of-Pocket, BHB, Rock Health Weekly | [BHT signup] · [Out-of-Pocket] · [BHB] · [Rock Health] |
| 8 | Follow Solome Tibebu, Nikhil Krishnan, April Koh, Robert Krayn on LinkedIn | LinkedIn search |
| 9 | Join Mental Health Startup Community Slack | [slack link] |
| 10 | Identify 3–5 Tuck alumni working in fitness/wellness | MyTuck network — manual |

### Inbox — Items to Route
[If inbox items exist:]

| # | File | Content | Proposed destination |
|---|------|---------|---------------------|
| I1 | ripple-foods.md | "check out Ripple Foods" | Company research — add to Bucket A or run `/research-company` |
| I2 | article-link.md | https://... | Article read — add to Bucket A |

> Inbox items can be added to the execute list, or routed manually. Delete each item from `inbox/` after routing.

[If inbox is empty:]
> Inbox clear — nothing to route.

---
**Type `run` to execute all automated items (1–6), `run 1 2 5` to run specific items, or `skip 3` to exclude specific items.**
**To include inbox items in execution:** `run I1 I2` or `inbox` to route all inbox items.**
```

Wait for the user's response before proceeding to Step 4.

### Step 4: Parse User Response

Parse the user's reply:

- **`run`** or **`yes`** or **`go`** → execute all items in the "Will Execute Automatically" table
- **`run [numbers]`** (e.g., `run 1 3 5`) → execute only those numbered items
- **`skip [numbers]`** (e.g., `skip 3 4`) → execute all automated items *except* those numbered
- **`inbox`** or **`run I1 I2`** → route inbox items (adds executable inbox items to execute list; routes non-executable items to appropriate data files and deletes the inbox files)
- **`cancel`** or **`no`** → abort; display "Cancelled — no changes made."
- **Anything else** → treat as `run` (assume confirmation)

Build the final **execute list** from the parsed response.

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

3. Save the completed dossier to: data/company-research/[slug].md
   File header must include: `Last updated: [today's date in YYYY-MM-DD format]`
   Slug format: lowercase company name with spaces replaced by hyphens (e.g., "Spring Health" → spring-health)

4. Return only the Executive Summary (3-5 sentences) — the full dossier is in the file.

Candidate context:
[paste profile.md content here — name, background, target roles, location]
```

#### Article Reads — `model: "haiku"`, `max_turns: 4`

One Task agent per article. Prompt:

```
Fetch and summarize this article for a job seeker in [industry from task notes]:

URL: [URL from notes]
Article context: [task text — e.g., "Alex Mullin's white paper on digital health and OUD treatment"]

Return:
- Title, author, publication, date (if findable)
- 4-5 key insights (bullets — specific facts and arguments, not vague summaries)
- Why it matters for this candidate's job search (1 sentence — be specific)

No preamble. Structured output only.
If the URL is behind a paywall or returns an error, report: "Paywall / access blocked — [URL]"
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
  - Company research: "Dossier saved to data/company-research/[slug].md"
  - Article read: "[first key insight, truncated to ~80 chars]"
  - Resource browse: "N results found matching filter"

**For each failed/blocked item (404, paywall, access denied):**
- Status remains `Pending`
- Notes → append ` | Checked [today's date] — [reason: 404 / paywall / access denied]`

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

**Talkiatry** — dossier saved: `data/company-research/talkiatry.md`
> $210M Series D (Feb 2026), 600+ psychiatrists on platform, telehealth-first SMI focus.
> Strategy & Ops team actively hiring. Strong fit for S&O background.
> Run `/research-company "Talkiatry"` for deeper refresh, or `/prep-interview "Talkiatry"` when applying.

**Headway** — dossier saved: `data/company-research/headway.md`
> ...

---

### Articles Read

**Alex Mullin — Digital Health and OUD Treatment** (MobiHealthNews)
- Digital health interventions reduce OUD treatment dropout by 40% in pilot studies
- Insurance parity laws are expanding reimbursement for digital therapeutics
- [3 more key insights]
- Why it matters: Provides credibility talking points for the Amae Health coffee chat with Alex.

---

### Resource Browse

**BizBuySell — SF Yoga Studios for Sale**
- Yoga Flow SF (3 locations) — asking $850K — [link]
- [2 more listings]

---

### Failed / Blocked
- Xponential Fitness careers page — returned 403 (blocked) — to-do remains Pending

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

## Model Selection Rationale

| Task type | Model | Why |
|-----------|-------|-----|
| Careers check | Haiku | Simple web fetch + keyword scan — no synthesis needed |
| Article read | Haiku | Fetch + summarize — straightforward extraction |
| Resource browse | Haiku | Fetch + filter — no analysis |
| Company research (sub-agents) | Haiku | Each agent fetches one focused topic — no cross-synthesis |
| Company research (orchestrator) | Sonnet | Must synthesize 5 agent outputs into a coherent dossier |
| Act skill itself (top-level) | Inherits from session | Categorization + report writing is the complex orchestration step |
