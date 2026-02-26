---
name: generate-cv
description: Generate a tailored CV + interview cheat sheet for a specific role — follows the 11-step resume workflow, saves to output/, and updates the pipeline
argument-hint: <job-url-or-jd> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(plugins/*), Glob(framework/*), Write(output/**), Edit(output/**), Edit(data/job-pipeline.md), WebFetch, WebSearch
---

# Generate CV — Tailored Resume + Cheat Sheet

Generate a fully tailored CV and companion interview cheat sheet for a specific job posting. Produces ATS-optimised output in the right format for the market and saves both files to `output/`. Quality standards, tailoring rules, and the pre-output checklist are defined inline in this skill.

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
8. `framework/style-guidelines.md`
9. `data/company-notes/<company-slug>.md` — personal notes, call context, and observations about this company (derive slug from the JD in Step 1 if already parsed, otherwise read after Step 3)

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

Apply the **Tailoring Rules** and **CV Quality Standards** defined below. Key requirements:

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

### Step 6b: Inline Quality Review (mandatory — do NOT skip)

Before generating the cheat sheet, run the following checks against the CV you just produced. Fix any issues found **in place** — rewrite the CV, don't just flag problems.

**1. Keyword coverage:**
- Take the 10 ATS keywords from Step 3. For each, verify it appears at least once (case-insensitive, but exact product names must match — "React Native" ≠ "React").
- If a keyword is missing, find a natural place to insert it. If genuinely not addable (candidate lacks the skill), note as a gap.

**2. Product specificity:**
- For every technology or platform named, verify the specific sub-product is labelled (not just the parent brand). E.g. "Salesforce" → which cloud? "AWS" → which services? Fix any that are too generic.

**3. Claim integrity:**
- Scan all quantified claims (years of experience, scale numbers, "across N projects").
- Cross-reference against project date ranges and source files from Step 5. Fix or soften any that don't match source data.
- Check certifications against `data/certifications.md` — fix or note status for any expired ones.

**4. No weakness admissions:**
- Search for hedging qualifiers: "currently expanding", "basic knowledge", "evaluated but not used", "learning", "aspiring", "exposure to", "introductory", "some experience". Remove or rewrite any found with confident, specific language.

**5. Concurrent engagement explanation:**
- If any selected projects overlap in time, confirm the CV explains how concurrent work was managed. Skip entirely if no timelines overlap.

**6. Team-fit signals:**
- Confirm at least 2–3 collaboration references exist (code reviews, onboarding, cross-functional coordination, sprint participation). Add naturally if missing.

**7. Structural consistency:**
- **Header pattern**: all project headers follow one format throughout (`Role — Description` or `Description — Role`). No "Flagship:" prefixes. Fix any that don't conform.
- **Bullet format**: all bullets within each section follow the same format (all with bold labels or all without). Fix inconsistencies.
- **Dates**: every project and engagement has a date range. Add approximate dates (e.g. "Q2 2023") if missing.
- **Availability**: header includes availability/location/remote context if market convention expects it or if candidate is in a different region from the role.
- **Sentence completeness**: every bullet point contains at least one verb. Fix fragments.

**8. Language and tense:**
- Spelling variant consistent throughout (all British OR all American English — match the job posting's variant).
- Tense: present tense for current/ongoing roles, past tense for completed ones.
- No native-language calques — check `data/profile.md` for candidate's native language, then scan for false friends or unusual phrasing.

**After all fixes are applied**, record a QC summary to include in Step 11's output:
- Keyword coverage: X/10 matched (list any unfixable gaps)
- Claims verified: X checked, Y corrected
- Issues fixed: list structural, language, or self-sabotage fixes made
- Clean: confirm if no issues found

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

**Cheat sheet quality rules:**
- For collaboration/teamwork questions, use the best **peer-work project** as the primary example — not projects where the candidate was sole decision-maker.
- Include rate pushback defense if the rate is above market average for the role.
- Include a short closing with interest statement + max 1–2 questions for the recruiter (save detailed/technical questions for the client interview).
- For each answer where a known anti-pattern could fire, add a bold **"Do NOT say:"** warning with the specific trap to avoid.

### Step 8: Determine Output Filenames

- Generate date prefix: `MMDDYY` (today's date)
- Use the company slug from Step 3 as the subfolder: `output/<company-slug>/`
- CV filename: `output/<company-slug>/MMDDYY-[role-slug].md` (e.g., `output/impossible-foods/022426-chief-of-staff.md`)
- Cheat sheet filename: `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- If a file at that path already exists, append `-v2`, `-v3` etc.

### Step 9: Save Output Files

Write both files:
1. **CV** → `output/<company-slug>/MMDDYY-[role-slug].md`
2. **Cheat sheet** → `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`

### Step 10: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive, fuzzy match — check if the company name from the JD appears as a substring in any active pipeline entry).
3. If found: update that entry's **CV Used** field to the CV output filename (just the filename, not full path).
4. If not found: note in the summary that the company isn't in the pipeline yet and suggest `/pipe add "[Company]" "[Role]"`.

### Step 11: Display Summary

```markdown
## CV Generated — [Role Title] at [Company]

**Format:** [US/UK/DACH/international] | **Market:** [market]
**CV saved:** `output/<company-slug>/MMDDYY-[role-slug].md`
**Cheat sheet:** `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`

### QC Summary (from Step 6b self-review)
- **Keyword coverage:** N/10 matched [list any unfixable gaps]
- **Claims verified:** N checked, N corrected
- **Issues fixed:** [list or "none"]
- **Language consistency:** clean / N items fixed

### ATS Keyword Coverage
| Keyword | Present? | Where |
|---------|----------|-------|
| [keyword 1] | ✅ | Professional Summary |
| [keyword 2] | ✅ | Project: [name] |
| [keyword 3] | ⚠️ | Gap — candidate lacks this skill |

**Coverage: N/10 keywords**

### Projects Selected
1. [Project name] — [one-line rationale]
2. ...

### Pipeline
[✅ CV Used field updated in pipeline for [Company]] OR [⚠️ [Company] not in pipeline — add with: `/pipe add "[Company]" "[Role]"`]

### Suggested Next Step
- Review output: `/review-cv output/<company-slug>/MMDDYY-[role-slug].md`
- When ready to apply: `/pipe update "[Company]" Applied`
- Before interview: `/prep-interview "[Company]"`
```

## Cheat Sheet Format

```markdown
# Interview Cheat Sheet — [Role] at [Company]
> Generated [date] from CV: output/<company-slug>/[cv-filename].md

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

## Tailoring Rules

- Never fabricate experience or inflate skill levels.
- **Project selection:** Choose 3–6 projects by relevance to the target role. If one project is marked type: `flagship`, it may deserve inclusion for depth — but it competes on relevance like any other project. If no single standout exists, choose 2–3 of equal weight that best demonstrate depth.
- **Project framing:** Adapt client descriptions to what's most impressive for the target role (e.g. parent company name for enterprise credibility, or technical characteristics for architecture roles). Full project context is in the project file.
- **Role-type emphasis:** Match what you lead with to what the job posting values most. Scan `data/project-index.md` tags and `data/skills.md` categories to find the candidate's strongest overlap with the role's focus area — then lead with those projects and skills. Don't assume which technologies or domains the candidate is strongest in; derive it from the data.
- For **entrepreneurial / startup roles:** include co-founded companies and side businesses from `data/companies.md`.
- For **consulting / advisory roles:** include relevant early-career experience, professional qualifications, and degree focus areas.
- Early-career experience (internships, student jobs, apprenticeships, bootcamps, first roles) is usually omitted unless specifically relevant to the target role.
- Keep resumes to 2–4 pages depending on role seniority.
- Daily rate and availability are only included if explicitly requested.

### Keyword Pragmatism

When the candidate's source data uses accurate but different terminology from a job posting's buzzwords, find honest bridge language rather than fabricating experience:
- Use qualifying adjectives that signal intent/direction without claiming full adoption (e.g. "-oriented", "-driven", "-based")
- Never invent patterns, tools, or practices the candidate hasn't actually used
- If a keyword gap is too wide to bridge honestly, omit it — don't stretch

## CV Quality Standards

### Keyword Discipline

- **Match the job posting's exact terminology.** If the posting says "CRM", the word "CRM" must appear in the CV. Don't rely on synonyms or related terms. ATS systems and speed-scanning recruiters won't make the connection.
- **When a technology has sub-products, always specify which one.** E.g. "Salesforce" could mean Sales Cloud, Service Cloud, or Marketing Cloud; "AWS" could mean any of 200+ services. Always label the specific product.
- **Extract the top 10 keywords from the job posting** before writing. After drafting, verify each appears at least once in the CV. Missing a primary keyword is a critical defect.

### Honest Scoping

- **Only count projects where the candidate worked *inside* the technology**, not just alongside it. Consuming a product's API from the outside is integration experience, not experience with that product. Make this distinction explicit in the CV.
- **Role titles must reflect how the candidate was engaged.** If hired in one role and later absorbed broader duties, frame it as progression (e.g., "Developer, expanding to Architecture & Team Lead"), not as the starting role.
- **Quantifiers must survive scrutiny.** "Across two projects" must mean two projects with genuine depth. "Three continents" must mean three production deployments, not one production + one pilot + one evaluation. When in doubt, use the more conservative framing.
- **Certification status must be current.** Check `data/certifications.md` for renewal status. Never list a certification as active if it is expired or renewal-pending without noting the status.

### Avoid Self-Sabotage

- **Never include weakness admissions.** Phrases like "currently expanding my X experience", "basic knowledge of Y", or "evaluated but not used in production" tell the reader what the candidate *can't* do. If the skill isn't strong enough to state positively, omit it entirely.
- **Explain concurrent engagements (if any overlap exists).** If any selected projects or roles overlap in time, the CV must acknowledge how concurrent work was managed. Without explanation, reviewers assume a timeline error or exaggeration. Add a brief explanation like "[Engagement A] maintained part-time alongside [Engagement B]" where applicable. Skip this check entirely if no timelines overlap.
- **Include team-fit signals.** Always include at least 2–3 references to collaboration across the CV: code reviews, knowledge transfer, team onboarding, training, sprint participation, coordination with client departments. Candidates who appear to only work solo raise red flags for team-based roles.
- **Apply `data/professional-identity.md` narrative reframes.** If the narrative patterns table shows weaker default framings alongside stronger coached versions, use the coached versions.

### Structural Consistency

- **Project headers must follow one pattern throughout.** Use either `Role — Description` or `Description — Role` for all project entries. Never mix. No prefixes like "Flagship:".
- **All bullets within a section must follow the same format.** If most bullets have bold labels (e.g., `**Architecture:**`), every bullet in that section must. No exceptions.
- **Every project and engagement must have dates.** No "second engagement" or "later period" without a time range. Even approximate dates (e.g., "Q2 2023") are better than nothing.
- **Include availability and location context** in the header, when the target market convention expects it or if the candidate is based in a different region from the role. Add a line like "Available: [date] · Remote ([timezone]) · Travel to [region] on request".
- **Sentence completeness:** Every bullet point must contain at least one verb. Sentence fragments without verbs are defects.

### Language Precision

- **No native-language calques.** Check `data/profile.md` for the candidate's native language, then watch for false friends and literal translations (e.g. German: "reconception" → redesign; French: "résumé" → summary; Spanish: "actually" → currently; Dutch: "eventually" → possibly).
- **British/American English consistency** — match the target market convention or the job posting's language. Don't mix within a single CV.
- **Tense must match engagement status.** Present tense for ongoing engagements, past tense for completed ones.
- **Use standard modern compound forms** (e.g. "subcontractors" not "sub-contractors", "freelancer" not "free-lancer").

## Future: Application Workflow Framework

> **TODO (later):** `/generate-cv`, `/review-cv`, `/review-cv-deep`, and `/cover-letter` share overlapping concerns — quality standards, output paths, and candidate data loading. If rules here need to be duplicated across those skills, extract a shared `framework/application-workflow.md` that all four reference. The trigger: if the same rule needs to be updated in more than one skill at the same time.

## Edge Cases

- **URL fetch fails**: Ask user to paste the JD text directly. Do not attempt to reconstruct the JD from partial content.
- **Too few projects**: If fewer than 3 relevant projects exist, use all available. Note in summary: "Only N projects available — consider adding more to `data/projects/`."
- **Missing profile.md**: Proceed without personal details. Omit compensation/availability from cheat sheet. Flag in summary.
- **Missing coached-answers.md**: Skip that section of cheat sheet silently.
- **Keywords not coverable**: If a keyword can't be added naturally to the CV (e.g. a technology the candidate genuinely doesn't have), flag it in the ATS coverage table as `⚠️ Gap — omit` and note it in the summary as a genuine skill gap.
- **Multiple roles at same company in pipeline**: Update the most recently active matching entry.
