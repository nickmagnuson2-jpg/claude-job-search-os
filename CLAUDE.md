<!-- This file is the orchestration brain — Claude reads it on every session.
     Personal details live in data/profile.md (gitignored).
     Everything else works as-is. -->

# AI Job Search System

## Purpose

This repository is your **end-to-end job search operating system** — from identifying target companies to accepting an offer. It covers the full lifecycle:

1. **Job search operations** — pipeline tracking, outreach, networking, and weekly retrospectives keep the search moving forward
2. **Market intelligence** — industry and company research informs targeting, outreach tone, and interview prep
3. **Application materials** — tailored CVs and interview cheat sheets generated for specific roles
4. **Interview preparation** — recruiter screenings, hiring manager interviews, and voice simulations with coaching
5. **Professional self-awareness** — identity, values, strengths, and narrative patterns that underpin everything
6. **Structured record-keeping** — all experience data lives here in markdown, versioned with git

## Self-Improvement Loop

After ANY correction from the user:
1. Open `memory/lessons.md` and add a new row to the Active Rules table with:
   - The pattern (what went wrong — factual, specific)
   - The rule (what to do instead — actionable, preventive)
   - Today's date
2. If a correction refines an existing rule, update that row instead of adding a duplicate.
3. Review `memory/lessons.md` at the start of any session involving skill edits, data file operations, or CV generation — scan takes under 60 seconds.

**Email/outreach correction tracking:**
- When Nick edits, rewrites, or rejects an email/LinkedIn draft: capture the delta as a row in Section 2 of `memory/lessons.md`
- New pattern: add row with Occurrences = 1, Promoted = No
- Recurring pattern: find existing row and increment Occurrences
- If Occurrences >= 2 AND Promoted = No: update `framework/style-guidelines.md` under a "Nick's Voice" section, then set Promoted = Yes in the lessons.md row

## Owner Profile

Owner details live in `data/profile.md` (gitignored — prevents personal data leaking on public forks).

Search goals and targeting criteria live in `data/goals.md` (also gitignored). See `framework/templates/goals.md` for the template.

**Always read `data/profile.md` and `data/goals.md` at the start of any session that generates CVs, coaching, or personalised output.**

### Profile Guard — Hard Prerequisite

**Before running ANY generative or research skill** (`/generate-cv`, `/research-company`, `/research-industry`, `/prep-interview`, `/cold-outreach`, `/follow-up`, `/draft-email`, `/voice-export`, `/extract-identity`, `/review-cv`, `/review-cv-deep`, `/weekly-review`, `/scan-jobs`, `/standup`), check that **both** `data/profile.md` and `data/goals.md` exist and contain real content (not just TODO placeholders). If either file is missing or contains only TODO placeholders:

1. **STOP** — do not proceed with the skill.
2. If `data/profile.md` is missing or empty: "⚠️ `data/profile.md` is missing or incomplete. Run `/import-cv` first to populate your profile, or manually create `data/profile.md` with your name, background, and career context."
3. If `data/goals.md` is missing or all TODOs: "⚠️ `data/goals.md` is missing or incomplete. Copy `framework/templates/goals.md` to `data/goals.md` and fill in your search thesis and target criteria before running skills."
4. Do not fall back to generic candidate context — the whole system depends on personalized data.

## Repository Structure

```
/
├── CLAUDE.md                  # This file — project instructions
├── framework/
│   ├── interview-workflow.md    # Interview coaching workflow and rules
│   ├── style-guidelines.md     # Tone, language, CV format conventions
│   ├── recruiter-screening.md   # Recruiter screening coaching session methodology
│   ├── mock-interview.md        # Hiring manager mock interview coaching methodology
│   ├── full-simulation.md       # Full simulation mode (uninterrupted conversation + debrief)
│   ├── voice-export.md          # Voice mode export — recruiter simulation prompt for Claude App
│   ├── outreach-guide.md         # Outreach frameworks, channel constraints, quality gates, anti-patterns
│   ├── data-enrichment.md       # Post-session data file enrichment (auto-capture new info from sessions)
│   ├── answering-strategies/     # In-call answering strategies (quick-reference for interviews)
│   │   ├── behavioral-questions-without-prepared-example.md  # Blank Mind Protocol for STAR questions
│   │   ├── gap-reframing.md                                  # Handling missing skills/experience
│   │   ├── pin-down-defense.md                               # Handling pressure tests on gaps
│   │   ├── question-back-technique.md                        # Turning questions around strategically
│   │   ├── anti-patterns.md                                  # What NOT to do (60-second pre-call checklist)
│   │   └── direct-answer-structure.md                        # Answer first, explain second (fixes essay structure)
│   └── templates/               # Templates for new data and session files
│       ├── project.md            #   New project file template
│       ├── recruiter-session.md  #   Recruiter screening session log template
│       ├── recruiter-summary.md  #   Recruiter progress summary template
│       ├── interview-session.md  #   Interview session log template
│       ├── interview-summary.md  #   Interview progress summary template
│       ├── plugin.md             #   New plugin manifest template
│       └── plugin-activation.md  #   Plugin activation config template (copy to data/plugin-activation.md)
├── coaching/
│   ├── coached-answers.md       # Spoken pitches, topical answers, and high-risk question frameworks (refined in sessions)
│   ├── anti-pattern-tracker.md  # Personal anti-pattern status: resolved/persistent/stable, trends, update log
│   ├── pressure-points.md        # Tactical list of pressure points for tough mode probing (updated after sessions)
│   ├── progress-recruiter/       # Recruiter screening progress tracking
│   │   ├── _summary.md           #   Scorecard, overall stats, session index (initialized from templates/)
│   │   └── YYYY-MM-DD-*.md      #   Individual session logs
│   └── progress-interview/       # Hiring manager interview progress tracking
│       ├── _summary.md           #   Scorecard, overall stats, session index (initialized from templates/)
│       └── YYYY-MM-DD-*.md      #   Individual session logs
├── data/
│   ├── project-index.md       # Lightweight index of all projects (for quick matching)
│   ├── professional-identity.md      # Generated by /extract-identity — identity, strengths, growth edges, values, narrative patterns
│   ├── profile.md             # Personal info, contact, compensation, availability, interests
│   ├── certifications.md      # All certifications with dates and status
│   ├── skills.md              # Complete skill inventory with experience levels
│   ├── education.md           # Degrees, training, qualifications
│   ├── companies.md           # Own companies (if applicable)
│   ├── job-pipeline.md      # Application tracking (managed by /pipe)
│   ├── networking.md        # Contacts and interaction log (managed by /networking)
│   ├── job-todos.md         # Active to-do list (managed by /todo)
│   ├── job-todos-daily-log.md # Daily progress log (generated by /todo daily)
│   ├── outreach-log.md      # Chronological outreach activity log (generated by /cold-outreach, /follow-up, /draft-email)
│   ├── weekly-review-log.md # Weekly retrospective log (generated by /weekly-review)
│   ├── company-research/    # LEGACY — older company dossiers before output/ migration; kept for read fallback
│   ├── industry-research/   # LEGACY — older industry analyses before output/ migration; kept for read fallback
│   ├── notes.md             # General notes and unroutable inbox captures (created by /act; also managed by /remember)
│   ├── company-notes/       # Personal notes per company — recruiter calls, video notes, call prep, observations
│   │   └── <slug>.md        #   One file per company (e.g., dusty-robotics.md) — read by generative skills
│   ├── project-background/    # Sensitive project background (NEVER use in CVs/resumes)
│   └── projects/
│       └── *.md                 # One file per project/engagement
├── plugins/                     # Drop-in extensions (interview coaching, CV generation)
├── examples/                    # Fictional example data (try features before importing your own)
│   ├── data/                    #   Complete fictional freelancer profile
│   ├── output/                  #   Sample generated CV
│   └── plugins/                 #   Example plugin (copy to plugins/ to activate)
├── .claude/
│   └── skills/                  # 20 skill definitions powering slash commands — see Working With This Repo
├── docs/
│   ├── methodology.md           # Full explanation of how each component works
│   ├── customization.md         # Guide to customizing skills, plugins, and data files
│   ├── usage.md                 # Usage guide and common workflows
│   └── privacy.md               # Privacy and security information
├── tools/
│   ├── convert_pdfs.py        # PDF-to-text extractor (for input files in files/)
│   ├── md_to_pdf.py           # Markdown CV → styled PDF converter
│   └── open_draft.py          # Opens Gmail compose URL with pre-filled draft (auto-run by outreach skills)
└── output/                    # All generated output — company-first hierarchy
    ├── [company-slug]/            #   One subfolder per named entity (company or industry)
    │   ├── [slug].md              #     Research dossier (no date prefix — canonical, versioned in place)
    │   ├── MMDDYY-article-*.md   #     Article saves
    │   ├── MMDDYY-[role-slug].md #     Generated CV
    │   ├── MMDDYY-[role-slug]-cheatsheet.md  #   Interview cheat sheet
    │   ├── MMDDYY-cover-letter.md  #   Cover letter
    │   ├── MMDDYY-prep.md         #     Interview prep package
    │   ├── MMDDYY-cold-outreach-[contact-slug].md  #  Outreach archive
    │   ├── MMDDYY-follow-up-[contact-slug].md      #  Follow-up archive
    │   └── MMDDYY-draft-email-[recipient-slug].md  #  Draft email archive
    └── MMDDYY-draft-email-[recipient-slug].md  # Flat fallback if no company known
```

## Output Files — Conventions

**Company-first hierarchy:** Every named entity (company or industry) gets its own subfolder under `output/`. All files related to that entity — research dossiers, CVs, cover letters, prep docs, outreach archives — live inside it.

- Subfolder is created on first write; no pre-creation step needed.
- Folder name = slug (lowercase, spaces→hyphens): `impossible-foods`, `behavioral-health-tech`
- The research dossier file name matches the folder: `output/<slug>/<slug>.md` (no date prefix — exception to the date rule, versioned in place)
- All other files inside the folder **must include a date prefix** (`MMDDYY`):

```
output/<company-slug>/MMDDYY-[descriptor].md
```

Examples:
- `output/impossible-foods/022426-chief-of-staff.md` — tailored CV
- `output/impossible-foods/022426-chief-of-staff-cheatsheet.md` — interview cheat sheet
- `output/impossible-foods/022426-prep.md` — interview prep doc
- `output/impossible-foods/022426-cover-letter.md` — cover letter
- `output/impossible-foods/022426-cold-outreach-sarah-chen.md` — outreach archive
- `output/behavioral-health-tech/behavioral-health-tech.md` — industry dossier (no date, matches folder)
- `output/MMDDYY-draft-email-[recipient-slug].md` — flat fallback when no company is identifiable

**Date prefix rule:** Applies to all dated artifact files within folders. Two exceptions have no date prefix because they are canonical, in-place-versioned files: the research dossier (`<slug>.md`) and the notes log (`notes.md`). Skills that write to `output/` must use the subfolder pattern. Flat `output/MMDDYY-*.md` is only for one-off outputs with no associated entity.

**`data/company-notes/<slug>.md` convention:** A free-form running log of personal context for each company — recruiter call notes, video/article observations, pre-call prep, general thoughts. Lives in `data/` (not `output/`) so it is automatically read by generative skills (`/generate-cv`, `/cover-letter`, `/prep-interview`, `/cold-outreach`). Create via `/remember "..."` or manually; append new entries at the top with a `## YYYY-MM-DD | [context]` header. The dossier (`output/<slug>/<slug>.md`) holds structured research Claude produced; `data/company-notes/<slug>.md` holds everything personal and informal.

## Data Files — Conventions

### IMPORTANT: Content Exclusions for CVs/Resumes

**`data/project-background/` folder contents must NEVER be used in CVs, resumes, or any client-facing materials.**

This folder contains sensitive background information (legal disputes, project failures, internal assessments) that:
- Is for internal reference and risk assessment only
- Must not appear in professional marketing materials
- Should not be mentioned in interviews unless specifically asked
- Serves as institutional knowledge for career planning and decision-making

When generating resumes or preparing interview materials, **completely ignore** all files in `data/project-background/`.

### Projects (`data/projects/*.md`)

Each project file follows a consistent structure with these fields: **Period**, **Role**, **Client**, **Industry**, **Location**, **Type**, then sections for **Description**, **Responsibilities**, **Key Achievements**, **Technologies**, and **Tags**. See `framework/templates/project.md` for the full field list including optional fields.

**Type values:** `flagship` | `consulting` | `contract` | `employment` | `co-founded` | `internship` | `side-project`. A `flagship` is the candidate's single most significant role or project — typically multi-year, high-impact, and central to their professional identity. Not every candidate has one, and that's normal. Not every candidate has a flagship — it's optional.

### Skills (`data/skills.md`)

Skills are grouped by category with experience levels. Keep this updated as new projects add skills.

### Certifications (`data/certifications.md`)

Include status: active, renewal pending, or expired. Include exam codes.

### Companies (`data/companies.md`) *(optional)*

Own business entities with founding dates, purposes, and outcomes. Only relevant for candidates with entrepreneurial or freelance backgrounds. These are not client projects — they provide context for ventures or business arrangements relevant to the candidate's career.

## Research Dossier Standards

Applies to `/research-company` and `/research-industry`. Both skills produce detailed dossiers with a **two-speed reading design:**

1. **Executive Summary** (top of every dossier) — thesis/verdict, opportunity rating, top reasons to target, top risks, recommended next action. Fits one screen. Scan in 2 minutes.
2. **Full dossier** — every major section opens with a bold **BLUF** (Bottom Line Up Front) sentence summarizing the takeaway. Scan all BLUFs in 60 seconds, or read any section in full for detail.

### Evidence Quality Rules

- **Source tiers:** Tier A (primary/official), Tier B (reputable secondary), Tier C (aggregator/crowd — flagged with caveat).
- **Confidence tags:** High-impact claims (market size, funding, growth, headcount, risk events) include `[Confidence: High | Medium | Low, as of YYYY-MM]`.
- **Contradictions:** When credible sources disagree, show both values with sources and mark `[Needs verification]`. Never silently pick one.
- **Freshness:** Prioritize last-12-month sources for trend/news claims. Older sources noted with justification.
- **Self-verification:** Both research skills include an Evidence Summary Table and contradiction audit as mandatory output sections. See skill definitions for details.

### Refresh Behavior

- If a dossier exists and is fresh, offer "view existing" or "refresh."
- On refresh, include a `## What Changed Since Last Update` section listing material differences.

### Handoff Chain

The natural research flow is: `/research-industry` (map the landscape) → `/research-company` (deep-dive top picks) → `/cold-outreach` or `/follow-up` (act on findings). Each skill suggests the next step explicitly.

## Resume Generation

The full CV generation workflow — 11 steps, tailoring rules, quality standards, and the pre-output checklist — is defined inline in `.claude/skills/generate-cv/SKILL.md`. Run `/generate-cv <job-url-or-jd>` to generate a tailored CV.

## Interview Training

See `framework/interview-workflow.md` — read this file before running any coaching session. It contains the session workflow, coaching rules, and progress logging requirements.

### Answering Strategies

Six quick-reference strategy files in `framework/answering-strategies/` provide in-call techniques for recruiter screenings. Each file has a Quick Overview section for fast scanning before interviews:

1. **[behavioral-questions-without-prepared-example.md](framework/answering-strategies/behavioral-questions-without-prepared-example.md)** — The Blank Mind Protocol: what to do when asked "Tell me about a time..." and you can't recall specifics (4 paths: specific example, pattern answer, honest pivot, clarifying question)
2. **[gap-reframing.md](framework/answering-strategies/gap-reframing.md)** — How to address missing skills, experience, or certifications without disqualifying yourself (acknowledge → pivot → question back)
3. **[pin-down-defense.md](framework/answering-strategies/pin-down-defense.md)** — How to handle pressure tests when recruiter restates your gap as a closed fact ("So you don't have X then")
4. **[question-back-technique.md](framework/answering-strategies/question-back-technique.md)** — How to turn questions around strategically to reveal real requirements and buy thinking time
5. **[anti-patterns.md](framework/answering-strategies/anti-patterns.md)** — What NOT to do: 60-second pre-call checklist of common mistakes (volunteering negatives, over-explaining, essay structure, etc.)
6. **[direct-answer-structure.md](framework/answering-strategies/direct-answer-structure.md)** — How to structure answers properly: Answer first → Brief context → Stop (fixes "essay structure" pattern)

**Pre-interview review:** Scan anti-patterns.md (60 seconds) and the Quick Overview sections of relevant strategy files before any recruiter call.

### Voice Simulation Workflow

For voice-based practice, three skills work together:

1. **`/voice-export <cv-path> <job-ad-url>`** — generates a self-contained recruiter simulation prompt for the Claude App (voice mode). No coaching in voice — just a realistic call.
2. *(User practises in the Claude App, then pastes transcript back here)*
3. **`/debrief <cv-path>`** — analyzes the transcript against coached answers, identifies anti-patterns, and logs the session to progress tracking.

## Working With This Repo

### Foundation (do once, revisit as things change)

- **Import your CV:** Run `/import-cv` with a file path or pasted text. Populates `data/` files. Can be run repeatedly — data merges automatically.
- **Discover your professional identity:** Run `/extract-identity` — guided coaching conversation producing `data/professional-identity.md` (strengths, growth edges, narrative patterns, values). Powers narrative consistency across all CVs, pitches, and coaching. **Do this before generating any CV.**
- **Fill in profile details:** Edit `data/profile.md` — compensation, availability, start date. Required for CV cheat sheets and outreach personalization.
- **Add new experience:** Create a file in `data/projects/` (copy `framework/templates/project.md`), add to `data/project-index.md`, update `data/skills.md` if new skills used.
- **Flesh out existing projects:** Many project files have TODO sections after import — fill these in for better CV generation and coaching.

### Research

- **Map an industry:** `/research-industry "industry name" [context]` — market structure, key players, talent demand, trends, jargon, and a ranked target company list based on candidate fit. Run this first when entering a new sector.
- **Deep-dive a company:** `/research-company "Company Name" [url] [context]` — mission, funding, people, culture, news, conversation starters, and similar companies to target. Run this after identifying specific targets.

  Industry research is wide (entire sector); company research is deep (one company). They don't duplicate — run industry first, then company on specific picks.

### Finding & Tracking Opportunities

- **Scan job boards:** `/scan-jobs <portal> [search terms]` — find matching roles with fit scoring.
- **Track applications:** `/pipe` — view pipeline with staleness alerts. `/pipe add <company> <role>` to add. `/pipe update <company> <stage>` to advance.
- **Manage tasks:** `/todo` — view active to-dos with pipeline and contact cross-references. `/todo add`, `/todo done`, `/todo daily`.

### Applications

- **Generate a tailored CV:** `/generate-cv <job-url-or-jd> [context]` — produces a role-specific CV + interview cheat sheet. Follows the 11-step resume workflow, saves to `output/`, updates the pipeline CV Used field.
- **Review a CV:** `/review-cv <cv-path>` for a quick quality gate. `/review-cv-deep <cv-path>` for a multi-perspective deep review.

### Outreach & Networking

- **Cold outreach:** `/cold-outreach <name> <company> [context]` — research-informed first-contact email or LinkedIn message, auto-logged to `data/outreach-log.md`.
- **Follow up:** `/follow-up <name>` — sequence-aware follow-up, tone-matched to prior messages.
- **Draft emails:** `/draft-email [context]` — thank-you notes, status updates, intro requests.
- **Manage contacts:** `/networking` — log interactions, track follow-up status, view relationship history.

### Interview Preparation

- **Prepare for an interview:** `/prep-interview <company> [role] [context]` — 3-agent prep package: question mapping to coached answers, company context digest, pre-call tactics. Saves to `output/`, adds debrief to-do.
- **Voice practice workflow:**
  1. `/voice-export <cv-path> <job-ad-url>` — generates a recruiter simulation prompt for the Claude App (voice mode)
  2. *(Practice in the Claude App, paste transcript back)*
  3. `/debrief` — analyzes transcript against coached answers, logs anti-patterns, updates coaching files

### Ongoing Tracking

- **Weekly retrospective:** `/weekly-review` — pipeline health, outreach response rates, task velocity, research activity, and ranked top 5 priorities for the coming week. Logs to `data/weekly-review-log.md`.
- **Daily progress:** `/todo daily` — snapshot of today's completions, research, outreach, and pipeline. Logs to `data/job-todos-daily-log.md`.
- **Update professional identity:** After coaching sessions or moments of professional reflection, update `data/professional-identity.md`.

## Tools & Environment

**Setup (optional — only needed for PDF features):** Python 3.8+, then `pip install -r requirements.txt`

**PDF generation:**
```bash
python tools/md_to_pdf.py <input.md> [output.pdf]
```

**PDF text extraction** (processes all PDFs in `files/`):
```bash
python tools/convert_pdfs.py
```

**Outreach drafting** (`tools/open_draft.py` — auto-run by `/cold-outreach`, `/follow-up`, `/draft-email`):
Opens a pre-filled Gmail compose window in the browser. Requires no manual setup — runs automatically when an outreach draft is approved.

## Style Guidelines

See `framework/style-guidelines.md` — covers tone, language conventions, and market-specific CV format options.
