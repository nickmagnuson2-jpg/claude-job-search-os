---
name: follow-up
description: Draft follow-up messages to existing contacts — sequence-aware, tone-matched, with value-add logic
argument-hint: [name] [channel:email|linkedin] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Edit(data/outreach-log.md), Write(data/outreach-log.md), WebSearch, WebFetch
---

# Follow-Up — Sequence-Aware Follow-Up Messages

Draft follow-up messages to existing contacts. Analyzes prior interaction history to determine sequence position, adds new value with each touchpoint, and matches the sender's established tone. Auto-logs and updates follow-up to-dos.

## Arguments

- `$ARGUMENTS`: Optional.
  - **No arguments:** Show the Stale Contact Dashboard — contacts needing follow-up.
  - **Name** (optional): Contact name to follow up with (quoted if multi-word).
  - **Channel** (optional): `channel:email` (default), `channel:linkedin`, `channel:inmail`.
  - **Context** (optional): Additional context — what happened since last contact, new info to share.

Examples:
- `/follow-up` — show stale contacts dashboard, pick one to follow up with
- `/follow-up "Alex Mullin"` — draft follow-up based on interaction history
- `/follow-up "Alex Mullin" channel:linkedin "saw his post about mental health access"`
- `/follow-up "Sarah Chen" "she mentioned intro to hiring manager"`

If arguments are provided but no matching contact is found, suggest using `/cold-outreach` instead.

## Instructions

### No-Args Mode: Stale Contact Dashboard

When invoked with no arguments:

1. Read `data/networking.md`.
2. If no contacts exist, display:
   ```
   No contacts tracked yet. Add contacts with `/networking add` or draft first-contact messages with `/cold-outreach`.
   ```
3. Find contacts needing follow-up:
   - Last interaction > 14 days ago
   - Last interaction has a pending follow-up action (not "—")
   - Recently contacted but no response logged yet
4. Sort by staleness (most overdue first).
5. Display:

   ```markdown
   ## Follow-Up Dashboard — [date]

   **Contacts needing follow-up: N**

   | # | Name | Company | Last Interaction | Days Since | Last Action | Suggested Follow-Up |
   |---|------|---------|-----------------|------------|-------------|-------------------|
   | 1 | Alex Mullin | Amae Health | 2026-02-18 | 4 | Cold outreach email | Gentle nudge + new insight |
   | 2 | Sarah Chen | Stripe | 2026-02-01 | 21 | Coffee chat | Re-establish, share update |

   Pick a number to draft a follow-up, or `/follow-up <name>` directly.
   ```

6. If the user picks a number, continue to the named contact workflow below.

---

### Named Contact Mode

#### Step 1: Load Contact History

1. Read `data/networking.md` — find the contact (case-insensitive, fuzzy match on full name).
2. If multiple matches, ask user to clarify.
3. If no match found:
   ```
   No contact named "[name]" found in networking.md.
   - Use `/networking add "[name]" [company]` to add them first
   - Use `/cold-outreach "[name]" [company]` for a first-contact message
   ```
4. Read all interactions for this contact. Count prior touchpoints to determine sequence position.

#### Step 2: Load Context

Read the following files in parallel (skip any that don't exist):

1. `data/profile.md` — sender background
2. `data/professional-identity.md` — strengths, values
3. Company dossier (generate slug from contact's company: lowercase, spaces→hyphens) — try `data/company-research/<slug>/<slug>.md` first (subfolder format), fall back to `data/company-research/<slug>.md` (legacy flat format)
4. `data/job-pipeline.md` — pipeline status for this company
5. `data/job-todos.md` — any pending follow-up to-dos for this contact
6. `framework/outreach-guide.md` — frameworks, constraints, anti-patterns

#### Step 3: Determine Follow-Up Type

Analyze the interaction history to determine the follow-up type:

| Situation | Follow-Up Type | Approach |
|-----------|---------------|----------|
| Sent cold outreach, no response | **Nudge** | Gentle nudge with new value-add |
| Had a meeting/coffee/call | **Post-meeting** | Thank you + continued interest + next step |
| Ongoing back-and-forth | **Continue thread** | Continue naturally, advance the conversation |
| Last contact 30+ days ago | **Re-establish** | Re-establish context, reference original connection |
| They offered something (intro, info) | **Collect** | Politely follow up on their offer |

#### Step 4: Sequence-Aware Drafting

Count prior outbound messages to this contact to determine sequence position. Follow the cadence from `framework/outreach-guide.md`:

| Follow-up # | Timing | Approach |
|-------------|--------|----------|
| 1st | 2–3 days after initial | Gentle nudge, reference original, add one new insight |
| 2nd | 5–8 days after initial | New angle — share relevant article, company news, or question |
| 3rd | 10–15 days after initial | Brief, direct — "wanted to bump this up" + clear ask |
| 4th | 20–28 days after initial | Last attempt — "I know you're busy, one last note" |
| 5th+ | 35+ days | Break-up: "No pressure, just wanted to leave the door open" |

**Critical rule:** Each follow-up MUST add new value. Never send "just checking in" or "circling back."

**Finding new value to add:**
1. Check `data/company-research/<slug>.md` for recent findings
2. Run 1–2 web searches for the contact's company — recent news, funding, launches, posts
3. Reference something from sender's recent activity (new insight, article, event)
4. If nothing fresh is available, ask a specific question that shows genuine curiosity

#### Step 5: Tone Matching

1. Read all prior sent messages to this contact from `data/networking.md`.
2. Analyze: sentence length, formality, contractions, opening/closing patterns, characteristic phrases.
3. Draft in the same style — the follow-up should read like it's from the same person.
4. Match the formality level of the existing thread (don't escalate or de-escalate without reason).

#### Step 6: Draft the Message

Follow channel constraints from `framework/outreach-guide.md`.

**Email follow-up structure:**
1. **Thread reference** (1 sentence) — "Following up on my note from [date]" or "Great meeting you at [event]"
2. **New value-add** (1–2 sentences) — the fresh insight, article, question, or update
3. **Renewed ask** (1 sentence) — same or slightly adjusted CTA
4. **Gracious close**

Keep it shorter than the original message. Follow-ups should be 50–100 words (shorter than initial outreach).

**LinkedIn DM follow-up:** Under 150 words, conversational tone.

**Post-meeting follow-up structure:**
1. **Thank you** (1 sentence) — specific to what you discussed
2. **Specific callback** (1 sentence) — reference a particular topic from the conversation
3. **Connection to next step** (1 sentence) — what you'll do or what you'd like to explore
4. **Open door** (1 sentence) — offer reciprocal value or leave space for continued dialogue

#### Step 7: Quality Gate

Run the three-question test. For each question, write a **specific one-sentence answer** — not just a rating:

1. **"Why you?"** — Does the follow-up reference something specific to this person? (e.g., "Referenced her recent post about scaling clinical ops" — NOT "She's at the company")
2. **"What's new?"** — What new value does this follow-up add? (e.g., "Sharing a relevant article about their competitor's Series C" — NOT "Just checking in")
3. **"Why me?"** — Does it reinforce (not repeat) the sender's credibility? (e.g., "Adds context about a similar challenge I solved at [Project]" — NOT "Same as before")

Rate each: **Strong** / **Adequate** / **Weak**. If any is **Weak** or generic, revise the draft to strengthen it before presenting.

#### Step 8: Present Draft

```markdown
## Follow-Up Draft — [Name] at [Company]

**Channel:** [email / linkedin]
**Follow-up type:** [Nudge / Post-meeting / Continue thread / Re-establish / Collect]
**Sequence position:** Follow-up #[N] (last contact: [date], [N] days ago)
**Value-add:** [What new information/angle this follow-up brings]

### Subject Line
[subject line — email only. For replies, use "Re: [original subject]"]

### Message
[the draft message]

---

**Quality Gate:**
- **Why you?** [Strong/Adequate/Weak] — [one-sentence specific answer]
- **What's new?** [Strong/Adequate/Weak] — [one-sentence specific answer]
- **Why me?** [Strong/Adequate/Weak] — [one-sentence specific answer]

**Metrics:**
- Word count: [N] (target: 50–100 for follow-ups)
- Sequence position: [N] of recommended 3–5 max

---
Want me to log this and update your follow-up to-do? (Y/N)
```

**After presenting the draft**, immediately write `tools/.pending-draft.txt` with this format (overwriting any previous draft):

```
TO:
SUBJECT: [subject line, or blank for LinkedIn]
BODY:
[full message text]
```

Then automatically run `python tools/open_draft.py` using the Bash tool to open the draft in Gmail. Show the output from the script to confirm it opened.

#### Step 9: Auto-Logging (after user approves)

**1. Log the interaction in `data/networking.md`:**

Add an entry under the contact's Interaction Log section:

```markdown
#### YYYY-MM-DD | [email/linkedin] | Follow-up #N — [1-line summary]

> [Full message text in blockquote]

**Follow-up:** [Next action — e.g., "Wait for response, follow up in 5 days if no reply" or "—" if final attempt]
```

Update the contact's Last Interaction date in the Contacts table.

**2. Update follow-up to-do in `data/job-todos.md`:**

- If a pending follow-up to-do exists for this contact: update the due date to the next follow-up window (based on sequence position), or mark complete if this was the final attempt (5th+).
- If no pending to-do exists: create one with the next follow-up date.
- **Task:** `Follow up: [Name] @ [Company] — [next action]`
- **Priority:** `Med`
- **Due:** Next follow-up date per the sequence cadence
- **Notes:** `Follow-up #N sent [date]`

**3. Append to `data/outreach-log.md`:**

Read `data/outreach-log.md` (create with the standard header if it doesn't exist). Append a new row to the table:

```
| [YYYY-MM-DD] | follow-up | [channel] | [Name] | [Company] | [subject line or 1-line summary] | Drafted |
```

Confirm:
```
Logged to networking.md. Follow-up to-do updated — next check-in due [date].
Outreach log updated → data/outreach-log.md
```

## Edge Cases

- **No arguments and no contacts in networking.md:** Display welcome message pointing to `/networking add` and `/cold-outreach`.
- **Contact found but no prior interactions logged:** Treat as a cold-outreach situation. Suggest `/cold-outreach` instead, but allow proceeding if user confirms (e.g., they contacted the person outside the system).
- **5th+ follow-up with no response:** Draft a graceful break-up message. Warn: "This is follow-up #[N]. Consider this a final attempt — pushing further risks being counterproductive."
- **Post-meeting follow-up sent late (>48 hours):** Acknowledge the delay gracefully in the draft ("Apologies for the delayed note — wanted to be thoughtful about following up").
- **Contact at company with existing dossier:** Leverage dossier for fresh talking points. If dossier is stale (>14 days), run 1–2 web searches for recent news.
- **`data/networking.md` doesn't exist:** Display error pointing to `/networking add` or `/cold-outreach`.
- **`data/job-todos.md` doesn't exist:** Create it with the standard header before adding the to-do.
