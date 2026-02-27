---
name: standup
description: Morning briefing — pipeline health, today's top 3 actions, pending outreach, and a momentum read of the search state
argument-hint: [none]
user-invocable: true
allowed-tools: Read(*), Glob(inbox/*), Bash(python tools/pipeline_staleness.py:*), Bash(python tools/outreach_pending.py:*), Bash(python tools/networking_followup.py:*)
---

# Standup — Morning Briefing

Reads five data files and generates a focused daily brief. Output is in-chat only — nothing is written to disk.

## Instructions

### Step 1: Load Data

**Run preprocessing scripts (parallel):**
```bash
python tools/pipeline_staleness.py --target-date $(date +%Y-%m-%d)
python tools/outreach_pending.py --target-date $(date +%Y-%m-%d)
python tools/networking_followup.py --target-date $(date +%Y-%m-%d)
```
Parse JSON output from each script. If a script returns empty results (missing data file), continue — never fail.

**Also read in parallel:**
1. `data/goals.md` — current phase, this week's focus, search thesis
2. `data/job-todos.md` — active to-do list with priorities and due dates
3. `data/job-todos-daily-log.md` — daily progress log (for checkout nudge check)

Also check `inbox/` for any captured items: `Glob(inbox/*)` — list filenames only.

**Checkout nudge check (run after loading daily log):**
Check if an entry exists for **yesterday's date** (look for `### YYYY-MM-DD` header matching yesterday). If no entry for yesterday is found, prepend this one-line nudge at the very top of the brief output (before the date header):

```
💡 Yesterday's checkout wasn't logged — run `/checkout` when wrapping up today.
```

If yesterday's entry exists, skip silently.

### Step 2: Analyze Each Data Source

**From goals.md:**
- Extract: current phase (Exploring / Active / Interviewing / Negotiating)
- Extract: this week's top 3 focus items
- Extract: search thesis (one-sentence version)
- If goals.md is all TODOs or missing: flag "⚠️ goals.md not populated — run `/standup` after filling in `data/goals.md`" and skip goals-dependent sections

**From pipeline_staleness.py JSON:**
- `stalled_entries[]` — each entry has: `name`, `role`, `stage`, `days_since_update`. Use for the "Attention Needed" list.
- Suggested action per stage: Researching → "run `/research-company` or move on"; Applied → "follow up or check status"; Screening → "send thank-you / follow up"; Interview → "follow up on timeline"; Offer → "respond or negotiate"
- `stage_distribution{}` — use for the pipeline snapshot count (N per stage)
- `metrics.total_active` — total active entries

**From job-todos.md:**
- Extract all Pending to-dos, sorted by: (1) overdue — due date has passed, (2) high priority, (3) due today or this week
- Take the top 3 for "Today's Top 3"
- Note total pending count

**From outreach_pending.py JSON:**
- `awaiting_response_overdue[]` + `awaiting_response[]` — sorted by `days_since_sent` descending (oldest first)
- Each entry has: `name`, `company`, `channel`, `days_since_sent`
- Show overdue entries first, then non-overdue awaiting entries

**From networking_followup.py JSON:**
- `followup_overdue[]` — contacts with overdue follow-ups (show first)
- `followup_due[]` — contacts with follow-ups due within 7 days (show after overdue)
- Each entry has: `name`, `company`, `follow_up_action`

**From inbox/:**
- Count items in `inbox/` (files that aren't README.md)
- If any items exist, flag them with: "N items in inbox/ — run `/act` to process"

### Step 3: Generate the Brief

Output the brief in this exact format:

```markdown
## Morning Brief — [Today's date, spelled out: e.g., Tuesday, February 25]

**Search thesis:** [one sentence from goals.md, or "— not set" if missing]
**Current phase:** [phase from goals.md, or "— not set"]

---

### This Week's Focus
[Numbered list from goals.md weekly focus section, or "— goals.md not populated" if missing]

---

### Pipeline — Attention Needed
[For each stale item:]
- **[Company]** — [stage] for [N] days → [suggested action]

[If no stale items:]
> Pipeline looks healthy — no items overdue for action.

**Snapshot:** [N] Researching · [N] Applied · [N] Screening · [N] Interviewing · [N] Offer

---

### Today's Top 3
1. [Highest priority todo with due date if set]
2. [Second priority]
3. [Third priority]

> [N] total pending to-dos — run `/todo` to see the full list.

---

### Awaiting Response
[For each outreach awaiting reply:]
- **[Name]** @ **[Company]** — sent [N] days ago ([channel: LinkedIn/email])

[If nothing pending:]
> No outreach awaiting response (last 30 days).

---

### Follow-Up Due
[For each contact with overdue follow-up:]
- **[Name]** ([Company]) — [what to do]

[If nothing due:]
> No follow-ups overdue.

---

### Inbox
[If inbox items exist:]
> **[N] items captured in inbox/** — run `/act` to review and route them.

[If inbox is empty or doesn't exist:]
> Inbox clear.

---

### Momentum Read
[1–2 honest sentences assessing search momentum based on the data above.
Be direct — if momentum is stalling, say so. If it's strong, say that.
Base it on: pipeline activity, outreach response rate, todo completion pace.
Examples:
- "Pipeline is active with 4 companies in motion, but 2 applications are stale — outreach would unblock both. Outreach response rate looks solid."
- "Search has slowed — no new applications this week and 3 todos are overdue. Worth a focused session today."
- "Strong week — pipeline moving at all stages and outreach follow-ups are current."]
```

### Step 4: Suggest One Action

After the brief, add a single suggested action — the one thing most likely to move the search forward today based on what you found:

```
**One thing:** [Specific, actionable — e.g., "Follow up with Amae Health (applied 6 days ago) — run `/follow-up 'Amae'`" or "Process inbox items before anything else — run `/act`"]
```

## Edge Cases

- **goals.md all TODOs:** Proceed with other sections; note goals.md is unpopulated at the top of the brief and skip the thesis/phase/focus sections.
- **job-pipeline.md missing:** Skip pipeline section, note "pipeline.md not found — run `/pipe add` to start tracking."
- **job-todos.md missing or empty:** Show "No active to-dos — run `/todo add` to capture actions."
- **outreach-log.md missing:** Skip awaiting-response section, note "No outreach log found."
- **networking.md missing:** Skip follow-ups section.
- **All files missing:** Display "⚠️ No data files found. Start with `/import-cv` to populate your profile, then `/pipe add` to track your first application."
- **No stale pipeline items:** Show the healthy message.
- **No todos:** Note it and suggest adding some.
