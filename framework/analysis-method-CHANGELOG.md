# Analysis Method — changelog

**This file is the evidence that the method improves.** The acceptance criterion is that it gets better
after every feedback session, and "did it improve" has to be answerable rather than felt.

**Every session gets an entry, including sessions that change nothing.** "No change, and here is why"
is a valid and required entry. A session that passes without an entry means the loop is not closing.

Newest first. One entry per feedback event.

Format:

```
## YYYY-MM-DD | <event> | v<n> -> v<n+1>
**Source:** post-hoc (debrief / client reaction / graded outcome) or in-flight (override / waved check)
**Candidate:** what was proposed
**Gate:** second occurrence, or blind-agent falsification survived
**Changed:** the specific rule added, revised, or retired, and where
**Not changed, and why:** candidates that did not clear the gate
```

---

## 2026-08-14 | C001 falsified and rejected; C002 replaces it | v1.5, NO standing change

**Source:** post-hoc. C001 (the frame-freeze gate) sat at `occurrences: 1`, which promotes only
if a blind agent FAILS to construct a problem statement where the rule flips. Nobody had run it.
The operator asked for the run.

**Gate result: FAILED. Two of three agents constructed a counter-case, so C001 does not promote.**

**Method.** Three blind agents, each given only the rule text plus neutral definitions, barred BY
NAME from `rule-candidates.yaml`, `analysis-method.md`, `frame-schema.yaml`, `deck-rubric.md`,
`output/` and `coaching/`. Two hunted counter-cases from different angles (scenario-first,
domain-sweep); the third attacked the internal logic. **Discount that all three found problems —
they were told to attack, so that is near-guaranteed and is not evidence.** What cannot be
prompted is convergence on the same mechanism from different angles, and that is what happened.

**THE CONVERGED FINDING: C001 conflates the author revising the frame with the world invalidating
it, and punishes both identically.** One agent located it in the passive voice — *"if the frame
changes"* hides which of the two occurred.

**The sharpest consequence, reached independently by all three:** the rule bars acting on the
output of its own best detector. Rehearsal is the first time an argument is spoken end to end
under questioning, which makes it the cheapest frame-defect instrument available. Stated as a
dilemma: if rehearsal never finds frame defects the freeze costs nothing and the rule is idle; if
it does find them the rule mandates ignoring them. **There is no state in which the rule is both
active and beneficial on that axis.** F3 is the check that has fired hardest across both corpora,
so a rule forbidding a late-discovered F3 fix is aimed at the wrong failure.

**Changed:** nothing in the standing tier. `rule-candidates.yaml` is the holding pen, not standing.
C001 is `rejected: yes` with the counter-cases and the full reasoning recorded so it cannot be
re-proposed from scratch next cycle. **C002 added**, carrying the kernel that survived: freeze
against AUTHOR revision, reopen on a named external trigger, re-rehearse the changed branch rather
than resetting the window, and a mandatory rehearsed limitation statement when the date is
immovable — because "the change does not ship" is not a neutral null option, it prescribes
fluently defending a structure you believe is broken.

**Not changed, and why:**
- **C002 is NOT promoted, and must not inherit C001's falsification.** It was derived from an
  attack on a different rule, which is not the same as surviving one. It needs its own blind run,
  with the agents barred from C001 and from the holding pen.
- **N stays absent.** No single number can work: the freeze point is driven by two independent
  clocks — rehearsal (a function of frame complexity) and information (when other people's inputs
  land). Days is also the wrong unit. Operator confirmed the unit should be REPS.
- **No hook, no skill edit.** A candidate in the holding pen is not enforced anywhere by design.

**What this entry is really evidence of.** The loop refused a rule that felt right to both the
operator and to Claude, on evidence, from agents that could not see the origin story. That is the
falsification step being load-bearing rather than ceremonial — and it is the first time it has run.

## 2026-08-14 | F13: the schema said `required` and nothing read it | v1.4 -> v1.5

**Source:** in-flight. The operator asked whether the run is being tracked at all, so that starting a
forward run does not mean starting blind. The check that answered it found the hole.

**What was actually wrong.** `frame-schema.yaml` marked `proposals` and `prediction` as backfill
impossible and `required`. Nothing read that. `required:` in the schema is DOCUMENTATION: the
`validation:` block enumerates every rule the checker implements and not one of them asserts that a
required field is present. **A locked frame that had lost both run-record ledgers returned clean.**
Verified two ways before building: `grep` over the checker found no schema-required parsing, and the
schema's own `validation:` list has no such entry.

This is the third instance in one session of the same shape — specified precisely, never wired.
The other two: the standing tier has no runner, and this changelog's own "every session writes an
entry" had nothing writing it. **The specification being correct is not the scarce thing. The
enforcement surface is.**

**Changed:**
- **F13** in `tools/check_frame_integrity.py`: on a `locked: true` frame, `proposals` and
  `prediction.will_be_probed` must both be non-empty. CANNOT_RUN while unlocked, so an open run is
  never pushed to invent a prediction before there is anything to predict about.
- **`prediction` narrowed to `{made_at_version, will_be_probed}`.** `will_break` dropped: it invites a
  confidence forecast, and a prepared operator's honest forecast is always "it will go well", which
  carries no information. Where the room pushes is a different question from how the operator performs.
  **Operator's call, and he is right** — the objection that surfaced it was "if I'm prepared I should
  not have any gaps."
- **The field's justification was amended, not just its shape.** It rested on "cold self-reads
  documented as near-perfectly calibrated across five consecutive instances." The 2026-08-13 outcome
  debrief had already corrected that — measured against an annotation, not an outcome, and graded
  against reality the self-read splits by block. The correction had never propagated here. The field
  now stands on the opposite reasoning: introspection demonstrably does not reach frame defects, so the
  informative quantity is the DIVERGENCE between this prediction and a blind agent's.
- **`limits.required_is_documentation`** added, stating plainly that every other `required: true` in
  the schema is still unenforced and that `checks:` is the only real signal.
- Acceptance test re-measured: **FAIL 7, was 6.** F13 fails the real 2026-08-05 reconstruction.

**Measured, not predicted.** F13 was run against the real reconstruction after being written, not
before: `proposals: []` and `prediction: null`, both offenders named in the output. 67 tests pass
(11 new). The 8/5 delivery really did lose both, and unlike F2b — whose inputs are unknowable after
the fact — these are knowably absent, so FAIL is the honest verdict rather than CANNOT_RUN.

**Not changed, and why:**
- **Binding each `will_be_probed` entry to an element id was rejected by the operator as too costly to
  maintain.** Consequence, recorded so it is not rediscovered: the comparison against what actually got
  probed stays manual and cannot be mechanized.
- **Schema version stays at 3.** Dropping an optional sub-field from a type string orphans no frame;
  no frame on disk carries a `prediction` at all.
- **No promotion gate was run.** The operator classed this as learning-while-building, the same
  disposition as the v1 -> v2 field additions: structural fields the run protocol cannot function
  without, discovered by building it. No rule changed.
- **Pairing the operator's prediction with a blind agent's is a live candidate, not built.**

## 2026-08-13 | Second corpus run: the gate generalises, and it found two bugs in itself | v1.3 -> v1.4

**Source:** in-flight. The gate was run against a second, unrelated engagement — a small-business
takeover decision in a different vault, never graded, no outcome to anchor to. Blind reconstruction,
same anti-anchoring protocol as the first.

**THE GENERALISATION QUESTION IS ANSWERED: it travels.** 9 of 11 checks executed on a problem type
Section F was never designed against, versus 7 on the corpus it was built from. The largest open
concern about the rubric — that it might only work on prioritization decks — does not hold.

**It found a real defect in the analysis: F3 fired five times.** Five inputs load-bearing across two
elements each, in a four-criteria frame. The first corpus had exactly one such collision and that one
is what lost the room. The source half-knew — it says in prose that one criterion's math *"silently
assumes"* another's input — but caught one of five, and none structurally.

**It found two bugs in the gate**, both invisible on the first corpus:

1. **Flat dotted keys.** The schema declares field names in dotted notation, so the transcriber wrote
   `d1.problem_statement:` as a flat top-level key. Ten fields landed that way. `validate_structure`
   saw no `d1` parent and read every nested field as "not authored yet" — correct for an incomplete
   frame, wrong here. The file passed structural validation and returned CANNOT_RUN on everything
   reading `d1` or `recommendation`. **The root cause is the schema's own notation**, so it recurs
   with any transcriber and had to be caught mechanically.
2. **YAML dates.** An unquoted `2026-07-21` parses to a date object, and `timestamp` fields demanded
   `str`, so a correctly-authored frame was reported malformed.

## 2026-08-13 | Frame schema 2 -> 3: surfaces become identifiers | same run

**F1b was running at a 50% false-positive rate and I would have reported its findings as real.**

It compared two free-text surface DESCRIPTIONS for equality and failed 4 of 4 elements. Two were
false: both said *"same line, stated parenthetically with the name"* — which is co-location — but the
strings differed because one carried extra detail. Comparing prose for equality was never going to
work.

**Fix:** `name_surface` / `measure_surface` become **identifiers** (`p5`, `slide-8`, `step-1`), matched
against a pattern that lives in the schema so tightening it stays a schema edit. The prose moves to
optional `*_surface_note` fields that nothing compares, so no information is lost.

**F1b now reports CANNOT_RUN on any frame below v3** rather than emitting findings it knows are
unreliable. That is the three-state rule turned on the schema's own history: a check that cannot be
trusted must say so. On the second corpus this took the result from 4 failures (2 false) to 3
failures, all real — **the check got more honest, not weaker.**

Operator's framing on the second schema change in one evening, and it is the right one: this is
discovery, not amendment. Worth noting that a *schema* settling over three passes is normal, while a
*rule* doing that would not be.

---

## 2026-08-13 | Frame schema 1 -> 2: run-state fields | method v1.2 -> v1.3

**Source:** in-flight, during design of the run protocol. Not a feedback event.

**This is LEARNING WHILE BUILDING, and it is deliberately not filed as a rule amendment.**
The operator's framing, and it is the right one: *"this is an example of me learning as we go. It
shouldn't be viewed as me changing it."*

The standing-tier promotion gate exists to stop **per-engagement judgments** being promoted to
standing by convenience — the documented root failure, where a call that was the most
problem-dependent in the case got treated as settled. **No judgment and no rule changed here.** Four
structural fields were added because designing the six-segment run protocol revealed the state it
needs to carry. Discovering that a protocol needs a field is not the failure mode the gate guards.

**Added:** `segment_completed`, `status`, `status_reason`, `declines`.

- **`segment_completed`** is the resume point. The alternative — deriving position from which fields
  are populated — was the original design and cannot distinguish *not authored yet* from *authored
  then abandoned*, which is precisely the case that loses multi-day state.
- **`status: abandoned`** requires a reason and counts as a completed segment E. An engagement that
  dies silently teaches the loop nothing, which is the failure the acceptance criterion targets.
- **`declines`** exists because **a decline that costs nothing is how a compounding loop quietly stops
  compounding.** Every run declines, the changelog fills with "no change", and it reads as stability
  rather than as stall. Two consecutive declines force the next run to apply a change or state a local
  optimum with the observation that would reopen it. It is cross-run state, so it must live in the
  frame — the checker sees one file plus an optional prior and cannot hold it in memory.

**The version bump was the risk, and it is handled.** An adversarial panel flagged that bumping to 2
would make the checker refuse every v1 frame, **including the reconstruction the acceptance regression
is pinned to** — silently deleting the only evidence the gate works. The checker now reads
`supports_frames_at: [1, 2]` from the schema instead of comparing equality, so adding a version is a
schema edit rather than a code edit, and a test asserts no hardcoded version list exists in the module.
Verified: the v1 reconstruction still returns 6 fail / 4 cannot-run / 1 pass, unchanged.

---

## 2026-08-13 | Checker built and run; acceptance test MEASURED | v1.1 -> v1.2

**Source:** in-flight. First execution of `tools/check_frame_integrity.py` against the blind
reconstruction of the 2026-08-05 frame.

**Result: 6 FAIL, 4 CANNOT_RUN, 1 PASS.** The gate independently reproduced the known failure with
no contamination in the chain: a blind agent transcribed the artifact without access to Section F,
and the checks were written from the schema rather than from the artifact.

**The headline is F3.** `i_csat` is load-bearing in two elements. That is verbatim the question that
broke the room, recovered by set intersection. **The instrument works.**

**F10struct was not on the original nine-rule assessment and found two real defects** that a year of
review had not: one assumption with no basis, another with neither basis nor sensitivity. A rule
nobody thought to apply to that work.

**Prediction accuracy: 0 for 3.** The acceptance test was stated three times before it was run and was
wrong every time — six rules, then seven, then a different seven. The measured result is now recorded
in `frame-schema.yaml` under `limits.acceptance_test` and the predictions are deliberately not kept.
**The lesson generalizes past this instrument: predicted coverage is not coverage.**

**Design decision that earned itself immediately:** every check reports PASS, FAIL or **CANNOT_RUN**,
and a CANNOT_RUN is never counted as a pass. Four rules could not execute here. Had they collapsed
into PASS the frame would have reported 5 pass / 6 fail and four untested rules would have read as
coverage — which is "artifacts of rigor without the rigor" rebuilt inside the tool meant to prevent it.
`clean` means no failures; `fully_covered` additionally means nothing went untested. Read both.

---

## 2026-08-13 | Acceptance test falsified the spec, pre-release | v1 -> v1.1

**Source:** in-flight. A blind agent reconstructed a real 2026-08-05 frame into the schema, having
been explicitly barred from reading Section F so it could not shape the transcription to fail.

**This is the loop working before a single run.** Three defects in the schema, found by the test
rather than by review.

**Changed:**

- **`d1.metric_roles` added.** F8 had no field to run against and was silently passing. The rule is
  that the locked problem statement assigns each metric a role and no element may reassign it. The
  reconstruction showed a metric held as a guardrail by the problem statement being used as a ranking
  input by a criterion, which is the documented drift, and the schema could not see it.
- **F2a now requires `because` to be NON-EMPTY.** As originally specified, an element citing nothing
  had all zero of its ids resolve and passed. The check would have cleared exactly the untraced
  element it exists to catch.
- **`name_surface` and `measure_surface` added.** F1 says the level below sits "on the surface that
  names it." A bare `measure` string cannot express where it sits. On the reconstruction the criteria
  surface carried no measures at all; three of four were recoverable only from surfaces five to seven
  pages downstream. That is the reachability failure appearing inside F1 and it was invisible.

**Also corrected: the acceptance test itself.** The claim of "six of the nine" was wrong in both
directions. F12 fails and was never on the nine-rule list; F9 was on it and cannot execute on a
single-version frame. Runnable failures are F1a, F1b, F2a, F3, F5, F8b and F12.

**Recorded as a permanent limit, not a bug:** the deterministic F2 catches a missing citation but
cannot catch a citation to a fact that is itself provenance. The reconstruction's top criterion cites
a fact amounting to "&lt;term&gt; is the word the brief uses" — it resolves cleanly and passes, and it is
the canonical F2 failure. **The blind agent is load-bearing for F2, not a supplement.**

---

## 2026-08-13 | Method created | — -> v1

**Source:** post-hoc. A rejection outcome plus a recruiter feedback call, and a full audit of the
engagement corpus behind it.

**Changed:** everything. v1 is the initial encoding.

- `analysis-method.md` — the standing rules
- `frame-schema.yaml` — schema v1, whole-engagement state, with the judgment class of every field
- `deck-rubric.md` — sections A-E promoted out of an engagement directory, Section F appended
- `method-moves.yaml`, `rule-candidates.yaml` — seeded

**Not yet validated.** Zero runs. Every rule here is a hypothesis about an instrument that has never
been used. The rules' underlying patterns are corpus-wide and well grounded; **the instrument is
untested.** First real test is a live rep on a deliberately different problem shape, which is chosen so
that a failure to generalize shows up immediately rather than after the method is canonical.

**Known limitation, recorded now so it is not discovered later:** the F2b creation-order check is
forward-only. It cannot run against any retrospectively reconstructed frame, because a finished
artifact has no version history. The acceptance test for `check_frame_integrity.py` is therefore six
rules, not nine.
