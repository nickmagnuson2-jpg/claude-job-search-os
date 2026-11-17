"""Tests for the workstream backlog header check and `--backlog` ranking in
tools/workstream_state.py.

Written before the implementation, against
output/infra-backlog/110726-workstream-backlog-rubric.md (gitignored; sections 1 to 6, with
the section 9 rulings overriding: Next is a fenced ```json next block under the header table,
not a table row).

decision_write.py (resolve_main, Refusal, paths, load_events, open_questions, routing) is not
on this branch yet; it lands with question-queue-1106. Every test here replaces it with a fake
module in sys.modules, so none depends on that branch. The one test that needs the real import
is skipped until the rebase (see the end of the file).

Fixtures use generic placeholder slugs and text only. This repo is public.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import workstream_state as ws  # noqa: E402

TODAY = dt.date(2026, 11, 8)
GOOD_ACTION = "write the parser test and watch it fail"

VALID_ROWS = {
    "Owner": "s1",
    "Value": "V2",
    "Size": "S",
    "Blocker": "none",
    "Files": "`tools/a.py`",
}


def next_block(items) -> str:
    body = items if isinstance(items, str) else json.dumps(items)
    return f"```json next\n{body}\n```"


def ws_text(*, status="ACTIVE", rows=None, drop=(), items=None, block=None,
            headings=(), table=True, after_table="", body="") -> str:
    """One workstream file. rows override VALID_ROWS; drop removes rows."""
    r = dict(VALID_ROWS)
    r.update(rows or {})
    lines = ["# Workstream: placeholder", ""]
    if table:
        lines += ["| Field | Value |", "|---|---|", f"| **Status** | {status} |"]
        lines += [f"| **{k}** | {v} |" for k, v in r.items() if k not in drop]
    else:
        lines += [f"| **Status** | {status} |"]
    lines.append("")
    if after_table:
        lines += [after_table, ""]
    if block is None and items is None:
        items = [{"date": "2026-11-08", "action": GOOD_ACTION}]
    if block is not False:
        lines += [block if isinstance(block, str) else next_block(items), ""]
    lines += ["## Why", "", "Placeholder text."]
    for h in headings:
        lines += ["", f"## {h}", "", "An entry."]
    if body:
        lines += ["", body]
    return "\n".join(lines) + "\n"


@pytest.fixture
def root(tmp_path):
    """A stand-in main checkout with a few files the Files row can name."""
    main = tmp_path / "main"
    for rel in ("tools/a.py", "tools/b.py", "tools/c.py", "tools/d.py", "docs/x.md"):
        p = main / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    (main / "data" / "workstreams").mkdir(parents=True)
    return main


def lookups(questions=None, branches=None):
    questions = questions or {}
    branches = branches or {}
    return {"question_state": lambda q: questions.get(q),
            "branch_state": lambda b: branches.get(b)}


def check(text, root, *, slug="alpha", **kw):
    lk = lookups(kw.pop("questions", None), kw.pop("branches", None))
    return ws.check_workstream(slug, text, today=kw.pop("today", TODAY), root=root, **lk, **kw)


def reasons(text, root, **kw):
    return check(text, root, **kw)["reasons"]


def has(rs, needle):
    return any(needle in r for r in rs)


# ---------------------------------------------------------------- parsing

def test_read_header_returns_rows_of_the_field_value_table():
    h = ws.read_header(ws_text())
    assert h["table"] is True
    assert h["rows"]["Status"] == "ACTIVE"
    assert h["rows"]["Value"] == "V2"
    assert h["rows"]["Files"] == "`tools/a.py`"
    assert h["duplicates"] == []
    assert h["line"]["Status"] == 5


def test_read_header_reports_no_table():
    h = ws.read_header(ws_text(table=False))
    assert h["table"] is False
    assert h["rows"] == {}


def test_read_header_unescapes_a_pipe_inside_a_cell():
    h = ws.read_header(ws_text(rows={"Value": r"V4 \| evidence: docs/x.md"}))
    assert h["rows"]["Value"] == "V4 | evidence: docs/x.md"


def test_next_row_in_a_second_table_is_ignored():
    second = "| Field | Value |\n|---|---|\n| **Size** | L |"
    text = ws_text(drop=("Size",), body=second)
    h = ws.read_header(text)
    assert "Size" not in h["rows"]


def test_table_stops_at_the_first_non_pipe_line():
    text = ws_text(drop=("Owner",), after_table="| **Owner** | s9 |")
    # after_table is separated from the table by a blank line, so it is not a header row
    assert "Owner" not in ws.read_header(text)["rows"]


def test_rows_inside_the_state_block_are_ignored():
    text = ws_text().replace(
        "| Field | Value |",
        f"{ws.BEGIN}\n| Field | Value |\n|---|---|\n| **Status** | TERMINAL |\n{ws.END}\n\n"
        "| Field | Value |", 1)
    h = ws.read_header(text)
    assert h["rows"]["Status"] == "ACTIVE"


def test_duplicate_header_row_is_refused(root):
    text = ws_text().replace("| **Size** | S |", "| **Size** | S |\n| **Size** | M |")
    assert ws.read_header(text)["duplicates"] == ["Size"]
    assert has(reasons(text, root), "duplicate row: Size")


def test_no_table_is_refused_as_no_header_table(root):
    assert reasons(ws_text(table=False), root) == ["no-header-table"]


def test_parse_status_is_unchanged_on_every_live_file():
    """Restraint: the default scan's status and read_header's Status agree on real files."""
    from vault_paths import main_checkout
    registry = main_checkout(ws.REPO_ROOT) / "data" / "workstreams"
    if not registry.is_dir():
        pytest.skip("no workstream registry readable from here")
    files = sorted(p for p in registry.glob("*.md") if not p.name.startswith("."))
    assert files
    for p in files:
        text = p.read_text(encoding="utf-8")
        h = ws.read_header(text)
        if not h["table"]:
            continue
        first = re.match(r"[A-Za-z-]*", h["rows"]["Status"]).group(0).upper() or "ACTIVE"
        assert ws._parse_status(text) == first, p.name


# ---------------------------------------------------------------- Next (json next block)

def test_valid_workstream_has_no_reasons(root):
    assert reasons(ws_text(), root) == []


def test_missing_next_is_refused(root):
    assert has(reasons(ws_text(block=False), root), "missing next block")


def test_next_as_a_table_row_is_refused(root):
    text = ws_text(rows={"Next": f"2026-11-08: {GOOD_ACTION}"})
    assert has(reasons(text, root), "Next is a json next block")


def test_empty_next_list_is_refused(root):
    assert has(reasons(ws_text(items=[]), root), "at least one item")


@pytest.mark.parametrize("body", ['{"date": "2026-11-08", "action": "x"}', '"a string"', "3"])
def test_next_that_is_not_a_list_is_refused(root, body):
    assert has(reasons(ws_text(block=next_block(body)), root), "must be a JSON list")


@pytest.mark.parametrize("body", [
    "[{'date': '2026-11-08', 'action': 'single quotes are not JSON'}]",
    '[{"date": "2026-11-08", "action": "trailing comma is not strict JSON",}]',
    '[{"date": "2026-11-08", "action": "a not a number value here", "n": NaN}]',
    '[{"date": "2026-11-08", "date": "2026-11-07", "action": "duplicate keys refused"}]',
])
def test_next_must_be_strict_json(root, body):
    assert has(reasons(ws_text(block=next_block(body)), root), "next block is not strict JSON")


def test_a_second_next_block_is_refused(root):
    text = ws_text(body=next_block([{"date": "2026-11-08", "action": GOOD_ACTION}]))
    assert has(reasons(text, root), "more than one next block")


def test_next_block_below_the_first_heading_is_refused(root):
    text = ws_text(block=False, body=next_block([{"date": "2026-11-08", "action": GOOD_ACTION}]))
    assert has(reasons(text, root), "between the header table and the first ## heading")


def test_next_block_after_a_probe_line_is_accepted(root):
    text = ws_text(after_table='<!-- PROBE: ["python3", "tools/a.py"] -->')
    assert reasons(text, root) == []


def test_next_block_inside_the_state_block_is_not_read(root):
    inside = f"{ws.BEGIN}\n{next_block([{'date': '2026-11-08', 'action': GOOD_ACTION}])}\n{ws.END}"
    text = ws_text(block=False, after_table=inside)
    assert has(reasons(text, root), "missing next block")


@pytest.mark.parametrize("item", [
    {"date": "2026-11-08"},
    {"action": GOOD_ACTION},
    {"date": "2026-11-08", "action": GOOD_ACTION, "size": "S"},
    "2026-11-08: a plain string item",
])
def test_next_item_must_be_exactly_date_and_action(root, item):
    assert has(reasons(ws_text(items=[item]), root), "item 1")


@pytest.mark.parametrize("date", ["11/08/2026", "2026-11-8", "2026-13-01", "", 20261108])
def test_next_without_a_date_is_refused(root, date):
    rs = reasons(ws_text(items=[{"date": date, "action": GOOD_ACTION}]), root)
    assert has(rs, "item 1: date")


def test_next_dated_in_the_future_is_refused(root):
    rs = reasons(ws_text(items=[{"date": "2026-11-09", "action": GOOD_ACTION}]), root)
    assert has(rs, "after today")


def test_next_dated_today_is_accepted(root):
    assert reasons(ws_text(items=[{"date": "2026-11-08", "action": GOOD_ACTION}]), root) == []


@pytest.mark.parametrize("action", ["TBD", "none", "n/a", "?", "fix it", "   ", "x" * 14, 5])
def test_placeholder_next_actions_are_refused(root, action):
    rs = reasons(ws_text(items=[{"date": "2026-11-08", "action": action}]), root)
    assert has(rs, "item 1: action")


def test_action_of_exactly_15_characters_is_accepted(root):
    assert reasons(ws_text(items=[{"date": "2026-11-08", "action": "a" * 15}]), root) == []


def test_every_item_is_checked_not_only_the_first(root):
    items = [{"date": "2026-11-08", "action": GOOD_ACTION}, {"date": "2026-11-08", "action": "TBD"}]
    assert has(reasons(ws_text(items=items), root), "item 2: action")


def test_next_older_than_newest_dated_section_is_refused(root):
    text = ws_text(items=[{"date": "2026-11-01", "action": GOOD_ACTION}],
                   headings=["2026-10-30 | old", "2026-11-05 10:00 | newer"])
    assert has(reasons(text, root), "older than the newest dated section (2026-11-05)")


def test_staleness_uses_the_newest_item_date(root):
    items = [{"date": "2026-11-01", "action": GOOD_ACTION},
             {"date": "2026-11-05", "action": "a second, newer planned item"}]
    text = ws_text(items=items, headings=["2026-11-05 | newest"])
    assert reasons(text, root) == []


def test_next_same_day_as_newest_section_is_accepted(root):
    text = ws_text(items=[{"date": "2026-11-05", "action": GOOD_ACTION}],
                   headings=["2026-11-05 15:05 PST (by `date`) | entry"])
    assert reasons(text, root) == []


def test_heading_with_date_not_first_does_not_count(root):
    text = ws_text(items=[{"date": "2026-09-01", "action": GOOD_ACTION}],
                   headings=["Fixed 2026-09-14", "Why 2026-10-01"])
    assert reasons(text, root) == []


def test_no_dated_sections_needs_only_a_valid_next(root):
    text = ws_text(items=[{"date": "2020-01-01", "action": GOOD_ACTION}])
    assert reasons(text, root) == []


def test_check_reports_oldest_and_newest_item_dates(root):
    items = [{"date": "2026-11-05", "action": GOOD_ACTION},
             {"date": "2026-11-02", "action": "the older planned item here"}]
    r = check(ws_text(items=items, headings=["2026-11-04 | x"]), root)
    assert r["oldest"] == "2026-11-02"
    assert r["newest"] == "2026-11-05"
    assert r["next"] == items


# ---------------------------------------------------------------- Value / Size

@pytest.mark.parametrize("value", ["v4", "High", "4", "V5", "", "V0", "Medium", "V2 extra"])
def test_value_outside_the_set_is_refused(root, value):
    assert has(reasons(ws_text(rows={"Value": value}), root), "Value")


@pytest.mark.parametrize("value", ["V3", "V4", r"V4 \| evidence:", r"V3 \| evidence:   "])
def test_v3_and_v4_need_an_evidence_pointer(root, value):
    assert has(reasons(ws_text(rows={"Value": value}), root), "needs an evidence pointer")


@pytest.mark.parametrize("value", ["V1", "V2"])
def test_v1_and_v2_need_no_evidence(root, value):
    assert reasons(ws_text(rows={"Value": value}), root) == []


def test_v4_with_evidence_is_accepted_and_reported(root):
    r = check(ws_text(rows={"Value": r"V4 \| evidence: docs/x.md:12"}), root)
    assert r["reasons"] == []
    assert (r["value"], r["evidence"]) == ("V4", "docs/x.md:12")


@pytest.mark.parametrize("size", ["s", "Small", "XL", "", "M L"])
def test_size_outside_the_set_is_refused(root, size):
    assert has(reasons(ws_text(rows={"Size": size}), root), "Size")


FOUR = "`tools/a.py`, `tools/b.py`, `tools/c.py`, `tools/d.py`"


def test_size_s_with_four_files_is_refused(root):
    assert has(reasons(ws_text(rows={"Size": "S", "Files": FOUR}), root), "Size S allows at most 3")


def test_size_s_with_three_files_is_accepted(root):
    files = "`tools/a.py`, `tools/b.py`, `tools/c.py`"
    assert reasons(ws_text(rows={"Size": "S", "Files": files}), root) == []


def test_size_m_with_eleven_files_is_refused(root):
    files = ", ".join(f"`new:tools/n{i}.py`" for i in range(11))
    assert has(reasons(ws_text(rows={"Size": "M", "Files": files}), root), "Size M allows at most 10")


def test_size_m_with_ten_and_l_with_eleven_are_accepted(root):
    ten = ", ".join(f"`new:tools/n{i}.py`" for i in range(10))
    eleven = ten + ", `new:tools/n10.py`"
    assert reasons(ws_text(rows={"Size": "M", "Files": ten}), root) == []
    assert reasons(ws_text(rows={"Size": "L", "Files": eleven}), root) == []


@pytest.mark.parametrize("guarded", ["new:tools/check_placeholder.py", "new:.claude/settings.json"])
def test_guarded_file_forces_size_l(root, guarded):
    assert ws.is_guarded(guarded.removeprefix("new:"))
    rs = reasons(ws_text(rows={"Size": "M", "Files": f"`{guarded}`"}), root)
    assert has(rs, "guarded")
    assert reasons(ws_text(rows={"Size": "L", "Files": f"`{guarded}`"}), root) == []


# ---------------------------------------------------------------- Blocker / Files / Owner

@pytest.mark.parametrize("blocker", [
    "none", "question:Q1", "branch:feature-x", "question:Q1, branch:feature-x",
])
def test_blocker_grammar_valid(root, blocker):
    rs = reasons(ws_text(rows={"Blocker": blocker}), root,
                 questions={"Q1": "open"}, branches={"feature-x": "open"})
    assert rs == []


@pytest.mark.parametrize("blocker", [
    "", "None", "waiting on review", "question:12", "question:q1", "branch:",
    "none, question:Q1", "question:Q1,branch:feature-x", "branch:has space",
])
def test_blocker_grammar_invalid(root, blocker):
    rs = reasons(ws_text(rows={"Blocker": blocker}), root,
                 questions={"Q1": "open"}, branches={"feature-x": "open"})
    assert has(rs, "Blocker")


def test_unknown_question_is_refused(root):
    assert has(reasons(ws_text(rows={"Blocker": "question:Q9"}), root), "Q9 was never allocated")


def test_resolved_question_is_a_stale_blocker(root):
    r = check(ws_text(rows={"Blocker": "question:Q1"}), root, questions={"Q1": "resolved"})
    assert r["reasons"] == []
    assert r["stale_blockers"] == ["question:Q1"]
    assert r["blockers"] == []


def test_unknown_branch_is_refused(root):
    assert has(reasons(ws_text(rows={"Blocker": "branch:gone"}), root), "branch gone does not exist")


def test_merged_branch_is_a_stale_blocker(root):
    r = check(ws_text(rows={"Blocker": "branch:done-x"}), root, branches={"done-x": "merged"})
    assert r["stale_blockers"] == ["branch:done-x"]
    assert r["blockers"] == []


def test_open_blockers_are_reported(root):
    r = check(ws_text(rows={"Blocker": "question:Q1, branch:b1"}), root,
              questions={"Q1": "open"}, branches={"b1": "open"})
    assert r["blockers"] == ["question:Q1", "branch:b1"]


@pytest.mark.parametrize("files", ["`/etc/x.py`", "`../x.py`", "`tools/../x.py`", "`new:/abs.py`",
                                   "`~/x.py`"])
def test_files_absolute_or_dotdot_is_refused(root, files):
    assert has(reasons(ws_text(rows={"Files": files}), root), "Files")


@pytest.mark.parametrize("files", ["", "tools/a.py", "`tools/a.py` `tools/b.py`", "`tools/a.py`,"])
def test_files_grammar_is_refused(root, files):
    assert has(reasons(ws_text(rows={"Files": files}), root), "Files")


def test_files_missing_path_needs_new_prefix(root):
    assert has(reasons(ws_text(rows={"Files": "`tools/zz.py`"}), root), "does not exist")
    assert reasons(ws_text(rows={"Files": "`new:tools/zz.py`"}), root) == []


def test_new_prefix_on_an_existing_path_is_refused(root):
    assert has(reasons(ws_text(rows={"Files": "`new:tools/a.py`"}), root), "already exists")


def test_directory_entry_must_exist_as_a_directory(root):
    assert reasons(ws_text(rows={"Files": "`tools/`"}), root) == []
    assert has(reasons(ws_text(rows={"Files": "`tools/a.py/`"}), root), "not a directory")
    assert has(reasons(ws_text(rows={"Files": "`tools`"}), root), "end it with /")


@pytest.mark.parametrize("row", ["Owner", "Value", "Size", "Blocker", "Files"])
def test_each_required_row_missing_is_refused(root, row):
    assert has(reasons(ws_text(drop=(row,)), root), f"missing row: {row}")


@pytest.mark.parametrize("owner", ["", "two words", "s1!"])
def test_owner_grammar(root, owner):
    assert has(reasons(ws_text(rows={"Owner": owner}), root), "Owner")


# ---------------------------------------------------------------- backlog report

def write(root, slug, text):
    (root / "data" / "workstreams" / f"{slug}.md").write_text(text, encoding="utf-8")


def run_backlog(root, *, questions=None, branches=None, board=None, ceiling=None):
    """ceiling None: the exempt list's own length, so tests about other properties are not
    tripped by the shrink-only ratchet. Ratchet tests pass ceiling explicitly."""
    board = board or {}
    registry = root / "data" / "workstreams"
    if ceiling is None:
        listed = ws.load_exemptions(registry)[1]
        ceiling = len(listed)
    return ws.backlog(registry, today=TODAY, root=root,
                      board_owner=lambda slug: board.get(slug, "UNROUTED: no session holds it"),
                      exempt_ceiling=ceiling, **lookups(questions, branches))


def slugs(report):
    return [i["slug"] for i in report["ranked"]]


def test_free_items_rank_before_blocked(root):
    write(root, "aa-blocked", ws_text(rows={"Value": r"V4 \| evidence: docs/x.md",
                                             "Blocker": "question:Q1"}))
    write(root, "zz-free", ws_text(rows={"Value": "V1"}))
    rep = run_backlog(root, questions={"Q1": "open"})
    assert slugs(rep) == ["zz-free", "aa-blocked"]
    assert [i["group"] for i in rep["ranked"]] == ["free", "blocked"]


def test_stale_blocker_ranks_as_free_and_is_shown(root):
    write(root, "one", ws_text(rows={"Blocker": "branch:old"}))
    rep = run_backlog(root, branches={"old": "merged"})
    assert rep["ranked"][0]["group"] == "free"
    assert rep["ranked"][0]["stale_blockers"] == ["branch:old"]


def test_rank_is_value_desc_then_size_asc_then_next_date_then_slug(root):
    def t(value, size, date):
        return ws_text(rows={"Value": value, "Size": size},
                       items=[{"date": date, "action": GOOD_ACTION}])
    v4 = r"V4 \| evidence: docs/x.md"
    write(root, "e-v1", t("V1", "S", "2026-11-01"))
    write(root, "d-v4-m", t(v4, "M", "2026-11-01"))
    write(root, "c-v4-s-new", t(v4, "S", "2026-11-07"))
    write(root, "b-v4-s-old", t(v4, "S", "2026-11-02"))
    write(root, "a-v4-s-old", t(v4, "S", "2026-11-02"))
    write(root, "f-v4-l", t(v4, "L", "2026-10-01"))
    assert slugs(run_backlog(root)) == [
        "a-v4-s-old", "b-v4-s-old", "c-v4-s-new", "d-v4-m", "f-v4-l", "e-v1"]


def test_rank_uses_the_oldest_item_date(root):
    old_and_new = [{"date": "2026-11-08", "action": GOOD_ACTION},
                   {"date": "2026-10-01", "action": "an old item that must not starve"}]
    write(root, "a-mid", ws_text(items=[{"date": "2026-11-01", "action": GOOD_ACTION}]))
    write(root, "b-old", ws_text(items=old_and_new))
    assert slugs(run_backlog(root)) == ["b-old", "a-mid"]


def test_overlap_reports_shared_paths_for_each_pair(root):
    write(root, "a", ws_text(rows={"Files": "`tools/a.py`, `tools/b.py`"}))
    write(root, "b", ws_text(rows={"Files": "`tools/b.py`, `new:tools/q.py`"}))
    write(root, "c", ws_text(rows={"Files": "`new:tools/q.py`"}))
    write(root, "d", ws_text(rows={"Files": "`docs/x.md`"}))
    ov = run_backlog(root)["overlaps"]
    assert ov == [
        {"a": "a", "b": "b", "shared": ["tools/b.py"], "blocked": False},
        {"a": "b", "b": "c", "shared": ["tools/q.py"], "blocked": False},
    ]


def test_directory_entry_overlaps_files_under_it(root):
    write(root, "a", ws_text(rows={"Files": "`tools/`"}))
    write(root, "b", ws_text(rows={"Files": "`tools/a.py`, `docs/x.md`"}))
    assert run_backlog(root)["overlaps"] == [
        {"a": "a", "b": "b", "shared": ["tools/a.py"], "blocked": False}]


def test_directory_prefix_is_by_segment_not_by_string(root):
    (root / "toolsx").mkdir()
    write(root, "a", ws_text(rows={"Files": "`tools/`"}))
    write(root, "b", ws_text(rows={"Files": "`toolsx/`"}))
    assert run_backlog(root)["overlaps"] == []


def test_overlap_with_a_blocked_item_is_marked(root):
    write(root, "a", ws_text(rows={"Files": "`tools/a.py`"}))
    write(root, "b", ws_text(rows={"Files": "`tools/a.py`", "Blocker": "question:Q1"}))
    assert run_backlog(root, questions={"Q1": "open"})["overlaps"] == [
        {"a": "a", "b": "b", "shared": ["tools/a.py"], "blocked": True}]


def test_terminal_workstreams_are_excluded_and_counted(root):
    write(root, "done", ws_text(status="TERMINAL", block=False, drop=tuple(VALID_ROWS)))
    write(root, "live", ws_text())
    rep = run_backlog(root)
    assert rep["terminal"] == ["done"]
    assert slugs(rep) == ["live"]
    assert rep["invalid"] == []


def test_table_less_terminal_is_still_terminal(root):
    write(root, "done", ws_text(status="TERMINAL", table=False, block=False))
    assert run_backlog(root)["terminal"] == ["done"]


def no_header_status(text):
    """ws_text() without its header Status row (Status is not a required row)."""
    out = text.replace("| **Status** | ACTIVE |\n", "", 1)
    assert out != text
    return out


def test_status_row_below_the_header_does_not_make_it_terminal(root):
    """Codex F2 / Grok F2: a TERMINAL row in a later table is not the workstream's Status."""
    text = no_header_status(ws_text(body="| **Status** | TERMINAL |"))
    assert check(text, root)["terminal"] is False
    assert check(text, root)["reasons"] == []
    write(root, "parent", text)
    rep = run_backlog(root)
    assert (rep["terminal"], slugs(rep)) == ([], ["parent"])


def test_status_row_above_the_header_does_not_override_it(root):
    text = ws_text().replace("# Workstream: placeholder\n",
                             "# Workstream: placeholder\n\n| **Status** | TERMINAL |\n", 1)
    write(root, "parent", text)
    rep = run_backlog(root)
    assert (rep["terminal"], slugs(rep)) == ([], ["parent"])


def test_header_terminal_with_a_date_suffix_is_terminal(root):
    write(root, "done", ws_text(status="TERMINAL (2026-11-06)", block=False,
                                drop=tuple(VALID_ROWS)))
    assert run_backlog(root)["terminal"] == ["done"]


def test_lowercase_header_terminal_is_terminal(root):
    write(root, "done", ws_text(status="terminal", block=False, drop=tuple(VALID_ROWS)))
    assert run_backlog(root)["terminal"] == ["done"]


def test_terminal_header_is_not_checked_for_backlog_rows(root):
    r = check(ws_text(status="TERMINAL", block=False, drop=tuple(VALID_ROWS)), root)
    assert (r["terminal"], r["reasons"]) == (True, [])


def test_invalid_workstreams_are_listed_with_reasons_and_not_ranked(root):
    write(root, "bad", ws_text(drop=("Value",)))
    write(root, "good", ws_text())
    rep = run_backlog(root)
    assert slugs(rep) == ["good"]
    assert rep["invalid"] == [{"slug": "bad", "reasons": ["missing row: Value"], "exempt": False}]


def test_owner_mismatch_with_board_is_reported_not_refused(root):
    write(root, "a", ws_text(rows={"Owner": "s1"}))
    write(root, "b", ws_text(rows={"Owner": "s2"}))
    write(root, "c", ws_text(rows={"Owner": "unheld"}))
    rep = run_backlog(root, board={"a": "s1", "b": "s3"})
    by = {i["slug"]: i for i in rep["ranked"]}
    assert rep["invalid"] == []
    assert (by["a"]["owner_drift"], by["b"]["owner_drift"], by["c"]["owner_drift"]) == (
        False, True, False)
    assert by["b"]["board_owner"] == "s3"


def test_value_distribution_is_reported(root):
    write(root, "a", ws_text(rows={"Value": "V1"}))
    write(root, "b", ws_text(rows={"Value": "V1"}))
    write(root, "c", ws_text(rows={"Value": "V2"}))
    assert run_backlog(root)["value_counts"] == {"V1": 2, "V2": 1, "V3": 0, "V4": 0}


def test_dotfiles_are_not_workstreams(root):
    write(root, ".hidden", ws_text(drop=("Value",)))
    assert run_backlog(root)["invalid"] == []


# ---------------------------------------------------------------- migration guard

CUT = "2026-11-08"


def exempt_file(root, slugs, cutover=CUT, raw=None):
    p = root / "data" / "workstreams" / ws.EXEMPT_FILE
    p.write_text(raw if raw is not None else json.dumps({"cutover": cutover, "exempt": slugs}),
                 encoding="utf-8")
    return p


def test_exempt_file_name_is_not_a_markdown_workstream():
    assert ws.EXEMPT_FILE == "backlog-exempt.json"


def test_missing_exempt_file_means_no_exemptions(root):
    write(root, "fresh", ws_text(drop=("Size",)))
    rep = run_backlog(root)
    assert rep["invalid"][0]["exempt"] is False
    assert has(rep["problems"], "fresh: header is invalid")


def test_load_exemptions_reads_cutover_and_slugs(root):
    exempt_file(root, ["old", "older"], cutover="2026-10-01")
    assert ws.load_exemptions(root / "data" / "workstreams") == (
        dt.date(2026, 10, 1), {"old", "older"})
    assert ws.load_exemptions(root / "nowhere") == (None, set())


@pytest.mark.parametrize("raw", [
    "{not json",
    "[]",
    '{"cutover": "2026-11-08"}',
    '{"exempt": []}',
    '{"cutover": "2026-11-08", "exempt": [], "extra": 1}',
    '{"cutover": "2026-11-08", "exempt": "old"}',
    '{"cutover": "2026-11-08", "exempt": ["old", 3]}',
    '{"cutover": "2026-11-08", "exempt": ["old", "old"]}',
    '{"cutover": "2026-11-08", "exempt": [""]}',
    '{"cutover": "11/08/2026", "exempt": []}',
    '{"cutover": "2026-13-01", "exempt": []}',
    '{"cutover": 20261108, "exempt": []}',
    '{"cutover": "2026-11-08", "cutover": "2026-11-09", "exempt": []}',
])
def test_malformed_exempt_file_is_refused(root, raw):
    exempt_file(root, [], raw=raw)
    with pytest.raises(ws.ExemptFileError):
        ws.load_exemptions(root / "data" / "workstreams")


def test_new_invalid_workstream_is_a_guard_problem(root):
    exempt_file(root, [])
    write(root, "fresh", ws_text(drop=("Size",)))
    assert has(run_backlog(root)["problems"], "fresh")


def test_exempt_invalid_workstream_is_not_a_problem(root):
    exempt_file(root, ["old"])
    write(root, "old", ws_text(drop=("Size",), headings=[f"{CUT} | x"]))
    rep = run_backlog(root)
    assert rep["problems"] == []
    assert rep["invalid"][0]["exempt"] is True


def test_exempt_workstream_with_a_post_cutover_heading_must_fill_rows(root):
    exempt_file(root, ["old"])
    write(root, "old", ws_text(drop=("Size",), headings=["2026-11-09 | new entry"]))
    assert has(run_backlog(root)["problems"], "old: dated section 2026-11-09 is after the cutover")


def test_cutover_is_read_from_the_file(root):
    exempt_file(root, ["old"], cutover="2026-11-01")
    write(root, "old", ws_text(drop=("Size",), headings=["2026-11-05 | entry"]))
    assert has(run_backlog(root)["problems"], "after the cutover (2026-11-01)")
    exempt_file(root, ["old"], cutover="2026-11-05")
    assert run_backlog(root)["problems"] == []


def test_exempt_workstream_that_became_valid_must_leave_the_list(root):
    exempt_file(root, ["old"])
    write(root, "old", ws_text())
    assert has(run_backlog(root)["problems"], "old: header is valid; remove it from backlog-exempt.json")


@pytest.mark.parametrize("table", [True, False])
def test_exempt_workstream_that_became_terminal_must_leave_the_list(root, table):
    """r2 Codex F1 / Grok F2: closing an exempt workstream is when its slot retires. It
    used to be skipped before the exempt check, so the report said ok and the slot stayed."""
    exempt_file(root, ["old"])
    write(root, "old", ws_text(status="TERMINAL", table=table))
    rep = run_backlog(root)
    assert rep["terminal"] == ["old"]
    assert rep["problems"] == ["old: workstream is terminal; remove it from backlog-exempt.json"]
    assert rep["status"] == "invalid"


def test_terminal_workstream_not_exempt_is_not_a_problem(root):
    exempt_file(root, [])
    write(root, "old", ws_text(status="TERMINAL"))
    assert run_backlog(root)["problems"] == []

def test_listed_slug_with_no_workstream_file_is_a_problem(root):
    exempt_file(root, ["gone", "old"])
    write(root, "old", ws_text(drop=("Size",)))
    assert run_backlog(root)["problems"] == [
        "gone: listed in backlog-exempt.json but has no workstream file; remove it"]


def test_exempt_list_shorter_than_the_ceiling_is_a_problem(root):
    """Codex F1 / Grok F1: a well-formed file listing a slug that was never exempt passes
    unless the list's length is pinned in tracked code. Their repro: one slug, `fresh`."""
    exempt_file(root, ["fresh"])
    write(root, "fresh", ws_text(drop=("Size",)))
    assert run_backlog(root, ceiling=2)["problems"] == [
        "backlog-exempt.json lists 1 slugs but EXEMPT_CEILING is 2; the list only shrinks, "
        "so lower EXEMPT_CEILING in tools/workstream_state.py to 1"]


def test_exempt_list_longer_than_the_ceiling_is_a_problem(root):
    exempt_file(root, ["old", "fresh"])
    write(root, "old", ws_text(drop=("Size",)))
    write(root, "fresh", ws_text(drop=("Size",)))
    assert run_backlog(root, ceiling=1)["problems"] == [
        "backlog-exempt.json lists 2 slugs but EXEMPT_CEILING is 1; the list only shrinks, "
        "so a slug was added"]


def test_exempt_list_at_the_ceiling_is_not_a_problem(root):
    exempt_file(root, ["old"])
    write(root, "old", ws_text(drop=("Size",)))
    assert run_backlog(root, ceiling=1)["problems"] == []


def test_missing_exempt_file_has_no_ceiling_problem(root):
    write(root, "good", ws_text())
    assert run_backlog(root, ceiling=5)["problems"] == []


def test_backlog_defaults_to_the_module_ceiling(root):
    names = [f"old{n}" for n in range(ws.EXEMPT_CEILING)]
    exempt_file(root, names)
    for name in names:
        write(root, name, ws_text(drop=("Size",)))
    kw = dict(today=TODAY, root=root, board_owner=lambda slug: "UNROUTED", **lookups())
    assert ws.backlog(root / "data" / "workstreams", **kw)["problems"] == []
    exempt_file(root, names[1:])
    assert has(ws.backlog(root / "data" / "workstreams", **kw)["problems"], "EXEMPT_CEILING")


def test_exempt_ceiling_matches_the_live_exempt_file():
    """The tracked ceiling equals the length of the real (gitignored) list."""
    from vault_paths import main_checkout
    registry = main_checkout(ws.REPO_ROOT) / "data" / "workstreams"
    if not (registry / ws.EXEMPT_FILE).is_file():
        pytest.skip("no backlog-exempt.json readable from here")
    assert len(ws.load_exemptions(registry)[1]) == ws.EXEMPT_CEILING


def test_backlog_reports_a_malformed_exempt_file_without_traceback(root, cli, capsys):
    cli()
    exempt_file(root, [], raw="{not json")
    code, out = run_cli(capsys, "--json")
    assert code == 2
    assert (json.loads(out)["status"], json.loads(out)["code"]) == ("error", "bad_exempt_file")
    code, out = run_cli(capsys)
    assert code == 2 and "bad_exempt_file" in out and "Traceback" not in out


# ---------------------------------------------------------------- CLI, isolation

class FakeRefusal(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


def fake_dw(main: Path, *, refuse=False, events=None, board=None):
    events = events or []
    board = board or {}

    def resolve_main(r):
        if refuse:
            raise FakeRefusal("no_main_checkout", f"{r} is not a main checkout")
        return main

    def open_questions(evs):
        asked = {e["id"]: e for e in evs if e.get("type") == "asked"}
        done = {e["id"] for e in evs if e.get("type") == "resolved"}
        return {i: q for i, q in asked.items() if i not in done}

    return types.SimpleNamespace(
        Refusal=FakeRefusal,
        resolve_main=resolve_main,
        paths=lambda m: {"main": m, "questions": m / "q.jsonl", "board": m / "board.md",
                         "workstreams": m / "data" / "workstreams"},
        load_events=lambda p: events,
        open_questions=open_questions,
        routing=lambda p: ((lambda o: board.get(o, f"UNROUTED: no session holds {o}")), "ok"),
    )


@pytest.fixture
def cli(root, monkeypatch):
    def install(**kw):
        monkeypatch.setitem(sys.modules, "decision_write", fake_dw(root, **kw))
        monkeypatch.setattr(ws, "_today", lambda: TODAY)
        monkeypatch.setattr(ws, "_git_branch_state", lambda main, name: None)
    return install


def run_cli(capsys, *args):
    code = ws.main(["--backlog", *args])
    return code, capsys.readouterr().out


def test_backlog_json_schema_is_pinned(root, cli, capsys):
    cli()
    write(root, "a", ws_text())
    code, out = run_cli(capsys, "--json")
    data = json.loads(out)
    assert code == 0
    assert set(data) == {"status", "today", "registry", "terminal", "invalid", "ranked",
                         "overlaps", "value_counts", "problems"}
    assert set(data["ranked"][0]) == {
        "slug", "group", "value", "evidence", "size", "next", "oldest", "newest", "blockers",
        "stale_blockers", "files", "owner", "board_owner", "owner_drift"}
    assert data["status"] == "ok"
    assert data["registry"] == str(root / "data" / "workstreams")


def test_backlog_exits_1_when_any_workstream_is_invalid(root, cli, capsys, monkeypatch):
    cli()
    write(root, "a", ws_text(drop=("Files",)))
    code, out = run_cli(capsys, "--json")
    assert code == 1
    assert json.loads(out)["status"] == "invalid"


def test_backlog_exits_0_when_all_valid(root, cli, capsys):
    cli()
    write(root, "a", ws_text())
    assert run_cli(capsys)[0] == 0


def test_backlog_plain_output_names_items_and_reasons(root, cli, capsys, monkeypatch):
    cli()
    write(root, "good-one", ws_text())
    write(root, "bad-one", ws_text(drop=("Owner",)))
    code, out = run_cli(capsys)
    assert code == 1
    assert "bad-one" in out and "missing row: Owner" in out and "good-one" in out


def test_backlog_reads_questions_and_board_through_decision_write(root, cli, capsys):
    cli(events=[{"type": "asked", "id": "Q1"}, {"type": "asked", "id": "Q2"},
                {"type": "resolved", "id": "Q2"}], board={"a": "s9"})
    write(root, "a", ws_text(rows={"Blocker": "question:Q1, question:Q2"}))
    data = json.loads(run_cli(capsys, "--json")[1])
    item = data["ranked"][0]
    assert (item["group"], item["blockers"], item["stale_blockers"]) == (
        "blocked", ["question:Q1"], ["question:Q2"])
    assert (item["board_owner"], item["owner_drift"]) == ("s9", True)


def test_backlog_does_not_run_probes(root, cli, capsys, monkeypatch):
    cli()

    def boom(*a, **k):
        raise AssertionError("probe ran")
    monkeypatch.setattr(ws, "_run_probe", boom)
    monkeypatch.setattr(ws.subprocess, "run", boom)
    write(root, "a", ws_text(after_table='<!-- PROBE: ["python3", "tools/a.py"] -->'))
    assert run_cli(capsys)[0] == 0


def test_backlog_writes_nothing(root, cli, capsys):
    cli()
    write(root, "a", ws_text())
    write(root, "b", ws_text(drop=("Value",)))
    reg = root / "data" / "workstreams"
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in reg.iterdir()}
    run_cli(capsys, "--json")
    run_cli(capsys)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in reg.iterdir()}
    assert before == after


def test_backlog_reports_resolver_refusal_without_traceback(root, cli, capsys):
    cli(refuse=True)
    code, out = run_cli(capsys, "--json")
    assert code == 2
    data = json.loads(out)
    assert (data["status"], data["code"]) == ("error", "no_main_checkout")
    code, out = run_cli(capsys)
    assert code == 2
    assert "no_main_checkout" in out and "Traceback" not in out


def test_backlog_reports_missing_decision_write_without_traceback(root, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "decision_write", None)   # makes the import fail
    code, out = run_cli(capsys, "--json")
    assert code == 2
    assert json.loads(out)["code"] == "decision_write_missing"


def test_header_guard_reads_the_main_checkout_registry_from_a_worktree(root, cli, tmp_path):
    cli()
    src = ws.backlog_sources(tmp_path / "some-worktree")
    assert src["registry"] == root / "data" / "workstreams"
    assert src["main"] == root


def test_branch_state_uses_git_in_the_main_checkout(monkeypatch, tmp_path):
    calls = []

    class R:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(argv, **kw):
        calls.append(argv)
        if "for-each-ref" in argv:
            return R(0, "" if argv[-1].endswith("nope") else argv[-1] + "\n")
        assert argv[-1] == "main"
        return R(0 if any("merged-x" in a for a in argv) else 1)
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    assert ws._git_branch_state(tmp_path, "nope") is None
    assert ws._git_branch_state(tmp_path, "feature-x") == "open"
    assert ws._git_branch_state(tmp_path, "merged-x") == "merged"
    assert all(a[:3] == ["git", "-C", str(tmp_path)] for a in calls)


def test_branch_state_against_a_real_git_repo(tmp_path):
    import subprocess
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", "one")
    git("branch", "merged-x")
    git("checkout", "-q", "-b", "open-x")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", "two")
    assert ws._git_branch_state(tmp_path, "merged-x") == "merged"
    assert ws._git_branch_state(tmp_path, "open-x") == "open"
    assert ws._git_branch_state(tmp_path, "absent-x") is None


def test_render_leaves_header_rows_byte_identical(tmp_path):
    p = tmp_path / "a.md"
    text = ws_text()
    p.write_text(text, encoding="utf-8")
    ws.render(p, repo_root=tmp_path)
    after = p.read_text(encoding="utf-8")
    assert after.startswith(text.rstrip("\n"))
    assert ws.read_header(after) == ws.read_header(text)


def test_default_scan_summary_and_exit_unchanged_by_missing_rows(tmp_path, capsys):
    """T22: the default scan ignores the new rows; only --backlog checks them."""
    text = ws_text(table=True, drop=tuple(VALID_ROWS), block=False,
                   after_table='<!-- PROBE: ["python3", "-c", "print(\'{\\"value\\": 1}\')"] -->')
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    code = ws.main(["--registry", str(tmp_path), "--repo-root", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(data["summary"]) == {"total", "surfacing", "changed", "error", "typed",
                                    "terminal", "names"}
    assert data["rows"][0]["state"] == "changed"


# ---------------------------------------------------------------- the live guard

def test_every_live_workstream_has_a_valid_header_or_is_exempt():
    """The migration guard over the MAIN checkout's registry (section 6 rule 1).

    Skips, with the reason, until decision_write.py is on this branch, and on a clean clone
    where resolve_main refuses.
    """
    try:
        import decision_write as dw
    except ImportError:
        pytest.skip("decision_write.py is not on this branch yet (lands with question-queue-1106)")
    try:
        src = ws.backlog_sources(ws.REPO_ROOT)
    except dw.Refusal as exc:
        pytest.skip(f"no main checkout: {exc.message}")
    if not src["registry"].is_dir():
        pytest.skip(f"no registry at {src['registry']}")
    rep = ws.backlog(src["registry"], today=dt.date.today(), root=src["main"],
                     question_state=src["question_state"], branch_state=src["branch_state"],
                     board_owner=src["board_owner"])
    assert rep["problems"] == []


def test_real_resolve_main_import():
    """The real decision_write.py (on main since #32), imported the way run_backlog imports
    it. Skips only on a clean clone, where resolve_main refuses."""
    import decision_write as dw
    try:
        expected = dw.resolve_main(ws.REPO_ROOT)
    except dw.Refusal as exc:
        pytest.skip(f"no main checkout: {exc.message}")
    assert ws.backlog_sources(ws.REPO_ROOT)["main"] == expected


# ---------------------------------------------------------------- mutation survivors (2026-11-09)
# Each test below names the line it pins. They were written against the 27 survivors of the
# 2026-11-09 mutation run on 992878da, not for coverage in general.

def test_today_is_the_real_date():
    """_today (line 340): the CLI's only clock. Every other test monkeypatches it."""
    before = dt.date.today()
    got = ws._today()
    assert before <= got <= dt.date.today()


def test_a_row_repeated_three_times_is_one_duplicate():
    """read_header (line 372): the third copy must not add the name a second time."""
    text = ("| Field | Value |\n|---|---|\n| **Owner** | s1 |\n| **Owner** | s2 |\n"
            "| **Owner** | s3 |\n")
    h = ws.read_header(text)
    assert h["duplicates"] == ["Owner"]
    assert h["rows"]["Owner"] == "s1"


def test_unclosed_next_block_is_reported_as_unclosed(root):
    """_read_next (lines 416-417): an unclosed fence is its own reason, not a JSON error."""
    body = json.dumps([{"date": "2026-11-08", "action": GOOD_ACTION}])
    text = ws_text(block=f"```json next\n{body}")
    assert reasons(text, root) == ["next block is not closed"]


def test_empty_files_row_is_reported_as_empty(root):
    """_check_files (line 445): an empty row says so, not "'' is not one backticked path"."""
    assert reasons(ws_text(rows={"Files": ""}), root) == ["Files is empty"]


@pytest.mark.parametrize("files", ["`/etc/x.py`", "`../x.py`", "`~/x.py`"])
def test_a_refused_files_path_is_not_kept_or_checked_further(root, files):
    """_check_files (line 456): a refused path gets one reason and is not returned."""
    r = check(ws_text(rows={"Files": files}), root)
    assert r["files"] == []
    assert len(r["reasons"]) == 1 and "repo-relative" in r["reasons"][0]


def test_shared_covers_a_file_under_a_directory_in_either_argument():
    """_shared (lines 601-602): the directory entry may be in the second list."""
    assert ws._shared(["tools/a.py"], ["tools/"]) == ["tools/a.py"]
    assert ws._shared(["tools/"], ["tools/a.py"]) == ["tools/a.py"]
    assert ws._shared(["toolsx/a.py"], ["tools/"]) == []


def test_overlaps_are_sorted_by_slug_pair_not_by_rank(root):
    """backlog (line 659): rank order is z, a, m; overlaps come back in slug order."""
    write(root, "z", ws_text(rows={"Value": r"V4 \| evidence: docs/x.md"}))
    write(root, "a", ws_text(rows={"Value": "V2"}))
    write(root, "m", ws_text(rows={"Value": "V1"}))
    rep = run_backlog(root)
    assert slugs(rep) == ["z", "a", "m"]
    assert [(o["a"], o["b"]) for o in rep["overlaps"]] == [("a", "m"), ("a", "z"), ("m", "z")]


def test_backlog_sources_uses_the_module_it_is_given(root, tmp_path, monkeypatch):
    """backlog_sources (line 682): a passed module is used; nothing is imported."""
    monkeypatch.setitem(sys.modules, "decision_write", None)   # any import now fails
    src = ws.backlog_sources(tmp_path / "wt", fake_dw(root))
    assert src["main"] == root


def test_question_state_counts_only_asked_events_as_asked(root, tmp_path):
    """backlog_sources (line 688): an id seen only in a non-asked event was never asked."""
    events = [{"type": "asked", "id": "Q1"}, {"type": "asked", "id": "Q2"},
              {"type": "resolved", "id": "Q2"}, {"type": "resolved", "id": "Q7"}]
    qs = ws.backlog_sources(tmp_path / "wt", fake_dw(root, events=events))["question_state"]
    assert [qs(q) for q in ("Q1", "Q2", "Q7", "Q9")] == ["open", "resolved", None, None]


def test_plain_mode_error_is_one_text_line(root, cli, capsys):
    """_backlog_error (line 698): without --json the error is text, not a JSON object."""
    cli(refuse=True)
    code, out = run_cli(capsys)
    wt = ws.REPO_ROOT
    assert (code, out) == (2, f"backlog: error no_main_checkout: {wt} is not a main checkout\n")


def test_plain_mode_report_is_text_not_json(root, cli, capsys):
    """run_backlog (line 750): without --json the report is the text layout."""
    cli()
    write(root, "a", ws_text())
    out = run_cli(capsys)[1]
    assert out.startswith(f"backlog {TODAY} from {root / 'data' / 'workstreams'}\n")


def test_print_backlog_layout_is_pinned(capsys):
    """_print_backlog (lines 706-730): the full text layout, every section and branch."""
    item = {"slug": "alpha", "value": "V4", "size": "S", "group": "blocked",
            "blockers": ["question:Q1"], "stale_blockers": ["branch:old"], "owner": "s1",
            "board_owner": "s2", "owner_drift": True, "evidence": "docs/x.md",
            "next": [{"date": "2026-11-08", "action": GOOD_ACTION}]}
    plain = {"slug": "beta", "value": "V1", "size": "M", "group": "free", "blockers": [],
             "stale_blockers": [], "owner": "unheld", "board_owner": "UNROUTED: x",
             "owner_drift": False, "evidence": None,
             "next": [{"date": "2026-11-07", "action": GOOD_ACTION}]}
    rep = {"today": "2026-11-08", "registry": "/reg",
           "invalid": [{"slug": "gamma", "exempt": True, "reasons": ["r1", "r2"]},
                       {"slug": "delta", "exempt": False, "reasons": ["r3"]}],
           "ranked": [item, plain],
           "overlaps": [{"a": "alpha", "b": "beta", "shared": ["tools/a.py", "tools/b.py"],
                         "blocked": True},
                        {"a": "beta", "b": "zeta", "shared": ["docs/x.md"], "blocked": False}],
           "terminal": ["omega", "psi"],
           "value_counts": {"V1": 1, "V2": 0, "V3": 0, "V4": 1},
           "problems": ["p1", "p2"]}
    ws._print_backlog(rep)
    assert capsys.readouterr().out == (
        "backlog 2026-11-08 from /reg\n"
        "\ninvalid (2):\n"
        "  gamma [exempt]: r1; r2\n"
        "  delta: r3\n"
        "\nranked (2):\n"
        "   1. alpha  V4 S blocked  blockers: question:Q1  stale blocker: branch:old"
        "  owner-drift: row s1, board s2\n"
        "      evidence: docs/x.md\n"
        f"      next 2026-11-08: {GOOD_ACTION}\n"
        "   2. beta  V1 M free  owner unheld\n"
        f"      next 2026-11-07: {GOOD_ACTION}\n"
        "\noverlaps (2):\n"
        "  alpha and beta: tools/a.py, tools/b.py [blocked]\n"
        "  beta and zeta: docs/x.md\n"
        "\nterminal (2): omega, psi\n"
        "values: V1 1, V2 0, V3 0, V4 1\n"
        "problems (2):\n"
        "  p1\n"
        "  p2\n")
