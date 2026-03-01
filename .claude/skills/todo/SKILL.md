---
name: todo
description: Lightweight to-do list for job search tasks not tied to specific applications
argument-hint: [add|done|daily|clear <task> [priority] [due]]
user-invocable: true
allowed-tools: Read(*), Read(data/job-pipeline.md), Read(data/networking.md), Read(data/job-todos-daily-log.md), Read(data/outreach-log.md), Glob(output/**), Bash(python tools/todo_write.py:*)
---

# Job Search To-Do Manager

Lightweight to-do list for job search tasks — networking follow-ups, company research, prep work, skill gaps, outreach, etc. Cross-references the job pipeline and networking contacts to show context. Tracks daily progress with an archivable summary.

## Arguments

- `$ARGUMENTS`: Optional. If empty, show active to-dos.
  - `add <task> [priority] [due]` — add a new to-do
  - `done <task>` — mark a to-do as complete and archive it
  - `daily` — **Replaced by `/checkout`** — use `/checkout` for end-of-day logging and daily summary
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

### Pipeline Sync (auto-run before any display)

Before displaying to-dos in **any command**, run the sync script:

```bash
PYTHONIOENCODING=utf-8 python tools/todo_write.py sync
```

Parse the JSON result:
- If `withdrawn > 0`, display a notice **above** the to-do list:
  ```
  ⚡ Pipeline sync: N to-do(s) auto-withdrawn — [Company] marked [Stage]
  ```
  (Use `summary` from the JSON for the notice text.)
- If `withdrawn == 0` (or `action` is `sync` with no withdrawals), skip silently.

Then read `data/job-todos.md` (it may have been updated by the sync) along with `data/job-pipeline.md` and `data/networking.md` for display.

### Command: Show To-Dos (no arguments)

1. Run **Pipeline Sync** (above) first.
2. Read these files in parallel:
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

1. Parse `$ARGUMENTS` to extract:
   - **Task** (required) — the to-do description
   - **Priority** (optional, default: `Med`) — `High`, `Med`, or `Low`
   - **Due** (optional, default: `--`) — date in YYYY-MM-DD format
2. Run the write script (substitute extracted values; use `--` for missing priority/due):
   ```bash
   PYTHONIOENCODING=utf-8 python tools/todo_write.py add "TASK" "PRIORITY" "DUE" "--"
   ```
   Use `--` as a placeholder for any omitted argument.
3. Parse JSON result:
   - If `status: error`, show the message and stop.
   - If `warning` key is present, display: `⚠ Note: {warning}`
4. Read `data/job-pipeline.md` and `data/networking.md`, build cross-reference links for the added task, then confirm:
   ```
   ✓ Added: TASK [PRIORITY | Due: DUE]
   > Pipeline: ... (if matched)
   > Contact: ... (if matched)
   ```

### Command: `done <task>`

1. Extract the task fragment from `$ARGUMENTS`.
2. Run the write script:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/todo_write.py done "FRAGMENT"
   ```
3. Parse JSON result:
   - If `status: error` and message mentions "Multiple matches", show the listed options and ask the user to be more specific. Stop.
   - If `status: error` and message mentions "No task found", show the error and stop.
   - If `status: ok`, confirm using values from the JSON:
     ```
     ✓ Completed: TASK
     N tasks remaining active.
     ```

### Command: `daily`

> **Replaced by `/checkout`** — use `/checkout` for end-of-day logging and daily progress summary. The `daily` command is no longer supported here.
>
> If the user runs `/todo daily`, display this message and stop:
> ```
> The `daily` command has moved. Run `/checkout` for end-of-day logging, progress trends, and tomorrow's top 3 priorities.
> ```

### Command: `clear`

1. Run the write script:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/todo_write.py clear
   ```
2. Parse JSON result:
   - If `archived: 0`, display: `Nothing to archive — no Done or Withdrawn items in Active section.`
   - Otherwise, report using `done` and `withdrawn` counts from the JSON:
     ```
     Archived N items: X Done, Y Withdrawn.
     ```

## Daily Log File Format

`data/job-todos-daily-log.md` is a log with newest entries at the top that accumulates daily snapshots. It enables progress tracking across days and weeks.

```markdown
# Job Search — Daily Progress Log

> Auto-generated by `/checkout`. One entry per day.
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

#### Research Completed
- Company: Ripple Foods (output/ripple-foods/ripple-foods.md)

#### Outreach Sent
- Becky O'Grady @ Ripple Foods — email — "Ripple's next chapter"

#### System Changes
- Self-improvement loop repairs + email tone clarity — 3 bugs fixed, `memory/lessons.md` created

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

## Status Values

- **Pending** — not yet started
- **In Progress** — actively being worked on
- **Done** — genuinely completed — the task was accomplished (counts toward velocity and completion stats)
- **Withdrawn** — deprioritized, cancelled, or made irrelevant (e.g., company pipeline entry withdrawn, role no longer exists, plan changed) — does NOT count as a completion; excluded from velocity and streak calculations

Use `Withdrawn` instead of `Done` for anything that did not result in real work or output. This keeps completion stats honest — `Done` represents actual accomplishment, `Withdrawn` represents things that fell away.

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
