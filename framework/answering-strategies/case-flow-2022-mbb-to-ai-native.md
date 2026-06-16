# Case Flow — Adapting 2022 MBB Habits to 2026 AI-Native Deployment Cases

> Companion to `output/halcyon/051926-ai-native-case-research.md`. The canonical case research doc covers what the 2026 AI-native deployment case format is, what is tested, failure modes, and the falsifiable prep rubric. This doc maps that case format onto the case-flow muscle memory Nick already has from MBB recruiting — what transfers, what doesn't, and where the named traps are.
>
> Status: 2026-05-20 first version, after Case #1 warm rep drill (`coaching/progress/2026-05-20-case-1-loan-officer-distrust-drill.md`) surfaced the polished-problem-solution-arc variant of the 2022-MBB reversion.

## Nick's pre-existing 5-step MBB case habit

1. **Get the information.** Listen, write down the prompt.
2. **Classify the case type.** "This is a profitability case," "this is a market-entry case," "this is an M&A case," etc.
3. **Drill in via clarifying questions.**
4. **Build out a structure from the type.** E.g. profitability case → profit = revenue − cost → drivers of each → systematic walkthrough.
5. **Work through the structure systematically.** Mostly linear; interviewer adds new info on a schedule.

This habit is good for 2022 MBB cases. It is the wrong habit for 2026 AI-native deployment cases. The traps are at Steps 2 and 4.

## Where the habit transfers

- **Step 1 (get information):** transfers cleanly. The first move is always to listen and write the prompt verbatim.
- **Step 3 (drill in via clarifying questions):** transfers cleanly, and this is the muscle to LEAN ON HARDER in the AI-native case. Hold this for 5-10 minutes before doing anything else. No solution content before minute 8 is the bright line.
- **Step 5 (work systematically):** transfers, but non-linearly. When the panel injects a wrinkle (new constraint, new stakeholder, new information), re-scope visibly rather than continuing the original march. *"That changes the slice. The new cut is..."*

## Where the habit fails

### Step 2 — classification is the trap

In a 2022 MBB case, classifying as "this is a profitability case" is GOOD. The classification primes the right downstream structure. The cases ARE typology-driven.

In a 2026 AI-native deployment case, classifying as "this is an adoption case" or "this is a trust case" is BAD. The case is off-archetype by design. The classification pre-loads a template that mostly won't fit. The 2022 reflex to classify IS the failure mode this case format is built to surface.

**The replacement move at Step 2:** name the **situation**, not the type.

| Categorical (the trap) | Situational (the replacement) |
|---|---|
| "This is a trust case." | "Three-month-old SBA deployment, mid-pack volume, named senior officers re-doing AI work, regulatory shadow from CRO with 2-week deadline." |
| "This is an adoption case." | "Deployed product, measured cycle-time hasn't moved despite documented pre-screen gains, narrow user feedback from senior officers." |
| "This is a regulatory case." | "Conservative CRO with OCC-examiner background asking for defensibility memo; she's swing-voted for fintech adoption before; the two prior rank-outs were minor documentation gaps." |

The descriptive name has no built-in solution. The categorical name does, and that built-in is the 2022 muscle firing.

### Step 4 — type-driven structure is the trap

In a 2022 MBB case, "profit = revenue − cost, drivers of each" is the structure that drops from "this is a profitability case." That mapping is the structure-engine of the entire case.

In a 2026 AI-native case, there is no equivalent "this case → this formula" mapping. Bringing a structure to the clarifying answers is the failure mode.

**The replacement move at Step 4:** use a **universal scaffold** instead of a type-driven structure. Three nodes that work for ANY AI-native deployment case:

1. **Stakeholder map + competing objectives.** Who wants what. Where do their objectives conflict. Who can stop the deployment.
2. **Constraints + cost-of-error + what can't be touched.** Regulatory, technical, contractual, political. What is the cost of a wrong automated output.
3. **Thin slice + what proves it.** What you ship first. What eval / metric tells you it worked. What kill criterion stops the expansion.

This is the AI-native equivalent of "revenue/cost/drivers." It is universal because every regulated-AI deployment has these three dimensions. The CONTENT inside each node comes from the clarifying answers, NOT from a template.

For an instantiated example, see `output/halcyon/051926-ai-native-case-research.md` §5 Case OPERATOR walkthrough — same scaffold instantiated against the loan-officer-distrust case.

## The adapted 5-step flow (hold in your head Monday)

| Old habit | Adapted for AI-native |
|---|---|
| 1. Get information | 1. Get information — write the situation verbatim |
| 2. Classify case type | 2. **Name the situation descriptively** (no type, no category) |
| 3. Drill in via clarifying questions | 3. Drill in (5-8 min, zero solution content) |
| 4. Build structure from type | 4. **Build custom structure from clarifying answers** — stakeholders + constraints + thin slice |
| 5. Work through systematically | 5. Work through + re-scope visibly on wrinkles |

## The single most important behavioral hack

**No solution content before minute 8.** Whatever else you do, hold this. The 2022 reflex wants you to demonstrate you have a structure in mind early. The 2026 AI-native panel grades the opposite: how long can you sit in the question. For a 90-min case, aim for 10-15 minutes of scoping before you propose anything structural. That feels too long. It is not.

## Named anti-patterns this case format surfaces

Two anti-patterns tracked in `coaching/anti-pattern-tracker.md`:

1. **Polished problem-solution arc (non-framework MBB reversion variant).** The trap at Step 2/Step 4 firing without a named framework. State problem → name goal → drop to action plan in one breath. Looks operator-y; isn't. Diagnostic: count clarifying questions before first proposed action verb. If zero, the pattern fired. Memory: `feedback_polished_problem_solution_arc_is_mbb_reversion.md`.

2. **Substance-defer instead of hypothesize-and-invite.** When the panel asks for substance under ambiguity and Nick deflects ("I'd love to understand more"). The reversal: 3-beat hypothesize-and-invite move (*"Hypothesis, push back if I'm off..."* → state shape → *"Where's that off?"*). Maps to Nick's named growth edge per `professional-identity.md` (perception-anxiety → inaction). Memory: `feedback_substance_defer_vs_hypothesize_and_invite.md`.

Both surfaced in Case #1 warm rep 2026-05-20. Drill against both in remaining case reps and Monday execution.

## Pre-Monday recall checklist

The 5 things to hold in your head walking into Monday's case (in order, terse):

1. Name the situation, not the type.
2. No solution content before minute 8.
3. Clarifying questions earn the structure — stakeholders, constraints, cost-of-error, success metric, can't-touch list.
4. Universal scaffold: stakeholder map → constraints → thin slice. Custom content from your clarifying answers.
5. Hypothesize and invite, don't defer. *"Hypothesis, push back if I'm off..."* → shape → *"Where's that off?"*
