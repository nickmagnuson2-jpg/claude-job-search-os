"""md_table: the shared helpers behind the atomic markdown-table writers.

Until 2026-10-21 table_insert_pos was copied into five writers and no test checked
where a new row lands: a mutant that put every new row at the top of the table, or
returned None, passed all of their suites. These tests pin each branch directly.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import md_table as mt  # noqa: E402


def data_row(line):
    return line.startswith("|") and not mt.is_sep_row(line) and not line.startswith("| Name")


TABLE = [
    "## Contacts",          # 0
    "",                     # 1
    "| Name | Role |",       # 2
    "|---|---|",            # 3
    "| A | x |",            # 4
    "| B | y |",            # 5
    "",                     # 6
    "## Next",              # 7
]


def test_insert_goes_after_the_last_data_row():
    assert mt.table_insert_pos(TABLE, 0, 7, data_row) == 6


def test_insert_goes_after_the_separator_when_there_are_no_data_rows():
    lines = ["## S", "| Name | Role |", "|---|---|", ""]
    assert mt.table_insert_pos(lines, 0, 4, data_row) == 3


def test_insert_goes_after_the_first_pipe_row_when_there_is_no_separator():
    lines = ["## S", "| Name | Role |", "text", ""]
    assert mt.table_insert_pos(lines, 0, 4, data_row) == 2


def test_insert_goes_to_the_section_end_when_there_is_no_table():
    lines = ["## S", "just prose", "more prose"]
    assert mt.table_insert_pos(lines, 0, 3, data_row) == 3


def test_insert_respects_the_section_bounds():
    """A data row outside [start, end) must not move the insertion point."""
    lines = TABLE + ["| Z | outside |"]
    assert mt.table_insert_pos(lines, 0, 7, data_row) == 6


def test_the_callers_data_row_test_decides_what_counts():
    assert mt.table_insert_pos(TABLE, 0, 7, lambda line: False) == 4


def test_find_section_matches_by_regex_and_ends_at_the_next_header():
    assert mt.find_section(TABLE, r"^## contacts") == (0, 7)
    assert mt.find_section(TABLE, r"^## Next") == (7, 8)
    assert mt.find_section(TABLE, r"^## Missing") == (-1, -1)


def test_is_sep_row_and_parse_cols():
    assert mt.is_sep_row("| --- | :-: |")
    assert not mt.is_sep_row("| a | b |")
    assert mt.parse_cols("| a |  b c | |") == ["a", "b c", ""]


def test_write_atomic_creates_the_folder_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "new" / "f.md"
    mt.write_atomic(target, "hello")
    assert target.read_text() == "hello"
    assert list(target.parent.iterdir()) == [target]


def test_read_file_returns_empty_for_a_missing_or_unreadable_file(tmp_path):
    assert mt.read_file(tmp_path / "missing.md") == ""
    assert mt.read_file(tmp_path) == ""
    (tmp_path / "f.md").write_text("x")
    assert mt.read_file(tmp_path / "f.md") == "x"


def test_out_ok_and_out_error_emit_json(capsys):
    mt.out_ok("add", "done", extra=1)
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "action": "add",
                                                   "summary": "done", "extra": 1}
    with pytest.raises(SystemExit) as e:
        mt.out_error("bad", code="dup", hint="h")
    assert e.value.code == 1
    assert json.loads(capsys.readouterr().out) == {"status": "error", "message": "bad",
                                                   "code": "dup", "hint": "h"}


def test_json_argument_parser_usage_error_is_json_on_stdout(capsys):
    """A stock argparse rejection left stdout empty, so callers parsing it got a
    JSONDecodeError instead of the usage message. Subparsers must inherit the class."""
    p = mt.JsonArgumentParser(prog="w.py")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("log").add_argument("--summary", required=True)
    for argv in (["log"], ["log", "--summary", "s", "--bogus"]):
        with pytest.raises(SystemExit) as exc:
            p.parse_args(argv)
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        d = json.loads(out)
        assert d["status"] == "error" and d["code"] == "usage"
        assert "usage:" in d["message"] and "w.py" in d["message"]
        assert "error:" in err and "usage: w.py" in err, "stderr keeps the stock usage text"


def test_json_argument_parser_help_is_not_an_error(capsys):
    """--help exits 0 through argparse's help action, never through error()."""
    p = mt.JsonArgumentParser(prog="w.py")
    with pytest.raises(SystemExit) as exc:
        p.parse_args(["--help"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert out.startswith("usage: w.py") and '"status"' not in out


def test_load_lines_strips_line_endings_and_errors_on_a_missing_file(tmp_path, capsys):
    f = tmp_path / "t.md"
    f.write_text("a\nb\n", encoding="utf-8")
    assert mt.load_lines(f) == ("a\nb\n", ["a", "b"])
    with pytest.raises(SystemExit) as exc:
        mt.load_lines(tmp_path / "missing.md")
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["code"] == "file_not_found"
