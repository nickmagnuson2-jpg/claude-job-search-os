# Review Findings Protocol — the standing rules

**Created 2026-08-14.** Version 1.

**This is the single source of truth for what happens between a review agent returning and anything
being changed.** Five surfaces in this repo dispatch review agents. Before this file they each
answered that question differently, which is why the same rule fired twice and got promoted zero
times: neither tool looked broken on its own.

**Surfaces cite this file. They do not copy it.** Duplicating these rules into five skill files is the
duplicated-domain-logic pattern this repo has a rule against, and it fails in the specific way that
matters here: five copies drift, and then the rule has five meanings.

---

## The rule

> A review that returns findings produces a **decision list, not a work order.**

Two obligations, both before anything is touched:

1. **Verify each finding yourself**, this session, against the actual artifact.
2. **State your own severity.** The reviewer's rating is an input you are re-deciding, never a value
   you inherit.

Then present. Apply nothing that has not passed the bar and been approved.

**The one exception, and it is not a loophole:** a factual error in Claude's own artifact with exactly
one correct answer. Fix those, and say plainly in the same message that you did. Every such fix is
still verified first.

---

## The three failure signatures

All three observed. They are distinct, and a tool can guard one while leaking the other two.

| Signature | What it looks like | What it costs |
|---|---|---|
| **Pre-applied** | A finding classed as housekeeping and applied unilaterally | A "wording correction" quietly deleted a defensive asset — the answer to a likely objection. A strategic decision made silently, disguised as copy-editing |
| **Inherited severity** | The reviewer's rating relayed as the verdict | A defect reported as an imprecise word was a wholly false claim. Acting on the reviewer's framing produces a one-word patch on a sentence that needed replacing |
| **Right finding, wrong specifics** | The defect is real; the location or the severity direction is wrong | An agent flagged a path bug on one branch of a two-branch tree; the bug was on the sibling branch, which sits at a different depth. "Fixing" it would have broken working code and left the defect in place |

**The third is the one that survives a careless verification pass**, because confirming *a defect
exists* is not confirming *this defect, here, at this severity*. Location and severity each need
independent derivation.

---

## The verification bar

Every finding, before it enters anything.

1. **Open the file and quote the actual line.** An agent's description of a step is not the step. Cite
   `path:line`.
2. **Derive severity from the artifact, not from the tag.** A tag-to-severity lookup table is
   inheritance wearing a mapping.
3. **A simplification that deletes a safety step is a downgrade.** The simplicity reviewer optimizes
   for less code and cannot see what the risk reviewer added. When they conflict, safety wins and the
   conflict is recorded.

**Three verdicts, and UNVERIFIABLE is never a pass.** CONFIRMED requires evidence proving it true.
REFUTED requires evidence proving it false. Anything else is UNVERIFIABLE. Default to UNVERIFIABLE
over guessing. Collapsing UNVERIFIABLE into CONFIRMED is how "we verified it" comes to mean "we looked
at it" — the same discipline the frame gate enforces with CANNOT_RUN.

**A refuted premise is worse than an open risk, not better.** REFUTED comes back separately because
the finding resting on it is unsound, not merely unaddressed.

---

## The Findings Ledger

Mandatory. **The review is not done without it.**

| # | Source | Finding | Their severity | My severity | Verdict | Disposition | Why |
|---|---|---|---|---|---|---|---|
| 1 | simplicity | [one line] | major | minor | CONFIRMED | **rejected** | Verified at `path:line` — deletes the retry guard the risk agent added |
| 2 | sequencing | [one line] | major | major | CONFIRMED | presented, rec: apply | Verified: step 3 reads a file step 6 creates |
| 3 | risk | [one line] | blocking | blocking | UNVERIFIABLE | **rejected — could not verify** | Named file does not exist; scope checked was `tools/` per `ls` |

For workflow-tier surfaces, the same shape as schema fields: `finding`, `severity` (own, described as
*derived from the artifact, not relayed*), `critic_severity`, `severity_disagreement` (one line, or
`agrees`), `verdict`, `evidence` (`path:line`, or the command run and its output, or the specific
absence checked **and the scope of that check**), `disposition`, `why`.

### The dispositions

`applied` · `presented` · `rejected` · `rejected — could not verify`

**`dropped` is not a disposition.** A finding that vanishes between review and output is
indistinguishable from one that was considered and refused, and only one of those is a decision.
Unverifiable findings go in as `rejected — could not verify`, never silently filtered.

### The three tells

Read these before trusting any review output:

1. **Both severity columns match on every row → it is a relay, not a judgment.**
2. **Zero rejections → it did not judge.** Six agents on one artifact do not all produce keepers.
3. **Fewer ledger rows than agent findings → something was dropped silently.** Count them.

**Never pre-filter by the critics' severity before the judging step.** The judge receives every
finding at every severity, precisely because it is re-rating them. Filtering to `blocking` first is
how an under-rated finding becomes invisible without anyone deciding to ignore it — that was
`plan-hardening`'s actual bug, and it was invisible in the output.

---

## Adoption status

| Surface | Verifies | Own severity | Presents, not applies | Ledger | Status |
|---|---|---|---|---|---|
| `.claude/workflows/plan-hardening.js` | ✅ Validate phase, independent model | ✅ `critic_severity` + `severity_disagreement` | ✅ register, edits nothing | ✅ | **Done** 2026-08-13 |
| `.claude/skills/critique-plan/SKILL.md` | ✅ verification bar | ✅ ledger + Step 4a derives `My severity`; agent rating recorded as an input | ✅ inline, approval before execute | ✅ | **Done** 2026-08-14 |
| `.claude/skills/review-cv-deep/SKILL.md` | ❌ no re-derivation against the CV | ❌ `:230` "use the highest severity assigned by any perspective" | ✅ writes a report | ❌ | **Open** |
| `.claude/skills/audit-pii/SKILL.md` | ⚠️ cross-checks **clean** verdicts only (`:153`); positives unverified | ❌ | ✅ approval-gated (`:150`) | ❌ | **Open** |
| `.claude/workflows/research-audit.js` | ✅ claim-level CONFIRMED/REFUTED/UNVERIFIABLE | ❌ | ✅ writes a doc | ❌ **`:107` "refuted claims dropped"** | **Open** |

Three observations the table earns:

- **Nobody had all four.** Each surface guarded a different subset, which is why no single one read as
  broken and the rule never promoted.
- **`audit-pii` guards the opposite direction from everyone else.** It distrusts a subagent's *clean*
  verdict, because PII subagents rationalize real names as benign examples. That is a real and
  separate defect class, and it belongs here too: **a clean verdict is a hypothesis, not a
  conclusion** — cross-check it against a deterministic scan.
- **`research-audit.js` validates rigorously and then drops what it refuted.** Correct verdicts,
  wrong disposition. The cheapest fix on the list.

---

## The half-fix, and why it stays recorded

`critique-plan` is the tool this rule kept firing against. Its 2026-08-13 fix added a Findings Ledger
requiring independent severity **while leaving Step 4a intact** — which still prescribed "use the
highest severity assigned by any agent for the merged row", followed by a tag-to-severity mapping
table. Two sections of one file disagreeing about one rule, with the ledger making it read compliant.

**Closed 2026-08-14.** Step 4a now records the agents' highest rating as `Agent severity`, explicitly
an input, and requires `My severity` derived from the plan text. The mapping table was reworded from
"Maps to" to "typically raised by — confirm against the plan text before assigning." The issue table
carries both columns and the summary counts on `My severity`.

**Kept here because the shape generalizes, and the three open surfaces will have it.** The tell is
structural: **a fix that adds a ledger without auditing the sections upstream of it has changed the
record-keeping, not the judgment.** When landing this protocol on `review-cv-deep`, `audit-pii`, and
`research-audit.js`, audit the whole file — the defect will not be where the ledger goes.

---

## What this is not

- **Not `/plan-hardening` versus `/critique-plan`.** Different tools, opposite output contracts.
  plan-hardening returns a residual risk register and edits nothing; critique-plan synthesizes a
  hybrid plan. Both are bound by this file; neither is the other.
- **Not the F9 compression ledger** (`deck-rubric.md`, `frame-schema.yaml`), which tracks what a frame
  element was when it got cut.
- **Not `extract-verify.js`'s outcome ledger**, which is data-extraction provenance.
- **Not `tools/friction_log.py`**, which logs hook false positives.
- **`tools/verification-system/` does not exist** and never did. An open todo references it as holding
  "the standard verification ledger format." **This file is that format.** Checked 2026-08-14:
  `ls tools/verification-system/` → no such directory.

---

## Origin and promotion status

Lands `feedback_verify_and_present_review_findings` (occurrences 2, previously `promoted: no`,
verdict NOT_ENFORCED — nothing anywhere enforced it).

- **2026-08-05, a client-facing deck review.** Three fixes applied before surfacing them. Nick: *"I want you to
  discuss this with me instead of making changes on your own."* Later the same night, verifying a
  second reviewer caught an understated severity and two findings that were simply wrong.
- **2026-08-10, `/meeting` four-agent review.** Signature three: a PII leak reported as "already in
  public history, the single most urgent action" was uncommitted working-tree only. Real finding,
  wrong urgency, wrong remediation.
- **2026-08-13 audit.** Traced to source: `critique-plan` *prescribed* the violation ("Simplifications
  from Agent 4 applied inline"). Guarding a poisoned source leaves the recurrence intact, so the
  source was fixed first and this protocol written second.

**Tier:** framework. **Next promotion:** a `check_review_ledger.py` hook is premature — the three
tells are countable but the ledger has no fixed on-disk location yet. **Gate: build the hook when a
ledger-bearing surface ships a review with a missing or all-agree ledger, or when the fourth surface
adopts.** Until then the ledger is enforced by the skills that cite this file.

## Pointers

- `memory/feedback_verify_and_present_review_findings.md` — the rule and its three incidents
- `framework/verification-umbrella.md` — the Family L composite this belongs to
- `framework/analysis-method.md` — the sibling standing doc; same thin-tier discipline
