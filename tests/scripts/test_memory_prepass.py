"""Tests for tools/memory_prepass.py — the deterministic stage of memory mining.

The router decides which files an LLM ever sees. If it drops a file into the
DETERMINISTIC lane that actually hides an already-landed promotion, it rebuilds the
exact failure the corrected pipeline exists to prevent (63% wrong verdicts on the
subset where it mattered, 2026-08-13). So the routing rules are pinned by test, and
the chunker is pinned to keep wikilink families intact.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "memory_prepass.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import memory_prepass as mp  # noqa: E402


def write(d: Path, name: str, body: str, tier: str | None = None) -> Path:
    fm = "---\nname: x\nmetadata:\n  type: feedback\n---\n\n"
    if tier:
        body += f"\n\n**Current tier:** {tier}.\n"
    p = d / name
    p.write_text(fm + body, encoding="utf-8")
    return p


def test_single_dated_incident_no_target_is_deterministic(tmp_path):
    p = write(tmp_path, "feedback_a.md", "**Origin:** 2026-05-12 one thing happened.", tier="behavioral")
    fact = mp.analyse(p, REPO_ROOT)
    assert fact["date_count"] == 1
    assert mp.route(fact) == "DETERMINISTIC"


def test_behavioral_tier_claim_alone_does_not_summon_an_agent(tmp_path):
    """'Current tier: behavioral' is the ABSENCE of a promotion, not a claim of one."""
    p = write(tmp_path, "feedback_b.md", "**Origin:** 2026-05-12.", tier="behavioral (intentional)")
    assert mp.route(mp.analyse(p, REPO_ROOT)) == "DETERMINISTIC"


@pytest.mark.parametrize("tier", ["hook (promoted 2026-05-14)", "skill", "framework", "hard-rule", "script tier"])
def test_structural_tier_claim_always_needs_verification(tmp_path, tier):
    p = write(tmp_path, "feedback_c.md", "**Origin:** 2026-05-12.", tier=tier)
    assert mp.route(mp.analyse(p, REPO_ROOT)) == "NEEDS-AGENT"


def test_naming_an_existing_artifact_needs_an_agent(tmp_path):
    """The ghost class: the file exists, but maybe not the mechanism inside it."""
    p = write(tmp_path, "feedback_d.md",
              "**Origin:** 2026-05-12. Target: `tools/check_draft_voice.py` should block it.",
              tier="behavioral")
    fact = mp.analyse(p, REPO_ROOT)
    assert fact["artifact_exists"]["tools/check_draft_voice.py"] is True
    assert mp.route(fact) == "NEEDS-AGENT"


def test_naming_a_nonexistent_artifact_stays_deterministic(tmp_path):
    p = write(tmp_path, "feedback_e.md",
              "**Origin:** 2026-05-12. If it fires again build `tools/check_nonexistent_thing.py`.",
              tier="behavioral")
    fact = mp.analyse(p, REPO_ROOT)
    assert fact["names_existing_artifact"] is False
    assert mp.route(fact) == "DETERMINISTIC"


def test_two_dates_is_not_recurrence_but_a_dated_supplement_is(tmp_path):
    """An origin plus a confirmation date is the norm; a dated supplement header is a fire."""
    plain = write(tmp_path, "feedback_f.md",
                  "**Origin:** 2026-05-12. Confirmed 2026-05-14.", tier="behavioral")
    assert mp.route(mp.analyse(plain, REPO_ROOT)) == "DETERMINISTIC"

    supp = write(tmp_path, "feedback_g.md",
                 "**Origin:** 2026-05-12.\n\n## Extension 2026-05-20: it happened again\n\nmore",
                 tier="behavioral")
    fact = mp.analyse(supp, REPO_ROOT)
    assert fact["dated_supplement_headers"] == ["2026-05-20"]
    assert mp.route(fact) == "NEEDS-AGENT"


@pytest.mark.parametrize("phrase", ["fired 3x", "second documented occurrence", "N=3", "happened again"])
def test_recurrence_phrases_summon_an_agent(tmp_path, phrase):
    p = write(tmp_path, "feedback_h.md", f"**Origin:** 2026-05-12. This {phrase} in practice.",
              tier="behavioral")
    fact = mp.analyse(p, REPO_ROOT)
    assert fact["recurrence_phrases"], phrase
    assert mp.route(fact) == "NEEDS-AGENT"


def test_terminal_behavioral_is_detected(tmp_path):
    p = write(tmp_path, "feedback_i.md",
              "**Origin:** 2026-05-12. Current tier: behavioral (intentional - no structural gate possible).")
    assert mp.analyse(p, REPO_ROOT)["declares_terminal_behavioral"] is True


def test_chunker_keeps_a_wikilink_family_together():
    facts = [
        {"file": "feedback_a.md", "wikilinks": ["feedback_b"]},
        {"file": "feedback_b.md", "wikilinks": []},
        {"file": "feedback_c.md", "wikilinks": []},
        {"file": "feedback_d.md", "wikilinks": []},
    ]
    chunks = mp.family_chunks(facts, size=2)
    home = {n: i for i, c in enumerate(chunks) for n in c}
    assert home["feedback_a.md"] == home["feedback_b.md"], "a linked family was split across agents"


def test_chunker_loses_no_files():
    facts = [{"file": f"feedback_{i}.md", "wikilinks": []} for i in range(37)]
    chunks = mp.family_chunks(facts, size=11)
    flat = [n for c in chunks for n in c]
    assert sorted(flat) == sorted(f["file"] for f in facts)
    assert len(flat) == len(set(flat)), "a file was assigned to two agents"


def test_empty_scope_is_an_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(tmp_path), "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "ZERO files" in json.loads(proc.stdout)["reason"]


def test_global_skill_paths_resolve_outside_the_repo(tmp_path):
    """A skill can live in ~/.claude/skills/, not just the repo.

    Checking only the repo reported 3 live global skills as absent; an agent reading
    that fact would call the rule's target unbuilt. That false-absence is the error
    class this whole pipeline exists to remove, so it is pinned here.
    """
    home_skill = Path.home() / ".claude" / "skills" / "lessons-learned" / "SKILL.md"
    if not home_skill.is_file():
        pytest.skip("global lessons-learned skill absent on this machine — Tier 2 only")
    assert mp.artifact_exists(".claude/skills/lessons-learned/SKILL.md", REPO_ROOT) is True
    assert mp.artifact_exists(".claude/skills/zzz-does-not-exist/SKILL.md", REPO_ROOT) is False


def test_repo_paths_still_resolve(tmp_path):
    assert mp.artifact_exists("tools/sweep.py", REPO_ROOT) is True
    assert mp.artifact_exists("tools/zzz_no_such_tool.py", REPO_ROOT) is False
