---
name: generate-cv
description: Generate a tailored CV + interview cheat sheet for a specific role — follows the 11-step resume workflow, saves to output/, and updates the pipeline
argument-hint: <job-url-or-jd> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(plugins/*), Glob(framework/*), Write(output/*), Edit(output/*), Edit(data/job-pipeline.md), WebFetch, WebSearch
---

# Generate CV — Tailored Resume + Cheat Sheet

Generate a fully tailored CV and companion interview cheat sheet for a specific job posting. Follows the 11-step process in `framework/resume-workflow.md`, produces ATS-optimised output in the right format for the market, and saves both files to `output/`.

## Arguments

- `$ARGUMENTS` (required at minimum a job URL or pasted JD text):
  - **`<job-url-or-jd>`** (required) — a URL to the job posting, or the full pasted job description text
  - **`[context]`** (optional) — additional instructions, e.g. `"emphasize McKinsey"`, `"US resume format"`, `"focus on operations experience"`

Examples:
- `/generate-cv https://jobs.impossible.com/cos-role` — fetch and tailor to that posting
- `/generate-cv "Chief of Staff, Ripple Foods..." "emphasize food/FMCG experience"` — pasted JD with context
- `/generate-cv https://example.com/job "US format, keep to 1 page"`

If no arguments provided, display usage:
```
Usage: /generate-cv <job-url-or-jd> [context]

Examples:
  /generate-cv https://company.com/jobs/role
  /generate-cv "Job description text..." "emphasize operations"
  /generate-cv https://company.com/job "US format"
```

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS`:
1. **Detect URL vs pasted text** — if the first token contains `http` or a recognisable domain pattern, treat it as a URL. Otherwise treat the entire argument (up to any quoted context string) as pasted JD text.
2. **Extract context string** — any quoted string that follows the URL/JD, or text after the job description ends.
3. **If URL detected**: use WebFetch to retrieve the job posting content. If the fetch fails, ask the user to paste the JD text directly.

### Step 2: Load Candidate Context (parallel)

Read the following files in parallel — skip any that don't exist, never fail:

1. `data/profile.md`
2. `data/professional-identity.md`
3. `data/education.md`
4. `data/skills.md`
5. `data/certifications.md`
6. `data/project-index.md`
7. `data/companies.md`
8. `framework/resume-workflow.md`
9. `framework/style-guidelines.md`

If `data/profile.md` is missing, warn: "Profile not found — run `/import-cv` first for best results. Proceeding with available data."

### Step 3: Analyse the Role

From the job posting text, extract:

- **Company name** and generate a slug (lowercase, hyphens, e.g. `impossible-foods`)
- **Role title** and a role slug (e.g. `chief-of-staff`)
- **Required skills** (must-haves stated explicitly)
- **Nice-to-have skills** (preferred, bonus, or desirable)
- **Seniority level** (IC, manager, director, VP, C-suite)
- **Top 10 ATS keywords** — the most important terms to appear in the CV for ATS passage. Prioritise: job title words, required skills, industry-specific terms, tools/technologies named.
- **Market** — infer from location mentions or company HQ: `US`, `UK`, `DACH`, or `international`. Default to `US` if unclear.
- **Industry** — infer from company and role context.

### Step 4: Check Plugins

If `data/plugin-activation.md` exists, read it. Glob `plugins/*/plugin.md` and check for any plugin with `scope: cv` or `scope: all`. If found, read those plugin files and apply any instructions they contain for CV generation.

### Step 5: Select Projects

1. Read `data/project-index.md` — scan all entries for relevance to the role's required skills, industry, seniority level, and company type.
2. Select **3–6 most relevant projects**. Criteria: skill overlap with required skills > industry/domain match > seniority match > recency.
3. Read the full project files for each selected project from `data/projects/`.
4. **NEVER read or use files from `data/project-background/`** — those are internal-only and must not appear in CVs.
5. Note the rationale for each selected project (will be used in the cheat sheet).

### Step 6: Generate the CV

Follow **all rules** in `framework/resume-workflow.md`. Key requirements:

- **Professional summary** — 3–4 lines tailored to this specific role. Opens with a hook tied to the company's mission or the role's core challenge.
- **Project ordering** — by relevance to this role, not chronologically. Most relevant project first.
- **Skills section** — emphasise skills that appear in the JD. Lead with required skills the candidate has.
- **ATS keyword coverage** — verify all 10 extracted keywords appear at least once in the CV text. If a keyword is missing, find a natural place to include it.
- **Format** — choose from `framework/style-guidelines.md` based on the market:
  - US: 1–2 pages, no photo, no DOB, concise bullet points
  - UK: 2 pages, CV (not resume), more narrative allowed
  - DACH: Photo optional, more formal structure
  - International: follow closest regional convention based on company HQ
- **Achievements over responsibilities** — lead bullets with quantified outcomes where possible.
- **No content from `data/project-background/`** — enforce absolutely.

### Step 7: Generate Companion Cheat Sheet

Alongside the CV, generate a pre-interview cheat sheet for this specific role:

**Cheat sheet contents:**

1. **15-second recruiter pitch** — tailored to this role. Format: "[Identity hook] with [X years / key credential]. Most relevant experience: [2 projects]. What I'm looking for: [role type] at [company type]. Interested in [Company] because [specific reason]."

2. **Must-have requirements coverage** — for each required skill or qualification from the JD:
   - 2–3 specific bullet points from the selected projects that demonstrate it
   - Direct quote-ready: "When asked about [requirement], cite [Project] where I [specific action/result]"

3. **Compensation, availability, start date** — pulled from `data/profile.md` (skip if not present)

4. **Coached answers to cross-reference** — read `coaching/coached-answers.md` if it exists. Flag any coached answers that directly apply to likely questions for this role. List: "Existing coached answer for: [topic]."

5. **Do NOT say** warnings — read `coaching/anti-pattern-tracker.md` and `framework/answering-strategies/anti-patterns.md`. Include the most relevant 5–7 warnings for this specific role/context.

6. **Keyword cheat** — list all 10 ATS keywords with a one-line reminder of which project to cite for each.

### Step 8: Determine Output Filenames

- Generate date prefix: `YYYYMMDD` (today's date)
- CV filename: `output/YYYYMMDD-[role-slug].md` (e.g., `output/20260223-chief-of-staff.md`)
- Cheat sheet filename: `output/YYYYMMDD-[role-slug]-cheatsheet.md`
- If a file at that path already exists, append `-v2`, `-v3` etc.

### Step 9: Save Output Files

Write both files:
1. **CV** → `output/YYYYMMDD-[role-slug].md`
2. **Cheat sheet** → `output/YYYYMMDD-[role-slug]-cheatsheet.md`

### Step 10: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive, fuzzy match — check if the company name from the JD appears as a substring in any active pipeline entry).
3. If found: update that entry's **CV Used** field to the CV output filename (just the filename, not full path).
4. If not found: note in the summary that the company isn't in the pipeline yet and suggest `/pipe add "[Company]" "[Role]"`.

### Step 11: Display Summary

```markdown
## CV Generated — [Role Title] at [Company]

**Format:** [US/UK/DACH/international] | **Market:** [market]
**CV saved:** `output/YYYYMMDD-[role-slug].md`
**Cheat sheet:** `output/YYYYMMDD-[role-slug]-cheatsheet.md`

### ATS Keyword Coverage
| Keyword | Present? | Where |
|---------|----------|-------|
| [keyword 1] | ✅ | Professional Summary |
| [keyword 2] | ✅ | Project: [name] |
| [keyword 3] | ⚠️ | Not found — consider adding |

**Coverage: N/10 keywords**

### Projects Selected
1. [Project name] — [one-line rationale]
2. ...

### Pipeline
[✅ CV Used field updated in pipeline for [Company]] OR [⚠️ [Company] not in pipeline — add with: `/pipe add "[Company]" "[Role]"`]

### Suggested Next Step
- Review output: `/review-cv output/YYYYMMDD-[role-slug].md`
- When ready to apply: `/pipe update "[Company]" Applied`
- Before interview: `/prep-interview "[Company]"`
```

## Cheat Sheet Format

```markdown
# Interview Cheat Sheet — [Role] at [Company]
> Generated [date] from CV: output/[cv-filename].md

## 15-Second Pitch
[Tailored pitch for this role]

## Must-Have Requirements — Coverage Map

### [Requirement 1]
- **Project [Name]**: [specific bullet, outcome-focused]
- **Project [Name]**: [specific bullet]
- _Cite when asked:_ "[Trigger phrase]"

### [Requirement 2]
...

## Compensation & Availability
- **Target comp:** [from profile.md]
- **Availability:** [from profile.md]
- **Start date:** [from profile.md]

## Existing Coached Answers (cross-reference coaching/)
- [topic] → see coached-answers.md: "[answer title/section]"
- [topic] → see coached-answers.md: "[answer title/section]"
(omit section if coaching/coached-answers.md not found)

## Do NOT Say — Pre-Call Warnings
1. [Warning from anti-patterns relevant to this role]
2. ...

## ATS Keyword Cheat
| Keyword | Cite This Project |
|---------|------------------|
| [keyword] | [Project name] |
```

## Edge Cases

- **URL fetch fails**: Ask user to paste the JD text directly. Do not attempt to reconstruct the JD from partial content.
- **Too few projects**: If fewer than 3 relevant projects exist, use all available. Note in summary: "Only N projects available — consider adding more to `data/projects/`."
- **Missing profile.md**: Proceed without personal details. Omit compensation/availability from cheat sheet. Flag in summary.
- **Missing coached-answers.md**: Skip that section of cheat sheet silently.
- **Keywords not coverable**: If a keyword can't be added naturally to the CV (e.g. a technology the candidate genuinely doesn't have), flag it in the ATS coverage table as `⚠️ Gap — omit` and note it in the summary as a genuine skill gap.
- **Multiple roles at same company in pipeline**: Update the most recently active matching entry.
