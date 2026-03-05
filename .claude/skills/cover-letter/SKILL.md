---
name: cover-letter
description: Generate a research-backed Problem-Solution cover letter for a specific role — leads with their challenge, proves you've solved it, bridges to what you'd do for them — saved to output/ with date prefix
argument-hint: <job-url-or-jd> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(framework/*), Write(output/**), Write(data/job-pipeline.md), WebFetch, WebSearch
---

# Cover Letter — Problem-Solution Generator

Generate a concise, high-signal cover letter using the Problem-Solution format — the structure that outperforms traditional cover letters in recruiter response rates. The letter leads with the company's specific challenge, proves you've solved a similar problem, bridges to what you'd do for them, and closes with a direct ask. 250-350 words. Saves to `output/` with a date-prefixed filename.

**Core philosophy:** The resume covers the past. The cover letter addresses the future. Never summarize the resume — add what the CV can't: the *why*, the connective tissue between experiences, and a problem-solving orientation that shows you've done your homework.

## Arguments

- **`<job-url-or-jd>`** (required) — URL to the job posting, or pasted job description text
- **`[context]`** (optional) — e.g. `"emphasize the Amae work"`, `"more informal tone"`, `"keep to 200 words"`, `"mention coffee chat with Alex"`

Examples:
- `/cover-letter https://company.com/jobs/cos` — fetch posting, generate letter
- `/cover-letter "Chief of Staff, Ripple Foods..." "emphasize food/FMCG"` — pasted JD with context
- `/cover-letter https://jobs.lever.co/amae/xyz "warm tone, mention coffee chat with Alex"`

If no arguments provided:
```
Usage: /cover-letter <job-url-or-jd> [context]

Examples:
  /cover-letter https://company.com/jobs/role
  /cover-letter "Job description text..." "emphasize operations experience"
```

## Instructions

### Step 1: Parse Arguments & Fetch JD

Parse `$ARGUMENTS`:
1. If the first token contains `http` or a recognisable domain, treat it as a URL. Use WebFetch to retrieve the job posting. If fetch fails, ask user to paste the JD directly.
2. Otherwise treat the full first argument as pasted JD text.
3. Extract any quoted or trailing string as the `[context]` override.

### Step 2: Load Candidate Context (parallel)

Read all cover-letter-relevant files listed in `framework/application-workflow.md` § Candidate Context Loading (the "Cover Letter" column). Skip any that don't exist, never fail.

Derive the company slug from Step 1 if already parsed; otherwise read `data/company-notes/<company-slug>.md` after Step 3.

### Step 3: Analyze the Role & Research the Company

From the JD text, extract:

- **Company name** and slug (lowercase-hyphenated)
- **Role title** and role slug
- **The company's core challenge** — what specific problem is this role hired to solve? Read the JD carefully: look for language about growth, scaling, transformation, launches, or pain points. If the JD is vague, use the company dossier or a quick WebSearch to identify a concrete challenge.
- **Top 3 required qualities** — the most important attributes the role calls for (beyond just skills)
- **3-5 ATS keywords** — the most important terms from the JD to weave naturally into the letter
- **Tone signals** from the JD — formal/casual, startup energy vs. enterprise, mission-forward vs. metrics-forward
- **Any personal connection signals** — check `data/networking.md` for a contact at this company

Now read:
- `data/company-notes/<company-slug>.md` (if not already read in Step 2)
- Company dossier at `output/<company-slug>/<company-slug>.md` — for recent news, leadership context, and company-specific talking points. Run the **Company Dossier Staleness Check** from `framework/application-workflow.md` § Company Dossier Staleness Check

### Step 4: Select 1-2 Proof Points

This is the most important step. You need proof points that **mirror the company's challenge** — not just your best achievements.

1. Read `data/project-index.md` — identify 1-2 projects where you solved a problem *similar* to the company's core challenge from Step 3.
2. Read those project files from `data/projects/`.
3. For each, extract:
   - The **problem** you faced (analogous to theirs)
   - The **specific action** you took
   - The **quantified result**
4. **Never use files from `data/project-background/`.**
5. **Choose proof points that don't duplicate your top CV bullets.** Go deeper on one story, or connect dots the CV can't. The cover letter should complement the CV, not restate it.

### Step 5: Draft the Cover Letter

Write four sections following the **Problem-Solution** structure. Total target: **250-350 words**.

**Section 1 — The Hook (2-3 sentences)**

Open with something specific to this company. Options (pick the strongest for this situation):
- **Company challenge opener:** Name a specific challenge the company faces (from JD language, dossier, or news). Show you understand their world.
- **Recent event opener:** Reference something the company recently did (funding round, product launch, expansion, leadership hire).
- **Personal connection opener:** If you have a contact at this company, lead with that warmth — "After speaking with [Name] about [topic]..."

Rules:
- Name the company in the first sentence. Always.
- The test: could another applicant send this same opener to a different company? If yes, rewrite.
- Never open with: "I'm writing to apply for...", "I've always been passionate about...", "I hope this finds you well", "I'm a [trait] professional with X years..."

**Section 2 — The Proof (3-5 sentences)**

Present 1-2 specific examples of how you've solved a similar problem. This is show-don't-tell.

- Lead with the problem you faced, then what you did, then the result.
- Use concrete numbers from project files.
- Frame each example as an analogy to their challenge: "When [Company/Project] faced [similar problem], I [action] which resulted in [outcome]."
- Do NOT reproduce CV bullet points verbatim — synthesize into narrative sentences.
- If using `data/professional-identity.md` reframes, apply the coached versions here.

**Section 3 — The Bridge (2-3 sentences)**

This is the "future" section — what you'd do for them. The CV handles the past; this section handles what comes next.

- Connect your capability to their specific needs: "At [Company], I'd apply this approach to [their specific challenge]."
- Reference 1-2 concrete priorities visible from the JD or your research.
- Position yourself as a thought partner, not a task executor (especially for senior/leadership roles).

**Section 4 — The Close (1-2 sentences)**

Confident, specific, brief.

- Express genuine enthusiasm tied to something specific about this company (not "I'm excited about the opportunity").
- Make a direct ask that advances the conversation: "I'd welcome the chance to discuss how [specific approach] maps to [Company]'s [specific challenge]."
- No "I look forward to hearing from you" — that's passive. Propose something.

### Step 6: Quality Gates (mandatory — fix in place before saving)

Run every check. Fix issues directly in the draft — don't just flag them.

**1. Uniqueness test:**
- Read each section. Could another candidate have written it for a different company? If any section fails this test, rewrite with more specificity.

**2. Resume separation test:**
- Does the cover letter add information the CV can't provide (the "why", the connective tissue, the future vision)? If it reads like a CV summary, rewrite section 2 with narrative and section 3 with forward-looking content.

**3. Problem-Solution integrity:**
- Does section 1 name a specific company challenge?
- Does section 2 show you've solved a similar problem with evidence?
- Does section 3 bridge to what you'd do for them?
- If any link in the chain is weak, strengthen it.

**4. Length check:**
- Target: 250-350 words. Hard ceiling: 400 words.
- If over 350: trim section 2 to one proof point, tighten section 3 to 2 sentences.

**5. Anti-pattern scan:**
- No hedging: "I believe I could", "I think I might", "hoping to", "looking to expand my skills" — remove all.
- No filler openers: "I am writing to apply for", "Please find attached", "I have always been passionate about" — rewrite.
- No em dashes — use commas or restructure.
- No trait claims without evidence: "I'm a strategic thinker" means nothing alone. Either back it up with a specific example or cut it.
- No generic enthusiasm: "I'm excited about the opportunity" — replace with specific enthusiasm about a concrete aspect of the company.

**6. Tone match:**
- Does the letter's energy match the JD's tone? Startup JD = direct and energetic. Enterprise JD = polished and measured. Mission-forward JD = warm but precise.

**7. ATS keyword weaving:**
- Verify 3-5 key terms from the JD appear naturally in the letter body. Don't force them — the CV carries the primary ATS burden. But reinforcing keywords here strengthens the overall application.

**8. Language consistency:**
- Spelling variant matches the JD (US vs. UK English).
- Company name spelled correctly and appears at least twice.
- Apply Nick's Voice rules from `framework/style-guidelines.md`.

### Step 7: Apply Context Overrides

If `[context]` was provided, apply now:
- `"emphasize [project/topic]"` → ensure that project or topic appears in section 2
- `"more informal tone"` → lighten language, use contractions
- `"keep to 200 words"` → trim to one proof point, tighten all sections
- `"mention coffee chat with [name]"` → integrate into section 1 as personal connection opener

### Step 8: Determine Output Filename

- Date prefix: `MMDDYY` (today's date)
- Company subfolder: `output/<company-slug>/`
- Filename: `output/<company-slug>/MMDDYY-cover-letter.md`
- If file exists: append `-v2`, `-v3`

### Step 9: Save Output

Write the cover letter to `output/<company-slug>/MMDDYY-cover-letter.md`.

File structure:

```markdown
# Cover Letter — [Role Title] at [Company]
> Generated [date] | [word count] words | Problem-Solution format

---

[Candidate Name]
[City, State] | [Email] | [LinkedIn or phone if in profile.md]

[Date — today's date spelled out, e.g. March 4, 2026]

Hiring Team, [Company]

[Section 1 — Hook: company challenge / recent event / personal connection]

[Section 2 — Proof: 1-2 similar problems you've solved]

[Section 3 — Bridge: what you'd do for them]

[Section 4 — Close: specific ask]

[First Name]

---
_Format: Problem-Solution | Sources: [list of project files used]_
```

If profile name/contact is unavailable, leave those fields as `[Your Name]` / `[Your Contact]`.

### Step 10: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive substring match).
3. If found: update the **CV Used** field to include the cover letter filename (append to existing value if a CV is already listed — separate with `, `).
4. If not found: note in summary.

### Step 11: Display Summary

```
## Cover Letter Generated — [Role Title] at [Company]

**Saved:** `output/<company-slug>/MMDDYY-cover-letter.md`
**Word count:** [N] words
**Format:** Problem-Solution
**Tone:** [matched to JD / adjusted per context]

### Structure
- Section 1 (Hook): [angle used — company challenge / recent event / personal connection]
- Section 2 (Proof): [project(s) used — one-line summary of the analogy]
- Section 3 (Bridge): [what you'd do — one-line]
- Section 4 (Close): [ask summary]

### Quality Gates
- Uniqueness test: pass / [N sections rewritten]
- Resume separation: pass / rewritten
- Problem-Solution chain: intact
- Length: [N] words — [within target / trimmed from N]
- Anti-patterns: clean / [N items fixed]
- ATS keywords woven: [N]/[N target]
- Language: [US/UK] consistent

### Pipeline
[✅ Cover letter noted in pipeline for [Company]] OR [⚠️ [Company] not in pipeline — add with: `/pipe add "[Company]" "[Role]"`]

### Suggested next step
- Generate a matching CV: `/generate-cv [job-url-or-jd]`
- Or prep for the interview: `/prep-interview "[Company]"`
```

## Edge Cases

- **URL fetch fails**: Ask user to paste JD. Do not reconstruct from partial content.
- **No professional-identity.md**: Proceed but note the letter will be less personalized. Recommend running `/extract-identity`.
- **Missing profile.md contact details**: Leave name/contact fields as placeholders in output.
- **No company dossier or recent news available**: Use JD language alone to identify the challenge. The Problem-Solution format still works — the "problem" comes from the JD's description of the role's mandate.
- **Cover letter > 400 words after quality gates**: Force-trim to 350 by reducing section 2 to one proof point and section 3 to 2 sentences.
- **Personal connection found in networking.md**: Use the personal connection opener in section 1 — "After speaking with [First Name]..." or "Through my conversation with [First Name]...". Use first name only unless instructed otherwise.
- **Context says "no cover letter header"**: Write the body sections only, no salutation/sign-off.
- **Multiple proof points equally strong**: Pick the one most analogous to the company's challenge, not the most impressive in isolation. Relevance beats magnitude.
