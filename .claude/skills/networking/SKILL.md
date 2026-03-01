---
name: networking
description: Track networking contacts and conversations — real-world and Claude chats — with auto-generated follow-up to-dos
argument-hint: [add|log|remove <name> [company] [role] [summary]]
user-invocable: true
allowed-tools: Read(*), Write(data/job-todos.md), Read(data/job-pipeline.md), Glob(data/*), Bash(PYTHONIOENCODING=utf-8 python tools/networking_read.py:*), Bash(PYTHONIOENCODING=utf-8 python tools/networking_write.py:*), Bash(PYTHONIOENCODING=utf-8 python tools/todo_write.py:*)
---

# Networking Tracker

Track networking contacts and interactions — coffee chats, recruiter calls, events, LinkedIn messages, and Claude conversations about job search strategy. Follow-up actions auto-generate as `/todo` entries.

**Full content capture**: Interactions store the actual message/email content (not just summaries) so Claude can analyze your tone, match your voice when drafting follow-ups, and track what you've already said to each contact.

## Arguments

- `$ARGUMENTS`: Optional. If empty, show all contacts.
  - `add <name> [company] [role]` — add a new contact
  - `log <name> <summary>` — log an interaction with a contact
  - `remove <name>` — archive a contact

Examples:
- `/networking` — show all contacts sorted by last interaction, flag stale ones
- `/networking add "Sarah Chen" Stripe "Engineering Manager"` — add a new contact
- `/networking log "Sarah Chen" "Had coffee, discussed PM roles. She'll intro me to hiring manager."` — log with summary only
- `/networking log "Sarah Chen"` — interactive mode: prompts to paste full email/message content
- `/networking remove "Sarah Chen"` — archive a contact

## Data Files

- Contact and interaction data: `data/networking.md`
- Follow-up to-dos written to: `data/job-todos.md`
- Pipeline cross-reference: `data/job-pipeline.md`

## Instructions

### Command: Show Contacts (no arguments)

1. Read `data/networking.md`.
2. If the file has no contacts, display a welcome message:
   ```
   No contacts yet. Add your first one:
     /networking add <name> [company] [role]

   Examples:
   - /networking add "Sarah Chen" Stripe "Engineering Manager"
   - /networking add "James Liu" — "Recruiter"
   - /networking add "Alex Park" Google
   ```
3. Run `networking_read.py` to get structured contact data:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/networking_read.py
   ```
   Use the JSON output to build the display:
   - `contacts[]` (sorted by last_interaction descending) → "All Contacts" table; include `interaction_count` and `days_since_last_interaction`
   - `stale_contacts[]` → "Stale Contacts (14+ days)" section (contacts with `stale: true`)
   - `pipeline_connections[]` → "Pipeline Connections" section (contacts whose company matches an active pipeline entry)
   - `metrics` → summary header (total_contacts, active_count, stale_count)

### Command: `add <name> [company] [role]`

1. Parse arguments and infer **Relationship** type:
   - `recruiter`, `hiring-manager`, `peer`, `mentor`, `referral`, `other`
   - If the role contains "recruiter", default to `recruiter`
   - If the role contains "manager", default to `hiring-manager`
   - Otherwise default to `peer`
2. Call `networking_write.py add`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/networking_write.py add "<name>" [--company CO] [--role ROLE] [--relationship TYPE] --repo-root .
   ```
3. If result `action == "duplicate_warning"`: warn user, show existing company.
4. On success: display the added contact and total contact count.
5. **Pipeline cross-reference**: Check `data/job-pipeline.md` — if any active entry matches this company, mention it.

### Command: `log <name> [summary]`

1. Read `data/networking.md`.
2. Find the matching contact (case-insensitive, fuzzy match on name — match on substring if unambiguous).
3. If multiple matches, ask the user to clarify.
4. If no match found, ask if the user wants to add them first.
5. **Collect interaction content** — two modes:
   - **With inline summary**: If a summary string is provided, use it as the summary. Ask if they want to also paste the full message content.
   - **Interactive mode** (no summary): Prompt the user to paste the full email/message content. Then ask for a short summary line, or auto-generate one from the content.
6. Ask or infer the **interaction type**:
   - `coffee`, `call`, `email`, `event`, `linkedin`, `claude-chat`, `other`
   - Infer from content/summary keywords:
     - "coffee" or "lunch" or "met up" → `coffee`
     - "call" or "phone" or "zoom" or "video" → `call`
     - Subject line present, or "Hi/Hey" greeting, or "email" mentioned → `email`
     - "event" or "meetup" or "conference" → `event`
     - "LinkedIn" or "DM" or "InMail" → `linkedin`
     - "Claude" or "AI" or "strategy session" → `claude-chat`
     - Otherwise → `other`
   - Always confirm the inferred type with the user or let them override
7. Parse **follow-up actions** from the content/summary:
   - Look for phrases like "follow up", "intro", "send", "share", "connect", "schedule", "reach out", "let me know", "next steps"
   - If found, extract the follow-up action text
   - If none detected, ask: "Any follow-up actions from this interaction?"
8. Call `networking_write.py log`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/networking_write.py log "<name>" \
     --date YYYY-MM-DD --type TYPE --summary "Summary line" \
     [--followup "Action text"] [--content "Full message text"] \
     --repo-root .
   ```
   The script writes the interaction entry (newest-first), updates Last Interaction in Contacts table, and shells out to `todo_write.py` for follow-up todos automatically.
9. If `code == "not_found"`: ask if user wants to add the contact first.
10. **Auto-generate follow-up to-dos** — if follow-up actions were identified AND the script didn't already create them (it does this when `--followup` is passed), create manually via:
    ```bash
    PYTHONIOENCODING=utf-8 python tools/todo_write.py add "Follow up: <name> @ <company> — <action>" Med <due-date> "From networking interaction on <date>"
    ```
11. Display the logged interaction and any cross-references.

### Tone & Voice Analysis

When full message content is stored, Claude can use it to:

- **Draft follow-ups** that match the user's natural tone with each contact
- **Detect tone shifts** — e.g., getting more formal or casual over time with someone
- **Avoid repetition** — know what's already been said so follow-ups build on prior messages
- **Adapt per relationship** — the user may write differently to recruiters vs. peers vs. mentors

When the user asks to draft a message to a contact, read their full interaction history first and match their established voice with that person.

### Command: `remove <name>`

1. Call `networking_write.py remove`:
   ```bash
   PYTHONIOENCODING=utf-8 python tools/networking_write.py remove "<name>" --repo-root .
   ```
2. If `code == "not_found"`: tell user the contact wasn't found.
3. On success: confirm removal (row deleted, interaction log heading prefixed `[ARCHIVED]`).

## Relationship Types

- **recruiter** — external or internal recruiter
- **hiring-manager** — the person who would manage you in the role
- **peer** — someone at your level, potential colleague or industry contact
- **mentor** — someone more senior giving advice or guidance
- **referral** — someone who can or did refer you to a company
- **other** — doesn't fit the above categories

## Interaction Types

- **coffee** — in-person coffee chat or meal
- **call** — phone or video call
- **email** — email exchange
- **event** — met at a networking event, meetup, or conference
- **linkedin** — LinkedIn message or connection
- **claude-chat** — Claude conversation about this contact's company, role prep, strategy, etc.
- **other** — doesn't fit the above

## Stale Contact Threshold

A contact is considered **stale** if their Last Interaction date is more than 14 days ago (or if they have no interactions logged). Stale contacts are flagged when viewing the contact list.

## Display Format

### Overview (shown by `/networking` with no args)

```markdown
## Networking — [date]

**Contacts: N** | Active: X | Stale (14+ days): X

### All Contacts

| Name | Company | Role | Relationship | Last Interaction | # Interactions |
|------|---------|------|-------------|------------------|----------------|
| Sarah Chen | Stripe | Engineering Manager | hiring-manager | 2026-02-15 | 3 |

### Stale Contacts (14+ days)

| Name | Company | Last Interaction | Days Since |
|------|---------|------------------|------------|
| ... | ... | ... | ... |

### Pipeline Connections

| Contact | Company | Pipeline Stage |
|---------|---------|---------------|
| Sarah Chen | Stripe | Applied |
```

Sort by Last Interaction descending (most recent first). Contacts with no interactions sort last.

### Interaction Log Format (in data file)

Each contact gets a section with entries in reverse chronological order (newest first):

```markdown
### Alex Mullin — Amae Health

#### 2026-02-18 | email | Cold outreach — Tuck alum, coffee chat

> Hi Alex,
>
> We never officially crossed paths at Tuck, but I'm a T'22 and also based
> in San Francisco.
>
> Most recently I was Chief of Staff to the Head of Product & Technology at
> Zuora after a stint at McKinsey, and I'm currently exploring what's next
> with a focus on mission-driven work, particularly in mental health.
>
> I saw that you're at Amae Health and would love to buy you a coffee and
> learn more about the platform and what you're doing to increase access to
> care.
>
> Would you have 20 minutes for coffee in the next couple weeks?
>
> Hope you are staying dry!
> Nick

**Follow-up:** Wait for response, schedule coffee chat

#### 2026-02-11 | linkedin | Connected on LinkedIn

Sent connection request with note about Tuck network.

**Follow-up:** Send cold outreach email
```

- Entries with full message content use blockquotes (`>`)
- Entries with summary-only use plain text (no blockquote)
- Every entry ends with a `**Follow-up:**` line
