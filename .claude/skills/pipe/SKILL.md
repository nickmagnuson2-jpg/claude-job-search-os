---
name: pipe
description: Track job applications through stages with auto-generated action items per stage
argument-hint: [add|update|remove <company> [role|stage] [url]]
user-invocable: true
allowed-tools: Read(*), Write(data/job-pipeline.md), Glob(data/*), Grep(data/*)
---

# Job Application Pipeline

Track job applications through a stage-based pipeline with auto-generated action items per stage transition.

## Arguments

- `$ARGUMENTS`: Optional. If empty, show the current pipeline summary.
  - `add <company> <role> [url]` — add a new application
  - `update <company> <new-stage>` — advance an application to a new stage
  - `remove <company>` — remove/archive an application

Examples:
- `/pipe` — show pipeline summary and what needs attention
- `/pipe add Stripe "Senior PM"` — add new application at Researching stage
- `/pipe add Notion "Product Manager" https://notion.so/careers/pm` — add with URL
- `/pipe update Stripe "Phone Screen"` — advance Stripe to Phone Screen
- `/pipe remove Stripe` — archive the Stripe application

## Data File

All pipeline data lives in `data/job-pipeline.md`.

## Instructions

### Command: Show Pipeline (no arguments)

1. Read `data/job-pipeline.md`.
2. If the file is empty or has no entries, display a welcome message:
   ```
   Pipeline is empty. Add your first application:
     /pipe add <company> <role> [url]

   Stages: Researching → Applied → Phone Screen → Interview → Offer → Accepted/Rejected/Withdrawn
   ```
3. If entries exist, build the display:
   - **Staleness calculation** — for every non-terminal entry, calculate `days_stale = today − Date Updated`. Flag entries where `days_stale > 7` with a `[⚠️ N days stale]` annotation appended inline to the Stage cell.
   - **Needs Attention** (show **above** the Full Pipeline table) — entries where `days_stale > 7` OR Next Action is blank/`—`. Use the same format as Full Pipeline but include a "Days Since Update" column.
   - **Active pipeline** — all non-terminal entries, most recently updated first, with stale annotations inline.
   - **Summary stats** — count per stage, total active, count of stalled entries.
   - **Archived** — count of terminal entries (Accepted/Rejected/Withdrawn), collapsed.

### Command: `add <company> <role> [url]`

1. Read `data/job-pipeline.md`.
2. Check for duplicates — if the company already exists in the active pipeline, warn the user and ask if they want to add a second role or update the existing one.
3. Add a new row to the pipeline table:
   - **Company**: from argument
   - **Role**: from argument
   - **Stage**: `Researching`
   - **Date Updated**: today's date (YYYY-MM-DD)
   - **Next Action**: auto-generated (see Stage Actions below)
   - **CV Used**: `—`
   - **Notes**: `—`
   - **URL**: from argument, or `—` if not provided
4. Write updated file.
5. Display the new entry and its auto-generated action items.
6. **Cross-reference**: Check if this company/role appears in `.claude/skills/scan-jobs/cache.md` — if found, mention the fit score from the scan.

### Command: `update <company> <new-stage>`

1. Read `data/job-pipeline.md`.
2. Find the matching company (case-insensitive, fuzzy). If multiple roles exist for the same company, ask which one.
3. Validate the stage transition is reasonable (warn but allow non-linear moves).
4. Update the row:
   - **Stage**: new stage
   - **Date Updated**: today's date
   - **Next Action**: auto-generated for the new stage (see Stage Actions below)
5. Write updated file.
6. Display the updated entry with stage-appropriate action items and any relevant coaching links.

### Command: `remove <company>`

1. Read `data/job-pipeline.md`.
2. Find the matching company. If multiple roles, ask which one.
3. Move the entry to the Archived section (set stage to `Withdrawn` if not already terminal).
4. Write updated file.
5. Confirm removal.

## Pipeline Stages

```
Researching → Applied → Phone Screen → Interview → Offer → Accepted
                                                         → Rejected
                                                   → Withdrawn (at any point)
```

Terminal stages: `Accepted`, `Rejected`, `Withdrawn`

## Stage Actions

Auto-generated action items when entering each stage:

### → Researching
- Research the company (product, culture, recent news)
- Check fit against your profile (`data/profile.md`)
- Identify the right CV angle for this role
- Find the job posting URL if not already captured

### → Applied
- Note which CV was used (update CV Used column)
- Set a follow-up reminder for 1 week out
- Save the job posting text for interview prep

### → Phone Screen
- Review `framework/answering-strategies/anti-patterns.md` (60-second checklist)
- Prep your recruiter pitch using `coaching/coached-answers.md`
- Review relevant coached answers for this role type
- Run `/voice-export` to practice if time allows

### → Interview
- Prep technical/behavioral answers for this specific role
- Research the team and interviewer(s) if known
- Review company-specific notes from the Researching stage
- Run a mock interview session with `/debrief` workflow

### → Offer
- Review compensation expectations from `data/profile.md`
- Compare against market rates
- Prepare negotiation points

### → Accepted
- Archive other active applications or mark as Withdrawn
- Update `data/profile.md` with new role details

### → Rejected
- Note rejection reason if known (for pattern tracking)
- Review what could be improved for similar roles

### → Withdrawn
- Note reason for withdrawing

## Display Format

When showing the pipeline, use this format:

```markdown
## Job Pipeline — [date]

**Active: N applications** | Researching: X | Applied: X | Phone Screen: X | Interview: X | Offer: X | Stalled: X

### Needs Attention
| Company | Role | Stage | Days Since Update | Next Action |
|---------|------|-------|------------------|-------------|
| Amae Health | Strategy & Ops | Researching ⚠️ | 5 days | — |
| Ripple Foods | Chief of Staff | Researching ⚠️ | 9 days | Follow up with recruiter |

### Full Pipeline
| Company | Role | Stage | Updated | Next Action | CV | URL |
|---------|------|-------|---------|-------------|-----|-----|
| Amae Health | Strategy & Ops | Researching ⚠️ [5 days stale] | 2026-02-18 | — | — | — |
| Ripple Foods | Chief of Staff | Researching ⚠️ [9 days stale] | 2026-02-14 | Follow up | — | — |
| Impossible Foods | CoS | Researching | 2026-02-23 | Research role | — | — |

### Archived (N)
[collapsed — show count only unless user asks]
```

Sort by Date Updated descending (most recent first).

**Staleness rules:**
- `days_stale = today − Date Updated` (calendar days, not business days)
- `days_stale > 7` → stale: append `⚠️ [N days stale]` to Stage cell in Full Pipeline; show in Needs Attention
- `days_stale ≤ 7` → no annotation
- Blank or `—` Next Action → always include in Needs Attention (regardless of staleness)
- An entry can appear in Needs Attention for both reasons simultaneously — show one row, note both issues
- Terminal stages (Accepted/Rejected/Withdrawn) are excluded from staleness checks
