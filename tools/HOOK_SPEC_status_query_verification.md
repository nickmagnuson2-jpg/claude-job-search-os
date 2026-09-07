# HOOK SPEC — status-query verification reminder (BUILT 2026-09-07)

Status: **BUILT AND WIRED.** `tools/check_status_query_verification.py`, wired as the repo's first
`UserPromptSubmit` hook in `.claude/settings.json`, committed in `9dc0d22`. Tests:
`tests/scripts/test_check_status_query_verification.py`, 62 passing. Mutation: 22 killed, 0
survived, 0 isolation failures, 2 allowlist entries for CLI plumbing.

**Kept, not archived.** The reasoning below is why the hook exists and why it is shaped this way,
and none of it is recoverable from the code. Read it before changing the trigger patterns or the
exit behaviour. Estimated effort was ~45 minutes; actual was longer, entirely because of the
mutation pass described under "What the build changed" at the bottom.

Companion: `tools/HOOK_AUTHORING.md` (scaffold + testing conventions — follow it).

---

## 1. The problem this solves

Claude asserts that a file is untouched / a workstream is outstanding / something hasn't happened, **without running a check in the current session.** This is the absence-assertion failure, already a CLAUDE.md Hard Rule — and the hard rule is not holding. It fired again on 2026-07-31, in a fresh context, *while Claude was authoring a framework document about verification discipline.* Salience is not enforcement.

Specific instance: Claude said a named file "hasn't had a pass this session" and was "untouched today." Never ran `ls -la`, never checked git, never opened it. The file had been created that same day. The false premise then produced a second, worse error — using it to tell the user a workstream was under-served, which devalued work they had just completed.

## 2. The key insight — why THIS hook, at THIS trigger

**You cannot hook the assertion itself.** Hooks fire on tool calls and lifecycle events; there is no gate on assistant text mid-generation. Nothing can block Claude from saying "that file is untouched."

But the failures are not uniformly distributed. **Both instances happened while answering the same shape of question:** a status / gap / readiness query — *"is there anything else I'm missing before I clear?"* That is a narrow, high-precision trigger, and it arrives as **user input**, which IS hookable.

So: don't try to catch the claim. **Inject the reminder at the moment the risky question arrives**, before Claude answers.

## 3. Design

| Property | Value |
|---|---|
| Event | `UserPromptSubmit` |
| Behavior | **Additive context injection only.** Always `exit 0`. Never blocks. |
| Script | `tools/check_status_query_verification.py` |
| Failure mode | Benign — worst case is a redundant reminder on a status question |

**Why non-blocking:** this is a heuristic trigger on natural language; blocking would be wrong.

**Why exit 0 is nonetheless correct HERE, unlike on PreToolUse** (clarified 2026-08-14): on
`UserPromptSubmit`, stdout **context injection is the delivery mechanism** — the hook's output
reaches the model by design, so exit 0 delivers. On `PreToolUse`, exit 0 + stderr is *not* surfaced
by Claude Code at all, so a WARN there reaches nobody. Do not generalize this spec's "always exit 0"
to a PreToolUse hook. See `memory/feedback_warn_vs_block_hook_design.md`, whose pre-2026-05-28
"default to WARN" form is superseded, and `tools/HOOK_AUTHORING.md` L77.

**⚠️ `UserPromptSubmit` is NOT currently wired in `.claude/settings.json`.** Currently wired: `PreToolUse`, `PostToolUse`, `Stop`. This hook adds a new event type — a settings.json addition, not a code problem, but budget a few minutes for it and verify the event name against current Claude Code docs before writing the handler.

## 4. Trigger patterns (starting set — tune after live use)

Case-insensitive, match against the raw user prompt:

```
anything (else )?(i'm |im |we're )?missing
what('s| is) (left|outstanding|still open|remaining)
is (there )?anything (else )?(i |we )?(need|should)
(are we|am i|is it) ready
before i (clear|go|start|leave|wrap)
is .{0,40} (done|finished|complete|ready)
what (do|should) i (still )?(need to|have to) do
status (check|update)
where (are we|do we stand)
did (i|we) (miss|forget)
```

**Precision over recall.** A miss costs nothing; a false positive on every message is noise that trains the reader to ignore it. Start narrow.

## 5. Injected text (draft — keep SHORT, it competes for attention)

```
⚠️ STATUS-QUERY DETECTED — absence-assertion risk is highest here.

Before asserting that any file, workstream, or task is untouched /
outstanding / not started / missing:
  • Run `ls -la <file>` or `git log -1 --format=%ci <file>` THIS SESSION.
  • Recency claims decay faster than existence claims — a file that
    existed yesterday probably still exists; a file untouched yesterday
    may have been rewritten an hour ago.
  • Name the scope you checked. "I haven't read X, so I can't say what
    it covers" is correct. "X is outstanding" without a check is not.
  • Then ask whether the work happened in a form you didn't expect
    before calling it unstarted.
```

## 6. Prior-art check — DO THIS FIRST

**`tools/check_no_confabulation.py` already exists and has not been read.** It may already cover part of this surface. Read it before writing anything; extending it may be cheaper and better than a new script. Do not assume it does or doesn't overlap — open it.

Also review for pattern/scaffold: `tools/check_edit_safety.py`, `tools/check_bare_python.py` (both mature, both hooked).

## 7. Test plan (per HOOK_AUTHORING.md)

- **Clean cases** (must NOT fire): ordinary work requests, code questions, prompts containing "missing" in a non-status sense ("the file is missing a header").
- **Trigger cases** (must fire): each regex in §4, plus the two verbatim instances from 2026-07-31 — *"whats taking so long?"* should NOT fire; *"Is there anything else I'm missing before I clear?"* MUST fire.
- **Mandatory live smoke test.** Hook regexes have a documented history in this repo of passing unit tests and failing live. Run it in a real session before considering it done.
- Confirm `exit 0` on every path, including the regex-match path.

## 8. Known risks

1. **Reminder fatigue** — if it fires too often it becomes invisible. Tune toward precision.
2. **Legitimate hedges look like the failure.** "I haven't read X, so I can't say" is *correct* behavior and superficially resembles an absence assertion. The hook only injects context, so this costs nothing — but any future *detector* built on this pattern must distinguish them.
3. Scope creep into a Stop-hook detector. That is a separate, ~2-3 hour build (see §9) and should be gated on evidence, not enthusiasm.

## 9. Deliberately NOT in scope

- **Stop-hook absence-claim detector** (~2-3 hrs): scan the finished response for absence phrasings + filename, cross-reference session tool calls for a matching `ls`/`git log`/`Read`/`Grep`, WARN if none. `Stop` is already wired and `tools/scan_transcript_failures.py` (21KB) is direct precedent. **Detective, not preventive** — the claim already reached the user. Its real value is producing a fire count. **Build only if this hook proves insufficient.** Measure before investing further.
- **Bash false-confirmation hook** (~45-60 min, separate rule): flags `cd <relative> &&` plus an unchained success `echo`. See `feedback_bash_confirm_must_chain_to_operation.md`. Independent of this spec.

## 10. Source memories

- `feedback_llm_verification_system.md` — Rules #1 and #13, plus the 2026-07-31 supplement documenting this recurrence
- `feedback_dont_call_work_unstarted_for_wrong_form.md` — the second-order failure this prevents
- `feedback_bash_confirm_must_chain_to_operation.md` — same-session sibling failure
- `feedback_warn_vs_block_hook_design.md` — why this is WARN-tier


---

## What the build changed (2026-09-07)

Three things the spec did not anticipate, recorded so the next build of this shape starts ahead.

**1. Section 4's trigger list needed anchoring, not just transcription.** The patterns as written
are keyword-shaped ("anything missing", "is X done"). Implemented literally they fire on ordinary
work: "missing" matches *the file is missing a header*, "ready" matches *rename the ready flag*.
Every pattern is now anchored to a question or imperative construction, and a `_MAX_PROMPT_CHARS`
ceiling drops long work requests that merely contain a trigger phrase. Section 4's own instruction
("precision over recall, start narrow") was right; the list underneath it was not narrow yet.

**2. Curly apostrophes are not an edge case here.** A large share of this user's prompts arrive
via dictation, which emits U+2019. Matching only U+0027 would have made the hook miss its primary
input channel silently. `_APOSTROPHES` normalises before matching.

**3. The payload key was the one thing unverifiable offline**, so `extract_prompt()` accepts
several plausible names rather than one. A hook reading the wrong key is indistinguishable from a
hook that never matches, which is the false-negative shape this repo has shipped before.

## What the mutation pass found, and why section 7's test plan was not enough

Section 7 asked for clean cases, trigger cases, a live smoke test, and "confirm exit 0 on every
path." All of that was done and produced **49 green tests**. Mutation then measured **12 survivors
of 26** — 46% of the module's decisions could be broken with the whole suite passing.

Seven were real gaps, now closed with tests. The sharpest is worth stating because it is not in
any checklist:

> The long-prompt test used prose that matched no trigger. So the real function and a mutant with
> the length ceiling REMOVED both returned False — one because of the ceiling, one because nothing
> matched. **The test was structurally incapable of observing the guard it targeted**, and no
> assertion could fix it. The guard only has observable behaviour on a long input that DOES
> contain a trigger.

Captured as `feedback_a_test_whose_input_fails_twice_cannot_see_its_target`. A second instance
turned up the same night in `check_banned_phrase`, where a clean-path guard was unreachable with
the live fixture, closed in `4afccbb`.

Also killed: an assertion using `not x` where the mutant returns `None`. `None` is falsy, so
truthiness could not see the difference. `is False` kills it. That one IS a weak assertion and IS
fixed by tightening — the distinction from the case above determines which fix applies.

**For section 7, if this spec is ever reused:** "confirm exit 0 on every path" is unfalsifiable
for a hook that exits 0 by design. The testable contract is what reaches stdout, and the guards
need a mutation pass, not a coverage count.

## Section 9 items: still out of scope, one now cheaper

The Stop-hook absence-claim detector remains deliberately unbuilt and still gated on evidence that
this hook is insufficient. Note that the fire count it was meant to produce is now partly
obtainable a cheaper way: this hook's injections are visible in transcripts, so measuring how
often the trigger fires is a transcript scan rather than a build.
