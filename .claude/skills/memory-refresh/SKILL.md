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

Both signals only cover memory files that have opted into the frontmatter schema (`occurrences:` key present) — the pre-2026-07-08 corpus was hand-swept once (see `memory/promotion-backlog-2026-07.md`) and deliberately not backfilled. This skill only prevents the *next* accumulation, not a substitute for that one-time pass.

## Instructions

### Step 1: Run the detector

```bash
python3 tools/scan_promotion_candidates.py --memory-dir "$HOME/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory" --repo-root . --mode interactive
```

### Step 2: Present findings

Summarize the JSON output conversationally — group by promotion vs. demotion, cite `occurrences`/`reopen_gate` for promotion candidates and `last_cited`/`age_days` for demotion candidates. If both lists are empty, say so plainly and stop — don't manufacture work.

### Step 3: Offer to act

For promotion candidates: ask Nick which ones to promote now. For each he picks, read the memory file in full (Rule/Why/How-to-apply sections), identify the concrete target (a `SKILL.md` step, a `tools/check_*.py` hook, or a `data/principles.md` entry — the file's own `reopen_gate` field usually names it), make the edit, then flip `promoted:` in the memory file's frontmatter to the tier it landed at (`skill` / `hook` / `principle` / `hard-rule`) and archive the memory file per the existing archive convention (`memory/archive-YYYY-MM.md`, source preserved).

For demotion candidates: confirm with Nick before archiving anything — a long `last_cited` gap can mean "genuinely stale" or "just hasn't come up," and only Nick can tell the difference. Archive confirmed ones the same way (append to the dated archive file, remove the index line if one exists, don't delete the source). **Index-line location (post 7-shard restructure, 2026-07-08):** the line almost never lives in `MEMORY.md` directly anymore — MEMORY.md only holds Critical Context now. Check MEMORY.md's Topic Shards router table to find which `memory/index-<topic>.md` shard the entry's topic maps to, and remove it there. Only check `MEMORY.md`'s own Critical Context block if the entry looks like an always-visible fact (employment status, family contact, hard-rule-DUE item).

### Step 4: Report

One-line summary: N promoted (with targets), M archived, K left pending for a future run. Don't re-run the detector after acting — the backlog file updates on the next scheduled/manual run, not live.
