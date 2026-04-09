---
name: dashboard
description: Launch the pipeline dashboard TUI - visual view of job pipeline grouped by stage with staleness, conversion funnel, and search
argument-hint: [none]
user-invocable: true
allowed-tools: Bash(python3 tools/dashboard/app.py)
---

# /dashboard

Launch the pipeline dashboard TUI.

## Trigger
`/dashboard`

## What It Does
Opens an interactive terminal dashboard showing your job pipeline grouped by stage, with staleness highlighting, conversion funnel, and search.

## How to Run
```bash
python3 tools/dashboard/app.py
```

## Key Bindings
- Arrow keys: navigate rows within a stage group
- Tab: move between stage groups
- /: search by company name
- s: toggle sort (stale-first vs date-first)
- q: quit

## Notes
- Read-only view. Use `/pipe` or `pipe_write.py` to make changes.
- Static snapshot. Relaunch to see updated data.
- Requires `textual` package: `pip3 install --user textual>=0.80.0`
