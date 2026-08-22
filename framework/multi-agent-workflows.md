# Multi-Agent Workflows — Reusable Patterns for Building AI Analysis Systems

A field guide to the agentic patterns this repo uses to run rigorous, repeatable
analyses with many LLM agents. The point is not "prompt an LLM"; it is to compose
*many* narrowly-scoped agents into a system whose output is more correct than any
single call — through fan-out coverage, adversarial verification, and iterative
refinement with an explicit stopping rule.

The three worked examples live in `.claude/workflows/` (invocable templates). This
doc teaches the *patterns* behind them so you can compose new ones.

> Mental model: an **agent** is a pure-ish function `prompt -> structured result`.
> A **workflow** is deterministic control flow (loops, fan-out, conditionals) over
> agent calls. Keep the *judgment* in the agents and the *orchestration* in code.
> That split is the whole game: reliable plumbing, smart-but-bounded workers.

---

## The five core patterns

### 1. Fan-out / gather (coverage through parallelism)
Decompose a question into N independent angles, run one agent per angle in
parallel, then merge. Each agent is blind to the others, so you get genuinely
independent coverage instead of one model's first idea elaborated.

- **Use when:** a question has separable sub-parts (research angles, files to map,
  dimensions to review).
- **Key move:** make the angles genuinely disjoint, and give each agent ONLY its
  angle so it goes deep, not wide.
- **Failure mode:** overlapping angles → redundant findings; too-broad angles →
  shallow ones. Design the decomposition first.

### 2. Adversarial verification (truth through refutation)
Every load-bearing claim from a generator is handed to a *separate* agent whose job
is to **refute** it against primary evidence. Keep only claims that survive. This is
the single highest-leverage reliability move — it catches plausible-but-wrong output
that no amount of better prompting on the generator will.

- **Use when:** the cost of a wrong-but-confident claim is high (research findings,
  extracted labels, code-review findings).
- **Key move — anti-anchoring:** the verifier must NOT see the generator's reasoning.
  Give it the sources + the claim only, and have it independently re-derive. An agent
  that sees the original answer ratifies it; the contamination is silent.
- **Strengthen with:** N skeptics per claim, majority-refute kills it; or
  *perspective-diverse* verifiers (correctness / security / does-it-reproduce) when a
  claim can fail multiple ways.

### 3. Iterative refinement with a convergence gate (quality through rounds)

> ⚠️ **This pattern failed twice under measurement. It has been REPLACED, not patched.**
> Two instrumented runs of `plan-hardening.js` produced 82/84/78/75/76 holes across five rounds with
> ~26 *new* blocking holes every round: the gate never fired, because **"attack this artifact" is an
> unbounded question and fresh critics re-derive it indefinitely.** The revise step also mutated the
> artifact between rounds, so late critics attacked the panel's own output.
>
> **The replacement shipped 2026-08-21** and is specified in `framework/plan-hardening-v2-spec.md`.
> Do not reuse the round-based design below for anything expensive; use the v2 stage graph instead.
> What survives from the text below: distinct critic lenses, a reviser allowed to reject with a
> reason, and the residual-risk-register-not-a-certificate rule.

### 3b. Bounded probe + per-defect regression retest (the replacement for #3)

**Use this instead of #3 whenever the artifact is expensive or irreversible.** Every stage asks a
question with a decidable answer, which is what makes termination arithmetic rather than hope.

```
S0 goal extraction (2 agents, one BLIND to the plan)  → S1 premise gate — HARD STOP if open
→ S2 typed target generation → S3 scoped probes (1/target + 1 unscoped)
→ S4a one agent per hole decides it → S4b one reviser applies the edits
→ S5 per-hole retest ("does hole E4 still fire?") → S6 repo-grounded validation → register
```

**Why this is worth more than #3, measured on the same plan rather than argued:**

| | #3 rounds (v1) | #3b bounded (v2) |
|---|---|---|
| Agents / tokens | 88 / 5.24M | 81 / 2.88M |
| Converged | **No**, 5 rounds | **Yes**, one pass |
| Output | 82 holes, hand-extraction required | 25 holes, each with a verdict |
| Could it tell a real fix from a claimed one? | **No** | **Yes — 14 of 25 "fixed" holes still fired** |
| Premise defects | buried among 82 peers | gated upstream, 3 surfaced at S1 |
| Payload integrity | reconstructed a plan from 3 chars | asserted; run aborts instead |

Five properties do the work, and each maps to a measured v1 failure:

1. **Bounded questions.** "Does hole E4 still fire?" terminates; "attack this plan" does not.
2. **A premise gate that hard-stops.** If the goal is wrong, execution findings are not 21 problems
   but 21 items pending one. v1 reported premise findings as peers of execution findings and they
   got buried — the deepest finding in run 1 arrived as one of 82 and had to be hand-extracted.
3. **An oracle arm.** Two goal statements, one authored **without ever seeing the plan**. The delta
   attributes failure to *aim* versus *execution*. Blindness is asserted structurally, not requested
   in a prompt.
4. **Per-defect regression retest — the single highest-value stage.** A fix is not verified until
   it is checked against the specific defect it claims to remove. On the first live run this caught
   **14 of 25** claimed fixes that did not close their hole. v1 threw the revised plan at a fresh
   panel, which cannot distinguish a real fix from a topic that got mentioned.
5. **Invariants that abort rather than warn.** Payload floor, structural blindness, gate
   unreachability, ID coverage, a written reason on every accepted risk, and bounded mutation
   (retention floor + an accretion budget that scales with commissioned fixes).

**Its known limit:** hole IDs are stable *within* a pass but no prior register is read back, so a
second pass over the same artifact is not yet trustworthy. See the spec's status header.

Generate → critique → revise → **judge** → loop, stopping when a round surfaces no
**new** blocking hole (or a round cap). The explicit stopping rule is what separates
this from endless tinkering — but it must be a *delta* rule, not "until the judge is
satisfied." Looping until a judge declares the artifact sound is anti-convergent by
construction: every fix adds prose, new prose is new attack surface, and N fresh critics
primed to attack will essentially always find something. Equally, **do not return a
pass/fail certificate.** A self-reported "airtight" is a claim the panel makes about
itself, and it has twice been wrong here — most recently certifying a spec that produced
7 real defects on execution, two of which the panel had flagged as blocking in an earlier
round and silently dropped. Return a *residual risk register* (each risk named, with a
status and where to verify it) plus the repo-state claims nobody checked. That is always
achievable and tells the executing agent where to look. Used here to harden a *plan* before executing it, but the shape
generalizes to any artifact (a design, a doc, a piece of code).

- **Use when:** an artifact is expensive to get wrong downstream (a plan that will
  drive many commits; a spec).
- **Key moves:** (a) multiple *distinct* critic lenses per round, not one generic
  reviewer; (b) a reviser that is allowed to REJECT a critique with a reason (don't
  cargo-cult every suggestion); (c) a **fresh** judge each round that re-reads the
  artifact itself, not just trusts that holes were fixed; (d) a severity scheme so
  only *blocking* holes gate convergence.
- **Failure mode:** the reviser bloats the artifact fixing minor nits. Gate on
  blocking-only; let major/minor accrue as a backlog.

### 4. Extract → verify (structured data from a messy corpus)
Pull structured records out of many unstructured sources (notes, logs, docs), one
agent per batch, then run pattern #2 (adversarial verify) on each record. Produces a
clean dataset with provenance and a confidence/disagreement flag per row.

- **Use when:** you need a labeled dataset out of free text (outcomes, entities,
  events) and correctness matters.
- **Key moves:** require a **source citation** per extracted field; run a
  deterministic first-pass where possible and only spend LLM tokens on the fuzzy
  residue; carry a `disagreement` flag when verifier ≠ extractor rather than silently
  overwriting.

### 5. Loop-until-dry / until-budget (exhaustive discovery)
For unknown-size work (find all the bugs, surface all the edge cases), keep spawning
finders until K consecutive rounds surface nothing new, deduping against everything
seen. For token-bounded runs, loop while a budget remains. A simple `while count < N`
misses the long tail; the "consecutive-dry" or budget rule is the correct shape.

---

## Design principles (the load-bearing ones)

- **Structured output over parsing.** Give each agent a JSON schema; validation
  happens at the tool layer and the model retries on mismatch. Never regex an agent's
  prose. Schemas are also documentation of what a stage returns.
- **Anti-anchoring for any "independent" agent.** Plan critics, verifiers, competing
  designers get ONLY the goal/spec/sources — never the artifact they'd anchor to. If
  an agent is meant to be a second opinion, prove it never saw the first.
- **Pipeline by default, barrier only when you must gather.** Run each item through
  all its stages independently (extract→verify per item) so fast items don't wait on
  slow ones. Only force a barrier (await all of stage N before stage N+1) when a stage
  genuinely needs the whole prior set — dedup, global ranking, an early-exit count.
- **Deterministic first, LLM for judgment only.** Do the counting/parsing/matching in
  code; reserve agents for synthesis, judgment, generation. Cheaper, testable, and it
  keeps the LLM's context focused on the part only it can do.
- **A stopping rule is part of the design.** Convergence judge, consecutive-dry
  counter, budget ceiling, round cap. Without one you either under-cover (stop too
  early) or burn tokens (never stop).
- **Cost awareness.** Concurrency is capped (~10-16 agents at once); total agents are
  bounded. A 3-round × 8-critic panel is ~30 agents and can run into millions of
  tokens. Scale the fleet to the stakes: a few finders for a quick check, a large
  panel for a decision that compounds.
- **Provenance and disagreement are outputs, not afterthoughts.** A dataset row
  without its source, or a "verified" claim that hides that the verifier disagreed, is
  a landmine for whoever trusts it later.

## When to reach for a workflow vs a single agent
- **Single Agent (Task):** one focused investigation, a search you'd otherwise do
  yourself, an isolated sub-task. Cheap, no orchestration.
- **Workflow (multi-agent):** you need coverage (fan-out), confidence (adversarial
  verify), or scale one context can't hold (a corpus, a migration). The tell: you're
  about to do the same analysis N times, or you want an independent check on your own
  conclusion.
- **Hybrid (usually right):** scout inline first to discover the work-list (list the
  files, find the entities, scope the diff), THEN fan out over it. You don't need to
  know the shape before the task — only before the orchestration step.

---

## The three reusable templates (`.claude/workflows/`)

Each is PII-free and parameterized: your specific subject/data flows in via `args` at
run time, never hardcoded (this repo is public). Invoke by name, or via `scriptPath`.

| Template | Pattern(s) | Give it (`args`) | Produces |
|---|---|---|---|
| `plan-hardening.js` | **#3b bounded probe + retest** | `planPath` **or** `planText` (NOT `plan`) + `context` (+ optional `outPath`, `registerPath`, `minPlanChars`, `minRetention`) | a revised plan + a residual risk register with a per-hole retest verdict + repo-grounded validations, both **persisted to disk** |
| `research-audit.js` | #1 fan-out + #2 verify | a subject + systems, each with current-state + research angles | cited, adversarially-validated recommendations |
| `extract-verify.js` | #4 extract→verify | a manifest of entities + their source files + a label taxonomy | a verified per-entity ledger with provenance |

**Invoke with `scriptPath`, not `name`.** `Workflow({name: "..."})` resolves a **stale snapshot**
of the script rather than the file on disk, and announces nothing when it does. Measured
2026-08-21: a run launched two minutes after a rewrite executed the *previous* version for 40
minutes. Use `Workflow({scriptPath: ".claude/workflows/plan-hardening.js", args: {...}})`, and
verify the persisted script's header before letting an expensive run proceed.

To adapt one, copy it, keep the skeleton (the loop / fan-out / schema discipline),
and change the prompts and schema for your domain. The skeleton is the reusable part;
the prompts are the disposable part.

---

## A worked example, end to end (how these compose)
A real analysis in this repo chained three of them:
1. **research-audit** over three scoring systems → a cited best-practices doc with
   per-system verdicts (fan-out research, each finding adversarially validated).
2. **plan-hardening** on the follow-up data-extraction plan → found blocking holes
   (sealed-data leakage risk, selection-bias, n-too-small). *That run used the superseded
   round-based design; the v2 stage graph above replaced it on 2026-08-21.*
3. **extract-verify** over the corpus → a verified outcome ledger, each label
   re-derived by a fresh-context agent, feeding a measurement harness.

The lesson: no single agent produced the result. **Coverage** (1), **rigor** (2), and
**a clean dataset** (3) each came from a different pattern, and the patterns are
reusable across totally different subjects. That reuse — not any one output — is the
compounding asset.

---

## Anti-patterns
- One mega-prompt doing everything → no independence, no verification, undebuggable.
- A "reviewer" agent that was shown the answer → anchored ratification, not review.
- Parsing prose instead of a schema → brittle, silent failures.
- Barriers everywhere → wasted wall-clock; fast items wait on slow ones.
- No stopping rule → runaway loops or premature stops.
- Silent truncation (top-N, no-retry, sampling) not logged → reads as "covered
  everything" when it didn't. Always `log()` what was dropped.
- Hardcoding personal/subject data into a template → not reusable, and in a public
  repo, a PII leak. Parameterize via `args`.
