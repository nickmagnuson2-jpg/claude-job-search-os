# The Adversarial Analysis Pipeline

Last updated: 2026-08-05
**What this is:** a reusable multi-agent method for producing analysis you can actually stake a decision on.
**Where it came from:** built over three rounds on a real multi-option strategy analysis (2026-07-30 → 07-31). Worked example and full artifact trail live in the gitignored `output/<company>/` directory for that engagement. Companion: `framework/multi-agent-workflows.md`.

---

## 0. The meta-lesson, first, because it is the transferable one

**This architecture was not designed up front. Every stage exists because the previous round failed in a specific way.**

| Round | What it produced | How it failed | What that failure bought |
|---|---|---|---|
| **1** | Four option columns | **Invalidated its own frame** — conflated two distinct constraints, no rider test, treated shared infrastructure as a differentiator | The corrected frame (round 2's three corrections) |
| **2** | Four columns + 12-judge panel | **All twelve judges returned "not safe as written."** Eight citation failures. Three defects appeared *independently in every column* | The failure taxonomy in §3, and the realization that convergent defects are frame defects |
| **3** | Amendment + frame audit + anti-laundering | *(specified, not yet run)* | — |

**The method is the loop, not the endpoint.** Each round: run it, read the failures, ask *"is this defect local to one agent, or did the instructions cause it?"*, and promote the answer into the next round's structure. A defect that appears in one column is that column's problem. **A defect that appears in all of them is yours.**

Do not try to write round 3's architecture on day one. You cannot — you do not yet know what your agents will get wrong. Budget for the rounds instead.

---

## 1. Pipeline architecture

```
STAGE 0  frame audit        3 agents, blind to each other, different attack surfaces
              ↓             (BLOCKING finding here stops everything)
STAGE 1  generate/amend     N agents, one per slice, isolated from each other
              ↓             (pipeline, not barrier — judges launch as each lands)
STAGE 2  judge              3 per slice, perspective-diverse lenses
              ↓             (any BLOCKING → targeted re-run, or fix, of THAT SLICE ONLY)
STAGE 2b VERIFY THE FIXES   1 agent per slice, fresh context, given the corrected
              ↓             slice + its findings verbatim. NON-OPTIONAL. See rule 5.
STAGE 3  synthesize         human or orchestrator, unscored
```

### Model assignment is deliberate

| Stage | Model | Why |
|---|---|---|
| Generate | Cheaper / broader | Wide research surface, structured drafting |
| **Judge** | **Stronger / narrower** | Adversarial audit is the harder reasoning task. **A judge weaker than the generator it audits is worse than no judge** — it produces false assurance. |
| Frame audit | Mixed, deliberately | At least one auditor on a *different model* from the rest, so the pipeline does not share a model-level blind spot on its most mechanical check |

### The four structural rules

**1. Isolation by construction.** Each generator sees only its own slice. It has no basis for cross-slice claims — which makes cross-slice claims *mechanically detectable as defects*. If agents can see each other, you lose that detector and gain silent convergence.

> **Corollary, added 2026-08-01 — name what isolation makes unobservable, then give it an owner.** Isolation buys you a detector and costs you a viewpoint. Properties that belong to the *set* rather than to any element — comparability of scope, coverage of the requirement space, consistency of units and denominators — are invisible to every isolated reviewer by construction, because each sees exactly one element. In the origin run, **sixteen agent reviews (twelve judges plus four verifiers) all returned findings and not one caught that two of three slices had priced a scope the source document does not define.** The three cost estimates being compared were not comparable at all. No reviewer was positioned to notice. Assign cross-item properties explicitly, to a reviewer given the whole set and only that question.

**2. Cross-slice comparison is the human's job.** The machine describes; you decide. Synthesis stays **unscored**. Rank criteria *before* re-reading cells, then choose cold.

**3. Perspective diversity, not redundancy.** Three different lenses beat three identical reviewers. Identical prompts produce correlated blind spots — the reviewers all miss the same things for the same reason.

**4. Targeted re-run, never whole-batch regeneration.** A BLOCKING finding sends *that slice* back with the finding quoted verbatim. Regenerating everything discards corrections that cost you a full panel to earn.

**5. Whoever applies the fixes may report "applied." Only an independent check may report "closed."** Stage 2b is not optional and not a formality. One fresh agent per slice, given the corrected slice plus its findings verbatim, asked exactly two questions: *is each finding actually fixed, and did the fix introduce anything new?*

Why this needs to be structural rather than remembered: **having a defect described in front of you feels like the ability to see whether it is gone.** The fixer checks the fix against the same reading of the finding that produced it, so there is no second reader anywhere in the loop. Observed failure modes when Stage 2b is skipped, all three from one run:

- **Fixing the symptom the finding names instead of the defect it describes** — a finding said an objection was raised and disposed of inside a favourable cell; the fix added the objection to the adversarial section and left the disposal untouched.
- **Partial application reported as complete** — two of three missing sources added, and the claim they existed to correct left standing.
- **A new defect introduced by the fix, in the same class as the finding** — an arithmetic error created while repairing an arithmetic finding.

**Corollary — audit the shape of completion claims, not only their content.** Tidy totals, uniform per-slice distributions, and "all N are closed" are signals to look for the unaudited step. In the origin run the orchestrator reported *"28 blocking findings, exactly 7 per column, all closed"*; the human caught it in one sentence without inspecting any finding, purely on the improbability of the shape. Three of the twenty-eight then failed inspection immediately. Also recount before believing a total: **N counts findings, not distinct defects** — convergent findings (the same defect caught by two blind judges) double-count, and dressing that arithmetic up as signal is itself a defect.

### Stage 0 exists because of an asymmetry worth internalizing

**Slice-level errors are independent. Frame-level errors are correlated.**

Your judges audit each slice's *compliance with the frame*. None of them audits the frame. So a frame error propagates into every slice simultaneously and every judge scores it as compliance. This is the single error class the panel is structurally blind to, and it is therefore the highest-leverage check in the pipeline.

### Why Stage 0 uses independent coverage, NOT an adversary

The instinct is to give the frame auditor an adversary that reviews its report. **That fails, for a reason that generalizes:**

> The frame auditor's failure mode is not a *wrong* finding — a wrong finding is caught the moment anyone acts on it. Its failure mode is **silence: missing something.** An adversary reviewing the report can only evaluate what is *in* the report. **You cannot audit an omission by reading the output.**

What surfaces omissions is **independent parallel coverage**: auditor B finding what auditor A missed *is* the false negative, made visible. And because identical auditors share blind spots, they get different attack surfaces:

| Auditor | Attacks |
|---|---|
| **Source diff** | Frame vs. the original brief. What was dropped, added, distorted, silently reinterpreted? Nearly deterministic — the brief is ground truth. |
| **Internal logic** | Contradictions; instructions that make a required output impossible; unverified inherited premises |
| **Evidence base** | Every empirical claim the frame rests on. Live-web corroboration attempts. |

**Reconcile them yourself, raw.** A finding raised by *only one* auditor is the most valuable output of the stage — it is exactly the omission a single auditor would have produced. Do not discount it for lacking corroboration; the others were looking elsewhere by design.

**Same logic applies anywhere you're tempted to add "a reviewer for the reviewer."** Ask first: is the failure mode *wrong output* or *missing output*? Adversaries catch the first. Only independent coverage catches the second.

---

## 2. Anti-laundering: the defect class that degrades long runs

**Confidence launders on every hop.** A claim enters as `[Assumption]`, gets restated in a summary, loses its qualifier, and by the next round it is load-bearing fact. Four observed variants:

| Variant | Signature |
|---|---|
| **Inference wearing a fact label** | An inference under a "Fact" header with a source tag |
| **Corroboration invented** | "independently echoed at [X]" where X says something adjacent but different |
| **Tag taxonomy drift** | Agent mints a tag not on the sanctioned list. **Reliably precedes confidence drift — treat as a leading indicator.** |
| **Assumption → structure** | One untested belief sizing an entire estimate or cell |

### Three countermeasures

**A. Tag monotonicity.** A claim's confidence may go **down** or stay **flat** as it propagates. **Never up** — unless new primary evidence is attached *at that hop* and cited *in the same sentence*. Mechanically checkable; catches all four variants.

**B. Split `[Given]`, because a `[Given]` is a fact about the source document, not a fact about the world.**

| Tag | For | Direction-of-error required? |
|---|---|---|
| `[Given-fact]` | Source-supplied **data** | No |
| `[Given-claim]` | Source-supplied **characterization** ("the highest-volume driver," "the industry benchmark is X") | **Yes** — it is someone's prior, not a measurement. Name who asserts it. |

**C. A load-bearing assumption register** — one cross-cutting file listing every assumption that changes a conclusion if wrong, with what would test it. Agents tag locally; nobody holds the list unless you make them. Split it into **frame-level** (correlated, inherited by all slices) and **slice-level** (single points of failure). Exemplar: `<engagement>-assumption-register.md` in the relevant gitignored `output/` folder.

Wire (A) and (B) into the generator preamble, and make the anti-laundering sweep an explicit judge item — which requires **giving that judge the previous version to diff against.** (We shipped a lens that demanded a non-regression sweep without supplying the predecessor. It would have produced confident "no regression found" verdicts from a judge structurally unable to detect regression — the worst failure shape, because it looks like verification. Check that every lens can actually execute its own checklist.)

---

**6. Every review round carries the original source, not only the prior round's output.** *(Added 2026-08-01.)* A reviewer handed an artifact plus a findings list will audit the artifact, because that is what is in front of it. Drift away from the original requirement is invisible from inside the artifact, and it worsens each round, since every round adds artifact and no round adds spec. In the origin run the defect that survived sixteen reviews was caught in one pass by re-reading the source document with no agents at all.

Two consequences worth wiring in:

- **Put the requirement in every reviewer's context**, and ask at least one reviewer per round the single question *"does this still answer what was actually asked?"*
- **Never let a synthesis artifact become the input to the next round.** This is the anti-laundering rule (§2) recurring at the *synthesis* step rather than the input step. In the origin run the assembled matrix recorded that one option had "no volume number in the case." It had one, on a different denominator, at a line the matrix never cited — and every downstream agent inherited the summary rather than the source, so nothing in the artifact could contradict it.

**7. The picture has to show how much the number is worth.** *(Added 2026-08-01.)* An evidence discipline that holds in prose leaks the moment output becomes an exhibit, a table, or a summary, because **a caveat next to a chart loses to the chart.** A bar with a clipped end reads as bounded no matter what the footnote says, and a computed number and a guess drawn as identical bars are indistinguishable to the reader. This is confidence laundering (§2) in its rendering form, and it is harder to catch because nothing false has to be written for it to happen.

Make the representation carry the tier: solid marks and a real axis only for arithmetic that cannot be wrong; source and confound printed on the exhibit for borrowed comparisons; **open ends drawn open** for estimates with no upper bound; ordinal comparisons drawn with no axis at all so no distance can be read off them; and for a quantity nobody knows, **axes drawn and marks absent, titled with what would fill it.** The empty exhibit is usually the strongest of them: an unknown drawn is concrete and sizeable, where an unknown stated invites the reader to move past it.

## 3. The failure taxonomy — hunt for these by name

Observed across twelve judge reports. Reusable as a review checklist for any multi-agent analysis.

| Defect | What it looks like | Why it survives |
|---|---|---|
| **Unpriced second constraint** | Two scarce resources named; only one ever priced, with the cheap one's argument borrowed for the expensive one | The priced half looks rigorous and carries the unpriced half |
| **Cross-slice superlatives** | "the strongest of the four," "only X can claim this" — from an author who saw one slice | Reads as confident synthesis; contaminates the real synthesis |
| **Unfalsifiable thresholds** | "high share," "predominantly," "clear majority" | Any result can be argued into either bucket afterward — which defeats the point of committing in advance |
| **The self-sealing increment** | The proposed next step is scoped so that no possible result can embarrass the recommendation | Looks like prudent staging |
| **Asymmetric summary selection** | The "what goes on the slide" list strips every disconfirming item while keeping the unverified reassuring one | Each cut is individually defensible; the *set* is not |
| **The appended rebuttal** | A rebuttal bolted onto the strongest-argument-against section, leaving the option undefeated in every branch | Reads as balance; actually disarms the one section built to leave an attack standing |
| **Citation failure** | The figure is not on the cited page — inverted metric, wrong attribution, or absent entirely | A real URL plus a confident tag defeats casual review. **Only fetching catches it.** |
| **Prompt silence as evidence** | "The brief doesn't raise this risk for my option" used as evidence the risk is absent | Wears a `[Given]` tag |
| **Silent scope divergence** *(added 8/1)* | Agents given one shared frame each choose a different slice of the requirement and none states the choice. Outputs look parallel and are not comparable | Nobody lies and each choice is locally reasonable. Only visible from outside the set, which per-slice review never is |
| **Spec drift under review** *(added 8/1)* | Round N+1 audits the artifact against round N's findings. Nothing checks it against the original requirement | Every round adds artifact and no round adds spec, so the artifact becomes the de facto standard |
| **Ambiguity collapsed toward the cheaper reading** *(added 8/1)* | An instruction admitting two readings is restated in the one that ends the work sooner, and the restatement is presented as a summary rather than as a decision | It happens at the moment a provisional answer appears, when the pull to stop analyzing is strongest, and it reads as consolidation |

**Two anti-drift instructions worth putting in every generator prompt:**
1. *"Name at least one piece of evidence that cuts AGAINST your assigned position, and engage it in the cell where it belongs — not in a footnote."*
2. *"Inverse advocacy counts equally: performative self-criticism that would mislead a decision-maker is a finding."* (Self-critical surfaces are where drift hides best — an agent that concedes loudly on a cheap axis while quietly protecting the expensive one reads as scrupulous.)

---

## 4. Reusable dispatch skeleton

A dispatch doc should be **executable cold in a fresh context.** Sections:

1. **State of play** — what each prior round produced, how it failed, what's next
2. **File inventory** — what to read, and **what is forbidden to whom** (isolation is enforced by the reading set, not by good intentions)
3. **Decisions locked** — with who decided and when
4. **Model assignment** — with the reasoning, so it isn't casually changed
5. **Frame** — corrections in force, output discipline, tag rules, forbidden claims
6. **Stage 0 / 1 / 2 prompts** — copy-pasteable, per-agent task emphasis
7. **Assembly instructions**
8. **Standing items** — findings that hold regardless of which option wins

**Reading sets may differ by role, deliberately.** Some judges need the predecessor to diff against; at least one should stay blind to it, because an unanchored fresh read is a different instrument. Say *why* in the doc, or someone will "fix" the inconsistency.

**Give amenders the judge reports on their own prior version.** The corrected artifact is the *output* of correction; the judge report is the *reasoning*. An agent editing without it will smooth away load-bearing hedges, because awkward defensive phrasing looks like bad writing.

---

## 5. When to use this — and when not to

**Use it when:** the output drives an irreversible or expensive decision; the analysis will be attacked by someone competent; evidence is thin and tier-C sourcing is likely; multiple options need *symmetric* treatment; you'll be asked "how do you know?"

**Don't when:** the answer is checkable in one step; the cost of being wrong is low; you need a direction rather than a defensible position. This pipeline is 19 agents. That is the right price for a decision you'll defend under questioning and the wrong price for a first draft.

**What it actually caught** (round 2, one run): 8 citation failures including three figures absent from their cited pages; a unit conflation that had survived four columns and a full round; a "mitigator" that the live literature reversed into a risk; and three defects present in *every* column — which is how we learned they were frame defects, not agent defects.

**Success signal:** the panel returning "not safe as written" on everything is the system **working**, not failing. Round 2's twelve-for-twelve was the most valuable output of the entire process, because it converted invisible drift into a specific fix list.

---

## 6. The compressed version

1. **Budget for rounds.** You cannot design the final architecture before seeing how your agents fail.
2. **Isolate generators** so cross-slice claims become detectable defects.
3. **Judge with the stronger model**, in diverse lenses, three per slice.
4. **Audit the frame separately** — the panel is blind to it, and its errors are correlated.
5. **Independent coverage, not adversaries**, wherever the failure mode is omission.
6. **Enforce tag monotonicity.** Confidence never rises without new cited evidence.
7. **Split `[Given]`** — a given is a fact about the document, not the world.
8. **Targeted re-runs.** Quote the finding. Never regenerate the batch.
9. **Keep synthesis unscored.** Rank criteria before re-reading; choose cold.
10. **Ask of every defect: local, or caused by my instructions?** The second kind is the one that compounds.
11. **"Applied" is mine to say; "closed" is not.** Verify fixes with a fresh reader, and treat a suspiciously tidy completion claim as evidence of an unaudited step.
12. **Carry the spec into every round.** Reviewers audit what is in front of them, so an artifact-only context converges on the artifact and drifts from the requirement. Sixteen reviews missed what one re-read of the source caught.
13. **Name what isolation makes unobservable, and give it an owner.** Cross-item properties — comparability, coverage, consistent denominators — belong to the set and are invisible to every per-item reviewer.
14. **The picture has to show how much the number is worth.** Open ends drawn open, ordinal drawn without an axis, unknowns drawn as empty exhibits. Captions get stripped in transit; the format does not.
15. **Watch for the instruction getting smaller.** Any restatement of a constraint is a place to check whether ambiguity was resolved toward whatever ends the work sooner.

---

## 7. Round 4 — the room (2026-08-05). What survived contact, and what the pipeline never checked

The analysis this pipeline produced was presented and defended live. Per §0, the room is just the
next round: read the failures, ask whether they were local or caused by the instructions, and
promote the answer into the structure.

**Result: the pipeline's outputs held. Its blind spot was total.**

### 7.1 What held

Zero fabricated figures. All arithmetic footed. Zero never-say violations across ~80 minutes under
pressure. No claim from the refuted list resurfaced. An independent clean-room reviewer, given only
the assignment and the artifact, found no unmet instruction. **The anti-laundering discipline of §2
and the tag hygiene of §4 did their job** — under live interrogation there was nothing to walk back.

### 7.2 What the room tested instead

| The room pressed on | Pipeline stage that covered it |
|---|---|
| The operational definition of the frame's own top criterion (**8 turns** to establish it) | none |
| Whether the criteria were **mutually exclusive** (one input was load-bearing in two of four) | none |
| Holding the argument when **interrupted at the frame slide** and never allowed to advance | none |
| Naming a **primitive** under pressure (a service endpoint vs raw data access) | none |

**Not thin coverage. No coverage.** Every stage in §1 asks a version of *"is this true, consistent,
and adequately caveated?"* The room asked *"what do your words mean, and are they the same words
twice?"* Those are different audits and the pipeline only contains the first.

### 7.3 Root cause, per §0's local-or-instructed test — all three are instructed

- **Undefined terms.** Every reviewer received a finished artifact and evaluated its claims. **No
  stage ever required the author to write down, in one line, what each criterion IS.** A term that
  survives four review passes without being defined *feels* defined. The first person to ask found
  out otherwise in ninety seconds.
- **Non-orthogonal criteria.** The two frame audits that could have caught it were scoped to the
  evidence base and the caveat stack. When the frame was then **simplified** (606 lines/17 caveats →
  ~200/7 rules — the right call), **nobody re-audited structure after the rewrite.** The
  simplification silently deleted the only stage where this defect class was checkable. Generalise:
  **a simplification pass must re-run every audit the thing it simplified had passed.**
- **Lost frame custody.** Every rehearsal was a *delivery* rehearsal — timings, verbatim lines,
  drills — and every one assumed the artifact advances. The failure mode that actually occurred had
  never been rehearsed once.

### 7.4 NEW STAGE — the frame hygiene gate (runs before submission, ~20 minutes)

Cheap, mechanical, and it closes all three. Run it on the frame ALONE, never on the artifact.

**(a) Definitions pass.** For every named term in your own frame — each criterion, metric,
threshold, rung — write a one-line operational definition **in under eight words.** Then check each
one is either printed on the page or rehearsed aloud. **If a term cannot be defined in one line, it
is not yet a criterion**; it is a mood, and it will cost you eight turns to discover that in the
room.

**(b) Orthogonality audit.** Give a fresh agent **only** the criterion names plus those one-line
definitions. No evidence, no narrative, no artifact — per §1's anti-anchoring rule, an agent that
has seen the deck will rationalise the overlap. Three questions:
1. Does any single input appear under two criteria?
2. Can two criteria move the same direction for the same underlying reason?
3. Which pair would a smart reader collapse into one, and what would that do to the ranking?
Thirty seconds of agent time. Would have caught the real defect outright.

**(c) Collapse test.** For each criterion, ask what the recommendation would be if that criterion
were deleted. A criterion that changes nothing is decoration; a criterion whose deletion flips the
answer is the one you will be interrogated on, so rehearse that one first.

### 7.5 Where the speed comes from — rebalance, do not just add

The gate above is additive. **Getting faster comes from the other direction: the effort was
allocated almost inversely to what got tested.**

| Investment | Cost | Yield in the room |
|---|---|---|
| Exhaustive claim refutation + evidence inventory (49 entities enumerated, 14 relabels catalogued) | very high | **never invoked.** No published stat was challenged. |
| Multi-round option selection (3 matrix rounds, 12 judges) | very high | **the chosen option was never contested.** The interviewer accepted it and attacked the criteria. |
| Frame audits on the caveat stack (2 rounds, both BLOCKING) | high | **no caveat was raised.** |
| The never-say list | low | **zero violations.** Highest yield per minute in the process. |
| Night-before drilling of ~3 specific coached lines | low | **all three deployed near-verbatim under pressure.** |
| Concession-then-trade structure on the summary page | low | accepted explicitly. |
| Frame hygiene (§7.4) | **~20 min** | **not done — and it is what the room spent its time on.** |

**The transferable rule: protective work is bounded, definitional work is not optional.** Refuting
attacks that might come has sharply diminishing returns past the point where you simply stop
fabricating. Defining your own terms has none — it is the first thing a competent reader tests,
because it is the cheapest way to find out whether you thought or just assembled.

Next time: cap the evidence-refutation and option-selection rounds at "defensible," bank the hours,
and spend twenty minutes on §7.4 plus one custody rep.

### 7.6 Custody rehearsal (delivery, not analysis)

One rep, before any live defense: **the counterpart halts you at the frame page and refuses to
advance.** Deliver the rest of the argument with no visual aid. This trains the one thing that
actually broke, and it is five minutes. Belongs in `/prep-interview` for any
presentation-defense-shaped round, not here.

### 7.7 Additions to §6

16. **Define every term in your own frame in one line, in writing, before you ship.** If it takes
    more than eight words it is not a criterion yet. Undefined terms survive review because
    reviewers audit claims, not vocabulary.
17. **Audit orthogonality on the frame alone.** One input in two criteria is a defect no
    artifact-holding reviewer will report, because the artifact makes the overlap read as emphasis.
18. **A simplification pass must re-run every audit the thing it simplified had passed.** Cutting
    caveats is usually right and it silently deletes stages.
19. **Rehearse losing the floor.** Every delivery rehearsal assumes the artifact advances. The
    likeliest real failure is being stopped on the page that carries your frame.
20. **Protective work is bounded; definitional work is not.** Past "we fabricate nothing,"
    refutation depth stops paying. Budget the saved hours into 16 and 19.
