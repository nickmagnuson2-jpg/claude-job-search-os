"""S6: Monday /standup writes the week's Top 5 and /checkout reads it. The two skills must
name the same heading literally; an LLM inferring a prefix match is not a contract."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HEADING = "#### Top 5 Priorities for Coming Week"


def test_writer_and_reader_name_the_same_heading():
    standup = (REPO / ".claude/skills/standup/SKILL.md").read_text(encoding="utf-8")
    checkout = (REPO / ".claude/skills/checkout/SKILL.md").read_text(encoding="utf-8")
    assert HEADING in standup and HEADING in checkout
    assert "data/weekly-review-log.md" in standup and "data/weekly-review-log.md" in checkout


def test_the_weekly_review_skill_is_gone():
    assert not (REPO / ".claude/skills/weekly-review").exists()


def test_standup_surfaces_the_learning_ledger():
    standup = (REPO / ".claude/skills/standup/SKILL.md").read_text(encoding="utf-8")
    assert "results.learning_ledger.data" in standup
