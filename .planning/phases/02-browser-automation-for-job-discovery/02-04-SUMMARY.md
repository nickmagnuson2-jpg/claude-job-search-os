---
phase: 02-browser-automation-for-job-discovery
plan: 04
subsystem: n8n-automation
tags: [n8n, subprocess, career-scanner, scheduled-scan, automation]
dependency_graph:
  requires:
    - phase: 02-03
      provides: scanner.py, cli.py, scan-targets.yaml
  provides:
    - n8n wrapper script for scheduled career scanning
  affects: [n8n-workflows, career-scanner]
tech_stack:
  added: []
  patterns: [n8n-subprocess-wrapper, json-stdout-for-n8n]
key_files:
  created:
    - tools/n8n_career_scan.py
  modified: []
decisions:
  - Added TimeoutExpired exception handling for the 300s subprocess timeout (plan template only had timeout param, not the exception catch)
patterns_established:
  - "n8n career scan wrapper: argparse --repo-root, subprocess to cli.py, JSON stdout, stderr for errors"
requirements_completed: [R2.7]
metrics:
  duration_seconds: 1118
  completed: 2026-04-09T14:57:31Z
  tasks_completed: 1
  tasks_total: 2
---

# Phase 02 Plan 04: n8n Automation Wrapper Summary

**n8n Execute Command wrapper for daily scheduled career scanning with 300s timeout and JSON output**

## Performance

- **Duration:** ~19 min
- **Started:** 2026-04-09T14:38:53Z
- **Completed:** 2026-04-09T14:57:31Z
- **Tasks:** 1 of 2 (Task 2 is checkpoint:human-verify)
- **Files created:** 1

## Accomplishments
- Created n8n_career_scan.py wrapper following n8n_dossier_nudge.py pattern
- Subprocess call to career_scanner/cli.py with 300s timeout (T-02-10 mitigation)
- Graceful handling when scan-targets.yaml is missing (exit 0, not error)
- JSON summary forwarded to stdout for n8n consumption

## Task Commits

Each task was committed atomically:

1. **Task 1: Create n8n career scan wrapper script** - `07d34ed` (feat)

**Task 2:** checkpoint:human-verify (pending user verification of end-to-end pipeline)

## Files Created/Modified
- `tools/n8n_career_scan.py` - n8n Execute Command wrapper that runs career scanner CLI via subprocess

## Decisions Made
- Added explicit subprocess.TimeoutExpired exception handler (plan template showed timeout param but not the catch block; n8n_dossier_nudge.py also lacks it, but the 300s timeout makes it important to handle gracefully)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added TimeoutExpired exception handler**
- **Found during:** Task 1 (script creation)
- **Issue:** Plan template included timeout=300 but no handling for subprocess.TimeoutExpired exception. Without a catch, the exception would produce an unhelpful Python traceback instead of a clean error message for n8n.
- **Fix:** Added try/except subprocess.TimeoutExpired with descriptive stderr message and sys.exit(1)
- **Files modified:** tools/n8n_career_scan.py
- **Committed in:** 07d34ed

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Essential for clean error reporting to n8n. No scope creep.

## Issues Encountered
None

## User Setup Required
None - n8n workflow configuration is covered in Task 2 checkpoint verification steps.

## Next Phase Readiness
- Task 2 checkpoint requires user to verify end-to-end scanner pipeline with real company data
- After verification, Phase 2 (browser automation for job discovery) is complete
- All 4 plans delivered: ATS parsers, scoring/dedup, orchestrator/CLI/skill, n8n automation

---
*Phase: 02-browser-automation-for-job-discovery*
*Completed: 2026-04-09 (Task 1 only; Task 2 pending checkpoint)*
