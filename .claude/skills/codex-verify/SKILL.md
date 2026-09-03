---
name: codex-verify
description: Run a second model (Codex) against work that matters and land the result where something reads it. Use when verifying a plan, a handoff, a build log, or a large code change before pushing; when a push is blocked by the cross-model gate; or when Nick says "have codex check this", "validate this with codex", "second opinion". Also covers the diverge mode for an independent take on a goal.
---

# /codex-verify

A second model finds what the author cannot see. Measured across four real runs on
2026-09-02 and 2026-09-03, Codex found something Claude had missed **every time**:
three P0s in a plan; a counterexample that broke a framing already built into a shipped
tool; a silent-loss defect that the *fix* for a silent-loss defect had rebuilt and that
was losing roles live; and four wrong claims in an analysis about to be reported as fact.

## Pick the mode first — they have OPPOSITE information rules

| Mode | Use for | What it gets |
|---|---|---|
| `verify` (default) | Checking work that exists: a diff, a plan, an analysis, a claim set | The diff, your numbered claims, your known errors, any prior report |
| `diverge` | An independent answer to the same goal | The goal ONLY. Diff and claims are structurally withheld |

**Do not use `verify` when you want independence.** An agent that has seen the artifact
anchors to it even when told not to, and the contamination is silent: the output looks
independent while drifting toward what it saw. `diverge` refuses to assemble the diff or
the claims so that cannot happen by oversight.

## Running it

```bash
PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py \
  --target "the pending-log fix for the career-scan drain" \
  --paths tools/career_scanner/scanner.py tools/role_queue_read.py \
  --claim "an unacknowledged role can never be lost" \
  --claim "every parser signals a swallowed failure" \
  --known-errors "I ran one diagnostic pass with a stripped env and got 20 spurious failures" \
  --prior output/analysis/090226-CODEX-VERIFY-DRAIN.md
```

`--print-only` assembles the prompt without spending a run. `--mode diverge` for the
independent form.

## What to feed it — this is the part that decides whether the run is worth anything

Ranked by measured value:

1. **Numbered claims** (`--claim`, repeatable). The single biggest lever. "What do you
   think of this framing?" returns thoughtful agreement, which is worth nothing;
   *"here are my seven claims, say which are wrong"* corrected four of them.
2. **Your own known errors** (`--known-errors`). Counter-intuitive and the highest-value
   ingredient found so far: telling it what you already got wrong lets it identify which
   of your *surviving* conclusions rest on contaminated evidence. That is the finding
   you cannot generate yourself.
3. **The diff** (`--paths`). Scoped to the changed paths, never the whole repo — a
   larger payload buries the signal. Truncation above 800 lines is announced in the
   prompt, never silent.
4. **The prior report** (`--prior`). Makes it check whether what it raised was actually
   fixed, rather than re-deriving it.

## The sandbox — do not loosen it

`--sandbox workspace-write` is a **local execution policy** on shell commands the model
runs on this machine. Write access buys exactly one thing: Codex writing its own report,
because `-o` captures the closing chat message and **not** the artifact.

Its price is isolation from the network and the user session. On 2026-09-02 its *lead*
finding was "automation is off, launchctl returned zero jobs" — **false**, an artifact of
its own sandbox, stated first, with cited evidence, in a register indistinguishable from
its true findings.

The fix is **not** `danger-full-access`, which would hand an external model unrestricted
shell to buy a `launchctl` reading. `codex_verify.py` runs the environment probes **here,
in the real shell**, and pastes the answers in as established facts. Never remove that.

**Standing calibration: discount its ENVIRONMENT claims, never its code reasoning.**
Asymmetric, and measured twice.

## Never `--with-api-key`

The ChatGPT subscription grants **zero** API credits; that flag switches to separate
pay-as-you-go billing on top of it.

## Where the output goes

Every run appends a row to `tools/.cross-model-ledger.jsonl`, which has two real readers:

- **`tools/cross_model_gate.py`** — the pre-push gate. A push carrying a governed
  document, a wired hook, or a large/broad code change is BLOCKED unless a verification
  covers it, or Nick types `CODEX_VERIFY_WAIVE="why" git push`.
- **`/standup`** — surfaces open findings and the running waiver count.

**Findings arrive undispositioned on purpose.** A finding stays open until someone marks
it fixed, rejected with a reason, or parked with a reason. This is deliberate and it has
an origin: for three weeks the career scanner scored ~30 roles a night into a file
nothing read. A report written to `output/analysis/` and consumed by nobody is that same
defect in a new costume.

## Before relaying anything it says

Cross-model output is where relaying is most tempting and least safe: you cannot inspect
its reasoning, see what it read, or assume its severity vocabulary matches yours. Run
every finding through `framework/review-findings-protocol.md` before it touches anything.
