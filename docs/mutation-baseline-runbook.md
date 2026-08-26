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
`tests/scripts/test_<name>.py` and no entry in `tools/mutation-allow.json`.
`mutation_check.py` excludes itself by design — a tool that rewrites live source must never
rewrite itself. Prints the counts and writes `targets.json`. Runs no mutations.

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
