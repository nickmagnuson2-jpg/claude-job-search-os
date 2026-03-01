---
name: remember
description: Capture a note mid-session and route it to the right data file — contacts, pipeline, profile, or a general decision log
argument-hint: "<note text>"
user-invocable: true
allowed-tools: Read(*), Write(data/networking.md), Write(data/job-pipeline.md), Edit(data/profile.md), Write(data/job-todos.md), Write(data/notes.md), Edit(data/notes.md), Write(inbox/*), Glob(inbox/*), Write(output/**), Edit(data/outreach-log.md), Bash(PYTHONIOENCODING=utf-8 python tools/remember_classify.py:*)
---

# Remember — Capture Notes Mid-Session

Quickly saves something you want to retain across sessions, routed to the right file. No setup required — just tell Claude what to remember.

## Arguments

- `$ARGUMENTS` (required): The note to capture. Plain language — no special format needed.

Examples:
- `/remember "Alex mentioned Amae is building a new clinic in Sacramento"` → appended to Alex Mullin's contact entry in networking.md
- `/remember "Alex Mullin replied to my email"` → updates outreach-log.md Status to Replied for most recent Alex Mullin row
- `/remember "heard back from Sarah Chen, she's happy to connect"` → updates outreach-log.md Status to Replied + logs contact note
- `/remember "comp floor is $130K in practice, not $140K"` → appended to profile.md compensation section
- `/remember "decided not to pursue Two Chairs — too similar to what I want to leave"` → appended to pipeline notes or decision log
- `/remember "Amae Series B was pre-empted by Altos, not a standard raise"` → appended to company research dossier or pipeline notes
- `/remember "recruiter at Talkiatry is Sarah Kim, reached out on LinkedIn"` → creates or updates networking contact entry

## Instructions

### Step 1: Classify the Note

Run `remember_classify.py` with the note text from `$ARGUMENTS`:

```bash
PYTHONIOENCODING=utf-8 python tools/remember_classify.py --note "[escaped $ARGUMENTS]"
```

Parse the JSON output:
- `destinations[]` — list of routing destinations, each with `type`, `file`, `entity` (matched contact or company name), and `slug` (for company-notes paths)
- `ambiguous` — if `true`, default to `data/notes.md` and flag the routing as uncertain in the Step 4 confirmation

Proceed to Step 2 with the resolved `destinations[]`. If the script fails or returns an empty destinations list, fall back to `data/notes.md` as general_note.

### Step 2: Read Target File(s)

Read the target file(s) identified in Step 1. Always read before writing.

If `data/notes.md` doesn't exist, it will be created in Step 3.

### Step 3: Write the Note

**For `data/outreach-log.md` (outreach reply):**
Read `data/outreach-log.md`. Scan all rows for the contact name (case-insensitive full-name substring match on the Recipient column). Find the most recent row where Status is `Drafted` or `Sent`.
- If found: update that row's Status cell to `Replied`.
- If no matching `Drafted`/`Sent` row found: write to `data/networking.md` instead (treat as a contact note) and note: "No outreach-log entry found for [Name] — logged to networking.md."
- An outreach reply note often also contains content that qualifies as a **Contact note** — if so, write to both files. Apply full-name matching rules.

**For `data/networking.md` (contact note):**
Find the matching contact's row or section. Append to their interaction log or notes field:
```
[YYYY-MM-DD] [Note text]
```
If the contact doesn't exist yet, add a minimal entry:
```
| [Name] | [Company if mentioned] | [Role if mentioned] | — | — | — | [Date] | Note from /remember: [note text] |
```
and suggest: "Contact added. Run `/networking add` to fill in full details."

**For `data/job-pipeline.md` (pipeline note):**
Find the matching company's row. Append to the Notes cell:
```
[Date]: [note text]
```

**For `data/profile.md` (profile update):**
Find the most relevant section (Compensation, Availability, Preferences, etc.) and append the note with a date stamp. If the note updates an existing value (e.g., changes comp floor), update the existing value and add a note showing what changed and when.

**For company notes (company note):**
Write to `data/company-notes/<slug>.md`. If the file doesn't exist, create it:
```markdown
# [Company Name] — Notes

> Running log of raw notes, call prep, and observations.
> Newest entries at the top. One section per date + context.

---
```
Then prepend a new entry at the top of the log (after the header block):
```
## [YYYY-MM-DD] | [context — infer from note text, e.g., "Recruiter call", "Video note", "General"]
[Note text]
```

**For `inbox/` (raw capture):**
Create a new file: `inbox/YYYYMMDD-HHMMSS-[slug].md` where slug is 2–4 words from the note (lowercase, hyphenated). Content:
```markdown
# [Note text, first line]

Captured: [YYYY-MM-DD HH:MM]

[Full note text]

---
*Route with `/act` when ready.*
```

**For `data/notes.md` (general note or decision):**
If the file doesn't exist, create it:
```markdown
# Job Search Notes

> Captured by `/remember`. Raw notes and decisions from sessions.

## Decisions

## Notes
```

Append under the appropriate section header with a date stamp:
```
**[YYYY-MM-DD]:** [Note text]
```

### Step 4: Confirm

Display a one-line confirmation:

```
✓ Remembered → [destination file]: "[truncated note]"
```

Examples:
```
✓ Remembered → data/networking.md (Alex Mullin): "Alex mentioned Amae is building a new Sacramento clinic"
✓ Remembered → data/profile.md (Compensation): "comp floor is $130K in practice"
✓ Remembered → data/notes.md (Decisions): "decided not to pursue Two Chairs"
✓ Remembered → data/job-pipeline.md (Amae Health) + data/networking.md (Alex Mullin): "Amae Series B was pre-empted by Altos"
✓ Remembered → inbox/20260224-143012-ripple-foods.md: "check out Ripple Foods" — run /act to route when ready
```

If routing was ambiguous, add:
```
> Routed to data/notes.md — if this belongs somewhere else, say /remember "[note]" and tell me the target.
```

## Edge Cases

- **No arguments**: Display usage with examples.
- **Name matches multiple contacts**: Write to all matches and note: "Written to N contacts matching '[name]'."
- **Company not in pipeline but mentioned**: Write to `data/notes.md` and suggest `/pipe add "[company]"` if it sounds like an active target.
- **Profile section unclear**: Default to appending at the bottom of profile.md under a `## Session Notes` section.
- **Note is very long**: Truncate to first 120 chars in the confirmation display; full note is always written in full.
