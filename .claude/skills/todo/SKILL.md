---
name: todo
description: Lightweight to-do list for job search tasks not tied to specific applications
argument-hint: [add|done|daily|clear <task> [priority] [due]]
user-invocable: true
allowed-tools: Read(*), Write(data/job-todos.md), Edit(data/job-todos.md), Read(data/job-pipeline.md), Read(data/networking.md), Write(data/job-todos-daily-log.md), Edit(data/job-todos-daily-log.md)
---

# Job Search To-Do Manager

Lightweight to-do list for job search tasks — networking follow-ups, company research, prep work, skill gaps, outreach, etc. Cross-references the job pipeline and networking contacts to show context. Tracks daily progress with an archivable summary.

## Arguments

- `$ARGUMENTS`: Optional. If empty, show active to-dos.
  - `add <task> [priority] [due]` — add a new to-do
  - `done <task>` — mark a to-do as complete and archive it
  - `daily` — generate today's summary, archive it, and show progress trends
  - `clear` — move all completed items to archive

Examples:
- `/todo` — show active to-dos sorted by priority, with pipeline/contact links
- `/todo add "Update LinkedIn headline" High` — add high-priority to-do
- `/todo add "Research PM salary benchmarks" Med 2026-02-25` — add with due date
- `/todo add "Follow up with recruiter at Acme"` — add with default Med priority
- `/todo done "Update LinkedIn headline"` — mark complete
- `/todo daily` — generate and archive today's progress summary
- `/todo clear` — archive all completed items

## Data Files

- **To-do data:** `data/job-todos.md`
- **Daily log:** `data/job-todos-daily-log.md` — archived daily summaries for progress tracking

## Instructions

### Cross-Referencing: Pipeline & Networking Links

Every time to-dos are displayed (by any command), cross-reference the task text against:

1. **`data/job-pipeline.md`** — scan each to-do's task text for company names that appear in the active pipeline. If found, annotate with the pipeline stage.
2. **`data/networking.md`** — scan each to-do's task text for contact names that appear in the contacts table. If found, annotate with the contact's company, relationship type, and last interaction date.

**How to match:**
- Extract all company names from the pipeline's Company column and all contact names from the networking Contacts table.
- For each to-do, check if any company name or contact name appears as a substring in the Task or Notes fields (case-insensitive).
- **Use full names for matching** — match "Amae Health" not just "Amae", match "Alex Mullin" not just "Alex". This prevents false positives on partial name matches.
- A to-do can link to both a pipeline entry and a contact.
- **If pipeline or networking files don't exist**, skip that cross-reference source silently — never fail because a file is missing.

**Display format for links** (appended below each linked to-do in the list):

```
  3. Prepare your "why mental health" narrative for Alex coffee chat [High | Due: 2026-02-25]
     > Pipeline: Amae Health — Strategy & Operations [Researching]
     > Contact: Alex Mullin — Product Lead @ Amae Health (peer) — last: 2026-02-18
```

If a to-do has no links, display it normally without annotation lines.

### Command: Show To-Dos (no arguments)

1. Read these files in parallel:
   - `data/job-todos.md`
   - `data/job-pipeline.md`
   - `data/networking.md`
2. If `job-todos.md` is empty or has no entries, display a welcome message:
   ```
   No to-dos yet. Add your first one:
     /todo add <task> [High|Med|Low] [YYYY-MM-DD]

   Examples of job search to-dos:
   - Update LinkedIn headline and summary
   - Research target companies list
   - Follow up with recruiter at [company]
   - Close skill gap: [specific skill]
   - Update portfolio/GitHub profile
   - Network: reach out to [person]
   ```
3. If entries exist, build the cross-reference links (see above), then display active to-dos sorted by priority (High → Med → Low), then by due date (soonest first, no-date last):
   ```markdown
   ## Job Search To-Dos — [date]

   **Active: N** | High: X | Med: X | Low: X | Overdue: X

   1. Task description [Priority | Due: date | Status]
      > Pipeline: Company — Role [Stage]
      > Contact: Name — Role @ Company (relationship) — last: date

   2. Task description [Priority | Due: date | Status]

   ---
   Completed (archived): N items
   ```
4. Flag any overdue items (due date < today) with a warning marker.
5. **Pipeline summary** — at the bottom, show a brief cross-reference of how many to-dos are linked to each active pipeline entry:
   ```
   Pipeline links: Amae Health (4 to-dos) | Stripe (1 to-do)
   ```

### Command: `add <task> [priority] [due]`

1. Read `data/job-todos.md`.
2. Parse arguments:
   - **Task**: required — the to-do description (quoted string or unquoted text before priority/date)
   - **Priority**: optional — `High`, `Med`, or `Low`. Default: `Med`
   - **Due**: optional — date in YYYY-MM-DD format. Default: `—`
3. Check for duplicates (fuzzy match on task text). Warn if similar item exists.
4. Add a new row to the Active section:
   - **Task**: from argument
   - **Priority**: from argument or `Med`
   - **Due**: from argument or `—`
   - **Status**: `Pending`
   - **Notes**: `—`
5. Write updated file.
6. Read pipeline and networking files, then display the added item with any cross-reference links detected.

### Command: `done <task>`

1. Read `data/job-todos.md`.
2. Find the matching task (case-insensitive, fuzzy match — match on substring if unambiguous).
3. If multiple matches, ask the user to clarify.
4. Update the item:
   - **Status**: `Done`
   - Add completion date to Notes (format: `Completed 2026-02-18`)
5. Move the row from Active to Completed section.
6. Write updated file.
7. Confirm completion and show remaining active count.

### Command: `daily`

Generate a daily progress summary, archive it, and show trends.

1. Read these files in parallel:
   - `data/job-todos.md`
   - `data/job-todos-daily-log.md` (create if doesn't exist)
   - `data/job-pipeline.md`
   - `data/networking.md`

2. **Build today's snapshot:**
   - Count items completed today (check Notes for today's date in Completed section)
   - Count items still active, grouped by priority
   - Count overdue items
   - Identify items that moved to "In Progress" today
   - List pipeline entries with their current stages
   - List networking contacts with pending follow-up to-dos

3. **Check for existing entry:** If today's date already has an entry in the daily log, update it (replace) rather than creating a duplicate.

4. **Write daily log entry** — append (or replace) today's entry in `data/job-todos-daily-log.md`:

   ```markdown
   ### 2026-02-18 (Tuesday)

   **Completed today: N** | Active remaining: N | Overdue: N

   #### Done
   - [x] Task description (Priority)
   - [x] Task description (Priority)

   #### Still Active
   - [ ] Task description [Priority | Due: date]
   - [ ] Task description [Priority | Due: date]

   #### Pipeline Snapshot
   | Company | Role | Stage |
   |---------|------|-------|
   | Amae Health | Strategy & Operations | Researching |

   #### Networking Activity
   - Alex Mullin (Amae Health) — last: 2026-02-18, pending follow-up to-dos: 2
   ```

5. **Calculate trends** from the daily log history:
   - **Completion rate:** items completed per day over the last 7 and 30 days
   - **Streak:** consecutive days with at least 1 item completed
   - **Total completed:** all-time count from the log
   - **Velocity:** average completions per active day (days with at least 1 completion)
   - **Overdue trend:** is the overdue count growing, shrinking, or stable?

6. **Display the daily summary:**

   ```markdown
   ## Daily Summary — [date]

   ### Today
   Completed: N | Still active: N | Overdue: N

   #### Done Today
   - [x] Task 1
   - [x] Task 2
   (or "Nothing completed yet today." if zero)

   #### Top Priority Remaining
   - [ ] Highest priority / most urgent items (up to 5)

   ### Progress
   | Metric | Value |
   |--------|-------|
   | Streak | N days |
   | This week | N completed |
   | Last 7 days | N completed |
   | Last 30 days | N completed |
   | All-time | N completed |
   | Avg per active day | N.N |
   | Overdue trend | ↑ growing / → stable / ↓ shrinking |

   ### Pipeline Activity
   | Company | Role | Stage | Related To-Dos |
   |---------|------|-------|----------------|
   | Amae Health | Strategy & Operations | Researching | 4 active |
   ```

### Command: `clear`

1. Read `data/job-todos.md`.
2. Identify all items with Status = `Done` still in the Active section (if any).
3. Move them to the Completed section.
4. Write updated file.
5. Report how many items were archived.

## Daily Log File Format

`data/job-todos-daily-log.md` is a log with newest entries at the top that accumulates daily snapshots. It enables progress tracking across days and weeks.

```markdown
# Job Search — Daily Progress Log

> Auto-generated by `/todo daily`. One entry per day.
> Newest entries first. Do not delete entries — they power progress tracking.

### 2026-02-18 (Tuesday)

**Completed today: 2** | Active remaining: 4 | Overdue: 0

#### Done
- [x] Read Quiet Capital's Amae investment thesis (Med)
- [x] Update LinkedIn headline (High)

#### Still Active
- [ ] Prepare "why mental health" narrative [High | Due: 2026-02-25]
- [ ] Follow up: Alex Mullin @ Amae Health [Med | Due: 2026-02-25]
- [ ] Read Alex Mullin's OUD white paper [Med | Due: 2026-02-25]
- [ ] Review Amae Health open roles [Med | Due: 2026-02-25]

#### Pipeline Snapshot
| Company | Role | Stage |
|---------|------|-------|
| Amae Health | Strategy & Operations | Researching |

#### Networking Activity
- Alex Mullin (Amae Health, peer) — last: 2026-02-18, pending to-dos: 2

---

### 2026-02-17 (Monday)
...
```

**Streak calculation:** A "streak" is consecutive calendar days (no gaps) where at least one item was completed. Weekends count — if Saturday and Sunday have no completions, the streak resets on Monday. This incentivizes consistent daily progress.

**First run:** If `data/job-todos-daily-log.md` doesn't exist, create it with the header and today's entry. Display trends as "N/A — first day tracked" for metrics that need history.

## Priority Levels

- **High** — blocking progress or time-sensitive (e.g., follow up before deadline, fix broken profile)
- **Med** — important but not urgent (e.g., update LinkedIn, research companies)
- **Low** — nice-to-have, do when there's time (e.g., optimize portfolio, read industry articles)

## Display Format

### To-Do List (shown by `/todo` with no args)

```markdown
## Job Search To-Dos — [date]

**Active: 5** | High: 1 | Med: 4 | Low: 0 | Overdue: 0

1. Prepare your "why mental health" narrative for Alex coffee chat [High | Due: 2026-02-25 | Pending]

2. Follow up: Alex Mullin @ Amae Health — Check for response [Med | Due: 2026-02-25 | Pending]
   > Pipeline: Amae Health — Strategy & Operations [Researching]
   > Contact: Alex Mullin — Product Lead @ Amae Health (peer) — last: 2026-02-18

3. Read Alex Mullin's white paper on OUD treatment [Med | Due: 2026-02-25 | Pending]
   > Contact: Alex Mullin — Product Lead @ Amae Health (peer) — last: 2026-02-18

4. Read Quiet Capital's Amae investment thesis [Med | Due: 2026-02-25 | Pending]

5. Review Amae Health open roles for fit [Med | Due: 2026-02-25 | Pending]
   > Pipeline: Amae Health — Strategy & Operations [Researching]

---
Pipeline links: Amae Health (2 to-dos)
Completed (archived): 0 items
```

Note: Cross-referencing uses **full name matching** — "Alex" alone won't match "Alex Mullin", and "Amae" alone won't match "Amae Health". Only to-dos containing the exact full company name or contact name get annotated.

Sort Active by: Priority (High → Med → Low), then Due date (soonest first, `—` last).
