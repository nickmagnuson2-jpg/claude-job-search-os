---
name: remember
description: Capture a note mid-session and route it to the right data file — contacts, pipeline, profile, or a general decision log
argument-hint: "<note text>"
user-invocable: true
allowed-tools: Read(*), Edit(data/networking.md), Edit(data/job-pipeline.md), Edit(data/profile.md), Edit(data/job-todos.md), Write(data/notes.md), Edit(data/notes.md), Write(inbox/*), Glob(inbox/*)
---

# Remember — Capture Notes Mid-Session

Quickly saves something you want to retain across sessions, routed to the right file. No setup required — just tell Claude what to remember.

## Arguments

- `$ARGUMENTS` (required): The note to capture. Plain language — no special format needed.

Examples:
- `/remember "Alex mentioned Amae is building a new clinic in Sacramento"` → appended to Alex Mullin's contact entry in networking.md
- `/remember "comp floor is $130K in practice, not $140K"` → appended to profile.md compensation section
- `/remember "decided not to pursue Two Chairs — too similar to what I want to leave"` → appended to pipeline notes or decision log
- `/remember "Amae Series B was pre-empted by Altos, not a standard raise"` → appended to company research dossier or pipeline notes
- `/remember "recruiter at Talkiatry is Sarah Kim, reached out on LinkedIn"` → creates or updates networking contact entry

## Instructions

### Step 1: Parse and Classify the Note

Read the note from `$ARGUMENTS`.

Classify the note into one of these destination types:

| Type | Detection | Destination |
|------|-----------|-------------|
| **Contact note** | Mentions a person's name AND something they said, did, or that you learned about them | `data/networking.md` — append to that contact's entry |
| **Company note** | Mentions a company name AND new intel (funding, people, strategy, news) | `data/company-research/<slug>.md` if it exists, otherwise `data/notes.md` |
| **Pipeline note** | Mentions a company AND a decision, status change, or strategic note about the application | `data/job-pipeline.md` — append to that company's Notes cell |
| **Profile update** | Mentions compensation, availability, start date, a preference, or a personal decision | `data/profile.md` — append to the relevant section |
| **Decision** | A clear decision that affects job search direction (e.g., "decided not to pursue X", "decided to prioritize Y") | `data/notes.md` under a ## Decisions section |
| **Raw capture** | Ambiguous item — a company name to look into, a link to read later, a half-formed thought, or the user explicitly says "just note this" / "save to inbox" | `inbox/` — create a timestamped file |
| **General note** | Anything else — insight, observation, follow-up thought | `data/notes.md` under a ## Notes section |

**Matching rules:**
- Extract person names and company names from the note text
- Check `data/networking.md` for matching contact entries (full-name match as substring, case-insensitive)
- Check `data/job-pipeline.md` for matching company entries (full-name match as substring, case-insensitive)
- Check `data/company-research/` for a matching slug file
- A note can match multiple destinations — if so, write to all that apply (e.g., a note about a person at a pipeline company might update both networking.md and job-pipeline.md)

**If classification is ambiguous**, default to `data/notes.md` and display a note asking if the routing was right.

### Step 2: Read Target File(s)

Read the target file(s) identified in Step 1. Always read before writing.

If `data/notes.md` doesn't exist, it will be created in Step 3.

### Step 3: Write the Note

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

**For `data/company-research/<slug>.md` (company note):**
Append at the bottom under a `## Manual Notes` section (create it if it doesn't exist):
```
### [YYYY-MM-DD]
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
