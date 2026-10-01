"""The Concision Pass in framework/outreach-guide.md links to rules; it does not copy them.

A link is only better than a copy if it cannot go stale silently. This test resolves every
`file` § `heading` citation in that section against the cited file, so renaming or deleting
a heading in a McKinsey framework file fails here instead of leaving the outreach skills
pointing at nothing.

The framework files are gitignored, so the live checks skip when they are absent (a fresh
clone, CI). The parser itself is tested on synthetic files and always runs.
"""
import re

import pytest

from conftest import REPO_ROOT

GUIDE = REPO_ROOT / "framework" / "outreach-guide.md"
SECTION = "## Concision Pass"
CITATION = re.compile(r"`(framework/[^`]+\.md)` § `([^`]+)`")
CONSUMERS = [
    REPO_ROOT / ".claude" / "skills" / "cold-outreach" / "SKILL.md",
    REPO_ROOT / ".claude" / "skills" / "follow-up" / "SKILL.md",
]


def section_text(guide_text: str) -> str:
    """Return the Concision Pass section body, or '' if the heading is missing."""
    lines = guide_text.splitlines()
    try:
        start = lines.index(SECTION)
    except ValueError:
        return ""
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def citations(guide_text: str) -> list[tuple[str, str]]:
    return CITATION.findall(section_text(guide_text))


def broken_links(guide_text: str, root) -> list[str]:
    """Every citation whose file is missing or whose heading is not a heading line there."""
    broken = []
    for rel_path, heading in citations(guide_text):
        target = root / rel_path
        if not target.is_file():
            broken.append(f"{rel_path} (file missing)")
            continue
        headings = {
            line.lstrip("#").strip()
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        }
        if heading not in headings:
            broken.append(f"{rel_path} § {heading} (heading missing)")
    return broken


def _synthetic(tmp_path, heading_in_source: str) -> str:
    (tmp_path / "framework").mkdir()
    (tmp_path / "framework" / "source.md").write_text(
        f"# Source\n\n### {heading_in_source}\n\nBody.\n", encoding="utf-8"
    )
    return (
        "# Guide\n\n## Concision Pass\n\n"
        "| 1 | Rule | `framework/source.md` § `Cut filler` | note |\n\n"
        "## Next Section\n\n"
        "| 9 | Out of scope | `framework/absent.md` § `Ignored` | note |\n"
    )


def test_resolving_citation_is_clean(tmp_path):
    guide = _synthetic(tmp_path, "Cut filler")
    assert citations(guide) == [("framework/source.md", "Cut filler")]
    assert broken_links(guide, tmp_path) == []


def test_renamed_heading_is_reported(tmp_path):
    guide = _synthetic(tmp_path, "Cut filler words")
    assert broken_links(guide, tmp_path) == [
        "framework/source.md § Cut filler (heading missing)"
    ]


def test_missing_file_is_reported(tmp_path):
    guide = _synthetic(tmp_path, "Cut filler").replace("source.md", "gone.md")
    assert broken_links(guide, tmp_path) == ["framework/gone.md (file missing)"]


def test_heading_text_in_body_does_not_count(tmp_path):
    """A heading that survives only as a phrase in a paragraph is still a broken link."""
    guide = _synthetic(tmp_path, "Other")
    (tmp_path / "framework" / "source.md").write_text(
        "# Source\n\nSee Cut filler below.\n", encoding="utf-8"
    )
    assert broken_links(guide, tmp_path) == [
        "framework/source.md § Cut filler (heading missing)"
    ]


@pytest.mark.skipif(not GUIDE.is_file(), reason="framework/outreach-guide.md is gitignored")
def test_live_guide_citations_resolve():
    guide = GUIDE.read_text(encoding="utf-8")
    found = citations(guide)
    # A format change that drops every citation would otherwise pass with nothing checked.
    assert len(found) >= 5, f"expected at least 5 citations in {SECTION}, found {len(found)}"
    assert broken_links(guide, REPO_ROOT) == []


@pytest.mark.skipif(not GUIDE.is_file(), reason="framework/outreach-guide.md is gitignored")
@pytest.mark.parametrize("skill", CONSUMERS, ids=lambda p: p.parent.name)
def test_drafting_skills_run_the_pass(skill):
    text = skill.read_text(encoding="utf-8")
    assert "`## Concision Pass` section of `framework/outreach-guide.md`" in text
    assert "- Concision pass:" in text
