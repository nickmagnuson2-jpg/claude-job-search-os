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

## A survival rate is only evidence when the tool has its own suite

`mutation_check.map_tests` selects covering tests two ways: by filename, **and by import
reference** — any test file that mentions the module. So a tool with no
`tests/scripts/test_<stem>.py` still comes back with a survival rate, computed from tests
written for something else. That number looks exactly like a real one.

On 2026-09-02 it produced `check_email_via_skill` at 23/23 and `open_draft` at 113/113, and
both were read as "tests that catch nothing" when the truth was "no tests at all". Thirteen
of 106 scored tools were in that state.

Two things now record the distinction rather than leaving it to the reader:

- `mutation_sweep` writes an `own` field on every target and result row.
- `tests/scripts/test_wired_hooks_have_own_suite.py` fails if a tool wired as a hook in
  `settings.json` has no suite named for it. **The sweep structurally cannot see this** — a
  tool that maps to zero test files is skipped by `build_targets`, so a wired hook with no
  tests never enters the baseline at all. It is absent, not failing.

## Coverage can exist and never run

`tools/test_schema_guard.py` was 148 lines of real regression coverage for the 2026-06-08
column-drift incident, sitting in `tools/` — which the suite does not collect and
`map_tests` does not glob. It stopped running in June and nothing noticed, while
`schema_guard.py` reported 18 of 26 mutants surviving.

A repo scan on 2026-09-02 found **129 test functions** across `tools/` and
`tools/career_scanner/` that had never executed. All have been ported into
`tests/scripts/`, and `tests/scripts/test_no_orphaned_test_files.py` blocks the next one.
It catches two shapes, and the second is why a `git mv` is not always the fix:

1. a pytest file outside `tests/` — collectible, but nothing collects it;
2. a `test_*.py` that is a standalone assert script with its own PASS/FAIL counters and a
   `sys.exit()`. pytest reports `no tests ran` on those, so they must be rewritten.

**Count test functions with `^\s*def test_`, not `^def test_`.** The first pass at that
scan anchored at column 0, missed every class-based test, and reported 38 instead of 129.

## Track the number over time

A sweep overwrites the last one, so the corpus-level figure exists only in whatever report
you happened to read. `tools/mutation_trend.py record` appends one dated row per sweep and
`show` prints the series with deltas. Baseline: **33.88% of decisions unprotected**
(3,657 of 10,794 mutants, 2026-09-02). Run `record` after every sweep; it refuses an
unchanged baseline, so a second call is a no-op rather than a fake flat data point.

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

## A stale `.pyc` reported surviving mutants as KILLED (fixed 2026-08-31)

**The most important thing to know about every number this tool produced before 2026-08-31.**

`mutation_check.py` rewrites its target dozens of times per second and spawned pytest **without**
`PYTHONDONTWRITEBYTECODE`. CPython invalidates a cached `.pyc` by `(mtime, size)`, a granularity coarse
enough that a later mutant could execute as an **earlier mutant's bytecode**. The mismatch registers as a
test failure, which the tool recorded as a KILL.

**The direction is what makes it urgent.** False *kills* mean survivors are **under-reported**: a baseline
reads cleaner than the corpus actually is, and a tool that looks hardened may not be. The opposite error
would only cost a redundant test; this one certifies that a suite protects a behaviour it does not, which
is the exact claim this instrument exists to license.

Measured on `ss_route_conversation.py`, mutant `resolve_person::NEGATE_CMP`:

| Condition | Verdict |
|---|---|
| Hand-applied mutation, 3 runs | **SURVIVED 3/3** |
| Tool, warm `__pycache__`, 5 runs (pre-fix) | KILLED 5/5 — *false* |
| Tool, first run after clearing `__pycache__` | SURVIVED — correct |
| Tool, post-fix, warm cache, 5 runs | SURVIVED 5/5 — stable |

Fixed at both subprocess sites (`run_tests` and the `--isolation` pass). `mutation_report.py` prints a
banner above the numbers for any `baseline.jsonl` older than the fix, and the banner disappears on its own
once a post-fix baseline is written — nothing to remember to remove.

**Any "mutation-clean" claim made before 2026-08-31 is unverified**, including ones written into commit
messages. When comparing a new sweep against an old one, every tool whose survivor count **RISES** is work
this bug hid.

## Test selection: substring matching over-selected and blew the cap (fixed 2026-08-31)

`map_tests` chose covering test files with `if stem in text` over the raw source — a bare substring scan,
no word boundary, comments and prose included. For `todo_write.py` that selected 15 files instead of 6; at
541 mutants the run blew the 5-hour cap and recorded `UNAUDITED_TIMEOUT`, so **the tool went unmeasured
entirely**. The worst match was the string `personal_todo_write` — a *different* tool whose name contains
this one — inside a comment listing launchd jobs.

Selection now runs over an AST-derived view of each test file (identifiers, imports, string literals;
docstrings and comments excluded), with three deliberate choices about which way to err:

- **String literals are KEPT** — many tests invoke a tool as a subprocess rather than importing it, and
  dropping literals would un-cover every CLI-invoked tool and convert real kills into survivors.
- **Unparseable files fail OPEN** — a file that will not parse stays selected. Excluding it would drop
  real coverage; including it only costs runtime.
- **Transitive imports are followed** — a test can exercise a target without naming it, by importing
  something that imports it. Of the 66 selections the tightening dropped, 2 had such a path; both are
  recovered.

## One orphan file silently voided every isolation result (fixed 2026-09-01)

A stray `tools/todo_write.py 2.mutation_backup` — a macOS/sync-style " 2" duplicate whose source
`tools/todo_write.py 2` never existed — sat in the tree. `tests/conftest.py` globbed **every**
`*.mutation_backup` and refused to run when any was present, so the `--isolation` subprocess of all
108 tools in a sweep was refused. Every one came back `isolation_unmeasured`.

**Nothing failed and nothing was flagged.** The isolation signal was simply gone for a whole run,
and the only visible symptom was a status word in a field the sweep record does not even copy
(`isolation_refused` is dropped when `mutation_sweep` builds its row, so the row showed
`isolation_refused: None` while the status said otherwise — the contradiction that led to the cause).

Where it came from: `mutation_sweep --targets` runs `mutation_check --list` over all ~110 tools,
each of which writes a backup beside its target. `todo_write.py` is the largest tool, so it had the
widest write window, and a file-sync process duplicated the backup before cleanup removed the
original. The duplicate is not tracked by the restore handler, so it stranded permanently.

Two fixes, both in `tools/conftest_guard.py` (new — the single source both `tests/conftest.py` and
`tools/mutation_check.py` import, so the two can no longer drift):

- **An orphan does not block.** A backup counts as stranded only if its implied source EXISTS. An
  orphan cannot be an in-flight mutation, because `mutation_check` only ever writes a backup beside
  a file it is rewriting. A *live* backup sitting next to an orphan still refuses — the narrowing
  must not invert into a silent pass while the tree really is mutated.
- **The orphan is still reported**, through the terminal reporter rather than `print()`. pytest
  captures stdout from a session fixture, so the first version of that note produced exactly zero
  visible characters in a default run — a warning nobody sees, which is the failure mode the
  hook-tier rule already forbids elsewhere.

**The refusal exit code moved from 3 to 86.** pytest reserves 0–5, and 3 is INTERNALERROR — so a
test file that genuinely blew up was indistinguishable from a refusal and got filed as a benign
`isolation_unmeasured` instead of being surfaced as broken.

**If you copy `tests/conftest.py` into a fixture tree, copy `tools/conftest_guard.py` too.** The
import resolves relative to the copied tree's own root; without it, pytest cannot collect and
`mutation_check` returns an error dict with no isolation keys at all.

## Backups moved OUT of the working tree (2026-09-01)

`~/Documents` is inside iCloud Drive (Desktop & Documents sync is on), and `mutation_check`
rewrites its target dozens of times per second. iCloud responded the way it always does to a
rapidly-changing file: it made conflict copies — `todo_write.py 2.mutation_backup` — inside
`tools/`. One of those, whose source `tools/todo_write.py 2` never existed, cost a 108-tool sweep
its entire isolation signal.

Backups now live in **`~/Library/Caches/claude-mutation-backups/`**, outside every synced tree, so
there is nothing in the working tree for iCloud to duplicate. `MUTATION_BACKUP_DIR` overrides the
location; tests use it to isolate their own store.

Three consequences worth knowing:

- **The filename is the percent-encoded absolute source path** (`/` → `%2F`), not a hash, because
  the guard must recover the source from the backup alone. "Does the source still exist?" is the
  entire orphan rule, and a hash cannot answer it.
- **Scoping is by decoded source, not by where the backup sits.** The repo and every tmp fixture
  tree share one store, so `stranded_backups(root)` filters on the source path. Without that a
  fixture's leftover would make the real repo's suite refuse to run — the same outage, reintroduced
  from the other side.
- **Orphans are pruned on every `mutation_check` startup.** A backup beside its target used to die
  with the tmp tree that held it; a shared store keeps them forever instead. 27 accumulated in one
  afternoon before pruning existed. Pruning only ever removes backups whose source is gone, so it
  cannot touch a live run's only copy.

**Verified end to end, not just by unit tests** (this is the third time the overnight sweep has been
burned, so green tests were not treated as sufficient): a real 2-tool sweep returned
`status=survivors` rather than `isolation_unmeasured` — the isolation pass measures again — and
restored all 9 quiesced launchd jobs; a real run SIGTERMed mid-flight left the target byte-identical
to pristine with zero backups anywhere; and planted SIGKILL-style wreckage (target mutated, backup
stranded) was detected, repaired, and — new — **reported in the banked record**. `mutation_check`
recovers at its own startup, so the sweep's `repaired` field never saw it and the row read as
pristine; `recovered_stranded_file` and `isolation_refused` are now carried into the record too.

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
