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
