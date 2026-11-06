# Multi-Model Architecture: the independent-reviewer layer

**Created 2026-11-06.** Version 1. Describes the code as read on that date.

**What this is.** The top tier of the ladder in `framework/verification-umbrella.md`: memory < skill
< hook < **independent reviewer**. The same model that produced a defect cannot reliably catch it,
because its self-review shares the priors that produced it. This layer sends work to models from
other families and records what they found where a gate and a daily brief read it.

**The code is the source of truth.** This file says what each part does and how the parts connect.
When it disagrees with a script, the script wins and this file is out of date. It does not restate
the operating procedure, which lives in `.claude/skills/verify/SKILL.md`, or what to do with a
finding once it arrives, which lives in `framework/review-findings-protocol.md`.

---

## The parts

| Part | Role | Reads | Writes |
|---|---|---|---|
| `.claude/skills/verify/SKILL.md` | Operating procedure: which mode, what to feed a run, how to end a blocked push | - | - |
| `tools/codex_verify.py` | Builds the prompt, runs one model inside an OS sandbox, parses its findings | the diff, the report the model wrote | one ledger row per run |
| `tools/reader_review.py` | Renders a finished document and runs `codex_verify.py` once per model on three fixed questions, or on 3 to 5 decision-maker seats | the document, its sources, a BUILD-LOG | reports, a BUILD-LOG Ship line (`--gate`) |
| `tools/.cross-model-ledger.jsonl` | The record: every run, every finding, every disposition, every waiver | - | - |
| `tools/finding_write.py` | The only writer of dispositions and acceptances; `list --open` is the workstream probe | the ledger | the ledger (whole-file rewrite) |
| `tools/cross_model_gate.py` | The pre-push gate, and `summary()` for `/standup` | the ledger, the push's `--numstat` | waiver rows |
| `tools/hooks/pre-push` | Calls the gate for the last non-deletion ref in the push, with that ref's commit time as `--since` | - | - |
| `/standup` | Prints `cross_model_gate.summary()` whenever it is non-empty | the ledger | - |

The flow is one direction: a run writes a row, the gate and `/standup` read rows, and
`finding_write.py` closes findings. A report written to `output/analysis/` that nothing reads is
the failure this design exists to prevent, which is why every run writes a ledger row and every
finding arrives with `disposition: null`.

---

## The models

Models are data in `codex_verify.MODELS`. Adding one is an entry there, plus its name in
`cross_model_gate.WIRED_MODELS` and `LEGACY_MODEL_FAMILIES`.

| Name | CLI | Family | Prompt via | Report via | Images |
|---|---|---|---|---|---|
| `codex` (default) | `codex exec` | openai | stdin | the model writes the file | `-i` flag |
| `grok` | `cursor-agent -p --mode ask --model cursor-grok-4.6-xhigh` | xai | argv | stdout, written to file by the wrapper | none (text only) |
| `fable` | `claude -p --model claude-fable-5-1` | anthropic | stdin | stdout, written to file by the wrapper | by path named in the prompt |

**Family is the field that counts, not the name.** The gate counts independent perspectives, and two
rows from one provider under different names are one perspective. A row with no `family` field is
mapped from its `model` label through `LEGACY_MODEL_FAMILIES`; a row with neither counts as `openai`.

**The author family is additive, never sufficient.** `AUTHOR_FAMILY = "anthropic"`. A Fable row can
add to coverage, but the number a tier demands must be met by families outside Anthropic. The
`Model` docstring names the admissible families on the cursor-agent subscription that are not
wired: google, moonshot, zhipu.

**A model that cannot take an image is refused, not run blind.** If `--image` is passed to a model
whose `image_via` is `none`, the run exits before dispatch. A new model defaults to `none`.

---

## `codex_verify.py`: one run

### Two modes with opposite information rules

| Mode | The prompt contains | The prompt never contains |
|---|---|---|
| `verify` (default) | `--target`, numbered `--claim`s, `--known-errors`, the diff for `--paths`, `--prior` report, `--question` | - |
| `diverge` | `--target` as the goal, `--question` | the diff, the claims, the known errors |

In `diverge` the diff and claims are never assembled, so an independent answer cannot be
contaminated by the artifact through a caller's oversight. Both modes append the sandbox warning,
the environment facts and the report rules.

### What goes into a `verify` prompt

1. The target, the numbered claims ("say which are WRONG, UNPROVEN, or right for the wrong reason"),
   and the known errors.
2. The diff: `git diff <base> -- <paths>`, where the base is the merge-base with the upstream
   branch, else `origin/HEAD`, else the nearer of `origin/main` and `origin/master`, else `HEAD`.
   Committed-but-unpushed work is therefore in the diff. Above 800 lines it is cut, and the cut is
   stated in the prompt.
3. A sandbox warning, then environment facts gathered in the real shell (loaded launchd jobs, git
   HEAD, `git status --short`, Python version), given as authoritative so the model does not probe
   its own sandbox and report that as the machine's state.
4. Report rules: write the report to the given path, end it with `## FINDINGS (machine-readable)`
   followed by a JSON array of `{id, severity, location, summary}`, severity defined by
   consequence (P0 reachable and silent; P1 needs an unusual input or fails loudly; P2 correctness
   not at stake), and an empty array is a valid result.

### Refusals before dispatch

| Exit | Condition |
|---|---|
| 4 | `--paths` include guard infrastructure (as decided by `check_guard_edit_approval.is_guarded`) and `--bar` is missing or under 8 words |
| 3 | A mutation backup is present for the source tree. While a mutation run holds the lock, every backup refuses. With no run alive, a backup identical to its source is ignored with a note, and any other backup still refuses |
| 5 | `verify` mode only: a `--paths` entry has no diff from the base and is missing, untracked, or tracked and unchanged. Gitignored files on disk are exempt |

`--print-only` skips the exit-3 and exit-5 checks and prints the prompt. On every run whose paths
the gate classifies, the script prints to stderr a `GATE:` line (the tier, the number of families
outside Anthropic the push will need, whether this model counts) and the count of open P0s on those
paths.

### The sandbox

The model runs under `sandbox-exec` with a generated seatbelt profile. If `sandbox-exec` is not
found, the run refuses. Codex and Grok have their own sandboxes disabled inside it (codex runs with
`--dangerously-bypass-approvals-and-sandbox`, grok with `--sandbox disabled`), because nesting the
two stopped codex attempting reads at all. Fable's argv carries no sandbox flag. The profile:

- denies reads of `data/`, `memory/`, `coaching/` and `.git/` always, and of every other gitignored
  entry except `output/`;
- denies reads of `~/.claude` (the live memory tier sits outside the repo);
- denies writes to the repo, then allows reads and writes in `output/analysis/` and writes in the
  report's directory;
- applies last, so nothing can re-open them: denies reads of every path in the gitignored
  `tools/.sealed-paths.conf`, and of everything beside the repo under its parent folder (from a
  linked worktree, beside the main checkout too).

`output/` is readable on purpose: the work under review lives there, and a verifier that cannot
read the artifact returns a clean report that still counts.

### What a run records

A row is appended for every run, including a failed one. `verified` is true only when the model
exited 0, the report file's mtime is no earlier than 1 second before dispatch, and the
report holds a parseable findings array. Findings without a `location` are kept and stored with
`location: null`. A missing model binary raises instead of looking like agreement. Under pytest, the
script refuses to start a real model or write the real ledger.

Default report path: `output/analysis/<MMDDYY>-<model>-<target slug>-<HHMMSS><4 hex>.md`.

---

## `reader_review.py`: a finished document for a named reader

1. **Render.** HTML goes through headless Chrome: the dumped DOM becomes text with hidden and
   collapsed content kept, and a light-mode screenshot at the measured page height is cut into
   3000 px page images. Markdown and text are copied as they are, with no images. Inputs land in
   `output/analysis/reader-review-inputs/<MMDDYY>-<slug>/`.
2. **Prompt.** Three questions: Q1 accuracy (against `--source-dir` if given, otherwise every
   unsupported claim marked UNVERIFIED), Q2 usefulness to `--reader` for `--purpose`, Q3
   confidentiality scoped to `--protect`. With no `--protect`, Q3 is skipped and the report must say
   so.
3. **Dispatch.** One `codex_verify.py` process per model (default `codex,fable,grok`), in parallel,
   with `--paths` set to the page text plus the source files, so each run writes its own ledger row.
   Grok gets no images.
4. **Evidence scope.** Each report gets a block prepended stating what that model was given and what
   the sandbox denied. Findings that say a claim is unsupported, and whose wording matches a line in
   `data/projects/` or `coaching/coached-answers/` the model could not read, are labeled
   `outside reviewer scope?` with that `path:line`. The label is a pointer for a human check; it
   does not change the findings array or the ledger.

`--gate <BUILD-LOG>` fills `--reader` and `--purpose` from the log's Phase 0 `reader` and
`value_item`, and `--source-dir` from a `sources/` folder beside the log; it refuses a blank `reader` or `value_item`, and appends a `reader_review:` line to the log's Ship
block. `--panel` replaces the three questions with 3 to 5 seats (`role | mandate | measured on |
fears`), each run in isolation on one model (default `codex`). Findings are clustered across seats;
a problem named by 2 or more seats is listed first as must-fix in `output/analysis/<MMDDYY>-<slug>-panel.md`.

---

## The ledger: `tools/.cross-model-ledger.jsonl`

Append-only JSON Lines, gitignored (the repo is public), resolved from the repo root of the script
that writes it, so a linked worktree has its own ledger. Staged by `tools/backup-data.sh`.

**Run row.** `recorded` (UTC ISO), `target`, `report`, `paths`, `findings`, `waived`, `model`,
`family`, `images`, `rc`, `report_written`, `report_fresh`, `verified`. A waiver row has
`target: "WAIVED"`, `report: null`, `waived: true` and a `reason`. Older rows lack some fields; the
gate treats a row with no provenance fields as usable.

**Finding.** `id`, `severity`, `location`, `summary`, `disposition` (`null` until set), and once set,
`why` and `dispositioned`. A parked P0 that Nick accepts also carries `accepted_by`, `accepted_at`,
`accepted_why`.

**Address.** `<line number>.<finding id>`, for example `12.F3`, where the line number counts
non-blank lines from 1. Stable because the file is append-only.

**Dispositions.** `fixed` and `rejected` close a finding. `parked` means verified and deferred: it
stays listed in `/standup`, and a parked P0 keeps blocking pushes until `finding_write.py accept`,
which is Nick's to run. Every disposition requires `--why`, and changing one requires `--force` and
clears any acceptance.

**Writers and the lock.** `codex_verify.py` and the gate's waiver path append through
`cross_model_gate.append_row`; `finding_write.py` rewrites the whole file. Both take the same
`inbox_lock` file lock, so an append cannot land between the rewrite's read and its replace. Never
edit the file by hand.

---

## `cross_model_gate.py`: the push gate

`tools/hooks/pre-push` reads the ref lines git passes it and keeps only the last one that is not a
deletion. It pipes `git diff --numstat` for that ref's range into the gate, with `--since` set to
the commit time of that ref's head. A review recorded before that time does not count.

**A push of several refs is checked on its last ref only.** On `git push origin a b`, a governed
path or an open P0 present only on `a` never reaches the gate, and the hook can exit 0. Push one
ref at a time.

### Blast-radius tiers

The highest matching tier wins, and every path matching a rule at that tier must be covered.

| Tier | Matches | Families required |
|---|---|---|
| 3 | enforcement machinery (`tools/check_*`, `tools/prepush_*`, `*_guard.py`, the gate, `codex_verify.py`, `tools/hooks/pre-push`, `tools/hooks/install.sh`); `CLAUDE.md`, `MEMORY.md`, `.claude/settings.json`, `.claude/settings.local.json`, `tools/.pending-draft*`, and paths containing `cover-letter`, `/cv-`, `resume`, `prep` or `cheat-sheet` | 2 |
| 2 | `.claude/skills/`, `framework/`, the data writers (`pipe`, `todo`, `networking`, `person`, `act` `_write.py`), `tools/finding_write.py`, `tools/*_ledger.py`; governed documents in `output/analysis/` (handoff, build-log, plan) | 2 |
| 1 | other `tools/` and `tests/` paths | 1, and only when the push changes 120 or more lines, or 6 or more files, of `.py`/`.sh` under `tools/` or `.claude/skills/` |

### What blocks

- **A qualifying push with an uncovered path.** A path is covered when usable, fresh rows naming it
  come from at least the required number of families outside Anthropic. Usable means waived, or
  `verified` true, or (on older rows) `rc` 0 with a report written. A row with an unreadable
  timestamp is not fresh.
- **An open P0 on any pushed path, on any push**, qualifying or not, fresh or stale. A parked P0
  blocks until accepted. P1 and P2 never block.
- **A ledger with unreadable lines.** Its coverage is unknown, so it clears nothing. A missing
  ledger has no rows and so covers nothing.

**Temporary exception.** Until 2026-11-12 (`FABLE_STANDIN_UNTIL`), a Fable row may fill one of the
required slots when at least one family outside Anthropic has also covered the path, and the clear
message says so. From 2026-11-13 the normal rule applies with nothing to revert.

**Waiver.** `CODEX_VERIFY_WAIVE="<why>" git push`, typed by Nick. It is recorded as a ledger row
whenever it skips a required verification or an open P0, and `/standup` prints the running count.

---

## Where results land

| Result | Location | Read by |
|---|---|---|
| The model's report | `output/analysis/` (default path above) | the session that dispatched it; `--prior` on the next round |
| The run and its findings | the ledger | the gate, `/standup`, `finding_write.py list` |
| Reader-review inputs and scope block | `output/analysis/reader-review-inputs/`, the top of each report | the session |
| Reader-review record | the BUILD-LOG Ship block (`--gate` only) | `tools/build_check.py`, which fails a log marked sent whose Ship block has no `reader_review` report |

Before any finding is relayed or acted on, it goes through `framework/review-findings-protocol.md`.

---

## What is not built

- **Live transcript observation.** The second-model versions of the interview checks B3, C1 and D1 in
  `framework/interview-prep-discipline.md` (clarifying-question count, bridge-frame cross-check,
  fired-belief detection) do not exist. This layer reviews finished artifacts only.
- **A fourth family.** No google, moonshot or zhipu model is wired. The earlier plan for this doc
  was gated on Gemini API access; that gate no longer applies to the doc, and no model has been
  added.
- **Automatic reader review.** `reader_review.py` runs only when invoked. The one existing check is
  `tools/build_check.py`, which fails a BUILD-LOG marked sent whose Ship block has no
  `reader_review` report; work without a BUILD-LOG is not checked. Wider wiring (a send-time gate on
  attachments, a closing step in the deck and cover-letter skills) is a parked decision.
- **Plan hardening** (`.claude/workflows/plan-hardening.js`) is parked and is not part of this layer.

## Known drift

`.claude/skills/verify/SKILL.md`, section "The sandbox", still describes codex running under
`--sandbox workspace-write`. The code replaced that with the seatbelt profile above, with the
Codex and Grok sandboxes disabled inside it. The reasoning in that section (do not hand an external model
an unrestricted shell; run environment probes in the real shell) still holds.

## Connections

- `framework/verification-umbrella.md`: why self-review cannot catch these defects, and the tier
  ladder this layer tops.
- `framework/review-findings-protocol.md`: what happens between a finding arriving and anything
  being changed.
- `.claude/skills/verify/SKILL.md`: the procedure, including what to feed a run and how to end a
  blocked push.
- `framework/interview-prep-discipline.md`: the live-transcript checks listed above as not built.
