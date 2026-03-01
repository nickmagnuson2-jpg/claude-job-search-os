---
name: remember
description: Capture a note mid-session and route it to the right data file — contacts, pipeline, profile, or a general decision log
argument-hint: "<note text>"
user-invocable: true
allowed-tools: Read(*), Write(data/job-todos.md), Glob(inbox/*), Write(output/**), Bash(PYTHONIOENCODING=utf-8 python tools/remember_classify.py:*), Bash(PYTHONIOENCODING=utf-8 python tools/remember_apply.py:*)
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

Write the destinations JSON to a temp file, then call `remember_apply.py`:

```bash
PYTHONIOENCODING=utf-8 python tools/remember_apply.py \
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
