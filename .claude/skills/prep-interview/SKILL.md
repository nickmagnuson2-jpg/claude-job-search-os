---
name: prep-interview
description: One-command interview prep package — question mapping, company context digest, and tactics/logistics — saved as a single output document
argument-hint: <company> [role] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(output/**), Write(output/**), Write(data/job-todos.md), Task, WebSearch, WebFetch
---

# Prep Interview — One-Command Interview Prep Package

Generates a comprehensive interview prep document for a scheduled or upcoming interview. Uses 3 parallel agents to map questions to coached answers, condense the company context, and build a tactics + logistics section. Produces a single output file and creates a follow-up debrief to-do.

## Arguments

- `$ARGUMENTS`:
  - **`<company>`** (required) — company name; matched against pipeline and company research dossier
  - **`[role]`** (optional) — role title if there are multiple pipeline entries for the same company
  - **`[context]`** (optional) — interview type (`recruiter`, `hiring-manager`, `panel`), date, or specific focus (e.g., `"hiring manager round, 2026-03-05"`, `"focus on operations experience"`)

Examples:
- `/prep-interview "Impossible Foods"` — full prep for Impossible Foods
- `/prep-interview "Amae Health" "Strategy & Operations"` — specify role
- `/prep-interview "Ripple Foods" "hiring manager, 2026-03-10"` — with date and type
- `/prep-interview "Impossible Foods" "Chief of Staff" "panel interview, focus on leadership"` — full args

If no company provided, display usage:
```
Usage: /prep-interview <company> [role] [context]

Examples:
  /prep-interview "Impossible Foods"
  /prep-interview "Amae Health" "Strategy & Operations"
  /prep-interview "Ripple Foods" "hiring manager, 2026-03-10"
```

## Instructions

### Step 1: Parse Arguments and Look Up Pipeline

1. Parse `$ARGUMENTS` into company name, optional role, optional context string.
2. **Infer interview type** from context string:
   - Contains "recruiter" or "phone screen" or "screening" → `recruiter`
   - Contains "hiring manager" or "HM" → `hiring-manager`
   - Contains "panel" or "loop" → `panel`
   - Otherwise → `general` (default)
3. **Extract interview date** if present in context (look for YYYY-MM-DD or natural date like "March 10").
4. Read `data/job-pipeline.md`. Search for the company name (case-insensitive, fuzzy — company name as substring). If multiple roles for the same company, and a role was specified, match on that. If still ambiguous, use the most recently updated entry.
5. From the matching pipeline entry, note: **stage**, **role**, **CV Used** (output filename), **URL**, **notes**.
6. Generate a slug from the company name (lowercase, hyphens).

### Step 2: Load Context (parallel)

Read the following in parallel — skip any that don't exist:

1. Company dossier — `output/<slug>/<slug>.md`
2. Company notes — `data/company-notes/<slug>.md` — personal notes, call context, recruiter observations
3. `data/profile.md`
4. `data/professional-identity.md`
5. `coaching/coached-answers.md`
6. `coaching/anti-pattern-tracker.md`
7. `coaching/pressure-points.md`
8. `framework/answering-strategies/anti-patterns.md`
9. If CV Used field is populated in pipeline: read `output/<cv-filename>` to get the exact projects and language used

Also Glob `data/projects/*.md` to have project files available for question mapping.

### Step 3: Web Research if No Company Dossier

If no company dossier was found (neither subfolder nor flat format):
- Run 2 web searches in parallel:
  1. `"[Company] news 2026"` — recent developments
  2. `"[Company] [role] interview"` — interview-specific info
- Use the results to construct a minimal company context.
- Note prominently in the output: "⚠️ No dossier found for [Company] — prep is based on quick web search. For deeper prep, run `/research-company "[Company]"` first."

### Step 3b: Cross-Skill Consistency Check

Before launching agents, verify consistency between the data sources loaded in Steps 2-3. Check for and flag any contradictions:

1. **CV vs. research dossier:** If both a CV (from pipeline's "CV Used" field) and a company research dossier exist, verify:
   - The company description/framing in the CV aligns with the dossier's findings (e.g., CV doesn't describe the company as "Series A" if the dossier shows Series C)
   - The role framing in the CV matches what the dossier says the company is hiring for

2. **CV vs. coached answers:** If both exist, verify:
   - Experience claims are consistent (e.g., CV says "5 years React" but coached answer says "about 3 years with React")
   - Project descriptions don't contradict each other across the two files
   - The professional summary narrative aligns with coached pitch answers

3. **Coached answers vs. professional-identity.md:** If both exist, verify:
   - Strengths claimed in coached answers align with those in professional-identity.md
   - Career direction framing is consistent

If inconsistencies are found, include a **Consistency Warnings** section in the output file (after the At a Glance table) listing each discrepancy with:
- What conflicts (file A says X, file B says Y)
- Suggested resolution (which version to use in the interview, or flag for the user to decide)

Pass any found inconsistencies to Agent 1 (Question Mapping) so it can account for them in answer frameworks.

### Step 4: Launch 3 Parallel Agents

Use the Task tool to launch **3 parallel subagents** (`subagent_type: "general-purpose"`, `model: "sonnet"`, `max_turns: 10`).

Pass to each agent:
- The company name, role, interview type, and interview date (if known)
- The candidate's key background points (from professional-identity.md and profile.md)
- The CV used (if available)
- The coached answers text (if available)
- The anti-pattern tracker text (if available)
- The company context (from dossier or web search results)
- Their specific focus and output format instructions below

---

#### Agent 1: Question Mapping

**Focus:** Likely interview questions → coached answers. Identify coverage gaps.

**Instructions:**
```
You are preparing [Candidate Name] for a [interview type] interview at [Company] for the [Role] position.

Your job: Map the most likely interview questions to existing coached answers, and flag gaps.

Context provided:
- Role requirements: [from JD/pipeline notes]
- Interview type: [recruiter/hiring-manager/panel/general]
- Company: [Company] — [brief context from dossier/search]
- Coached answers available: [coached-answers content]
- Projects/experience: [from CV if available, otherwise project summaries]

Step 1: Generate 10–12 likely questions for this specific role and interview type.
For a recruiter screen: focus on background, motivation, logistics, fit.
For a hiring manager: focus on behavioral STAR questions, strategic thinking, specific experience.
For a panel: mix of above plus role-specific technical/functional questions.
Tailor to [Company]'s stage/industry/culture signals from the context.

Step 2: For each question, check coached-answers.md and project experience for a good answer.
- If a strong coached answer exists: note the answer framework and key points to hit.
- If a partial match exists (related but not exact): note what to adapt.
- If no coached answer exists: flag as GAP.

Step 3: List the top 5 GAPS — questions where no good coached answer exists. These need new prep.

Output format:

## Likely Questions → Coached Answers

### Q1. [Question]
**Type:** [behavioral/motivational/situational/logistics]
**Answer framework:**
- [Key point 1 — cite specific project if relevant]
- [Key point 2]
- [Key point 3]
**Coached answer reference:** [Section in coached-answers.md, or "CONSTRUCT FROM: [project]"]
**Anti-patterns to avoid:** [1-2 relevant warnings]

### Q2. [Question]
...

## Answer Gaps (prep these before the interview)
| Question | Why it's a gap | Suggested approach |
|----------|---------------|-------------------|
| [Question] | No coached answer, no direct project | Use [gap-reframing strategy] |
```

---

#### Agent 2: Company Context Digest

**Focus:** Condense company research to what matters for THIS specific interview.

**Instructions:**
```
You are preparing [Candidate Name] for a [interview type] interview at [Company] for the [Role].

Your job: distill the company dossier (or web research) into only what matters for this interview.

Company context provided: [dossier or web search summary]
Interview type: [recruiter/hiring-manager/panel/general]
Candidate background: [brief summary from professional-identity.md]

Produce a tight, interview-ready company brief. NO fluff. Be specific and actionable.

## Company Context Digest

### Mission in 2 Sentences
[What [Company] does and why it matters — in plain language]

### Key Business Challenge Right Now
[The most important problem or opportunity the company is facing — what this team/role is hired to solve]

### Recent News to Reference
[1-3 specific, recent developments the candidate can mention to show they've done their homework. Include "I noticed that..." conversation starters.]

### What This Interviewer Probably Cares About
[2-3 things the hiring manager or recruiter at [Company] at this stage likely cares about most — based on role, stage, interview type, and company context]

### Cultural Signals
[2-3 specific cultural cues from the research — how the company talks about itself, what they value, any phrases or themes to mirror]

### Things to Avoid Mentioning
[Any sensitive topics, competitors to avoid comparing, assumptions to steer clear of]
```

---

#### Agent 3: Tactics & Logistics

**Focus:** Pre-call checklist, opening/closing strategy, questions to ask, logistics.

**Instructions:**
```
You are preparing [Candidate Name] for a [interview type] interview at [Company] for the [Role].

Your job: tactical preparation — how to handle the call, not just what to say.

Anti-patterns available: [anti-patterns.md content]
Anti-pattern tracker: [anti-pattern-tracker.md content — personal persistent patterns]
Company stage and interview type: [Company], [interview type], [stage from pipeline]

## Pre-Call Checklist (60-second scan before the call)
[Top 7-10 checklist items — pulled from anti-patterns.md + personal anti-pattern tracker, tailored to this specific interview type and company]
- [ ] [Item]
- [ ] [Item]

## Opening Strategy (first 60 seconds)
[How to open the call — tone, the one thing to establish immediately, what NOT to do in opening]
[Specific suggested opening line for a [interview type] call]

## Closing Strategy
[How to close the call — what to ask, what to confirm, how to leave a strong impression]
[Specific suggested closing move for [interview type] at [Company]]

## Questions to Ask — [Interview Type] at [Company]
[5–7 strong questions tailored to this interview type and company. For each: the question + one-line note on what it signals.]

For recruiter: focus on role clarity, team dynamics, next steps, timeline.
For hiring manager: focus on team priorities, success metrics, company direction, role impact.
For panel: mix — some technical/functional, some culture, some about the panel members' work.

1. [Question] — *signals: [what this shows]*
2. [Question] — *signals: [what this shows]*
...

## Format & Logistics Reminders
- Interview type: [recruiter/hiring manager/panel]
- Platform: [if known from context, otherwise "confirm platform before the call"]
- Duration: [if known, otherwise "typically N minutes for [type]"]
- Who you're talking to: [from pipeline notes or company research, if known]
- Dress: [appropriate level — video call defaults to business casual unless context suggests otherwise]
- Pre-call: [specific prep actions — review notes, test tech, etc.]
```

---

### Step 5: Compile Output Document

After all 3 agents return, compile their outputs into a single document. Do not just concatenate — synthesize:
- Ensure question mapping (Agent 1) and company context (Agent 2) don't repeat the same facts
- Merge any overlapping anti-pattern warnings between Agent 1 and Agent 3 into one unified list
- Cross-link where relevant (e.g., if Agent 2 identified a key business challenge, and Agent 1 has a question about it, note the link)

### Step 6: Determine Output Filename and Save

- Use the company slug from Step 1 as the subfolder: `output/<company-slug>/`
- Generate: `output/<company-slug>/MMDDYY-prep.md`
  (company is already in the folder name, omit from filename)
- If file exists, append `-v2`, `-v3` etc.
- Write the compiled document to that path.

### Step 7: Add Debrief To-Do

1. Read `data/job-todos.md`.
2. Add a new to-do:
   - **Task**: `Debrief after [Company] interview — run \`/debrief\``
   - **Priority**: `High`
   - **Due**: interview date (if known from context), otherwise today
   - **Notes**: `From /prep-interview on [today's date]`
3. Write updated file.

### Step 8: Display Summary

```markdown
## Interview Prep Ready — [Company] / [Role]

**Interview type:** [recruiter/hiring-manager/panel/general]
**Date:** [date if known, or "not specified"]
**Stage:** [from pipeline]

**Saved:** `output/<company-slug>/MMDDYY-prep.md`

### Coverage
- Questions mapped: N (N with coached answers, N gaps)
- Company dossier: [✅ Full dossier used] / [⚠️ Quick web search only — run `/research-company "[Company]"` for depth]
- Anti-patterns checklist: N items

### Top 3 Answer Gaps (prep these)
1. [Question with no coached answer]
2. [Question with no coached answer]
3. [Question with no coached answer]

### To-Do Created
✅ "Debrief after [Company] interview" added to job-todos.md [due: date]

---
Full prep doc: `output/<company-slug>/MMDDYY-prep.md`
After the interview: `/debrief` to log session and update coached answers
```

## Output File Format

```markdown
# Interview Prep — [Company] / [Role]
> [Interview type] | [Date if known] | Generated [today]

## At a Glance
| Field | Detail |
|-------|--------|
| Company | [name] |
| Role | [title] |
| Stage | [pipeline stage] |
| Interview type | [recruiter/hiring-manager/panel/general] |
| Date | [date or "TBD"] |
| Key contact | [if known from pipeline or research] |
| CV used | [filename or "not specified"] |

[IF INCONSISTENCIES FOUND IN STEP 3b]:
## ⚠️ Consistency Warnings

| # | Conflict | Source A | Source B | Suggested Resolution |
|---|----------|----------|----------|---------------------|
| 1 | [What conflicts] | [File A says X] | [File B says Y] | [Which to use / user to decide] |

> Review these before the interview. Use the suggested resolution or update the source files.

## 15-Second Pitch (for this role)
[Tailored from professional-identity.md + specific role requirements at this company]
Format: "[Who you are] with [key credential/experience]. Most relevant: [2 projects/roles]. Looking for [role type] — interested in [Company] because [specific, researched reason]."

## Likely Questions → Coached Answers
[From Agent 1 — 10–12 questions with answer frameworks, key points, coached answer references, and per-answer anti-patterns]

## Answer Gaps (no coached answer exists — prep these)
[From Agent 1 — questions flagged as gaps, with suggested approach for each]

## Company Context (condensed)
[From Agent 2 — mission, key business challenge, news to reference, what they care about, cultural signals, things to avoid]

## Questions to Ask
[From Agent 3 — 5–7 tailored questions with "signals:" notes]

## Pre-Call Checklist
[From Agent 3 — 7–10 checkbox items, tailored to interview type and personal anti-patterns]

## Opening & Closing Strategy
[From Agent 3]

## Logistics
[From Agent 3 — format, platform, duration, who you're talking to, dress, pre-call reminders]
```

## Edge Cases

- **Company not in pipeline**: Proceed with available data. Note: "⚠️ [Company] not found in pipeline — consider adding with `/pipe add`."
- **No coached answers file**: Skip coached answer cross-referencing. Note the gap in all question mapping entries.
- **No company dossier and web search fails**: Proceed with whatever context is available from the pipeline and profile. Flag prominently.
- **Agent fails**: Proceed with remaining agents' data. Note which section is thin.
- **No interview date provided**: Omit date from output. Set debrief to-do due date as "—" (no deadline).
- **Multiple pipeline entries for same company**: If role was specified, use the matching one. If not, use the most recently updated entry. Note which entry was matched.
