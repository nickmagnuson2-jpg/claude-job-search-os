---
name: research-company
description: Deep company research with parallel agents — overview, funding, people, news, competitive landscape — with conversation starters and similar companies to target
argument-hint: <company-name> [url] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Write(data/company-research/*), Edit(data/company-research/*), Edit(data/job-todos.md), Write(data/job-todos.md), Read(data/job-todos.md), Task, WebFetch, WebSearch
---

# Research Company — Deep Dossier with Parallel Agents

Research a company in depth by launching five parallel agents, each investigating a different dimension. Produces a comprehensive dossier with conversation starters tailored to the context (coffee chat, interview, networking), plus a curated list of similar companies worth targeting in your job search.

Use this before meetings, coffee chats, interviews, or when evaluating opportunities at a specific company.

## Arguments

- `$ARGUMENTS` (required): At minimum a company name.
  - **Company name** (required): The company to research (quoted if multi-word)
  - **URL** (optional): The company's website — detected by `http` prefix or domain suffix (`.com`, `.io`, `.org`, `.health`, etc.)
  - **Context** (optional): Why you're researching — informs the tone of conversation starters

Examples:
- `/research-company "Amae Health" https://amaehealth.com "coffee chat with Alex Mullin"`
- `/research-company Stripe https://stripe.com "interview for PM role"`
- `/research-company "McKinsey" "networking event next week"`
- `/research-company "Amae Health"` — general research, no specific context

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS` into three components:

1. **Company name** (required): The first quoted string or unquoted word(s) before any URL or context.
2. **URL** (optional): Any token containing `http` or matching a domain pattern (e.g., `amaehealth.com`). If a bare domain is provided, prepend `https://`.
3. **Context string** (optional): Remaining text after company name and URL.

If no arguments provided, display usage:
```
Usage: /research-company <company-name> [url] [context]

Examples:
  /research-company "Amae Health" https://amaehealth.com "coffee chat with Alex Mullin"
  /research-company Stripe "interview for Senior PM role"
  /research-company "Company Name"
```

**Infer context type** from the context string:
- Contains "coffee" or "chat" or "catch up" or "lunch" → `coffee-chat`
- Contains "interview" or "screening" or "phone screen" → `interview`
- Contains "networking" or "event" or "conference" or "meetup" → `networking`
- Otherwise → `general`

**Extract contact name** from the context string if present (e.g., "coffee chat with Alex Mullin" → contact name is "Alex Mullin"). Look for patterns like "with [Name]", "meeting [Name]", "[Name] at [Company]".

**Generate a slug** from the company name for the output filename: lowercase, spaces/special chars replaced with hyphens (e.g., "Amae Health" → `amae-health`).

### Step 2: Load Candidate Context

Read the following files in parallel (skip any that don't exist — proceed with what's available):

1. `data/profile.md` — background, location, interests
2. `data/professional-identity.md` — strengths, values, career direction
3. `data/education.md` — schools, degrees (for alumni matching)
4. `data/networking.md` — existing contacts at this company
5. `data/job-pipeline.md` — active pipeline entries at this company
6. `data/project-index.md` — experience domains for overlap detection

Also check if `data/company-research/<slug>.md` already exists (for refresh logic).

Also check `data/industry-research/` for any existing industry dossier that covers this company's sector. If found, note the file path — agents can reference it instead of reproducing industry-level analysis.

**Build a candidate context summary** (2-3 paragraphs, shared with all agents) covering:
- Candidate's name, current situation, and background (from profile)
- Key skills and career direction (from professional-identity)
- Any existing connections to this company (from networking, pipeline)
- Education details relevant for alumni matching
- Experience domains that might overlap with the company's work

**If no `data/profile.md` exists**, proceed with a generic candidate context and suggest the user run `/import-cv` first.

### Step 2b: Refresh Logic

If `data/company-research/<slug>.md` already exists:
- Check the "Last updated" date in the file.
- **< 14 days old**: Ask the user: "Existing research from [date] found. Refresh or view existing?"
  - If "view", display the existing file and stop.
  - If "refresh", continue to Step 3.
- **>= 14 days old**: Inform the user the research is stale and auto-refresh: "Research from [date] is stale — refreshing now."
- When refreshing, preserve any content under a `## Manual Notes` section if the user added one.

### Step 3: Launch 5 Parallel Research Agents

Use the Task tool to launch **five parallel subagents** (`subagent_type: "general-purpose"`, `max_turns: 12`).

**Each agent receives:**
- The candidate context summary from Step 2
- The company name and URL (if provided)
- The contact name (if extracted from context)
- Their specific research focus and instructions (below)
- Instruction to cite source URLs for every factual claim

**Important:** Tell each agent to use WebSearch and WebFetch to gather information. Each agent should perform 3-5 targeted searches and follow the most promising results.

---

#### Agent 1: Company Overview

**Focus:** Mission, products, business model, market position, differentiation.

**Research instructions:**
```
Research [Company Name] ([URL if provided]) comprehensively. Search for:
1. "[Company]" — main website and Wikipedia/Crunchbase profiles
2. "[Company] products services" — what they actually sell/offer
3. "[Company] business model" OR "[Company] how it works"
4. "[Company] mission vision values"
5. "[Company] vs" OR "[Company] competitors" — market positioning

Compile your findings into these sections (cite URLs for every claim):

## Mission & Purpose
What the company exists to do. Their stated mission. The problem they solve.

## Products & Services
What they actually offer. Key product lines. How customers use them.

## Business Model
How they make money. Pricing model. Customer segments. B2B vs. B2C.

## Market Position & Differentiation
What makes them different from competitors. Their unique angle. Market size if known.

## Key Facts
- Founded: [year]
- HQ: [location]
- Industry: [sector]
- Website: [url]

If information is thin, note what couldn't be found and suggest the candidate ask about it directly.
```

---

#### Agent 2: Funding & Financial Trajectory

**Focus:** Funding history, investors, revenue signals, growth trajectory, risk indicators.

**Research instructions:**
```
Research the funding and financial trajectory of [Company Name]. Search for:
1. "[Company] funding" OR "[Company] crunchbase"
2. "[Company] investors" OR "[Company] series"
3. "[Company] revenue" OR "[Company] growth"
4. "[Company] hiring" OR "[Company] jobs" — as a growth proxy
5. "[Company] layoffs" OR "[Company] challenges" — risk signals

Compile your findings into these sections (cite URLs for every claim):

## Funding History
All known funding rounds with dates, amounts, and lead investors. Total raised.

## Investors & Board
Notable investors, board members, strategic backers.

## Growth Signals
Hiring velocity, office expansions, new markets, customer growth. Job postings as proxy.

## Revenue & Business Health
Any public revenue figures, ARR, customer count. If private, note what's inferrable.

## Risk Indicators
Any red flags: layoffs, pivots, legal issues, negative press, leadership departures.

## Stage Assessment
Where this company sits: pre-seed, seed, Series A/B/C, growth, mature, public.
What the likely runway and trajectory look like.

If the company is very early-stage or private with limited public data, say so explicitly and note what to ask about in conversation.
```

---

#### Agent 3: People & Culture

**Focus:** Leadership team, culture signals, the specific contact (if any), alumni connections.

**Research instructions:**
```
Research the people and culture at [Company Name]. Search for:
1. "[Company] leadership team" OR "[Company] founders"
2. "[Company] glassdoor" OR "[Company] reviews" OR "[Company] culture"
3. "[Company] LinkedIn" — team size and composition
[IF CONTACT NAME]: 4. "[Contact Name] [Company]" — their background, publications, talks
[IF CONTACT NAME]: 5. "[Contact Name] LinkedIn" — their career path
[IF SCHOOL FROM CANDIDATE CONTEXT]: 6. "[Company] [School Name]" — alumni connections

Compile your findings into these sections (cite URLs for every claim):

## Leadership Team
Key executives and founders. Their backgrounds. Notable hires.

## Team Size & Composition
Approximate headcount. Key departments. Engineering vs. business ratio if inferrable.

[IF CONTACT NAME PROVIDED]:
## Contact Research: [Contact Name]
- Current role and responsibilities
- Career path and background
- Publications, talks, or media appearances
- Shared connections or interests with the candidate
- Conversation hooks specific to this person

## Culture & Work Environment
Glassdoor/review signals. Company values in practice. Work style (remote, hybrid, in-office).
Notable culture elements (mission-driven, fast-paced, etc.).

## Alumni & Network Connections
Any connections between the candidate's school/network and the company.
Notable alumni or shared-background team members.

If Glassdoor or review data is unavailable, note it and suggest the candidate ask about culture directly.
```

---

#### Agent 4: News & Strategic Context

**Focus:** Recent developments, competitive positioning, company-specific industry tailwinds/headwinds, milestones.

**Research instructions:**
```
Research recent news and strategic context for [Company Name]. Search for:
1. "[Company] news 2025 2026" — recent developments
2. "[Company] announcement" OR "[Company] launch" — milestones
3. "[Company] competitors" OR "[Company] vs" — competitive positioning
4. "[Company] partnership" OR "[Company] expansion" — strategic moves
5. "[Company] strategy" OR "[Company] challenges" — strategic direction

Compile your findings into these sections (cite URLs for every claim):

## Recent News & Developments
Last 6-12 months of news. Product launches, partnerships, milestones.
Ordered by recency (newest first).

## Competitive Positioning
Key competitors and how [Company] differs from each.
Focus on THIS company's competitive advantages and vulnerabilities — not a comprehensive industry landscape.
(For full industry mapping, the candidate should use `/research-industry`.)

## Industry Tailwinds & Headwinds Affecting This Company
NOT a standalone industry overview — focus only on the 2-3 industry dynamics that most directly affect THIS company's trajectory, hiring, and prospects.
For each: what the trend is and how it specifically helps or hurts [Company].

## Milestones & Timeline
Key company milestones in chronological order. Founding, launches, pivots, funding events.

If news is sparse, note that the company has a low media profile and suggest asking what recent milestones they're proud of.
```

---

#### Agent 5: Similar Companies to Target (Anchored on Competitive Proximity)

**Focus:** Find companies similar to THIS company that the candidate could also target. The shortlist is anchored on competitive proximity — companies solving similar problems, serving similar customers, or operating in the same niche. For a broader industry-wide target list anchored on candidate fit across all segments, the candidate should use `/research-industry`.

**Research instructions:**
```
Research the competitive landscape around [Company Name] to find similar companies the candidate could also target in their job search. Search for:
1. "[Company] competitors" OR "[Company] alternatives" — direct competitors
2. "[Company] vs" — head-to-head comparisons that name specific rivals
3. "[Industry/sector] startups" OR "[Industry/sector] companies" — broader ecosystem players
4. "[Industry/sector] companies [location from candidate context]" — companies in the candidate's geography
5. "companies like [Company]" OR "[Company] similar companies" — peer companies
6. "[Industry/sector] market map" OR "[Industry/sector] landscape" — industry maps that list many players

For each competitor or similar company found, research:
- What they do and how they differ from [Company Name]
- Approximate size and stage (startup vs. growth vs. enterprise)
- HQ location and whether they have presence near the candidate
- Whether they appear to be hiring (check for careers pages, recent job postings)
- Why the candidate's background might be relevant to them

Compile your findings into these sections (cite URLs for every claim):

## Direct Competitors
Companies solving the same problem or serving the same customers as [Company Name].
For each: name, one-line description, how they differ from [Company], size/stage, HQ, careers page URL if found.

## Adjacent Players
Companies in the same industry or ecosystem but solving different problems or serving different segments. These are companies where similar skills and domain knowledge transfer.
For each: name, one-line description, relevance to the candidate, size/stage, HQ.

## Emerging Players
Newer or smaller companies in this space that might be earlier-stage but growing. Good for candidates who want high impact or early-stage experience.
For each: name, one-line description, why they're interesting, size/stage, HQ.

## Companies to Target — Ranked Shortlist
Rank the top 8-10 companies (from all categories above) that the candidate should consider targeting, based on:
1. Relevance to the candidate's background and skills (from candidate context)
2. Hiring signals (active job postings, growth indicators)
3. Geographic fit (near candidate or remote-friendly)
4. Stage/culture fit (based on candidate context)

For each entry in the shortlist:
- **Company name** + one-line description
- **Why target**: How the candidate's background maps to this company
- **Stage & size**: Funding stage, approximate headcount
- **Location**: HQ + remote policy if known
- **Hiring signal**: Active roles, growth indicators, or "unknown"
- **Website / careers page**: URL

If the industry is niche and competitors are hard to find, broaden the search to adjacent industries or note the gap.
```

### Step 4: Synthesize Results

After all five agents return, synthesize their findings:

1. **Deduplicate** — if multiple agents report the same fact, keep the most detailed version with the best source. Agent 4 (News) and Agent 5 (Competitive Landscape) may both mention competitors — merge into a single view, keeping Agent 5's deeper analysis and Agent 4's news angle.
2. **Resolve conflicts** — if agents report contradictory information (e.g., different founding years, conflicting competitor lists), note both with their sources and flag the discrepancy.
3. **Cross-link** — connect findings across agents (e.g., a product mentioned by Agent 1 that's in the news from Agent 4, a leader from Agent 3 who led a funding round from Agent 2, or a competitor from Agent 5 that recently appeared in Agent 4's news).
4. **Enrich the shortlist** — use findings from Agents 1-4 to enhance Agent 5's similar-companies shortlist. For example, if Agent 2 found a specific investor, note which shortlisted companies share that investor. If Agent 3 found alumni connections, flag shortlisted companies with similar alumni overlap potential.

### Step 5: Cross-Reference with Candidate Data

Using the data loaded in Step 2:

1. **Networking contacts** — list any contacts at this company from `data/networking.md` with their role and last interaction date.
2. **Pipeline status** — if the company appears in `data/job-pipeline.md`, note the stage and any active applications.
3. **Experience overlap** — scan `data/project-index.md` for 2-3 projects with the strongest relevance to this company's domain, technology, or industry.
4. **Education connections** — note any alumni overlap between the candidate's schools and the company's team (from Agent 3 findings).

### Step 6: Generate Conversation Starters

Generate **8-12 conversation starters** tailored to the context type. Each starter should be:
- Informed by actual research findings (reference specific facts)
- Connected to the candidate's background where possible
- Natural and conversational (not interrogation-style)

**By context type:**

- **`coffee-chat`**: Curiosity-driven, learning-oriented. Focus on understanding the person's journey, the company's mission, what excites them. Avoid anything that feels like a job interview.
  - Examples: "I read about [specific initiative] — what's the story behind that?", "How did you end up at [Company] from [their previous role]?"

- **`interview`**: Strategic questions demonstrating preparation and fit. Show you've done homework. Probe for information that helps you assess the role.
  - Examples: "I noticed [Company] recently [milestone] — how has that changed the team's priorities?", "The role mentions [X] — what does success look like in the first 90 days?"

- **`networking`**: Relationship-building, mutual value exchange. Focus on shared interests, how you might help each other.
  - Examples: "I worked on [related project] at [your company] — I'd love to hear how you're approaching [similar challenge]."

- **`general`**: Balanced mix of the above.

### Step 7: Generate Follow-Up To-Dos

Based on the research findings, generate actionable follow-up items and write them to `data/job-todos.md`.

Read `data/job-todos.md` first. Then add relevant to-dos such as:
- "Read [article/talk] by [contact or company leader]" — if the research surfaced specific content worth reviewing
- "Review [your project] for overlap discussion with [Company]" — if experience overlap was found
- "Connect with [person] on LinkedIn" — if alumni or other connections were identified
- "Prepare [topic] talking points for [Company] meeting" — if specific preparation areas emerged
- "Follow up on [Company] research — check [news source] for updates" — if the research was thin in some areas
- "Research [similar company] — flagged as strong fit from [Company] competitive landscape" — for the top 2-3 companies from Agent 5's shortlist that have strong hiring signals
- "Check [similar company] careers page for [relevant role type]" — if Agent 5 found active hiring at a shortlisted company

Use the same format as existing entries in `data/job-todos.md`:
- **Priority**: `Med` (default) or `High` if time-sensitive
- **Due**: 7 days from today, or earlier if meeting date is known from context
- **Status**: `Pending`
- **Notes**: `From /research-company on [date]`

If `data/job-todos.md` doesn't exist, create it with the standard header format (see `data/job-todos.md` structure from the `/todo` skill).

### Step 8: Write Output File

Create the output directory if needed, then write to `data/company-research/<slug>.md`:

```markdown
# [Company Name] — Research Dossier

> Generated by `/research-company` on [date]. Context: [context type].
> Last updated: [date]

## At a Glance

| Field | Detail |
|-------|--------|
| Company | [name] |
| Website | [url] |
| Industry | [sector] |
| Founded | [year] |
| HQ | [location] |
| Size | [headcount estimate] |
| Stage | [funding stage] |
| Total Funding | [amount] |
| Last Round | [round, date, amount] |

## Connection to You

[Networking contacts at this company, pipeline status, experience overlap, education connections. If none, state "No existing connections found — this is a cold approach."]

---

## Mission & Purpose

[From Agent 1]

## Products & Services

[From Agent 1]

## Business Model

[From Agent 1, supplemented by Agent 2]

## Funding & Financial Trajectory

[From Agent 2]

## Leadership & Team

[From Agent 3]

### Contact Research: [Name]

[From Agent 3 — only include if a contact name was extracted from context. Include their background, career path, shared connections, and conversation hooks specific to this person.]

## Culture & Work Environment

[From Agent 3]

## Recent News & Developments

[From Agent 4]

## Competitive Positioning

[From Agent 4 — how this company differs from key competitors. NOT a full industry map — for that, see `/research-industry`.]

## Industry Tailwinds & Headwinds Affecting This Company

[From Agent 4 — only the 2-3 dynamics most relevant to this company's trajectory]

---

## Similar Companies to Target

[From Agent 5 — the core job-search output of this section]

### Ranked Shortlist

[Top 8-10 companies from Agent 5's ranked shortlist, enriched with cross-references from other agents. For each:]

#### 1. [Company Name]
> [One-line description]

- **Why target:** [How candidate's background maps to this company]
- **Stage & size:** [Funding stage, approximate headcount]
- **Location:** [HQ + remote policy if known]
- **Hiring signal:** [Active roles, growth indicators, or "unknown"]
- **Website:** [URL]
- **Careers:** [Careers page URL if found]

#### 2. ...

### Direct Competitors
[From Agent 5 — companies solving the same problem]

### Adjacent Players
[From Agent 5 — same industry, different problem, transferable skills]

### Emerging Players
[From Agent 5 — earlier-stage companies worth watching]

---

## Conversation Starters — [Context Type]

[8-12 numbered starters, each with a brief note on what research finding it references]

1. **[Starter question]**
   _Based on: [research finding]_

2. ...

## Preparation Notes

### Things to Definitely Mention
[2-4 items from the candidate's background that directly connect to this company's work]

### Things to Avoid
[Topics to steer away from — sensitive company issues, competitor comparisons that could backfire, assumptions to avoid]

### Your Relevant Experience to Reference
[2-3 specific projects/roles from the candidate's background with brief explanation of relevance]

---

## Sources

[All URLs cited by agents, organized by section]

## Raw Agent Outputs

### Agent 1: Company Overview
[Full unedited output]

### Agent 2: Funding & Financial Trajectory
[Full unedited output]

### Agent 3: People & Culture
[Full unedited output]

### Agent 4: News & Strategic Context
[Full unedited output]

### Agent 5: Competitive Landscape & Similar Companies
[Full unedited output]
```

**Important:** The Raw Agent Outputs appendix must contain the **complete, unedited output** from each agent. Do not summarize, truncate, or reformat.

### Step 9: Display Summary

After writing the file, display a concise summary to the user:

```markdown
## Company Research Complete — [Company Name]

**Saved to:** `data/company-research/<slug>.md`
**Context:** [context type] [with contact name if applicable]

### Key Findings
- [3-5 most important/interesting facts about the company]

### Your Connections
- [Networking contacts, pipeline status, or "No existing connections"]

### Top 3 Conversation Starters
1. [Best starter for this context]
2. [Second best]
3. [Third]

### Similar Companies to Target
[Top 3-5 from the ranked shortlist, one line each: name — why it's a fit — hiring signal]

### To-Dos Created
- [List of to-dos added to job-todos.md]

---
Full dossier: `data/company-research/<slug>.md`
```

## Edge Cases

- **No arguments provided**: Display usage message with examples (Step 1).
- **Company not found / thin results**: Complete the dossier with whatever was found. Add a "Research Gaps" section noting what couldn't be found. Suggest the user provide a URL or more specific company name.
- **Ambiguous company name** (e.g., "Mercury" could be bank, insurance, car): If search results show multiple companies, ask the user to clarify — suggest providing a URL or industry.
- **Contact name extracted but not in networking.md**: Note in the dossier that this contact isn't tracked yet. Suggest: "Consider running `/networking add \"[Name]\" [Company] [Role]`".
- **No profile data**: Proceed with generic candidate context. Note in output: "Candidate context was limited — run `/import-cv` for better personalization."
- **Agent returns thin results**: Proceed with remaining agents' data. Note which research dimension was thin in the output. Do NOT fail the entire operation because one agent struggled.
- **Agent failure / timeout**: If an agent fails to return, proceed with the remaining agents. Note the gap in the output and what section is affected.
- **Niche industry with few competitors**: If Agent 5 finds very few direct competitors, it should broaden to adjacent industries and note the niche nature. The shortlist may be shorter than 8 — that's fine, quality over quantity.
- **Relationship to /research-industry**: This skill goes deep on ONE company; `/research-industry` maps the entire landscape. They are complementary: `/research-industry` first to identify targets and understand the terrain, then `/research-company` to deep-dive on specific picks. If `data/industry-research/` already has a dossier for this company's industry, reference it in the "Industry Tailwinds & Headwinds" section and link to it rather than reproducing the landscape analysis.
