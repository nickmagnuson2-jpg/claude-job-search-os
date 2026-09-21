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
attacking a frame with known defects spends its budget on what you already know.

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

**Three properties that make it usable here** (rewritten 2026-08-21 — the previous version of this
block described the superseded round-based design, naming a judge stage, `critic_severity`,
`severity_disagreement`, `unverified_claim` and an `UNCONVERGED` state, **none of which exist**):

1. **It validates its own claims on an independent model.** The Validate phase checks still-firing
   holes and any new blocking holes the fix introduced against the real repo, instructed to refute
   first. `REFUTED` comes back separately, because a refuted premise makes its risk unsound rather
   than merely open. `UNVERIFIABLE` is never a pass. The stage is **capped at 12 claims** and logs
   what it dropped — read `validation_coverage` in the register before treating the pass as complete.
2. **Severity is decided per hole, and premise findings gate execution findings.** `minor` holes are
   filtered at probe time; each surviving hole gets its own disposition agent. Premise findings are
   not peers of execution findings — if the premise gate returns `open` the run HARD STOPS and
   execution critique never runs.
3. **It reports which holes the fix itself introduced.** A dedicated agent sees only the diff between
   the original and revised artifact and asks whether those changes created a NEW blocking hole. The
   first live run found 2. The cheapest fix is usually to drop the addition rather than build what it
   demands.

**Terminal states are `PREMISE-OPEN` / `CLOSED` / `RESIDUAL` / `OPEN`.** `OPEN` means holes survived
their own fix — the first live run returned `OPEN` with 14 of 25. `RESIDUAL` is normal and means
risks were knowingly accepted with written reasons. Diff the revised artifact against what you
submitted before acting on the register.

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

---

## Closing an engagement: the code drain

Every engagement produces scripts. Nothing used to look at them, so one closed with 34 in its
`scripts/` directory of which **four were already probes the method was separately planning to
build from scratch**. They had sat there a week. The cost is not the rewrite; it is that a
rewrite is WORSE, because the original carries the defect that produced it, written down at the
moment it was understood.

**The gate, so this is not a habit anyone has to remember.** `frame.yaml` carries a `scripts`
block and F14 in `tools/check_frame_integrity.py` refuses a close while any script beside the
frame lacks a disposition. Three values:

| | Means | Requires |
|---|---|---|
| `promote` | MECHANISM: every consumer would want the same answer | `target`, the `tools/` module it becomes |
| `engagement_only` | POLICY: it encodes this client's data, questions or page | `reason` |
| `superseded` | replaced by something else | `target`, what replaced it |

**F14 also verifies that a `promote` target EXISTS.** Without that, `target: tools/foo.py` is a
note-to-self: the decision is recorded and nothing checks it was carried out. Six such entries
sat in a real frame reading PASS.

### Promoting one, in order

1. **Search `tools/` for the mechanism FIRST.** This is the step that pays. Two deck tools were
   queued for promotion and both carried Chrome discovery; a third copy already lived in
   `check_deck_geometry.py`, byte-identical, and one of the two HARDCODED a browser path with no
   fallback. Promoting them as written would have shipped a second and third driver. Extract the
   shared part first: `tools/chrome_runner.py` now holds it and `check_deck_geometry` got
   **shorter**. A promotion that only adds files has probably skipped this step.
2. **Split mechanism from policy, and only the mechanism moves.** Locating and invoking a browser
   is identical for every caller. What gets injected into the page and what gets extracted
   afterwards differ completely and stay with the caller. `slide_check.py` had already performed
   this split inside the engagement: it holds "check a printed value against a recomputation"
   while the per-slide modules hold *which* values a slide prints.
3. **Re-resolve the paths.** An engagement script resolves relative paths against its own
   directory, because it lived beside the data. In `tools/` that silently doubles the path.
4. **Move the TESTS to where the mechanism now lives.** Two tests for browser discovery lived in
   the deck-geometry suite and began failing on promotion -- not because behaviour broke, but
   because they tested a generalizable INPUT at a CONSUMER, and patching a re-export does not
   reach the implementation. Test the mechanism once where it lives; each consumer tests only
   what it alone does.
5. **Verify on the real artifact, not a fixture.** `tools/deck_to_pdf.py` was run against the
   engagement's actual deck and produced 393,163 bytes across 2 pages, matching the sent
   deliverable exactly. That is the measurement; a green unit test is not.
6. **Mutation, then record the landing.** `mutation_check.py --isolation`, zero survivors or a
   written reason per survivor. Then the frame's `target` points at a file that exists, and F14
   goes green on its own.
7. **RETIRE THE ORIGINAL, and this step is the one that was missing.** A promotion is not done
   when the target exists; it is done when the engagement no longer carries its own copy. Either
   delete the original or reduce it to a thin caller that imports the promoted module and holds
   only policy. **F14 verifies that a `promote` target EXISTS and never that the SOURCE was
   retired**, so a promotion that leaves a full duplicate in place reads GREEN.

   Measured on 2026-09-21, from the 2026-09-20 promotion pass: `deck_to_pdf.py`,
   `render_slides.py` and `fit_chart_viewbox.py` were all still full duplicates in the
   engagement's `scripts/`, none of them referencing `tools/` at all. The cost was not tidiness.
   `tools/fit_chart_viewbox.py` gained a horizontal-clip check that the stale copy does not
   have, and a generator docstring still pointed at the stale copy -- so the next rebuild would
   have used the version that cannot see a clipped label. Found by a second model, not by the
   gate.

   **The general shape, because it is the same defect the method keeps finding:** a check that
   verifies one end of a two-ended relation passes while the other end rots. Prefer a check that
   reads both.

**What does NOT travel.** A definition module (`junk_def.py`, `day_parts.py`) whose docstring says
"import it, do not re-declare it" is a real pattern and a local value: the PATTERN belongs to
`framework/glossary.md`, the five literal values belong to the client. A restraint harness stays
local on purpose -- the engagement IS the labelled dataset its probe is measured against, and
promoting it would separate the measurement from its ground truth.

## Mutating the generator to find the assertion nobody wrote

A check suite cannot reveal an assertion that was never written. It looks thorough, every line
of it is correct, and the gap is the line that is absent. Reading the suite does not find it,
because there is nothing there to read.

**The move: break the GENERATOR on purpose, regenerate, and run the suite.** If the suite stays
green, that survivor IS the missing assertion, stated as a number rather than as something a
person had to notice. This is mutation testing with the artifact generator as the subject and
the artifact's own checks as the tests; nothing about the technique was ever specific to unit
tests, we had only ever pointed it at `tools/`.

Run end to end on 2026-09-21, on a shipped deck whose chart printed no label for one band:

| step | result |
|---|---|
| baseline, correct generator | 65 checks, green |
| reinstate the defect in the generator | **65 checks, still green** -- survivor |
| write the missing assertion | mutant **dies** |
| restore the generator | 72 checks green, chart byte-identical |

Both halves or it proves nothing: the mutant must die AND the restored generator must still
pass. A check that only ever fails is not evidence.

**Limits, stated so the move is not oversold.** It reaches only what a generator produces --
hand-authored markup has no generator and stays invisible. Each cycle costs a regenerate plus a
suite run, so it is a pre-send gate, never an on-edit one. And the operators differ from the
code ones: artifact defects are OMISSIONS (drop a label, drop a legend entry, truncate a
string), not inverted conditionals.

**Cadence is change-triggered, not pass-triggered.** "After every editing pass" is a habit and
habits convert at zero. What changes the answer is a change to the generator or to its check
suite; editing copy does not alter coverage. Hash both, make a fresh run a precondition of
sending, and let it skip when neither moved.

## Three defects that look identical when the run is green

They are separated by one question -- **is there a second copy, a missing row, or a missing
assertion?** -- and each mechanism is inert against the other two. All three fired on the same
artifact on 2026-09-21.

| defect | example | mechanism |
|---|---|---|
| **derivable but typed** | `closure` said "Five elements" while seven were declared | delete the copy; compute it at read time |
| **added but undeclared** | a claim reached a sent page and no element declared that surface | a gate: the record is a PRECONDITION of the artifact, not a companion |
| **never asserted** | a chart band printed no label and every check was green | mutate the generator, above |

A single source of truth fixes the first and cannot touch the second, because there is no copy
to propagate -- there is an absent row. Do not merge these into one heading: a rule that feels
complete quietly licenses two of the three to keep happening.

## Where a method learning goes, and it is not a matter of taste

This document and `data/workstreams/analysis-method.md` are different KINDS, and for a week
nothing said which got what, so both accumulated both and the workstream drifted.

| kind | goes to |
|---|---|
| a distilled, timeless RULE | **here.** This is what the next engagement reads BEFORE starting |
| the EVIDENCE that produced it | the engagement that produced it (`output/<slug>/`) |
| an OPEN DECISION about the method | `data/workstreams/analysis-method.md` |
| a number derivable from the engagements | **nowhere.** `tools/engagement_runs.py` computes it |

The memory corpus is where a rule goes to be FOUND, by grep. This document is where it goes to
be USED. Landing a method learning only in memory leaves the method unaware of it -- which is
exactly what happened on 2026-09-21 until Nick asked where the updates had been going.
