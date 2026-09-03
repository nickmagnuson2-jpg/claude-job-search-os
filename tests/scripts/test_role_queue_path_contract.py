"""The producer/consumer path contract for the career-scan role queue.

WHY THIS FILE EXISTS
--------------------
From 2026-08-11 to 2026-09-02 the career scanner ran daily and scored roughly thirty
roles a night. Nobody ever saw one. The scanner wrote `data/inbox.md`; `/standup`'s
"Career-scan matches" section globbed the `inbox/` DIRECTORY for `*career-scan*` files
that had never existed and never could. Producer healthy, consumer pointing elsewhere,
no error anywhere, for three weeks.

That defect is invisible to unit tests: both halves work perfectly in isolation. It is
only detectable by asserting the two halves refer to the SAME path, which is what this
file does.
"""
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scanner import role_queue_path  # noqa: E402
from tools.role_queue_read import read_queue  # noqa: E402

STANDUP_SKILL = REPO_ROOT / ".claude" / "skills" / "standup" / "SKILL.md"


def test_the_reader_reads_the_path_the_writer_writes(tmp_path):
    """The core contract, exercised rather than asserted about."""
    import json
    from tools.career_scanner.scanner import write_role_queue

    role = {"company": "Acme", "title": "Deployment Strategist",
            "url": "https://boards.example/1", "score": 7, "published_at": "2026-09-01"}
    written = write_role_queue(tmp_path, [role], [], [])
    assert written == role_queue_path(tmp_path)

    out = read_queue(tmp_path)
    assert out["exists"] is True, "the reader could not find what the writer just wrote"
    assert out["new_count"] == 1
    assert out["roles"][0]["title"] == "Deployment Strategist"


def test_standup_invokes_the_reader_by_name():
    """The skill must actually call the tool.

    Without this, the reader can be correct and still be orphaned - which is precisely
    the shape of the original bug. A path contract that both halves satisfy is useless
    if the surface never calls either.
    """
    if not STANDUP_SKILL.is_file():
        pytest.skip("standup SKILL.md not present")
    text = STANDUP_SKILL.read_text(encoding="utf-8")
    assert "role_queue_read.py" in text, (
        "/standup does not invoke tools/role_queue_read.py, so scanned roles reach "
        "nobody. This is the 2026-09-02 defect restated."
    )


def test_standup_no_longer_globs_the_directory_that_never_had_career_scan_files():
    """The dead branch must be gone, not merely supplemented.

    A category that can never match trains the reader to ignore the section it lives
    in, which is how ~30 roles a day went unnoticed.
    """
    if not STANDUP_SKILL.is_file():
        pytest.skip("standup SKILL.md not present")
    text = STANDUP_SKILL.read_text(encoding="utf-8")
    # Look for the CATEGORISATION RULE, not any mention of the string. The skill now
    # documents the historical defect in prose, and a test that cannot tell an
    # explanation from a live rule would force the explanation to be deleted -- losing
    # the only record of why the section exists. Match the rule shape: a glob on the
    # same line as a routing arrow or the word "category".
    dead = [
        line for line in text.splitlines()
        if re.search(r"\*career-(scan|match)\*", line)
        and re.search(r"→|->|\bcategory\b", line)
    ]
    assert not dead, (
        "standup still categorises inbox/ files by a career-scan glob; no such file "
        f"has ever existed there and the scanner does not write them: {dead}"
    )


def test_a_missing_queue_is_reported_not_raised(tmp_path):
    """/standup must degrade, never fail, on a scan that has not run."""
    out = read_queue(tmp_path / "empty")
    assert out["exists"] is False
    assert out["new_count"] is None, "a missing queue must not report zero new roles"


def test_a_missing_queue_never_reports_zero(tmp_path):
    """Null and zero are different claims. 'The scan found nothing' and 'the scan did
    not run' must not render identically - that conflation is the false-zero defect
    one layer up."""
    out = read_queue(tmp_path / "nope")
    assert out["new_count"] is not 0  # noqa: F632 - identity check is the point
