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
and runs mostly against `frame.yaml` rather than the pages** (F.2 is the exception, see its
note) — it applies unchanged to a deck, a whiteboard,
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
| A10 | **Every number carries an inline provenance tier from the moment it is written.** Counted / modelled / supplied, or Confirmed / Assumption / Hypothesis / Unknown. A8 sources the PAGE; A10 tiers the NUMBER |
| A11 | **An assumption that drives a decision may not sit in a footnote or in small grey type**, and it states which decision flips if it is wrong |

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
| C13 | **Every printed ratio recomputes from the printed values.** A reader with a calculator reaches the figure on the page, or the figure does not go on the page. See the note below |
| C14 | **A breakdown conserves, and "other" is never its largest row.** If it is, you do not have a breakdown; reach for the client's own taxonomy before adding rows |
| C15 | **Comparison symmetry.** A treatment applied to one side of a comparison (a range, a bracket, a sizing, a concession) is applied to the other, or you say on the page why it cannot be |

### The C13 rule, and the two ways it fails

**Both observed on one artifact, 2026-09-17.**

*Rounding a derived figure.* "$65,979 per customer per month" against a $461,856 headline: 7 x
65,979 = 461,853, a $3 gap, because 461,856 / 7 = 65,979.43 and neither the dollar nor the
booking count divides evenly. The fix is not more decimals, it is not printing a derived figure
that invites the multiplication.

*Subtracting ratios.* "32.4% against 1.6%, a 20.8x gap... the other 19x" fails twice over: 32.4
/ 1.6 = 20.25 from the printed values, not 20.8, and 20.8 minus 1.7 is not a meaningful
operation on two ratios. The ratio of ratios is 12.2x. **When a ratio is hard to print honestly,
print the four underlying rates and let the reader see the gap.**

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
| E7 | **The read-surface audit.** A reader with the artifact and nothing else, no frame, no reasoning, no talk track. Separate instrument from E1 and it fails on different things. See below |
| E8 | **The cut pass runs the two tests, and names what is protected BEFORE it starts** |
| E9 | **Re-render and look after any edit that ADDS text.** Fixed-height pages overprint silently. Verification is rasterize-and-look, never a numeric diff |
| E10 | **End-of-cycle sweeps on the RENDERED text:** banned tokens, cross-page consistency on every duration and absolute, and a restatement count for each claim |

### E7, the read-surface audit — and why E1 cannot do its job

**E1 reads the LEDES in order. E7 reads the WHOLE PAGE cold.** They catch different defects and
neither substitutes for the other.

A deck can route around its own defect in delivery while the page keeps doing the damage. At
the 2026-08 engagement the talk tracks were already instructed to stay silent on a row, so no talk-track pass
ever quoted the sentence that was causing the harm: *"the delivery was already routing around
the problem while the page kept doing the damage, which is why this was a read-surface defect,
invisible to every talk-track pass"* (`output/<engagement>/080426-deck-v3-DECISIONS.md:988`).

**For a SENT artifact this is the primary gate, not a supplementary one**, because there is no
delivery to route around anything.

**Scope the instrument to the claim, corrected 2026-09-17.** A test that shows a reader only the
HEADLINES answers "does this pair stand as an argument." It cannot answer "does the page carry
its proof," because it never saw a page. Quoting headline-only readers as evidence about the
page is a conclusion stated wider than the scope that produced it. Run both, and say which ran.

### E8, the cut pass

**Two tests, and the second is the one that cuts** (`output/<engagement>/080426-deck-v3-PLANNING-SESSION-PROMPT.md:26-38`):

1. **Defensibility.** Is it true, sourced, and ours?
2. **Function.** Why is it here, and how does it progress the argument?

> "Test 2 is stricter and is the operative cut criterion. An element can be true, well sourced,
> defensible and genuinely mine, and still do no work on the page. Test 1 keeps it. Only test 2
> kills it." **Do not let "it's true" or "it's good context" count as an answer.**

**Name what is protected before the pass begins**, not during it. Everything not named is
genuinely on the table.

**Ranking the survivors by impact on the headline number inverts the result.** Measured
2026-09-17: the block that moved the number most was the one three independent readers cut as
irreproducible, while the blocks that moved it by zero were the ones answering the reader's
strongest objections. **Rank by which objection a block closes, and whether a cold reader
actually raised it** — which is what E7 produces.

### E10, and the defect it exists to catch

**A fix creates new surface, and the check usually points at the old surface.** Observed
2026-09-17: a breakdown whose "other" row was 40% of the total was fixed by regrouping, and the
same edit introduced a new column that named 555 of 622. Fixed and reintroduced, same table,
same session, same defect class.

**So the check runs on what the edit just CREATED, not on what it was sent to repair.** Did this
edit make a list, a column, a breakdown, a ratio? Does it conserve, or is it labelled partial?

---

## F. Frame integrity

**Why this section exists.** A-E score craft. A deck can pass craft and lose on frame. Five
independent instruments in the 2026-08-05 engagement (review lanes, the planning packet's test 1,
Stage 0 triage, a 13-panel, and Sections A-E of this rubric) were all built to catch **untrue** things
and were structurally unable to catch **unanswerable** ones. F is that missing audit.

**F.1 and F.3 run against `frame.yaml`, not against the pages.** F.2 is the exception and always
was: **F6 reads the ARTIFACT** (that is the point of it, and after the 2026-09-17 correction its
detector is a blind agent holding the artifact only) and **F7 reads the artifact against the
rehearsal transcript.** The blanket "F never touches the pages" contract was written before F6 and
F7 had named instruments, and a caller following it literally could not run either. Corrected
2026-09-17. F is still artifact-agnostic in the sense that matters: it applies unchanged to a deck,
a whiteboard, a memo or a spoken answer. Where a rule says
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

**Where to aim F3 and F4: probe the BOUNDARIES between adjacent elements, not the elements.**
Recovered 2026-09-17 from `coaching/progress/2026-08-04-<engagement>-whiteboard.md:61`.
The observed failure is never a forgotten item; it is two adjacent concepts fusing into one. An
agent asked "is element 3 sound" finds nothing. An agent asked "state the line between element
3 and element 4 in one sentence" finds the fusion.

### The operating tests, restored 2026-09-17

**These were in `output/analysis/081326-deck-rubric-section-F-DRAFT.md` and did not survive
promotion.** The pattern of the loss is the finding: the promotion kept every rule a SCRIPT can
check and dropped nearly every test a HUMAN runs, which made the rubric scoreable and stopped
it being operable. A presence-only check is not a valid exit under this project's own promotion
rule, and F1, F2 and F5 had been reduced to exactly that.

| Rule | The test you actually run |
|---|---|
| **F1** | For every noun on the surface, **state the level below it in one sentence.** |
| **F2** | Write *"Because [fact], the decision turns on X."* **If that sentence cannot be written, the element does not go on the surface.** |
| **F5** | **The deletion test.** A criterion whose deletion changes nothing is decoration; a criterion whose deletion flips the answer is the one you will be interrogated on first. Run it on every element in the set. |

**F5's deletion test is the one to restore first.** The `closure` and `exclusions` fields make
F5 checkable by script; the deletion test is what makes it decidable by a person, and it is the
only instrument here that finds decoration rather than error.

### F.2 Reachability — can it be defended where it will be attacked?

| # | Rule | Enforced by |
|---|---|---|
| **F6** | **The answer to the most likely probe sits on the surface carrying the claim it defends**, never downstream. Every placement names the delivery model it assumes and answers: *what happens to this if I lose the floor here?* | **blind agent given the ARTIFACT ONLY**, asked what it cannot answer from the page; Nick disposes what it returns |
| **F7** | **Spoken vocabulary matches printed vocabulary for every metric.** Read the frame aloud against the artifact | **token diff against the rehearsal transcript is the instrument**; Nick disposes the diff |

### Why F6 and F7 are not Nick's to run, corrected 2026-09-17

**These two were assigned to "Nick" and that contradicted this section's own premise.** F exists
because five instruments could catch *untrue* things and none could catch *unanswerable* ones.
The the 2026-08 engagement outcome debrief states the mechanism that makes F necessary:

> "A frame defect is not introspectable, because the frame feels coherent from the inside...
> the instrument for the second kind has to be external and mechanical."
> — `coaching/progress/2026-08-13-<engagement>-outcome.md:72-79`

Assigning F6 and F7 to introspection, on exactly the failure class introspection provably
cannot reach, is the defect this section was built to prevent, reproduced inside it.

**The human element does not leave; it moves to the right place.** The agent produces the
finding, Nick disposes it. That is the same split as every other row: the enforcer surfaces,
the human decides. What changed is that Nick is no longer the *detector* for a defect he
cannot see from inside.

**Demonstrated 2026-09-17 on a 2026-09 client take-home.** Four blind reads, artifact only, no
frame. A self-run of the same headline test had PASSED and the blind run FAILED, on the same
pair of sentences, the same day. The self-read supplied the missing bridge from memory, which
is what a frame defect looks like from the inside.
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
F5, F8, F9), and that six is the acceptance test for `check_frame_integrity.py`. F4 and **F6** are
blind-agent tests; **F7** is the token diff. **No rule in F is detected by unaided introspection**,
per the correction above: the human disposes what the instrument returns.

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

## How this file reaches a build session

**The link was one-way until 2026-09-17 and the gate never fired.** This file named
`mckinsey-slides` as its execution layer from the day it was promoted; nothing pointed back.
Measured that day: zero matches for "deck-rubric" across `CLAUDE.md`, `docs/`, the project's
`.claude/skills/`, and the global skill itself. A deck built minutes after loading the skill
violated C11 three times and shipped no C7 takeaway box.

**The return link now lives in the global skill as STEP 0**, written generically: any project
carrying a `framework/deck-rubric.md` gets it read before the build, and it outranks the skill
wherever they differ. That is mechanism, not policy, so it works for the next project too.

**The A-E checker now exists, and it covers nine rules, not A-E.** `tools/check_deck_craft.py`
was built 2026-09-17 as the sibling of `check_frame_integrity.py`: **A2, A4, A8, B4, B5, B7, C2,
C7, C11**, three-state PASS / FAIL / CANNOT_RUN, exit 0 or 2.

    PYTHONIOENCODING=utf-8 python3 tools/check_deck_craft.py <deck.html> --convention-report

**What it deliberately does NOT check, so nobody reads a green run as a clean deck.** A10, C13,
C14 and the B1/B8 word-quality rules need semantics the script does not have, and a weak detector
on a good rule trains the gate to be ignored. E1, E7 and E8 describe a PROCESS rather than a
property of a rendered artifact, so no artifact checker can see them. The selection criterion is
**exclude a rule when nothing has yet tested it, not when it is new** — a freshness filter would
have dropped A10 and E8, both taught in August and merely typed in September.

**It is a CLI gate and is not wired as a hook**, because wiring takes two measurements and only
one is done: mutation survival is clean (245 killed, 0 survived, 11 equivalents allowlisted with
differential proofs), while the restraint measurement needs a corpus of rendered decks that does
not exist yet. Declared in `NON_HOOK_CHECKERS` with that reasoning.

**It found real defects on its first live run** against the deck built the same day: three C2
failures and two B7 over-long bold runs, none of which the hand pass had caught. It also produced
one false positive, since fixed: an `<h3>` heading a prose column was read as an untitled chart,
because the heuristic scoped "chart title" to the page rather than to the heading's own container.

## Companion files

- `framework/slide-craft-mckinsey.md` — the principles this rubric scores against
- `framework/analysis-method.md` — the standing rules for the frame F audits
- `framework/frame-schema.yaml` — the schema F runs against
- `tools/check_frame_integrity.py` — the deterministic subset of F
- `mckinsey-slides` skill — the builder
