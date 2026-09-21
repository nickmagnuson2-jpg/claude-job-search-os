# Glossary — The Mag Method

One term, one meaning, one owner file. Seeded 2026-09-21.

**This file is not a reference doc, and saying so matters.** A glossary that only humans read
converts at zero by the enforcement-tier rule. This one has two machine consumers, and if it ever
stops having them it should be deleted rather than maintained:

1. **`tools/scan_promotion_candidates.py` aggregates `occurrences` by CANONICAL TERM**, not per
   file. A defect family written down 28 times under 28 names has 28 counters reading 1 and trips
   no promotion gate; one counter reading 28 trips immediately. The canonical column below is that
   aggregation key.
2. **The per-engagement glossary feeds the jargon probe** (see the last section). A term printed on
   a client-facing page must resolve to a definition the stated audience would accept.

---

## Rulings, 2026-09-21

**The method is called The Mag Method.** Named by its author, who rejected an alternative name
proposed for the same document. Use it in anything shown to a person.

**`framework/analysis-method.md` is the owner file.** "analysis method" survives as an alias **in
file paths only**, because renaming the file would break every citation for no gain.

**"the method", unqualified, is banned in governed artifacts.** It was the most-used and
least-specific form in the corpus when this file was written (15 repo files, 27 live-tier files,
measured 2026-09-21 by `grep -ril`), and it is the term that let four names for one thing coexist
without anybody noticing.

**`data/workstreams/analysis-method.md` is not the method.** It is the workstream tracking work ON
the method. Two artifacts, one name, actively colliding as of this writing.

---

## Universal terms — the method itself

These are stable across engagements. They describe the machinery, not any client's domain.

| Canonical | Aliases seen in the wild | Means | Owner |
|---|---|---|---|
| **The Mag Method** | "the analysis method", "the method", "MAG method", "the frame protocol" | The end-to-end method: frame a problem, gather evidence against it, compress to a defendable artifact, gate the result | `framework/analysis-method.md` |
| **the frame** | "frame.yaml", "the decision record", "the spine" | The governed, versioned decision record for one engagement. Every decision and its reason. Mutated only through its sanctioned write path | `output/<slug>/frame.yaml` |
| **fact** | "finding", "f-number" | A claim in the frame with a source and an evidence tier. Tiers: A primary/official, B reputable secondary, C aggregator or crowd | frame schema |
| **element** | "candidate", "e-number" | A thing considered for the deliverable. May end on the page, in the workbook, or excluded | frame schema |
| **exclusion** | "cut", "killed" | An element deliberately kept off the board, with its reason. Distinct from a compression-ledger cut, which removes something already built | frame schema |
| **compression ledger** | "the cuts", "what got cut" | The record of what was REMOVED from a built artifact and why. Each entry is a decision plus its stated reason | frame schema |
| **decline** | "overrule", "recorded disagreement" | An objection raised by a reviewer, judged correct or partly correct, and NOT actioned — recorded with its reason and its cost-of-being-wrong. A decline is not a dodge; the distinction is that the reason is written down before the lock | frame schema |
| **unknown** | "open question", "u-number" | Something the data cannot settle, carrying a disposition: `question` (ask), `assumption` (proceed and state it), or closed | frame schema |
| **disposition** | — | **Method sense:** how an unknown is being handled. **Warning:** this word also has a domain sense in call-centre data, and that collision reached a client page. Never use it unqualified in an audience-facing artifact | this file |
| **probe** | "check", "question-probe" | A deterministic check that answers a question a human would otherwise have to remember to ask. Named probes: **jargon**, **provenance**, **foundation**, **part-whole**, **rounding-direction** | `framework/analysis-method.md` |
| **gate** | "guard", "hook", "check" | A program that BLOCKS an action when a rule fails. A check that only warns is not a gate | `.claude/settings.json` + `tools/check_*.py` |
| **three-state** | "PASS/FAIL", "green" | A verdict of PASS, FAIL, or **CANNOT_RUN**, plus a separate coverage flag. A check that could not execute is never reported as a pass. Collapsing CANNOT_RUN into PASS produces a green result that means nothing and reads like coverage | `tools/check_frame_integrity.py` |
| **detection step** | — | The mechanical half of a decision: surfacing that a judgment is required. Automatable | see Decomposition below |
| **ruling step** | — | The human half: making the call once it is surfaced. Often not automatable, and usually should not be | see Decomposition below |

### Decomposition — the term the method was missing

**A decision is not human-or-machine. It decomposes into a mechanical DETECTION step and a human
RULING step.** Established 2026-09-21 across twelve worked compression decisions from one
engagement: only two were irreducibly human, and both were audience modeling — predicting what a
specific reader does with a page when nobody is present to correct them.

**You do not automate the judgment. You automate the detection that puts the judgment in front of
the operator at the right time.** The failures in that engagement were never a machine making a
wrong call; they were a detection that did not fire, so the ruling landed on the operator at a bad
moment or not at all.

**Caveat carried deliberately:** those twelve are the decisions that reached the ledger. A
mechanical catch produces an artifact; a gut call often produces nothing. The unlogged decisions
likely skew human, so "two of twelve" understates the human share.

---

## Borrowed vocabulary — the semantic layer

Validated 2026-09-21 against `output/061626-semantic-layers-briefing.md` (Tier A/B: dbt, Cube,
Databricks, Atlan) rather than invented here, per `validate_method_before_encoding`. **The typed
construction record the probes need is a solved problem with a mature vocabulary. Adopt it.**

| Term | Means | Why the method needs it |
|---|---|---|
| **metric** | A named, defendable calculation carrying its formula, owner, and filters | This IS the typed construction record. Four of five probes are blocked on reported quantities carrying their construction as DATA rather than as prose and script |
| **measure** | The lower-level numeric building block a metric is built from | Distinguishes a raw column from a defended number |
| **dimension** | The axis a metric is sliced by | What a comparison varies over |
| **grain** | What one row represents | Getting it wrong causes double-counting. "Aggregation at the wrong grain" is a named failure mode in the literature and was a real defect in practice |
| **entity** | A business object and its keys; how the layer knows what joins to what | Where part-whole relationships between populations become visible |
| **metric sprawl** | The same word returning different numbers in different places | The exact failure when two parties measure the same rate with two different denominators and only one of them says so |

**The catch, quoted from the briefing and carried on purpose:** *"the layer only governs the
questions someone modeled in advance; everything outside that boundary silently falls back to the
same unreliable text-to-SQL, and the whole thing rots without a human owner."* That is a coverage
boundary where inside is governed and outside silently is not — the same shape as collapsing
CANNOT_RUN into PASS. A semantic layer needs its own `fully_covered`.

---

## Per-engagement glossaries — a different artifact with a different job

**Every engagement gets `output/<slug>/glossary.yaml`, and it is not this file's little sibling.**

| | Universal (this file) | Per-engagement |
|---|---|---|
| Holds | method vocabulary | the client's domain vocabulary |
| Feeds | the promotion counter | **the jargon probe** |
| Lifetime | permanent | dies with the engagement |

**The evidence for splitting them.** In the engagement that produced this file, every jargon defect
caught by a human reading the page was DOMAIN vocabulary — a statistical term of art used without
explanation, a word with one meaning in the client's operational data and another in the method,
an operational term that survived its own deletion four separate times. **Zero were method
vocabulary.** A universal glossary would have caught none of them.

Shape, one entry per term:

```yaml
- term: <as printed on the page>
  means: <one sentence the stated audience would accept>
  audience_ok: true            # false -> the jargon probe flags it
  aliases: [<other names this went by during the build>]
  owner: <the tab, script, or section that defines it>
```

**Enforcement:** a term appearing on an audience-facing page that is absent here, or present with
`audience_ok: false`, is a jargon-probe failure. Absent that probe, this file is a doc and converts
at zero — say so plainly rather than treating it as a gate.

---

## How to add a term

1. It earns a row only if a **machine consumer** reads it — the promotion counter or a probe. If
   neither does, the term belongs in prose, not here.
2. Record the aliases actually observed, not the ones imagined. The aliases are the aggregation
   keys that make a family countable.
3. Name the owner file. A term with no owner is how two records for one thing get started.
