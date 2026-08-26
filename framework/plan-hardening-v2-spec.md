# Plan Hardening v2 — Specification

**Status: BUILT, with one named gap (2026-08-21).** `.claude/workflows/plan-hardening.js`
implements the S0-S6 stage graph. **Four of the six §9 invariants are assertions exactly as
specified** (§9.1, §9.2, §9.3, §9.5); §9.4 is half-built and §9.6 was *replaced* rather than
implemented — see both gaps below. Each implemented guard is broken on purpose by a test in
`tests/scripts/test_plan_hardening_invariants.py` (**33 tests**; the guards were mutation-tested by
hand — `tools/mutation_check.py` is a Python AST mutator and cannot parse JavaScript, so this repo's
mutation instrument has never run against this file). One run has executed end to end with the
register persisted
(`output/analysis/082126-plan-hardening-register-v2run1.json`, run `wf_a440ab49-405`).

**Gap 1, named rather than glossed:** §9.4's *cross-pass* half is NOT built. Hole IDs are
assigned deterministically within a pass and retest verdicts key to them, but no prior register is
ever read back, so an ID from pass *n* is not guaranteed to denote the same hole in pass *n+1*.
Single-pass runs are sound; a second pass over the same plan is not yet trustworthy. That matters
now rather than hypothetically: the first live run returned `OPEN` with 14 holes still firing, so a
second pass is required work, not a thought experiment.

**Gap 2 — §9.6 as written was never implemented.** §9.6 specifies "the diff handed to the new-holes
agent must equal the sum of recorded fixes." No such check exists. What the code enforces under the
name INVARIANT 6 is two *different* properties, added 2026-08-21 and both well-tested: **6a
retention** (≥60% of substantive lines survive verbatim) and **6b accretion** (length ≤ a budget
that scales with commissioned fixes, hard-capped at 3x). They guard the same failure v1 exhibited —
a reviser re-authoring the plan — by a different mechanism. §9 has been updated to describe what is
actually enforced, and the original §9.6 text is preserved there as unimplemented.

Cite this document as built for what §11 lists as done, and never for cross-pass behavior or for a
diff-equals-sum-of-fixes guarantee.

**Supersedes:** the v1 behavior of `.claude/workflows/plan-hardening.js` (unbounded multi-round
critique with a revision loop). v1 is not deleted; it is superseded, and §10 records the measured
reasons so the design is not re-derived from scratch.

---

## 1. What v1 got wrong, measured

Two runs against the same plan, instrumented.

| | Run 1 | Run 2 |
|---|---|---|
| Agents | 88 | ~35 (2-round cap) |
| Tokens | 5.24M | — |
| Rounds | 5 | 2 |
| Converged | **No** | — |
| Holes per round | 82 / 84 / 78 / 75 / 76 | — |
| *New* blocking per round | 30 / 24 / 30 / 25 / 26 | — |

Five defects, each of which v2 addresses by construction:

**D1 — the question was unbounded, so the loop could not converge.** "Attack this plan" has no
terminating answer. Each round's critics were fresh, re-derived overlapping objections, and every
one registered as new. Total hole volume never dropped across five rounds.

**D2 — the plan mutated under the panel.** A revise step folded holes into the plan between rounds,
so by the final round the critics were attacking an artifact the panel itself had authored. The loop
closed on itself.

**D3 — payload loss was silent.** The final reviser received the literal string `ial` instead of the
plan body, **reconstructed the worklist from the critics' paraphrases**, renamed items, and dropped
others. It flagged this in its own header, which is the only reason it was caught. Nothing asserted
the payload.

**D4 — premise findings were reported as peers of execution findings.** The deepest finding in run 1
was that the plan's goal might be aimed at a channel carrying almost no traffic. It arrived phrased
as one of 82 holes and was not extracted. Crisp execution findings crowd out soft premise findings
in a flat register, every time.

**D5 — volume was mistaken for rigor.** 82 holes is a haystack, not a review. The findings that
mattered had to be hand-extracted afterward, which is the work the harness was supposed to do.

Two things v1 got right and v2 keeps: **scoped adversarial critique** (round 1 alone produced
several genuinely load-bearing findings), and **repo-grounded validation** (an independent pass that
checks factual claims against real state was the most reliable arm in both runs).

---

## 2. Core design claim

> **Bounded questions converge. Unbounded questions do not.**

Every stage below asks a question with a decidable answer. "Is hole E4 closed?" terminates. "Attack
this plan" does not. Termination becomes arithmetic rather than hope.

The second claim, equally load-bearing:

> **Premise findings are upstream of execution findings and must gate them, not sit beside them.**

If the goal is wrong, execution findings are not 21 problems; they are 21 items pending the
resolution of one. Merging them into a flat count misrepresents the state and buries the premise.

---

## 3. Two layers, one register, hard stop between them

- **Premise layer** — is `G` the right goal? Is the problem framed correctly?
- **Execution layer** — given `G`, does the plan achieve it?

**The gate is a HARD STOP.** If `premise_status` is `open`, the execution stage does not run. This
is deliberate and it is the expensive choice: the observed failure was five rounds of execution
critique polishing a plan whose aim had never been checked. A banner would be more forgiving when
time-boxed; it also reproduces the failure. Hard stop.

---

## 4. Stage graph

```
  S0  GOAL EXTRACTION      2 agents, independent, one blind to the plan
       |
  S1  PREMISE GATE         1 agent + measurement gate     ── HARD STOP ──┐
       |                                                                 |
  S2  TARGET GENERATION    1 agent (typed, from 4 generators)            |
       |                                                          premise_status: open
  S3  SCOPED PROBES        N agents (1/target) + 1 unscoped              |
       |                                                                 |
  S4  FIX                  orchestrator or operator; keyed to hole IDs   |
       |                                                                 |
  S5  RETEST               N agents (1/hole ID) + 1 new-holes-only       |
       |                                                                 |
  S6  VALIDATION           1 agent, repo-grounded, independent model     |
       |                                                                 |
     REGISTER  <───────────────────────────────────────────────────────  ┘
```

### S0 — Goal extraction (2 agents)

Two agents produce a goal statement for the same work:

- **`goal_from_plan`** — reads the plan, states what it is trying to achieve.
- **`goal_from_problem`** — reads **only the problem context, never the plan**, and states what a
  plan in this situation should achieve.

The blindness is mandatory, per the anti-anchoring rule: an agent that has read the plan recovers
the plan's own self-description, which is precisely the artifact under suspicion.

**This pair is the oracle arm.** It bypasses the plan the way an injected rule bypasses retrieval,
and the delta attributes the failure to *aim* versus *execution*.

### S1 — Premise gate (1 agent)

A third agent receives both goal statements, **not the plan**, and answers two bounded questions.
(Earlier drafts said "1 agent + deterministic diff." **There is no deterministic diff** — the
materiality of the delta is the model's judgment, returned in `PREMISE_SCHEMA.delta`. Treat it as a
judgment, not a computation.)

1. Is the delta between them material or immaterial? (material = they describe different outcomes,
   different success conditions, or different beneficiaries)
2. Independent of the delta: is `goal_from_problem` itself the right goal, or is the problem framed
   wrongly upstream of both?
3. **The measurement question (added 2026-08-25).** Which premises does this work rest on that a
   *cheap measurement* would settle — a grep, a count, a query, one API call, reading a log — rather
   than argument? Return each with its concrete command, rough cost, and whether it has **already
   been run with the observed value in hand**. Intending to measure later is `no`. An empty list is
   the right answer for a plan that genuinely rests on nothing measurable; do not invent
   measurements to look rigorous.

   **Why this sits in the premise gate and not in the critique.** Critique can only explore answers
   somebody already imagined. A measurement can return an answer that was in nobody's hypothesis
   space — which is not a hypothetical: on 2026-08-21 an 88-agent, 5.24M-token hardening run of the
   memory-hygiene plan returned `UNCONVERGED`, produced a revision no participant had read, and its
   own handoff says do not execute it. On 2026-08-25 a roughly 15-minute measurement invalidated one
   worklist item outright, re-scoped the largest one, and returned a third option the plan's own
   binary had no slot for. The expensive error in plan critique is arguing at length about a question
   a fifteen-minute count answers.

Output sets `premise_status`:

- `resolved` → proceed to S2.
- `open` → **STOP.** Set by a material delta, by `goal_is_right` of `no`/`unclear`, **or by any
  `measurable_premises` entry with `measured: no`** — the last of these is forced in code after the
  agent returns, not left to the agent's own status field (see Invariant 7). Emit the premise findings and the register. Do not run S2-S5. The deliverable
  is the premise finding; that is a successful run, not a failed one.

### S2 — Target generation (1 agent)

Targets are derived, never freeformed. Two inputs.

**(a) Type the plan.** Exactly one of four:

| Type | Success is | Failure mode it invites |
|---|---|---|
| **Diagnostic** | the question is answered, *including "no"* | can only confirm, never falsify |
| **Intervention** | pre-registered before/after delta | no baseline, so no attribution |
| **Guard** | fires on real violations, silent on near-misses | activation measured, restraint never |
| **Deliverable** | acceptance criteria met **and someone uses it** | ships to a surface nobody runs |

**A plan that resists typing is doing more than one thing and should be split. Emit that as a
premise finding and stop.**

**(b) Run the four generators** over the plan, each producing candidate targets:

1. **Stated constraints** — the plan's own declared rules. Self-violation is the highest-yield
   target class and the cheapest to check.
2. **Irreversible steps** — anything that cannot be undone, plus anything whose damage propagates
   (backups, mirrors, published artifacts, sent messages).
3. **Cost drivers** — the two or three steps consuming most of the budget. Underestimates here
   dominate the plan's real cost.
4. **Load-bearing assumptions** — claims that, if false, collapse an item. Especially claims about
   system state that were asserted rather than measured.

Plus the type's own failure mode from the table, always included as a target.

Output: 5-9 named targets, each with an explicit **"what would count as a hole here."** Fewer than 5
suggests a shallow read; more than 9 reproduces v1's volume problem.

### S3 — Scoped probes (N + 1 agents)

One agent per target. Each is told to find holes **only within its target** and to return few,
high-confidence holes rather than volume. Each hole carries a concrete failure scenario: inputs or
circumstances → the wrong outcome. A hole with no failure scenario is not a hole.

**Plus exactly one unscoped agent** whose only question is: *"what did this target list miss?"*

This agent is not optional and is the deliberate cost of scoping. Scoping structurally cannot find
the hole nobody thought to look for. In run 1, the single best finding (an irreversible step whose
damage propagated through a nightly mirror, with no rollback path) came from an angle that would not
have appeared on a generated target list.

### S4 — Fix (SPLIT IN TWO — S4a: 1 agent per hole; S4b: 1 reviser)

**Implementation note, 2026-08-21.** A single agent asked to disposition 25 holes *and* rewrite the
plan returned dispositions for E1-E8 and stopped. "Disposition N holes" is unbounded in the same
family as "attack this plan." The stage is therefore split: **S4a** runs one agent per hole (one
retry each), so ID coverage is structural; **S4b** runs one reviser that applies the recorded edits
and reviews nothing, with one bounded retry if it violates §9.6's replacement. Agent count is
therefore **≈ N_holes + 1**, not the "orchestrator or operator" this section originally described.

For each open hole, the plan is edited and the fix recorded against the **hole ID**. **The
implementation offers two dispositions, not three** — `DISPOSITION_SCHEMA` is `fixed | accepted`.
`moot` is specified below and is NOT implemented; see §5.

- `fixed` — the plan changed; record what changed.
- `accepted` — deliberately not fixed. **Requires a written reason.** Same contract as
  `tools/mutation-allow.json`: an allowlist without justification is how a gate decays back into
  "we looked at it."
- `moot(<premise-id>)` — invalidated by an unresolved premise finding.

### S5 — Retest (N + 1 agents)

**This is the stage that distinguishes v2 from v1.** For each hole ID, a fresh agent receives *the
original hole* and *the revised plan* and answers one bounded question: **does this specific hole
still fire?**

`closed` · `still-fires` · `fix-introduced-a-new-problem`

This is the regression discipline applied to plans rather than code: a fix is not verified until it
is checked against the specific defect it claims to remove. v1 instead threw the revised plan to a
general panel, which is the equivalent of re-running a whole suite and hoping the relevant assertion
exists.

**Plus exactly one agent scoped to new holes only:** "the following changed since the last pass;
did any of these changes introduce a NEW blocking hole?" It is shown the diff, not the whole plan,
so it cannot re-derive the original findings.

### S6 — Validation (1 agent, independent model)

Every factual claim about system state — in the plan **and in the findings** — is checked against
reality by an agent on a different model with tool access. Verdicts: `CONFIRMED` · `REFUTED` ·
`UNVERIFIABLE`.

Two rules learned from v1, where the validator itself produced false refutations:

- **Name the scope in the same sentence as the verdict.** A refutation must state what was checked
  and why that scope would have contained the thing. v1's validator refuted a true file count by
  comparing a raw recursive file count against a filtered one — two different denominators, reported
  as a contradiction.
- **Check configuration in every location it can live**, not only the nearest one. v1's validator
  declared a capability absent after checking one local config file while it was configured in the
  global one. That was its top blocking finding and it was wrong.

---

## 5. Register schema

One durable record per plan. **Hole IDs are stable across passes** — that is what makes S5 possible
at all. *(Aspirational: see Gap 1. IDs are stable WITHIN a pass; no prior register is read back.)*

⚠️ **Five fields below are specified and NOT emitted by the implementation** (verified 2026-08-21
against the live register): `plan`, `pass`, per-finding `validation:`, `invalidates:`, and the
`moot(<premise-id>)` status. Validations are emitted as a separate top-level `validations` array,
not keyed onto findings. The implementation additionally emits `bounded_mutation`,
`validation_coverage`, `new_holes_from_fix`, `harness_self_check`, and `persisted`, none of which
appear below. **Read the live register for the real shape; this block is the design target.**

```yaml
plan: <stable-id>
pass: <n>
plan_type: diagnostic | intervention | guard | deliverable

goal_from_problem: "<authored WITHOUT the plan in context>"
goal_from_plan:    "<authored FROM the plan>"
goal_delta:        material | immaterial
premise_status:    open | resolved        # HARD GATE on the execution stage

findings:
  - id: P1
    layer: premise
    claim: "<one sentence>"
    failure_scenario: "<concrete>"
    status: open | fixed | accepted
    reason: "<required when accepted>"
    invalidates: [E2, E5]

  - id: E2
    layer: execution
    target: irreversibility            # which target produced it; unscoped agent uses `unscoped`
    claim: "<one sentence>"
    failure_scenario: "<concrete>"
    status: open | fixed | accepted | moot(P1)
    reason: "<required when accepted>"
    retest: closed | still-fires | fix-introduced-new | not-yet-run
    validation: CONFIRMED | REFUTED | UNVERIFIABLE | n/a
```

`moot(P1)` is the field that makes the two layers fold into one analysis without merging them. The
execution finding stays in the register — the work is not lost — but it does not count as open while
its premise is unresolved.

---

## 6. Termination

The pass ends when **every finding is `fixed`, `accepted` with a reason, or `moot`**, and the S5
retest confirms each `fixed` hole is `closed`.

⚠️ **`moot` is not implementable today** — the schema offers only `fixed | accepted`, so this
termination condition cannot be reached by that route. The implementation adds a fourth terminal
state, **`OPEN`**, for the case this section does not cover: findings dispositioned `fixed` whose
retest returns `still-fires`. The first live run terminated `OPEN` with 14 such holes. Terminal
states in the code are `PREMISE-OPEN | CLOSED | RESIDUAL | OPEN`.

Contrast with v1, which ended when "no new blocking hole appears" — a condition that never fired
because the question generating the holes was unbounded.

Three terminal states, all legitimate outcomes:

- `PREMISE-OPEN` — stopped at S1. The premise finding is the deliverable.
- `CLOSED` — all findings resolved and retested.
- `RESIDUAL` — some findings `accepted` with reasons. **This is normal.** The output is a residual
  risk register, never a pass/fail certificate.

---

## 7. Cost model

| Stage | Agents |
|---|---|
| S0 goal extraction | 2 |
| S1 premise gate | 1 |
| S2 target generation | 1 |
| S3 probes | N + 1 |
| **S4a dispositions** | **N_holes** (1 per hole, +1 retry each on failure) |
| **S4b revise** | **1** (+1 bounded retry if it violates 6a/6b) |
| S5 retest | M + 1 |
| S6 validation | **up to 12** (one per claim, capped; the cap is logged) |

**Corrected 2026-08-21.** The original "≈ N + M + 7" **omitted S4 entirely** and counted S6 as one
agent. The real shape is **≈ N_targets + N_holes + M_retests + ~16**. The first live run:
**81 agents, 2.88M tokens, 40 minutes** on a plan yielding 25 holes — against v1's 88 agents and
5.24M tokens for a worse, unconverged result. A run that stops at the premise gate still costs
**~4 plus one validator per premise finding**.

---

## 8. Harness self-check

The metric that says the process is working rather than producing volume: **when premise findings
arrive.**

- Healthy: premise findings surface at S1, and are rare at S3.
- Unhealthy: premise findings only appear from the S3 unscoped agent, meaning goal extraction is too
  weak and is being rescued by luck.

Track the ratio across passes. It is the one number that audits the harness itself, and both v1 runs
lacked it entirely.

---

## 9. Invariants the implementation must enforce

Not prose — these are assertions the script makes, and each is a check a gate reads:

1. **Payload assertion.** Before any stage that consumes the plan, assert the plan text is non-empty
   and above a floor length. A short payload **aborts the run**; it must never be reconstructed.
   (v1 rebuilt a plan from a 3-character payload.)
2. **Blindness assertion.** The `goal_from_problem` agent's prompt must not contain the plan text.
   Assert structurally, not by instruction.
3. **Gate assertion.** S2-S5 are unreachable while `premise_status: open`.
4. **ID stability.** A hole ID assigned in pass *n* refers to the same hole in pass *n+1*. Retest
   verdicts are keyed to IDs, never to positions or titles.
5. **Reason requirement.** `status: accepted` without a non-empty `reason` fails the run.
6. **Bounded mutation.** *(REPLACED 2026-08-21. The original text — "no plan mutation between probe
   and retest except through recorded, hole-keyed fixes; the diff handed to the new-holes agent must
   equal the sum of recorded fixes" — was **never implemented**, and is retained here so the
   substitution is visible rather than silent.)* What the code enforces instead, as two separate
   assertions, because the first live fire proved they measure different things:
   - **6a reconstruction** — ≥`MIN_RETENTION` (default 0.6) of the original's substantive lines must
     survive **verbatim**. Independent of hole count. Catches a reviser that paraphrases the plan and
     lands inside any length budget.
   - **6b accretion** — length ≤ `min(original × 1.15 + fixes × perFixChars, original × maxTotalGrowth)`
     (defaults 800 and 3). Scales with commissioned work, because a flat ratio encodes "a few small
     fixes" and aborts a faithful 25-fix edit. The 3x ceiling is required: without it, 25 fixes on a
     short plan permitted 12.66x.
   Both are reported in the register as `bounded_mutation` on success as well as failure, so
   accretion is visible rather than silent.
7. **Measurement gate.** *(Added 2026-08-25.)* If the premise agent returns any `measurable_premises`
   entry with `measured: "no"`, the orchestrator **overrides `premise_status` to `open`** and emits
   one `M<n>` premise finding per unrun measurement, each naming the concrete command and its
   estimated cost. Enforced in code after the agent returns, deliberately: a prose instruction is
   precisely what a model rationalises past, and the whole point of this gate is that the plan does
   not get argued about until the cheap facts are in hand. The guard must discriminate, not merely
   stop — a fully measured premise proceeds, and an empty `measurable_premises` list is legitimate.
   Origin: 2nd fire of `feedback_measure_before_restructuring_a_thesis`.

---

## 10. Relationship to the layer split

The premise/execution split is the same instrument as separating retrieval failure from judgment
failure in a rule-governed system: two layers, and an **oracle arm** that bypasses the suspect layer
to attribute the failure. Here the oracle arm is the plan-blind `goal_from_problem` agent.

The general principle, worth stating because it recurs: **when a system can fail at two layers, a
single verdict hides which one broke. Measuring the layers separately and adding an arm that
bypasses one of them is what makes the diagnosis actionable.**

---

## 11. What would make this real

**Superseded by the status header at the top of this file, which is authoritative.** This section is
retained as the *criteria*, each marked against a fresh-agent audit run 2026-08-21 that checked every
claim in this document against the code.

1. **DONE** — `.claude/workflows/plan-hardening.js` implements the S0-S6 stage graph. The audit
   corrected three descriptions that had drifted: S1 has no deterministic diff, S4 is split in two
   and was omitted from the §7 cost model entirely, and S6 fans out to as many as 12 validators
   rather than 1. Those corrections are recorded in §4 and §7.
2. **PARTIAL — 4 of 6.** §9.1 (payload floor), §9.2 (structural blindness), §9.3 (gate
   unreachability) and §9.5 (written reason) are assertions exactly as specified. **§9.4 is half**
   — IDs are stable within a pass, nothing reads a prior register (Gap 1). **§9.6 was replaced, not
   implemented** (Gap 2); the substitute guards are real and tested, but they are not what §9.6 said.
3. **DONE** — 33 tests; 10 assert failure plus the specific invariant string, and the driver
   executes the real workflow source under a stubbed DSL rather than regex-matching it, so a syntax
   error fails rather than passing vacuously. All four cases named in the original criterion exist.
   There are also restraint arms (a legitimate fix must NOT trip 6a/6b; no persist agent runs when no
   path is given; a failed write must not report as durable). **Caveat:** the guards were mutated by
   hand. `tools/mutation_check.py` is a Python AST mutator and cannot parse JavaScript, so this
   repo's mutation instrument has never run against this file, and there is no
   `tools/mutation-allow.json` entry for it.
4. **DONE** — run `wf_a440ab49-405`, register persisted to
   `output/analysis/082126-plan-hardening-register-v2run1.json`.

Cite this document against the header's two named gaps, never as uniformly built.
