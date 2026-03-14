<!-- This file is the orchestration brain — Claude reads it on every session.
     Personal details live in data/profile.md (gitignored).
     Everything else works as-is. -->

# AI Job Search System

## Purpose

End-to-end job search operating system — see [README.md](README.md) for the full description.

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
├── CLAUDE.md              # This file — project instructions
├── framework/             # Workflows, methodologies, style guides, answering strategies, templates
├── coaching/              # Coached answers, anti-pattern tracker, pressure points, session logs
│   ├── coached-answers/   #   Practiced answers by question type
│   ├── pressure-points/   #   Weakness areas with mitigation strategies
│   ├── anti-pattern-tracker.md
│   └── progress-recruiter/#   Session logs per company
├── data/                  # Owner data: profile, projects, skills, pipeline, networking, todos
│   ├── company-notes/     #   Personal notes per company (read by generative skills)
│   ├── industry-notes/    #   Personal notes per industry (read by generative skills)
│   ├── project-background/#   Sensitive background (NEVER use in CVs)
│   └── projects/          #   One file per project/engagement
├── plugins/               # Drop-in extensions
├── examples/              # Fictional example data
├── .claude/skills/        # 27 skill definitions powering slash commands
├── memory/                # MEMORY.md (auto-loaded), lessons.md, archive files
├── docs/                  # See docs/README.md for full index
├── tools/                 # Python scripts: PDF, preprocessing, atomic writes, n8n wrappers
└── output/                # All generated output — company-first hierarchy (see conventions below)
```

For full usage guide and skill reference, see `docs/usage.md`.

## Output Files — Conventions

**Company-first hierarchy:** Every named entity (company or industry) gets its own subfolder under `output/`. Subfolder = slug (lowercase, hyphens): `impossible-foods`, `behavioral-health-tech`.

- Dossier matches folder name: `output/<slug>/<slug>.md` (no date prefix — versioned in place)
- All other files use date prefix: `output/<slug>/MMDDYY-[descriptor].md`
- Flat `output/MMDDYY-*.md` only for one-off outputs with no associated entity

Examples: `output/impossible-foods/022426-chief-of-staff.md` (CV), `output/impossible-foods/022426-cover-letter.md` (cover letter), `output/behavioral-health-tech/behavioral-health-tech.md` (dossier).

**`data/company-notes/<slug>.md`** — Free-form personal context per company (recruiter calls, observations, prep). Lives in `data/` so generative skills read it automatically. Append entries with `## YYYY-MM-DD | [context]` header.

**`data/industry-notes/<slug>.md`** — Same pattern for industries.

## Data Files — Conventions

### Write-Only Files (never use Edit)

The Edit tool silently fails on markdown files with table rows >500 characters. These files **must use Write (read-then-write full file)**:

- `data/job-todos.md` — **mutations must use `tools/todo_write.py`** (add/done/clear/sync)
- `data/job-pipeline.md` — pipeline rows with long Notes cells
- All output dossiers (`output/**/*.md`)

A `PostToolUse` hook warns when Edit is used on an affected file. If you see the warning, re-read and Write the full content.

### Content Exclusions for CVs/Resumes

**`data/project-background/` contents must NEVER appear in CVs, resumes, or client-facing materials.** Internal reference only.

### Projects (`data/projects/*.md`)

Each project follows the structure in `framework/templates/project.md`: Period, Role, Client, Industry, Location, Type, Description, Responsibilities, Key Achievements, Technologies, Tags.

**Type values:** `flagship` | `consulting` | `contract` | `employment` | `co-founded` | `internship` | `side-project`.

## Research Dossier Standards

Applies to `/research-company` and `/research-industry`. Both produce dossiers with a **two-speed reading design:**

1. **Executive Summary** — thesis, opportunity rating, top reasons/risks, next action. Scan in 2 minutes.
2. **Full dossier** — every section opens with a bold **BLUF** sentence. Scan all BLUFs in 60 seconds.

### Evidence Quality Rules

- **Source tiers:** Tier A (primary/official), Tier B (reputable secondary), Tier C (aggregator/crowd — flagged).
- **Confidence tags:** High-impact claims include `[Confidence: High | Medium | Low, as of YYYY-MM]`.
- **Contradictions:** Show both values with sources, mark `[Needs verification]`.
- **Self-reported metrics:** Always qualify company-sourced outcomes with "they report" / "self-reported." Never present as independently verified without external corroboration.
- **Self-verification:** Both skills include Evidence Summary Table and contradiction audit.

### Refresh & Handoff

- Fresh dossier (<14 days): offer "view existing" or "refresh." On refresh, include `## What Changed`.
- Flow: `/research-industry` → `/research-company` → `/cold-outreach` or `/follow-up`.

## Resume Generation

Shared standards (tailoring rules, 16-point quality checklist, cheat sheet) live in `framework/application-workflow.md`. Referenced by `/generate-cv`, `/apply`, `/cover-letter`.

## Interview Training

See `framework/interview-workflow.md` for session workflow, coaching rules, and progress logging.

Six answering strategy files in `framework/answering-strategies/` cover recruiter call techniques (blank mind protocol, gap reframing, pressure defense, question-back, anti-patterns, direct answer structure). Each has a Quick Overview for pre-call scanning.

**Voice simulation:** `/voice-export` (generate prompt) → practice in Claude App → `/debrief` (analyze transcript).

## Tools & Environment

**Setup:** Python 3.8+, `pip install -r requirements.txt` (only for PDF features).

**Key commands:**
```bash
python tools/md_to_pdf.py <input.md> [output.pdf]              # CV to PDF
python tools/convert_pdfs.py                                     # Extract text from PDFs in files/
PYTHONIOENCODING=utf-8 python tools/todo_write.py add "Task" Med 2026-03-01 "--"
```

**Atomic write scripts** (all return JSON, all use `PYTHONIOENCODING=utf-8 python tools/<script>.py`):
- `--repo-root .` flag: `todo_write.py` accepts it anywhere; `pipe_write.py` and `networking_write.py` require it **before** the subcommand (e.g., `pipe_write.py --repo-root . update ...`)
- `todo_write.py` — add/done/clear/sync for `data/job-todos.md`
- `pipe_write.py` — add/update/remove for `data/job-pipeline.md`
- `networking_write.py` — add/log/remove for `data/networking.md`; `log` auto-updates `data/outreach-log.md` status on reply detection
- `remember_apply.py` — routes notes to 8 destinations
- `act_apply.py` — pipeline-add/contact-add/notes-add for inbox routing

### Email Drafts

**Use `tools/open_draft.py` for sending emails** — not the Google MCP integration (insufficient permissions for draft creation). Write the draft to `tools/.pending-draft.txt` in this format, then run `PYTHONIOENCODING=utf-8 python tools/open_draft.py`:

```
TO: recipient@example.com
SUBJECT: Subject line
BODY:
Email body here
```

This opens a Gmail compose window in the browser with fields pre-filled.

### Post-Interview Workflow

After any recruiter screen or interview:
1. Update `data/company-notes/<slug>.md` with call intel (newest entry at top)
2. Update pipeline via `pipe_write.py` (stage, next-action, notes)
3. Log interaction via `networking_write.py log`
4. Add follow-up todo via `todo_write.py add`
5. Draft thank-you email via `tools/open_draft.py`
6. If mock session preceded the call, update session file in `coaching/progress-recruiter/`

### Gotchas

- **Windows encoding:** ALL `tools/*.py` scripts require `PYTHONIOENCODING=utf-8` prefix or they crash on Unicode.
- **Separator row noise:** Script output includes `{"task": "---"}` entries from markdown table separators. Filter with `[e for e in entries if e.get("task") != "---"]`.
- **Edit safety hook:** `.claude/settings.json` runs `tools/check_edit_safety.py` after every Edit on `.md` files.

### Background Automation (n8n)

Start via `tools\run_n8n.bat` (sets `NODES_EXCLUDE=[]`; bare `n8n start` breaks Execute Command). Dashboard: http://localhost:5678

| Workflow | Schedule | What it does |
|----------|----------|--------------|
| Gmail Fetch | Every 15 min | `gmail_fetch.py` → `inbox/` |
| Standup Cache Warm | Weekdays 8am | `act_classify.py` + `pipeline_staleness.py` → `tools/.cache/` |
| Follow-up Nudge + Dossier Freshness | Daily 9am | Writes inbox items if overdue/stale |
| Weekly Review Reminder | Friday 4pm | Writes weekly-review reminder to `inbox/` |

## Memory Hygiene

MEMORY.md is loaded into every conversation. Keep it under 100 lines of active context.

**Archive resolved sections** to `memory/archive-YYYY-MM.md` when:
- A skill change, bug fix, or migration is completed and merged — the codebase is the source of truth
- A search lead is resolved (rejected, withdrawn, dead) — move to archive with outcome
- A "new feature" note has been stable for >2 weeks — it's no longer new
- Session-specific reminders have passed their date

**Keep in MEMORY.md** only:
- Active search context (live leads, upcoming interviews)
- Stable architectural patterns needed across sessions
- Known bugs not yet fixed
- User preferences
- Critical personal context (employment status, framing rules)

## Style Guidelines

See `framework/style-guidelines.md` — covers tone, language conventions, and CV format options.
