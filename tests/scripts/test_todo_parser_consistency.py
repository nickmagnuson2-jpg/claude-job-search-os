"""One todo parser, one definition of overdue (S5). Modeled on
test_stage_classification_consistency.py.

The bug class: attention.py, todos_summary.py and todo_daily_metrics.py each parsed
data/job-todos.md themselves and disagreed on the same file: overdue 206 vs 197 (one used
`<=`, two `<`), pending 480 vs 485 (one counted Withdrawn and Done rows left in the Active
section as remaining). tools/todo_read.py now holds the rules.

These tests enforce:
  1. SINGLE SOURCE OF TRUTH: each consumer imports todo_read, and the functions that read
     todo rows contain no parsing of their own (no table splitting, no section regex, no
     date comparison against today, no "pending" string test).
  2. CROSS-TOOL PARITY: the three CLIs report the same open count and the same overdue
     count, on a fixture built to hit every way they used to differ, and on the live file
     when it is present (the owner's env; skipped where data/ is absent).
"""
import ast
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import todo_read  # noqa: E402

# file -> the functions in it that read todo rows
CONSUMERS = {
    "attention.py": ["queue_todos"],
    "todos_summary.py": ["parse_todos", "rank_todos"],
    "todo_daily_metrics.py": ["parse_todos"],
    "day_check.py": ["_todos_closed"],
    "act_classify.py": ["parse_todos"],
}


def _functions(fname):
    src = (TOOLS / fname).read_text(encoding="utf-8")
    tree = ast.parse(src)
    return src, {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


@pytest.mark.parametrize("fname", sorted(CONSUMERS))
def test_consumer_imports_todo_read(fname):
    src = (TOOLS / fname).read_text(encoding="utf-8")
    assert "import todo_read" in src, f"{fname} must read todos through tools/todo_read.py"


def _offences(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if "## Active" in v or "## Completed" in v or v.startswith("| Task") or v == "|":
                out.append(f"string {v!r}")
            if v.lower() == "pending":
                out.append("a 'pending' status test")
        if isinstance(n, ast.Compare) and any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                                              for op in n.ops):
            names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
            if "today" in names:
                out.append("a date comparison with today")
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) in ("split", "strptime"):
            out.append(f".{n.func.attr}() call")
    return out


@pytest.mark.parametrize("fname,func", [(f, fn) for f, fns in sorted(CONSUMERS.items()) for fn in fns])
def test_todo_functions_hold_no_parsing_of_their_own(fname, func):
    _src, funcs = _functions(fname)
    assert func in funcs, f"{fname}:{func} is gone; update CONSUMERS"
    node = funcs[func]
    uses = {n.value.id for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    assert "todo_read" in uses, f"{fname}:{func} does not call todo_read"
    offences = _offences(node)
    assert not offences, f"{fname}:{func} parses todos itself: {offences}"


def test_the_offence_detector_sees_the_old_copies():
    """The guard above is only real if it would have caught the code it replaced."""
    old = ast.parse('''
def queue_todos(repo, today):
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not status.lower().startswith("pending"):
            continue
        if d <= today:
            pass
''').body[0]
    found = _offences(old)
    assert any("split" in f for f in found)
    assert any("pending" in f for f in found)
    assert any("today" in f for f in found)


# --- parity ---------------------------------------------------------------------------

TODAY = "2026-01-10"
FIXTURE = """# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|------|----------|-----|--------|-------|
| Overdue pending | High | 2026-01-05 | Pending | a note that says ## Completed inline |
| Due today | High | 2026-01-10 | Pending | |
| Future | Low | 2026-01-20 | Pending | |
| No due | Low | — | Pending | |
| In progress overdue | Medium | 2026-01-01 | In Progress | |
| Withdrawn left here | Low | 2026-01-01 | Withdrawn 2026-01-02 (role dropped) | |
| Done left here | Low | 2026-01-01 | Done | |
| --- | | | | |

## Completed

| Task | Priority | Completed | Notes |
|------|----------|-----------|-------|
| Closed one | High | 2026-01-10 | |
| Pending stranded here | High | Pending | |
"""


def _run(script, root, *extra):
    p = subprocess.run([sys.executable, str(TOOLS / script), "--repo-root", str(root), *extra],
                       capture_output=True, text=True, timeout=120,
                       env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})
    assert p.returncode in (0, 1), p.stderr
    return json.loads(p.stdout)


def _three(root, today):
    s = _run("todos_summary.py", root, "--target-date", today)
    a = _run("attention.py", root, "--json", "--today", today,
             "--memory-dir", str(Path(root) / "no-memory"))
    m = _run("todo_daily_metrics.py", root, "--target-date", today)
    q = [x for x in a["queues"] if x["name"] == "todos"][0]
    return {
        "open": (s["total_pending"], len(m["active_remaining"])),
        "overdue": (s["overdue_count"], q["count"], len(m["overdue"])),
    }


def test_the_three_tools_agree_on_the_fixture(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-todos.md").write_text(FIXTURE, encoding="utf-8")
    got = _three(tmp_path, TODAY)
    # open: overdue pending, due today, future, no due, in progress. Not withdrawn/done.
    assert got["open"] == (5, 5), got
    # overdue: the pending and the in-progress row due before today. Not the one due today.
    assert got["overdue"] == (2, 2, 2), got


def test_the_module_rules_on_the_fixture():
    t = date(2026, 1, 10)
    assert [r["task"] for r in todo_read.overdue_rows(FIXTURE, t)] == [
        "Overdue pending", "In progress overdue"]
    assert len(todo_read.open_rows(FIXTURE)) == 5
    assert len(todo_read.active_rows(FIXTURE)) == 7
    assert [r["task"] for r in todo_read.completed_on(FIXTURE, t)] == ["Closed one"]


LIVE = REPO_ROOT / "data" / "job-todos.md"


@pytest.mark.skipif(not LIVE.exists(), reason="live todo file not present (public/CI env)")
def test_the_three_tools_agree_on_the_live_file():
    today = date.today().isoformat()
    got = _three(REPO_ROOT, today)
    assert len(set(got["open"])) == 1, got
    assert len(set(got["overdue"])) == 1, got


def test_act_classify_decides_open_through_todo_read():
    src, funcs = _functions("act_classify.py")
    seg = ast.get_source_segment(src, funcs["classify_todo"])
    assert "todo_read.is_open(row)" in seg and '"pending"' not in seg


def test_act_classify_reads_the_same_open_rows():
    import act_classify
    rows = act_classify.parse_todos(FIXTURE)
    assert [r["task"] for r in rows] == [r["task"] for r in todo_read.active_rows(FIXTURE)]
    assert sum(1 for r in rows if todo_read.is_open(r)) == 5
    assert all("row" not in r for r in rows), "rows stay JSON-serializable"


@pytest.mark.skipif(not LIVE.exists(), reason="live todo file not present (public/CI env)")
def test_act_classify_agrees_on_the_live_file():
    import act_classify
    text = LIVE.read_text(encoding="utf-8")
    assert len(act_classify.parse_todos(text)) == len(todo_read.active_rows(text))


# --- todo_read's own rules, one by one ------------------------------------------------------

def test_is_row_rejects_prose_separators_and_the_header():
    assert todo_read.is_row("| a | b |") is True
    assert todo_read.is_row("prose | a |") is False
    assert todo_read.is_row("| --- | --- |") is False
    assert todo_read.is_row("|:---:|---|") is False
    assert todo_read.is_row("| Task | Priority |") is False


def test_active_rows_skip_spacers_short_rows_and_read_notes():
    text = ("## Active\n\n| Task | Priority | Due | Status | Notes |\n|---|---|---|---|---|\n"
            "| --- | | | | |\n| — | | | | |\n|  | x | | Pending | |\n| short | High | x |\n"
            "| four cols | High | 2026-01-01 | Pending |\n| five | Low | — | Pending | a note |\n")
    rows = todo_read.active_rows(text)
    assert [r["task"] for r in rows] == ["four cols", "five"]
    assert rows[0]["notes"] == "" and rows[1]["notes"] == "a note"


def test_active_rows_refuse_a_drifted_header():
    with pytest.raises(todo_read.SchemaDriftError):
        todo_read.active_rows("## Active\n\n| Task | Due | Priority | Status | Notes |\n")


def test_completed_rows_skip_prose_and_short_rows():
    text = ("## Completed\n\n| Task | Priority | Completed | Notes |\n|---|---|---|---|\n"
            "prose line mentioning 2026-01-10\n| two | cols |\n| ok | Low | 2026-01-10 |\n")
    assert [r["task"] for r in todo_read.completed_rows(text)] == ["ok"]
    assert todo_read.completed_rows("## Active\n") == []


def test_parse_date_cases():
    assert todo_read.parse_date("—") is None and todo_read.parse_date("2026-13-40") is None
    assert todo_read.parse_date("~2026-01-05 soft") == date(2026, 1, 5)


def test_section_row_counts():
    # The "| --- |" spacer row reads as a separator, so 7 of the 8 Active lines are rows.
    assert todo_read.section_row_counts(FIXTURE) == (7, 2)
    assert todo_read.section_row_counts("nothing") == (-1, -1)
