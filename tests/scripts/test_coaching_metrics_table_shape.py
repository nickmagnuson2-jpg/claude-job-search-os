"""W2 guard: every coaching table row has as many cells as its header.

data/workstreams/coaching-ledger-integrity.md, fix W2. The append-only coaching logs are
counted by column (tools/coaching_metrics.py --ledger). A row one cell narrower or wider
than its header moves every later value into the neighbouring column, silently: the file
still renders, and an index-based count returns a plausible wrong number.

The rows that are malformed today are data, and fixing data needs Nick. They are listed in
KNOWN_BAD by a fingerprint of the row text (line numbers move when a log grows), each with a
reason. The live test fails on any mismatched row NOT in the list, and also on a list entry
that no longer matches a mismatched row, so the list can only shrink: fix the row, delete the
entry.
"""
import hashlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import coaching_metrics as cm  # noqa: E402

# (repo-relative file, fingerprint(row)) -> why the row is malformed and left alone.
# Recorded 2026-11-06 from the live files. Line numbers are as of that date, a hint only.
KNOWN_BAD = {
    ("coaching/hypotheses.md", "57cfb6b9a36384d1"):
        "H1b test log, line 190: 7 cells under an 8-cell header. --ledger reports its "
        "verdict as unparsed. Append-only log; a data fix needs Nick.",
    ("coaching/hypotheses.md", "f290dccb7a7a43c9"):
        "H1b test log, line 200: 7 cells under an 8-cell header. --ledger reads it "
        "right-aligned. Append-only log; a data fix needs Nick.",
    ("coaching/hypotheses.md", "202e3f6e692f7ed1"):
        "H4 test log, line 288: 7 cells under an 8-cell header. --ledger reads it "
        "right-aligned. Append-only log; a data fix needs Nick.",
    ("coaching/hypotheses.md", "8444c6715b702d1a"):
        "H4 test log, line 295: 7 cells under an 8-cell header. --ledger reports its "
        "verdict as unparsed. Append-only log; a data fix needs Nick.",
    ("coaching/progress/_summary.md", "ffad56b0e50acb60"):
        "Session Index, line 212: a 10-cell row under the 6-cell header, shaped like a "
        "Per-Dimension Trend row appended to the wrong table. A data fix needs Nick.",
}


def fingerprint(row_text: str) -> str:
    return hashlib.sha256(row_text.strip().encode("utf-8")).hexdigest()[:16]


def scan(root: Path) -> dict:
    """Mismatched rows across root/coaching/**/*.md, keyed (relpath, fingerprint)."""
    files = sorted((root / "coaching").rglob("*.md"))
    found, tables, rows = {}, 0, 0
    for path in files:
        result = cm.table_shape(path.read_text(encoding="utf-8"))
        tables += result["tables"]
        rows += result["rows"]
        rel = path.relative_to(root).as_posix()
        for m in result["mismatched"]:
            found.setdefault((rel, fingerprint(m["text"])), []).append(m)
    return {"files": len(files), "tables": tables, "rows": rows, "found": found}


def check(root: Path, known: dict) -> list[str]:
    """Problems with root's coaching tables against the known-bad list; [] means clean."""
    s = scan(root)
    problems = []
    if s["files"] == 0 or s["tables"] == 0 or s["rows"] == 0:
        problems.append(f"scanned {s['files']} file(s), {s['tables']} table(s) and "
                        f"{s['rows']} row(s) under {root / 'coaching'}: nothing was checked")
    for key, ms in sorted(s["found"].items()):
        # A known-bad entry covers ONE row. A second copy of the same text is new
        # (Grok r1 F3), so the tolerated set cannot grow by duplication.
        for m in (ms if key not in known else ms[1:]):
            problems.append(f"NEW {key[0]}:{m['line']}: {m['cells']} cells, header has "
                            f"{m['header_cells']} (fingerprint {key[1]})")
    for key in sorted(set(known) - set(s["found"])):
        problems.append(f"STALE known-bad entry {key}: no mismatched row has this "
                        "fingerprint any more; delete the entry")
    for key, reason in sorted(known.items()):
        if not reason.strip():
            problems.append(f"known-bad entry {key} has no reason")
    return problems


# --- the live guard ----------------------------------------------------------------

@pytest.mark.live_data
def test_every_coaching_table_row_matches_its_header_or_is_known_bad():
    problems = check(REPO, KNOWN_BAD)
    assert problems == [], "\n".join(problems)


# --- table_shape on fixtures ---------------------------------------------------------

HDR = "| Date | Call | Verdict |"
SEP = "|---|---|---|"


def test_rows_matching_the_header_are_not_reported():
    r = cm.table_shape("\n".join([HDR, SEP, "| 1 | a | b |", "| 2 | c | d |"]))
    assert r == {"tables": 1, "rows": 2, "mismatched": []}


def test_a_narrow_row_is_reported_with_both_counts_and_its_text():
    narrow = "| 2 | c |"
    r = cm.table_shape("\n".join([HDR, SEP, "| 1 | a | b |", narrow]))
    assert r["mismatched"] == [{"line": 4, "cells": 2, "header_cells": 3, "text": narrow}]


def test_a_wide_row_is_reported():
    r = cm.table_shape("\n".join([HDR, SEP, "| 1 | a | b | extra |"]))
    assert [(m["cells"], m["header_cells"]) for m in r["mismatched"]] == [(4, 3)]


def test_an_escaped_pipe_is_cell_content_not_a_separator():
    r = cm.table_shape("\n".join([HDR, SEP, r"| 1 | a \| still a | b |"]))
    assert r["mismatched"] == []


def test_a_row_without_a_trailing_pipe_has_the_same_cells():
    r = cm.table_shape("\n".join([HDR, SEP, "| 1 | a | b"]))
    assert r["mismatched"] == []


def test_line_numbers_are_one_based_file_lines():
    r = cm.table_shape("\n".join(["# Title", "", HDR, SEP, "| x |"]))
    assert r["mismatched"][0]["line"] == 5


def test_each_table_is_measured_against_its_own_header():
    wide_hdr = "| A | B | C | D |"
    content = "\n".join([HDR, SEP, "| 1 | a | b |", "", wide_hdr, "|---|---|---|---|",
                         "| 1 | 2 | 3 | 4 |", "| 1 | 2 | 3 |"])
    r = cm.table_shape(content)
    assert r["tables"] == 2 and r["rows"] == 3
    assert [(m["line"], m["header_cells"]) for m in r["mismatched"]] == [(8, 4)]


def test_a_row_followed_by_a_separator_is_a_data_row_not_a_new_header():
    # GFM: inside a table there is no second header. A narrow row that happens to sit on
    # a rule is still narrow, and the rows after it are still measured against the first
    # header (Grok r1 F1).
    content = "\n".join([HDR, SEP, "| 1 | a | b |", "| 2 | c |", "|---|---|---|",
                         "| 3 | d | e |"])
    r = cm.table_shape(content)
    assert r["tables"] == 1 and r["rows"] == 4
    assert [(m["line"], m["cells"]) for m in r["mismatched"]] == [(4, 2)]


def test_a_new_table_needs_a_non_pipe_line_before_it():
    content = "\n".join([HDR, SEP, "| 1 | a | b |", "", "| A | B |", "|---|---|",
                         "| x | y |"])
    r = cm.table_shape(content)
    assert r["tables"] == 2 and r["mismatched"] == []


def test_pipe_lines_without_a_separator_are_not_a_table():
    r = cm.table_shape("\n".join(["| not | a table |", "| one | two | three |"]))
    assert r == {"tables": 0, "rows": 0, "mismatched": []}


def test_a_prose_line_ends_the_table():
    r = cm.table_shape("\n".join([HDR, SEP, "| 1 | a | b |", "prose", "| lone |"]))
    assert r["rows"] == 1 and r["mismatched"] == []


def test_a_prose_line_directly_above_a_separator_is_not_a_header():
    # Only a "|" line can head a table; prose followed by a rule is not one.
    r = cm.table_shape("\n".join(["prose", "|---|---|", "| 1 | a | b |"]))
    assert r == {"tables": 0, "rows": 0, "mismatched": []}


def test_a_separator_shaped_row_in_the_body_is_measured():
    # Grok r1 F2: a dash-only line in the body is a row, and a short one is mismatched.
    r = cm.table_shape("\n".join([HDR, SEP, "| - | - |", "|---|---|---|", "| 1 | a | b |"]))
    assert r["rows"] == 3
    assert [(m["line"], m["cells"]) for m in r["mismatched"]] == [(3, 2)]


def test_the_header_separator_is_not_a_data_row():
    r = cm.table_shape("\n".join([HDR, "|---|", "| 1 | a | b |"]))
    assert r == {"tables": 1, "rows": 1, "mismatched": []}


# --- check() on a fixture repo -------------------------------------------------------

def _repo(tmp_path, files: dict) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


BAD_ROW = "| 2 | c |"
TABLE = "\n".join([HDR, SEP, "| 1 | a | b |"]) + "\n"


def test_check_is_clean_when_every_row_matches(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE})
    assert check(root, {}) == []


def test_check_fails_on_a_new_mismatched_row(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE + BAD_ROW + "\n"})
    problems = check(root, {})
    assert len(problems) == 1 and problems[0].startswith("NEW coaching/log.md:4:")


def test_check_accepts_a_listed_known_bad_row(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE + BAD_ROW + "\n"})
    assert check(root, {("coaching/log.md", fingerprint(BAD_ROW)): "data, needs Nick"}) == []


def test_check_keys_known_bad_by_file_not_only_by_row_text(tmp_path):
    root = _repo(tmp_path, {"coaching/a.md": TABLE + BAD_ROW + "\n",
                            "coaching/b.md": TABLE + BAD_ROW + "\n"})
    problems = check(root, {("coaching/a.md", fingerprint(BAD_ROW)): "listed"})
    assert len(problems) == 1 and problems[0].startswith("NEW coaching/b.md:")


def test_check_scans_nested_folders(tmp_path):
    root = _repo(tmp_path, {"coaching/progress/deep/log.md": TABLE + BAD_ROW + "\n"})
    assert check(root, {})[0].startswith("NEW coaching/progress/deep/log.md:4:")


def test_check_fails_on_a_stale_known_bad_entry(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE})
    problems = check(root, {("coaching/log.md", "0" * 16): "was fixed"})
    assert len(problems) == 1 and problems[0].startswith("STALE")


def test_check_fails_on_a_known_bad_entry_without_a_reason(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE + BAD_ROW + "\n"})
    problems = check(root, {("coaching/log.md", fingerprint(BAD_ROW)): "  "})
    assert problems == [f"known-bad entry ('coaching/log.md', '{fingerprint(BAD_ROW)}') "
                        "has no reason"]


def test_check_counts_a_second_copy_of_a_known_bad_row_as_new(tmp_path):
    root = _repo(tmp_path, {"coaching/log.md": TABLE + BAD_ROW + "\n" + BAD_ROW + "\n"})
    problems = check(root, {("coaching/log.md", fingerprint(BAD_ROW)): "listed once"})
    assert len(problems) == 1 and problems[0].startswith("NEW coaching/log.md:5:")


def test_check_refuses_to_pass_when_tables_have_no_data_rows(tmp_path):
    root = _repo(tmp_path, {"coaching/stub.md": HDR + "\n" + SEP + "\n"})
    problems = check(root, {})
    assert len(problems) == 1 and "0 row(s)" in problems[0]


def test_check_refuses_to_pass_when_nothing_was_scanned(tmp_path):
    assert check(tmp_path, {}) != []
    root = _repo(tmp_path, {"coaching/prose.md": "no tables here\n"})
    assert check(root, {}) != []


def test_known_bad_list_has_a_reason_for_every_entry():
    assert all(reason.strip() for reason in KNOWN_BAD.values())
