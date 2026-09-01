---
name: cold-outreach
description: Research a contact and produce an Outreach Brief (why-now, sourced proofs, positioning, hard don'ts) for Nick to write in his own voice — escalates to a full drafted email/LinkedIn message only on explicit request. Auto-logs either way.
argument-hint: <name> <company> [role] [channel:email|linkedin] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Write(tools/.pending-draft.source), Edit(data/outreach-log.md), Write(data/outreach-log.md), Write(output/**), Edit(framework/style-guidelines.md), Write(memory/lessons.md), mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebSearch, WebFetch
---

# Cold Outreach — First-Contact Messages

> ## ⚠️ DEFAULT CHANGED 2026-08-26 — READ BEFORE RUNNING
>
> **This skill no longer drafts the message by default. It produces an Outreach Brief and stops.**
> Nick writes the prose. See **Step 5b: Outreach Brief Mode**, which is now ON unless he explicitly
> asks for a draft ("put a draft together" / "draft it for me" / "write it"). **"Send it," "let's go,"
> and time pressure are NOT that ask** — urgency is precisely when the old default used to win.
>
> Steps 6-8 (drafting, quality gate, `.pending-draft.txt`, `open_draft.py`) run **only** on that
> explicit escalation. Research, verification, positioning and logging are all still automated; only
> the sentences are his. Origin: `memory/feedback_give_nick_beats_not_a_polished_script.md`, 4th fire.

Research a recipient and hand Nick everything he needs to write a first-contact message in his own voice: sender context, verified recipient facts, the why-now, sourced proofs, positioning, and the hard don'ts. On explicit request, escalate to a full drafted message that passes the three-question quality gate. Auto-logs the contact and creates a follow-up to-do either way.

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
5. **If approved (Y):** For each rule, promote by LAYER per the `memory/lessons.md` Section 2 loop (indexed in `framework/style-guidelines.md`): **voice mechanics** → `framework/voice-reference.md` §3; **drafting judgment** (what to say/cut/position per audience) → `framework/content-rules.yaml` + its `content-rules.md` index, applying the C1 generalizability gate; **CV/format** → `framework/style-guidelines.md`. Then update the lessons.md row: set Promoted = Yes.
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
11. `framework/content-rules.md` — **DRAFTING-JUDGMENT index** (what to say / cut / position — distinct from voice mechanics). Cold outreach leans on the `L2`/`L3` (subject + no alum-handshake), `I1` (name the company in sentence one), `A3` (no invented research-signal on replies), `C1`/`C3` (one ask, no logistics) rules. The **active** rules are the pre-send checklist run in Step 7's Content-Rules Pass; reference-only rules (in `content-rules.yaml`) inform what to include and cut. Rule-gate to what applies here.

### Step 4: Research & Personalization Gate (mandatory)

**A cold email does not proceed past this step without a cited, recipient- or company-specific hook.** Per [[feedback_cold_outreach_flow_gaps]] (2026-07-15 Loop-2 voice test): flat personalization is the #1 reason a draft "doesn't sound like Nick" and gets no reply. The hook must be a *specific, quotable* thing (the company's own words about its mission/problem, a named project, a post, a distinctive path) tied to something real about Nick, NOT a headline paraphrase. It is `I`-provenance (cited to a source), never `G`.

Run a **real** research pass (not a skim): use Exa (`mcp__exa__web_search_exa` / `web_fetch_exa`) on BOTH the company and the person, and fetch at least one primary source (the company's own site/about page, the recipient's public bio/post). Record the hook + its source URL for the Step 6b provenance audit. If no citable hook surfaces after a genuine pass, STOP and tell Nick the personalization gap rather than drafting on the headline alone.

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

### Step 5b: Outreach Brief Mode — **THE DEFAULT for cold outreach** (flipped 2026-08-26)

> **STOP. For cold outreach this mode is ON unless Nick asks for a draft.** Do not fall through to
> Steps 6-8. Produce the Outreach Brief below, hand it to Nick, and stop. He writes the prose.

**Why this is the default and not a flag** (Nick, 2026-08-26): *"how can I just get the beats to do the
research on the company, on the role, on what I would bring, how I should position myself, and then I
do the outreach? We automate everything around it so that it's still getting my voice."* An opt-in rule
requires him to remember to invoke it at the exact moment he is moving fast, which is the moment he
reaches for the finished draft instead. **The 2026-08-25 cold email to a target company is the worked example: it ran
through this skill, it worked (a same-day reply and a call inside a day), and he still wanted the
prose to have been his.** A rule that only fires when he remembers to ask loses to urgency. Per
`memory/feedback_give_nick_beats_not_a_polished_script.md` (4th fire) and his Tier-1 authenticity
non-negotiable. It is NOT grounded in a recipient objection — the one recipient who noticed said the
opposite (see that file's 2026-08-26 supplement before citing anyone).

**Escalate to a full draft ONLY on an explicit ask** — "put a draft together," "draft it for me,"
"write it." Then run Steps 6-8 as normal. "Send it," "let's go," or time pressure are NOT that ask.

**The Outreach Brief — deliberately stops where the writing starts.** No subject line, no opener, no
sentences to edit. Anything Nick could paste is a violation of this step.

1. **Company** — what they do, stage, and **the one thing that changed recently**. This is the why-now
   and it is the beat most often missing (the 2026-08-25 email had the funding round, the launch press coverage
   and the posting date all available and used none of them).
2. **Role** — the verbatim JD lines that matter, both shapes if it carries two, and the open screens.
3. **Recipient** — verified facts only. Flag every Wispr-dictated proper noun as unconfirmed.
4. **What Nick would bring** — 2-3 candidate proofs, **each with its source line** (`data/projects/*.md`,
   a transcript, an archive). A proof with no source line does not go in the brief.
5. **Positioning** — the thesis in one sentence, and which prong carries it.
6. **The hook** — a real, cited research fact. The company's own words, never a headline alone.
7. **Hard don'ts** — the falsifiable traps for this specific recipient (bad-overlap dates, claims Nick
   cannot make, a framing the company has moved off).

Then stop. Nick dictates; the mechanical-diff rule below governs cleanup of what he dictates.

**Trigger for the dictation-cleanup rule below:** Nick passes a voice-pure dictation guide via argument or earlier in the conversation ("use this as the spine: '...'"), OR signals he'll author the substance himself ("give me the spine," "I want to put it in my words," "I'll write it myself"), OR — now the common path — dictates his prose after receiving the Brief above.

**Rule:** The polished output's diff from the guide must be **mechanical only** — grammar, punctuation, Wispr-homophone silent-correct, sentence-boundary cleanup. Do NOT add new sentences, qualitative adjectives ("solid concept," "really cool"), feature-list descriptors when products are named, volume/scale estimates Nick didn't include, a second ask, or URLs to a company's own docs when the recipient works there. Do NOT reorganize structure beyond the dictation. If something seems missing, pause and ask Nick before adding.

**When Nick signals he'll write it himself** ("give me the spine"), give him structure + key points + raw hook material — NOT a finished, polished message — and do NOT run the fully-drafted Step 6-8 flow. Escalate to a full draft only if he explicitly asks for one ("put a draft together").

**Pre-present check:** diff the polished draft against the guide. If the diff includes new content beyond mechanical fixes, revise to cut.

See `memory/feedback_voice_pure_diff_minimal.md`, `memory/feedback_minimize_polish_on_voice_pure_dictation.md`, `memory/feedback_no_product_docs_to_employees.md`, `memory/feedback_give_nick_beats_not_a_polished_script.md`.

### Step 6: Draft the Message

**SPINE FIRST.** Before drafting, write one sentence answering *why is Nick going after this class
of seat?* — from `data/goals.md`, in plain speech, not taxonomy. That sentence is the email's spine;
the Step 4 research hook is seasoning and must never be the spine. **Step 7 row 4 will demand this
sentence back, quoted verbatim from the draft**, so a draft written without it fails the gate rather
than sailing through it. Carry all three pillars (Step 7's pillar table enumerates them).

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

**Flow-gap guardrails (mandatory — per [[feedback_cold_outreach_flow_gaps]]).** Before presenting, verify each; the drafting flow — not the voice corpus — is what fails, and it fails the same four ways:

1. **Warm opener present.** Nick's real cold emails open with a SHORT warmth beat (`Hope your week's going well -`, `small world!`), then get to it. Not a straight-into-the-pitch cold open, and not a flabby full pleasantry — warm but brief. A missing warm opener is the most common miss.
2. **No thesis-jargon recital.** Grep the draft for Nick's own job-search taxonomy ("deployment-strategist seat," "forward-deployed side," "the lane," "bringing AI into legacy enterprises") and de-recite it into plain speech. Reading his positioning doc aloud at the recipient reads as inauthentic. (Exception: keep a real target term like "forward-deployed roles" when the recipient literally works at a forward-deployed company — then it reads as fluency, not jargon. Judge per recipient.)
3. **One resonance beat, then the ask.** Two consecutive "why this resonates" paragraphs kill the ask for a 5-second scanner. Keep exactly ONE resonance beat, then the ask.
4. **Tight + scannable (~5 seconds).** Cut surface repetition and any corpus-validated phrase that reads as filler *in this context* (corpus-validated ≠ always-keep). Cold email should be scannable fast.
5. **Time-blocks vs. Calendly is per-send, not fixed.** Offer concrete time blocks OR a Calendly link; if unsure which fits this recipient, ask Nick. Do not hardcode either.

### Step 6b: Substance-Provenance Audit (mandatory)

**Before this step, apply `framework/writing-discipline.md`.** It is canonical for the four provenance labels (`N` / `C` / `I` / `G`), what counts as a substantive sentence, the audit output format, and the invariant that `G` is blocked in any slot carrying a claim about who Nick is, what he brings, what he wants, or what he has done. **This step adds only the slot table below.** Do not restate the labels here; if they need to change, change them there.

Label the provenance of every substantive sentence before the quality gate. Most acute in cold outreach because the recipient has no prior context to fact-check Claude's framing.

**Substantive sentences in cold outreach:** identity hook / credibility line / personalization / ask / value-prop / closing CTA. Standard pleasantries are not substantive; skip them.

**Audit rule (slot table for this artifact):**

| Slot | G allowed? | If G found |
|---|---|---|
| Identity hook (who Nick is) | **No** | STOP. Pull from `data/professional-identity.md` or ask Nick. |
| Credibility line (specific achievement / experience) | **No** | STOP. Pull from `data/projects/*.md` or ask Nick. |
| Personalization (recipient-specific opener / connection) | **No** | STOP. Either it's `I` with a citable source, or it's speculation - replace. Cold outreach without real personalization fails worse than slightly-less-personalized outreach. |
| **Reader-provenance of the hook** (added 2026-08-25) | **No** | **A citable source makes the FACT real; it does not make the READING Nick's.** Never write a sentence asserting Nick read, saw, or noticed something Claude found in research. Either attribute it to the company / public record ("the company's own line is..."), or confirm with Nick that he has seen it, or cut it. Origin 2026-08-25: a draft asserted Nick had read a LinkedIn post Claude found via Exa; the audit passed clean because the slot was `I` with a real URL, and Nick's verdict was "does not work at all." |
| Bridge sentence (linking recipient's situation to Nick's offer) | **No** | STOP. Ask Nick for the link or extract from corpus. |
| Ask (what Nick wants from this contact) | **No** | STOP. Ask Nick for the specific ask. |
| Standard pleasantries | Yes | Proceed. |
| Sign-off | Yes | Proceed. |

If any `G` blocks fire, return to Step 6 (draft) and request the spine for those slots, or surface a "this slot needs research" gap to Nick before continuing. Do not proceed to Step 7 with `G` in any blocked slot. Trace every `I` to its source before Step 7.

**Cold-outreach-specific note:** Cold recipients judge Nick almost entirely on the credibility + personalization slots. A `G` in those slots converts a cold outreach into a generic-template-spam read. The cost of stopping here (one Nick turn) is much lower than the cost of sending a generic-coded message that burns the contact permanently.

### Step 7: Quality Gate

**This gate is an EXTRACTION, not a rating (rewritten 2026-08-25).** For each question you must
**quote the sentence in the draft that does the work, verbatim.** Do not paraphrase and do not
score. **An empty quote box is a STOP, not a low score** — it means the draft is missing that thing.

Why it changed: ratings are self-graded and effectively always pass. A draft that scored
Strong/Strong/Strong on 2026-08-25 had no organizing claim in it at all and one sentence that was
false about Nick. A rating tests the strength of what is present and is structurally blind to what
is absent. Quoting cannot be blind to absence: either the sentence exists or the box is empty.

| # | Question | What to quote |
|---|---|---|
| 1 | **Why you?** | The sentence proving this is *this specific person*, not anyone at the company. |
| 2 | **Why now?** | The sentence carrying the timing trigger. |
| 3 | **Why me?** | The sentence establishing credibility **for this specific ask**. |
| 4 | **Why this class of role?** | The **organizing claim**: the sentence saying why Nick is going after this *kind* of seat. Added 2026-08-25. This is the spine, and it is the box that was empty on the draft that passed 3-for-3. |

Rules for the extraction:

- **Quote verbatim from the draft.** If you find yourself writing the answer rather than copying it,
  the sentence is not in the draft, and the box is empty.
- **One box empty → STOP and revise before presenting.** Not "note what would strengthen it."
- **Question 4 cannot be satisfied by a proof point.** "I ran a contact center pilot" is evidence of
  capability; it is not a claim about what Nick is going after. The spine names the *class of role*
  and why it fits. Pull it from `data/goals.md` and state it in plain speech, never in taxonomy.
- **A quote may serve only one row.** If the same sentence is doing double duty, one of the two jobs
  is not actually being done.

**Pillar coverage (mandatory, enumerated — no judgment call).** Fill all three rows. Enumerated
checklists convert; abstract instructions do not (measured: an 18-item list produced zero violations
live while every abstract rule failed). Pillars come from `data/goals.md` and
`data/professional-identity.md`; as of 2026-08-25 they are consulting / operator / builder.

| Pillar | In draft? | Sentence |
|---|---|---|
| Consulting | ✓ or ✗ | quote or `—` |
| Operator | ✓ or ✗ | quote or `—` |
| Builder | ✓ or ✗ | quote or `—` |

**Any `✗` requires a written reason in the Step 8 block.** Dropping a pillar because it fits the
recipient's product least is *under-selection*, the defect class measured 2026-08-20 at 6 of 13
send-time edits. It is a real choice sometimes; it is never a silent one.

**Tonal self-check (mandatory — the hook cannot catch this).** The three-question gate above tests *persuasion strength*, not voice fidelity; `check_draft_voice.py` is mechanical-only. Read the draft cold against the matched `voice-reference.md` exemplar and answer:

1. **Too polished / not human?** Smoother and more balanced than the exemplar — corporate copy rather than Nick typing fast?
2. **Generic / could be anyone?** Strip the proper nouns: would an ex-colleague think "yeah, that's Nick," or could any candidate have sent it? (the ex-colleague readability bar)
3. **Off register vs the exemplar?** Right content, wrong temperature — warmer/more eager, stiffer, or more hedged than the matched exemplar?

If any answer is bad, the exemplar didn't drive the draft. **Regenerate from the exemplar — do not patch sentence-by-sentence.** Present only once all three pass.

**Content-Rules Pass (mandatory — visible-output discipline, like the Step 6b provenance audit).** Load `framework/content-rules.md`. **Rule-gate:** select only the active rules whose trigger applies to a cold draft to THIS recipient (channel=cold always selects `L2`/`L3`/`I1`/`A3`/`C1`/`C3`; add `G2` if the recipient is non-technical, `G3` if they work at the company being discussed, `H6` for the availability close). Check the draft against each selected rule and record a one-line verdict in the Content-rules row of the Step 8 block. Advisory — you surface hits, Nick decides; nothing blocks. Run it every time; a silent skip is the `[[feedback_llm_self_policing_fails]]` failure. (Rule `G1` is also hook-enforced by `check_draft_voice.py`.)

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

**Quality Gate (extraction — quote the draft verbatim; an empty box is a STOP):**
- **Why you?** "[quoted sentence]"
- **Why now?** "[quoted sentence]"
- **Why me?** "[quoted sentence]"
- **Why this class of role?** "[quoted organizing claim]"

**Pillar coverage:**
| Pillar | In draft? | Sentence |
|---|---|---|
| Consulting | [✓/✗] | "[quote]" or — |
| Operator | [✓/✗] | "[quote]" or — |
| Builder | [✓/✗] | "[quote]" or — |
[Any ✗ needs a one-line reason here.]

**Metrics:**
- Word count: [N] (target: 75–125)
- Character count: [N] (LinkedIn only — target: <300 for connect)
- Suggested send time: [day/time in recipient timezone if known]
- Tone calibration: voice-reference exemplar — [cold outreach / Tuck alum / mission-aware / 3-bullet pitch] (cold = first contact, no prior body for this recipient)
- Content-rules pass: [rules checked: L2, L3, I1, C1... → `ok`, or rule-id + what fired]

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

**Write the marker in a SEPARATE Bash call that completes BEFORE the `open_draft.py` call — never chain them with `&&`.** The PreToolUse hook evaluates the whole command string before any of it runs, so `printf … > .pending-draft.source && python3 tools/open_draft.py` reads the OLD marker and BLOCKs as "stale marker." Two calls: (1) refresh the marker, (2) run `open_draft.py`. Same applies to every draft re-open when iterating. Per [[reference_pending_draft_marker_before_open_draft]] (fired 2026-06-22 + 2026-07-16).

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
