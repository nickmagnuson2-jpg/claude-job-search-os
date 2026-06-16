---
name: remember
description: Capture a note mid-session and route it to the right data file — contacts, pipeline, profile, decisions, accomplishments, or a general note log
argument-hint: "<note text>"
user-invocable: true
allowed-tools: Read(*), Write(data/job-todos.md), Glob(inbox/*), Write(output/**), Bash(PYTHONIOENCODING=utf-8 python3 tools/remember_classify.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/remember_apply.py:*)
---

# Remember — Capture Notes Mid-Session

Quickly saves something you want to retain across sessions, routed to the right file. No setup required — just tell Claude what to remember.

## Arguments

- `$ARGUMENTS` (required): The note to capture. Plain language — no special format needed.

Examples:
- `/remember "Jordan mentioned Acme is building a new site in a new metro"` → appended to Jordan Lee's contact entry in networking.md
- `/remember "Jordan Lee replied to my email"` → updates outreach-log.md Status to Replied for most recent Jordan Lee row
- `/remember "heard back from Sarah Chen, she's happy to connect"` → updates outreach-log.md Status to Replied + logs contact note
- `/remember "comp floor is $130K in practice, not $140K"` → appended to profile.md compensation section
- `/remember "decided not to pursue Lumen — too similar to what I want to leave"` → `data/decisions.md` (prompts for what drove it / what changes if wrong)
- `/remember "shipped the Lane B dossier"` → `data/accomplishments.md` (prompts for why it matters)
- `/remember "Acme Series B was pre-empted by a growth fund, not a standard raise"` → appended to company research dossier or pipeline notes
- `/remember "recruiter at Talkiatry is Sarah Kim, reached out on LinkedIn"` → creates or updates networking contact entry

## Instructions

### Step 1: Classify the Note

Run `remember_classify.py` with the note text from `$ARGUMENTS`:

```bash
PYTHONIOENCODING=utf-8 python3 tools/remember_classify.py --note "[escaped $ARGUMENTS]"
```

Parse the JSON output:
- `destinations[]` — list of routing destinations, each with `type`, `file`, `entity` (matched contact or company name), and `slug` (for company-notes paths)
- `ambiguous` — if `true`, default to `data/notes.md` and flag the routing as uncertain in the Step 4 confirmation

Proceed to Step 2 with the resolved `destinations[]`. If the script fails or returns an empty destinations list, fall back to `data/notes.md` as general_note.

### Step 1.5: Structure decision and accomplishment captures

If any destination `type` is `decision` or `accomplishment`, the entry is a chronological log entry that should be structured, not a flat sentence. Before writing, prompt Nick for the missing fields, assemble a multi-line body, and pass THAT as `--note` in Step 3 (not the raw `$ARGUMENTS`). The writer adds the `## YYYY-MM-DD` header, so do not include a date header in the assembled body.

**For a `decision`** ask (skip any already clear from the note):
- What did you decide?
- What drove it?
- What changes if it's wrong?

Assemble:
```
What: [decision]
Drove it: [driver]
Changes if wrong: [reversal trigger]
```

**For an `accomplishment`** ask:
- What landed?
- Why does it matter? (substrate for /weekly-review or a LinkedIn post)

Assemble:
```
What landed: [win]
Why it matters: [significance]
```

**Escape hatch:** if Nick says "just log it" / "skip the questions" or gives a one-liner and declines to elaborate, write the raw note flat (no assembly). Don't force the fields when he wants speed.

### Step 2: Read Target File(s)

Read the target file(s) identified in Step 1. Always read before writing.

If `data/notes.md` doesn't exist, it will be created in Step 3.

### Step 3: Write the Note

Write the destinations JSON to a temp file, then call `remember_apply.py`:

```bash
PYTHONIOENCODING=utf-8 python3 tools/remember_apply.py \
  --note "[escaped $ARGUMENTS]" \
  --destinations '[<destinations JSON from Step 1>]' \
  --repo-root .
```

Or use `--destinations-file` on Windows to avoid shell-escaping issues:
1. Write destinations JSON to a temp file (e.g., `/tmp/dests.json`)
2. Call: `remember_apply.py --note "..." --destinations-file /tmp/dests.json --repo-root .`

Parse the response:
- Single destination: `{"status":"ok","action":"<type>","file":"..."}` — use `file` in confirmation
- Multi destination: `{"status":"ok","action":"multi_write","results":[...]}` — surface any `warning` fields
- Error: `{"status":"error","message":"..."}` — surface to user with fallback suggestion

If the script fails or classify returned empty destinations: fall back to writing `data/notes.md` as general_note.

### Step 4: Confirm

Display a one-line confirmation:

```
✓ Remembered → [destination file]: "[truncated note]"
```

Examples:
```
✓ Remembered → data/networking.md (Jordan Lee): "Jordan mentioned Acme is building a new a new metro clinic"
✓ Remembered → data/profile.md (Compensation): "comp floor is $130K in practice"
✓ Remembered → data/notes.md (Decisions): "decided not to pursue Lumen"
✓ Remembered → data/job-pipeline.md (Acme AI) + data/networking.md (Jordan Lee): "Acme Series B was pre-empted by a growth fund"
✓ Remembered → inbox/20260224-143012-northwind.md: "check out Northwind" — run /act to route when ready
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
