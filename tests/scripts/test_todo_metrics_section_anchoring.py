"""Section detection must anchor to line starts, not match anywhere in the file.

`tools/todo_daily_metrics.py` located the Completed section with
    re.search(r"## Completed.*?\\n(.*?)(?=\\n## |\\Z)", content, re.DOTALL)
which searches ANYWHERE in the document. A todo whose NOTES cell contains the
literal text "## Completed" therefore acts as a phantom section header: the regex
matches inside the table row, then terminates at the real header, and the captured
"section" is whatever few lines sat between them.

Found live 2026-08-17. Three Active rows carried a recovery note reading
"...misfiled into ## Completed by the 2026-08-05 bad sync...". They sat immediately
above the real `## Completed` header, so parse_todos captured a 2-row window instead
of the 744-row section and reported **completed_today: 0 while five rows in the file
literally read "Completed 2026-08-17"**.

That is the worst shape of failure available here: no error, no warning, a plausible
number. It flows straight into the daily log, the streak, and velocity.

The data is user-authored prose and always will be — notes quote file paths, section
names, and markdown. So the fix belongs in the parser, not in a rule about what Nick
is allowed to type.
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import todo_daily_metrics as m  # noqa: E402


def _fixture(note: str) -> str:
    return f"""# Job Search To-Dos

## Active

| Task | Priority | Due | Status | Notes |
| --- | --- | --- | --- | --- |
| A live task | High | 2026-08-01 | Pending | {note} |

## Completed

| Task | Priority | Completed | Notes |
| --- | --- | --- | --- |
| Finished one | High | 2026-08-17 | Completed 2026-08-17 |
| Finished two | Med | 2026-08-17 | Completed 2026-08-17 |
| Finished earlier | Low | 2026-08-01 | Completed 2026-08-01 |
"""


def test_completed_section_found_despite_phantom_header_in_notes():
    """The live 2026-08-17 failure, reduced."""
    content = _fixture("misfiled into ## Completed by the bad sync")
    completed, _active, _overdue = m.parse_todos(content, date(2026, 8, 17))
    assert len(completed) == 2, "notes cell swallowed the real Completed section"


def test_active_section_found_despite_phantom_header_in_notes():
    """Same defect, other section: '## Active' in prose must not truncate Active."""
    content = _fixture("moved back to ## Active after the restore")
    _completed, active, _overdue = m.parse_todos(content, date(2026, 8, 17))
    assert len(active) == 1
    assert active[0]["task"] == "A live task"


def test_clean_notes_still_parse():
    """Guard against a fix that over-tightens and breaks the ordinary case."""
    content = _fixture("an ordinary note")
    completed, active, overdue = m.parse_todos(content, date(2026, 8, 17))
    assert len(completed) == 2
    assert len(active) == 1
    assert len(overdue) == 1


def test_only_todays_completions_counted():
    """The date filter must survive the anchoring change."""
    content = _fixture("misfiled into ## Completed by the bad sync")
    completed, _a, _o = m.parse_todos(content, date(2026, 8, 1))
    assert [c["task"] for c in completed] == ["Finished earlier"]


def test_source_regexes_are_line_anchored():
    """Structural guard: catches a future edit that reintroduces the unanchored form.

    Asserted against the source text because the defect is invisible in behaviour
    until a note happens to contain the magic string -- which is precisely how it
    survived unnoticed.
    """
    src = Path(m.__file__).read_text(encoding="utf-8")
    for pattern in re.findall(r'r"\^?## (?:Active|Completed)[^"]*"', src):
        assert pattern.startswith('r"^'), f"unanchored section regex: {pattern}"
