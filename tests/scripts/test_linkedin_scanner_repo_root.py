"""Tests for llm.py REPO_ROOT resolution — fable-audit 2026-07-07 #14.

Kept separate from test_linkedin_scanner_unit.py (which imports scan.py and so needs
pandas/tqdm and gates on `importorskip("termcolor")`). This file imports ONLY
src.llm and mocks its two optional deps (anthropic, termcolor), so it runs in any
environment — the whole point is that the #14 fix is actually verified, not skipped.

The bug: llm.py computed REPO_ROOT with 4 '..' hops (one level ABOVE the repo), so
data/profile.md never loaded and every ranking silently used the generic CoS
fallback. The fix is 3 hops (src/ -> linkedin-scanner/ -> tools/ -> repo root).
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = REPO_ROOT / "tools" / "linkedin-scanner"

# Mock optional deps so llm.py imports without them installed. Track whether we
# injected termcolor so we can remove it afterward — test_linkedin_scanner_unit.py
# uses importorskip("termcolor") as its gate, and a lingering mock would make that
# gate pass and then error on the (absent) pandas dep.
_injected_termcolor = "termcolor" not in sys.modules
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = MagicMock()
if _injected_termcolor:
    sys.modules["termcolor"] = MagicMock()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

if str(SCANNER_DIR) not in sys.path:
    sys.path.insert(0, str(SCANNER_DIR))

import src.llm as llm  # noqa: E402

if _injected_termcolor:
    del sys.modules["termcolor"]


def test_repo_root_resolves_to_actual_repo():
    """REPO_ROOT must be the real repo root (3 hops), not its parent. These markers
    exist only at the true repo root — under the old 4-hop bug REPO_ROOT was the
    repo's parent dir, where they are absent, so this asserts the off-by-one fix."""
    root = llm.REPO_ROOT
    assert os.path.isdir(os.path.join(root, "tools", "linkedin-scanner")), root
    assert os.path.isfile(os.path.join(root, "requirements.txt")), root


def test_profile_summary_loads_profile_when_present():
    """When data/profile.md exists at REPO_ROOT, profile_summary is its content
    (first 500 chars) — proving the profile actually loads rather than the generic
    fallback. Skipped where the gitignored profile is absent (public template)."""
    profile_path = os.path.join(llm.REPO_ROOT, "data", "profile.md")
    if not os.path.exists(profile_path):
        pytest.skip("data/profile.md not present (public template env)")
    with open(profile_path, encoding="utf-8") as f:
        expected = f.read()[:500]
    assert llm.profile_summary == expected


# ── goals-derived ranking persona (fable-audit #16) ───────────────────────────

def test_target_role_parser_reads_top_ranked_role(tmp_path):
    """_load_target_role extracts the #1 role type and drops the parenthetical."""
    goals = tmp_path / "goals.md"
    goals.write_text(
        "## Target Criteria\n\n"
        "**Role types** (ranked, most to least preferred):\n"
        "1. Deployment Strategist / Engagement Lead at an AI-native company (the judgment seat)\n"
        "2. Business Operations / Strategy & Operations on a strong team\n",
        encoding="utf-8",
    )
    assert llm._load_target_role(str(goals)) == \
        "Deployment Strategist / Engagement Lead at an AI-native company"


def test_target_role_falls_back_when_goals_absent(tmp_path):
    missing = tmp_path / "nope.md"
    assert llm._load_target_role(str(missing)).startswith("the candidate's target role")


def test_template_has_no_hardcoded_cos_persona():
    tmpl = Path(llm.RANK_PROFILE_PROMPT_FILE).read_text(encoding="utf-8")
    assert "Chief of Staff" not in tmpl
    assert "Strategy Ops" not in tmpl
    assert "{target_role}" in tmpl  # the injectable slot exists


def test_assembled_prompt_has_no_unfilled_slots():
    assert "{target_role}" not in llm.rank_profile_prompt
    assert "{profile_summary}" not in llm.rank_profile_prompt
    assert llm.TARGET_ROLE in llm.rank_profile_prompt
