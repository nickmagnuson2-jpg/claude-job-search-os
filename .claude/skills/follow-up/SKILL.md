---
name: follow-up
description: Draft follow-up messages to existing contacts — sequence-aware, tone-matched, with value-add logic
argument-hint: [name] [channel:email|linkedin] [context]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Grep(data/*), Edit(data/networking.md), Write(data/networking.md), Write(data/job-todos.md), Write(tools/.pending-draft.txt), Write(tools/.pending-draft.source), Edit(data/outreach-log.md), Write(data/outreach-log.md), Write(output/**), Edit(framework/style-guidelines.md), Write(memory/lessons.md), mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebSearch, WebFetch
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
- `/follow-up "Jordan Lee"` — draft follow-up based on interaction history
- `/follow-up "Jordan Lee" channel:linkedin "saw his post about operations"`
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
   | 1 | Jordan Lee | Acme AI | 2026-02-18 | 4 | Cold outreach email | Gentle nudge + new insight |
   | 2 | Sarah Chen | Stripe | 2026-02-01 | 21 | Coffee chat | Re-establish, share update |

   Pick a number to draft a follow-up, or `/follow-up <name>` directly.
   ```

6. If the user picks a number, continue to the named contact workflow below.

---

### Named Contact Mode

#### Step 0: Lessons Promotion Check

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
5. If a relationship dossier exists at `data/people/<slug>.md` (slug = contact name lowercased, accents folded, spaces→hyphens), read it. It holds the synthesized judgment layer the raw log doesn't: what they committed to, what Nick owes them, pressure points, and the next move. This is load-bearing for sequence-aware drafting — a "what I owe" item is often the strongest follow-up hook.

#### Step 1b: Reply Status Check

Before determining follow-up type, check whether the contact has replied to prior outreach:

1. Read `data/outreach-log.md` — scan for rows where:
   - Recipient column matches this contact's name (case-insensitive full-name substring match)
   - Status is `Drafted` or `Sent`
2. If no matching rows found, skip silently — contact not in outreach-log, or status already updated.
3. If matching rows exist, ask before proceeding:
   ```
   Did [Name] reply to your [most recent matching date] message?
     Y — Yes, they replied
     N — No response yet
     S — Skip / don't update status now
   ```
4. **If Yes (replied):** Update the most recent matching row's Status to `Replied` in `data/outreach-log.md`. Store `reply_status = "replied"` — this informs Step 3.
   Optionally ask: "Briefly, what did they say? (helps tailor the follow-up — press Enter to skip)"
   If context is provided, carry it into Steps 3 and 6.
5. **If No (no response):** Update the most recent matching row's Status to `No reply` in `data/outreach-log.md`. Store `reply_status = "no_reply"`.
6. **If Skip:** Proceed without updating outreach-log. Store `reply_status = "unknown"`.

#### Step 2: Load Context

Read the following files in parallel (skip any that don't exist):

1. `data/profile.md` — sender background
2. `data/professional-identity.md` — strengths, values
3. Company dossier — `output/<slug>/<slug>.md` (slug = contact's company, lowercase, spaces→hyphens)

   **Staleness check:** After reading, grep for `Last updated:` in the first 10 lines. If the dossier is more than 30 days old (or no `Last updated:` line is found), display this inline warning — then continue, never block:
   > ⚠️ Company dossier is [N] days old (last updated YYYY-MM-DD). Consider refreshing: `/research-company "[Company]"`
4. `data/job-pipeline.md` — pipeline status for this company
5. `data/job-todos.md` — any pending follow-up to-dos for this contact
6. `framework/outreach-guide.md` — frameworks, constraints, anti-patterns
7. `framework/style-guidelines.md` — Nick's voice patterns for tone matching (see "Nick's Voice" section)
8. `framework/voice-reference.md` — **EMPIRICAL voice reference extracted from labeled corpus.** Contains validated rules + verbatim exemplars. **MUST read both the rules AND the exemplars sections — research finding: rules alone underperform; rules + 2-3 exemplars beats both.** Pay particular attention to follow-up patterns (`Following up on my previous email...`, `Bumping this back to the top of your inbox`, post-call thank-you with specific reference) — these are the modes most relevant to this skill. **When Step 5 falls through to the voice-reference exemplar (no prior body), that exemplar is the generative spine — adapt it, don't fill the Step 6 structure with fresh prose.**
9. `framework/content-rules.md` — **DRAFTING-JUDGMENT index** (what to say / cut / position per audience — distinct from voice mechanics). The **active** rules are the pre-send checklist run in Step 7's Content-Rules Pass; reference-only rules (in `content-rules.yaml`) inform what to include and cut as you draft. Rule-gate to what applies to THIS recipient/type — don't apply all rules blindly.

#### Step 3: Determine Follow-Up Type

Analyze the interaction history to determine the follow-up type:

| Situation | Follow-Up Type | Approach |
|-----------|---------------|----------|
| Sent cold outreach, no response | **Nudge** | Gentle nudge with new value-add |
| Had a meeting/coffee/call | **Post-meeting** | Thank you + continued interest + next step |
| Ongoing back-and-forth | **Continue thread** | Continue naturally, advance the conversation |
| Last contact 30+ days ago | **Re-establish** | Re-establish context, reference original connection |
| They offered something (intro, info) | **Collect** | Politely follow up on their offer |

**Use `reply_status` from Step 1b to confirm the situation:**
- `replied` → route to **Post-meeting** or **Continue thread** (not Nudge), even if interaction log says "cold outreach"
- `no_reply` → route to **Nudge** regardless of how the interaction log describes the last message
- `unknown` → infer from interaction history as usual

#### Step 3b: Post-Meeting Transcript Pull (content branch — added 2026-06-12)

**Fires when:** the Step 3 follow-up type is **Post-meeting** or **Continue thread** off a real call/interview that just happened, AND a recent transcript for this contact exists. This is the branch for "I just interviewed and want the follow-up grounded in what was actually said." Skip entirely for Nudge / Re-establish / Collect types.

**Chains after `/debrief`.** The intended flow is `/granola-pull → /debrief → /follow-up`: by the time this runs, the raw transcript is already saved to `data/voice-corpus/granola/` and a debrief per-call file may exist in `coaching/progress/`. This branch consumes both.

**This branch sources CONTENT, never tone (load-bearing rule).** The transcript is *spoken, mixed-voice* (both parties). It is the richest source for the specific callback — what the interviewer actually said, the phrase or moment that resonated, commitments made, the next step named, any feedback or question Nick can now answer. It is NOT a tone source: **Step 5 tone-matching stays on the email corpus. Never tone-match off the transcript** — spoken voice is not email voice, and a draft toned off a transcript reads like a transcribed ramble.

1. **Auto-detect the transcript.** Glob `data/voice-corpus/granola/*.md` for files whose name contains the contact's name token(s) (first or last, lowercased) AND dated within ~7 days; take the most recent match.
   - Exactly one recent match → name it and confirm: "Found transcript: `<path>` (YYYY-MM-DD). Base the follow-up content on this? (Y/n)"
   - Multiple → list them, ask which.
   - **Override:** if Nick passed `transcript:<path>`, use it directly (skip auto-detect).
2. **Also read, if present:** the companion `-summary.md` (Granola AI summary) and the debrief per-call file `coaching/progress/<date>-<slug>.md` — the debrief's callback moments + Net read are already-synthesized hooks. Honors `framework/two-tier-capture.md`: transcript = raw tier (cite for verbatim), debrief = synthesized tier.
3. **Extract content hooks** (carry into Step 6 + label provenance `I`, transcript-cited in Step 6b):
   - The single most resonant thing the interviewer said (verbatim/near — the callback).
   - Any commitment / next step they named (informs the close).
   - A specific topic worth referencing as the value-add.
   - Anything they flagged as feedback or a question Nick can now answer — **a follow-up that answers their stated concern is the strongest kind** (e.g., a founder who coaches "push back on the AI more" → the follow-up acknowledges that directly and offers a relevant agentic side-project as living proof).
4. **Graceful degradation:** if no transcript is found (and none passed), skip this branch silently and proceed with the existing Step 2 context (networking blockquote + dossier). No warning, no block. **Silent skip applies to content hooks only — the pre-bound-proof safety gate is Step 3e and runs regardless.**

#### Step 3c: Reply-Mode Source Grounding (mandatory when replying to a specific received message)

**Fires when:** the follow-up is a *reply* to a specific message the contact sent (email thread, recruiter note, intro) — i.e. there's a verbatim inbound to respond to. Detect via reply-mode (subject starts "Re:") or because the trigger was "reply to their email." Skip for cold nudges with no inbound to ground against.

**This step exists because:** the highest-frequency cause of multi-spin reply churn is drafting from a *self-summary* of the inbound (including the paraphrase passed into this skill's context arg) instead of the verbatim source — which silently carries mis-assigned attributes (which thing they said about which entity) and invented positioning claims. "Quick / ASAP" makes this MORE important, not skippable. Origin: 2026-06-15 recruiter-reply incident, 7 versions. See `memory/feedback_ground_email_draft_in_verbatim_source_not_paraphrase.md`.

1. **Pull the verbatim inbound.** If not already in context, fetch it: `tools/gmail_fetch.py --all-mail --search "<query>" --body`. Read it line by line — do NOT work from a summary.
2. **Build a claim→source map (working notes, not the email).** For every substantive sentence the draft will make about the contact's message — per entity / per role / per offer — quote the verbatim line it grounds in:
   ```
   - "Beacon is worth moving on quickly" → "This one is worth moving on quickly." [verbatim]
   - "which verticals are they building out" → "depends on which verticals they are actively building out" — tied to NORTHWIND, not Beacon
   - "insurance-brokerage in my lane" → NO source line. INVENTED. Cut or recast to the role-shape claim.
   ```
3. **Any claim with no source line is invented** — cut it, recast it to something grounded, or ask Nick. This is stricter than Step 6b's after-the-fact provenance labeling: re-read their actual words; do not label an un-checked claim as "grounded."
4. **Cross-assignment check:** explicitly verify each attribute is attached to the entity the contact attached it to (the Beacon-vs-Northwind swap is the canonical miss).
5. **Mirror their framing where agreeing:** if Nick is agreeing with the contact's read, echo the contact's own structure/words (role-by-role) rather than a generic blob.
6. **Record any delivery signal in the thread, immediately.** If the inbound shows a bounce, a delivery receipt, or an explicit "I never got it" / "can you resend", write it to the outreach log before drafting:
   ```
   PYTHONIOENCODING=utf-8 python3 tools/outreach_status.py --set-status \
     --recipient "<name>" --date YYYY-MM-DD --artifact <token> --status <Bounced|Delivered>
   ```
   The `--date` is the date of the row being corrected, not today. An **unrecorded bounce is what produced the 2026-08-10 suppressive-claim defect**: the log still read `Sent`, so a later prep doc concluded the CV had arrived and told Nick not to re-offer it.

**Pre-stage voice scan (do before open_draft, every draft):** scan for banned tokens yourself — "genuinely"/"truly", semicolons, em-dashes, "exactly the kind of" — rather than relying on the `check_draft_voice.py` hook to catch them. Each hook trip is a wasted spin.

**Voice-dictated proper nouns:** any company/person name that entered via Nick's dictation (Wispr) is unverified — flag for confirm before it goes in an outbound email (e.g. "Acme Labs" → was actually "Acme AI").

#### Step 3d: Voice-Pure Dictation Mode (when Nick provides a guide)

**Trigger:** Nick passes a voice-pure dictation guide via argument or earlier in the conversation ("use this as the spine: '...'"), OR signals he'll author the substance himself ("give me the spine," "I want to put it in my words," "I'll write it myself").

**Rule:** The polished output's diff from the guide must be **mechanical only** — grammar, punctuation, Wispr-homophone silent-correct, sentence-boundary cleanup. Do NOT add new sentences, qualitative adjectives ("solid concept," "really cool"), feature-list descriptors when products are named, volume/scale estimates Nick didn't include, a second ask, or URLs to a company's own docs when the recipient works there. Do NOT reorganize structure beyond the dictation. If something seems missing, pause and ask Nick before adding.

**When Nick signals he'll write it himself** ("give me the spine"), give him structure + key points + raw hook material — NOT a finished, polished message — and do NOT run the fully-drafted Step 4-8 flow. Escalate to a full draft only if he explicitly asks for one ("put a draft together").

**Pre-present check:** diff the polished draft against the guide. If the diff includes new content beyond mechanical fixes, revise to cut.

See `memory/feedback_voice_pure_diff_minimal.md`, `memory/feedback_minimize_polish_on_voice_pure_dictation.md`, `memory/feedback_no_product_docs_to_employees.md`, `memory/feedback_give_nick_beats_not_a_polished_script.md`.

#### Step 3e: Pre-Bound Proof Safety Gate

**Runs for EVERY follow-up type — Post-meeting, Continue thread, Nudge, Re-establish, Collect.** Unlike Step 3b, this is not a content branch and does not depend on a transcript existing.

1. **Locate the prep doc:** `PYTHONIOENCODING=utf-8 python3 tools/prep_doc_parse.py --company-slug <slug>`. If it returns `{"doc": null}`, this step is a no-op — say so in one line and continue.
2. **If a prep doc exists, locate a transcript** for this contact using the Step 3b glob rule (`data/voice-corpus/granola/*.md`, name contains a contact name token, dated within ~7 days, most recent match).
3. **Transcript found:** run

   ```
   PYTHONIOENCODING=utf-8 python3 tools/transcript_exclusions.py --transcript <path> --wide
   ```

   Surface every `hits` sentence verbatim to the drafting pass, and read `candidates` yourself. **If any hit or candidate's domain overlaps the proof the prep doc pre-bound, that proof is blocked.** If the prep doc carries a `**Reserve proof**` line, use it. If it does not, surface the block to Nick and ask — do not improvise a substitute. *The overlap judgment is yours; running the detection is not optional.*
4. **`hit_count: 0` does NOT clear the proof.** The tool's own `coverage` string says paraphrased exclusions are not detected. Read the counterpart's turns for domain exclusions expressed in other words before deploying a pre-bound proof.
5. **No transcript found:** emit the literal line `[exclusion scan: NOT RUN — no transcript]` in the draft-review output. **A safety check that did not run is never reported as a check that passed.**

**Origin:** a prep doc pre-bound a single proof and said not to substitute it. Partway into the call the counterpart ruled that entire domain out of scope and called the proof's central deliverable commoditizable. The follow-up, drafted from the full transcript, led with it anyway — because nothing asked anyone to re-read the counterpart's turns for exclusions.

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
1. Check `output/<slug>/<slug>.md` for recent findings
2. Run 1–2 searches for the contact's company (recent news, funding, launches, posts) via Exa (`mcp__exa__web_search_exa`), WebSearch fallback only if Exa returns nothing
3. Reference something from sender's recent activity (new insight, article, event)
4. If nothing fresh is available, ask a specific question that shows genuine curiosity

#### Step 5: Tone Matching

**Tone-match calibration chain — try in order, stop at first hit. Track which source was used; it goes in the Quality Gate (Step 7).**

**1. `networking.md` blockquoted bodies (highest fidelity).** Read the recipient's Interaction Log section. If any prior interactions include the full message body in blockquote format (`> Body text...`), use those for tone matching. For a follow-up, this should almost always be available — the whole point of follow-up is continuing an existing thread.

**2. Archive file fallback.** If no blockquoted body found, glob `output/*/*-{draft-email,cold-outreach,follow-up}-<recipient-slug>.md` and read the most recent archive. Archives always include the full draft body. Recipient slug = full name lowercased with hyphens.

**3. Voice-reference exemplar fallback (yellow flag for follow-ups).** If neither source has a prior body, fall back to the `framework/voice-reference.md` exemplar for the follow-up nudge type. **MUST surface this as "Tone calibration: voice-reference exemplar (no prior body found)" in Step 7.** For a follow-up this should be rare — if it happens, pause and consider whether this is actually a cold-outreach situation in disguise.

**After picking a source, analyze:** sentence length, formality, contractions, opening/closing patterns, characteristic phrases. Draft in the same style. Match the formality level of the existing thread (don't escalate or de-escalate without reason).

**Transcript guard (when Step 3b fired):** an interview transcript is NOT a valid tone source — it is spoken, mixed-voice content. Tone-match ONLY off the email corpus (the chain above). The transcript supplies *what to say* (the callback, the resonant moment), never *how Nick writes it*. If the only available "prior body" is a transcript, treat tone calibration as a fall-through to the voice-reference exemplar and surface that in Step 7.

#### Step 6: Draft the Message

Follow channel constraints from `framework/outreach-guide.md`.

**Email follow-up structure:**
1. **Thread reference** (1 sentence) — "Following up on my note from [date]" or "Great meeting you at [event]"
2. **New value-add** (1–2 sentences) — the fresh insight, article, question, or update
3. **Renewed ask** (1 sentence) — same or slightly adjusted CTA
4. **Gracious close**

Keep it shorter than the original message. Follow-ups should be 50–100 words (shorter than initial outreach).

**Ask scope — broad-open default for nudges** (added 2026-05-21): when the next-step context isn't known (recruiter went silent, founder didn't reply, scheduling pending), prefer **broad-open asks** over narrow-specific asks. Example from sent corpus 2026-05-21: Nick edited `scheduling for the live mortgage-servicing case` → `about next steps`. Pinning the topic forecloses paths the recipient might offer. Use narrow scope only when (a) the recipient explicitly asked you to follow up about a specific item or (b) the only outstanding gate IS that specific item. See `memory/feedback_always_follow_up_with_recruiters.md` + voice-reference.md §1 "Broad-open ask > narrow-specific ask in nudges."

**Iteration safety — re-anchor pass at round 3+** (added 2026-05-21): if Nick has revised the draft 3+ times since this skill was invoked, the voice anchor has drifted (each inline edit bypasses Steps 1-7). Re-load `framework/voice-reference.md` and scan against §2 (anti-patterns) explicitly — phrase by phrase, not vibes-only. The 3-question qualitative tonal self-check in Step 7 is necessary but not sufficient. See `memory/feedback_voice_anchor_pass_at_iteration_3.md`.

**Re-engagement after a prior pass — soften default** (added 2026-05-21): when revising a re-engagement draft to someone who passed previously (silent or explicit), default direction is **soften, never sharpen** unless Nick explicitly says "hold the line" or "harder." See `memory/feedback_soften_default_in_post_pass_reengagement.md`.

**LinkedIn DM follow-up:** Under 150 words, conversational tone.

**Post-meeting follow-up structure:**
1. **Thank you** (1 sentence) — specific to what you discussed
2. **Specific callback** (1 sentence) — reference a particular topic from the conversation
3. **Connection to next step** (1 sentence) — what you'll do or what you'd like to explore
4. **Open door** (1 sentence) — offer reciprocal value or leave space for continued dialogue

#### Step 6b: Substance-Provenance Audit (mandatory)

Before the quality gate, label the provenance of every substantive sentence in the draft. This is the gate that catches Claude-generated self-positioning content before it reaches Nick's voice as a fait accompli.

**Provenance labels:**
- **N** — Nick-dictated *this session* (the spine Nick just provided)
- **C** — Nick-corpus (verbatim or near-verbatim phrase from `voice-reference.md`, prior `data/reflections/`, sent emails, or `data/professional-identity.md` / `data/goals.md`)
- **I** — Claude-inferred from a cited source (research dossier, public bio, role posting, public LinkedIn/company source, OR a just-pulled interview transcript / debrief per Step 3b — must be specifically citable). Transcript-sourced callbacks are `I`, not `G`: they trace to a real line the interviewer said.
- **G** — Claude-generated (no source — synthesized from general training / pattern-matching)

**What counts as a "substantive sentence":** opener / new-value-add / ask / value-prop / bridge sentence / story beat / closing CTA. Logistical sentences and standard pleasantries are not substantive; skip them.

**Audit rule:**

| Slot | G allowed? | If G found |
|---|---|---|
| Self-positioning (who Nick is, what he brings, what he wants) | **No** | STOP. Ask Nick to dictate that slot. |
| New-value-add (the article / insight / connection this follow-up adds) | **No** | STOP. Ask Nick what's actually new. |
| Bridge sentence (linking recipient's situation to Nick's offer) | **No** | STOP. Ask Nick for the link or extract from corpus. |
| Story / anecdote | **No** | STOP. Ask Nick for the actual story or pull from prior sent corpus. |
| Opener referencing recipient's work / product / strategy | **No** | STOP. Either it's `I` with a citable source, or it's speculation — replace with corpus-grounded line. |
| Logistics / scheduling / standard pleasantries | Yes | Proceed. |
| Sign-off | Yes | Proceed. |

For every **I** sentence, name the source inline in working notes (`[Source: <path or URL>]`) — does not have to appear in the final email, but must be traceable before Step 7.

**Output of this step** (in working notes, not the email):

```
Substance audit:
- Opener: "..." → C (prior thread, 5/14)
- New value-add: "..." → N (Nick dictated 5/21 17:10)
- Bridge: "..." → G ❌ STOP — need Nick to provide
```

If any `G` blocks fire, return to Step 4 (sequence-aware drafting) and request the spine for those slots. Do not proceed to Step 7 with `G` in any blocked slot.

**Why this exists:** Voice corruption in self-positioning content is the highest-frequency failure mode of email drafting (~10 separate behavioral rules in memory all instance this defect). This step collapses them into one structural gate. Origin: 2026-05-21 memory audit.

#### Step 7: Quality Gate

Run the three-question test. For each question, write a **specific one-sentence answer** — not just a rating:

1. **"Why you?"** — Does the follow-up reference something specific to this person? (e.g., "Referenced her recent post about scaling clinical ops" — NOT "She's at the company")
2. **"What's new?"** — What new value does this follow-up add? (e.g., "Sharing a relevant article about their competitor's Series C" — NOT "Just checking in")
3. **"Why me?"** — Does it reinforce (not repeat) the sender's credibility? (e.g., "Adds context about a similar challenge I solved at [Project]" — NOT "Same as before")

Rate each: **Strong** / **Adequate** / **Weak**. If any is **Weak** or generic, revise the draft to strengthen it before presenting.

**Tonal self-check (mandatory — the hook cannot catch this).** The gate above tests *value-add*, not voice fidelity; `check_draft_voice.py` is mechanical-only. Read the draft cold against the tone source from Step 5 (prior body if available, else the matched exemplar):

1. **Too polished / not human?** Smoother and more balanced than the prior thread — corporate copy rather than Nick continuing a conversation?
2. **Generic / could be anyone?** Strip the proper nouns: does it still read as Nick, or as any candidate's nudge?
3. **Off register vs the thread?** Warmer/more eager, stiffer, or more hedged than the established thread temperature?

If any answer is bad, regenerate from the tone source — do not patch sentence-by-sentence. Present only once all three pass.

**Mechanical pre-send checklist (do this explicitly, don't rely on having "read" the rule earlier — two documented-but-missed recurrences each, see `memory/lessons.md` Section 2, 2026-06-08/2026-06-30/2026-07-09):**

1. **Bare "next steps" scan.** Grep the draft for the literal phrase "next steps." If found without a softener immediately before or after it ("potential," "hopefully," "whenever you have a read," "any read yet on potential..."), add one. Bare "next steps" reads as presumptuous; this has been missed twice despite being canonical (`voice-reference.md` Exemplar 5 rule 4).
2. **Personalized-pleasantry check.** Is there a known specific about the recipient's timing this week (a holiday, a just-passed long weekend, a stated vacation, a known event)? If yes and the draft has no pleasantry referencing it (opener OR close — either position is valid), add one (`voice-reference.md` line 40). This has been missed three times.

Both checks are mechanical (pattern-match the draft text), not judgment calls — run them every time before Step 8, not just when they happen to come to mind.

**Content-Rules Pass (mandatory — mirror of the provenance audit's visible-output discipline).** Load `framework/content-rules.md`. **Rule-gate:** select only the active rules whose trigger applies to THIS draft (recipient_role, email_type, whether a value-offer/BCC-move/parallel-channel/logistical-concern is in play) — do NOT walk all rules. For each selected rule, check the draft and record a one-line verdict in the Content-rules row of the Step 8 block below (`ok` or the rule id + what fired). This is advisory: you surface hits, Nick decides — nothing here blocks. Run it every time; a silent skip is the exact failure `[[feedback_llm_self_policing_fails]]` documents. (`content-rules.md` rule G1 "shape of role" is also enforced by the `check_draft_voice.py` hook — it fires regardless.)

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
- Tone calibration: [networking.md blockquote / archive: `output/<slug>/<file>.md` / voice-reference exemplar (no prior body found)]
- Content-rules pass: [rules checked: A2, B1, H3... → `ok`, or rule-id + what fired]

---
Want me to log this and update your follow-up to-do? (Y/N)
```

**After presenting the draft**, immediately write `tools/.pending-draft.txt` with this format (overwriting any previous draft):

```
TO:
SUBJECT: [subject line, or blank for LinkedIn]
ATTACH: [optional absolute path — omit line entirely if no attachment]
BODY:
[full message text]
```

`ATTACH:` is optional. Include it when the follow-up genuinely needs a file (the recipient asked for a CV, or you're sending the artifact you previously offered). When included, `open_draft.py` copies the file to the macOS clipboard so Cmd+V in Gmail compose attaches it.

**Immediately after writing `.pending-draft.txt`, also write `tools/.pending-draft.source`** with two lines:

```
follow-up
<ISO 8601 timestamp, e.g. 2026-05-13T15:30:42>
```

This marker is read by the `check_draft_voice.py` PreToolUse hook to verify the draft was produced by a skill (not inline). Without it, the hook blocks `open_draft.py`.

Then automatically run `python3 tools/open_draft.py` using the Bash tool to open the draft in Gmail. Show the output from the script to confirm it opened.

**After opening the draft**, also save an archive copy to `output/<company-slug>/MMDDYY-follow-up-<contact-slug>.md`:
- Company slug = contact's company name, lowercase, spaces→hyphens (e.g., "Acme AI" → `acme-ai`)
- Contact slug = contact's full name, lowercase, spaces→hyphens (e.g., "Jordan Lee" → `jordan-lee`)
- Date prefix = `MMDDYY` (today's date)

Archive file format:
```markdown
# Follow-Up: [Contact Name] @ [Company] — [YYYY-MM-DD]

**Channel:** [email / linkedin]
**Contact:** [Name][, Role if known]
**Company:** [Company Name]
**Date:** [YYYY-MM-DD]
**Sequence position:** Follow-up #[N]

---

[Full draft text as presented — subject line on first line if email, then message body]
```

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
- **Post-meeting follow-up sent late (>48 hours):** Do NOT apologize for the timing of contact (`voice-reference.md` Section 2 apology nuance: apologize for real disruptions, never for reaching out or for taking time). Reframe forward without an apology — e.g. "Wanted to follow up properly on our conversation about [topic]" — not "Apologies for the delayed note".
- **Contact at company with existing dossier:** Leverage dossier for fresh talking points. If dossier is stale (>14 days), run 1–2 searches for recent news via Exa (`mcp__exa__web_search_exa`), WebSearch fallback.
- **`data/networking.md` doesn't exist:** Display error pointing to `/networking add` or `/cold-outreach`.
- **`data/job-todos.md` doesn't exist:** Create it with the standard header before adding the to-do.
