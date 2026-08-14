---
name: apply
description: Generate tailored CV + cover letter and add to pipeline in one command — the complete apply bundle
argument-hint: "<job-url-or-jd> [context]"
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(framework/*), Glob(plugins/*), Write(output/**), Write(data/job-pipeline.md), mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebFetch, WebSearch, Bash(python3 tools/projects_to_yaml.py:*), Bash(python3 tools/cv_merge_theme.py:*), Bash(~/.local/bin/rendercv render:*), Bash(rendercv render:*), Bash(rm -rf output/**/rendercv_output)
---

# Apply — One-Command Application Bundle

Generate a tailored CV, companion cheat sheet, and cover letter for a specific role — then add (or update) the company in the pipeline. Replaces the 3-command flow of `/generate-cv` + `/cover-letter` + `/pipe add`.

## Arguments

- **`<job-url-or-jd>`** (required) — URL to the job posting, or pasted job description text
- **`[context]`** (optional) — additional instructions, e.g. `"emphasize McKinsey"`, `"US format, 1 page"`, `"warm tone, mention coffee chat with Jordan"` (Alex = an illustrative contact name)
- **`--deep-review`** or **`--deep`** (optional flag) — after generating the bundle, automatically run `/review-cv-deep` against the saved CV. Produces a six-perspective audit (Recruiter / Hiring Manager / Competitor / Skeptic / Copy Editor / Source Auditor) saved to `output/<slug>/MMDDYY-magnuson-DEEP-REVIEW.md`. Use for high-stakes applications where the ~5-minute cost is justified.

Examples:
- `/apply https://jobs.impossible.com/cos-role` — full bundle from URL
- `/apply "Chief of Staff, Northwind..." "emphasize food/FMCG experience"` — pasted JD with context
- `/apply https://jobs.lever.co/acme/xyz "warm tone, mention coffee chat with Jordan"`

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
1. **Detect `--deep-review` or `--deep` flag** anywhere in the argument string. If present, set `deep_review = true` and strip the flag from the remaining arguments before further parsing.
2. If the first remaining token contains `http` or a recognisable domain, treat it as a URL. Use WebFetch to retrieve the job posting. If fetch fails, ask user to paste the JD directly.
3. Otherwise treat the full first argument (before any quoted context string) as pasted JD text.
4. Extract any quoted or trailing string as the `[context]` override.

### Step 2: Profile Guard

Check that both `data/profile.md` and `data/goals.md` exist and contain real content (not just TODO placeholders):
- If `data/profile.md` is missing or has only TODOs: "⚠️ `data/profile.md` is missing or incomplete. Run `/import-cv` first."
- If `data/goals.md` is missing or has only TODOs: "⚠️ `data/goals.md` is missing or incomplete. Run `/setup-goals` first."
- Do not proceed until both files have real content.

### Step 3: Load Candidate Context (parallel)

Read all files listed in `framework/application-workflow.md` § Candidate Context Loading (both "CV" and "Cover Letter" columns — `/apply` needs the superset). Skip any that don't exist, never fail.

Check for plugins per the § Candidate Context Loading plugin instructions.

### Step 4: Analyse the Role

From the job posting text, extract:

- **Company name** and generate a slug (lowercase, hyphens — e.g. `beacon`)
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
- Company dossier at `output/<company-slug>/<company-slug>.md` — run the **Company Dossier Staleness Check** from `framework/application-workflow.md` § Company Dossier Staleness Check

### Step 5: Select Projects & Generate Factual Stubs

1. Read `data/project-index.md` — scan all entries for relevance to the role's required skills, industry, seniority level, and company type.
2. Select **3–6 most relevant projects**. Criteria: skill overlap with required skills > industry/domain match > seniority match > recency.
3. **Side-project trigger:** if the JD mentions technical background, engineering, CS/EE/ML, applied AI, or "builds products," scan side-projects (type: `side-project`) in the project-index regardless of seniority and include the strongest one in a compact Selected Projects section on the CV.
4. Read the full project files for each selected project from `data/projects/`.
4b. **Reconcile against source corrections (mandatory, before drafting any prose):**
   ```bash
   PYTHONIOENCODING=utf-8 python3 tools/source_corrections.py data/projects/<slug>.md [more...]
   ```
   Corrections live in HTML comments pinned to the bullet they correct, which makes them invisible exactly when you paraphrase that bullet. Draft from the **corrected** wording, and note that one live correction says "keep this wording, but the underlying fact is X" — that one means the bullet stands and the cheat sheet carries the real fact. Fired twice (2026-07-08 CVs, 2026-08-07 application answer), both caught only by after-the-fact review agents. See `memory/feedback_cv_em_dash_and_source_verb_regression.md`.
5. **NEVER read or use files from `data/project-background/`.**
6. Note the rationale for each selected project (used in the cheat sheet).
7. **Generate factual EXPERIENCE stubs.** Run:
   ```bash
   PYTHONIOENCODING=utf-8 python3 tools/projects_to_yaml.py --include <slug1>,<slug2>,... --out /tmp/cv-experience-stubs.yaml --json
   ```
   Where `<slug>` matches the filename (no `.md`) of each selected project, in order most-recent-first. The resulting YAML is the **source-of-truth baseline** for company, position, date, location, and highlights. Per lesson #54, every quantified claim on the final CV must trace to this stub or to `data/projects/<slug>.md`. Tailored phrasing is fine; invented numbers are not.

### Step 6: Generate the CV YAML

The CV is emitted as a **RenderCV YAML** in the same shape as the canonical reference `output/example-ventures/042826-cos-example.yaml`. The design block is NOT included — it composes in at Step 10a. See `/generate-cv` SKILL.md § Step 6 for the full YAML template.

Apply all **Tailoring Rules** and **CV Quality Standards** from `framework/application-workflow.md`:

- **Professional summary** — 3–4 lines tailored to this role. Hook tied to the company's mission or the role's core challenge.
- **Experience entries** — start from the Step 5 stubs verbatim. Tailor bullet PHRASING for JD relevance. Do NOT invent quantified claims not present in the stubs or `data/projects/<slug>.md` (lesson #54).
- **Experience ordering** — strict reverse-chronological. Do not reorder by relevance.
- **Skills section** — emphasise skills appearing in the JD. Every Skills line item must be substantively evidenced by an experience bullet; hedge words (`-adjacent`, `partnership` without a verb, `exposure`, `familiarity`) are forbidden (lessons #31, #53).
- **ATS keyword coverage** — all 10 extracted keywords must appear at least once.
- **Achievements over responsibilities** — quantified outcomes from stubs, not invented.
- **No content from `data/project-background/`.**
- **No em dashes** (CLAUDE.md hard rule) — but EN DASH (`–`) is used in date ranges per the reference YAML.

### Step 6b: Inline CV Quality Review (mandatory — do NOT skip)

Run all 18 checks from `framework/application-workflow.md` § CV Quality Checks. Fix issues in place — never just flag.

After all fixes, record a QC summary using the template in `framework/application-workflow.md` § QC Summary Template. The template requires per-check line citations — a bare "clean" without evidence is not acceptable.

### Step 7: Generate the Cover Letter (Problem-Solution Format)

Use the **Problem-Solution** structure — leads with their challenge, proves you've solved it, bridges to what you'd do for them. Total target: **250-350 words**. The resume covers the past; the cover letter addresses the future. Never summarize the CV.

**Section 1 — The Hook (2-3 sentences)**
- Open with something specific to this company: a challenge they face (from JD language, dossier, or news), a recent event, or a personal connection from `data/networking.md`.
- Name the company in the first sentence. Always.
- The uniqueness test: could another applicant send this same opener to a different company? If yes, rewrite.
- Never open with: "I'm writing to apply for...", "I've always been passionate about...", "I'm a [trait] professional with X years..."

**Section 2 — The Proof (3-5 sentences)**
- Present 1-2 specific examples of how you've solved a problem similar to the company's challenge.
- Lead with the problem you faced, then action, then quantified result.
- Frame as analogy: "When [Company/Project] faced [similar problem], I [action] which resulted in [outcome]."
- Do NOT reproduce CV bullet points verbatim — synthesize into narrative.
- Choose proof points that complement (not duplicate) the CV's top bullets.

**Section 3 — The Bridge (2-3 sentences)**
- Connect your capability to their specific needs: "At [Company], I'd apply this approach to [their specific challenge]."
- Reference 1-2 concrete priorities from the JD or research.
- Position as thought partner, not task executor (especially for senior roles).

**Section 4 — The Close (1-2 sentences)**
- Express genuine enthusiasm tied to something specific about this company.
- Direct ask that advances the conversation: "I'd welcome the chance to discuss how [specific approach] maps to [Company]'s [specific challenge]."

**Cover letter quality gates:**
- **Uniqueness test:** each section must be specific enough that it can't be sent to another company unchanged.
- **Resume separation:** the letter must add what the CV can't (the "why", connective tissue, future vision).
- **Length:** 250-350 words. Hard ceiling 400. If over 350, trim section 2 to one proof point.
- **Anti-patterns:** no hedging ("I believe I could", "hoping to"), no filler openers, no em dashes, no trait claims without evidence, no generic enthusiasm.
- **ATS:** 3-5 key JD terms woven naturally into the letter body.
- **Company name** appears at least twice and is spelled correctly.
- **Language variant** consistent (US/UK — match the JD).

Apply any `[context]` overrides: `"emphasize [project]"`, `"more informal tone"`, `"keep to 200 words"`, `"mention coffee chat with [name]"`, etc.

### Step 8: Generate Companion Cheat Sheet

Generate a pre-interview cheat sheet following the structure, quality rules, and markdown template in `framework/application-workflow.md` § Cheat Sheet Structure.

### Step 9: Determine Output Filenames

- Date prefix: `MMDDYY` (today's date)
- Company subfolder: `output/<company-slug>/`
- **CV artifacts all use the `MMDDYY-magnuson` stem** (Nick's preference — clean person-named files for submission, no role title in CV filenames):
  - CV YAML source (content-only): `output/<company-slug>/MMDDYY-magnuson.content.yaml`
  - CV YAML (final, design baked in): `output/<company-slug>/MMDDYY-magnuson.yaml`
  - CV PDF (the artifact you send): `output/<company-slug>/MMDDYY-magnuson.pdf`
  - CV Markdown (rendercv-emitted, used by review skills): `output/<company-slug>/MMDDYY-magnuson.md`
- Cheat sheet **does** include the role slug (role-specific): `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- Cover letter: `output/<company-slug>/MMDDYY-cover-letter.md`
- If a CV file at that path already exists (e.g. two apps at same company same day): append `-v2`, `-v3` etc. to ALL four CV files.

### Step 10: Save CV Source YAML

Write the tailored content-only YAML from Step 6 to:
`output/<company-slug>/MMDDYY-magnuson.content.yaml`

Must NOT contain a `design:` block — composed in by Step 10a. `tools/cv_merge_theme.py` errors if duplication is detected.

### Step 10a: Compose Theme + Render

```bash
PYTHONIOENCODING=utf-8 python3 tools/cv_merge_theme.py \
  --content output/<company-slug>/MMDDYY-magnuson.content.yaml \
  --out     output/<company-slug>/MMDDYY-magnuson.yaml \
  --json

~/.local/bin/rendercv render output/<company-slug>/MMDDYY-magnuson.yaml \
  --pdf-path       MMDDYY-magnuson.pdf \
  --markdown-path  MMDDYY-magnuson.md \
  --output-folder  output/<company-slug>/rendercv_output \
  --dont-generate-html \
  --dont-generate-png
```

**No `cd`, deliberately.** `--pdf-path`/`--markdown-path` resolve relative to the input file, so output lands beside the YAML as before; `--output-folder` resolves against cwd and is pinned. A relative `cd` is a latent false-confirmation bug in this harness (the cwd resets between tool calls) — see `memory/feedback_bash_confirm_must_chain_to_operation.md`.

Delete the auto-generated `rendercv_output/` subfolder once canonical files are in place. The `.yaml` is design+content baked together (reproducible standalone); the `.pdf` is the send artifact; the `.md` is for review skills.

### Step 10c: Save Cheat Sheet & Cover Letter

1. **Cheat sheet** → `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md` (role-slug — cheat sheets are role-specific)
2. **Cover letter** → `output/<company-slug>/MMDDYY-cover-letter.md`

### Step 10d: Deep Review (only if `deep_review = true`)

If the `--deep-review` or `--deep` flag was set in Step 1, invoke `/review-cv-deep` against the just-saved CV before updating the pipeline. Pass two arguments: the CV filename (just the filename — the skill reads from `output/`) and the JD (pass the URL if one was provided; otherwise write the JD text to a temp file and pass that path).

The deep-review skill produces `output/<company-slug>/MMDDYY-magnuson-DEEP-REVIEW.md` — a six-perspective audit with CRITICAL / IMPORTANT / MINOR / NITPICK findings and a Top 5 Highest-Impact Changes table.

Wait for deep-review to complete before proceeding to Step 11. Capture the key verdicts (Recruiter phone-screen decision, Hiring Manager interview decision, Competitor shortlist rank, Top 3 critical issues) for the Step 12 summary display.

If the flag was not set, skip this step entirely.

### Step 10e: Confirm submission status (mandatory — do NOT skip)

`/apply` generates artifacts; submission is a separate human action. Before any pipeline write that would flip stage to `Applied`, ask the user:

> Did you submit this application to **[Company]** just now? (Y/N)

Branch on answer (capture as `pipeline_stage`):

- **Y** → `pipeline_stage = "Applied"`; proceed to Step 11.
- **N** → `pipeline_stage = "To Apply"`; default next-action = `Submit when ready — bundle generated [today]`; proceed to Step 11.

**Why this exists:** auto-flipping the pipeline row to `Applied` whenever a bundle is generated creates ghost rows when Nick generates artifacts but doesn't actually submit. Origin: 2026-05-28 a target-company row — `/apply` produced output/<slug>/ artifacts on 2026-05-08; pipeline showed `Applied 5/8` for ~3 weeks even though Nick never submitted (warm-intro path was pending). The post-gen confirmation closes this silent corruption surface. See `memory/feedback_pipeline_applied_status_must_be_user_confirmed.md`.

**When using `--deep-review`:** if the verdict suggests fixing before submitting, answering **N** here captures the bundle on disk + adds the row at `To Apply` without committing to `Applied` prematurely.

### Step 10f: Fit-reason capture (mandatory prompt, optional answer)

Applying is a fit-forming moment — Nick just decided this role is worth a tailored bundle, so his fit read is fresh. Ask one line:

> One-line fit read for the calibration ledger — why is this in your lane (or where's the reservation)?

Capture his answer as `fit_reason` (verbatim, one line) and, if he names one, `fit_verdict` ∈ {fit, not-fit, neutral, unknown}. Light and optional — accept a skip, do not block the bundle. Rationale: this is the source-coverage input the calibration loop needs; the blind machine re-run abstained on 9 of 52 of Nick's fit calls because his reasoning lived only in his head (`output/analysis/071526-machine-vs-human-agreement.md`). Sanitize any `|` out of his words (it would break the table row); replace with `/`.

### Step 11: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive substring match).
3. **If found:** Update the entry:
   - Set **CV Used** to the CV filename (just the filename, not full path)
   - Append the cover letter filename to **CV Used** (separate with `, `)
   - **Stage transition rules:**
     - If current stage is `Researching` and `pipeline_stage == "Applied"`: update to `Applied`
     - If current stage is `Researching` and `pipeline_stage == "To Apply"`: update to `To Apply`
     - If current stage is `To Apply` and `pipeline_stage == "Applied"`: update to `Applied`
     - If current stage is `Applied` or later (Phone Screen, Interview, Offer, etc.): **do NOT regress** the stage; only update CV Used field. The edge-case rule below applies.
4. **If not found:** Add a new row to the Active section:
   - Stage: `pipeline_stage` (from Step 10e — either `Applied` or `To Apply`)
   - Date Added: today
   - Date Updated: today
   - CV Used: [cv filename]
   - Next Action: if `pipeline_stage == "To Apply"`: `Submit when ready — bundle generated [today]`; else default per stage convention
   - Notes: if `pipeline_stage == "Applied"`: `Added by /apply on [today's date]`; else `Bundle generated by /apply on [today's date] — pending submission`
4b. **Append the fit-reason tag (both branches)** if `fit_reason` was captured in Step 10f: append ` [fit-reason [today's date] <fit_verdict>: <fit_reason>]` to the Notes cell (omit `<fit_verdict> ` if none named). Format must match `pipe_write.py`'s `compose_fit_note` exactly so the extractor/scorer can grep it: `[fit-reason YYYY-MM-DD verdict: reason]`. Keep it inside the single Notes cell (no stray `|`).
5. Write `data/job-pipeline.md`.

### Step 12: Display Summary

```markdown
## Application Bundle Ready — [Role Title] at [Company]

### Files Saved
- **CV PDF (send file):** `output/<company-slug>/MMDDYY-magnuson.pdf`
- **CV YAML (source):** `output/<company-slug>/MMDDYY-magnuson.yaml`
- **CV Markdown (for review):** `output/<company-slug>/MMDDYY-magnuson.md`
- **Cheat sheet:** `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- **Cover letter:** `output/<company-slug>/MMDDYY-cover-letter.md`

### CV Quality Summary
- **Keyword coverage:** N/10 matched [list any unfixable gaps]
- **Claims verified:** N checked, N corrected
- **Issues fixed:** [list or "none"]
- **Language consistency:** clean / N items fixed

### Deep Review Verdict (only if `--deep-review` was used)
- **Deep review file:** `output/<company-slug>/MMDDYY-magnuson-DEEP-REVIEW.md`
- **Recruiter (phone invite?):** Yes / No / Maybe
- **Hiring Manager (interview?):** Yes / No / Maybe
- **Competitor shortlist rank:** N of 8
- **Top 3 critical issues surfaced:** [one-line each]
- **Recommendation:** [one-line — proceed, fix before submitting, or reconsider]

### Cover Letter
- **Word count:** N words [within target / over — consider trimming]
- **Hook angle:** [one-line summary]
- **Proof points:** [projects used]

### Pipeline
[✅ Pipeline updated — [Company] stage: `<pipeline_stage>`, CV Used set] OR [✅ New pipeline entry added — [Company] / [Role] / `<pipeline_stage>`]

If `pipeline_stage == "To Apply"`: add a one-line reminder under this section: *"Run `/pipe update <Company> stage:Applied` after submitting."*

### Projects Selected
1. [Project name] — [one-line rationale]
2. ...

### Suggested Next Step
- Review the CV: `/review-cv output/<company-slug>/MMDDYY-magnuson.md`
- Open the printable PDF: `open output/<company-slug>/MMDDYY-magnuson.pdf`
- When ready to interview: `/prep-interview "[Company]"`
- To re-render after edits (no `cd` — the shell cwd resets between tool calls, so a relative `cd` is a latent false-confirmation bug; `--pdf-path`/`--markdown-path` resolve relative to the input file, `--output-folder` against the cwd): `~/.local/bin/rendercv render output/<company-slug>/MMDDYY-magnuson.yaml --pdf-path MMDDYY-magnuson.pdf --markdown-path MMDDYY-magnuson.md --output-folder output/<company-slug>/rendercv_output --dont-generate-html --dont-generate-png`
```

## Edge Cases

- **URL fetch fails:** Ask user to paste the JD directly. Do not attempt to reconstruct.
- **Too few projects:** If fewer than 3 relevant projects exist, use all available. Note in summary.
- **Missing profile.md:** Proceed without personal details. Leave name/contact as placeholders in cover letter. Flag in summary.
- **Cover letter > 350 words:** Flag in summary with suggestion to trim paragraph 2 to 2 evidence points.
- **Personal connection in networking.md:** Mention in the cover letter hook paragraph — "After speaking with [First Name]..."
- **Existing pipeline entry already at Applied or later:** Do not regress the stage, even if Step 10e returned `N`. Only update CV Used field. Surface a note in the Step 12 summary: *"Existing stage `<current_stage>` preserved; CV Used updated."*
- **Keyword not coverable:** If a keyword can't be added naturally (candidate lacks the skill), flag as `⚠️ Gap — omit` in the ATS coverage table.
- **Company dossier stale (>30 days):** Surface inline warning (see Step 4) but continue.
