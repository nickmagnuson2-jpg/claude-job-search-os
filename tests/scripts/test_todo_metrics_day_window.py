"""10b (todo #113): the artifact scan counts only files modified ON the target date.

Before, the window had a lower bound only, so a past-date /checkout counted every file
touched since that day."""
import os
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import todo_daily_metrics as tdm  # noqa: E402


def _touch(p: Path, when: datetime):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    t = when.timestamp()
    os.utime(p, (t, t))


def test_day_window_is_one_local_day():
    start, end = tdm.day_window(date(2026, 1, 7))
    assert start == datetime(2026, 1, 7).timestamp() and end == datetime(2026, 1, 8).timestamp()
    s2, e2 = tdm.day_window(date(2026, 3, 8))  # DST change in US zones; still midnight to midnight
    assert e2 == datetime(2026, 3, 9).timestamp() and s2 == datetime(2026, 3, 8).timestamp()


def test_a_past_date_counts_only_that_day(tmp_path):
    out = tmp_path / "output" / "acme"
    _touch(out / "a-day-before.md", datetime(2026, 1, 6, 23, 59))
    _touch(out / "a-on-the-day.md", datetime(2026, 1, 7, 0, 0))
    _touch(out / "b-on-the-day.md", datetime(2026, 1, 7, 23, 59, 59))
    _touch(out / "c-next-day.md", datetime(2026, 1, 8, 0, 0))
    _touch(tmp_path / "data" / "reflections" / "r.md", datetime(2026, 1, 9, 12, 0))
    got = tdm.scan_substantive_work(tmp_path, date(2026, 1, 7))
    assert got["outputs_today"] == 2
    assert sorted(got["outputs_paths_by_entity"]["acme"]) == ["acme/a-on-the-day.md", "acme/b-on-the-day.md"]
    assert got["reflections_today"] == 0
    assert tdm.scan_substantive_work(tmp_path, date(2026, 1, 9))["reflections_today"] == 1
