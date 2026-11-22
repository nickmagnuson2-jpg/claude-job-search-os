"""The B2 guards are wired: every tool class they judge invokes both of them.

Codex r1 F1 (2026-11-22): a hook that is not registered in .claude/settings.json is never
called, and its own subprocess tests cannot notice. This test reads the committed
PreToolUse configuration and checks that, for each of Write, Edit, MultiEdit and Bash,
some matcher that matches the tool name runs each guard. Install with Part 2 of
output/analysis/112226-b2-guard-staging/INSTALL.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"
GUARDS = ("check_memory_frontmatter_write.py", "check_lessons_write.py")
TOOLS = ("Write", "Edit", "MultiEdit", "Bash")


def commands_for(tool: str) -> list[str]:
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    out = []
    for group in hooks:
        matcher = group.get("matcher", "")
        # An empty or absent matcher matches every tool (Claude Code; Grok r1 F4).
        if not matcher or matcher == "*" or re.fullmatch(matcher, tool):
            out += [h.get("command", "") for h in group.get("hooks", [])]
    return out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("guard", GUARDS)
def test_each_tool_class_invokes_each_guard(tool, guard):
    cmds = [c for c in commands_for(tool) if c.rstrip().endswith(f"/tools/{guard}")]
    assert len(cmds) == 1, f"{guard} is wired {len(cmds)} times for {tool}"
    assert cmds[0].startswith("PYTHONIOENCODING=utf-8 python3 $CLAUDE_PROJECT_DIR/tools/")


def test_a_tool_the_guards_do_not_judge_does_not_invoke_them():
    cmds = commands_for("Read")
    assert not any(g in c for c in cmds for g in GUARDS)
