# HOOK SPEC — status-query verification reminder (NOT BUILT)

Status: **specified, not implemented.** Captured 2026-07-31 so the build can happen cold, without re-deriving the reasoning.
Estimated effort: **~45 minutes.**
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

**Why non-blocking:** per the project's warn-vs-block hook design rule, reserve BLOCK for unambiguous violations. This is a heuristic trigger on natural language; blocking would be wrong.

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
