---
name: apply
description: Generate tailored CV + cover letter and add to pipeline in one command — the complete apply bundle
argument-hint: "<job-url-or-jd> [context]"
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(framework/*), Glob(plugins/*), Write(output/**), Write(data/job-pipeline.md), WebFetch, WebSearch
---

# Apply — One-Command Application Bundle

Generate a tailored CV, companion cheat sheet, and cover letter for a specific role — then add (or update) the company in the pipeline. Replaces the 3-command flow of `/generate-cv` + `/cover-letter` + `/pipe add`.

## Arguments

- **`<job-url-or-jd>`** (required) — URL to the job posting, or pasted job description text
- **`[context]`** (optional) — additional instructions, e.g. `"emphasize McKinsey"`, `"US format, 1 page"`, `"warm tone, mention coffee chat with Alex"`

Examples:
- `/apply https://jobs.impossible.com/cos-role` — full bundle from URL
- `/apply "Chief of Staff, Ripple Foods..." "emphasize food/FMCG experience"` — pasted JD with context
- `/apply https://jobs.lever.co/amae/xyz "warm tone, mention coffee chat with Alex"`

If no arguments provided:
```
Usage: /apply <job-url-or-jd> [context]

Examples:
  /apply https://company.com/jobs/role
  /apply "Job description text..." "emphasize operations experience"
  /apply https://company.com/job "US format, keep to 1 page"
```

## Instructions

### Step 1: Parse Arguments & Fetch JD

Parse `$ARGUMENTS`:
1. If the first token contains `http` or a recognisable domain, treat it as a URL. Use WebFetch to retrieve the job posting. If fetch fails, ask user to paste the JD directly.
2. Otherwise treat the full first argument (before any quoted context string) as pasted JD text.
3. Extract any quoted or trailing string as the `[context]` override.

### Step 2: Profile Guard

Check that both `data/profile.md` and `data/goals.md` exist and contain real content (not just TODO placeholders):
- If `data/profile.md` is missing or has only TODOs: "⚠️ `data/profile.md` is missing or incomplete. Run `/import-cv` first."
- If `data/goals.md` is missing or has only TODOs: "⚠️ `data/goals.md` is missing or incomplete. Run `/setup-goals` first."
- Do not proceed until both files have real content.

### Step 3: Load Candidate Context (parallel)

Read the following in parallel — skip any that don't exist, never fail:

1. `data/profile.md`
2. `data/professional-identity.md`
3. `data/goals.md`
4. `data/education.md`
5. `data/skills.md`
6. `data/certifications.md`
7. `data/project-index.md`
8. `data/companies.md`
9. `framework/style-guidelines.md`
10. `coaching/coached-answers.md`
11. `coaching/anti-pattern-tracker.md`
12. `framework/answering-strategies/anti-patterns.md`

Check for plugins: if `data/plugin-activation.md` exists, read it. Glob `plugins/*/plugin.md` for any with `scope: cv` or `scope: all`.

### Step 4: Analyse the Role

From the job posting text, extract:

- **Company name** and generate a slug (lowercase, hyphens — e.g. `impossible-foods`)
- **Role title** and a role slug (e.g. `chief-of-staff`)
- **Required skills** (must-haves)
- **Nice-to-have skills**
- **Seniority level**
- **Top 10 ATS keywords** — the most important terms for ATS passage
- **Market** — US / UK / DACH / international (default US if unclear)
- **Industry** — infer from company and role
- **Mission / core challenge** — what problem does this company solve? What's the role's primary mandate?
- **Top 3 required qualities** — the most important attributes beyond just skills
- **Tone signals** from the JD

Now read:
- `data/company-notes/<company-slug>.md` — personal notes (skip if doesn't exist)
- `data/networking.md` — check for any contact at this company (informs cover letter hook)
- Company dossier at `output/<company-slug>/<company-slug>.md` — if found, check `Last updated:` date:
  - If >30 days old: display inline warning:
    ⚠️ Company dossier is [N] days old (last updated YYYY-MM-DD). Consider refreshing: `/research-company "[Company]"`
  - Then continue — never block

### Step 5: Select Projects

1. Read `data/project-index.md` — scan all entries for relevance to the role's required skills, industry, seniority level, and company type.
2. Select **3–6 most relevant projects**. Criteria: skill overlap with required skills > industry/domain match > seniority match > recency.
3. Read the full project files for each selected project from `data/projects/`.
4. **NEVER read or use files from `data/project-background/`.**
5. Note the rationale for each selected project (used in the cheat sheet).

### Step 6: Generate the CV

Apply all Tailoring Rules and CV Quality Standards from `framework/style-guidelines.md`:

- **Professional summary** — 3–4 lines tailored to this specific role. Opens with a hook tied to the company's mission or the role's core challenge.
- **Project ordering** — by relevance to this role, not chronologically. Most relevant project first.
- **Skills section** — emphasise skills that appear in the JD. Lead with required skills the candidate has.
- **ATS keyword coverage** — verify all 10 extracted keywords appear at least once in the CV text.
- **Format** — based on market:
  - US: 1–2 pages, no photo, no DOB, concise bullet points
  - UK: 2 pages, CV (not resume), more narrative allowed
  - DACH: Photo optional, more formal structure
- **Achievements over responsibilities** — lead bullets with quantified outcomes where possible.

### Step 6b: Inline CV Quality Review (mandatory — do NOT skip)

Run these checks and fix issues in place — never just flag:

1. **Keyword coverage** — verify each of the 10 ATS keywords appears at least once.
2. **Product specificity** — check "Salesforce" → which cloud? "AWS" → which services?
3. **Claim integrity** — cross-reference quantified claims against project date ranges. Fix anything that doesn't match.
4. **No weakness admissions** — remove "currently expanding", "basic knowledge of", "exposure to", "learning", etc.
5. **Concurrent engagement explanation** — if any projects overlap in time, confirm the CV explains how.
6. **Team-fit signals** — confirm at least 2–3 collaboration references exist.
7. **Structural consistency** — all project headers follow one format, all bullets within a section follow the same format, every project has dates.
8. **Language and tense** — British/American English consistency matching the JD; present tense for current roles, past for completed.

Record a QC summary for the final output.

### Step 7: Generate the Cover Letter

Write three paragraphs following this structure:

**Paragraph 1 — Hook (3–5 sentences)**
- Open with the company's mission or the role's core challenge — NOT "I am writing to apply for…"
- Name the company in the first sentence.
- If there's a personal connection in `data/networking.md`, reference it briefly in sentence 2.
- Close with a direct claim that pulls the reader forward.
- Tone: match the JD's energy.

**Paragraph 2 — Value Bridge (4–6 sentences)**
- Translate 2–3 experiences into proof of fit for the role's top required qualities.
- Lead each evidence point with what was done and the result — not a job description.
- Use concrete numbers from project files where they exist.
- Tie each proof point back to the specific challenge this role faces.
- Do NOT reproduce CV bullet points verbatim.

**Paragraph 3 — Close with Ask (2–3 sentences)**
- Express specific enthusiasm for this company (not generic "I'm excited about the opportunity").
- Make a direct ask: one sentence, specific.

**Cover letter quality gates:**
- Total length: 200–300 words preferred. Flag if over 350.
- No hedging language: "I believe I could", "I think I might", "hoping to" — remove all.
- No filler openers: "I am writing to apply for", "Please find attached" — rewrite if found.
- Company name appears at least twice and is spelled correctly.
- Spell-check variant consistency (match the JD's language).

Apply any `[context]` overrides: `"emphasize [project]"`, `"more informal tone"`, `"keep to 200 words"`, `"mention coffee chat with [name]"`, etc.

### Step 8: Generate Companion Cheat Sheet

Generate a pre-interview cheat sheet for this specific role:

1. **15-second recruiter pitch** — "[Identity hook] with [key credential]. Most relevant: [2 projects]. Looking for [role type] at [company type]. Interested in [Company] because [specific researched reason]."
2. **Must-have requirements coverage** — for each required skill from the JD: 2–3 specific bullets from selected projects.
3. **Compensation, availability, start date** — from `data/profile.md`.
4. **Coached answers to cross-reference** — from `coaching/coached-answers.md` if exists. Flag directly applicable answers.
5. **Do NOT say warnings** — top 5–7 relevant anti-patterns from `coaching/anti-pattern-tracker.md` and `framework/answering-strategies/anti-patterns.md`.
6. **Keyword cheat** — all 10 ATS keywords with one-line reminder of which project to cite.

### Step 9: Determine Output Filenames

- Date prefix: `MMDDYY` (today's date)
- Company subfolder: `output/<company-slug>/`
- CV: `output/<company-slug>/MMDDYY-[role-slug].md`
- Cheat sheet: `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- Cover letter: `output/<company-slug>/MMDDYY-cover-letter.md`
- If any file already exists at that path: append `-v2`, `-v3` etc.

### Step 10: Save Output Files

Write all three files:
1. **CV** → `output/<company-slug>/MMDDYY-[role-slug].md`
2. **Cheat sheet** → `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
3. **Cover letter** → `output/<company-slug>/MMDDYY-cover-letter.md`

### Step 11: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive substring match).
3. **If found:** Update the entry:
   - Set **CV Used** to the CV filename (just the filename, not full path)
   - Append the cover letter filename to **CV Used** (separate with `, `)
   - If the stage is `Researching`: update it to `Applied`
4. **If not found:** Add a new row to the Active section:
   - Stage: `Applied`
   - Date Added: today
   - Date Updated: today
   - CV Used: [cv filename]
   - Notes: `Added by /apply on [today's date]`
5. Write `data/job-pipeline.md`.

### Step 12: Display Summary

```markdown
## Application Bundle Ready — [Role Title] at [Company]

### Files Saved
- **CV:** `output/<company-slug>/MMDDYY-[role-slug].md`
- **Cheat sheet:** `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- **Cover letter:** `output/<company-slug>/MMDDYY-cover-letter.md`

### CV Quality Summary
- **Keyword coverage:** N/10 matched [list any unfixable gaps]
- **Claims verified:** N checked, N corrected
- **Issues fixed:** [list or "none"]
- **Language consistency:** clean / N items fixed

### Cover Letter
- **Word count:** N words [within target / over — consider trimming]
- **Hook angle:** [one-line summary]
- **Proof points:** [projects used]

### Pipeline
[✅ Pipeline updated — [Company] stage: Applied, CV Used set] OR [✅ New pipeline entry added — [Company] / [Role] / Applied]

### Projects Selected
1. [Project name] — [one-line rationale]
2. ...

### Suggested Next Step
- Review the CV: `/review-cv output/<company-slug>/MMDDYY-[role-slug].md`
- When ready to interview: `/prep-interview "[Company]"`
```

## Edge Cases

- **URL fetch fails:** Ask user to paste the JD directly. Do not attempt to reconstruct.
- **Too few projects:** If fewer than 3 relevant projects exist, use all available. Note in summary.
- **Missing profile.md:** Proceed without personal details. Leave name/contact as placeholders in cover letter. Flag in summary.
- **Cover letter > 350 words:** Flag in summary with suggestion to trim paragraph 2 to 2 evidence points.
- **Personal connection in networking.md:** Mention in the cover letter hook paragraph — "After speaking with [First Name]..."
- **Existing pipeline entry already at Applied or later:** Do not regress the stage. Only update CV Used field.
- **Keyword not coverable:** If a keyword can't be added naturally (candidate lacks the skill), flag as `⚠️ Gap — omit` in the ATS coverage table.
- **Company dossier stale (>30 days):** Surface inline warning (see Step 4) but continue.
