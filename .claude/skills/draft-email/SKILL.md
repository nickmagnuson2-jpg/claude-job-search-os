---
name: draft-email
description: Draft job-search emails — thank-you notes, status updates, intro requests, expressions of interest
argument-hint: <recipient> <purpose> [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Edit(data/job-todos.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Edit(data/outreach-log.md), Write(data/outreach-log.md)
---

# Draft Email — General-Purpose Job Search Emails

Draft thank-you notes, status updates, intro requests, expressions of interest, and informational interview requests. Auto-detects the email type from the purpose, pulls relevant context from data files, and offers to log to networking.

Use `/cold-outreach` for first-contact messages to strangers. Use `/follow-up` for re-engaging existing contacts. Use `/draft-email` for everything else.

## Arguments

- `$ARGUMENTS` (required): At minimum a recipient and purpose.
  - **Recipient** (required): Name of the person (quoted if multi-word)
  - **Purpose** (required): What the email is for (quoted string or remaining text)
  - **Context** (optional): Additional details — what happened, what to reference

Examples:
- `/draft-email "Sarah Chen" "thank you for coffee chat"`
- `/draft-email "Pete" "update on my job search"`
- `/draft-email "Alex Mullin" "ask for intro to Amae Health CEO"`
- `/draft-email "Lisa Park" "interested in the CoS role at Notion"`
- `/draft-email "James Liu" "informational interview about PM at Stripe"`

If no arguments provided, display usage:
```
Usage: /draft-email <recipient> <purpose> [context]

Email types (auto-detected from purpose):
  Thank-you    — "thank you for coffee chat"
  Update       — "update on my job search"
  Intro request — "ask for intro to [person/company]"
  Interest     — "interested in [role] at [company]"
  Informational — "informational interview about [topic]"

Examples:
  /draft-email "Sarah Chen" "thank you for interview yesterday"
  /draft-email "Pete" "update on my search, landed two interviews"
  /draft-email "Alex Mullin" "intro to Amae Health CEO"
```

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS` into components:

1. **Recipient** (required): First quoted string or first word(s) that look like a name.
2. **Purpose** (required): Next quoted string or remaining text describing the email's purpose.
3. **Context** (optional): Any additional text after the purpose.

### Step 2: Auto-Detect Email Type

Match the purpose string against keywords to determine the email type:

| Type | Keywords | Priority |
|------|----------|----------|
| **Thank-you** | "thank", "thanks", "grateful", "appreciated", "great meeting" | 1 |
| **Intro request** | "intro", "introduce", "connect me", "introduction", "put me in touch" | 2 |
| **Status update** | "update", "news", "progress", "let know", "share what" | 3 |
| **Interest** | "interest", "apply", "role", "position", "opportunity", "opening" | 4 |
| **Informational** | "informational", "learn more", "perspective", "advice", "pick your brain" | 5 |
| **General** | No match | 6 |

If the type is **General**, ask the user to clarify:
```
I'm not sure what type of email this is. Could you clarify?
- Thank-you note
- Status update to your network
- Intro request (asking someone to connect you)
- Expression of interest in a role
- Informational interview request
- Something else (describe it)
```

### Step 3: Load Context

Read the following files in parallel (skip any that don't exist):

1. `data/profile.md` — sender background
2. `data/professional-identity.md` — strengths, values
3. `data/networking.md` — existing contact info and prior interactions with the recipient
4. `framework/outreach-guide.md` — anti-patterns, tone matching, metrics

**Type-specific context loading:**

- **Status update:** Also read `data/job-pipeline.md`, `data/job-todos.md`, `data/job-todos-daily-log.md`
- **Intro request:** Also read `data/company-research/<company-slug>.md` if the target company is identifiable
- **Interest:** Also read `data/company-research/<company-slug>.md` and `data/job-pipeline.md`
- **Informational:** Also read `data/company-research/<company-slug>.md`

### Step 4: Tone Matching

If the recipient exists in `data/networking.md`:
1. Read all prior interactions — especially any sent messages in blockquotes.
2. Match the sender's established tone with this person.
3. Note the relationship type (recruiter, peer, mentor, etc.) for formality calibration.

If the recipient is not in `data/networking.md`:
- Default to professional, concise, peer-to-peer tone.
- Note: "Recipient not found in contacts — using default tone."

### Step 5: Draft by Email Type

---

#### Thank-You

**Timing:** Within 2–24 hours of the interaction (80% of hiring managers say it influences decisions; only 24% of candidates send one).

**Length:**
- Formal interview: 100–150 words
- Coffee chat / informational: 75–100 words

**Structure:**
1. **Thank for time** (1 sentence) — be specific about what you met about
2. **Specific callback** (1–2 sentences) — reference a particular discussion point that resonated
3. **Connect to your experience** (1 sentence) — briefly link what you discussed to your background
4. **Express continued interest** (1 sentence) — forward-looking, enthusiastic but not desperate
5. **Gracious close**

**Subject line:** "Thank you — [topic or their name]" or "Great meeting you, [Name]"

**No follow-up to-do created** — thank-you emails are one-shot.

---

#### Status Update

**When to use:** Sharing progress with mentors, peers, or your network. Keeps contacts engaged and top-of-mind.

**Data to pull:**
- `data/job-pipeline.md` — active applications and stages
- `data/job-todos.md` — recent completions
- `data/job-todos-daily-log.md` — recent progress snapshots

**Length:** 100–125 words

**Structure:**
1. **Warm opener** (1 sentence) — reference last conversation or connection
2. **Progress bullets** (3–5 bullets) — scannable, concrete updates
3. **What you're looking for** (1 sentence) — specific and actionable ("CoS roles at Series B–C healthtech companies" not "exploring opportunities")
4. **Reciprocal offer or specific ask** (1 sentence) — not vague "keep me in mind"
5. **Close**

**Subject line:** "Quick update — [your name]" or "[Name], job search update"

**Follow-up to-do:** Created with 30-day due date to send next update.

---

#### Intro Request

**Technique:** Use the **forwardable email technique** — write a request to the connector PLUS a self-contained blurb they can forward unchanged.

**Structure:**
1. **Ask to the connector** (2–3 sentences):
   - Reference your relationship
   - Use **double opt-in language**: "Would you be open to checking if [target] would welcome an intro?"
   - Never assume they'll forward without asking
2. **Line break**
3. **Forwardable blurb** (under 100 words):
   - Who you are (1 sentence — impact, not title)
   - Why this company/person specifically (1 sentence — shows research)
   - What you're looking for (1 sentence — specific ask)
   - LinkedIn URL
   - Low-pressure closing ("Happy to share more context if helpful")

**Subject line:** "Would you intro me to [target name]?" or "Quick ask — [target company]"

**Follow-up to-do:** Created with 7-day due date to check if connector responded.

---

#### Interest / Application

**When to use:** Expressing interest in a specific role, usually to someone at the company (not a formal application portal).

**Length:** Under 125 words

**Structure:**
1. **Connection hook** (1 sentence) — how you found the role or why you're reaching out to them
2. **Fit points** (2–3 bullets) — specific experience that maps to the role requirements
3. **Specific ask** (1 sentence) — "Would you be open to a brief conversation about the role?" or "Happy to send my resume if you'd like to take a look"
4. **Close**

**Do NOT attach resume** — offer to send if interested. Unsolicited attachments reduce response rates.

**Subject line:** "[Role title] — [your name]" or "Re: [role] at [company]"

**Follow-up to-do:** Created with 7-day due date.

---

#### Informational Interview Request

**When to use:** Requesting time to learn about someone's role, company, or industry. Assumes some connection exists (mutual contact, alumni, met at event).

**Length:** 75–100 words

**Structure:**
1. **Connection reference** (1 sentence) — how you know them or why you're reaching out
2. **Specific topic interest** (1 sentence) — what you want to learn about (not vague "your career")
3. **Low-commitment ask** (1 sentence) — "Would you have 15–20 minutes for a quick call or coffee?"
4. **Gracious close** — "Totally understand if timing doesn't work"

**Subject line:** "Quick question about [topic]" or "[Mutual contact] suggested I reach out"

**Follow-up to-do:** Created with 7-day due date.

---

#### General

If the email type doesn't match any category above:

1. Ask the user for more context about the purpose.
2. Draft based on the outreach guide's general principles: concise, one CTA, no anti-patterns.
3. Follow standard metrics: 75–125 words, clear subject line, no filler.

---

### Step 6: Quality Check

Run a simplified quality check against `framework/outreach-guide.md`:

- **Anti-patterns:** Scan the draft for banned phrases (filler openers, hedge words, multiple CTAs).
- **Length:** Verify word count is within target range for the email type.
- **CTA count:** Exactly 1.
- **Tone:** Matches the sender's established voice (or defaults to professional peer-to-peer).

If any check fails, revise before presenting.

### Step 7: Present Draft

```markdown
## Email Draft — [Type] to [Recipient]

**Type:** [Thank-you / Update / Intro Request / Interest / Informational / General]
**Tone:** [Matched to prior messages / Default professional]

### Subject Line
[subject line]

### Message
[the draft message]

---

**Quality Check:**
- Anti-patterns: [Clean / Found: list]
- Word count: [N] (target: [range for this type])
- CTA: [Single / Multiple — fix]

---
[If recipient is in networking.md]: Want me to log this interaction? (Y/N)
[If recipient is NOT in networking.md]: Want me to add [Name] as a contact and log this? (Y/N)
```

**After presenting the draft**, immediately write `tools/.pending-draft.txt` with this format (overwriting any previous draft):

```
TO:
SUBJECT: [subject line]
BODY:
[full message text]
```

Then show: `Draft saved → run \`python tools/open_draft.py\` to open in your mail client.`

### Step 8: Auto-Logging (after user approves)

**If recipient exists in `data/networking.md`:**
1. Log the interaction:
   ```markdown
   #### YYYY-MM-DD | email | [Type] — [1-line summary]

   > [Full message text in blockquote]

   **Follow-up:** [Next action or "—" for thank-you emails]
   ```
2. Update the contact's Last Interaction date.

**If recipient is NOT in `data/networking.md` and user wants to add:**
1. Add contact to the Contacts table (infer company and role from context if possible).
2. Create their Interaction Log section.
3. Log the interaction as above.

**Follow-up to-dos** (all types except thank-you):
- Read `data/job-todos.md`, add:
  - **Task:** `Follow up: [Name] — [action based on email type]`
  - **Priority:** `Med`
  - **Due:** Type-dependent (7 days for intro/interest/informational, 30 days for update)
  - **Status:** `Pending`
  - **Notes:** `From /draft-email on [date]`

**If `data/job-todos.md` doesn't exist**, create it with the standard header before adding.

**Append to `data/outreach-log.md`:**

Read `data/outreach-log.md` (create with the standard header if it doesn't exist). Append a new row to the table:

```
| [YYYY-MM-DD] | draft-email | email | [Name] | [Company or "—"] | [subject line or 1-line summary] | Drafted |
```

Confirm:
```
Logged to networking.md. [Follow-up to-do created — due [date].]
Outreach log updated → data/outreach-log.md
```

## Edge Cases

- **No arguments:** Display usage message with examples and email type reference.
- **Recipient name is ambiguous:** If multiple contacts match in `data/networking.md`, ask user to clarify.
- **Thank-you sent late (>48 hours):** Acknowledge gracefully in the draft — "I wanted to take a moment to properly thank you" rather than apologizing.
- **Status update with no pipeline/todo data:** Draft with whatever information the user provided in context. Note: "No pipeline or to-do data found — consider running `/pipe` and `/todo` first for richer updates."
- **Intro request but no company dossier:** Draft with available context. Suggest `/research-company` for the target company to strengthen the ask.
- **Purpose is unclear / General type:** Ask the user for clarification before drafting.
- **`data/networking.md` doesn't exist:** Skip logging offer, or offer to create the file if user wants to start tracking.
