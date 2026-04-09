---
phase: 02-browser-automation-for-job-discovery
plan: 03
subsystem: career-scanner-orchestrator
tags: [scanner, cli, yaml-config, skill, orchestrator]
dependency_graph:
  requires: [career_scanner_package, greenhouse_parser, lever_parser, ashby_parser, generic_parser, scorer.py, dedup.py]
  provides: [scanner.py, cli.py, scan-targets.yaml, scan-companies-skill]
  affects: [tools/career_scanner/, data/scan-targets.yaml, .claude/skills/scan-companies/]
tech_stack:
  added: []
  patterns: [yaml-config-driven-scanning, inbox-prepend-pattern, argparse-json-cli]
key_files:
  created:
    - tools/career_scanner/scanner.py
    - tools/career_scanner/cli.py
    - data/scan-targets.yaml
    - .claude/skills/scan-companies/SKILL.md
  modified: []
decisions:
  - Seeded scan-targets.yaml with 8 companies from pipeline (Headway, Outset) and goals-aligned targets (Ramp, Notion, Discord, Spring Health, Cerebral, Lyra Health)
  - Lyra Health set to active=false since it uses a custom careers site requiring generic/Playwright parser verification
  - Used git add -f for data/scan-targets.yaml since data/ is gitignored but config files should be tracked
  - Followed granola_auto_debrief.py inbox write pattern exactly (header detection, insertion point, full-file write)
metrics:
  duration_seconds: 983
  completed: 2026-04-09T14:35:56Z
  tasks_completed: 2
  tasks_total: 2
---

# Phase 02 Plan 03: Scanner Orchestrator & CLI Summary

End-to-end scanner pipeline (YAML config -> ATS fetch -> score -> dedup -> inbox write) with CLI entrypoint and /scan-companies skill definition, seeded with 8 target companies from pipeline and goals.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 13b284f | feat | Scanner orchestrator, CLI, and scan-targets config |
| 88775c3 | feat | /scan-companies skill definition |

## Task Details

### Task 1: Scanner Orchestrator, CLI, and Config

**scanner.py** implements the full pipeline:
- `load_targets(repo_root)` reads active companies from data/scan-targets.yaml via yaml.safe_load
- `fetch_company_roles(target)` dispatches to the correct ATS parser (greenhouse/lever/ashby/generic) based on config, overrides company name, applies role_filters
- `scan_all_targets(repo_root, dry_run)` orchestrates: load targets, fetch all roles with 0.5s rate limiting between companies, score via scorer.py, dedup via dedup.py, sort by score descending, write to inbox
- `format_inbox_entry(roles)` formats scored roles as markdown for inbox
- `write_inbox(repo_root, roles)` follows granola_auto_debrief.py pattern (read existing, find insertion point after header/comments, prepend, full-file write)

**cli.py** provides argparse CLI with `--dry-run` and `--repo-root` flags. Outputs JSON summary to stdout (companies_scanned, total_fetched, new_roles, skipped_dupes, top_roles). Status messages to stderr.

**scan-targets.yaml** seeded with 8 companies:
- From pipeline: Headway (greenhouse), Outset (ashby)
- From goals alignment: Ramp (ashby), Notion (ashby), Discord (greenhouse), Spring Health (greenhouse), Cerebral (greenhouse), Lyra Health (generic, inactive pending verification)
- All entries have role_filters for operations/strategy focus per goals.md

### Task 2: /scan-companies Skill

Skill definition at `.claude/skills/scan-companies/SKILL.md` with:
- Step 0: Profile Guard (verify profile.md and goals.md exist)
- Step 1: Verify scan targets exist and are active
- Step 2: Run CLI with PYTHONIOENCODING=utf-8 prefix
- Step 3: Present results as score-sorted table
- Step 4: Suggest next actions for roles scoring 7+
- Step 5: Target management guide with ATS slug discovery instructions

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] data/ directory is gitignored**
- **Found during:** Task 1 commit
- **Issue:** data/ is in .gitignore (prevents personal data leaking), but scan-targets.yaml is machine config that should be tracked
- **Fix:** Used `git add -f data/scan-targets.yaml` to force-track the config file
- **Files modified:** data/scan-targets.yaml (git tracking)
- **Commit:** 13b284f

**2. [Rule 1 - Bug] python command not found on macOS**
- **Found during:** Task 1 verification
- **Issue:** macOS uses `python3` not `python` as the command
- **Fix:** Used `python3` in verification commands; CLI SKILL.md documents `python3` invocation
- **Files modified:** .claude/skills/scan-companies/SKILL.md
- **Commit:** 88775c3

## Verification Results

- scanner.py imports (scan_all_targets, format_inbox_entry): PASS
- cli.py imports (main): PASS
- scan-targets.yaml parses with yaml.safe_load: PASS (8 companies)
- SKILL.md exists with scan-companies name: PASS

## Self-Check: PASSED

All 4 files found. All 2 commits found.
