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
`tests/scripts/test_<name>.py` and no entry in `tools/mutation-allow.json`. Prints the
counts and writes `targets.json`. Runs no mutations.

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

Safe while it runs: reading anything, and editing `data/`, `output/`, `.claude/skills/`,
or docs. The sweep only touches `tools/*.py`.

## Why serial, and why not to "speed it up"

`mutation_check` rewrites its target in place, so two concurrent runs in one tree corrupt
each other. On 2026-08-26 agents hit `isolation_failed` purely from a sibling's stranded
backup, and every suite-green claim in that run became worthless. Backgrounding several
copies of the sweep recreates the exact bug it works around. If parallelism is wanted, the
answer is **one git worktree per runner**, never one tree.

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
