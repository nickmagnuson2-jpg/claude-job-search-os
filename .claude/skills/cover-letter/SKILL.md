---
name: cover-letter
description: Generate a tailored 3-paragraph cover letter for a specific role — hook, value bridge, close with ask — saved to output/ with date prefix
argument-hint: <job-url-or-jd> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(framework/*), Write(output/*), Edit(data/job-pipeline.md), WebFetch, WebSearch
---

# Cover Letter — Tailored 3-Paragraph Generator

Generate a concise, high-signal cover letter for a specific role. Three paragraphs: hook on the company's mission, a value bridge backed by specific work, and a direct close with an ask. Follows style-guidelines.md for tone. Saves to `output/` with a date-prefixed filename.

## Arguments

- **`<job-url-or-jd>`** (required) — URL to the job posting, or pasted job description text
- **`[context]`** (optional) — e.g. `"emphasize the Amae work"`, `"more informal tone"`, `"keep to 200 words"`

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

Read the following in parallel — skip any that don't exist, never fail:

1. `data/profile.md` — name, contact, tone signals
2. `data/goals.md` — search thesis, role type preferences
3. `data/professional-identity.md` — strengths, narrative patterns, reframes, values
4. `data/project-index.md` — lightweight project index for fast relevance scanning
5. `framework/style-guidelines.md` — tone conventions
6. `data/networking.md` — check if any contact at this company exists (informs hook personalisation)

If `data/profile.md` is missing, warn and proceed with available data.

### Step 3: Analyze the Role

From the JD text, extract:

- **Company name** and slug (lowercase-hyphenated)
- **Role title** and role slug
- **Mission / core challenge** — what problem does this company solve? What's the role's primary mandate?
- **Top 3 required qualities** — the most important attributes the role calls for (beyond just skills)
- **Tone signals** from the JD — formal/casual, startup energy vs. enterprise, mission-forward vs. metrics-forward
- **Any personal connection signals** — check `data/networking.md` for a contact at this company

### Step 4: Select 2–3 Supporting Proof Points

1. Read `data/project-index.md` — identify 2–3 projects most relevant to the role's top 3 required qualities.
2. Read those project files from `data/projects/`.
3. For each, extract the single strongest evidence of fit — one concrete outcome or action, not a responsibility list.
4. **Never use files from `data/project-background/`.**

### Step 5: Draft the Cover Letter

Write three paragraphs. Adhere to these structural rules:

**Paragraph 1 — Hook (3–5 sentences)**
- Open with the company's mission or the role's core challenge — not "I am writing to apply for…"
- Name the company in the first sentence.
- If there's a personal connection (known contact, referral, coffee chat already happened), reference it briefly in sentence 2.
- Close the paragraph with a direct claim: "That's exactly the kind of work I'm built for" / "I'd bring X and Y to that challenge" — something that pulls the reader forward.
- Tone: match the JD's energy. Startup = direct and energetic. Mission-forward = warm but precise.

**Paragraph 2 — Value Bridge (4–6 sentences)**
- Translate 2–3 experiences into proof of fit for the role's top required qualities.
- Lead each evidence point with what you DID and what happened as a result — not a job description.
- Use concrete numbers from project files where they exist.
- Tie each proof point back to the specific challenge this role faces — don't just list achievements in a vacuum.
- Do NOT reproduce CV bullet points verbatim — synthesize into sentences that tell a story.

**Paragraph 3 — Close with Ask (2–3 sentences)**
- Express specific enthusiasm for this company (not generic "I'm excited about the opportunity").
- Make a direct ask: "I'd welcome the chance to talk through how my background maps to what you're building."
- Keep it one sentence for the ask — don't over-explain.

**Quality gates — apply before saving:**
- Total length: 200–300 words preferred. Flag if over 350.
- No hedging language: "I believe I could", "I think I might", "hoping to", "looking to expand my skills" — remove all of these.
- No filler openers: "I am writing to apply for", "Please find attached my CV", "I have always been passionate about" — rewrite if found.
- Spell-check variant consistency (US vs. UK — match the JD's language).
- Check that the company name appears at least twice and is spelled correctly.

### Step 6: Apply Context Overrides

If `[context]` was provided, apply the instructions now:
- `"emphasize [project/topic]"` → ensure that project or topic appears in paragraph 2
- `"more informal tone"` → lighten the language, use contractions
- `"keep to 200 words"` → trim paragraph 2 to 2 evidence points
- `"mention coffee chat with [name]"` → add to paragraph 1's personal connection sentence

### Step 7: Determine Output Filename

- Date prefix: `MMDDYY` (today's date)
- Filename: `output/MMDDYY-cover-letter-[company-slug].md`
- If file exists: append `-v2`, `-v3`

### Step 8: Save Output

Write the cover letter to `output/MMDDYY-cover-letter-[company-slug].md`.

File structure:

```markdown
# Cover Letter — [Role Title] at [Company]
> Generated [date] | [word count] words

---

[Candidate Name]
[City, State] | [Email] | [LinkedIn or phone if in profile.md]

[Date — today's date spelled out, e.g. February 25, 2026]

Hiring Team, [Company]

[Paragraph 1 — Hook]

[Paragraph 2 — Value Bridge]

[Paragraph 3 — Close with Ask]

[First Name]

---
_Sources: [list of project files used]_
```

If profile name/contact is unavailable, leave those fields as `[Your Name]` / `[Your Contact]`.

### Step 9: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive substring match).
3. If found: update the **CV Used** field to include the cover letter filename (append to existing value if a CV is already listed — separate with `, `).
4. If not found: note in summary.

### Step 10: Display Summary

```
## Cover Letter Generated — [Role Title] at [Company]

**Saved:** `output/MMDDYY-cover-letter-[company-slug].md`
**Word count:** [N] words
**Tone:** [match / adjusted for JD energy]

### Structure used
- Para 1 (Hook): [one-line summary of hook angle]
- Para 2 (Value Bridge): [projects used — name + one proof point each]
- Para 3 (Close): [one-line summary]

### Quality gates
- Hedging language: clean / N items removed
- Filler openers: clean / rewritten
- Length: [N words — within target / over — consider trimming para 2]
- Language variant: [US / UK] consistent

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
- **Cover letter > 350 words after quality gates**: Flag in summary with a suggestion to trim paragraph 2 to 2 evidence points.
- **Personal connection found in networking.md**: Mention in the hook paragraph — "After speaking with [Name]..." or "Through my conversation with [Name]...". Use the contact's first name only unless the candidate instructed otherwise.
- **Context says "no cover letter header"**: Write the body paragraphs only, no salutation/sign-off.
