#!/usr/bin/env python3
"""Pipeline dashboard TUI - read-only view of job-pipeline.md"""
import sys
from pathlib import Path

# Add tools/ and tools/dashboard/ to path
_tools_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_tools_dir))
sys.path.insert(0, str(_tools_dir / "dashboard"))

from pipeline_data import load_dashboard_data, get_staleness_level, STAGE_ORDER

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import (
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
)


# Funnel stage order (progression stages only, in pipeline order)
FUNNEL_STAGES = [
    "Researching",
    "Applied",
    "Phone Screen",
    "Screening",
    "Interview",
    "Interviewing",
    "Offer",
]


class PipelineDashboard(App):
    """Terminal dashboard for viewing job pipeline status."""

    CSS_PATH = "app.tcss"
    TITLE = "Job Pipeline"
    SUB_TITLE = "Dashboard"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("slash", "toggle_search", "Search"),
        ("s", "sort_staleness", "Sort: Stale"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sort_by_staleness = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="funnel-bar")
        yield Input(placeholder="Search company...", id="search-input")
        yield VerticalScroll()
        yield Footer()

    async def on_mount(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        self.data = load_dashboard_data(repo_root)
        self._build_funnel_bar()
        await self._rebuild_tables()

    def _build_funnel_bar(self) -> None:
        """Build conversion funnel and summary stats for the header bar."""
        all_entries = self.data["all_entries"]
        metrics = self.data["metrics"]

        # Count entries per stage from all_entries (includes terminal)
        stage_counts: dict[str, int] = {}
        for entry in all_entries:
            stage = entry.get("stage", "")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        # Build funnel string: "24 Applied > 8 Interview (33%) > 2 Offer (25%)"
        funnel_parts = []
        prev_count = None
        for stage in FUNNEL_STAGES:
            count = stage_counts.get(stage, 0)
            if count == 0 and not funnel_parts:
                # Skip leading zeros
                continue
            if prev_count is not None and prev_count > 0 and count <= prev_count:
                pct = round(count / prev_count * 100)
                funnel_parts.append(f"{count} {stage} ({pct}%)")
            else:
                funnel_parts.append(f"{count} {stage}")
            prev_count = count

        funnel_str = " > ".join(funnel_parts) if funnel_parts else "No entries"

        # Count interview-stage entries
        interview_count = sum(
            stage_counts.get(s, 0) for s in ("Interview", "Interviewing", "Phone Screen", "Screening")
        )

        stats = (
            f"Active: {metrics['total_active']} | "
            f"Interviews: {interview_count} | "
            f"Stale: {metrics['total_stalled']}"
        )

        bar = self.query_one("#funnel-bar", Static)
        bar.update(f"{stats} | {funnel_str}")

    async def _rebuild_tables(self, grouped=None) -> None:
        """Rebuild stage tables, optionally with filtered/sorted data."""
        if grouped is None:
            grouped = self.data["grouped_entries"]

        container = self.query_one(VerticalScroll)

        # Remove existing children
        children = list(container.children)
        for child in children:
            await child.remove()

        for stage, entries in grouped.items():
            # Apply sort
            if self._sort_by_staleness:
                sorted_entries = sorted(
                    entries,
                    key=lambda e: (
                        0 if e.get("stale") else 1,
                        -(e.get("days_since_update") or 0),
                    ),
                )
            else:
                sorted_entries = sorted(
                    entries,
                    key=lambda e: e.get("date_updated", ""),
                    reverse=True,
                )

            table = DataTable(
                id=f"table-{stage.lower().replace(' ', '-')}",
                cursor_type="row",
            )
            table.add_columns("Company", "Role", "Date Updated", "Days Stale", "Next Action")

            for entry in sorted_entries:
                level = get_staleness_level(entry)
                row = self._build_row(entry, level)
                table.add_row(*row)

            collapsible = Collapsible(
                table,
                title=f"{stage} ({len(entries)})",
                collapsed=False,
            )
            await container.mount(collapsible)

    def _build_row(self, entry: dict, level: str) -> tuple:
        """Build a styled row tuple for a DataTable."""
        style = ""
        if level == "stale":
            style = "white on dark_red"
        elif level == "warning":
            style = "black on yellow"

        company = Text(entry["company"])
        role = Text(entry["role"])
        date_updated = Text(entry["date_updated"])

        days = entry.get("days_since_update")
        days_text = Text(f"{days}d" if days is not None else "-")

        # Next Action: prefer todo cross-references, fall back to pipeline next_action
        todo_actions = entry.get("todo_actions", [])
        if todo_actions:
            action_str = "; ".join(todo_actions[:2])
            if len(action_str) > 60:
                action_str = action_str[:57] + "..."
        else:
            raw = entry.get("next_action", "")
            if not raw or raw.strip() in ("", "-", "--"):
                action_str = "-"
            else:
                action_str = raw[:60] if len(raw) > 60 else raw
        next_action = Text(action_str)

        if style:
            for cell in (company, role, date_updated, days_text, next_action):
                cell.stylize(style)

        return company, role, date_updated, days_text, next_action

    async def action_toggle_search(self) -> None:
        """Toggle search input visibility."""
        search = self.query_one("#search-input", Input)
        search.toggle_class("visible")
        if search.has_class("visible"):
            search.focus()
        else:
            search.value = ""
            await self._rebuild_tables()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Filter tables by company name as user types."""
        query = event.value.strip().lower()
        if not query:
            await self._rebuild_tables()
            return

        # Filter grouped entries by company name match
        filtered: dict[str, list[dict]] = {}
        for stage, entries in self.data["grouped_entries"].items():
            matching = [e for e in entries if query in e["company"].lower()]
            if matching:
                filtered[stage] = matching

        await self._rebuild_tables(filtered)

    async def action_sort_staleness(self) -> None:
        """Toggle sort between stale-first and date-first."""
        self._sort_by_staleness = not self._sort_by_staleness
        await self._rebuild_tables()


if __name__ == "__main__":
    app = PipelineDashboard()
    app.run()
