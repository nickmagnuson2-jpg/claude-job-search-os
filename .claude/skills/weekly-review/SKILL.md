---
name: weekly-review
description: Weekly job search retrospective — pipeline health, outreach response rates, task velocity, and ranked priorities for the coming week
argument-hint: ""
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Write(data/weekly-review-log.md), Edit(data/weekly-review-log.md), Bash(python tools/todo_daily_metrics.py:*), Bash(python tools/pipeline_staleness.py:*), Bash(python tools/dossier_freshness.py:*), Bash(python tools/outreach_pending.py:*)
---

# Weekly Review — Job Search Retrospective

Runs a structured weekly retrospective covering the past 7 days. Surfaces stalled pipeline entries, outreach gaps, task velocity trends, and research activity — then produces a ranked priority list for the coming week. Auto-logs each session to `data/weekly-review-log.md`.

Takes no arguments. Always reviews the past 7 calendar days from today.

## Instructions

### Step 1: Load All Data

**Run preprocessing scripts (parallel):**
```bash
python tools/pipeline_staleness.py --target-date $(date +%Y-%m-%d) --days-threshold 7
python tools/outreach_pending.py --target-date $(date +%Y-%m-%d) --lookback-days 7
python tools/dossier_freshness.py --target-date $(date +%Y-%m-%d) --lookback-days 7
```
Parse JSON output from each script. If a script returns empty results (missing data file), continue — never fail.

**Also read in parallel (not covered by scripts):**
1. `data/job-todos.md` — for overdue count and high-priority untouched tasks (Step 4)
2. `data/job-todos-daily-log.md` — for velocity data (Step 4)

**Date window:** Today is `[today]`. The review covers `[today - 6 days]` through `[today]` (7 days inclusive). Calculate the Monday and Sunday bounding the week for the log header.

### Step 2: Analyse Pipeline Health

From `pipeline_staleness.py` JSON output:

1. **Stage distribution** — use `stage_distribution{}` to count entries per stage
2. **Stalled entries** — use `stalled_entries[]` — each entry has: `name`, `role`, `stage`, `days_since_update`. Flag with ⚠️.
3. **No-movement entries** — filter `active_entries[]` where `date_added == date_updated` — added but never advanced
4. **Days since last update** — use `days_since_update` field from each active entry

Build:
- **Stage distribution** table
- **Stalled entries** list (⚠️ markers)
- **No-movement entries** — added but never advanced
- **Days since last update** per entry

### Step 3: Analyse Outreach Activity

From `outreach_pending.py` JSON output (run with `--lookback-days 7`):

1. **Sent this week** — `recent_outreach.sent`
2. **Replied** — `recent_outreach.replied`
3. **No reply** — `recent_outreach.no_reply`
4. **Response rate** — `recent_outreach.response_rate_percent` (already computed; show "N/A" if 0 denominator)
5. **Awaiting response overdue** — `awaiting_response_overdue[]` — each entry has: `name`, `company`, `days_since_sent`

If the script returns all zeros (missing log file), note: "No outreach log found — create with `/cold-outreach` or `/follow-up`."

### Step 4: Analyse Task Completion Velocity

**Primary source:** `data/job-todos-daily-log.md` (already loaded in Step 1)

1. **This week's completions:** Find all `### YYYY-MM-DD` entries within the 7-day window. For each entry, extract the `**Completed today: N**` value from the header line. Sum all N values across the window.
2. **Prior week completions:** Find all entries in the prior 7-day window (days 8–14 ago) using the same `### YYYY-MM-DD` pattern. Sum the `**Completed today: N**` values.
3. **Overdue tasks:** Read `data/job-todos.md` — count Active rows where Due date < today and Status ≠ Done/Withdrawn.
4. **High-priority untouched:** Count Active rows with Priority = High and Status = Pending throughout the week (no completion noted this week).

**Calculate velocity trend:**
- This week vs prior week: if this week > prior week by ≥2 → ↑ Up; if lower by ≥2 → ↓ Down; otherwise → → Flat

**Graceful fallback:** If `data/job-todos-daily-log.md` has no entries for either window (first run or log not maintained), fall back to raw todo file: count items in Completed section of `data/job-todos.md` where Notes contain a date within the 7-day window. If this also yields nothing, note: "Velocity data unavailable — run `/checkout` each day to track."

### Step 5: Analyse Research Activity

From `dossier_freshness.py` JSON output:

1. **Updated this week** — `recent_dossiers.this_week[]` — each entry has: `slug`, `last_updated`
2. **Count** — `summary.updated_this_week`
3. No manual glob or file reads needed — the script handles dossier detection (identifies files where filename stem == parent directory name)

### Step 6: Generate Top 5 Priorities for Coming Week

Rank 5 specific, actionable priorities for the next 7 days. Use this scoring to rank:

1. **Blocking status** — is this preventing stage advancement? (highest weight)
2. **Time sensitivity** — overdue item or hard deadline approaching?
3. **Stage advancement potential** — would this action move a pipeline entry forward?
4. **Relationship maintenance** — contact awaiting response, follow-up window closing?
5. **Research gap** — pipeline entry with no company dossier?

For each priority, write: `[Specific action] — [Company/Contact] — [Why urgent/important]`

Examples:
- "Apply to Impossible Foods CoS role — posting may close, been researching 5 days"
- "Follow up with Alex Mullin — no reply in 6 business days"
- "Run `/research-company 'Ripple Foods'` — pipeline entry with no dossier, been stalled 9 days"
- "Complete and send Amae Health application — stuck at Researching since Feb 18"

### Step 7: Write to Weekly Log

1. Check if `data/weekly-review-log.md` exists. If not, create it with this header:
   ```markdown
   # Job Search — Weekly Review Log

   > Auto-generated by `/weekly-review`. One entry per week.
   > Newest entries first. Do not delete entries — they power trend tracking.

   ---
   ```
2. Check if an entry for this week's Monday date already exists. If so, replace it. If not, prepend a new entry after the header.

**Log entry format:**
```markdown
### Week of YYYY-MM-DD (Monday) — YYYY-MM-DD (Sunday)

**Completed: N tasks** | Outreach: N sent, N replied (N% response rate) | Pipeline: N active (N stalled)

#### Pipeline Health
| Company | Role | Stage | Days Since Update | Status |
|---------|------|-------|------------------|--------|
| Ripple Foods | Chief of Staff | Researching | 9 | ⚠️ stalled |
| Amae Health | Strategy & Ops | Researching | 5 | ⚠️ stalled |
| Impossible Foods | CoS | Researching | 0 | Active |

#### Outreach Activity
- Sent: N | Replied: N | No reply: N | Response rate: N%
- Awaiting response (>5 biz days): [Name @ Company, N days]
(omit if no outreach log)

#### Task Velocity
- Completed this week: N | Prior week: N | Trend: ↑/→/↓
- Overdue: N | High-priority untouched: N

#### Research Completed
- Company: [Name] — `output/[slug]/[slug].md`
- Industry: [Name] — `output/[slug].md`
(omit section if none this week)

#### Top 5 Priorities for Coming Week
1. [Specific action — Company/Contact — why urgent]
2. [Specific action — Company/Contact — why urgent]
3. [Specific action — Company/Contact — why urgent]
4. [Specific action — Company/Contact — why urgent]
5. [Specific action — Company/Contact — why urgent]

---
```

### Step 8: Display Full Summary

After writing the log, display the full review to the terminal:

```markdown
## Weekly Review — Week of [Monday] to [Sunday]

### Pipeline Health
**Active: N** | Researching: X | Applied: X | Phone Screen: X | Interview: X | Offer: X
**Stalled (>7 days): N**

| Company | Role | Stage | Days Since Update | Status |
|---------|------|-------|------------------|--------|
| ... | ... | ... | ... | ... |

### Outreach Activity
| Metric | Value |
|--------|-------|
| Sent this week | N |
| Replied | N |
| No reply | N |
| Response rate | N% |
| Awaiting response (>5 biz days) | [names] |

### Task Velocity
| Metric | Value |
|--------|-------|
| Completed this week | N |
| Prior week | N |
| Trend | ↑ / → / ↓ |
| Overdue tasks | N |
| High-priority untouched | N |

### Research This Week
[List of dossiers generated, or "None this week."]

### Top 5 Priorities — Coming Week
1. [Action — Company — reason]
2. [Action — Company — reason]
3. [Action — Company — reason]
4. [Action — Company — reason]
5. [Action — Company — reason]

---
Saved to: `data/weekly-review-log.md`
```

## Edge Cases

- **First run / no data files**: Run with whatever is available. If all data files are missing, display a setup message: "No job search data found yet. Start with `/pipe add` and `/todo add` to track your search, then run `/weekly-review` again."
- **No outreach log**: Skip outreach section in log entry and display. Note the gap.
- **No daily log**: Skip velocity section. Note: "Run `/checkout` each day to enable velocity tracking."
- **No pipeline entries**: Note the pipeline is empty. Suggest `/pipe add` to start tracking.
- **Partial data**: Always produce output from whatever is available. Never fail because one file is missing.
