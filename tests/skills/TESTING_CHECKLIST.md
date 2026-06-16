# Skill Integration Testing Checklist

> Template for testing each skill after preprocessing script integration.
> Copy this checklist for each skill being optimized.
> Mark each item with `[x]` when complete.

---

## Skill: `/[skill-name]`

**Script(s) used:** `tools/[script].py`
**Date tested:** YYYY-MM-DD

### Pre-Integration Baseline

- [ ] Run skill before optimization and save output to `tests/baselines/[skill]-before.md`
- [ ] Note approximate number of file reads in the skill (baseline)

### Script-Level Checks

- [ ] `pytest tests/scripts/test_[script].py -v` — all tests pass
- [ ] Script handles missing data files gracefully (no crash, returns empty arrays)
- [ ] Script handles partial data gracefully (missing fields → sensible defaults)
- [ ] Script output JSON is valid and matches documented schema

### Skill-Level Changes

- [ ] `Bash(python tools/[script].py:*)` added to `allowed-tools` frontmatter
- [ ] In-context computation steps replaced with script call + JSON parse
- [ ] Source file reads that are now covered by the script removed from instructions

### Post-Integration Behavior Check

- [ ] Run skill after optimization — output matches pre-optimization baseline
- [ ] Missing data file scenario: skill produces appropriate message (not crash)
- [ ] Stale data scenario: skill surfaces warning (not crash)
- [ ] "No history" scenario: skill produces first-run message

### Regression Check

- [ ] Save post-optimization output to `tests/baselines/[skill]-after.md`
- [ ] Diff `before` vs `after` — no meaningful content differences (formatting diffs OK)

### Token Count Estimate

- [ ] File reads eliminated: N
- [ ] Estimated token savings: ~X tokens per run

---

## Scenarios to Test per Skill

### `/checkout`

1. **Normal run** — todos file has completions today, outreach sent today, pipeline has entries
2. **Empty todos** — no active or completed items
3. **First run** — no daily log file exists yet

### `/weekly-review`

1. **Normal run** — daily log has entries from the past 7+ days
2. **No daily log** — falls back to raw todo file correctly
3. **Mixed week** — some days with log entries, some without

### `/standup`

1. **Normal run** — all files populated
2. **Yesterday checkout missing** — nudge appears at top
3. **Yesterday checkout exists** — no nudge

---

## Known Issues / Notes

> Add any issues encountered during testing here.
