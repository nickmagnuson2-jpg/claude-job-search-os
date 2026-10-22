"""personal_todo_write: row formatting.

The writer had no tests until 2026-10-21, when it was found not to escape "|" in
cell values, the cause of the 2026-07 job-todos.md column drift that todo_write had
already fixed for itself. These pin the fix without touching a real vault.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import md_table  # noqa: E402
import personal_todo_write as ptw  # noqa: E402


def test_a_pipe_in_any_active_cell_cannot_add_columns():
    row = ptw.fmt_active("call a|b", "Low", "—", "Pending", "x | y")
    assert md_table.parse_cols(row) == ["call a/b", "Low", "—", "Pending", "x / y"]


def test_a_pipe_in_any_done_cell_cannot_add_columns():
    row = ptw.fmt_done("pay | file", "2026-10-21", "n|m")
    assert md_table.parse_cols(row) == ["pay / file", "2026-10-21", "n/m"]


def test_safe_cell_leaves_empty_and_plain_values_alone():
    assert md_table.safe_cell("") == ""
    assert md_table.safe_cell(None) is None
    assert md_table.safe_cell("plain") == "plain"
