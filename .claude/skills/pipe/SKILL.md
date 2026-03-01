---
name: pipe
description: Track job applications through stages with auto-generated action items per stage
argument-hint: [add|update|remove <company> [role|stage] [url]]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Bash(PYTHONIOENCODING=utf-8 python tools/pipe_read.py:*), Bash(PYTHONIOENCODING=utf-8 python tools/pipe_write.py:*)
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
3. Run `pipe_read.py` to get structured pipeline data:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/pipe_read.py
   ```
   Use the JSON output to build the display:
   - `needs_attention[]` → "Needs Attention" table (above Full Pipeline); use `stale_label` for the Days Since Update column and note `missing_action` entries
   - `active_entries[]` → "Full Pipeline" table, most recently updated first; use each entry's `stale_label` for inline Stage annotations
   - `metrics` → summary stats header (total_active, total_stalled, stage_distribution counts)
   - `metrics.archived_count` → Archived section count (collapsed)
   - `company_index` → use in `add` command to check for duplicates before writing

### Command: `add <company> <role> [url]`

1. Call `pipe_write.py add`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/pipe_write.py add "<company>" "<role>" [--url URL] --repo-root .
   ```
2. If result `action == "duplicate_warning"`: warn user and show `existing_roles[]`. Ask if they want to add a second role anyway (if yes, re-run with `--stage` override) or update the existing entry.
3. On success: display the new entry and its auto-generated action items (see Stage Actions below).
4. **Cross-reference**: Check if this company/role appears in `.claude/skills/scan-jobs/cache.md` — if found, mention the fit score from the scan.

### Command: `update <company> <new-stage>`

1. Call `pipe_write.py update`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/pipe_write.py update "<company>" "<new-stage>" [--role ROLE] [--next-action TEXT] --repo-root .
   ```
2. If result `code == "ambiguous_match"`: show the `matches[]` list and ask user to specify `--role`.
3. If result `code == "not_found"`: tell user no active entry was found.
4. On success: display the updated entry with stage-appropriate action items and any relevant coaching links.

### Command: `remove <company>`

1. Call `pipe_write.py remove`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/pipe_write.py remove "<company>" [--role ROLE] --repo-root .
   ```
2. If `code == "ambiguous_match"`: show `matches[]` and ask user which role to remove.
3. On success: confirm removal (soft-deleted to ## Archived with Withdrawn stage).

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

**Staleness and attention logic is computed by `pipe_read.py`** — use `stale_label` and `missing_action` fields from the JSON output. Do not re-compute dates inline.
