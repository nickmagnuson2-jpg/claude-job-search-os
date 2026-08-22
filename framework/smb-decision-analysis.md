# SMB Decision Analysis — a method for irreversible small-business calls

Last updated: 2026-08-10

A repeatable workflow for the question *"should we commit to this?"* about a small
business: take it over, sign the lease, raise the money, hire the first employee. The
defining trait of these decisions is that they are **irreversible, under-researched, and
resting on two or three assumptions nobody has checked.**

Extracted 2026-08-10 from a live business-takeover analysis (2026-07). A prior attempt
at the same shape (2026-05) produced heavier artifacts — a formula-driven workbook and a
pitch deck — but lacked the three disciplines in §3, which are what let an analysis
survive being wrong. Those artifact generators are optional producers *inside* this
method, not the method.

---

## 1. When this earns its cost — and when it does not

**Use it when all three hold:**
- The decision is **irreversible or expensive to unwind** (a lease, an acquisition, capital).
- The answer rests on **assumptions that have never been verified**, not on preference.
- Getting it wrong is **discoverable too late** — after money moves.

**Do not use it when:**
- **A measurement is available and cheap.** Two API calls and $0.05 beat a panel. Measure, don't deliberate.
- The decision is **reversible** — just try it.
- You want **generation, not verification.** This method is built to attack a position, not to produce one.

---

## 2. The seven stages

Each produces one dated artifact. The order matters: fact base before structure, structure
before ideation, ideation before modelling, verification before recommending.

| # | Stage | Produces | The point |
|---|---|---|---|
| 1 | **Fact base** | Market/entity research | Separate **confirmed** from **inferred**, explicitly and in writing. Most bad analysis is inference wearing a fact's clothes. |
| 2 | **Structured problem** | Issue tree, 7-step | Define the actual question. The stated question is usually not it. |
| 3 | **Multi-lens ideation** | Synthesis across independent lenses | Generate options no single frame produces. Each lens runs blind to the others. |
| 4 | **Assumptions model** | Unit economics, scenarios, **assumptions register** | Name every number that is a guess, and what would replace it with a fact. |
| 5 | **Adversarial verification** | Structured verdicts | Attack each branch. Record a verdict **and a confidence**, not a vibe. |
| 6 | **Gates** | 2-4 gating questions | Convert the call into cheap, answerable, deal-killing questions. |
| 7 | **Stakeholder artifact** | Per-audience briefing | The decision-maker is not the analyst. Build for them. |

### On stage 4 — the assumptions register

The register is the load-bearing artifact. Every guessed number gets a row and a note
saying *what fact would replace it and who has that fact.* This is what turns "we should
research more" into a finite list someone can actually close.

### On stage 5 — verdicts carry confidence

A verdict is `PARTIAL`, `VIABLE_WITH_CONDITIONS`, `REFUTED` — paired with High / Medium /
Low. A bare yes/no hides how much weight it can bear. Most honest verdicts on a real
small business are PARTIAL at Medium, and saying so is the finding.

### On stage 6 — gates beat verdicts

A single go/no-go is brittle and usually premature. Gates are better:

> **GO to diligence, not to a commitment.** Three questions gate everything: what is
> actually on offer, is it profitable at real scale, and does the asset legally transfer.

Each gate is cheap to answer and can kill the deal on its own. Prefer a **bounded
stage-one operating test** (run one cycle, consignment, earn-out) over a leap.

---

## 3. The three disciplines that let the analysis survive being wrong

This is what separates the method from a pile of documents. **Assume the central premise
will turn out false**, because in the source engagement it did.

### 3.1 A canonical spine

Exactly one document is current. Everything else points at it. Without this, a reader
finds three analyses and cannot tell which one is live.

### 3.2 Superseded banners that state what survives

When a premise dies, do **not** delete the work and do **not** silently patch it. Banner it:

> 🔴 **SUPERSEDED (date) on [the specific claim].** [What was assumed] predates
> [what was later confirmed]. **What survives:** [the conclusions that outlive the retired
> premise]. Current spine: `<file>`. Current thesis: `<file>`.

The **what survives** line is the hard part and the valuable one. When the premise
collapses, most teams throw out everything or keep everything. Naming the survivors is the
actual judgment.

### 3.3 Blind-run reconciliation

Periodically re-run the analysis **in a fresh context with no sight of the shipped
artifacts**, then reconcile:

| Section | Contents |
|---|---|
| **CONVERGES** | Where the independent run validates what shipped |
| **DIVERGES / SHARPENS** | Specific, enumerated edits |
| **NET VERDICT** | What actually changes |

An analyst who has seen the artifact will anchor to it even while trying not to. The
contamination is silent: the output looks independent and drifts toward the original.
Blindness must be structural, not instructed.

**Rebuild, don't patch.** When confirmed facts contradict the spine, re-derive the
workflow on the new reality. Patching leaves the retired premise load-bearing in places
nobody remembers to check.

---

## 4. Two audiences, two artifacts

The analyst's document is not the decision-maker's document, and neither is the
counterparty's. Build at least two:

- **For the principals** — the honest position, including what is unknown and what would kill it.
- **For the counterparty** — the same facts, their frame, no internal go/no-go content.

Keep internal go/no-go material out of anything shared externally. Mark shareable artifacts explicitly.

---

## 5. Failure modes

| Failure | Symptom | Guard |
|---|---|---|
| Inference laundered as fact | A finding everyone repeats with no source | Stage 1 confirmed/inferred split |
| Stale analysis read as current | Two live-looking docs disagree | Canonical spine + superseded banners |
| Anchored re-review | A second pass that agrees suspiciously fast | Blind-run reconciliation |
| Premature verdict | Go/no-go before the gating facts exist | Gates, not verdicts |
| Analysis as procrastination | Artifacts accumulate, no gate closes | Each gate names who holds the fact |
| Built for the analyst | The decision-maker never reads it | Per-audience artifact |

---

## 6. Related

- `framework/adversarial-analysis-pipeline.md` — the adversarial layer in depth
- `framework/multi-agent-workflows.md` — fan-out, adversarial verification, convergence gates
- `framework/problem-solving-mckinsey.md` — stage 2's 7-step method and issue trees
- `/plan-hardening` — premise gate then scoped probes with a per-hole retest over a written plan, usable at stage 5
- `/research-audit` — fan-out research with claim validation, usable at stage 1
- `/megastorm` — multi-lens ideation, usable at stage 3
