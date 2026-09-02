# Writing Discipline — Provenance and Ownership (CANONICAL)

> **The question this file answers:** before a written artifact ships, whose ideas are in it, and does it respect the reader?
>
> **Sibling boundary.** `framework/voice-pure-dictation.md` answers a different question: *what may Claude do to Nick's words?* That file governs the handling of Nick's voice (the three modes, the minimal-diff rule, Wispr discipline, the Q&A-paste workflow). This file governs the artifact itself, whether or not any dictation was involved. The two are closely linked and must not duplicate each other. When a rule is about **Claude's treatment of Nick's input**, it lives there. When it is about **what must be true of the output before it ships**, it lives here.
>
> **Precedence:** this file is canonical for the provenance labels, the substantive-sentence definition, and the audit rule. Skill files are canonical only for their own slot rows. On conflict, this file wins and the skill gets fixed.

## Mandatory read

Any skill that drafts prose for a human reader MUST open its drafting step with: *"Before drafting, apply `framework/writing-discipline.md`."* Current skills in scope:

- `/draft-email`
- `/cold-outreach`
- `/follow-up`
- `/cover-letter`
- `/apply` (the application-answers path)
- `/outreach-batch` (delegates to the `/cold-outreach` slot table; the labels still come from here)

Enforced by `tests/scripts/test_writing_discipline_parity.py`. A skill added to this list without the pointer fails the suite.

## Why this exists

Nick's writing is produced with AI assistance and much of it is **publicly facing**: outreach to real people, application answers read by hiring teams, cover letters. The discipline is not private hygiene. It governs artifacts that reach real readers who will form a judgment about Nick from them.

Four principles, adopted 2026-08-20 from Clay's AI Writing Policy (Sophie Alpert, published 2026-08-19). All four are endorsed. Clay's own version is prose with no enforcement mechanism; its real gate is a colleague asking "what did you mean by this line," which does not exist in a single-operator system. This file is the mechanical version.

1. **Stand behind every idea and every sentence.** "AI wrote that, ignore it" is not an available answer. If a reader asks what a line meant, there must be an answer.
2. **Writing is thinking.** The value is not only the artifact. It is the act of deciding what to emphasize and how to structure it. Skipping that produces a worse understanding of the subject. **See the Selection Gate below: this principle is endorsed and NOT yet built.**
3. **More time authoring than consuming.** Generating a long artifact from a short prompt and asking a reader to work through it transfers cost onto them. Compression is the direction that respects the reader.
4. **Longer is not better.** Length added by a model is usually vacuous sentences that obscure meaning.

**The direction that matters.** Dictation inverts the ratio these principles worry about. Nick's raw utterance is typically longer than the artifact that ships, so the pipeline compresses rather than expands. `framework/two-tier-capture.md` preserves the raw utterance permanently, which means the input to every compression remains inspectable.

## The provenance labels

Every substantive sentence in a draft carries exactly one label:

| Label | Meaning | Requirement |
|---|---|---|
| **N** | Nick-dictated **this session**. The spine he just provided. | None. This is the strongest grade. |
| **C** | Nick-corpus. Verbatim or near-verbatim from `framework/voice-reference.md`, prior `data/reflections/`, sent emails, `data/professional-identity.md`, `data/goals.md`, `data/projects/*.md`. | Must be locatable in the corpus. |
| **I** | Claude-inferred from a **citable** source: research dossier, role posting, public bio, company page, a pulled transcript or debrief. | Name the source in working notes as `[Source: <path or URL>]`. Must be traceable before the draft ships. |
| **G** | Claude-generated. No source. Synthesized from general training or pattern-matching. | Blocked in every slot carrying a claim about Nick. |

**Ownership is not binary, and that is the point.** Clay's policy has one grade: you stand behind it or you do not. This has four, which makes the useful question answerable: not "did a human write this" but "which grade of ownership does this slot require." A `C` sentence was not authored this session and is still genuinely Nick's, because his past writing is a valid source. That distinction is what lets a system draft at all without laundering.

## What counts as a substantive sentence

Any sentence that makes a claim, carries a position, or does persuasive work: opener, ask, value proposition, bridge, story beat, closing call to action.

**Not substantive:** logistics ("Wednesday at 2pm works"), scheduling, standard pleasantries, sign-off.

Skills MAY extend this list with artifact-specific slot types. Skills MAY NOT narrow it.

## The audit rule

Before the quality check, label every substantive sentence. Then apply the skill's own slot table.

**The invariant, which no skill may weaken:** `G` is blocked in every slot that carries a claim about who Nick is, what he brings, what he wants, or what he has done. When a `G` lands in a blocked slot, STOP and either request the dictation, pull from corpus, or find a citable source. Do not proceed to the quality check with `G` in a blocked slot.

**Slot tables live in the skills**, because the slots genuinely differ by artifact: cold outreach has a personalization slot that email does not, a follow-up has a new-value-add slot, a cover letter is self-positioning nearly end to end. Each skill owns its rows. This file owns the labels, the definition, and the invariant above.

### Audit output format

In working notes, never in the artifact. **Both blocks are required.** The provenance block answers *where each sentence came from*; the selection block answers *what this artifact leads with and what lost*.

```
Substance audit:
- Opener: "..." → C (voice-reference.md Exemplar 3)
- Value-prop: "..." → N (Nick dictated 5/21 17:10)
- Bridge: "..." → G ❌ STOP — need Nick to provide

Selection record:
- Lead: "<the sentence carrying this artifact's point>" — why this one
- Cut:  "<candidate considered and dropped>" — why not
- Cut:  "<candidate considered and dropped>" — why not
```

### The selection record (mandatory fields: `Lead:` and `Cut:`)

**`Lead:` names the one sentence the artifact leads with, and why it beat the alternatives.**

**`Cut:` names every substantive candidate that was considered and dropped, with the reason.** An empty `Cut:` is only valid when no other candidate existed, which is rare in any artifact with more than one proof point. **If material was available and nothing was cut, that is the defect, not a clean run.**

**Why these two fields and not a checklist.** The measured failure (see below, n=13) is **under-selection**: including everything approved, leading with the wrong proof, inventing connective material. A checklist catches known patterns. A required `Cut:` field makes not-choosing structurally impossible, which is the actual defect.

**This is where the scattered selection rules get used.** `content-rules.yaml` C4 (escape hatch on an invited ask), B7 (invented conceptual parallel), and B8 (leading with a heavy proof-drop) are tagged `decision_class: selection`. They are not a separate pass to remember. They are the **reasons written in the "why not" column** at the moment the choice is made. The Content-Rules Pass is advisory and Claude rule-gates itself into it; this record is mandatory and ungated, which is why the selection question lives here and not there.

**Ceiling, stated honestly.** This is skill-tier. It produces evidence that *a selection decision was made and written down*. It does not verify the decision was *right* - no mechanism can, because that is judgment. `tests/scripts/test_writing_discipline_parity.py` asserts the fields are declared and that no skill redefines the format locally. That is the enforceable half.

**Why this exists:** voice corruption in self-positioning content is the highest-frequency failure mode of drafting. Roughly ten separate behavioral rules in memory all instance the same defect. This step collapses them into one structural gate. Origin: 2026-05-21 memory audit; consolidated out of three duplicated skill copies 2026-08-20.

## The Selection Gate — PLACEHOLDER, NOT BUILT

**Status: endorsed principle, no mechanism. Do not report this as covered.**

The provenance audit answers *where each sentence came from*. It does not answer *why that sentence survived and forty others did not*. Principle 2 above is about selection: deciding what to emphasize and how to structure it. In this system that decision happens during the compression from the raw tier to the shipped artifact, and **Claude currently makes it.**

The minimal-diff rule in `voice-pure-dictation.md` constrains what may be ADDED. Nothing constrains what may be DROPPED, and every cut is a claim about what mattered. A long dictation rendered as a short email is mostly a sequence of choices about what did not matter.

**Known risk:** dictation produces a stronger felt sense of ownership than typing does, because the words were genuinely spoken. The compressed output therefore reads as Nick's even where the emphasis was decided elsewhere. Felt ownership is not evidence of ownership.

**Before building anything here, run the measurement.** Take three raw-tier captures. Before rereading the synthesized version, write one sentence naming the point. Then check whether the shipped artifact leads with it. Two-tier capture is what makes this test possible at all, and nothing has been measured yet. Deciding before measuring turns a real finding into ceremony.

### Measured 2026-08-20 (n=2, coached-answers only)

**Scope, stated with the result:** two pairs from `coaching/coached-answers/` (`driving-org-change.md`, `why-did-you-leave.md`), chosen because each carries a frozen raw tier and a Claude-authored tier in one file. Nick named the point of each raw delivery before seeing the Claude tier; scoring criterion (MATCH / PARTIAL / DRIFT) was fixed in advance. **This measured the surface where the discipline is strongest and does NOT clear the system.**

**Structural result, and the reason the original hypothesis was wrong.** Neither file contains a compression. Claude never rewrote either answer; the raw dictation IS the shipped artifact and Claude's contribution is annotation beside it under a "do NOT edit the raw above" header. The compression pen was never picked up, so there was no compression loss to find. **Two-tier capture worked exactly as designed.**

**What the test found instead: the prospective vector.** Polish notes direct the NEXT revision, and that is where emphasis gets redirected.

| Pair | Score | Detail |
|---|---|---|
| `driving-org-change` | **DRIFT** | Nick's stated point was the post-acquisition change and the 600-person scale. The first note reads "Opener buries the user - start with the exec problem, not the org-chart framing," instructing removal of exactly that framing. Scale and change-context appear in none of the seven notes. |
| `why-did-you-leave` | **PARTIAL** | Every beat survives and Nick's ordering is preserved. Weighting inverts on one beat: Nick called AI skill-building "the added benefit"; the annotation calls the structure "the central move" and invests further in that beat. |

**The mechanism, restated more precisely than the compression theory above.** The tell in the DRIFT case is grammatical. "Opener buries the user" is phrased as a **defect**, not as "you lead with context, I would lead with the problem, which do you want?" An emphasis *choice* entered the record as a *correction*. That is cheaper to fix than compression loss: annotation proposing a different emphasis must be marked as a choice, not stated as a defect.

**Still untested:** every artifact where Claude actually holds the pen. Emails, cover letters, and application answers were not measured. That is the surface this whole rule is about.

### Measured 2026-08-20 on drafted artifacts (n=13)

**An earlier revision of this section claimed no instrument existed. That was wrong.** It was based on grepping `output/` for phrases like "as sent by nick," which returned 1. The instrument is `memory/lessons.md` Section 2, where send-time edits are recorded as analyzed corrections tagged "sent-vs-draft diff," with the Claude text and Nick's replacement quoted. Scope error: the claim was broader than the search. Left recorded here because a future session would otherwise re-derive the same false absence.

**Scope:** 78 Section-2 rows; 13 record an actual send-time edit to a Claude draft. Classification below is Claude's and should be spot-checked; the criterion is whether the defect concerns **what to say and what to lead with** (selection) or **how to word it** (style).

| Class | Count | Rows |
|---|---|---|
| **Selection / emphasis** | **6** | escape-hatch softening an invited ask; every approved callback included; hard capability claim; restating items settled in another channel; leading with the wrong proof; an invented conceptual parallel |
| Style / register / voice | 5 | flat opener; spoken-register logistics; wrong exemplar's opener; eager-generic close; company name omitted |
| Mechanics / judgment | 2 | cc decision; BCC acknowledgment |

**The direction is the opposite of the hypothesis stated above.** The compression theory predicted Claude over-compresses and drops Nick's emphasis. The recorded evidence shows **under-selection**: Claude includes everything approved, leads with the wrong proof, and invents connective material, with Nick doing the cutting at send. The clearest instance: Claude included every Nick-confirmed callback as its own paragraph and Nick cut one as "too much" **even though he had explicitly approved it in conversation**. Approval is not selection, and Claude declined to select at all.

**The Selection Gate is not unbuilt. It is built and scattered.** Several of these were already promoted into `framework/content-rules.yaml`: C4 (`ask-discipline`, escape hatch on an invited ask), B7 and B8 (`proof-and-positioning`, the invented parallel and the wrong lead proof), plus a "2-3 substantive callbacks max, not all approved-callbacks" rule with an explicit cut criterion ("would removing this make the email less effective?"). These are selection rules filed under voice and positioning categories, with no name for what they share. **The gap is nomenclature and consolidation, not mechanism.**

**REOPEN gate:** a 7th selection-class row appears in `memory/lessons.md` Section 2, OR a selection defect ships that none of the existing content rules would have caught. At that point the move is to give the scattered rules a shared category and a single gate, not to invent a new mechanism.

## Connections

- `framework/voice-pure-dictation.md` — the sibling. What Claude may do to Nick's words.
- `framework/two-tier-capture.md` — why the raw tier exists and why the selection test is runnable.
- `framework/voice-reference.md` — the empirical style layer. Downstream of this file: style is applied to content whose ownership already passed.
- `framework/application-workflow.md` — the hard-filter demonstration gate. A separate axis: demonstration asks whether the artifact proves the bar, this file asks whose idea it is. A proof-of-thought answer must pass both.
- Analysis that produced this file: `output/analysis/082026-clay-ai-writing-policy.md`. Nick's dictated position: `data/reflections/2026-08-20-ai-writing-policy.md`.
