# Mutation baseline — runbook

Last updated: 2026-08-26

**What it produces:** the survivor map for every auditable tool in `tools/` — the count that
says how much of the test suite actually protects anything. A survivor is a decision that
was changed on purpose with the whole suite still green.

**No LLM, no agents, no token spend.** Pure CPU, deterministic, resumable. That is what
makes it a good unattended job: there is no judgment call for it to get wrong at 3am.

Harness: `tools/mutation_sweep.py` · report: `tools/mutation_report.py` ·
per-tool engine: `tools/mutation_check.py`.
State (target list + results) lands in gitignored `output/analysis/082626-mutation-baseline/`.

---

## Build the target list

```
PYTHONIOENCODING=utf-8 python3 tools/mutation_sweep.py --targets
```

Deterministic selection, not a judgment call: every `tools/*.py` that has a matching
`tests/scripts/test_<name>.py`. Prints the counts and writes `targets.json`. Runs no
mutations.

**`mutation-allow.json` is NOT consulted here** (corrected 2026-08-26). It is keyed per
MUTANT, and `mutation_check.py` honours it at that level, counting an allowlisted mutant
as `allowlisted` rather than as a survivor. Selection used to drop the whole TOOL when any
of its mutants was allowlisted, so justifying one survivor silently removed the tool from
the corpus permanently. It had removed 9, including **`check_public_pii.py`** — the
always-on hook that keeps real names out of this PUBLIC repo. All 47 live entries are
mutant-scoped; not one is a whole-tool key, so the exclusion was serving no case at all.

**Coverage is 76 of 145 tools, not 76 of 76.** The report's "all auditable tools were
measured" is true and easy to misread: 58 tools have no `test_<name>.py` and are invisible
to selection by construction. Auditable means "has a test file", never "is safe".

**Two files self-exclude**, for one reason at two layers: `mutation_check.py` refuses
itself, and `mutation_sweep.py` skips itself because the sweep *runs from* that file, so
targeting it rewrites live source under the running process. Both appear in the
`self_excluded` list with `mutants: -1` rather than vanishing, so `selected` minus
`self_excluded` equals `auditable` and neither can quietly leave the corpus. Measure either
one by hand, when no sweep is in flight:

```
PYTHONIOENCODING=utf-8 python3 tools/mutation_check.py tools/mutation_sweep.py --isolation
```

## Run it (and resume it)

```
PYTHONIOENCODING=utf-8 nohup python3 tools/mutation_sweep.py \
    >> output/analysis/082626-mutation-baseline/sweep.log 2>&1 &
```

Resuming is automatic — banked tools are read from `baseline.jsonl` and skipped, so a kill,
crash, or reboot costs at most the single tool in flight. Just run the same command again.

## Check on it

```
tail -5 output/analysis/082626-mutation-baseline/sweep.log
wc -l < output/analysis/082626-mutation-baseline/baseline.jsonl    # tools banked
pgrep -f tools/mutation_sweep.py || echo "not running"
```

## Stop it safely

Order matters: stop the parent first so it cannot launch another tool, then signal the
child, which restores its target from a handler installed for exactly this.

```
pkill -f tools/mutation_sweep.py
pkill -TERM -f tools/mutation_check.py
```

Then confirm the tree is clean. Both commands should print nothing:

```
git status --short tools/
find . -name '*.mutation_backup' -not -path './.git/*'
```

## The harness measures itself

Closed 2026-08-26. Both driving tools had no test file when they were built, which made
them invisible to the sweep they run — a tool demanding mutation evidence while carrying
none is the hypocrisy this whole instrument exists to detect.

| tool | tests | mutants | survivors | crash-only kills |
|---|---|---|---|---|
| `mutation_sweep.py` | 32 | 49 | **0** | 14 of 49 (29%) |
| `mutation_report.py` | 30 | 55 | **0** | 17 of 55 (31%) |

Both pass `--isolation`, and neither reports an assertion-free or tautological test. The
15 survivors found on the first pass are the reason several assertions look like they are
testing prose: three progress lines and the `SWEEP COMPLETE` marker could all be deleted
with the suite green, and for an unattended 5-hour job the log **is** the entire UI. Two
others were worse — `if args.targets:` forced true turns the bare command into a silent
no-op that measures nothing, and dropping `return run_sweep(...)` hands `sys.exit(None)`,
which is exit 0: a failed overnight run reporting success.

Re-run either after changing them; a survivor here means the harness can break without the
suite noticing, which is the one place that cannot be allowed to go stale.

## Read the result

```
PYTHONIOENCODING=utf-8 python3 tools/mutation_report.py
```

Coverage, totals, per-category survival, and the ranked worst-first work list. It always
states how many of the auditable tools it actually measured and names every one it did not,
so a partial run cannot be misread as a complete one.

---

## While it runs, this repo is not safe for ordinary work

At any instant one `tools/*.py` is **mutated on disk**. Across a full run essentially every
file in `tools/` is transiently broken.

- **Tests: blocked, loudly.** `tests/conftest.py` exits 3 while any `.mutation_backup`
  exists. Built after 2026-08-24, when the same pytest command gave 11 failures and then
  110 passes minutes apart, and one of those failures read as "the PII hook now lets leaks
  through" — it did not. You get a refusal, not a wrong answer.
- **Skills: dangerous, and silent.** `/remember`, `/pipe`, `/act`, `/standup`, `/checkout`
  shell out to `tools/*.py`. Invoking one mid-mutation runs the mutant against real data
  files, with no warning.
- **`git add -A`: do not.** It would stage a mutant and its backup.
- **Editing `tools/`: pointless.** Each target is restored from the runner's in-memory copy.
- **launchd: handled automatically since 2026-09-01.** This was the worst case and the one
  nothing covered. `gmail-fetch` and `gmail-fetch-personal` fire every 900s and
  `granola-auto-debrief` every 3h, all shelling into `tools/*.py`; an 18:00 to 04:00 run
  overlaps roughly 80 fires. `gmail_fetch.py` alone carries 377 mutants, so during its own
  measurement window a fetch job would run mutated mail-handling code against real Gmail.
  The sweep now unloads those jobs for the duration -- see below.

Safe while it runs: reading anything, and editing `data/`, `output/`, `.claude/skills/`,
or docs. The sweep only touches `tools/*.py`.

## The scheduled jobs are taken down for the duration

`tools/job_quiesce.py`. On startup the sweep `launchctl bootout`s every
`com.nickmagnuson.jobsearch.*` job that is currently loaded (never its own), records what
went down in `<state-dir>/.quiesced-jobs.json`, and restores them when it finishes.

**The guard cannot live inside the tools.** The obvious design is a
"refuse if a `.mutation_backup` exists" check at the top of each scheduled tool. Mutating
that check's own `if` is exactly what the sweep does, so the protection has to sit outside
the blast radius. Unloading the jobs does that; a guard in the file does not.

**Restore is the whole risk**, so there are four defences:

1. The marker is written **before** the first bootout, so any crash after it leaves a
   record of the debt.
2. Only jobs that were **actually loaded and actually booted out** are recorded. Restore
   never starts a job you deliberately turned off.
3. A **failed restore keeps the marker**, so the next pass retries. Stuck and loud beats
   clean and wrong.
4. Recovery does not depend on the sweep running again: the next sweep restores a stranded
   marker at startup, and `check_automation_health` (daily 08:00) restores it whenever a
   marker exists with no `mutation_sweep` process behind it. A marker **while the sweep is
   alive** is reported and not warned about -- it is the system working.

SIGTERM and SIGINT are handled explicitly. Neither unwinds a `finally` on its own, and
launchd SIGTERMs a job it wants gone. SIGKILL is the case defence 4 exists for.

**If you ever need to put them back by hand:** `bash tools/launchd/install.sh install`.

## Why serial, and why not to "speed it up"

`mutation_check` rewrites its target in place, so two concurrent runs in one tree corrupt
each other. On 2026-08-26 agents hit `isolation_failed` purely from a sibling's stranded
backup, and every suite-green claim in that run became worthless. Backgrounding several
copies of the sweep recreates the exact bug it works around. If parallelism is wanted, the
answer is **one git worktree per runner**, never one tree.

## The timeout used to poison the rest of the run (fixed 2026-08-26)

`subprocess.run(timeout=)` kills the child with **SIGKILL**, which cannot be caught — so
the SIGTERM/SIGINT/SIGHUP handler below, built for exactly this class of failure, never
covered the sweep's own timeout path. On 2026-08-26 `pipe_write.py` hit the old 45-minute
cap and was left mutated on disk with a stranded backup. `tests/conftest.py` then refused
to run for every later `--isolation` check, and **41 consecutive tools were recorded as
`isolation_failed`** — every one of them a false accusation, later disproven by running
each file alone. The boundary was exact: tools 1-10 clean, tool 11 the timeout, tools
12-53 all "failing".

Three changes, because it was three bugs:

1. `repair_stranded()` runs after **every** tool, restoring a target from its backup and
   printing `REPAIRED`. Recorded in the row, never silent.
2. The cap is **120 minutes**. `pipe_write.py` needs 68 and was never going to fit in 45.
3. `mutation_check` now distinguishes conftest's exit 3 (**"I refused to look"**) from a
   real failure, reporting `isolation_refused` and status `isolation_unmeasured`.
   Unmeasured and failing must not render the same.

A separate second instance the same night, worth its own warning: `test_mutation_check.py`
ran mutation runs against the **live** `tools/vault_paths.py`. During the sweep that was a
nested mutation run colliding with the outer one, and it left vault_paths.py mutated for
two hours **with no `.mutation_backup`** — so every stranded-backup check reported the tree
clean. Those tests now operate on a byte-copy in `tmp_path`. **A tree can be corrupt with
no backup present; absence of a backup is not evidence of a clean tree.**

## Crash-safety (fixed 2026-08-26)

`mutation_check` had no crash handler. `finally` covers a clean exit and SIGINT — which
raises `KeyboardInterrupt` — but **not** SIGTERM, which kills the interpreter outright. A
sweep stopped that way left four live tools mutated on disk, among them `pipe_write.py` with
`if arch_start == -1` inverted, which would have appended a duplicate `## Archived` header to
the real `data/job-pipeline.md` on the next `/pipe` archive. The hazard was written up in
prose hours earlier in the same run, and then fired again — prose is not an enforcement tier.
`arm_restore()` now installs a handler for SIGTERM/SIGINT/SIGHUP with an atexit backstop.

## `weak_kill_count` — repaired 2026-08-26, now worth reading

A weak kill is one where the suite noticed the mutation only because the code **crashed**,
not because a test checked a value. That is much weaker evidence, so this is the field that
separates "a test asserted" from "Python threw."

It was broken until 2026-08-26: it measured pytest's *rendering*. Under `--tb=line` pytest
prefixes `AssertionError:` only when it generates a multi-line explanation, so
`assert 'neg' == 'pos'` classified correctly while `assert 1 == 2` was filed as a crash.
Conditional on the compared type, which made the number incomparable between an int-heavy
tool and a string-heavy one, and is why adding message strings to tests moved it 31 -> 11
with no code change. Commit `a4a07fe` adds pytest's bare-`assert` token to the assertion
kinds; `assert` is a keyword so it can never be an exception name.

Verified before/after on one real tool at the same commit: `blind_view.py` weak 13 -> 8,
with `killed=34` and `survived=10` unchanged. **The fix moves only kill classification,
never survivor counts.**

Read it as: of the mutants that died, how many died to an actual assertion. A tool with few
survivors and mostly weak kills is not well tested — it just crashes readily.

**Still not fixed, and it runs the other way:** a test that launches the tool as a
subprocess and re-raises an unexpected exit as `AssertionError: unexpected exit 1` dresses
a crash as an assertion. That inflates the strong count and cannot be fixed in the
classifier — it is a test-authoring pattern. `check_scanner_examined_something` reports
`weak_kill_count: 0` while genuinely having crash-shaped kills for exactly this reason.
