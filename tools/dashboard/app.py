#!/usr/bin/env python3
"""Pipeline dashboard TUI - read-only view of job-pipeline.md"""
import sys
from pathlib import Path

# Add tools/ and tools/dashboard/ to path
_tools_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_tools_dir))
sys.path.insert(0, str(_tools_dir / "dashboard"))

from pipeline_data import load_dashboard_data, get_staleness_level

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


class PipelineDashboard(App):
    """Terminal dashboard for viewing job pipeline status."""

    CSS_PATH = "app.tcss"
    TITLE = "Job Pipeline"
    SUB_TITLE = "Dashboard"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("slash", "toggle_search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="funnel-bar")
        yield Input(placeholder="Search company...", id="search-input")
        yield VerticalScroll()
        yield Footer()

    def on_mount(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        self.data = load_dashboard_data(repo_root)
        self._build_funnel_bar()
        self._build_stage_tables()

    def _build_funnel_bar(self) -> None:
        metrics = self.data["metrics"]
        bar = self.query_one("#funnel-bar", Static)
        bar.update(
            f"Active: {metrics['total_active']} | "
            f"Stale: {metrics['total_stalled']} | "
            f"Archived: {metrics['archived_count']}"
        )

    def _build_stage_tables(self) -> None:
        container = self.query_one(VerticalScroll)
        grouped = self.data["grouped_entries"]

        for stage, entries in grouped.items():
            table = DataTable(id=f"table-{stage.lower().replace(' ', '-')}")
            table.add_columns("Company", "Role", "Date Updated", "Days Stale", "Next Action")

            for entry in entries:
                level = get_staleness_level(entry)
                row = self._build_row(entry, level)
                table.add_row(*row)

            collapsible = Collapsible(
                table,
                title=f"{stage} ({len(entries)})",
                collapsed=False,
            )
            container.mount(collapsible)

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

    def action_toggle_search(self) -> None:
        search = self.query_one("#search-input", Input)
        search.toggle_class("visible")
        if search.has_class("visible"):
            search.focus()


if __name__ == "__main__":
    app = PipelineDashboard()
    app.run()
