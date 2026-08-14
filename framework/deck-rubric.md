# Deck Rubric — the enumerated gate

**Promoted 2026-08-13** from the 2026-08-03 engagement-specific rubric in `output/`, with the
`Status in v1` column stripped and the worked conflicts generalized. Section F is new.

## Where this sits

Three layers, one job each. Do not collapse them.

| Layer | File | Holds |
|---|---|---|
| **Principles** | `framework/slide-craft-mckinsey.md` | Why and how. Ledes, the four corners, the 30-second test, the 11-point checklist, formatting, graphics |
| **Gate** | **this file** | The enumerated scoring instrument. A-E score craft, F scores the frame |
| **Execution** | `mckinsey-slides` skill | Builds the artifact. 16:9 HTML, PDF export, component layouts |

Same shape as the analysis method one layer up: a thin standing doc, a deterministic gate, and an
executable that produces the thing.

## How to use it

Every page passes A through D. E is the deck-level gate and runs once, last. **F is artifact-agnostic
and runs against `frame.yaml`, not against the pages** — it applies unchanged to a deck, a whiteboard,
a memo, or a spoken answer.

**Nothing ships until E1 and F both pass.**

---

## A. The four corners — structural, every page

| # | Rule |
|---|---|
| A1 | **Lede / action title.** One key insight, active voice, never more than two lines, key info up front, preferably no punctuation |
| A2 | **Section tracker** so the reader can follow where they are. House position is top right |
| A3 | **Chapter tracker** for longer documents. Roughly 15+ pages qualifies |
| A4 | **Stickers** communicate maturity: Preliminary, Draft, As of, HYPOTHESIS for an 80/20, FOR DISCUSSION if it needs input. House position is left, under the subtitle rule |
| A5 | **Legends top right corner.** Inline badges beat a remote key |
| A6 | **Only use the page body.** Nothing outside it |
| A7 | **Footnotes** clarify details, bottom left |
| A8 | **Every page has a source.** Including the ones that feel like they do not need one |
| A9 | **Consistent font sizing in the page body.** See the note below |

### The A9 rule, stated correctly

The print bar is often quoted as "no more than two font sizes in the page body," which collides with
any inherited layout spec that mandates distinct sizes for body, row labels, pulled metrics and
captions.

The resolution is in the source: *"maintain the same font size across the same content (e.g. prose,
charts) and do not shrink it too much."* **The rule governs consistency within a content type, not a
hard count across the page.** All prose is one size, all captions are one size, and nothing shrinks to
fit. An inherited projection bar stands.

---

## B. Titles and text

| # | Rule |
|---|---|
| B1 | **Avoid ambiguous verbs, use a number instead** |
| B2 | **Stand-alone titles.** Never start a page with "..." |
| B3 | **Do not use "us" or "we", use the client's name.** See the note below |
| B4 | **A number in the title must be findable on the page** |
| B5 | **Bullets: 2 to 5 per list. Never exactly one.** A list by definition has 2+ |
| B6 | **Parallel bullets**, common grammatical form |
| B7 | **Bold only the first few words.** Over-bolding distracts and lengthens |
| B8 | **Kill empty adjectives.** "Current" and "existing" are almost always redundant. "Significant" and "material" get used when you do not have an actual number. If you have a number or a range, use it |
| B9 | **Eliminate trailing prepositional phrases.** Key information goes in front |
| B10 | **No filler** like "Based on the analysis" |

### The B3 rule is not a find-and-replace

The canonical example does two things at once: *"This is a terrific opportunity for us"* becomes
*"This is a $1B opportunity for Company X."* It names the client **and** adds a number.

The complication is that "we" is sometimes legitimate, when the speaker's own organization is genuinely
the actor, and inside lines that have been memorized for delivery. **Name the client wherever the
client is the actor. Keep "we" only where you are genuinely the actor. Every instance gets decided;
none get bulk-replaced.**

---

## C. Charts and data display

| # | Rule |
|---|---|
| C1 | **Temporal data: horizontal orientation, total on the left. Conceptual data: vertical, total on top** |
| C2 | **Chart titles are two-part:** bold subject, then italic unit after a comma |
| C3 | **Numbers sit inside the bars**, reversed out, where the bars can hold them |
| C4 | **Break the axis** when one value dwarfs the others, so small values stay readable |
| C5 | **Numbers must foot** despite rounding |
| C6 | **Footnote the calculation** when anyone will ask how it was derived |
| C7 | **Key takeaways box** on any chart, and call out the number |
| C8 | **Consistent timeframes**, made explicit |
| C9 | **Use color judiciously.** Red/green/yellow only if you are confident in the ratings. No elaborate color key elsewhere on the page |
| C10 | **No more than 3 to 4 colors** |
| C11 | **Do not use dashed elements.** They are hard to distinguish and interpret. See the note below |
| C12 | **All pages are essentially tables.** A table organizes logically, forces MECE decomposition, and creates clear top-to-bottom, left-to-right flow |

### The C11 rule, and how to keep a visual grammar anyway

Encoding schemes reach for dashes to signal epistemic status: dashed badges for uncertainty, dashed
tracks for unbounded ranges, dashed outlines for absent data. That collides with C11.

**They reconcile because the dash is almost never the load-bearing signal.** An open arrowhead is what
says a range has no upper bound. The absence of marks inside real axes is what says the data is
missing. Filled versus empty is already dash-free.

**Strip every dash, keep every semantic.** The drawing gets more legible, not less. Placeholder
hatching is scaffolding and comes out before submission, so it is exempt.

---

## D. The eight pieces of feedback, pre-empted

Each is feedback you are guaranteed to get, so the fix goes in up front.

| Feedback | Trigger |
|---|---|
| "De-word this a bit" | Small body font |
| "Simplify" | A combination of graphs, boxes and call-outs on one page |
| "What is the key insight?" | A chart with no key-takeaways box |
| "This is a busy slide" | Charts not legible at page size |
| "Too many points per page" | The title says three things |
| "What are the implications?" | No call-out or summary page |
| "How is this calculated?" | A complex calculation on the page |
| "Are we double counting?" | Numbers are all-in and do not discount |

---

## E. Deck-level gates — run before submission

| # | Gate |
|---|---|
| E1 | **The 30-second print-out test.** Print it, read only the ledes in order, and the story must hold up on its own. **Nothing ships until this passes** |
| E2 | **Insight ledes are ~80% of the story**, process ledes the rest. A process lede is justified only where process is the content |
| E3 | **Top-down.** Aggregated to detailed, recommendation first |
| E4 | **Four corners updated and consistent** across every page |
| E5 | **Optimal spacing.** Do not overcrowd, and do not leave too much empty space. **Fill by densifying, never by distributing** |
| E6 | **Fit and align content within page borders**, rows and columns aligned consistently |

---

## F. Frame integrity

**Why this section exists.** A-E score craft. A deck can pass craft and lose on frame. Five
independent instruments in the 2026-08-05 engagement (review lanes, the planning packet's test 1,
Stage 0 triage, a 13-panel, and Sections A-E of this rubric) were all built to catch **untrue** things
and were structurally unable to catch **unanswerable** ones. F is that missing audit.

**F runs against `frame.yaml`, not against the pages**, and it is artifact-agnostic. Where a rule says
"surface," read: the page, the board, or the sentence that names the element.

### F.1 Derivation — is the frame sound?

| # | Rule | Enforced by |
|---|---|---|
| **F1** | **Every named element carries the level below it, on the surface that names it.** Criterion to its measure. Capability to its surface and owner. Quantity to its decomposition and dominant input. Driver to the test that would kill it | checker (`measure` present and short) + blind agent (is it the right level) |
| **F2** | **Every element traces to a named fact**, written as *"Because [fact], the decision turns on X."* **Provenance to the client's own vocabulary is not a derivation** | checker (`because` resolves to a real fact id, and that fact's `first_seen` precedes the element's) + blind agent given the brief only |
| **F3** | **No single input is load-bearing in two elements.** Where two could be accused of overlap, name the pair and state the distinction in one sentence | checker (set intersection over `inputs` ids) |
| **F4** | **Interaction logic is stated:** independent, sequential gate, or trade-off pair. And do any two elements recommend opposite actions on the same object? If so that is the finding, not a defect to hide | blind agent |
| **F5** | **The set is closed and the closure is defended.** Why these N and no more, with at least one plausible element deliberately excluded and its reason given | checker (`closure` and `exclusions` non-empty) + blind agent given the brief only |

**F3 and F4 run on element names and one-line definitions only, never on the artifact.** An agent
holding the artifact rationalises overlap as emphasis.

### F.2 Reachability — can it be defended where it will be attacked?

| # | Rule | Enforced by |
|---|---|---|
| **F6** | **The answer to the most likely probe sits on the surface carrying the claim it defends**, never downstream. Every placement names the delivery model it assumes and answers: *what happens to this if I lose the floor here?* | Nick |
| **F7** | **Spoken vocabulary matches printed vocabulary for every metric.** Read the frame aloud against the artifact | Nick, assisted by a token diff against the rehearsal transcript |
| **F8** | **Every element traces back to the locked problem statement.** If the problem statement assigned a metric a role (guardrail, target, constraint), no element may reassign it | checker (every `because` fact appears in the D1 fact base) + blind agent |

### F.3 Discipline — what the process must record

| # | Rule | Enforced by |
|---|---|---|
| **F9** | **Compression ledger.** Every collapse, rename or cut records what the element was, what its definition was, and where that definition now lives. "Nowhere on the surface carrying the element" is a defect. **Frame elements are PROTECTED by default** | checker (diff current against the `locked:` version, fail on a dropped `measure` for a protected element) |
| **F10** | **Every unknown is dispositioned: assumption / question / data request.** Assumption is unknowable in the timebox and carries a label, a basis and a sensitivity note. Question needs a named human's judgment. Data request exists in a system and carries an owner and a date. **The disposition changes by mode; the item does not** | checker (structure) + Nick (which disposition) |
| **F11** | **Where the frame requires data that does not exist and forbids sourcing it, the output is the named empty slot**, never a laundered characterization. *"We do not know how our own headline number is computed"* is a finding, and usually the strongest one available | Nick |
| **F12** | **Every recommendation carries a confidence and a next action.** Confidence is stated, not implied. The next action names who disposes it | checker |

### The F gate

**F fails if any of F1-F12 fails.** On failure the artifact does not ship, exactly as E1 governs the
ledes test.

**Blocking condition, so the gate can demonstrably fail:** the 2026-08-05 deck fails **nine of twelve**
(F1, F2, F3, F4, F5, F6, F7, F8, F9). The deterministic subset of those nine is **six** (F1, F2, F3,
F5, F8, F9), and that six is the acceptance test for `check_frame_integrity.py`. F4 is the blind-agent
test; F6 and F7 are human.

---

## What a run must record

Cheap to capture during the run, impossible to reconstruct afterward. Same criterion as `frame.yaml`.

- **Which rules fired.** A rule with zero fires across N runs is decoration and becomes a demotion
  candidate. This is F5's own logic turned on the rubric itself
- **The cut list.** What was removed and why. A definition purged from the pages reappears in the room
  spoken, which is exactly how F7 defects are born. F9's compression ledger is the mechanism
- **The rehearsal recording**, without which F7 cannot run at all

---

## Run order

**Mechanical, no content needed.** Chart unit lines (C2), strip dashes and keep semantics (C11),
trackers and stickers to house positions (A2, A4), source lines on every page (A8).

**During the content pass.** The client-name sweep decided instance by instance (B3), bold moved to
the first few words (B7), a number into every lede that can carry one (B1), axis legibility (C4).

**Last, gated.** F in full, then E1. Nothing ships until both pass.

---

## Companion files

- `framework/slide-craft-mckinsey.md` — the principles this rubric scores against
- `framework/analysis-method.md` — the standing rules for the frame F audits
- `framework/frame-schema.yaml` — the schema F runs against
- `tools/check_frame_integrity.py` — the deterministic subset of F
- `mckinsey-slides` skill — the builder
