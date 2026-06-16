# Bridge to the Stated Need — Match the Interviewer's Signal in One Turn

## What Is It

The interviewer almost always tells you what they need — not as a question, but as a **statement**: a constraint they're under, a problem they're living, or a description of the role/what excites them. A **bridge to the stated need** is the one-sentence move that names the overlap between that signal and a specific, proven thing about you, then stops.

It is the single highest-EV move in a founder/hiring-manager call, and it is usually *handed to you* in the back third when they describe the role or what they love. Missing it is the most expensive recurring failure in the corpus (hypothesis **H4**; anti-pattern "failed to detect-and-bridge the interviewer's stated live need").

---

## Quick Overview

**What it is:** Detect when the interviewer states a need (a constraint OR a role/value description that matches you), and respond with one specific bridge sentence + "that's why I want this," then stop.

**Why it's hard:** The signal is not phrased as a question, so the "answer their question" reflex never fires on it. Under live load the default is to deliver prepared content instead of routing to their last signal.

**The two valences (same muscle):**
- **Negative — don't contradict the stated constraint.** They name what they need; do not answer with its opposite. *(A founder call: the founder said "we're sprinting toward what's coming up"; Nick gave a slow-down-to-speed-up philosophy. Dim 4 = 1.5, the canonical anchor.)*
- **Positive — claim the handed match.** They describe the role/their excitement in words that echo what you just said you value; name the through-line, don't generically affirm. *(A founder call: the founder described the engagement-lead role as "working solutions, not powerpoint slides" — almost verbatim Nick's own "not strategy-deliver-a-deck-and-move-on" from two turns earlier; Nick said "Gotcha. Absolutely." and pivoted.)*

---

## How to SPOT it — three tells, in priority order

1. **Lexical echo of your own words.** They describe the role/their problem using a phrase that maps onto something *you said earlier in this same call*. Hearing your own idea come back in their mouth is the cue. You don't need to catch the whole pattern — just "wait, that's my point coming back at me."
2. **Excitement self-disclosure.** "This is the part I love" / "what gets me most excited" / "what I spend most of my time on, even as CEO." When they tell you what they personally love, they've just pointed at the highest-value bridge in the call.
3. **Abstract → concrete shift.** The moment they stop describing the company and start describing *what this person actually does day to day*, they are implicitly asking "is this you?" Answer the unasked question.

**Listening reframe:** run two loops, not one. Loop A = "answer their question" (fires on questions). Loop B = "match a statement to me" (fires on constraints, excitement, role descriptions). Most misses are Loop B never firing because there was no question mark.

---

## The MOVE — the 8 seconds after you spot it

One specific bridge sentence, then **stop talking**.

> Structure: "That's exactly the [X] I [said I cared about most] — [one specific, quantified proof]. That's the seat I want."

Worked example (what the positive-valence call should have gotten):
> "That's exactly the implementation work I said I cared about most — the call-center pilot wasn't a deck, I built the listening framework and drove it to a $10M rollout. That's the seat I want."  *(~10 sec, then silence.)*

Negative-valence example (what the negative-valence call should have gotten):
> "Then the question for this seat is what comes off your plate *before* the conference, not after. That's the kind of compression I'd own."  *(Match the stated constraint; don't argue pacing philosophy.)*

**Three rules that make it work (each one is exactly what the failures violated):**
- **Specific proof, not the value restated.** "I love implementation" again = the generic affirm that fails. A named, quantified instance = the bridge.
- **Say "that's why I want this" out loud.** Close the loop explicitly; don't imply it.
- **Then stop.** Don't bury it under your next question. Silence lets it land. Pivoting is what makes it evaporate.

---

## Why This Works

- Generic affirmation of a handed match ("Absolutely, that's exactly what I love") is **worse than silence** — it signals you heard the words but missed the gift; you read as agreeable, not aligned.
- The specific bridge converts "pleasant candidate" into "that's the person he just described." That conversion is the call.
- It is almost always offered in the back third of a founder call, in the exact form the positive-valence call used. The job is to stop being surprised by it.

---

## When to Use

Every call, whenever a tell fires — founder/CEO meets especially, but any stage. Bidirectional: hostile rooms (don't contradict the stated constraint) and warm rooms (claim the handed match) identically. Independent of format and of interviewer disposition — that independence is precisely the H4 claim.

---

## How It's Operationalized (so this doesn't decay into a doc no one reads)

- **`/prep-interview`** writes, on every cheat sheet: the predicted interviewer "live-need" sentence(s) + the one-line in-call governor.
- **`/debrief`** scores a per-call binary: *did they state a need / hand a match? did you bridge within one turn?* — tracked as a rate, feeding **H4**'s test log.
- This file is the **single canonical source**. `coaching/hypotheses.md` (H4), `coaching/anti-pattern-tracker.md`, and the founder-call comparison doc reference it rather than restating the strategy — no drift.

---

## Cross-references

- Hypothesis: `coaching/hypotheses.md` → **H4** (test log, promotion criteria, behavior-change).
- Anti-pattern: `coaching/anti-pattern-tracker.md` → "Failed to detect-and-bridge the interviewer's stated live need (either valence)."
- Origin analysis: the founder-call comparison doc in `coaching/progress/`.
- Source calls: the two founder-call transcripts in `coaching/progress/` — one negative-valence, one positive-valence.
- Sibling strategies: `direct-answer-structure.md`, `question-back-technique.md` (the bridge often *precedes* a question-back), `gap-reframing.md`.
