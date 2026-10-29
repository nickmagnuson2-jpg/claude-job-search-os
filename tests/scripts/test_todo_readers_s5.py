"""S5: the behaviours of the two queue readers that moved onto tools/todo_read.py.

Placeholder tasks only (public repo). These pin what the move must keep: attention's
skip-not-zero on drift and its oldest-first order, todos_summary's ranking flags and output.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import attention  # noqa: E402
import todos_summary as ts  # noqa: E402

TODOS = """# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
|------|----------|-----|--------|-------|
| Newer overdue | Low | 2026-01-08 | Pending | |
| Older overdue | High | 2026-01-02 | Pending | |
| Due today | Medium | 2026-01-10 | Pending | |
| Tomorrow | High | 2026-01-11 | Pending | |
| No date | High | — | Pending | |
| Done one | High | 2026-01-01 | Done | |
"""
TODAY = date(2026, 1, 10)


def _repo(tmp_path, text=TODOS):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "job-todos.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_attention_lists_overdue_oldest_first_and_skips_on_drift(tmp_path):
    q = attention.queue_todos(_repo(tmp_path), TODAY)
    assert q["status"] == "ok" and q["count"] == 2 and q["denominator"] == 6
    assert [r["task"] for r in q["detail"]["top"]] == ["Older overdue", "Newer overdue"]
    assert q["oldest_age_days"] == 8
    drift = TODOS.replace("| Task | Priority | Due | Status | Notes |", "| Task | Due | Priority | Status | Notes |")
    s = attention.queue_todos(_repo(tmp_path, drift), TODAY)
    assert s["status"] == "SKIPPED" and s["count"] is None and "schema drift" in s["reason"]


def test_attention_with_nothing_overdue(tmp_path):
    q = attention.queue_todos(_repo(tmp_path), date(2026, 1, 1))
    assert q["count"] == 0 and q["oldest_age_days"] is None


def test_rank_todos_flags_and_order():
    ranked = ts.rank_todos(ts.parse_todos(TODOS), TODAY)
    by = {t["task"]: t for t in ranked}
    assert "Done one" not in by
    assert by["Due today"]["is_due_today"] is True and by["Tomorrow"]["is_due_today"] is False
    assert by["Older overdue"]["is_overdue"] is True and by["Due today"]["is_overdue"] is False
    assert by["No date"]["days_until_due"] is None
    # High first; within High: overdue (8 days) vs tomorrow (1 day) vs no date (last)
    assert [t["task"] for t in ranked] == ["Tomorrow", "Older overdue", "No date",
                                           "Due today", "Newer overdue"]


def test_same_distance_prefers_the_overdue_row():
    text = TODOS.replace("| Older overdue | High | 2026-01-02 |", "| Older overdue | High | 2026-01-09 |")
    ranked = ts.rank_todos(ts.parse_todos(text), TODAY)
    assert [t["task"] for t in ranked][:2] == ["Older overdue", "Tomorrow"]


def test_cli_output(tmp_path):
    root = _repo(tmp_path)
    (root / "data" / "workbooks").mkdir()
    (root / "data" / "workbooks" / "_morning-starter-2026-01-10.md").write_text("start here")
    r = subprocess.run([sys.executable, str(TOOLS / "todos_summary.py"), "--repo-root", str(root),
                        "--target-date", "2026-01-10", "--top-n", "2"],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout)
    assert d["total_pending"] == 5 and d["overdue_count"] == 2 and d["high_priority_count"] == 3
    assert [t["task"] for t in d["top_n"]] == ["Tomorrow", "Older overdue"]
    assert d["morning_starter"]["exists"] is True and d["morning_starter"]["content_preview"] == "start here"
    (root / "data" / "workbooks" / "_morning-starter-2026-01-10.md").unlink()
    d2 = json.loads(subprocess.run([sys.executable, str(TOOLS / "todos_summary.py"), "--repo-root",
                                    str(root), "--target-date", "2026-01-10"],
                                   capture_output=True, text=True, timeout=60).stdout)
    assert d2["morning_starter"] == {"path": "data/workbooks/_morning-starter-2026-01-10.md",
                                     "exists": False, "content_preview": ""}
