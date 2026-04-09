---
phase: 03-visual-pipeline-dashboard-tui
reviewed: 2026-04-09T12:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - tools/dashboard/pipeline_data.py
  - tools/dashboard/app.py
  - tools/dashboard/app.tcss
  - .claude/skills/dashboard/SKILL.md
findings:
  critical: 0
  warning: 3
  info: 2
  total: 5
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-04-09
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

The pipeline dashboard TUI is a well-structured read-only Textual app with clean separation between the data layer (`pipeline_data.py`) and the UI layer (`app.py`). The code is readable, imports are verified against `pipe_read.py`, and the TCSS styling is straightforward. Three warnings were found: a missing `await` on a coroutine call, a potential crash when `pipeline_data` returns empty content, and redundant parsing logic that duplicates `pipe_read`. Two info items cover minor code quality observations.

## Warnings

### WR-01: Missing `await` on async `_rebuild_tables()` call

**File:** `tools/dashboard/app.py:200`
**Issue:** `action_toggle_search` calls `self._rebuild_tables()` without `await`, but `_rebuild_tables` is defined as `async def` (line 110). In Textual, calling an async method without `await` from a synchronous action handler silently drops the coroutine -- the tables will not rebuild when the user clears the search by pressing `/` a second time.
**Fix:** Either make `action_toggle_search` async and await the call, or use `self.call_later(self._rebuild_tables)`:
```python
async def action_toggle_search(self) -> None:
    """Toggle search input visibility."""
    search = self.query_one("#search-input", Input)
    search.toggle_class("visible")
    if search.has_class("visible"):
        search.focus()
    else:
        search.value = ""
        await self._rebuild_tables()
```

### WR-02: Missing `await` on async `_rebuild_tables()` in `action_sort_staleness`

**File:** `tools/dashboard/app.py:221`
**Issue:** Same pattern as WR-01. `action_sort_staleness` is a sync method calling `self._rebuild_tables()` without `await`. The sort toggle will appear to do nothing.
**Fix:**
```python
async def action_sort_staleness(self) -> None:
    """Toggle sort between stale-first and date-first."""
    self._sort_by_staleness = not self._sort_by_staleness
    await self._rebuild_tables()
```

### WR-03: No error handling if pipeline file is missing or empty

**File:** `tools/dashboard/pipeline_data.py:128-132`
**Issue:** `read_file` returns an empty string when the file is missing. `parse_pipeline("")` is then called, which returns a result dict, but `_parse_all_entries("")` returns an empty list. While this does not crash, the dashboard silently shows "No entries" in the funnel bar with no indication that the data file could not be found. A user launching the dashboard from the wrong directory would get a blank screen with no error message.
**Fix:** Add a check after loading content and surface a diagnostic message:
```python
pipeline_content = read_file(pipeline_path)
if not pipeline_content.strip():
    # Return empty data with a flag so the UI can show a warning
    return {
        "grouped_entries": {},
        "all_entries": [],
        "active_entries": [],
        "metrics": {"total_active": 0, "total_stalled": 0, "archived_count": 0},
        "stage_distribution": {},
        "error": f"Pipeline file not found or empty: {pipeline_path}",
    }
```
Then check for `self.data.get("error")` in `on_mount` and display it via `self.notify()`.

## Info

### IN-01: Redundant table parsing in `_parse_all_entries`

**File:** `tools/dashboard/pipeline_data.py:47-70`
**Issue:** `_parse_all_entries` re-implements markdown table parsing that `pipe_read.parse_pipeline` already does. The only difference is that it includes terminal stages. Consider adding a parameter to `parse_pipeline` (e.g., `include_terminal=True`) to avoid maintaining two parsers that could drift out of sync -- for example, `_parse_all_entries` parses only 5 columns while `pipe_read` parses 8.
**Fix:** Extend `parse_pipeline` with an `include_terminal` flag, or extract a shared row-parser helper.

### IN-02: Duplicate `"---"` skip logic

**File:** `tools/dashboard/pipeline_data.py:59`
**Issue:** Lines 53-54 already skip rows matching `r"\|\s*(Company|---)"`, but line 59 adds another check `if cols[0] == "---"`. The second check is redundant since the regex on line 53 already handles separator rows. Same pattern exists in `_parse_todos` at line 95. Not a bug, but unnecessary clutter.
**Fix:** Remove the `if cols[0] == "---": continue` checks on lines 59 and 95.

---

_Reviewed: 2026-04-09_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
