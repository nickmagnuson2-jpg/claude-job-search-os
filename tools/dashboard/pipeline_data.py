#!/usr/bin/env python3
"""
pipeline_data.py - Data loading layer for the pipeline dashboard TUI.

Reads job-pipeline.md and job-todos.md, groups entries by stage,
computes staleness levels, and cross-references todos by company.
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Add tools/ to path so pipe_read can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipe_read import (
    parse_pipeline,
    read_file,
    STAGE_THRESHOLDS,
    TERMINAL_STAGES,
    DEFAULT_THRESHOLD,
)

# Preferred stage display order (most urgent first)
STAGE_ORDER = [
    "Offer",
    "Interviewing",
    "Interview",
    "Phone Screen",
    "Screening",
    "Applied",
    "Researching",
]


def get_staleness_level(entry: dict) -> str:
    """Return staleness level for coloring: 'stale', 'warning', or 'fresh'."""
    if entry.get("stale"):
        return "stale"
    days = entry.get("days_since_update")
    if days is not None:
        threshold = STAGE_THRESHOLDS.get(entry.get("stage", ""), DEFAULT_THRESHOLD)
        if days >= threshold - 2:
            return "warning"
    return "fresh"


def _parse_all_entries(content: str) -> list[dict]:
    """Parse ALL pipeline entries including terminal stages (Withdrawn, Rejected, etc.)."""
    all_entries = []
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        if re.match(r"\|\s*(Company|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0]:
            continue
        if cols[0] == "---":
            continue

        entry = {
            "company": cols[0],
            "role": cols[1] if len(cols) > 1 else "",
            "stage": cols[2] if len(cols) > 2 else "",
            "date_updated": cols[3] if len(cols) > 3 else "",
            "next_action": cols[4] if len(cols) > 4 else "",
        }
        all_entries.append(entry)
    return all_entries


def _parse_todos(content: str, companies: set[str]) -> dict[str, list[str]]:
    """Parse job-todos.md and build company -> pending tasks mapping.

    Args:
        content: Raw text of job-todos.md
        companies: Set of lowercase company names to match against

    Returns:
        Dict mapping lowercase company name to list of task descriptions
    """
    todos_by_company: dict[str, list[str]] = {}
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        # Skip header and separator rows
        if re.match(r"\|\s*(Task|---)", line):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4 or not cols[0]:
            continue
        if cols[0] == "---":
            continue

        task_text = cols[0]
        status = cols[3] if len(cols) > 3 else ""

        # Skip completed or withdrawn tasks
        if status.strip().lower() in ("done", "withdrawn"):
            continue

        # Match task to companies
        task_lower = task_text.lower()
        for company in companies:
            if company in task_lower:
                if company not in todos_by_company:
                    todos_by_company[company] = []
                todos_by_company[company].append(task_text)

    return todos_by_company


def load_dashboard_data(repo_root: Path) -> dict:
    """Load and process all dashboard data.

    Returns dict with keys:
        grouped_entries: dict[str, list[dict]] - stage -> entries, ordered
        all_entries: list[dict] - all entries including terminal
        active_entries: list[dict] - from pipe_read
        metrics: dict - total_active, total_stalled, archived_count
        stage_distribution: dict - stage -> count
    """
    pipeline_path = repo_root / "data" / "job-pipeline.md"
    todos_path = repo_root / "data" / "job-todos.md"

    pipeline_content = read_file(pipeline_path)
    todos_content = read_file(todos_path)

    # Parse active entries via pipe_read
    result = parse_pipeline(pipeline_content, date.today())
    active_entries = result["active_entries"]

    # Parse ALL entries (including terminal) for funnel data
    all_entries = _parse_all_entries(pipeline_content)

    # Build company set for todo matching
    companies = {e["company"].lower() for e in active_entries}

    # Parse todos and cross-reference
    todos_by_company = _parse_todos(todos_content, companies)

    # Enrich entries with todo_actions
    for entry in active_entries:
        company_key = entry["company"].lower()
        entry["todo_actions"] = todos_by_company.get(company_key, [])

    # Group by stage in preferred order
    grouped: dict[str, list[dict]] = {}

    # First pass: collect entries by stage
    stage_buckets: dict[str, list[dict]] = {}
    for entry in active_entries:
        stage = entry["stage"]
        if stage not in stage_buckets:
            stage_buckets[stage] = []
        stage_buckets[stage].append(entry)

    # Order stages: known order first, then any remaining
    ordered_stages = [s for s in STAGE_ORDER if s in stage_buckets]
    remaining = [s for s in stage_buckets if s not in ordered_stages]
    ordered_stages.extend(sorted(remaining))

    # Sort within each group: stale first, then by days_since_update descending
    for stage in ordered_stages:
        entries = stage_buckets[stage]
        entries.sort(
            key=lambda e: (
                0 if e.get("stale") else 1,
                -(e.get("days_since_update") or 0),
            )
        )
        grouped[stage] = entries

    return {
        "grouped_entries": grouped,
        "all_entries": all_entries,
        "active_entries": active_entries,
        "metrics": result["metrics"],
        "stage_distribution": result["stage_distribution"],
    }
