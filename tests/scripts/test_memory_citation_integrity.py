"""Guard: no skill or CLAUDE.md cites a memory file that resolves nowhere.

fable-audit 2026-07-07 #1 — six skill citations pointed at memory files that had
been archived and deleted, creating recall dead-ends (and ghost-fact risk). Every
memory citation on the public surface must resolve as a live memory file OR appear
in an archive rollup.

Skips where the per-user memory sidecar isn't present (e.g. public CI), since the
memory dir is gitignored and personal.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# The Claude Code memory sidecar for this project: ~/.claude/projects/<enc>/memory
MEM = Path.home() / ".claude" / "projects" / str(REPO).replace("/", "-") / "memory"

# Only match citation-shaped references: [[slug]], `slug`, or `memory/slug.md`.
# This avoids false positives on code identifiers like user_id / project_root.
_SLUG = r"(?:feedback|reference|project|user)_[a-z0-9_]+"
CITATION = re.compile(
    r"\[\[(" + _SLUG + r")\]\]"                       # [[slug]]
    r"|`(?:memory/)?(" + _SLUG + r")(?:\.md)?`"       # `slug` or `memory/slug.md`
)


def _iter_citations(text):
    for m in CITATION.finditer(text):
        yield m.group(1) or m.group(2)


@pytest.mark.skipif(not MEM.is_dir(), reason="memory sidecar not present (public CI env)")
def test_no_dead_memory_citations_on_public_surface():
    archives = [p.read_text(encoding="utf-8", errors="ignore") for p in MEM.glob("archive-*.md")]

    def resolves(slug):
        if (MEM / f"{slug}.md").exists():
            return True
        return any(slug in a for a in archives)

    surface = list((REPO / ".claude" / "skills").glob("*/SKILL.md")) + [REPO / "CLAUDE.md"]
    dead = []
    for f in surface:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for slug in sorted(set(_iter_citations(text))):
            if not resolves(slug):
                dead.append(f"{f.relative_to(REPO)} -> {slug}")

    assert not dead, "Memory citations resolving nowhere (file or archive):\n" + "\n".join(dead)
