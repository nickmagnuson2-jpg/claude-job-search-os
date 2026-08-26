---
name: debrief
description: Debrief a real interview or drill — Nick-first cold scoring against the v0.9+ rubric, then Claude annotates, then ≥1pt disagreements trigger reconciliation. Updates progress/_summary.md, hypotheses.md, anti-pattern-tracker.md.
argument-hint: <path-to-cv-or-call-context>
user-invocable: true
allowed-tools: Read(*), Glob(*), Grep(*), Write(coaching/**), Edit(coaching/**), Write(data/company-notes/**), Bash(python3 tools/pipe_write.py:*), Bash(python3 tools/todo_write.py:*)
---

# /debrief — Cross-call interview debrief

> **MANDATORY READ before writing any output:** `framework/two-tier-capture.md` and `coaching/debrief-rubric.md`. This skill is a synthesis-producing skill operating on voice-corpus material AND a scoring skill bound to a versioned rubric. The principle: raw transcript preserved verbatim with wiki-links + synthesized debrief written separately. Both exist. Never collapse.

## Purpose

Score a single interview call against the 5-dimension rubric, surface
anti-patterns and hypothesis evidence, and propagate updates to the
canonical cross-call view (`coaching/progress/_summary.md`).

## Inputs

- A Granola transcript (live or pasted) OR a session note Nick wrote
- The call's date, target, and (best guess) format tag
- The rubric: `coaching/debrief-rubric.md` (read on every invocation — reference for scoring)
- Active hypotheses: `coaching/hypotheses.md`
- Cross-call view: `coaching/progress/_summary.md`
- Anti-pattern catalog: `coaching/anti-pattern-tracker.md`

## Workflow (mandatory order)

### Step 0.5: Raw-Granola precondition hard gate (MANDATORY — do NOT skip)

Before any per-call synthesized file is created in `coaching/progress/`, **verify that the raw transcript has been persisted to `data/voice-corpus/granola/`.** This is a hard gate — the synthesized output exists separately from but anchored to the raw; writing a debrief without the raw on disk produces an orphan synthesis with no source-of-truth, and silently violates the two-tier-capture principle (`framework/two-tier-capture.md`).

**Check:** the granola corpus directory should contain a file matching the call:

```bash
ls data/voice-corpus/granola/*<YYYY-MM-DD>*<slug>*.md 2>/dev/null
```

Pattern: `YYYY-MM-DD-HHMM-<slug>.md` (transcript) + `YYYY-MM-DD-HHMM-<slug>-summary.md` (summary). Both should exist when granola_save.py / granola_auto_debrief.py have run cleanly.

**If raw is missing:** BLOCK and tell Nick:

> ⚠️ Raw Granola transcript not found at `data/voice-corpus/granola/` for this call. The two-tier-capture rule requires raw-on-disk before any synthesized debrief. Options:
> 1. Run `/granola-pull` first to fetch and persist (recommended)
> 2. If transcript was pasted (no Granola source), persist it manually: `python3 tools/granola_save.py --transcript <path> --date <YYYY-MM-DD> --slug <slug>`
> 3. To bypass for a one-off case (e.g., notes-only debrief with no recording): set `DEBRIEF_NO_RAW=1` in environment and re-invoke. This will produce a per-call file with `<!-- raw: none, bypass-flag -->` in the metadata, which `/checkout` and `/weekly-review` will surface for audit.

Do NOT proceed to Step 1 until either (a) raw is on disk, OR (b) bypass flag is set.

**Why this gate exists:** 2026-05-28 Phase E audit surfaced that `/debrief` was producing per-call synthesized files without verifying raw persistence — silent orphan-synthesis risk. Composes with `[[persist_granola_raw_tier_immediately]]` REOPEN gate met (5/28). Also composes with E3 `/checkout` cascade (Granola snippets — those snippets require raw on disk).

### Step 1: Pull and stage

Pull the transcript via `/granola-pull` if not already provided. Confirm
date, target, and format tag with Nick. Create the per-call file at
`coaching/progress/<YYYY-MM-DD>-<HHMM>-<slug>.md` with metadata block:

```markdown
<!-- session-metadata
format: <compound-tag>
time-pressure: <1|2|3>
structure: <1|2|3>
asymmetry: <1|2|3>
stage: <stage-tag>
holistic-rating: TBD
v0.9-tagged-by: claude-pending-confirm
-->
```

Compound formats: `unstructured-chat | structured-behavioral | founder-vibe-check | peer-screen | technical-deep-dive | drill`.
Stages: `networking | recruiter-screen | hiring-manager | founder-meet | peer-meet | onsite-loop | drill`.
Drill format leaves orthogonal axes blank.

### Step 1b: Mode check (MANDATORY branch point)

**If `stage: networking`** — this is a peer/mentor/warm-contact call where nobody is evaluating Nick and no role is being screened (no interviewer, no rubric target, usually no pipeline row). Skip Steps 2–9b entirely (the 5-dimension rubric, Tier-1 NN screen, and hypothesis test log all presume an evaluator grading Nick's interview performance — none applies to a mutual conversation). Go directly to **Networking Call Mode** below, then rejoin at Step 10.

**Otherwise** (`recruiter-screen | hiring-manager | founder-meet | peer-meet-as-evaluator | onsite-loop | drill`) — proceed to Step 2 as normal; this is real interview-evaluation content.

Origin: 2026-07-09 — a networking catch-up (peer, no hiring signal) was run through the interview rubric and Nick had to stop mid-scoring: "I don't think this is the right format for networking calls."

### Step 1c: Proof-availability exclusion scan (real interviews only — run BEFORE scoring)

Run the deterministic scan on the staged transcript before Step 2 scoring begins:

```
PYTHONIOENCODING=utf-8 python3 tools/transcript_exclusions.py --transcript <path> --wide
```

Read `hits` and `candidates`. If the interviewer **excluded the domain of the proof Nick had intended to deploy** — said they would never do that piece, that it is commoditized, that it is table stakes, that it is not what they do — then a "proof not deployed" observation in Step 5 is **not** a detection failure by Nick.

Score that miss as **"proof unavailable — interviewer excluded the domain"** and say so explicitly in the Live-Need Bridge binary at Step 5. Do not log it against H4 or the anti-pattern tracker.

`hit_count: 0` does not mean no exclusion happened — the tool's `coverage` string says paraphrases are not detected. Read the counterpart's turns.

**Why this step exists:** the rubric otherwise penalizes Nick for not deploying a proof the interviewer had already ruled out of scope, which is the correct in-room decision. Scoring it as a miss chases the wrong fix. Pairs with `/follow-up` Step 3e, which blocks the same proof from being deployed in the written follow-up.

---

## Networking Call Mode (stage: networking only)

Four short takeaways instead of a 5-dimension rubric. No interviewer signal, no NN screen, no hypothesis log — there's no evaluator and no role to test fit against.

### NW-1: Nick's cold read (still nick-first — no polish-anchoring even here)

Ask, before any Claude analysis:

> 1. **Story Delivery** (1–5, half-points OK): How did you present where you're at right now — the transition, what you're looking for? One-line evidence in your own words.
> 2. What was most valuable about this call for you — information, connection, or a lead?
> 3. What did you learn that changes something (market read, role-shape signal, a fact worth keeping)?
> 4. Anything you'd do differently next time (tone, ask, sequencing)?

Save under `## Nick's Cold Read`.

### NW-2: Claude annotates Story Delivery + extracts takeaways

**Story Delivery annotation (mirrors rubric Steps 3–4 in miniature, scoped to this one dimension):** read the transcript's self-narrative passage (the "where I'm at" / background walkthrough) and independently score 1–5 against the same anchors Dimension 2 (Delivery Crispness) and Dimension 5 (Authenticity) use in `coaching/debrief-rubric.md` — filler density, hedging, whether the story landed with texture or read as rehearsed. If Claude's score differs from Nick's by ≥1 point, run the same reconciliation as Step 4 (show both, ask Nick to adjudicate) before recording a final score. This is the only scored dimension in Networking Call Mode — everything else below is takeaways, not scoring.

**Structured takeaways** — read the transcript and pull out, quoting or citing the transcript line where useful:

- **New information worth keeping** — market intel, company facts, industry signal. Candidate content for `data/company-notes/<slug>.md` or `data/industry-notes/<slug>.md`.
- **Commitments** — what THEY offered (intro, info, a connection) and what NICK owes them (a promised follow-up, an intro, information).
- **Explicit next step**, if any was named.
- **Relationship-strength read**: cold / warm / hot, one line of evidence (e.g., "offered to network Nick into her own contacts unprompted — warm").

Save both under `## Claude's Takeaways` (Story Delivery final score at the top, takeaways below).

### NW-3: Capture the to-do and the info — mandatory, not just proposed

Two concrete outputs every networking-call debrief must produce (don't just describe them — do them, after Nick confirms wording):

1. **A to-do**, if NW-2 surfaced anything Nick owes the contact or a next move on the relationship (a nudge date, a promised send, "follow up if X"). Run `tools/todo_write.py add "<task>" "<priority>" "<due>" "<notes>"`. If nothing is owed and no next move exists yet, say so explicitly rather than inventing a generic "stay in touch" todo.
2. **Additional info worth remembering** — any new fact from NW-2 that isn't already captured elsewhere (market intel, a company data point, something about the contact). Confirm the exact destination and wording with Nick, then write it: append to `data/company-notes/<slug>.md` / `data/industry-notes/<slug>.md` for market intel, or note it in the `data/networking.md` interaction log entry (Step 9 still runs — this skill doesn't skip logging the interaction) if it's contact-specific color with no other natural home.

If the relationship reads warm/hot and looks like it's becoming an ongoing active tie (per `data/people/` promotion criteria in project CLAUDE.md), suggest `/networking promote` — do not auto-create a person dossier.

### NW-4: No pipeline step

Skip Step 9b entirely — there is no interview event advancing a pipeline stage. (Exception: if the contact is explicitly tied to a company already in `data/job-pipeline.md`, e.g. a warm-intro source for a live process, note that pipeline row's next-action instead of creating a new one — don't invent a stage change for a call that wasn't its own pipeline event.)

Then continue to **Step 10** (Predictions for next session — reframe as "what's the next move with this contact") and **Step 11** (close trigger todos) as normal. **Step 12** (`/follow-up` hand-off) still applies and is usually the natural next action for a networking call.

---

### Step 2: NICK-FIRST SCORING (mandatory gate)

Before producing ANY analysis output, ask Nick to score the call cold:

> "Score 1–5 (half-points OK) on each dimension, with one-line evidence
> in your own words. I'll annotate after you submit."
>
> 1. Format Resilience:
> 2. Delivery Crispness:
> 3. STAR Quality:
> 4. Applied Listening:
> 5. Authenticity:
> Overall (your gut):
> Interviewer signal (low/med/high):

**Do not produce dimension scoring or evidence aggregation before Nick
submits.** This is the polish-anchoring defense (per `feedback_llm_verification_system` memory).

**Also ask, in the same message (one line, required for real calls):**

> "Did you read the prep doc before the call? (read / skimmed / didn't open it)"

This gates attribution, not scoring. If the doc was **not read**, every prep-doc-resident behavior-change clause did not run — H4's live-need bridge and in-call governor, B3's clarifying-question count, B4's bare definitions, B6's whose-customers line, the NN probe questions. Any resulting miss must be logged against the **prep artifact not consumed** anti-pattern, NOT against the skill it superficially resembles. Scoring a bridge-miss as an H4 failure when the governor was never read misattributes a distribution problem to a retrieval problem, and the tracker then chases the wrong fix.

Record the answer in the per-call file's metadata block as `prep-doc: read | skimmed | unread`.

Origin: 2026-08-06 founder screen — a correct, same-day prep doc went unopened; zero of six prepared questions were asked and the positioning frame the doc had already corrected is what drew the pass. Distinct from the 2026-07-22 read-but-not-retrieved failure. See `/prep-interview` § B6 Reset-Carrier Rule.

Save Nick's scores to the per-call file under `## Nick's Cold Score`.

### Step 3: Claude annotates

Now read the transcript and rubric. For each dimension:
- Independently score (Claude's read).
- Surface specific evidence Nick may have missed (quoted lines, counts).
- Cross-reference against prior calls (e.g. "filler density of X is up
  Y% vs your last 3 calls").

Save under `## Claude's Annotation`.

**Dimension 2 is MECHANICAL. Run the tool; do not hand-build a comparison table.**

```bash
PYTHONIOENCODING=utf-8 python3 tools/filler_baseline.py --rank <slug-fragment>
PYTHONIOENCODING=utf-8 python3 tools/filler_baseline.py --top 12          # the ranked set
```

Quote the `citable_claim` field verbatim. It is pre-formatted as *"N% filler, rank R of D
(scope, >=W Nick-words)"* precisely so a position cannot be reported without its denominator.

**Three hard constraints on any Dim-2 claim:**

1. **No superlative without the computed set.** "Lowest", "worst", "best", "first", "only" are
   available ONLY from a `--top` run in this session. A five-row table assembled by hand
   supports a pairwise claim ("lower than X and Y") and nothing more.
2. **If `excluded_corrupt` is non-zero, say so in the debrief and treat every ranking as
   provisional.** Those files parse to zero turns and are invisible to the comparison. Repair
   them (`data/voice-corpus/granola/_duplicates/README.md` documents the label formats) before
   citing a rank as settled.
3. **Never cite a row flagged `[UNRELIABLE ATTRIBUTION]`** as a comparator. In-person captures
   put the room on the owner channel; the number is real and means nothing.

**Origin, 2026-08-24.** A debrief reported *"lowest filler density in the corpus, on the highest
Nick-word count in the corpus."* It was rank 9 of 42. The splitter had been validated correctly
against three logged files, and that validation was treated as licence for a corpus-wide
superlative built from a hand-picked five-row table. 25 label-corrupted transcripts were
meanwhile parsing to zero turns and dropping out silently, hiding seven real calls that beat
the claimed best. 13th fire of `feedback_name_the_scope_before_stating_the_conclusion`.
**Validating the instrument is not computing the denominator.** This step exists so the correct
move is also the cheapest one.

### Step 4: Disagreement trigger

For any dimension where Claude's score ≠ Nick's by ≥1 point:
- Generate a `## Reconciliation: Dim N` section
- Show both scores side by side, evidence for each
- Ask Nick to adjudicate: "Keep your score, take mine, or split?"
- Nick's adjudicated score is final.

For dimensions within 0.5 of each other: take Nick's score, no reconciliation needed.

### Step 5: Final scores + interviewer signal

Record final scores under `## Final Scores`:
- Per-dimension (1–5 with half-points)
- Overall (combined; Nick's call)
- Interviewer signal (low/med/high) + one-line evidence
- **Live-Need Bridge binary (REQUIRED — H4 / `framework/answering-strategies/bridge-to-stated-need.md`).** Two yes/no, with one-line evidence each:
  - *Did the interviewer state a live need?* (a constraint they're under, OR a role/value description that echoes something Nick said) — Yes/No.
  - *If yes: did Nick bridge to it within one turn* (one specific proof + "that's why I want this", not a generic affirm or a contradicting default story)? — Yes/No, with valence (positive-match / negative-constraint).
  - This binary is canonical input to Step 7's H4 test-log row. Track it every call; the rate is the metric.

### Step 6: Anti-pattern check

Scan transcript and Nick's evidence for any patterns from
`coaching/anti-pattern-tracker.md`. For each detected:
- Increment count in `_summary.md` Anti-Pattern Scorecard
- Append occurrence to the per-pattern History section in
  `anti-pattern-tracker.md`

For new patterns Nick names: add as `NEW (candidate)` row in `_summary.md`
Anti-Pattern Scorecard, and a new section in `anti-pattern-tracker.md`.

### Step 6b: Signal Analysis — Tier-1 Non-Negotiables screen (MANDATORY)

The rubric scores **how Nick delivered**. Step 6b scores **what the call REVEALED about role fit** against Nick's Tier-1 non-negotiables. Distinct dimension; do not collapse with rubric scoring.

**Source-of-truth:** read `data/goals.md` § Non-Negotiables. Each bullet there is a Tier-1 NN. Do NOT hardcode — the list evolves per `[[Goals.md cadence]]` (frequent updates), and stale hardcoded copies drift silently.

**Screen each NN against the call** using this 4-state rubric:

| State | Definition |
|---|---|
| **PASS** | Explicit positive signal — the call directly affirmed this NN is met (interviewer described culture/structure/etc. that matches, or Nick probed and got affirmative concrete evidence). |
| **YELLOW** | Ambiguous — signal present but not conclusive; OR red flag absent but not affirmatively confirmed. Common state for early-round calls. |
| **FAIL** | Explicit negative signal — the call surfaced concrete evidence the role violates this NN. |
| **NOT TESTED** | Nothing in the call touched this dimension. Common; flag for next-call probe. |

**Output to per-call file** under new heading `## Signal Analysis (Tier-1 NN screen)`:

```markdown
## Signal Analysis (Tier-1 NN screen)

| Non-Negotiable | Status | Evidence (one line from call) |
|---|---|---|
| Mission-aligned / interesting work | PASS \| YELLOW \| FAIL \| NOT TESTED | "[verbatim quote or paraphrase + speaker]" |
| In-person / hybrid Bay Area | ... | ... |
| Supportive leadership invested in growth | ... | ... |
| Direct assertive culture | ... | ... |
| Not a pressure cooker w/o safety net | ... | ... |
| Pushback / declining doesn't pay social cost | ... | ... |
| Ambiguity surfaces + resolves fast | ... | ... |

### Untested this call
- [NN name] — probe target for next session: `[suggested screen question from goals.md or derived]`
- [...]

### Net read
[One line: which way does the call shift Nick's fit-confidence — strongly toward fit, weak signal but encouraging, neutral, weak red flag, or strong red flag? Distinct from interviewer signal (low/med/high) — that's about THEIR read; this is Nick's read of the ROLE.]
```

**Why this exists:** 2026-05-22 a target-company debrief surfaced that `/debrief` was scoring delivery (rubric) without scoring **what Nick learned about role-fit**. The fit-signal screen is a distinct dimension; without it, Nick has no structured way to integrate "this call went well delivery-wise but the culture didn't match NN-X" across calls. The "Untested" list also drives next-call planning (specific NN probes go in `/prep-interview` next time).

**Composes with:**
- Step 5 interviewer signal (low/med/high) — that captures THEIR read; Step 6b captures NICK's read of the role
- Step 7 hypothesis log — pattern of "Untested → probed → FAIL" across calls may seed a new H<N> on role-shape hypotheses
- `/prep-interview` Family V update (Phase E M3) — the "Untested" list from prior debrief flows into next prep
- `[[feedback_debrief_add_signal_analysis_step]]` (the rule this step implements, REOPEN gate met Mon 5/25)

**Gap-surface behavior:** if all 7 NN are NOT TESTED, surface a stronger note in the Net read: "no fit-signal extracted from this call — was the format wrong, or did Nick not probe?" This is a Step 6b → Nick coaching loop.

### Step 7: Hypothesis test log

For each Active or Tested hypothesis in `coaching/hypotheses.md`:
- Compute prediction for this call (from claim + format tag)
- Compare to observed scores
- Append a row to that hypothesis's test log:
  `| <date> | <call> | <format> | <pred> | <obs> | Support|Refute|Inconclusive | <note> |`

After appending: check promotion criteria. If met, flag for Nick:
"H<N> meets promotion criteria — review and promote?"

### Step 8: Cross-call correlation surface (judgment-as-wedge)

Compute and surface (NOT propose) any notable cross-call patterns:
- Dimension correlations with format/orthogonal axes (n permitting)
- Recent vs baseline deltas (filler density, anti-pattern counts)
- Predictions from last debrief's "focus for next session" — did they
  hold up?

Present to Nick:
> "Surfaced patterns (suggestive, not statistical at n=<n>): …
> Anything worth registering as a hypothesis?"

If Nick names one: add to `coaching/hypotheses.md` Hypothesis Backlog
section as a Hunch (Ladder: Hunch → Active when Nick promotes).

### Step 9: Propagate updates

Update files in this order (each as a separate atomic write):
1. `coaching/progress/<call-file>.md` — full debrief
2. `coaching/hypotheses.md` — append test log rows, append new hunches
3. `coaching/anti-pattern-tracker.md` — append history entries
4. `coaching/progress/_summary.md` — recompute computed sections, append Session Index row, append Update Log entry

### Step 9b: Advance the job-pipeline stage (mandatory for real interviews)

A debrief means the call HAPPENED, so the `data/job-pipeline.md` row for this company is now stale (it still describes the round as upcoming). `/debrief` is the closure point for the pipeline stage just as Step 11 is for the trigger todos. Skipping this is the source of phantom "prep needed" items in `/standup`: the debrief closes the prep todos but the pipeline row keeps reading "round scheduled," and standup re-surfaces it as prep-needed.

Skip for drills (no pipeline row). For a real interview:

1. Determine the new stage + next-action (propose from the debrief's Net read, then confirm with Nick), e.g. stage `<Round> complete (<date>); awaiting next-step read`, next-action `Thank-you to <interviewer> sent; nudge <recruiter> if no read by <date>`.
2. Run (omit `--notes` so the existing notes column is preserved):

```bash
PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py --repo-root . update "<Company>" "<new stage>" --next-action "<next action>"
```

3. Parse the JSON: `status: ok` → report inline `✓ Pipeline advanced: <Company> → <stage>`. `Multiple matches` or any error → show the result and ask Nick which row; never guess the company row.

Origin: 2026-06-08 a target company. The Strategic Thinking round happened and `/debrief` closed every prep todo, but no step advanced the pipeline row, so it stayed "Strategic Thinking round scheduled (Mon 6/8)" and the next `/standup` surfaced it as prep-needed. Same structural lesson as Step 11's origin: a skill that consumes the trigger for an event must also advance every piece of state that event changes, not just the todos.

### Step 10: Predictions for next call

Ask Nick: "Based on this debrief, what's the focus for your next
session?" Record under `## Predictions for Next Session` in the per-call
file. `/prep-interview` reads this back at the start of the next call.

### Step 11: Close ALL trigger todos for this call (mandatory)

The call that just got debriefed has **two distinct trigger-todo families** in `data/job-todos.md`, both of which `/debrief` is the closure point for. Skipping either is the recurring source of phantom-overdue todos.

Derive the person/company slug from the call file path: `coaching/progress/<YYYY-MM-DD-HHMM>-<slug>.md` → `<slug>` is usually the person's last name or company.

**11a. Close the prep todo** — `[Person] call —` or `[Company] call —` style, created upstream of the call.

```bash
PYTHONIOENCODING=utf-8 python3 tools/todo_write.py done "<slug> call"
```

**11b. Close the debrief-reminder todo** — `Debrief after [Company] interview ... - run /debrief` style, created by `/prep-interview` Step 7 when the prep doc was generated. Search by company name (the debrief-reminder always carries the company, not the person).

```bash
PYTHONIOENCODING=utf-8 python3 tools/todo_write.py done "Debrief after <Company>"
```

Parse each JSON result (both 11a and 11b):
- `status: ok` → report inline: `✓ Closed trigger todo: <task>`
- `status: error` with "No task found" → try a second variant (first name, full name, company variants). If all variants miss for 11a, report once: `(no matching prep todo found — manual close may be needed)`. For 11b, the debrief-reminder may simply not exist (call wasn't booked via `/prep-interview`) — fine to skip silently. Do NOT block the debrief on either.
- `status: error` with "Multiple matches" → display the matches and ask Nick which to close. Don't guess.

Also sweep for related follow-up patterns the same call may have triggered: `Print [Company] [doc] PDF before [Person] call` style todos, anything explicitly named with the call as its trigger. Close each.

Origin: 2026-05-14 audit (a recruiter screen) found the prep todo orphaned → added 11a. 2026-05-19 standup (a target-company round) found the debrief-reminder todo orphaned by the same structural defect on the other side of the workflow → added 11b. Pattern: skill workflows that consume *any* trigger todo must close *every* trigger todo, not just the primary one.

### Step 12: Hand off to /follow-up (transcript-aware) — for real interviews

A debrief means a real conversation just happened and its transcript is on disk (`data/voice-corpus/granola/`). That transcript is the richest possible source for the post-interview thank-you / momentum note — the verbatim callback, the moment that resonated, the feedback Nick can now answer. Close the loop by offering it:

> "Want me to draft the follow-up? `/follow-up \"<Name>\"` will pull this transcript for the specific callback (content only — your email voice still comes from the corpus)."

Do NOT auto-invoke — offer and let Nick choose. The transcript-aware branch lives in `/follow-up` Step 3b (content-not-tone, auto-detect from granola, graceful degradation); this hand-off is the link in the chain `/granola-pull → /debrief → /follow-up`. Skip for drills (no real counterpart to follow up with). Origin: 2026-06-12 — Nick asked to ground post-interview follow-ups in the actual transcript and to link the two skills.

## Anti-patterns this skill is designed against

- **Polish anchoring:** Step 2 is mandatory before any Claude output.
- **Status retconning:** Hypothesis test log is append-only; never
  edit a row, even if verdict turns out wrong (add a corrective row).
- **Action-loop drift:** Step 10 is required; `/prep-interview` reads it back.
- **Two-ledger drift:** Anti-pattern counts are canonical in `_summary.md`,
  per-pattern detail lives in `anti-pattern-tracker.md`. Don't maintain
  duplicate counts.

## Memory rules honored

- `feedback_judgment_as_wedge` — Step 2 enforces Nick-first scoring; Step 8 surfaces correlations without proposing hypotheses.
- `feedback_qualitative_vs_binary_verification` — pre-committed rubric (v0.9 → v1) provides scoring scaffold; multi-pass via Step 4 disagreement trigger.
- `feedback_multipass_independent_review` — divergence is signal, not noise.
- `feedback_llm_verification_system` — append-only test log; Nick-first gate; no polish before Nick scores.
- `feedback_two_tier_capture` — raw transcript and synthesized debrief both preserved.
