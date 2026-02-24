---
name: cold-outreach
description: Draft personalized cold emails and LinkedIn messages to new contacts — research-informed, tone-matched, with auto-logging
argument-hint: <name> <company> [role] [channel:email|linkedin] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Edit(data/job-todos.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Edit(data/outreach-log.md), Write(data/outreach-log.md), WebSearch, WebFetch
---

# Cold Outreach — First-Contact Messages

Draft personalized cold emails and LinkedIn messages to new contacts. Loads sender context, researches the recipient, selects the best framework, and drafts a message that passes the three-question quality gate. Auto-logs the contact and creates a follow-up to-do.

## Arguments

- `$ARGUMENTS` (required): At minimum a name and company.
  - **Name** (required): The contact's name (quoted if multi-word)
  - **Company** (required): Their company name (quoted if multi-word)
  - **Role** (optional): Their role/title (quoted if multi-word)
  - **Channel** (optional): `channel:email` (default), `channel:linkedin`, `channel:inmail`
  - **Context** (optional): Additional context — why you're reaching out, what role you're interested in

Examples:
- `/cold-outreach "Sarah Chen" Stripe "Head of Ops"`
- `/cold-outreach "James Park" "Two Chairs" channel:linkedin`
- `/cold-outreach "Maria Lopez" "Amae Health" "VP Operations" channel:email "interested in CoS role"`
- `/cold-outreach "Tom Rivera" Notion "saw his talk on scaling ops"`

If no arguments provided, display usage:
```
Usage: /cold-outreach <name> <company> [role] [channel:email|linkedin|inmail] [context]

Examples:
  /cold-outreach "Sarah Chen" Stripe "Head of Ops"
  /cold-outreach "James Park" "Two Chairs" channel:linkedin
  /cold-outreach "Maria Lopez" "Amae Health" "VP Ops" "interested in CoS role"
```

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS` into components:

1. **Name** (required): First quoted string or first unquoted word(s) that look like a name.
2. **Company** (required): Next quoted string or next word after the name.
3. **Role** (optional): Next quoted string (if present and not a channel or context).
4. **Channel** (optional): Token matching `channel:email`, `channel:linkedin`, or `channel:inmail`. Default: `email`.
5. **Context** (optional): Remaining text after name, company, role, and channel.

### Step 2: Duplicate Check

Search `data/networking.md` for the contact name (case-insensitive substring match on full name).

- **If found with prior interactions:** Warn the user:
  ```
  Found existing contact: [Name] at [Company] — last interaction [date].
  You've already been in touch. Use `/follow-up [name]` instead?
  ```
  Proceed only if the user confirms they want a new cold outreach (e.g., different company, re-approaching after long gap).

- **If found with no interactions:** Note it and proceed — they were added but never contacted.

- **If not found:** Proceed normally.

### Step 3: Load Context

Read the following files in parallel (skip any that don't exist):

1. `data/profile.md` — sender background, location, interests
2. `data/professional-identity.md` — strengths, values, narrative patterns
3. `data/education.md` — schools, degrees (for alumni matching)
4. `data/networking.md` — for tone matching (read 2–3 prior sent messages as style reference)
5. `data/company-research/<company-slug>.md` — existing company dossier (generate slug: lowercase, spaces→hyphens)
6. `data/job-pipeline.md` — pipeline status for this company
7. `framework/outreach-guide.md` — frameworks, constraints, anti-patterns, quality gate

### Step 4: Lightweight Research

If no company dossier exists in `data/company-research/`:

1. Run 2–3 targeted web searches:
   - `"[Name] [Company]"` — their role, LinkedIn, recent activity
   - `"[Company] news"` — recent developments, funding, launches
   - `"[Company] [industry/domain]"` — if context suggests a specific area
2. Find: their role, recent activity, company news, shared connections or alumni
3. Do NOT run a full `/research-company` — suggest it as a follow-up if the user wants depth:
   ```
   Tip: Run `/research-company "[Company]"` for a full dossier before reaching out.
   ```

If a dossier exists, use it as the primary research source and supplement with 1 web search for the contact specifically.

### Step 5: Select Framework

Use the waterfall personalization logic from `framework/outreach-guide.md`:

| Priority | Signal | Framework |
|----------|--------|-----------|
| 1 | Shared alumni / mutual contact / same company history | Persona-Based |
| 2 | Known admirable work, content, or decisions | 3Ps (Praise-Picture-Push) |
| 3 | Identifiable operational pain you can address | PAS (Problem-Agitate-Solve) |
| 4 | Company at inflection point (post-funding, scaling, pivoting) | BAB (Before-After-Bridge) |
| 5 | General / no strong signal | AIDA |

Select the framework matching the highest-priority signal available. Note the signal and framework choice for the output.

### Step 6: Draft the Message

Follow channel constraints from `framework/outreach-guide.md`:

**Email (default):**
- 75–125 words
- 2–4 word subject line (<40 chars)
- 3–5 short paragraphs
- Structure:
  1. **Connection hook** (1 sentence) — the "why you" + "why now"
  2. **Credibility line** (1 sentence) — the "why me", impact not credentials
  3. **Specific company/person reference** (1 sentence) — proves research
  4. **Low-pressure ask** (1 sentence) — coffee, 15–20 min chat, specific question
  5. **Gracious close** — warm sign-off, first name

**LinkedIn Connect:**
- Under 300 characters total (hard limit)
- Plain text, no formatting
- 2–3 sentences maximum
- Structure: Identity hook → One credibility line → Soft reason to connect

**LinkedIn InMail:**
- Under 100 words body, under 200 chars subject
- Structure: Same as email but more compressed

**Tone matching:** Follow the tone matching protocol from `framework/outreach-guide.md`. Read 2–3 prior sent messages from `data/networking.md` and match the sender's natural voice. If no prior messages exist, default to professional, concise, peer-to-peer.

### Step 7: Quality Gate

Run the three-question test from `framework/outreach-guide.md`. For each question, write a **specific one-sentence answer** — not just a rating:

1. **"Why you?"** — Why this specific person? (e.g., "She led the Series B ops buildout and would understand my scaling background" — NOT "She works at the company")
2. **"Why now?"** — What timing trigger makes this relevant? (e.g., "They just announced a COO hire, signaling ops investment" — NOT "Job search")
3. **"Why me?"** — What establishes the sender's credibility for this specific ask? (e.g., "Scaled a 3-person ops team to 25 across two geographies" — NOT "Relevant experience")

Rate each: **Strong** / **Adequate** / **Weak**

If any answer is **Weak** or generic (the kind of thing any applicant could say), revise the draft to strengthen that dimension before presenting. If **Adequate**, note what would strengthen it.

### Step 8: Present Draft

Show the draft with metadata:

```markdown
## Cold Outreach Draft — [Name] at [Company]

**Channel:** [email / linkedin / inmail]
**Framework:** [name] — [why this framework was chosen]
**Key signal:** [the personalization signal used]

### Subject Line
[subject line — email only]

### Message
[the draft message]

---

**Quality Gate:**
- **Why you?** [Strong/Adequate/Weak] — [one-sentence specific answer]
- **Why now?** [Strong/Adequate/Weak] — [one-sentence specific answer]
- **Why me?** [Strong/Adequate/Weak] — [one-sentence specific answer]

**Metrics:**
- Word count: [N] (target: 75–125)
- Character count: [N] (LinkedIn only — target: <300 for connect)
- Suggested send time: [day/time in recipient timezone if known]

---
Want me to log this contact and create a follow-up to-do? (Y/N)
```

**After presenting the draft**, immediately write `tools/.pending-draft.txt` with this format (overwriting any previous draft):

```
TO:
SUBJECT: [subject line, or blank for LinkedIn]
BODY:
[full message text]
```

Then automatically run `python tools/open_draft.py` using the Bash tool to open the draft in Gmail. Show the output from the script to confirm it opened.

### Step 9: Auto-Logging (after user approves)

**1. Add/update contact in `data/networking.md`:**

Read `data/networking.md`. If the contact doesn't exist in the Contacts table, add a new row:

```markdown
| [Name] | [Company] | [Role] | peer | [today] | — |
```

If they already exist (from Step 2 — found but no interactions), update the Last Interaction date.

**2. Log the interaction:**

Add an entry under the contact's Interaction Log section (create the section if new contact):

```markdown
### [Name] — [Company]

#### YYYY-MM-DD | [email/linkedin] | Cold outreach — [1-line summary]

> [Full message text, line by line, in blockquote]

**Follow-up:** Wait for response — follow up in 3–5 business days if no reply
```

**3. Create follow-up to-do in `data/job-todos.md`:**

Read `data/job-todos.md`, then add:

- **Task:** `Follow up: [Name] @ [Company] — check for response`
- **Priority:** `Med`
- **Due:** 7 days from today (YYYY-MM-DD)
- **Status:** `Pending`
- **Notes:** `From /cold-outreach on [date]`

**4. Append to `data/outreach-log.md`:**

Read `data/outreach-log.md` (create with the standard header if it doesn't exist). Append a new row to the table:

```
| [YYYY-MM-DD] | cold-outreach | [channel] | [Name] | [Company] | [subject line or 1-line summary] | Drafted |
```

Confirm:
```
Logged to networking.md and created follow-up to-do (due [date]).
Outreach log updated → data/outreach-log.md
```

## Edge Cases

- **No arguments:** Display usage message with examples.
- **Name matches existing contact with recent interaction (<14 days):** Strong warning to use `/follow-up` instead. Only proceed on explicit confirmation.
- **No company dossier and web search returns thin results:** Draft with available information, note research gaps, suggest `/research-company` for depth.
- **LinkedIn connect over 300 chars:** Trim aggressively. Drop the least critical sentence. Count characters, not words.
- **No prior sent messages for tone matching:** Default to professional, concise, peer-to-peer tone.
- **Contact at a company already in pipeline:** Note the pipeline stage and tailor the outreach accordingly (e.g., if already "Applied", the cold outreach might reference the application).
- **`data/networking.md` doesn't exist:** Create it with the standard header and contacts table before adding the new contact.
- **`data/job-todos.md` doesn't exist:** Create it with the standard header before adding the to-do.
