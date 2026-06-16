---
name: cold-outreach
description: Draft personalized cold emails and LinkedIn messages to new contacts — research-informed, tone-matched, with auto-logging
argument-hint: <name> <company> [role] [channel:email|linkedin] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Write(tools/.pending-draft.source), Edit(data/outreach-log.md), Write(data/outreach-log.md), Write(output/**), Edit(framework/style-guidelines.md), Write(memory/lessons.md), mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebSearch, WebFetch
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
- `/cold-outreach "Sam Carter" "Lumen" channel:linkedin`
- `/cold-outreach "Priya Anand" "Acme AI" "VP Operations" channel:email "interested in CoS role"`
- `/cold-outreach "Tom Rivera" Notion "saw his talk on scaling ops"`

If no arguments provided, display usage:
```
Usage: /cold-outreach <name> <company> [role] [channel:email|linkedin|inmail] [context]

Examples:
  /cold-outreach "Sarah Chen" Stripe "Head of Ops"
  /cold-outreach "Sam Carter" "Lumen" channel:linkedin
  /cold-outreach "Priya Anand" "Acme AI" "VP Ops" "interested in CoS role"
```

## Instructions

### Step 0: Lessons Promotion Check

Before drafting, surface any accumulated voice patterns ready to be canonized:

1. Read `memory/lessons.md`.
2. Scan Section 2 for rows where **Occurrences ≥ 2** AND **Promoted = No**.
3. If none found, skip silently and proceed to Step 1.
4. If any found, surface them before proceeding:
   ```
   [N] outreach rule(s) ready to add to your Nick's Voice guidelines:

   1. Pattern: [pattern text]
      Rule: [rule text]
      Occurrences: [N]

   Promote to style-guidelines.md now? (Y / N)
   ```
5. **If approved (Y):** For each rule, append it to the most specific matching subsection in `framework/style-guidelines.md` → Nick's Voice (Greetings & Closings / Phrasing Patterns / Sentence-Level Rules). If no subsection fits, create one. Then update the lessons.md row: set Promoted = Yes.
6. **If declined (N):** skip and proceed. Rules remain in lessons.md for next time.

---

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
4b. `data/people/<slug>.md` (if present; slug = recipient name lowercased, accents folded, spaces→hyphens) — synthesized relationship dossier (commitments, what Nick owes them, pressure points, next move). Usually absent for a true cold contact, but read it if a prior relationship was promoted.
5. Company dossier — `output/<slug>/<slug>.md` (slug = company name, lowercase, spaces→hyphens)

   **Staleness check:** After reading, grep for `Last updated:` in the first 10 lines. If the dossier is more than 30 days old (or no `Last updated:` line is found), display this inline warning — then continue, never block:
   > ⚠️ Company dossier is [N] days old (last updated YYYY-MM-DD). Consider refreshing: `/research-company "[Company]"`
6. Company notes — `data/company-notes/<slug>.md` — personal context, recruiter call notes, observations
7. `data/job-pipeline.md` — pipeline status for this company
8. `framework/outreach-guide.md` — frameworks, constraints, anti-patterns, quality gate
9. `framework/style-guidelines.md` — Nick's voice patterns for tone matching (see "Nick's Voice" section)
10. `framework/voice-reference.md` — **EMPIRICAL voice reference extracted from labeled corpus.** Contains validated rules + verbatim exemplars. **MUST read both the rules AND the exemplars sections — research finding: rules alone underperform; rules + 2-3 exemplars beats both.** **The matched exemplar is the generative spine for Step 6: draft by adapting it to this recipient, NOT by filling the Step 6 structure with fresh prose. The persuasion framework (Step 5) and the structure (Step 6) shape argument ORDER, not voice — the exemplar shapes voice. If the exemplar isn't doing the work, the draft will read generic.**

### Step 4: Lightweight Research

If no company dossier exists in `output/<slug>/`:

1. Run 2–3 targeted web searches:
   - `"[Name] [Company]"` — their role, LinkedIn, recent activity
   - `"[Company] news"` — recent developments, funding, launches
   - `"[Company] [industry/domain]"` — if context suggests a specific area
2. Find: their role, recent activity, company news, shared connections or alumni
3. Do NOT run a full `/research-company` — suggest it as a follow-up if the user wants depth:
   ```
   Tip: Run `/research-company "[Company]"` for a full dossier before reaching out.
   ```

If a dossier exists, use it as the primary research source and supplement with 1 search for the contact specifically via Exa (`mcp__exa__web_search_exa`), falling back to WebSearch only if Exa returns nothing (per the Exa A/B verdict).

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

**Generate FROM the matched exemplar, not from the structure below.** The framework (Step 5) sets argument order; the structure below sets length and what each part must accomplish. Neither supplies voice — the `voice-reference.md` exemplar does. Adapt the exemplar to this recipient; any quoted text below is a spec of intent, never a sentence to paste or lightly reword. If the draft could have been sent to any contact at any company, the exemplar didn't drive it — restart from the exemplar, do not patch.

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

### Step 6b: Substance-Provenance Audit (mandatory)

Before the quality gate, label the provenance of every substantive sentence in the draft. This is the gate that catches Claude-generated self-positioning content before it reaches Nick's voice as a fait accompli — most acute in cold outreach because the recipient has no prior context to fact-check Claude's framing.

**Provenance labels:**
- **N** — Nick-dictated *this session* (the spine Nick just provided)
- **C** — Nick-corpus (verbatim or near-verbatim phrase from `voice-reference.md`, prior `data/reflections/`, sent emails, or `data/professional-identity.md` / `data/goals.md`)
- **I** — Claude-inferred from cited research (research dossier, public bio, role posting, public LinkedIn/company source — must be specifically citable)
- **G** — Claude-generated (no source — synthesized from general training / pattern-matching)

**What counts as a "substantive sentence":** identity hook / credibility line / personalization / ask / value-prop / closing CTA. Standard pleasantries are not substantive; skip them.

**Audit rule:**

| Slot | G allowed? | If G found |
|---|---|---|
| Identity hook (who Nick is) | **No** | STOP. Pull from `data/professional-identity.md` or ask Nick. |
| Credibility line (specific achievement / experience) | **No** | STOP. Pull from `data/projects/*.md` or ask Nick. |
| Personalization (recipient-specific opener / connection) | **No** | STOP. Either it's `I` with a citable source, or it's speculation — replace. Cold outreach without real personalization fails worse than slightly-less-personalized outreach. |
| Bridge sentence (linking recipient's situation to Nick's offer) | **No** | STOP. Ask Nick for the link or extract from corpus. |
| Ask (what Nick wants from this contact) | **No** | STOP. Ask Nick for the specific ask. |
| Standard pleasantries | Yes | Proceed. |
| Sign-off | Yes | Proceed. |

For every **I** sentence, name the source inline in working notes (`[Source: <path or URL>]`) — does not have to appear in the final email, but must be traceable before Step 7.

**Output of this step** (in working notes, not the email):

```
Substance audit:
- Identity hook: "..." → C (professional-identity.md)
- Credibility: "..." → C (data/projects/zuora.md Key Achievements)
- Personalization: "..." → I (recipient's recent Substack post — [URL])
- Ask: "..." → N (Nick dictated 5/21 17:10)
```

If any `G` blocks fire, return to Step 6 (draft) and request the spine for those slots, or surface a "this slot needs research" gap to Nick before continuing. Do not proceed to Step 7 with `G` in any blocked slot.

**Cold-outreach-specific note:** Cold recipients judge Nick almost entirely on the credibility + personalization slots. A `G` in those slots converts a cold-outreach into a generic-template-spam read. The cost of stopping here (one Nick turn) is much lower than the cost of sending a generic-coded message that burns the contact permanently.

**Why this exists:** Voice corruption in self-positioning content is the highest-frequency failure mode of email drafting (~10 separate behavioral rules in memory all instance this defect). This step collapses them into one structural gate. Origin: 2026-05-21 memory audit.

### Step 7: Quality Gate

Run the three-question test from `framework/outreach-guide.md`. For each question, write a **specific one-sentence answer** — not just a rating:

1. **"Why you?"** — Why this specific person? (e.g., "She led the Series B ops buildout and would understand my scaling background" — NOT "She works at the company")
2. **"Why now?"** — What timing trigger makes this relevant? (e.g., "They just announced a COO hire, signaling ops investment" — NOT "Job search")
3. **"Why me?"** — What establishes the sender's credibility for this specific ask? (e.g., "Scaled a 3-person ops team to 25 across two geographies" — NOT "Relevant experience")

Rate each: **Strong** / **Adequate** / **Weak**

If any answer is **Weak** or generic (the kind of thing any applicant could say), revise the draft to strengthen that dimension before presenting. If **Adequate**, note what would strengthen it.

**Tonal self-check (mandatory — the hook cannot catch this).** The three-question gate above tests *persuasion strength*, not voice fidelity; `check_draft_voice.py` is mechanical-only. Read the draft cold against the matched `voice-reference.md` exemplar and answer:

1. **Too polished / not human?** Smoother and more balanced than the exemplar — corporate copy rather than Nick typing fast?
2. **Generic / could be anyone?** Strip the proper nouns: would an ex-colleague think "yeah, that's Nick," or could any candidate have sent it? (the ex-colleague readability bar)
3. **Off register vs the exemplar?** Right content, wrong temperature — warmer/more eager, stiffer, or more hedged than the matched exemplar?

If any answer is bad, the exemplar didn't drive the draft. **Regenerate from the exemplar — do not patch sentence-by-sentence.** Present only once all three pass.

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
- Tone calibration: voice-reference exemplar — [cold outreach / Tuck alum / mission-aware / 3-bullet pitch] (cold = first contact, no prior body for this recipient)

---
Want me to log this contact and create a follow-up to-do? (Y/N)
```

**After presenting the draft**, immediately write `tools/.pending-draft.txt` with this format (overwriting any previous draft):

```
TO:
SUBJECT: [subject line, or blank for LinkedIn]
ATTACH: [optional absolute path — omit line entirely if no attachment]
BODY:
[full message text]
```

`ATTACH:` is optional and almost always omitted for cold outreach. Default behavior: do NOT attach a resume on first contact — offer to send if interested. Only include `ATTACH:` when the recipient has already asked for a CV/portfolio. When included, `open_draft.py` copies the file to the macOS clipboard so Cmd+V in Gmail compose attaches it.

**Immediately after writing `.pending-draft.txt`, also write `tools/.pending-draft.source`** with two lines:

```
cold-outreach
<ISO 8601 timestamp, e.g. 2026-05-13T15:30:42>
```

This marker is read by the `check_draft_voice.py` PreToolUse hook to verify the draft was produced by a skill (not inline). Without it, the hook blocks `open_draft.py`.

Then automatically run `python3 tools/open_draft.py` using the Bash tool to open the draft in Gmail. Show the output from the script to confirm it opened.

**After opening the draft**, also save an archive copy to `output/<company-slug>/MMDDYY-cold-outreach-<contact-slug>.md`:
- Company slug = company name, lowercase, spaces→hyphens (e.g., "Acme AI" → `acme-ai`)
- Contact slug = contact's full name, lowercase, spaces→hyphens (e.g., "Jordan Lee" → `jordan-lee`)
- Date prefix = `MMDDYY` (today's date)

Archive file format:
```markdown
# Cold Outreach: [Contact Name] @ [Company] — [YYYY-MM-DD]

**Channel:** [email / linkedin / inmail]
**Contact:** [Name][, Role if known]
**Company:** [Company Name]
**Date:** [YYYY-MM-DD]

---

[Full draft text as presented — subject line on first line if email, then message body]
```

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
