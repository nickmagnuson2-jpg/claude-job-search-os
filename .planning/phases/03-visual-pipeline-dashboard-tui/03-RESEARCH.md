# Phase 3: Visual Pipeline Dashboard (TUI) - Research

**Researched:** 2026-04-09
**Domain:** Terminal UI (TUI) with Python Textual framework
**Confidence:** HIGH

## Summary

This phase builds a read-only terminal dashboard that visualizes `data/job-pipeline.md` and `data/job-todos.md` as an interactive, grouped-by-stage table with staleness highlighting and conversion metrics. The existing `tools/pipe_read.py` already handles 90% of the data parsing and staleness computation -- the dashboard is primarily a presentation layer on top of proven logic.

Textual 8.2.3 is the clear choice: it provides DataTable, Collapsible, Header, Footer, and a CSS-like styling system purpose-built for this type of dashboard. The framework is stable (version 8.x, actively maintained), runs on Python 3.14 (the version on this machine), and installs cleanly via pip. Rich (which Textual depends on) handles per-cell text styling for staleness colors.

**Primary recommendation:** Use Textual 8.2.3 with DataTable per stage group inside Collapsible containers. Reuse `pipe_read.py`'s `parse_pipeline()` function directly. Style stale rows via Rich `Text` objects with colored styles passed as cell values.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Full table view -- all columns (Company, Role, Stage, Last Updated, Days Stale, Next Action) with rows grouped by collapsible stage sections and counts per stage.
- **D-02:** Header bar contains summary stats (Active count, Interview count, Stale count) and conversion funnel. Full-width table below. No sidebar.
- **D-03:** Rows grouped by stage (Interviewing, Applied, Researching, etc.) with collapsible sections and entry counts per group.
- **D-04:** View-only dashboard. No data mutations. Actions stay in Claude Code skills.
- **D-05:** Keyboard navigation: arrow keys to navigate rows, Tab to switch stage groups, '/' to search by company name, 'q' to quit.
- **D-06:** Static snapshot -- reads pipeline data on launch, displays it. No file-watching.
- **D-07:** Row color coding for staleness: red background for 7+ days stale, yellow for 3-7 days, default for fresh entries. Uses existing per-stage thresholds from pipe_read.py.
- **D-08:** Within each stage group, stale entries sort to the top.
- **D-09:** Header displays stage counts with conversion rates: "24 Applied > 8 Interview (33%) > 2 Offer (25%)".
- **D-10:** Funnel uses all-time totals including archived/terminal entries.

### Claude's Discretion
- Textual widget choices and CSS styling
- Exact key bindings beyond the ones specified above
- How to render collapsible stage sections (TreeView, Collapsible, or custom)
- Footer content (key binding hints, help text)
- Search implementation details (fuzzy vs exact match)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| R3.1 | Terminal dashboard using Python Textual or Rich library | Textual 8.2.3 confirmed available, compatible with Python 3.14, provides all needed widgets |
| R3.2 | Pipeline view: applications grouped by stage with counts | Collapsible widget + DataTable per group; pipe_read.py already computes stage_distribution |
| R3.3 | Staleness highlighting: red 7+ days, yellow 3-7 days | Rich Text objects with colored styles in DataTable cells; pipe_read.py has per-stage thresholds |
| R3.4 | Next actions column showing upcoming tasks per application | Cross-reference job-todos.md by company name fuzzy match against pipeline entries |
| R3.5 | Conversion funnel: Applied to Interview to Offer rates | pipe_read.py provides stage_distribution + archived_count; extend to include terminal entries in denominator |
| R3.6 | Quick filters: by stage, by staleness, by date added | DataTable sort() API + Textual Input widget for search; filter by rebuilding visible rows |
| R3.7 | Keyboard navigation and search | Textual's built-in key binding system (BINDINGS class attribute) + action methods |
| R3.8 | Reads directly from job-pipeline.md and job-todos.md | pipe_read.py already reads and parses these files; import and call directly |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| textual | 8.2.3 | TUI framework -- App, DataTable, Collapsible, Header, Footer | Only serious Python TUI framework with widget system and CSS [VERIFIED: PyPI registry] |
| rich | 14.3.3 | Text styling within DataTable cells (staleness colors) | Required dependency of Textual, also used directly for Text objects [VERIFIED: PyPI registry] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| (none) | - | - | All other dependencies are part of Textual's install |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Textual (full TUI) | Rich only (static render) | Rich can print formatted tables but has no interactivity, no keyboard nav, no scrolling. Does not satisfy R3.6/R3.7. |
| Textual | curses / blessed | Much lower level, no widget system, significant hand-rolling needed. |

**Installation:**
```bash
pip3 install textual>=8.0.0
```

**Version verification:** Textual 8.2.3 confirmed as latest on PyPI (2026-04-09). Requires Python >=3.9, <4.0. Rich >=14.2.0 pulled in as dependency. [VERIFIED: PyPI API]

## Architecture Patterns

### Recommended Project Structure
```
tools/dashboard/
    __init__.py          # Empty
    app.py               # Textual App class, entry point
    app.tcss             # Textual CSS stylesheet
    pipeline_data.py     # Data loading: calls pipe_read.parse_pipeline(), reads todos, builds view model
```

### Pattern 1: Reuse pipe_read.py as a Library
**What:** Import and call `pipe_read.parse_pipeline()` directly rather than re-parsing the markdown file.
**When to use:** Always -- this is the primary data source.
**Example:**
```python
# Source: tools/pipe_read.py (existing codebase)
import sys
from pathlib import Path
from datetime import date

# Add tools/ to path so we can import pipe_read
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipe_read import parse_pipeline, read_file, TERMINAL_STAGES

repo_root = Path(__file__).resolve().parent.parent.parent
content = read_file(repo_root / "data" / "job-pipeline.md")
data = parse_pipeline(content, date.today())
# data["active_entries"], data["stage_distribution"], data["metrics"]
```

### Pattern 2: Collapsible + DataTable Per Stage Group
**What:** One Collapsible widget per pipeline stage, each containing a DataTable with that stage's entries.
**When to use:** This satisfies D-01, D-03 (collapsible stage sections with counts).
**Example:**
```python
# Source: Textual docs - Collapsible and DataTable widgets [CITED: textual.textualize.io]
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Collapsible, DataTable, Static
from rich.text import Text

class PipelineDashboard(App):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("/", "search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.funnel_text, id="funnel")  # Conversion funnel bar
        for stage, entries in self.grouped_entries.items():
            title = f"{stage} ({len(entries)})"
            with Collapsible(title=title, collapsed=False):
                table = DataTable(id=f"table-{stage}")
                yield table
        yield Footer()
```

### Pattern 3: Staleness Row Coloring via Rich Text
**What:** Use Rich `Text` objects with colored styles for each cell in stale rows. DataTable cells accept any Rich renderable.
**When to use:** Satisfies D-07 (red/yellow row coloring).
**Example:**
```python
# Source: Textual DataTable docs [CITED: textual.textualize.io/widgets/data_table/]
from rich.text import Text

def styled_cell(value: str, days_stale: int | None, threshold: int) -> Text:
    """Return a Rich Text object with staleness-appropriate styling."""
    if days_stale is not None and days_stale >= threshold:
        # Red for stale (over threshold)
        return Text(str(value), style="white on dark_red")
    elif days_stale is not None and days_stale >= threshold - 2:
        # Yellow for warning zone (within 2 days of threshold)
        return Text(str(value), style="black on yellow")
    return Text(str(value))
```

Note: The exact threshold for "yellow" needs alignment with D-07. The context says "red if 7+ days, yellow if 3-7 days" but pipe_read.py uses per-stage thresholds. Recommendation: use per-stage thresholds for red (entry.stale == True), and a secondary band (threshold - 2 to threshold) for yellow warning.

### Pattern 4: Conversion Funnel Calculation
**What:** Count all-time entries per stage (including terminal) for honest conversion rates.
**When to use:** Satisfies D-09, D-10.
**Example:**
```python
# pipe_read.py currently skips terminal entries. Dashboard needs to also count them.
# Extend or re-parse to include archived count per original stage.
# Simplest: count active per stage + use archived_count from metrics.
# For accurate funnel: need to know which stage archived entries were in.

def build_funnel(active_entries: list, all_entries_including_terminal: list) -> str:
    """Build funnel string like '24 Applied > 8 Interview (33%) > 2 Offer (25%)'"""
    stage_order = ["Researching", "Applied", "Phone Screen", "Screening",
                   "Interview", "Interviewing", "Offer"]
    counts = {}
    for e in all_entries_including_terminal:
        s = e.get("stage", "")
        counts[s] = counts.get(s, 0) + 1

    parts = []
    prev_count = None
    for stage in stage_order:
        c = counts.get(stage, 0)
        if c == 0 and prev_count is None:
            continue
        if prev_count and prev_count > 0:
            rate = f" ({c * 100 // prev_count}%)"
        else:
            rate = ""
        parts.append(f"{c} {stage}{rate}")
        prev_count = c
    return " > ".join(parts)
```

### Pattern 5: Todo Cross-Reference for Next Action
**What:** Parse job-todos.md and match company names to pipeline entries for the Next Action column.
**When to use:** Satisfies R3.4.
**Example:**
```python
def load_todos(repo_root: Path) -> dict[str, list[str]]:
    """Return {company_lower: [task1, task2]} from job-todos.md."""
    content = (repo_root / "data" / "job-todos.md").read_text(encoding="utf-8")
    todos_by_company: dict[str, list[str]] = {}
    for line in content.splitlines():
        if not line.startswith("|") or "---" in line or "Task" in line:
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4:
            continue
        task = cols[0]
        status = cols[3] if len(cols) > 3 else ""
        if status in ("Done", "Withdrawn"):
            continue
        # Match company names mentioned in task text
        for company in pipeline_companies:
            if company.lower() in task.lower():
                todos_by_company.setdefault(company.lower(), []).append(task)
    return todos_by_company
```

### Anti-Patterns to Avoid
- **Re-parsing pipeline markdown from scratch:** pipe_read.py already handles edge cases (separator rows, terminal stages, section headers). Import it.
- **Mutating data from the dashboard:** D-04 is firm -- view-only. Never import pipe_write.py or todo_write.py in the dashboard.
- **Using a single giant DataTable for all entries:** Breaks the collapsible-by-stage requirement (D-03). Use one DataTable per stage group.
- **File watching / polling:** D-06 says static snapshot. Read once on launch. No watchdog, no timers.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pipeline markdown parsing | Custom regex parser | `pipe_read.parse_pipeline()` | Already handles 8 columns, section headers, terminal stages, separator row noise |
| Staleness calculation | Custom date math | `pipe_read.py` STAGE_THRESHOLDS + stale flag | Per-stage thresholds already defined and tested |
| Terminal UI framework | curses/blessed wrapper | Textual App + widgets | CSS styling, widget composition, key bindings all built-in |
| Row styling | Manual ANSI escape codes | Rich Text objects | Textual DataTable natively accepts Rich renderables |
| Key binding system | Raw keyboard input handling | Textual BINDINGS + action methods | Handles focus, key propagation, footer display automatically |

**Key insight:** The dashboard is 80% presentation layer. All the hard logic (parsing, staleness, thresholds, terminal stage detection) already exists in pipe_read.py.

## Common Pitfalls

### Pitfall 1: Collapsible + DataTable Focus Conflict
**What goes wrong:** When DataTable is inside Collapsible, keyboard focus can get trapped or skip between containers unexpectedly.
**Why it happens:** Both Collapsible and DataTable are focusable widgets. Tab order may not behave intuitively.
**How to avoid:** Set explicit `can_focus` properties. Use Textual's `focus_next()` / `focus_previous()` to control Tab behavior. Test keyboard navigation early. [ASSUMED]
**Warning signs:** Arrow keys stop working, Tab doesn't move between stage groups.

### Pitfall 2: DataTable Doesn't Support Row-Level Background CSS
**What goes wrong:** You try to apply CSS classes to individual rows for staleness coloring, but DataTable rows are not individual widgets.
**Why it happens:** DataTable renders cells as a virtual canvas, not as discrete widget elements. CSS classes can't target individual rows.
**How to avoid:** Use Rich `Text` objects with inline styles for each cell value. Apply the staleness color style to every cell in the row. [VERIFIED: textual.textualize.io/widgets/data_table/]
**Warning signs:** Staleness colors don't appear or only affect one cell.

### Pitfall 3: pipe_read.py Column Mapping Inconsistency
**What goes wrong:** pipe_read.py and pipeline_staleness.py have slightly different column mappings. pipe_read.py maps column 3 as "Date Updated" and column 4 as "Next Action". pipeline_staleness.py maps column 3 as "Date Added" and column 4 as "Date Updated".
**Why it happens:** The scripts were written independently with different assumptions about the pipeline table format.
**How to avoid:** Use pipe_read.py's mapping (it matches the actual pipeline header: Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL). Do not use pipeline_staleness.py's parser. [VERIFIED: tools/pipe_read.py lines 95-106 vs tools/pipeline_staleness.py lines 79-85]
**Warning signs:** Dates appearing in wrong columns, staleness calculations off.

### Pitfall 4: Funnel Denominator Requires Re-Parsing Terminal Entries
**What goes wrong:** pipe_read.py skips terminal entries (archived_count is just a number, no per-stage breakdown). D-10 requires all-time totals per stage for conversion rates.
**Why it happens:** pipe_read.py was designed for attention-flagging, not funnel analysis.
**How to avoid:** Extend the dashboard's data loader to also parse terminal entries and record their stage. Or add a flag to pipe_read.py to include terminal entries. The simplest approach: write a lightweight re-parser in pipeline_data.py that counts terminal entries by stage, since pipe_read.py already handles the column mapping we can reuse the same approach. [VERIFIED: pipe_read.py lines 112-114]
**Warning signs:** Conversion rates look inflated (denominator too small).

### Pitfall 5: PYTHONIOENCODING Not Needed for Direct Import
**What goes wrong:** Developer adds PYTHONIOENCODING=utf-8 prefix to the dashboard launch command out of habit.
**Why it happens:** Other tools in this project require it because they output JSON to stdout. The dashboard is a TUI app, not a CLI tool.
**How to avoid:** The dashboard uses Textual's rendering, not stdout. Launch as `python3 tools/dashboard/app.py`. PYTHONIOENCODING is irrelevant. [VERIFIED: CLAUDE.md tools section]
**Warning signs:** N/A -- it won't break anything, just unnecessary.

### Pitfall 6: PEP 668 Blocks Global pip install on macOS
**What goes wrong:** `pip3 install textual` fails with "externally-managed-environment" error on macOS with Python 3.14.
**Why it happens:** macOS system Python uses PEP 668 to prevent global installs.
**How to avoid:** Use `pip3 install --user textual` or create a venv. The project's existing requirements.txt suggests pip install works (maybe with --user). Check the project's install pattern. [VERIFIED: pip3 install attempt returned PEP 668 error]
**Warning signs:** "externally-managed-environment" error.

## Code Examples

### Complete App Skeleton
```python
# Source: Textual docs + project patterns [CITED: textual.textualize.io/tutorial/]
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Collapsible, DataTable, Input
from textual.containers import VerticalScroll
from rich.text import Text

class PipelineDashboard(App):
    """Job pipeline dashboard -- read-only view."""

    CSS_PATH = "app.tcss"
    TITLE = "Job Pipeline"
    SUB_TITLE = "Dashboard"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("/", "toggle_search", "Search"),
        ("s", "sort_staleness", "Sort: Staleness"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="funnel-bar")
        yield Input(placeholder="Search company...", id="search-input")
        with VerticalScroll():
            # Stage groups populated in on_mount
            pass
        yield Footer()

    def on_mount(self) -> None:
        self.load_data()
        self.build_tables()

    def load_data(self) -> None:
        """Load pipeline and todo data."""
        # Import and call pipe_read
        ...

    def build_tables(self) -> None:
        """Create Collapsible + DataTable for each stage group."""
        ...
```

### TCSS Stylesheet Example
```css
/* Source: Textual CSS docs [CITED: textual.textualize.io/guide/CSS/] */
#funnel-bar {
    dock: top;
    height: 1;
    background: $primary-background;
    color: $text;
    text-style: bold;
    padding: 0 2;
}

#search-input {
    dock: top;
    display: none;  /* Toggle visibility with / key */
}

#search-input.visible {
    display: block;
}

Collapsible {
    margin: 0 0;
    padding: 0;
}

DataTable {
    height: auto;
    max-height: 20;
}

DataTable > .datatable--cursor {
    background: $accent;
}
```

### Textual Key Binding + Action Pattern
```python
# Source: Textual docs [CITED: textual.textualize.io/guide/actions/]
def action_toggle_search(self) -> None:
    """Show/hide the search input."""
    search = self.query_one("#search-input", Input)
    search.toggle_class("visible")
    if search.has_class("visible"):
        search.focus()

def on_input_changed(self, event: Input.Changed) -> None:
    """Filter tables based on search input."""
    query = event.value.lower()
    # Rebuild visible rows filtering by company name
    ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Textual 0.x (pre-1.0 API) | Textual 8.x (stable API) | 2024-2025 | API is mature, DataTable sorting built-in, Collapsible stable since 0.37 |
| Rich-only tables (static) | Textual for interactive TUIs | 2023+ | Rich is still for static output; Textual adds interactivity layer |

**Deprecated/outdated:**
- Textual "reactive" CSS loading via `--dev` flag still works but is a dev convenience, not required.
- Old Textual examples using `class Meta` for bindings -- use `BINDINGS` class attribute instead.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Collapsible + DataTable focus interaction works smoothly | Pitfall 1 | May need custom focus handling -- test early |
| A2 | DataTable.sort() works within individual tables in Collapsible containers | Architecture Pattern 2 | If not, implement manual sort before populating |
| A3 | VerticalScroll container handles multiple Collapsible widgets with DataTables inside | Architecture | If layout breaks, may need to flatten or use ListView |

## Open Questions

1. **Staleness yellow band definition**
   - What we know: D-07 says "red if 7+ days, yellow if 3-7 days" but pipe_read.py has per-stage thresholds (Applied=5d, Offer=3d, etc.)
   - What's unclear: Should yellow be a fixed 3-7 day range, or relative to each stage's threshold?
   - Recommendation: Use per-stage thresholds. Red = at or above threshold (stale flag from pipe_read.py). Yellow = within 2 days of threshold. This respects the existing logic while adding the warning band.

2. **Funnel data for terminal entries**
   - What we know: pipe_read.py counts terminal entries but doesn't track their stage. D-10 needs per-stage totals.
   - What's unclear: Whether terminal entries in the markdown preserve their original stage (they do -- "Rejected" and "Withdrawn" are the stage values).
   - Recommendation: For funnel calculation, parse ALL entries (including terminal section) and count by stage. "Withdrawn" and "Rejected" count toward the stage they were in when they exited (which is their current stage value). Simpler than tracking original stage.

3. **PEP 668 install strategy**
   - What we know: pip3 install fails globally on this macOS system.
   - What's unclear: Whether the project uses a venv, --user installs, or --break-system-packages.
   - Recommendation: Add `textual>=8.0.0` to requirements.txt and document `pip3 install --user -r requirements.txt` or venv creation. Check what pattern existing dependencies use.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Everything | Yes | 3.14.3 | -- |
| textual | TUI framework (R3.1) | No (not installed) | 8.2.3 (latest on PyPI) | Must install |
| rich | Cell styling (R3.3) | No (not installed) | 14.3.3 (pulled by textual) | Installed with textual |
| pip3 | Package install | Yes | -- | --user flag or venv needed (PEP 668) |

**Missing dependencies with no fallback:**
- textual must be installed before dashboard can run

**Missing dependencies with fallback:**
- None

## Project Constraints (from CLAUDE.md)

- All Python scripts require `PYTHONIOENCODING=utf-8` prefix (but dashboard is a TUI app, not a CLI stdout tool -- may not need it)
- `python3` only on macOS, never bare `python`
- Data files are markdown tables parsed with regex line-by-line (pipe_read.py follows this pattern)
- Edit tool silently fails on markdown files with long table rows -- irrelevant for view-only dashboard
- No em dashes in any output (dashboard displays data, doesn't generate outreach, but avoid in UI text)
- Scripts should auto-load .env via `_load_dotenv()` (not needed for dashboard -- no API keys)

## Sources

### Primary (HIGH confidence)
- [PyPI: textual 8.2.3](https://pypi.org/project/textual/) - Version, dependencies, Python compatibility verified via PyPI API
- [PyPI: rich 14.3.3](https://pypi.org/project/rich/) - Version verified via PyPI API
- [Textual DataTable docs](https://textual.textualize.io/widgets/data_table/) - API, sorting, cell styling, cursor modes
- [Textual Collapsible docs](https://textual.textualize.io/widgets/collapsible/) - API, events, constructor params
- [Textual Widget Gallery](https://textual.textualize.io/widget_gallery/) - Full widget inventory
- [Textual CSS Guide](https://textual.textualize.io/guide/CSS/) - Styling system, dynamic classes, color properties
- [Textual Header docs](https://textual.textualize.io/widgets/header/) - Title, subtitle, customization
- tools/pipe_read.py (lines 34-46, 73-185) - Existing parser, thresholds, terminal stages
- tools/pipeline_staleness.py (lines 26-33, 56-135) - Alternative parser (different column mapping -- do not use)
- data/job-pipeline.md - Actual data format (8-column markdown table)
- data/job-todos.md - Todo format (5-column: Task, Priority, Due, Status, Notes)

### Secondary (MEDIUM confidence)
- [Textual DataTable conditional formatting discussion](https://github.com/Textualize/textual/discussions/2101) - Community patterns for row coloring

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Textual is the only serious Python TUI framework with widget system; version verified on PyPI
- Architecture: HIGH - DataTable, Collapsible, Header/Footer all confirmed in current API docs; pipe_read.py verified in codebase
- Pitfalls: MEDIUM - Focus interaction between Collapsible and DataTable is assumed based on docs, not tested
- Data integration: HIGH - pipe_read.py source code reviewed, column mapping verified against actual data file

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (Textual 8.x API stable)
