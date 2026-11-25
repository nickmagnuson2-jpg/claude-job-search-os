Last updated: YYYY-MM-DD

# Overnight run YYYY-MM-DD: <one-line purpose>

<!--
HOW TO USE. Copy to output/overnight-<MMDDYY>/brief.md, fill every <...>, create
output/overnight-<MMDDYY>/NEEDS-NICK.md, then launch with:

  PYTHONIOENCODING=utf-8 python3 tools/launch_swarm.py output/overnight-<MMDDYY>/brief.md --org <org-session-name> --dry-run

tools/launch_swarm.py refuses to open any window unless this brief passes its checks (the
headings below, the org line matching --org, the backlog before the sections, each
section's Owns / Log file / Done when lines, and NEEDS-NICK.md existing). It also writes
each session's launch prompt, including the logging block, so that text lives only there.

REVIEW BEFORE LAUNCH. The launcher also refuses unless the cross-model ledger holds a Codex
AND a Grok review of the brief's exact current text, with every finding dispositioned
(tools/finding_write.py set <run>.<id>). Run both commands from the repo root after the
last edit; any later edit voids them. They inline the brief between ===BRIEF START=== and
===BRIEF END===, which is how the launcher matches a review to the brief:

  PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py --model codex --target "Plan review of this overnight swarm brief (shape: framework/templates/overnight-brief.md). A finding must show a section that cannot reach its Done-when, a claim false against the named code, a violated constraint, two sections editing one tracked file, or a misstated number. Severity P0/P1/P2. ===BRIEF START===$(cat 'output/overnight-<MMDDYY>/brief.md')===BRIEF END==="

  PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py --model grok --target "Plan review of this overnight swarm brief (shape: framework/templates/overnight-brief.md). A finding must show a section that cannot reach its Done-when, a claim false against the named code, a violated constraint, two sections editing one tracked file, or a misstated number. Severity P0/P1/P2. ===BRIEF START===$(cat 'output/overnight-<MMDDYY>/brief.md')===BRIEF END==="

ADOPTION IS NOT ENFORCED. A swarm launched by hand, without tools/launch_swarm.py, skips
every check. The checks bind only when the launcher is used.

Keep the headings exactly as written; the checker reads them.
-->

Organizer: **<org-session-name>**
Standby org: **<standby-session-name>** (takes over if the organizer runs out of context; named before launch)
Needs Nick: `output/overnight-<MMDDYY>/NEEDS-NICK.md` (exists before the first dispatch; every question for Nick is listed there as it arises)

**Governing test (Nick, 2026-11-18):** "Will this help me get a job?" Each section names which of conversations, applications or speed of a repeatable workflow it moves.

## Ranked backlog

Ranked by value to the search before any section is written: live processes first, then new roles, then tooling. Each row names the evidence for its rank.

| Rank | Item | Moves | Evidence | Section |
|---|---|---|---|---|
| 1 | <item> | <conversations / applications / speed> | <path:line or measured count with denominator> | <S1> |

## Rules checked against this plan

| Rule | What it says | This plan |
|---|---|---|
| <memory rule slug> | <what it says> | <how this plan respects it> |

## Rules for every session

1. **Check in with the org by SendMessage** at start (name + section), at each commit, when blocked, when done, and at least every 30 minutes. Silence for 30+ minutes is chased; 60 is flagged.
2. **Timestamps come from `date`**, never an estimate. Log in dated `##` sections in your section's log file (named per section below).
3. **No sends, no pushes, no applications.** Do not write `data/job-pipeline.md`, `data/scan-targets.yaml`, the company queue or `data/inbox.md`. Drafts only through `/follow-up`, `/cold-outreach`, `/draft-email`, stopping before send.
4. **Code only in a worktree** under `~/Library/Caches/claude-worktrees/`. Mutation `--max-hours 2` unless the section says otherwise. Codex then Grok, serially, fully staged. Ask the org for the suite slot. A section that runs parallel subagents runs at most 20 at once; each agent gets an explicit private scratch path, every other scratchpad path is forbidden to it, and each agent writes a provenance file of the scratch paths it touched. No `git stash`: the stash list is shared across worktrees, so a stash in one session can be popped by another.
5. **Guard-protected files** (`tools/check_*.py`, `.claude/settings.json`, `CLAUDE.md`, anything `check_guard_edit_approval` blocks): stage the change under `output/overnight-<MMDDYY>/<section>-staging/` with INSTALL.md and a sha256 baseline. Do not ask another session to apply it. The org installs onto the section's branch and commits; THEN the builder runs Codex + Grok (`--bar`) on that installed tip, then the suite. Reviews of the staged copy do not count for the push. Any repair after install is a new staged baseline, a new install commit and both reviews again.
6. **Memory tier:** only the sections named for it write it, only after the org says so, only through the writers (`record_fire` / `memory_write`). Everyone else proposes lessons to `output/lessons-<MMDD>/<session>-<ref>.md`.
7. **Questions for Nick go only through `tools/decision_write.py add`** (with `--lean`, `--rests-on`, `--if-nothing`), after checking `data/goals.md`, RULING lines in `data/workstreams/*.md` and the decision queue. The morning page lists them.
8. **Every number states its denominator and how it was obtained.** A number that decides a question for Nick rests on at least 2 independent blind readers; the brief names the second reader. Verify on real data. `/verify` before commit; `/audit-pii` before landing any public-file change. Public repo: placeholders only in tracked files.
9. **Exa: at most $2 per session**, logged per batch.
10. **READY means files and git, not a message**: for the branch tip's tree, a PASS row in `tools/.suite-ledger.jsonl`, a mutation record under `output/analysis/` with 0 unexplained survivors, Codex and Grok rows in the main cross-model ledger at that tree with every finding dispositioned, and an audit-pii row in main's ledger. The org checks each before landing. READY also means a clean `PYTHONIOENCODING=utf-8 python3 tools/land_branch.py --json --suite <branch>` run from the branch's own worktree.
11. **Scope:** only your section. Report anything else to the org; do not start it.
12. **Logging:** every section logs to its named log file, append only (`cat >>`), heading `## YYYY-MM-DD HH:MM by \`date\` | <X>: <what>`, at start, each commit, each review result, block, plan change, done; never more than 30 minutes apart. Log first, then message the org. The org's cron reads the newest `| <X>:` heading and chases a gap over 30 minutes. At its first check-in, each section creates its own check-in cron (two fixed minutes per hour, 30 apart), so the cadence does not depend on remembering.
13. **Check-in format:** every check-in and every reply to an org ping is "(1) changed (2) uncommitted (3) unlogged (4) blocked on". Reply to an org ping in the same turn. Before any AskUserQuestion or long measurement, message the org "waiting on Nick for X" / "measuring X until about HH:MM". Each section runs in its own Terminal window, so its AskUserQuestion reaches Nick. Use it for an approval that must live in that window (a guard install, a memory-tier write), and tell the org first.
14. **Ask why, and is there a better way, before building:** every build plan, design lean or pilot names Why (conversations / applications / speed, with evidence) and Alternatives considered (2+, including services Nick already has) and why the chosen one wins. The org rejects plans without both lines. Send the plan to the org before the first code commit. Every plan also names the existing tool it extends, and why the change is the smallest that keeps the same function.
15. **Next items are add-only:** never rewrite, reorder or delete another entry's text in a workstream file, by hand or by script; add a new dated entry that supersedes it. Never edit a STATE-BLOCK.
16. **Suite slot = a live "go now" from the org only**, granted after `pgrep -fl "suite_record|pytest"` shows nothing running. "Free whenever you're ready" is not a grant. Release it by message when done. The grant goes in a later tool call than the check, after its result is read. The check pattern is `pgrep -fl "suite_record.py run"` alone: a bare `suite_record` matches mutation runs that name the file, and `pytest` matches every mutant a mutation run starts, so with a mutation run live that pattern can never grant.
17. **Review staging:** `git add` every new file before a review (never `git add -N`), run models one after another, and check the `tree` field of your ledger rows; a `tree: null` row does not count. Review rounds: one Codex + one Grok; a third only for a P0. Only a P0 gets a fix; a P1 is parked with a reason unless org or Nick rules otherwise.
18. **Close-out:** when the org calls the close-out, run /lessons-learned in proposal mode to `output/lessons-<MMDD>/<session>-<ref>.md` and report an inventory, one line per piece of work and where it lives. Nothing is removed before every session has answered.

## Sections

<!-- One block per section. The ID is S<n> or L<n>. Every line below is required. -->

### S1: <title> | moves: <conversations / applications / speed>

- **Input:** <files, rulings, with paths>
- **Do:** <steps>
- **Owns:** <every file or path this section writes; no other section writes them>
- **Scratch isolation:** <only if the section runs parallel subagents: the scratch root, one private path per agent, and where each agent's provenance file goes; otherwise 'none'>
- **Log file:** `data/workstreams/<file>.md`
- **Done when:** <the named files that exist, with every listed field filled>

## Org work

- **Before launch:** NEEDS-NICK.md created; the backlog ranked; the standby org named; file ownership checked for overlaps.
- **Before launch and before any irreversible step:** read every peer message queued during the turn; a hold from a peer stops the step.
- **At every handover:** the incoming org checks each item it keeps against the sections' Owns lines and Nick's rulings.
- **During the run:** the suite slot; installs of staged guard changes under Nick's logged approval; the check-in cron.
- **Morning:** `output/overnight-<MMDDYY>/MORNING.md` with READY branches, one push command, the decision-queue count and the lanes' outputs.

## Close-out: run metrics

The org runs this after the last session answers the close-out, and links the output from MORNING.md:

```
PYTHONIOENCODING=utf-8 python3 tools/run_metrics.py --start <window start, ISO with offset> --end <window end> --org <org-session-name> [--org <successor org>] --run-id overnight-<MMDDYY> --out output/overnight-<MMDDYY>/run-metrics.json
```

`run-metrics.json` carries the run fields (`RUN_FIELDS` in `tools/run_metrics.py`): sessions with timings and Nick-minutes, branches with time-to-land, and `failure_candidates` with a catcher. The candidates are a starting list, not a count: validated on the 11-21 run, the catcher matched 6 of 14 hand-coded rows. The org writes the run's failure table from them (failure, who caught it, fix tier).
