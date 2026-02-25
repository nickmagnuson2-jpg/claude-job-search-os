---
name: research-industry
description: Deep industry research with parallel agents — market overview, key players, talent demand, trends & regulation, entry strategy — with target companies and positioning advice
argument-hint: <industry-name> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Write(data/industry-research/*), Edit(data/industry-research/*), Edit(data/job-todos.md), Write(data/job-todos.md), Read(data/job-todos.md), Task, WebFetch, WebSearch
---

# Research Industry — Deep Landscape Analysis with Parallel Agents

Research an industry in depth by launching five parallel agents, each investigating a different dimension. Produces a comprehensive landscape analysis with target companies ranked by candidate fit, positioning advice, and actionable next steps.

Use this when exploring a new industry, preparing for interviews in an unfamiliar sector, evaluating a career pivot, or building a systematic target list for your job search.

## Arguments

- `$ARGUMENTS` (required): At minimum an industry name.
  - **Industry name** (required): The industry or sector to research (quoted if multi-word)
  - **Context** (optional): Why you're researching — informs the positioning advice and target company ranking

Examples:
- `/research-industry "behavioral health tech" "exploring pivot from consulting"`
- `/research-industry "climate tech" "interview at Watershed, want to understand the landscape"`
- `/research-industry "defense tech" "networking with people in this space"`
- `/research-industry fintech` — general landscape research, no specific context

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS` into two components:

1. **Industry name** (required): The first quoted string or unquoted word(s) before any context string.
2. **Context string** (optional): Remaining text after the industry name (usually in quotes).

If no arguments provided, display usage:
```
Usage: /research-industry <industry-name> [context]

Examples:
  /research-industry "behavioral health tech" "exploring pivot from consulting"
  /research-industry "climate tech" "interview at Watershed, want to understand the landscape"
  /research-industry fintech
```

**Infer context type** from the context string:
- Contains "interview" or "screening" or "role at" → `interview-prep`
- Contains "pivot" or "transition" or "switch" or "career change" → `career-pivot`
- Contains "networking" or "event" or "conference" → `networking`
- Contains "explore" or "learn" or "understand" → `exploration`
- Otherwise → `general`

**Extract company name** from the context string if present (e.g., "interview at Watershed" → anchor company is "Watershed"). Look for patterns like "at [Company]", "role at [Company]", "[Company] interview".

**Generate a slug** from the industry name for the output filename: lowercase, spaces/special chars replaced with hyphens (e.g., "behavioral health tech" → `behavioral-health-tech`).

### Step 2: Load Candidate Context

Read the following files in parallel (skip any that don't exist — proceed with what's available):

1. `data/profile.md` — background, location, interests
2. `data/professional-identity.md` — strengths, values, career direction
3. `data/education.md` — schools, degrees (for alumni/network leverage)
4. `data/skills.md` — complete skill inventory
5. `data/project-index.md` — experience domains for overlap detection
6. `data/job-pipeline.md` — active pipeline entries in this industry
7. `data/networking.md` — existing contacts in this industry

Also check if `data/industry-research/<slug>.md` already exists (for refresh logic).

**Build a candidate context summary** (2-3 paragraphs, shared with all agents) covering:
- Candidate's name, current situation, and background (from profile)
- Key skills, experience domains, and career direction (from professional-identity, skills, project-index)
- Any existing connections to this industry (from networking, pipeline)
- Education details relevant for networking leverage
- Why the candidate is researching this industry (from context string)

**If no `data/profile.md` exists**, proceed with a generic candidate context and suggest the user run `/import-cv` first.

### Step 2b: Refresh Logic

If `data/industry-research/<slug>.md` already exists:
- Check the "Last updated" date in the file.
- **< 30 days old**: Ask the user: "Existing research from [date] found. Refresh or view existing?"
  - If "view", display the existing file and stop.
  - If "refresh", continue to Step 3.
- **>= 30 days old**: Inform the user the research is stale and auto-refresh: "Research from [date] is stale — refreshing now."
- When refreshing, preserve any content under a `## Manual Notes` section if the user added one.
- **For any refresh:** Read and retain the existing file's **Executive Summary** and **At a Glance** sections before overwriting. These will be compared against new findings in Step 4 to generate a "What Changed" delta.

### Step 3: Launch 5 Parallel Research Agents

Use the Task tool to launch **five parallel subagents** (`subagent_type: "general-purpose"`, `model: "haiku"`, `max_turns: 12`).

**Each agent receives:**
- The candidate context summary from Step 2
- The industry name
- The anchor company (if extracted from context)
- Their specific research focus and instructions (below)
- The shared evidence quality standards (below)

**Important:** Tell each agent to use WebSearch and WebFetch to gather information. Each agent should perform 3-5 targeted searches and follow the most promising results.

**Shared evidence quality standards (include verbatim in every agent prompt):**

> **Source quality — classify every source you cite:**
> - **Tier A** (primary): government/regulator data, industry body reports, company filings, official press releases, peer-reviewed research
> - **Tier B** (reputable secondary): major business publications (WSJ, Bloomberg, TechCrunch, Forbes), named analysts (Gartner, McKinsey, CB Insights), established trade press
> - **Tier C** (aggregator/crowd): Glassdoor, Reddit, Quora, Owler, anonymous forums, generic listicles — always flag with `[Tier C — verify independently]`
>
> **Confidence tags — for high-impact claims** (market size, growth rates, funding amounts, headcount, compensation figures, regulatory changes), add: `[Confidence: High | Medium | Low, as of YYYY-MM]`
> - High = primary source or 2+ corroborating Tier A/B sources
> - Medium = single Tier B source, no contradiction found
> - Low = Tier C only, inference, or data older than 18 months
>
> **Freshness:** Prioritize sources from the last 12 months for news, trend, and growth claims. If using older sources, note why they remain relevant (e.g., foundational industry structure data that hasn't changed).
>
> **Contradictions:** If two credible sources disagree on a fact, report both values with their sources and mark as `[Needs verification]`. Do not silently pick one or average them.

---

#### Agent 1: Market Overview & Structure

**Focus:** Market size, growth trajectory, segmentation, value chain, dominant business models.

**Research instructions:**
```
Research the [Industry Name] industry comprehensively. Search for:
1. "[Industry] market size" OR "[Industry] TAM" — market sizing data
2. "[Industry] market report 2025 2026" — recent analyst reports
3. "[Industry] segments" OR "[Industry] categories" — how the market is structured
4. "[Industry] business models" OR "[Industry] revenue models" — how companies make money
5. "[Industry] value chain" OR "[Industry] ecosystem" — how players interconnect
6. "[Industry] cyclical" OR "[Industry] economic cycle" — sensitivity to economic conditions

Compile your findings into these sections. Cite URLs for every claim, classify sources by tier (A/B/C), and tag high-impact claims with [Confidence: H/M/L, as of YYYY-MM]:

## Market Size & Growth
Total addressable market (TAM). Current size and projected growth rate (CAGR).
Key growth drivers and tailwinds. What's fueling expansion.
SAM (Serviceable Available Market) if relevant — the realistic addressable portion.

## Market Segmentation
How the industry breaks down into segments/sub-sectors.
Which segments are largest, fastest-growing, most profitable.
Visual-friendly breakdown (e.g., "B2B SaaS tools (40%) | Services (30%) | Hardware (20%) | Other (10%)").

## Value Chain & Profit Pools
How value flows through the industry. Who are the buyers, intermediaries, and suppliers.
Where do companies plug in along the value chain.
**Critically: where does profit actually concentrate?** Some segments capture disproportionate margins.
Identify the high-margin vs. low-margin positions — this directly affects compensation and job quality.
(Inspired by Bain's Profit Pool Analysis: the highest revenue segment is not always the most profitable.)

## Dominant Business Models
How companies in this industry typically make money.
SaaS, marketplace, services, licensing, hardware, etc. Which models are winning.
Unit economics overview: typical margins, customer acquisition dynamics, retention.

## Industry Maturity & Cycle Assessment
Where is this industry on the maturity curve: nascent, emerging, growth, mature, declining.
What signals support this assessment.

**Cyclical vs. secular assessment:** Is the current trajectory driven by:
- **Secular trends** (lasting 10-20 years, structural shifts) — e.g., aging population driving healthcare demand
- **Cyclical dynamics** (18 months - 7 years, economic sensitivity) — e.g., construction tied to interest rates
- **Both** — note which forces dominate

This distinction is critical for career timing. Joining an industry in a cyclical peak feels like growth but may reverse. Joining during a secular trend's early stages is the ideal timing.

## Market Concentration
Is this a fragmented market (many small players) or consolidated (oligopoly)?
What is the approximate market share of the top 5 players?
Concentration directly affects employer leverage: highly concentrated = fewer employers with more power over comp.
Fragmented = more options but potentially less stability.

If quantitative data is thin, note the best available estimates and which sources are most credible.
```

---

#### Agent 2: Key Players & Ecosystem Map

**Focus:** Dominant companies, emerging disruptors, investors, categorized by segment and tier.

**Research instructions:**
```
Research the key players in the [Industry Name] industry. Search for:
1. "[Industry] companies" OR "[Industry] startups" — broad player landscape
2. "[Industry] market leaders" OR "top [Industry] companies" — dominant players
3. "[Industry] startups funding 2025 2026" — recently funded companies
4. "[Industry] unicorn" OR "[Industry] fastest growing" — breakout companies
5. "[Industry] market map" OR "[Industry] landscape map" — visual ecosystem maps
6. "[Industry] investors" OR "[Industry] VCs" — who's funding the space

Compile your findings into these sections. Cite URLs for every claim, classify sources by tier (A/B/C), and tag high-impact claims with [Confidence: H/M/L, as of YYYY-MM]:

## Market Leaders
The 5-8 dominant companies defining the industry. For each:
- Name, one-line description, what makes them dominant
- Size (headcount, revenue if known), HQ location
- Founded, funding stage, key investors
- Website

## High-Growth / Breakout Companies
5-8 companies on steep growth trajectories — recently raised large rounds, expanding rapidly, getting buzz. For each:
- Name, one-line description, what's driving their growth
- Recent funding (round, amount, date, lead investor)
- HQ location, headcount estimate
- Website

## Emerging / Early-Stage Players
5-8 newer or smaller companies worth watching. For each:
- Name, one-line description, what's novel about their approach
- Stage and funding if known
- HQ location
- Website

## Key Investors & Backers
Which VCs, PE firms, and strategic investors are most active in this space.
Notable investment themes or thesis areas.

## Industry Map Summary
A text-based overview of how these players cluster — by segment, by tier, by geography.
Note any "white space" segments that are underserved or have few players.

If the industry is very broad, focus on the segments most relevant to the candidate's background (from candidate context).
```

---

#### Agent 3: Talent Demand & Career Paths

**Focus:** In-demand roles, skills gaps, compensation ranges, career trajectories, where the jobs are.

**Research instructions:**
```
Research the talent landscape in the [Industry Name] industry. Search for:
1. "[Industry] jobs" OR "[Industry] careers" — general job market
2. "[Industry] hiring trends 2025 2026" — employment dynamics
3. "[Industry] salary" OR "[Industry] compensation" — pay ranges
4. "[Industry] skills" OR "[Industry] skills gap" — what's in demand
5. "[Industry] career path" OR "how to get into [Industry]" — entry and progression
6. "[Industry] roles" OR "[Industry] job titles" — what roles exist
7. "[Industry] KPIs" OR "[Industry] metrics" — what numbers the industry watches
8. "[Industry] terminology" OR "[Industry] glossary" — industry-specific language

Given the candidate context below, focus particularly on roles where the candidate's background would be relevant.

Compile your findings into these sections. Cite URLs for every claim, classify sources by tier (A/B/C), and tag high-impact claims with [Confidence: H/M/L, as of YYYY-MM]:

## In-Demand Roles
The most-hired roles in this industry right now. Group by function:
- **Operations & Strategy**: [roles, seniority levels]
- **Product & Engineering**: [roles]
- **Business Development & Sales**: [roles]
- **Finance & Analytics**: [roles]
- **Other**: [any industry-specific roles]
For each role type: typical title, what they do, typical experience level.

## Skills in Demand
Technical and non-technical skills most valued in this industry.
- **Must-have skills**: Non-negotiable for most roles
- **Differentiating skills**: Set candidates apart
- **Emerging skills**: Growing in importance
Flag which skills the candidate already has vs. would need to develop (based on candidate context).

## Key Metrics & KPIs
The 5-8 metrics that everyone in this industry watches. For each:
- **Metric name**: What it measures, why it matters
- **Benchmark**: What "good" looks like
- **Your relevance**: How this connects to metrics the candidate has used before (based on candidate context)

Knowing industry-specific KPIs lets you frame your past achievements in the language this industry uses.
(E.g., SaaS: ARR, churn, NRR, CAC:LTV | Healthcare: readmission rates, cost per episode | Retail: same-store sales, inventory turns)

## Industry Jargon Glossary
15-25 essential terms, acronyms, and phrases specific to this industry that the candidate must know.
For each: the term, a plain-English definition, and an example of how it's used in conversation.

This is a make-or-break credibility signal. Using the right terms naturally signals belonging.
Misusing them or avoiding them marks you as an outsider immediately.

## Compensation Landscape
Salary ranges for key roles at different levels and company stages.
Equity/bonus norms. Geographic pay differences. Startup vs. enterprise comp.

## Career Paths & Entry Points
Common paths into this industry. What backgrounds translate well.
Typical progression: entry role → mid-level → senior → leadership.
Lateral entry points for career changers (especially relevant if candidate context mentions a pivot).
**Bridge roles**: Roles that sit between the candidate's current domain and this industry, letting them gain credibility without starting over.

## Career Trajectory & Exit Options
Where do people go *after* working in this industry? Is it a launchpad to other opportunities or a specialized cul-de-sac?
- **Common next moves**: What do 5-10 year veterans typically move to?
- **Talent factory companies**: Which companies are known for developing talent that gets poached?
- **Cross-industry mobility**: How portable are skills built here?

## How This Industry Actually Hires
The hidden job market is real — 50-80% of roles are never publicly advertised. How does hiring *actually* work in this industry?
- **Referral-heavy vs. open application**: What % of hires come through networks?
- **Key recruiters/headhunters**: Which firms specialize in this space?
- **Conference hiring**: Are industry events de facto recruiting fairs?
- **Seasonal patterns**: When do budgets open? When is hiring frozen?
- **The connectors**: Who are the well-networked people everyone in this industry knows?

## Hiring Hotspots
Geographic concentrations (cities, hubs). Remote-friendliness of the industry.
Which companies are hiring most aggressively right now.

## Candidate Fit Assessment
Based on the candidate's background, assess:
- **Strong overlap areas**: Skills/experience that transfer directly
- **Gaps to address**: Skills or domain knowledge the candidate would need
- **Positioning angle**: How to frame their background for this industry
- **Automation risk**: Which roles in this industry face AI/automation pressure vs. which are protected?
```

---

#### Agent 4: Trends, Innovation & Regulation

**Focus:** Technology shifts, regulatory landscape, investment themes, future outlook, risks.

**Research instructions:**
```
Research trends and the regulatory environment in the [Industry Name] industry. Prioritize sources from the last 12 months — older sources only if still clearly relevant. Search for:
1. "[Industry] trends 2026" OR "[Industry] outlook 2026" — current trends
2. "[Industry] technology" OR "[Industry] innovation" — tech shifts
3. "[Industry] regulation" OR "[Industry] policy" — regulatory landscape
4. "[Industry] AI" OR "[Industry] automation" — AI/tech disruption
5. "[Industry] challenges" OR "[Industry] risks" — headwinds and risks
6. "[Industry] predictions" OR "future of [Industry]" — forward-looking analysis
7. "[Industry] biggest problems" OR "[Industry] pain points" — what keeps leaders up at night

Compile your findings into these sections. Cite URLs for every claim, classify sources by tier (A/B/C), and tag high-impact claims with [Confidence: H/M/L, as of YYYY-MM]:

## Industry Pain Points — What Keeps Leaders Up at Night
The 3-5 biggest problems this industry is currently grappling with. For each:
- What the problem is and why it persists
- Who it affects most (which segments, which companies)
- How it creates opportunities for problem-solvers

**This is the #1 differentiator in conversations.** When you can articulate what keeps leaders in this industry up at night, you shift from "applicant" to "potential problem-solver." An insider names specific challenges with nuance; a tourist says "I know the industry faces some challenges."

## Major Trends (Now)
3-5 trends actively reshaping the industry right now. For each:
- What's happening, why it matters, who's driving it
- How it affects hiring and career opportunities
- Whether this is a **secular trend** (structural, lasting 10+ years) or **cyclical** (will reverse with economic conditions)

## Technology & Innovation
Key technology shifts. AI/automation impact. Emerging tools and platforms.
How technology is changing what companies in this industry do and how they hire.

Where relevant, assess technology maturity using Gartner's Hype Cycle lens:
- Innovation trigger → Peak of inflated expectations → Trough of disillusionment → Slope of enlightenment → Plateau of productivity
Technologies on the "Slope of Enlightenment" are the strongest career bets.

## Regulatory Landscape
Current regulatory environment. Recent or upcoming policy changes.
Compliance requirements. How regulation creates or constrains opportunity.
Government incentives, subsidies, or mandates relevant to the industry.

**Regulatory capture assessment:** Does regulation primarily protect incumbents (creating a moat for existing players) or create opportunity for new entrants? This affects whether to target established companies vs. disruptors.

## Investment Themes
Where money is flowing. Hot sub-sectors. What investors are betting on.
How investment trends signal which companies will grow (and hire).
Capital flows are a 6-12 month leading indicator of hiring — money in = jobs coming, money out = layoffs coming.

## Disruption Risk Assessment
Evaluate the industry's vulnerability to disruption using these signals:
- **Heavy regulation creating complacency?** (New tech often enters unregulated segments)
- **Opaque/complex pricing?** (Customers can't understand costs → ripe for simplification)
- **Intermediary-heavy value chains?** (Platforms tend to disintermediate)
- **Low customer switching costs?** (Incumbents vulnerable to better alternatives)
- **Data gravity building?** (Companies with more data get compounding advantages)

Rate overall disruption risk: **Low** / **Moderate** / **High** / **Actively being disrupted**
Note which segments face the most disruption and which are most protected.

## Risks & Headwinds
Industry-level risks: economic sensitivity, regulatory threats, competitive dynamics, technology disruption.
Which segments are most/least exposed.

## 3-Year Outlook
Where is this industry heading by 2028-2029?
Which segments will grow, shrink, or transform?
What does this mean for someone entering the industry now?

Reference specific reports, analysts, or thought leaders where possible.
```

---

#### Agent 5: Entry Strategy & Target Companies for Candidate

**Focus:** How the candidate specifically maps to this industry. Builds a ranked target list of companies and a concrete entry strategy based on their background.

**Research instructions:**
```
Using the candidate context provided, research how this specific candidate should approach the [Industry Name] industry. Search for:
1. "[candidate's current domain] to [Industry] career change" — transition stories
2. "[Industry] companies [candidate's city/location]" — local opportunities
3. "[Industry] companies hiring [relevant role type]" — role-specific openings
4. "[Industry] conferences" OR "[Industry] events" OR "[Industry] communities" — networking entry points
5. "[Industry] newsletters" OR "[Industry] podcasts" OR "[Industry] thought leaders" — knowledge sources
6. "[Industry] fellowships" OR "[Industry] programs" — structured entry programs

Compile your findings into these sections. Cite URLs for every claim, classify sources by tier (A/B/C), and tag high-impact claims with [Confidence: H/M/L, as of YYYY-MM]:

## Candidate → Industry Mapping
A direct analysis of how the candidate's background maps to this industry:
- **Transferable experience**: Specific projects, roles, or skills that directly apply
- **Domain overlap**: Where the candidate's past work intersects with industry needs
- **Unique angle**: What makes this candidate's background unusual/valuable in this industry
- **Narrative**: A 2-3 sentence positioning statement the candidate could use
- **Credible visitor positioning**: The goal is NOT to fake insider status. It's to demonstrate enough depth, humility, and genuine engagement that insiders recognize you as someone worth investing in. Frame the candidate as a "credible visitor" — someone who has done the homework, brings relevant outside perspective, and approaches the industry with genuine curiosity rather than assumed expertise.

## Target Companies — Ranked Shortlist
Rank the top 12-15 companies the candidate should target, considering:
1. Relevance to the candidate's background and skills
2. Hiring signals (active job postings, growth indicators)
3. Geographic fit (near candidate or remote-friendly)
4. Stage/culture fit (based on candidate context)
5. Segment diversity (don't cluster all picks in one segment)

For each entry:
- **Company name** + one-line description
- **Segment**: Where they sit in the industry landscape
- **Why target**: How the candidate's background maps to this company specifically
- **Stage & size**: Funding stage, approximate headcount
- **Location**: HQ + remote policy if known
- **Hiring signal**: Active roles, growth indicators, or "unknown"
- **Website / careers page**: URL

[IF ANCHOR COMPANY WAS EXTRACTED]:
Include the anchor company in the list and note it as the starting point. Rank other companies relative to it.

## Networking Entry Points
- **Industry events & conferences**: Top 3-5 relevant events (with dates if findable)
- **Online communities**: Slack groups, Discord servers, LinkedIn groups, subreddits
- **Newsletters & podcasts**: Top 3-5 for getting up to speed quickly
- **Thought leaders**: 5-8 people to follow on LinkedIn/X for industry signal
- **Professional associations**: Relevant industry bodies or membership organizations

## Knowledge Ramp-Up Plan
A suggested sequence for the candidate to build industry knowledge:
1. **Week 1**: [Subscribe to X, read Y report, listen to Z podcast]
2. **Week 2**: [Attend X event, connect with Y people, research Z companies]
3. **Week 3**: [Apply to X, reach out to Y, prepare Z talking points]

## Structured Entry Programs
Any relevant fellowships, rotational programs, bootcamps, or accelerators designed for career changers or industry entrants.
```

### Step 4: Synthesize Results

After all five agents return, synthesize their findings:

1. **Deduplicate** — if multiple agents mention the same companies or facts, merge into the most detailed version with the best source. Agent 2 (Key Players) and Agent 5 (Target Companies) will both name companies — use Agent 2 for the landscape map and Agent 5 for the candidate-specific ranking.
2. **Resolve contradictions** — if agents report contradictory information (e.g., different market sizes, conflicting trend assessments), **show both values with their sources and mark as `[Needs verification]`**. Do not silently pick one. Prefer higher-tier sources when choosing which to feature prominently, but always disclose the discrepancy.
3. **Cross-link** — connect findings across agents (e.g., a trend from Agent 4 creating demand for a role from Agent 3, a company from Agent 2 that matches a hiring hotspot from Agent 3, an investment theme from Agent 4 validating a target company from Agent 5).
4. **Enrich the target list** — use findings from Agents 1-4 to enhance Agent 5's target company list. Add context about each company's market position (Agent 2), relevant roles they'd hire for (Agent 3), and how industry trends favor them (Agent 4).
5. **Contradiction audit** — Before writing the final dossier, scan all agent outputs for numerical claims (market size, growth rates, funding amounts, headcount, compensation figures). If the same metric appears in multiple agent outputs with different values, treat this as a contradiction and apply the contradiction protocol — report both values with sources and mark `[Needs verification]` — even if individual agents didn't flag it.
6. **Write BLUFs** — For each major section of the dossier, draft a single bold opening sentence summarizing the key takeaway for the candidate. The BLUF answers: "If the reader only reads this one sentence, what must they know?" Keep BLUFs factual and specific — never generic filler like "This is a growing industry."
7. **Draft Executive Summary** — Distill all findings into the Executive Summary format (see Step 8 template). This is the last synthesis step — do it after all deduplication, conflict resolution, and cross-linking is complete.
8. **Build Evidence Summary Table** — Compile all high-impact claims from the dossier into a summary table. For each claim, record: the claim, source, source tier, confidence level, and as-of date. Count contradictions found, sources older than 12 months, and Tier C sources used. This table goes at the bottom of the dossier, just before the raw agent appendix.
9. **Generate refresh delta (refreshes only)** — If this is a refresh of an existing dossier, compare the new synthesis against the retained previous Executive Summary and At a Glance. Generate a `## What Changed Since Last Update` section listing: market size revisions, new major players, shifting trends, regulatory changes, revised attractiveness rating, material changes in target company rankings. Keep it to the 3-7 most significant changes. If nothing material changed, say so.

### Step 5: Cross-Reference with Candidate Data

Using the data loaded in Step 2:

1. **Networking contacts** — list any contacts working in this industry from `data/networking.md` with their company, role, and last interaction.
2. **Pipeline status** — if any companies in this industry appear in `data/job-pipeline.md`, note the stage and applications.
3. **Company research** — check `data/company-research/` for any companies in this industry that have already been researched. Note which ones and their research dates.
4. **Experience overlap** — scan `data/project-index.md` for 3-5 projects with the strongest relevance to this industry's needs.
5. **Skills overlap** — cross-reference `data/skills.md` with Agent 3's skills-in-demand list to identify matches and gaps.

### Step 6: Generate Industry Talking Points

Generate **10-15 talking points** the candidate should be able to discuss in conversations about this industry. Each should:
- Demonstrate genuine knowledge (reference specific facts from research)
- Connect to the candidate's background where possible
- Be appropriate for the context type

**By context type:**

- **`interview-prep`**: Strategic talking points showing deep preparation. Link industry trends to the specific company and role. Show you understand the competitive landscape.
  - Examples: "I've been tracking how [trend] is reshaping [segment] — [Company] seems well-positioned because [reason]..."

- **`career-pivot`**: Transition-framing talking points. Show why the pivot makes sense, how experience transfers, genuine interest.
  - Examples: "What drew me to [Industry] was [insight from research] — my background in [X] maps to [specific industry need]..."

- **`networking`**: Curiosity-driven, value-offering points. Show you've done homework and have relevant perspectives to share.
  - Examples: "I was reading about [trend] and noticed [insight] — how is that playing out at [their company]?"

- **`exploration`**: Learning-oriented questions and observations. Show genuine curiosity without pretending expertise.
  - Examples: "I'm trying to understand how [segment] differs from [segment] — from outside it looks like [observation]..."

- **`general`**: Balanced mix of the above.

### Step 7: Generate Follow-Up To-Dos

Based on the research findings, generate actionable follow-up items and write them to `data/job-todos.md`.

Read `data/job-todos.md` first. Then add relevant to-dos such as:
- "Deep-dive research [Company] — top-ranked target from [Industry] landscape" — for the top 3-5 companies from the target list (suggest running `/research-company`)
- "Subscribe to [newsletter/podcast] to build [Industry] knowledge" — from Agent 5's networking entry points
- "Attend [event] on [date]" — if specific upcoming events were found
- "Connect with [person] on LinkedIn — [Industry] thought leader" — for key people identified
- "Review [project] for [Industry] positioning" — if experience overlap was found
- "Draft [Industry] positioning statement" — if the candidate needs to articulate their entry story
- "Fill skills gap: research [skill] learning resources" — for critical skills gaps identified

Use the same format as existing entries in `data/job-todos.md`:
- **Priority**: `High` for top 3 companies and immediate actions, `Med` for the rest
- **Due**: 14 days from today for research tasks, 7 days for networking actions
- **Status**: `Pending`
- **Notes**: `From /research-industry on [date]`

If `data/job-todos.md` doesn't exist, create it with the standard header format.

### Step 8: Write Output File

Create the output directory if needed, then write to `data/industry-research/<slug>.md`:

```markdown
# [Industry Name] — Industry Landscape Analysis

> Generated by `/research-industry` on [date]. Context: [context type].
> Last updated: [date]

## At a Glance

| Field | Detail |
|-------|--------|
| Industry | [name] |
| Market Size | [TAM estimate] |
| Growth Rate | [CAGR or growth descriptor] |
| Maturity | [nascent / emerging / growth / mature / declining] |
| Cycle Type | [secular growth / cyclical / mixed — brief note] |
| Key Segments | [top 3-4 segments] |
| Dominant Business Model | [SaaS, services, marketplace, etc.] |
| Market Concentration | [fragmented / moderating / concentrated / oligopoly] |
| Disruption Risk | [low / moderate / high / actively being disrupted] |
| Regulatory Intensity | [low / moderate / high] |
| Remote-Friendliness | [low / moderate / high] |

## Executive Summary

**Verdict:** [One sentence — is this industry worth pursuing and why]
**Attractiveness:** High | Medium | Low — [one-sentence rationale]

**Best segments right now:**
1. [Segment] — [why, with evidence]
2. [Segment] — [why]
3. [Segment] — [why]

**Biggest structural risk:** [One sentence, citing source]

**Your fit:** [Strongest transferable skill/experience] / **Gap to close:** [Most critical gap]
**Top 3 targets:** [Company A] · [Company B] · [Company C]
**Recommended first move:** [One concrete action this week]

## Connection to You

[Networking contacts in this industry, pipeline entries, companies already researched, experience overlap, skills overlap. If none, state "No existing connections to this industry — fresh territory."]

[IF REFRESH]: ## What Changed Since Last Update
> Comparing against previous research from [previous date].

[3-7 bullet points listing material changes: market size revisions, new major players, shifting trends, regulatory changes, revised attractiveness rating, target company ranking shifts. If nothing material changed, state "No material changes detected."]

---

## Market Overview

### Market Size & Growth

**[BLUF — one bold sentence: the headline number and whether it's growing, stalling, or declining]**

[From Agent 1]

### Market Segmentation

**[BLUF — which segments matter most for the candidate and why]**

[From Agent 1]

### Value Chain & Profit Pools

**[BLUF — where the money actually is, in one sentence]**

[From Agent 1 — where value flows and where margin concentrates. High-margin segments = better comp and job quality.]

### Dominant Business Models

**[BLUF — the winning model and its economics in one sentence]**

[From Agent 1]

### Industry Maturity & Cycle Assessment

**[BLUF — life cycle stage and whether timing is favorable for entry]**

[From Agent 1 — life cycle stage, cyclical vs. secular assessment, market concentration]

---

## Key Players

### Market Leaders

**[BLUF — who dominates and what that means for job seekers]**

[From Agent 2 — the 5-8 dominant companies]

### High-Growth / Breakout Companies

**[BLUF — where the growth hiring is concentrated]**

[From Agent 2 — companies on steep growth trajectories]

### Emerging / Early-Stage Players

**[BLUF — the high-risk/high-upside options]**

[From Agent 2 — newer companies worth watching]

### Key Investors & Backers
[From Agent 2]

---

## Talent & Career Landscape

### In-Demand Roles

**[BLUF — the roles with the most demand and best candidate fit]**

[From Agent 3]

### Skills in Demand

**[BLUF — candidate's strongest match and biggest gap, in one sentence]**

[From Agent 3 — with candidate fit flags]

### Key Metrics & KPIs Everyone Watches

**[BLUF — the 2-3 metrics the candidate must learn to speak fluently]**

[From Agent 3 — the 5-8 metrics that matter, benchmarks, and how the candidate's past metrics map to these]

### Compensation Landscape

**[BLUF — typical comp range for the candidate's target level]**

[From Agent 3]

### Career Paths & Entry Points

**[BLUF — the most realistic entry path for this candidate]**

[From Agent 3 — including bridge roles for career changers]

### Career Trajectory & Exit Options

**[BLUF — launchpad or cul-de-sac? One sentence.]**

[From Agent 3 — where do people go after this industry? Launchpad or cul-de-sac?]

### How This Industry Actually Hires

**[BLUF — referral-driven or open-application friendly? The key insight.]**

[From Agent 3 — referral vs. open application, key recruiters, conference hiring, seasonal patterns]

### Hiring Hotspots
[From Agent 3]

### Your Fit Assessment

**[BLUF — net assessment: strong fit, stretch, or long shot?]**

[From Agent 3 — candidate-specific overlap, gaps, positioning, automation risk]

---

## Industry Pain Points — What Keeps Leaders Up at Night

**[BLUF — the single biggest problem and why it creates opportunity]**

[From Agent 4 — the 3-5 biggest problems. This is the #1 conversational differentiator.]

## Trends & Outlook

### Major Trends (Now)

**[BLUF — the trend most likely to create jobs in the next 12 months]**

[From Agent 4 — with secular vs. cyclical flags]

### Technology & Innovation

**[BLUF — the technology shift with the most career impact]**

[From Agent 4 — with Hype Cycle maturity assessment where relevant]

### Regulatory Landscape

**[BLUF — net regulatory impact: tailwind or headwind for hiring?]**

[From Agent 4 — including regulatory capture assessment]

### Investment Themes

**[BLUF — where capital is flowing and what that signals for hiring]**

[From Agent 4 — capital flows as leading indicator of hiring]

### Disruption Risk Assessment

**[BLUF — overall disruption rating and what it means for career stability]**

[From Agent 4 — vulnerability signals, overall rating, protected vs. exposed segments]

### Risks & Headwinds
[From Agent 4]

### 3-Year Outlook

**[BLUF — the one-sentence forward view: better, worse, or transforming?]**

[From Agent 4]

---

## Your Entry Strategy

### Candidate → Industry Mapping

**[BLUF — the candidate's strongest positioning angle, in one sentence]**

[From Agent 5 — transferable experience, unique angle, positioning narrative]

### Target Companies — Ranked Shortlist

[Top 12-15 companies from Agent 5, enriched with Agent 2-4 cross-references]

#### 1. [Company Name]
> [One-line description]

- **Segment:** [Where they sit in the industry landscape]
- **Why target:** [How candidate's background maps to this company]
- **Stage & size:** [Funding stage, approximate headcount]
- **Location:** [HQ + remote policy if known]
- **Hiring signal:** [Active roles, growth indicators, or "unknown"]
- **Website:** [URL]
- **Careers:** [Careers page URL if found]

#### 2. ...

### Networking Entry Points
[From Agent 5 — events, communities, newsletters, thought leaders]

### Knowledge Ramp-Up Plan
[From Agent 5 — week-by-week plan]

### Structured Entry Programs
[From Agent 5 — fellowships, programs, accelerators]

---

## Industry Talking Points — [Context Type]

[10-15 numbered talking points, each with context note]

1. **[Talking point]**
   _Based on: [research finding]. Connection to your background: [if applicable]_

2. ...

## Positioning Advice

### How to Frame Your Background
[2-3 paragraphs on how the candidate should present their experience for this industry]

### Things to Emphasize
[3-5 items from the candidate's background that resonate with this industry]

### Things to Downplay or Reframe
[Topics to avoid or reposition — industry-specific sensitivities, background elements that don't translate]

### Your Relevant Experience to Reference
[3-5 specific projects/roles with brief explanation of industry relevance]

---

## Industry Jargon Glossary

[From Agent 3 — 15-25 essential terms with plain-English definitions and usage examples. Master these before any conversation.]

| Term | Definition | Used in context |
|------|-----------|-----------------|
| [TERM] | [Plain-English definition] | "[Example sentence using the term naturally]" |
| ... | ... | ... |

---

## Evidence Summary

| # | Claim | Source | Tier | Confidence | As-of |
|---|-------|--------|------|------------|-------|
| 1 | [High-impact claim, e.g., "Market size: $45B"] | [Source name + URL] | [A/B/C] | [High/Medium/Low] | [YYYY-MM] |
| 2 | [Next claim] | [Source] | [Tier] | [Confidence] | [As-of] |
| ... | | | | | |

**Contradictions found:** N (brief description of each, with section reference)
**Sources older than 12 months:** N of total (note if foundational data or stale)
**Tier C sources used:** N of total (all flagged inline with caveats)

## Sources

[All URLs cited by agents, organized by section. Include source tier for each: (A), (B), or (C).]

---
---

# Appendix: Raw Agent Outputs

> Below this line is unedited agent output preserved for auditability.
> The synthesized dossier above is the authoritative version.

### Agent 1: Market Overview & Structure
[Full unedited output]

### Agent 2: Key Players & Ecosystem Map
[Full unedited output]

### Agent 3: Talent Demand & Career Paths
[Full unedited output]

### Agent 4: Trends, Innovation & Regulation
[Full unedited output]

### Agent 5: Entry Strategy & Target Companies
[Full unedited output]
```

**Important:** The Raw Agent Outputs appendix must contain the **complete, unedited output** from each agent. Do not summarize, truncate, or reformat. The double `---` line before the appendix visually separates the authoritative dossier from the audit trail.

### Step 9: Display Summary

After writing the file, display a concise summary to the user. This is derived directly from the Executive Summary — do not duplicate effort:

```markdown
## Industry Research Complete — [Industry Name]

**Saved to:** `data/industry-research/<slug>.md`
**Context:** [context type] [with anchor company if applicable]

**Attractiveness:** [High | Medium | Low] — [rationale from Executive Summary]

### Best Segments
1. [Segment] — [why]
2. [Segment] — [why]
3. [Segment] — [why]

### Your Fit
- **Strongest overlap:** [top transferable skill/experience area]
- **Biggest gap:** [key skill or knowledge to develop]
- **Positioning angle:** [1-sentence narrative]

### Top 5 Target Companies
1. [Company] — [why it's a fit] — [hiring signal]
2. ...
3. ...
4. ...
5. ...

### Quick-Start Actions
1. [Most impactful immediate action]
2. [Second action]
3. [Third action]

### To-Dos Created
- [List of to-dos added to job-todos.md]

### What's Next?
[2-3 relevant suggestions from Step 10]

---
Full landscape analysis: `data/industry-research/<slug>.md`
```

### Step 10: Suggest Next Steps (Handoff)

Based on the research findings, suggest the most relevant next actions using other skills:

- **For top-ranked targets:** "Deep-dive your top picks with `/research-company [company]`."
- **If anchor company was identified:** "Get the full picture on [anchor] with `/research-company [anchor]`."
- **If strong candidate fit found:** "Generate a tailored CV with `/generate-cv` when you find a specific role."
- **If networking entry points identified:** "Draft outreach with `/cold-outreach [contact] [company]`."
- **If career pivot context:** "Refine your positioning by running `/extract-identity` to surface transferable narrative patterns."
- **If pipeline entries exist:** "Check pipeline status with `/pipe` and update stages."

Display 2-3 of the most relevant suggestions based on context type and findings — not all of them.

## Edge Cases

- **No arguments provided**: Display usage message with examples (Step 1).
- **Industry too broad** (e.g., "technology"): If initial searches return overly generic results, ask the user to narrow: "Technology is very broad — could you specify a segment? E.g., 'health tech', 'climate tech', 'defense tech', 'fintech'."
- **Industry too niche**: If search results are very thin, broaden to the parent industry and note the niche focus. E.g., "precision fermentation" → research within "alternative proteins" / "food tech" while keeping the niche lens.
- **Anchor company extracted**: Include it in the target list and use it as a reference point ("companies like [Anchor]"). Suggest running `/research-company` for a deep dive on the anchor.
- **No profile data**: Proceed with generic candidate context. Note in output: "Candidate context was limited — run `/import-cv` for better personalization."
- **Agent returns thin results**: Proceed with remaining agents' data. Note which research dimension was thin in the output. Do NOT fail the entire operation because one agent struggled.
- **Agent failure / timeout**: If an agent fails to return, proceed with the remaining agents. Note the gap in the output and what section is affected.
- **Existing company research found**: If `data/company-research/` contains dossiers for companies in this industry, reference them in the "Connection to You" section and note them in the target list.
- **Overlaps with /research-company**: This skill maps the landscape; `/research-company` goes deep on one company. The natural flow is: `/research-industry` first to identify targets, then `/research-company` on the top picks.
