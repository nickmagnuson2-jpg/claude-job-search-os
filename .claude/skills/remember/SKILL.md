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
- `/remember "Jordan Lee replied to my email"` → asks who reached whom (Step 1.2); on "they reached me" it logs an `(inbound)` note on Jordan Lee and sets the newest open (Drafted/Sent) outreach row to Replied. The wording alone never does this.
- `/remember "heard back from Sarah Chen, she's happy to connect"` → same: the direction is asked, never read from the words
- `/remember "comp floor is $95K in practice, not $100K"` → appended to profile.md compensation section
- `/remember "decided not to pursue Lumen — too similar to what I want to leave"` → `data/decisions.md` (prompts for what drove it / what changes if wrong)
- `/remember "shipped the Lane B dossier"` → `data/accomplishments.md` (prompts for why it matters)
- `/remember "Acme Series B was pre-empted by a growth fund, not a standard raise"` → appended to company research dossier or pipeline notes
- `/remember "recruiter at Northwind is Sarah Chen, reached out on LinkedIn"` → creates or updates networking contact entry

## Instructions

### Step 0: `#personal` short-circuits everything

If the note carries the **`#personal`** tag, `remember_classify.py` returns a single
`personal_capture` destination and stops. The note is appended to the personal-OS
vault inbox and **nothing is written into this repo.**

- The tag is the ONLY trigger — never infer "this looks personal" from the topic.
  Misrouting a job-search note loses it from the search workflow; misrouting a
  personal note into the job-search repo is worse. An explicit tag cannot misfire.
- The short-circuit is deliberate. Classification is otherwise multi-destination, so
  without it a personal note that happens to mention a known contact or company would
  be filed in the vault **and** copied into `data/networking.md` or a company note.
- The vault path resolves through `tools/vault_paths.py` and is never printed. If the
  vault is unconfigured the capture FAILS LOUDLY rather than falling back to this repo.

```bash
PYTHONIOENCODING=utf-8 python3 tools/remember_classify.py --note "#personal book the vet"
# -> {"destinations":[{"type":"personal_capture","file":"<personal-vault>/data/inbox.md"}]}
```

Built 2026-08-19 (todo #10). Tests: `tests/scripts/test_remember_personal_routing.py`.

### Step 1: Classify the Note

Run `remember_classify.py` with the note text from `$ARGUMENTS`:

```bash
PYTHONIOENCODING=utf-8 python3 tools/remember_classify.py --note "[escaped $ARGUMENTS]"
```

Parse the JSON output:
- `destinations[]` — list of routing destinations, each with `type`, `file`, `entity` (matched contact or company name), and `slug` (for company-notes paths)
- `ambiguous` — if `true`, default to `data/notes.md` and flag the routing as uncertain in the Step 4 confirmation
- `direction_needed` — if `true`, every `contact_note` has `"direction": null` and Step 1.2 must fill it in. `remember_apply.py` refuses a `contact_note` without a direction.

Proceed to Step 2 with the resolved `destinations[]`. If the script fails or returns an empty destinations list, fall back to `data/notes.md` as general_note.

### Step 1.2: Record who reached whom (required for a contact note)

If `direction_needed` is `true`, ask Nick once, naming the contact:

> "Did Sam reach you, or did you reach Sam?"

Map the answer, and set it as `"direction"` on every `contact_note` in `destinations[]`:
- they reached Nick (they wrote, replied, called, texted) → `"in"`. This also marks the contact's newest open `outreach-log.md` row `Replied`.
- Nick reached them (he sent, replied, followed up) → `"out"`.
- both, in this one note (he sent, and they replied) → `"both"`. This counts as a reply and a send, and also marks the outreach row `Replied`.
- neither (a meeting, an observation, something heard about them) → `"none"`.

Never infer the direction from the note's wording, even when it reads like a reply ("Sam replied ..."). Ask. A note can name two contacts; if the answer differs per contact, ask for each.

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
- Why does it matter? (substrate for a retro or a LinkedIn post)

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
- Multi destination: `{"status":"ok","action":"multi_write","results":[...]}` — surface any `warning` fields. If `has_errors` is `true`, some destinations were NOT written: surface each result whose `status` is `error` (for example `direction_required`, a contact note without a direction, or `possible_duplicate_reply`, below) and fix it before confirming
- Error: `{"status":"error","message":"..."}` — surface to user with fallback suggestion

If the script fails or classify returned empty destinations: fall back to writing `data/notes.md` as general_note. Never for a `direction_required` refusal: that means Step 1.2 was skipped, so go back to Step 1.2, ask the direction, and rerun with ONLY the refused `contact_note` destinations (every one, if the note names several contacts), now carrying the direction (in a multi-destination call the other destinations were already written, so rerunning the whole list writes them twice); the note belongs in `networking.md`.

A `possible_duplicate_reply` refusal (direction `in` or `both`; nothing in the call was written) means the contact's newest entry is already a reply with no message from Nick after it, shown in `existing` (or that this same call already carries a reply note for that contact); nothing was written for that contact. Show Nick the entry. Because the call wrote nothing, always rerun it: without that contact note if the note is the same reply (it is already counted), with that destination as direction `out` if the refused direction was `both` (so Nick's message is still counted), or with `"new_reply": true` on it if Nick says it is a new message. Every other destination in the call is rerun unchanged. (This differs from `direction_required` above, where the other destinations were already written and only the refused note is rerun.) Rerun that one destination with `"new_reply": true` only if he says it is a new message from them.

A `not_found` refusal on a `contact_note` means the contact is not in the Contacts table of `data/networking.md`: the note is logged through `networking_write.py log` (the only writer of that file, since 2026-11-04), which logs only to a listed contact, and nothing was written for it. This is not a script failure, so do not fall back to `data/notes.md`. The refusal comes from the pre-flight, so nothing in the call was written (the same holds for `file_not_found`, a missing `networking.md`). Ask Nick whether to add the contact (`PYTHONIOENCODING=utf-8 python3 tools/networking_write.py --repo-root . add "<Name>" --company "<Company>"`), then rerun the whole call, or rerun it without that `contact_note` if he does not want the contact added.

### Step 4: Confirm

Display a one-line confirmation:

```
✓ Remembered → [destination file]: "[truncated note]"
```

Examples:
```
✓ Remembered → data/networking.md (Jordan Lee): "Jordan mentioned Acme is building a new a new metro clinic"
✓ Remembered → data/profile.md (Compensation): "comp floor is $95K in practice"
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
