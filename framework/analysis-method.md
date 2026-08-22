# Analysis Method — the standing rules

**Created 2026-08-13.** Version 1. Changes land in `analysis-method-CHANGELOG.md`.

This file is deliberately thin. It holds **only what cannot flip when the problem changes.** Anything
that flips is per-engagement and belongs in a `frame.yaml`, not here. If this file grows past a few
minutes of reading, something per-engagement has leaked in and should be evicted.

## The three layers

| Layer | Where | Holds |
|---|---|---|
| **Standing** | this file, `frame-schema.yaml`, `method-moves.yaml` | Encoded once. Free at run time |
| **Execution** | `.claude/workflows/analysis-method.js` | Stages. Agent stages run unbounded; operator stages stop and ask |
| **State** | `output/<slug>/frame.yaml` | One file, whole engagement |
| **Gates** | `tools/check_frame_integrity.py`, `framework/deck-rubric.md` | Deterministic where possible, blind-agent or human where not |

## The unit of cost

**Operator judgment moments, never wall-clock minutes.** Agent time is unbounded; let it run. Every
stage declares `requires_operator: yes | no` and, if yes, the specific `decision:` stated as a question.

**Design objective: minimize the COUNT of judgment moments and maximize the leverage of each.** A
forty-stage workflow where thirty-eight stages are agent-run and two need the operator is good. A
five-stage workflow where all five need the operator is worse. Stage count is not the metric.

## The three judgment classes

| Class | Decided | Cost | Owner |
|---|---|---|---|
| **Standing** | Once, encoded here | Free | Nobody |
| **Per-engagement** | Every time, because the problem statement changed | The scarce resource | Operator |
| **Derived** | Not decided at all; falls out of the two above | Free | Agent |

**The test for standing: try to construct a problem statement where the answer flips.** If you can, it
is per-engagement. If you genuinely cannot, it is standing, and **the attempt gets recorded.** A
standing designation without a surviving falsification attempt is a designation by fiat, which is the
documented root failure: a per-engagement judgment promoted to standing, then used to shield the
reasoning underneath it.

**A rule can be standing while its contents are per-engagement.** The form is standing; the filling is
not. "A never-say list exists, enumerated and absolute" is standing; which items are on it is not.

## The six judgment moments

The minimum viable set. Nothing else in the method requires the operator.

1. The problem statement
2. **The problem type** — highest leverage in the whole method, because one call unlocks an entire
   lookup table of derived work
3. The two mode parameters
4. Which elements are in the set, and why it closes there
5. The disposition of each unknown
6. The recommendation, and any override

## The two mode parameters

Case-versus-engagement is the wrong cut. The real parameters are:

- **Can I get more data?** Sets the disposition of unknowns
- **How many shots do I get?** Sets the weight on custody and rehearsal

One spine, two parameters. A one-shot fixed-data run and an iterative obtainable-data run are the same
method with different dispositions.

## Discovery splits in two, and the order is not optional

| | **D1 — specification** | **D2 — data** |
|---|---|---|
| Answers | What am I doing, in what shape, what is in and out | What is actually true here |
| Stopping rule | The problem statement is locked and the mode is set | The schema's required fields are populated |

**D1 MUST precede D2, because the spec determines what data is worth gathering.** Run D2 first and you
build the evidence index before anyone knows what will consume it.

**A D2 output with no consumer in the frame, and no guard role, is reading rather than discovery.**
Reading is fine. It is just not on the critical path.

## Every discovery output declares its destiny

One of three, and only the third is waste:

1. **Destined for the room.** Protected by the reachability rules
2. **A guard that must exist and never surfaces.** Its value is that nothing happened. A never-say list
   with zero violations is the highest-yield item per minute in the corpus, and it is invisible
3. **Neither**

**The goal is not to shrink discovery. It is to make the discovery reach the room.** A lot of deep work
correctly surfaces as one defensible sentence.

## The conversion law

**This is the binding constraint on every design decision here.**

> Enumerated + absolute + small-N + rehearsed aloud + no in-moment judgment → **converts.**
> Abstract prescriptions → **do not.**

Evidence: an 18-item never-say list produced zero violations in roughly eighty minutes; three drilled
lines deployed near-verbatim; every abstract rule failed. **Corollary: a rule that requires in-moment
judgment will not convert and should be rewritten or cut.**

**Second corollary, and the reason this file is short:** a prose framework doc is the tier with a
documented conversion rate of zero. Four written artifacts predicted a failure correctly and changed
nothing. This file is standing rules only, because standing rules are the one kind of prose nobody has
to recall under pressure. The checker reads them.

## The five properties of a closed element set

Provenance · closure · reasoned exclusion · interaction logic · operational definition.

## The invariant

**Every named element carries the level below it, and that level is what gets tested.** A criterion
carries its measure. A capability carries its surface and its owner. A quantity carries its
decomposition and dominant input. A driver carries the test that would kill it.

What "the level below" means for a given element is a **lookup** by problem type, not a judgment. The
table lives in `method-moves.yaml`.

## The three dispositions

Every unknown is one of: **assumption** (unknowable in the timebox; carries a label, a basis, a
sensitivity note) · **question** (needs a named human's judgment) · **data request** (exists in a
system; carries an owner and a date).

**The disposition changes by mode. The item does not.**

Dispositions are assigned **in the spec, before analysis runs**, not discovered during it.

## The recommendation form

Imperative verb + quantified object + named constraint. One sentence. Confidence stated, not implied.
A next action naming who disposes it.

## Validation work: rebuild, never review

Where the deliverable already exists and the job is to establish whether to trust it: **rebuild
independently, then diff. Do not review in place.** Reviewing anchors you to the artifact and reduces
the question to "does each entry look wrong."

**The diff is the deliverable.** Neither the original nor the rebuild is the product.

**In machine-assisted work, the human's criteria are the product.** Lead with the criteria and their
derivation, never with the output. The output is evidence for the criteria, not the reverse.

## The compounding loop

**The acceptance criterion for this whole method is that it improves after every feedback session.**
Not that any single run is good. If a session can happen without the method changing or being
explicitly confirmed unchanged, the criterion has failed.

**Candidates, not rules.** A feedback event produces entries in `rule-candidates.yaml`, never direct
edits to this file. Two sources:

1. **Post-hoc** — a debrief, a client reaction, a graded outcome
2. **In-flight** — an `overrides:` entry (the operator disagreed with the method, in writing, with a
   falsifiable release condition) or a check that fired and was waved through. This source is higher
   yield and is the one nobody builds

**The promotion gate.** A candidate enters the standing tier when either:

- it has fired **twice**, or
- it has fired **once** and a **blind agent**, given only the candidate rule and asked to construct a
  problem statement where it flips, **fails to construct one**

The falsification attempt is never run by the operator. That is self-policing, the documented failure
mode.

**Promotion is what keeps this file thin.** Without a gate you accrue a rule per session and arrive at
a document nobody consumes, which is the failure this design exists to prevent.

**Demotion is symmetrical.** Every run records `checks_fired`. A rule with zero fires across N runs is
decoration and gets surfaced for cut. This is the closure rule turned on the rulebook itself: a rule
whose deletion changes nothing is not a rule.

**Every session writes a changelog entry, including the ones that change nothing.** "No change, and
here is why" is valid and required. That is what makes *did it improve* answerable rather than felt.

**This file, `frame-schema.yaml`, and `method-moves.yaml` are all standing tier and all change through
this same gate.** The schema is not exempt.

## The adversarial fork: `/plan-hardening` on the frame

**A frame is a plan-shaped artifact**, so the same instrument that stress-tests a plan stress-tests a
frame. This is a **fork, not the default path**. Cost on the first v2 run: **81 agents, 2.88M tokens,
40 minutes** for a 25-hole plan (the spec's model is ~N+M+7 agents; a run that stops at the premise
gate costs 4). Worth it before an irreversible or high-stakes delivery, wasteful on a routine run.

**Fire it when:** the artifact goes in front of a room that can reject it, the engagement is
one-shot (`d1.mode.shots: one`), or the deterministic gate came back clean and that cleanliness is
itself suspicious. **Skip it when:** the gate found real failures — fix those first, since a panel
attacking a frame with known defects spends its rounds on what you already know.

**Where it slots:** after the deterministic gate passes, before the artifact is locked.

**Exact invocation contract. `args` MUST be a JSON object, never a prose string** — the workflow
calls `JSON.parse` on a string arg and dies in 17ms with `Unexpected identifier` before a single
agent runs. The key is `planPath` or `planText`, **not `plan`**:

```
Workflow({ scriptPath: ".claude/workflows/plan-hardening.js", args: {
  planPath: "output/<slug>/frame.yaml",   // OR planText: "<the plan markdown>"
  context:  "<1-paragraph domain context, and NOT the plan itself — an agent",
                                          //  authors a goal from this while blind to the plan>
  outPath:      "output/<slug>/frame-v2.yaml",   // optional, now actually written
  registerPath: "output/<slug>/hardening-register.json",  // optional, now actually written
}})
```

**Use `scriptPath`, not `name`.** `name` resolves a stale snapshot of the script and says nothing
when it does; measured 2026-08-21, a run executed the previous version for 40 minutes.
**`rounds` and `lenses` are gone** — v2 has no rounds. The stage graph is a single bounded pass.

Pass the frame plus the `d1` block as `planPath`/`planText`, and the engagement's context as
`context`. Fired 2026-08-17: this block previously said "as the plan," which reads as a `plan:`
key and as free prose; both fail.

**What it returns is a residual risk register, not a pass.** There is deliberately no `airtight`
boolean. Read the register.

**Three properties that make it usable here, all added 2026-08-13:**

1. **It validates its own claims on an independent model.** The Validate phase takes every
   `unverified_claim` and every risk premise and checks it against the real repo, instructed to
   refute first. `REFUTED` comes back separately, because a refuted premise makes its risk unsound
   rather than merely open. `UNVERIFIABLE` is never a pass.
2. **The judge derives its own severity.** It receives every hole at every severity — not a set
   pre-filtered by the critics' ratings, which is how an under-rated hole used to become invisible —
   and records `critic_severity` and `severity_disagreement` alongside its own call. **A register
   where every row reads "agrees" is a judge that did not judge.**
3. **It reports which risks attack its own additions.** The loop revises the plan between rounds, so
   later rounds can attack machinery the panel introduced. Those are flagged, and the cheapest fix is
   usually to drop the addition rather than build what it demands.

**UNCONVERGED means the design is still growing, not that it is unsound.** Diff the hardened artifact
against what you submitted before acting on the register.

## What a run must record, or lose forever

Cheap during the run, impossible to reconstruct afterward. Non-negotiable.

- **The rejection record.** What was proposed and turned down, with the reason. A system storing only
  accepted output learns the operator's existing decisions rather than improving on them
- **Creation order.** A fact cited by an element that predates it is a retrofitted citation
- **Stage yield.** A stage that never changes the frame is a demotion candidate
- **Unknowns that surfaced and were never dispositioned.** Afterward you can only see what you handled
- **The pre-room prediction.** Contaminated the instant feedback arrives

## Pointers

- `framework/frame-schema.yaml` — the state file's schema, with the judgment class of every field
- `framework/deck-rubric.md` — the gate. A-E craft, F frame integrity
- `framework/method-moves.yaml` — the move catalogue and instantiation table, keyed by problem type
- `framework/rule-candidates.yaml` — the holding pen
- `framework/analysis-method-CHANGELOG.md` — the evidence that this improves
- `framework/slide-craft-mckinsey.md` · `framework/problem-solving-mckinsey.md` ·
  `framework/smb-decision-analysis.md` · `framework/adversarial-analysis-pipeline.md` (an optional
  fork, invoked when stakes justify it, never the default path)
