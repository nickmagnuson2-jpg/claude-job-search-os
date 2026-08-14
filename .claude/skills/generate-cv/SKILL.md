---
name: generate-cv
description: Generate a tailored CV + interview cheat sheet for a specific role — follows the 11-step resume workflow, saves to output/, and updates the pipeline
argument-hint: <job-url-or-jd> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(plugins/*), Glob(framework/*), Write(output/**), Write(data/job-pipeline.md), mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebFetch, WebSearch, Bash(python3 tools/projects_to_yaml.py:*), Bash(python3 tools/cv_merge_theme.py:*), Bash(~/.local/bin/rendercv render:*), Bash(rendercv render:*), Bash(rm -rf output/**/rendercv_output)
---

# Generate CV — Tailored Resume + Cheat Sheet

Generate a fully tailored CV and companion interview cheat sheet for a specific job posting. Produces ATS-optimised output in the right format for the market and saves both files to `output/`. Quality standards, tailoring rules, and the pre-output checklist are defined in `framework/application-workflow.md`.

## Arguments

- `$ARGUMENTS` (required at minimum a job URL or pasted JD text):
  - **`<job-url-or-jd>`** (required) — a URL to the job posting, or the full pasted job description text
  - **`[context]`** (optional) — additional instructions, e.g. `"emphasize McKinsey"`, `"US resume format"`, `"focus on operations experience"`

Examples:
- `/generate-cv https://jobs.impossible.com/cos-role` — fetch and tailor to that posting
- `/generate-cv "Chief of Staff, Northwind..." "emphasize food/FMCG experience"` — pasted JD with context
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

Read all CV-relevant files listed in `framework/application-workflow.md` § Candidate Context Loading (the "CV" column). Skip any that don't exist, never fail.

Derive the company slug from the JD in Step 1 if already parsed, otherwise read `data/company-notes/<company-slug>.md` and the company dossier after Step 3.

Run the **Company Dossier Staleness Check** from `framework/application-workflow.md` § Company Dossier Staleness Check.

### Step 3: Analyse the Role

From the job posting text, extract:

- **Company name** and generate a slug (lowercase, hyphens, e.g. `beacon`)
- **Role title** and a role slug (e.g. `chief-of-staff`)
- **Required skills** (must-haves stated explicitly)
- **Nice-to-have skills** (preferred, bonus, or desirable)
- **Seniority level** (IC, manager, director, VP, C-suite)
- **Top 10 ATS keywords** — the most important terms to appear in the CV for ATS passage. Prioritise: job title words, required skills, industry-specific terms, tools/technologies named.
- **Market** — infer from location mentions or company HQ: `US`, `UK`, `DACH`, or `international`. Default to `US` if unclear.
- **Industry** — infer from company and role context.

### Step 4: Check Plugins

If `data/plugin-activation.md` exists, read it. Glob `plugins/*/plugin.md` and check for any plugin with `scope: cv` or `scope: all`. If found, read those plugin files and apply any instructions they contain for CV generation.

### Step 5: Select Projects & Generate Factual Stubs

1. Read `data/project-index.md` — scan all entries for relevance to the role's required skills, industry, seniority level, and company type.
2. Select **3–6 most relevant projects**. Criteria: skill overlap with required skills > industry/domain match > seniority match > recency.
3. Read the full project files for each selected project from `data/projects/`.
4. **NEVER read or use files from `data/project-background/`** — those are internal-only and must not appear in CVs.
5. Note the rationale for each selected project (will be used in the cheat sheet).
6. **Generate factual EXPERIENCE stubs.** Run:
   ```bash
   PYTHONIOENCODING=utf-8 python3 tools/projects_to_yaml.py --include <slug1>,<slug2>,... --out /tmp/cv-experience-stubs.yaml --json
   ```
   Where `<slug>` matches the filename (no `.md`) of each selected project, in the order they should appear on the CV (most recent first). Read the resulting YAML — it is the **source-of-truth baseline** for company, position, date, location, and highlights. Per lesson #54, every quantified claim on the final CV must trace back to either this stub or directly to `data/projects/<slug>.md`. Tailored phrasing is fine; invented numbers are not.

### Step 6: Generate the CV YAML

The CV is emitted as a **RenderCV YAML** in the same shape as the canonical reference `output/example-ventures/042826-cos-example.yaml`. The design block is NOT included here — it is composed in from the shared theme in Step 9b. Structure:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/rendercv/rendercv/refs/tags/v2.8/schema.json
cv:
  name: NICK MAGNUSON
  location: San Francisco, CA
  email: priya.anand@example.com
  phone: tel:+1-415-555-0148
  social_networks:
    - network: LinkedIn
      username: nicholas-magnuson
  sections:
    SUMMARY:
      - <3–4 line tailored summary>
    EXPERIENCE:
      - <entries from Step 5 stubs, tailored>
    EDUCATION:
      - institution: TUCK SCHOOL OF BUSINESS AT DARTMOUTH
        area: Management Science and Quantitative Methods (STEM)
        degree: MBA
        date: 2020 – 2022
        location: Hanover, NH
      - institution: DUKE UNIVERSITY
        area: Public Policy, Markets and Management Certificate
        degree: BA
        date: 2011 – 2015
        location: Durham, NC
    ADDITIONAL INFORMATION:
      - label: Skills
        details: <tailored skills line — concrete tools + craft only; see Skills rule below>
      - label: Building              # use "Building" NOT "Side project" (Nick: "side project is not what I am")
        details: <the AI products Nick has built — named, by what they ARE + capability, NOT internal plumbing>
      - label: Hobbies
        details: <hobbies line from profile.md>
locale:
  language: english
settings:
  current_date: today
  bold_keywords: []
  pdf_title: Nick Magnuson - CV
```

Apply all **Tailoring Rules** and **CV Quality Standards** from `framework/application-workflow.md`. Key requirements:

- **Professional summary** — 3–4 lines tailored to this specific role. Opens with a hook tied to the company's mission or the role's core challenge. **Voice (Nick, 2026-06-22):** lead with the BUILDER/OPERATOR identity and results ("I get things done, use data, and build the thing that fixes it"), NOT a credential-first opener. Do NOT lead with "McKinsey-trained" or frame Nick as someone who "lands complex change in large organizations" — for early-stage/founder audiences that reads as pedigree-play (and founders hire for agency over credentials). McKinsey + Zuora are SUPPORTING credibility, named mid-summary, not the lead. First-person ("I ...") is acceptable and in voice for the summary.
- **Experience entries** — start from the Step 5 stubs verbatim. Tailor bullet PHRASING for JD relevance (emphasize keywords, lead with most-relevant impact). Do NOT invent quantified claims not present in the stubs or in `data/projects/<slug>.md`. Lesson #54: prior-CV numbers are hypotheses, source files are tests.
- **Experience ordering** — strict reverse-chronological (most recent first). Do NOT reorder by relevance — non-chronological CVs are flagged by ATS systems and confuse recruiters. Tailor through bullet selection and emphasis, not ordering.
- **Skills section** — **concrete tools + craft ONLY.** Per lesson #31/#53 + `feedback_cv_one_page_default_no_skills_fluff` + Nick 2026-06-22: every item must be substantively evidenced by a bullet; hedge words (`-adjacent`, `partnership` without a verb, `exposure`, `familiarity`) forbidden. **DO NOT emit Title-Case competency buzzwords or JD-mirroring compound phrases** ("Implementation & Deployment", "Customer Onboarding", "Change Management", "Cross-Functional Program Management", "Stakeholder Management", "Operating Cadence & Playbook Design", "Executive Communication") — those are already shown in the bullets and read as keyword bait. Nick's preferred framing (2026-06-22): group as the real things he does — **building with AI** (Claude Code, Claude/OpenAI APIs, Next.js, Postgres, AI agent workflows), **data analysis and financial modeling** (SQL, R, Tableau, Looker, Excel), **executive decks** (PowerPoint). Build the line from `data/skills.md` + evidenced experience, sentence case, strongest/most-differentiated first. **Building entry:** label it `Building` (NOT "Side project") and name Nick's AI products (Portrait Crossword at portraitcrossword.com; the job-search OS) by what they ARE + capability, never by listing internal primitives (hooks, scripts) — see `feedback_dev_jargon_to_ceo_context`.
- **ATS keyword coverage** — verify all 10 extracted keywords appear at least once in the CV text. If a keyword is missing, find a natural place to include it.
- **Achievements over responsibilities** — lead bullets with quantified outcomes where possible (sourced from stubs, not invented).
- **No content from `data/project-background/`** — enforce absolutely.
- **No em dashes** (per CLAUDE.md hard rule) — but the EN DASH (`–`) is used in date ranges per the reference YAML.

### Step 6a-corrections: Reconcile against source corrections (mandatory, BEFORE first render)

Run this against every source project file you drew bullets from in Step 5/6:

```bash
PYTHONIOENCODING=utf-8 python3 tools/source_corrections.py data/projects/<file>.md [more...]
```

Source files carry their honesty history in HTML comments pinned to the bullet they correct. Those comments are invisible while you are reading the claim text you're paraphrasing — which is exactly how this has failed **twice**, both times with the file already read in full in the same session:

- **2026-07-08:** "Ran the operating cadence" / "stood up from scratch" reintroduced the `"facilitated" not "stood up"` overclaim that `zuora.md` had carried a dated correction for since 2026-05-12.
- **2026-08-07:** "built the POC portfolio with our head of engineering" reintroduced an overstatement the same file corrects to "partnered with."

For each correction returned, check the drafted line against the **corrected** wording, not the original claim. A correction that says "keep this wording, but the underlying fact is X" (there is one live example) means the CV line stands and the *cheat sheet* must carry the real fact — do not silently rewrite the bullet.

Also grep the drafted content YAML for `—` before first render. No em dashes, ever (hard project rule). Both fires shipped 3-4 of them in the Summary section.

**Why here and not Step 10b:** both failures were caught only by the 6-agent deep review, after the render. This check costs one command and runs before the expensive pass. Origin: `memory/feedback_cv_em_dash_and_source_verb_regression.md`.

### Step 6b: Inline Quality Review (mandatory — do NOT skip)

Before generating the cheat sheet, run all 18 checks from `framework/application-workflow.md` § CV Quality Checks against the CV you just produced. Fix any issues found **in place** — rewrite the CV, don't just flag problems.

After all fixes, record a QC summary using the template in `framework/application-workflow.md` § QC Summary Template.

### Step 7: Generate Companion Cheat Sheet

Alongside the CV, generate a pre-interview cheat sheet following the structure, quality rules, and markdown template in `framework/application-workflow.md` § Cheat Sheet Structure.

### Step 8: Determine Output Filenames

- Generate date prefix: `MMDDYY` (today's date)
- Use the company slug from Step 3 as the subfolder: `output/<company-slug>/`
- **CV artifacts all use the `MMDDYY-magnuson` stem** (Nick's preference — clean person-named files for submission, no role title in CV filenames):
  - CV YAML (content-only, no design block): `output/<company-slug>/MMDDYY-magnuson.content.yaml`
  - CV YAML (final, design baked in, render-ready and reproducible standalone): `output/<company-slug>/MMDDYY-magnuson.yaml`
  - CV PDF (the artifact you send): `output/<company-slug>/MMDDYY-magnuson.pdf`
  - CV Markdown (rendercv-emitted, used by review skills): `output/<company-slug>/MMDDYY-magnuson.md`
- Cheat sheet **does** include the role slug (it is role-specific, not interchangeable across roles at the same company): `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`
- If a CV file at that path already exists (e.g. two applications at the same company on the same day), append `-v2`, `-v3` etc. to ALL four CV files in the set. The cheat sheet's role-slug already disambiguates it.

### Step 9: Save CV Source YAML

Write the tailored content-only YAML from Step 6 to:
`output/<company-slug>/MMDDYY-magnuson.content.yaml`

The `.content.yaml` MUST NOT contain a `design:` block — design lives in `framework/cv-themes/tuck-mbb.yaml` and is composed in by Step 9a. `tools/cv_merge_theme.py` will error if a design block is already present (that's how the duplication safety net works).

### Step 9a: Compose Theme

Merge the content YAML with the shared design theme:

```bash
PYTHONIOENCODING=utf-8 python3 tools/cv_merge_theme.py \
  --content output/<company-slug>/MMDDYY-magnuson.content.yaml \
  --out     output/<company-slug>/MMDDYY-magnuson.yaml \
  --json
```

The `.yaml` file is now complete and standalone — design + content baked together. Anyone with this single file can re-render the exact PDF months from now, even if the shared theme has changed since.

### Step 9b: Render PDF + Markdown + PNG

```bash
~/.local/bin/rendercv render output/<company-slug>/MMDDYY-magnuson.yaml \
  --pdf-path       MMDDYY-magnuson.pdf \
  --markdown-path  MMDDYY-magnuson.md \
  --output-folder  output/<company-slug>/rendercv_output \
  --dont-generate-html
```

**No `cd`, deliberately.** `--pdf-path` and `--markdown-path` are resolved *relative to the input file* (verified against `rendercv render --help`), so the artifacts land beside the YAML exactly as before. `--output-folder` is the one path resolved against the cwd, so it is pinned explicitly. The shell cwd resets between tool calls in this harness, which makes a relative `cd` a latent false-confirmation bug — see `memory/feedback_bash_confirm_must_chain_to_operation.md`.

The PDF is the artifact you send. The markdown is the rendercv-emitted version used by `/review-cv` and `/review-cv-deep`. The PNG (in `rendercv_output/*_1.png`) is for the layout verification in Step 9b-verify — do NOT delete `rendercv_output/` until after that step.

### Step 9b-verify: Render & verify before presenting (mandatory — checks #19, #20)

Per `framework/application-workflow.md` § Render & verify. Do NOT skip — this is where the layout/length defects that otherwise force user iteration get caught.

1. **`Read` the PNG** (`output/<company-slug>/rendercv_output/*_1.png`). Visually confirm: exactly one page for Nick; italics, line breaks, and spacing render cleanly (no stray asterisks, no broken emphasis, no awkward title wraps); page well-filled, not overflowing. Reasoning from the markdown alone misses these — the rendercv gotchas (bare-year → "Jan YYYY"; `\n` breaking position italics) are only visible in the render. **Bare-year fix (Nick, 2026-06-22):** for a single-year role, write the date as an UNQUOTED integer (`date: 2024`) — that renders as "2024". A quoted `date: '2024'` renders as "Jan 2024" (wrong, and it spotlights short stints / the silent gap). Multi-year ranges (`date: 2022 – 2024`) already render verbatim.
2. **Count pages:** `PYTHONIOENCODING=utf-8 python3 -c "from pypdf import PdfReader; print(len(PdfReader('output/<company-slug>/MMDDYY-magnuson.pdf').pages))"`. For Nick this must be 1.
3. **If over one page or layout is off, fix it now and re-render** — trim per § Length & One-Page Verification (tighten summary → merge/cut weakest-oldest bullets → shorten the Building entry → drop filler, before touching design). Never hand over a 2-page CV or one with mechanical layout defects for the user to "edit down."
3b. **Fill the page — don't leave a half-empty CV (Nick, 2026-06-22).** If there is meaningful blank space at the bottom, the CV is under-filled — bump sizing in the FINAL `MMDDYY-magnuson.yaml` design block (NOT the shared theme, so other CVs are unaffected) and re-render: body `11pt` / name `20pt`, `line_spacing: 0.6em`, `sections.space_between_regular_entries: 0.5em`, and `section_titles.space_above: 0.45cm` for clear breaks between SUMMARY / EXPERIENCE / EDUCATION / ADDITIONAL INFORMATION. Re-check it still holds at one page (Step 2). The target is a full, balanced single page, not a top-heavy one.
4. Once verified, `rm -rf output/<company-slug>/rendercv_output/`.

### Step 9c: Save Cheat Sheet

Write the cheat sheet from Step 7 to: `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md` (cheat sheets keep the role slug — they are role-specific, not interchangeable across roles at the same company)

### Step 10: Update Pipeline

1. Read `data/job-pipeline.md`.
2. Search for the company name (case-insensitive, fuzzy match — check if the company name from the JD appears as a substring in any active pipeline entry).
3. If found: update that entry's **CV Used** field to the CV output filename (just the filename, not full path).
4. If not found: note in the summary that the company isn't in the pipeline yet and suggest `/pipe add "[Company]" "[Role]"`.

### Step 10b: Run Deep Review (always — do NOT skip)

After saving the CV and updating the pipeline, automatically invoke `/review-cv-deep` against the just-saved CV. This is mandatory, not optional. The deep review produces a six-perspective audit (Recruiter / Hiring Manager / Competitor / Skeptic / Copy Editor / Source Auditor) saved to `output/<company-slug>/MMDDYY-magnuson-DEEP-REVIEW.md`.

**Rationale:** A single-pass inline review (Step 6b) catches surface issues but routinely misses (a) source-data fabrications detectable only by cross-referencing project files, (b) chronology bugs that a structured external-eyes pass surfaces, and (c) high-leverage missed assets documented in source files but absent from the CV. The 6-agent flow is the only reliable way to catch these. Speed is not an acceptable trade-off — every CV gets the same scrutiny.

Pass two arguments to `/review-cv-deep`: the CV filename (just the filename — the skill reads from `output/`) and the JD (pass the URL if provided; otherwise write the JD text to a temp file and pass that path).

Wait for the deep review to complete before proceeding to Step 11. Capture the key verdicts (Recruiter phone-screen decision, Hiring Manager interview decision, Competitor shortlist rank, top 3 CRITICAL/IMPORTANT findings) for the Step 11 summary display.

**Apply the high-confidence quality fixes before presenting — do NOT hand the user a report of cleanup they have to ask for.** After the deep review returns, auto-apply (and re-render + re-verify per Step 9b-verify) the objective, low-risk findings the panel converges on: cut vague/source-unbacked filler bullets, fix skills format/filler, remove a location line from the summary, fix any unevidenced skill or label, resolve layout/one-page defects. Then re-run Step 9b-verify. Leave for the user ONLY the judgment/voice calls (summary phrasing they must stand behind, which optional achievements to include, claim-level decisions). Surface those as a short menu, not as a list of mechanical edits. Origin: 2026-06-11 recruiter-channel CV — the deep review flagged filler bullets, the skills grouping, and the summary location line, but they were left for the user to catch, driving ~6 extra rounds.

**Opt-out:** if the user explicitly passes `--no-deep-review` in `[context]`, skip this step and note "Deep review skipped per --no-deep-review flag" in the Step 11 summary. This exists for fast-iteration cases (drafting variants); the default is always-on.

### Step 11: Display Summary

```markdown
## CV Generated — [Role Title] at [Company]

**Format:** [US/UK/DACH/international] | **Market:** [market]
**CV PDF (send file):** `output/<company-slug>/MMDDYY-magnuson.pdf`
**CV YAML (source):** `output/<company-slug>/MMDDYY-magnuson.yaml`
**CV Markdown (for review):** `output/<company-slug>/MMDDYY-magnuson.md`
**Cheat sheet:** `output/<company-slug>/MMDDYY-[role-slug]-cheatsheet.md`

### QC Summary (from Step 6b self-review)
- **Keyword coverage:** N/10 matched [list any unfixable gaps]
- **Claims verified:** N checked, N corrected
- **Issues fixed:** [list or "none"]
- **Language consistency:** clean / N items fixed

### Deep Review Verdict (from Step 10b — always runs)
- **Deep review file:** `output/<company-slug>/MMDDYY-magnuson-DEEP-REVIEW.md`
- **Recruiter (phone invite?):** Yes / No / Maybe
- **Hiring Manager (interview?):** Yes / No / Maybe
- **Competitor shortlist rank:** N of 8
- **Top 3 critical/important issues surfaced:** [one-line each]
- **Recommendation:** [one-line — proceed, fix before submitting, or reconsider]

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
- Review output: `/review-cv output/<company-slug>/MMDDYY-magnuson.md`
- Open the printable PDF: `open output/<company-slug>/MMDDYY-magnuson.pdf`
- When ready to apply: `/pipe update "[Company]" Applied`
- Before interview: `/prep-interview "[Company]"`
```

## Edge Cases

- **URL fetch fails**: Ask user to paste the JD text directly. Do not attempt to reconstruct the JD from partial content.
- **Too few projects**: If fewer than 3 relevant projects exist, use all available. Note in summary: "Only N projects available — consider adding more to `data/projects/`."
- **Missing profile.md**: Proceed without personal details. Omit compensation/availability from cheat sheet. Flag in summary.
- **Missing coached-answers.md**: Skip that section of cheat sheet silently.
- **Keywords not coverable**: If a keyword can't be added naturally to the CV (e.g. a technology the candidate genuinely doesn't have), flag it in the ATS coverage table as `⚠️ Gap — omit` and note it in the summary as a genuine skill gap.
- **Multiple roles at same company in pipeline**: Update the most recently active matching entry.
