---
name: memory-refresh
description: Show what's due for promotion (recurring memory rules not yet skill/hook/principle-wired) or demotion (memory entries unread for 60+ days) — and offer to act on it
argument-hint: [none]
user-invocable: true
allowed-tools: Bash(python3 tools/scan_promotion_candidates.py:*), Read(*), Edit(*), Write(*)
---

# Memory Refresh

Interactive wrapper over `tools/scan_promotion_candidates.py` — the same detector the weekly `com.nickmagnuson.jobsearch.memory-promotion-scan` launchd job runs headlessly. **Both entry points share one script by design** (2026-07-08 decision: no drift between cron and on-demand) — this skill never re-implements the detection logic, only presents it and offers to act.

Two mechanical signals, zero LLM judgment in the detector itself:
- **Promotion candidates**: memory files with `occurrences >= 2` and `promoted: no` in frontmatter — a rule that's recurred at least once since capture but never got wired into a skill/hook/`data/principles.md`.
- **Demotion candidates**: memory files with `last_cited` older than 60 days — stamped automatically by the `memory-last-cited-stamp.js` global hook whenever the file is actually Read, not by Claude's judgment.

Both signals only cover memory files that have opted into the frontmatter schema (`occurrences:` key present). **Updated 2026-08-13:** the corpus WAS backfilled — `tools/backfill_memory_schema.py` stamped 383 files, taking feedback-rule coverage from 4.7% to 100%. (This line previously read "deliberately not backfilled," which was true when written and false after Phase 1.) Read `schema_coverage` in the detector output every run: an empty candidate list against low coverage is an empty scan, not an all-clear.

Two frontmatter values carry caveats the detector cannot infer:
- **`occurrences: 1` from the backfill is a floor, not a count.** `needs_review: true` is what says so. Don't read it as "fired once."
- **`terminal: true`** marks a rule no artifact can enforce (calibration judgments, context-dependent stances). These are suppressed from promotion candidates by design and reported in their own `terminal_rules` bucket with a stated reason. A terminal rule is not a backlog item — it is finished at the behavioral tier.

## Instructions

### Step 1: Run the detector

```bash
python3 tools/scan_promotion_candidates.py --memory-dir "$HOME/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory" --repo-root . --mode interactive
```

### Step 2: Present findings

Summarize the JSON output conversationally — group by promotion vs. demotion, cite `occurrences`/`reopen_gate` for promotion candidates and `last_cited`/`age_days` for demotion candidates. If both lists are empty, say so plainly and stop — don't manufacture work.

### Step 3: Offer to act

For promotion candidates: ask Nick which ones to promote now. For each he picks, read the memory file in full (Rule/Why/How-to-apply sections), identify the concrete target (a `SKILL.md` step, a `tools/check_*.py` hook, or a `data/principles.md` entry — the file's own `reopen_gate` field usually names it), make the edit, then flip `promoted:` in the memory file's frontmatter to the tier it landed at (`skill` / `hook` / `principle` / `hard-rule`).

**Then run Step 3a before archiving anything.** Promotion and archival are separate decisions and the second one is not automatic.

### Step 3a: The CLASS guard — MANDATORY before any archive

**Never archive on the `promoted:` flag alone.** A memory file can have a shipped machinery half AND an un-promoted principle half; `promoted: yes` was earned by the machinery. Archiving on it pulls the portable kernel out of the live tier before anyone extracted it — the loss `data/principles.md` exists to prevent.

For every archive candidate (promotion-side or demotion-side), join it against the sort dataset `output/analysis/061226-memory-sort-results.md`, schema `FILE | CLASS | CANON | ARCHIVE`:

| CLASS | CANON | Action |
|---|---|---|
| `MACHINERY` | `-` | **Archive.** Pure harness-bound machinery; the codebase is now source of truth. |
| `HYBRID` or `PRINCIPLE` | `principles` / `identity` / any non-`-` | **HOLD.** Surface as "hold for principles promotion." The kernel has not been extracted yet. Never auto-archive. |
| `FACT` | `profile` / `identity` | **HOLD.** Route to the identity docs, don't archive. |
| *absent from the dataset* | — | **HOLD.** The dataset classified 330 files on 2026-06-12; the corpus is larger now, so a miss means *unclassified*, never *safe*. Classify it or leave it live. |

Fail-safe direction is HOLD. An un-archived file costs shard bytes; a wrongly-archived hybrid costs the kernel.

**Origin:** 2026-06-15 archive pass — of 9 candidates flagged `yes:canonical`, re-verification by CLASS found **6 were HYBRID/PRINCIPLE**. Only 1 was genuinely archivable. Nick: *"hold the six hybrid ones."* See `memory/feedback_dont_archive_hybrid_before_kernel_promoted.md`.

Archive survivors per the existing convention (`memory/archive-YYYY-MM.md`, source preserved). Per the CLAUDE.md Memory Hygiene rule, before deleting any source file: `grep -rl <entry-slug> .claude/skills/ CLAUDE.md docs/` and repoint or stub every live citation, or the archive creates a recall dead-end.

For demotion candidates: confirm with Nick before archiving anything — a long `last_cited` gap can mean "genuinely stale" or "just hasn't come up," and only Nick can tell the difference. Archive confirmed ones the same way (append to the dated archive file, remove the index line if one exists, don't delete the source). **Index-line location (11-shard router, 2026-08-13):** the line almost never lives in `MEMORY.md` directly anymore — MEMORY.md only holds Critical Context now. Check MEMORY.md's Topic Shards router table to find which `memory/index-<topic>.md` shard the entry's topic maps to, and remove it there. Only check `MEMORY.md`'s own Critical Context block if the entry looks like an always-visible fact (employment status, family contact, hard-rule-DUE item).

### Step 4: Report

One-line summary: N promoted (with targets), M archived, K left pending for a future run. Don't re-run the detector after acting — the backlog file updates on the next scheduled/manual run, not live.
