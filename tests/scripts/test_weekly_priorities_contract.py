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


def test_standup_is_allowed_to_write_the_weekly_log_and_nothing_else():
    """853/856/863/867.F1, 2026-10-29: Step 2w had to write data/weekly-review-log.md, but
    the skill's allowed-tools granted no Write at all."""
    import re
    skill = (Path(__file__).resolve().parents[2] / ".claude/skills/standup/SKILL.md").read_text()
    tools = re.search(r"^allowed-tools:(.*)$", skill, re.M).group(1)
    writes = re.findall(r"\b(?:Write|Edit)\(([^)]*)\)", tools)
    assert writes == ["data/weekly-review-log.md"]


def test_checkout_never_skips_a_missing_week_silently():
    skill = (Path(__file__).resolve().parents[2] / ".claude/skills/checkout/SKILL.md").read_text()
    assert "Skip the weekly priority cross-reference silently" not in skill
    assert "No weekly priorities on file for this week" in skill


def test_checkout_uses_only_this_weeks_entry():
    """Round 7 (Grok), 2026-10-29: checkout read the NEWEST Top 5 with no week check, so a
    missed Monday write silently starred last week's priorities."""
    skill = (REPO / ".claude/skills/checkout/SKILL.md").read_text(encoding="utf-8")
    assert "### Week of YYYY-MM-DD" in skill
    assert "never star last week's priorities" in skill
