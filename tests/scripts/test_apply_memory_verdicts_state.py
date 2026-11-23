"""tests for apply_memory_verdicts.py on the learning-loop state matrix (2026-11-22, B2).

Every verdict now passes memory_frontmatter.check_text(new, original) before it is
written: a token outside the vocabulary, a pointer with no target, or an `exit_path:
skill` claim without its record is refused and the file is left alone. The three new
keys (reopen_at, skill_file, skill_step) can be set and created. Fixtures are invented.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "apply_memory_verdicts.py"

FILE = """---
name: feedback_widget_rule
description: "An invented rule."
metadata:
  node_type: memory
  type: feedback
  occurrences: 2
  promoted: no
  reopen_gate: "REOPEN on the 3rd fire"
---

**Rule:** invented prose that must never be touched.
"""


@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_widget_rule.md").write_text(FILE, encoding="utf-8")
    (d / "feedback_gadget_rule.md").write_text(FILE.replace("widget", "gadget"), encoding="utf-8")
    return d


def run(memdir: Path, verdict_line: str, tmp_path: Path, apply=True):
    v = tmp_path / "verdicts.txt"
    v.write_text(verdict_line + "\n", encoding="utf-8")
    args = [sys.executable, str(SCRIPT), "--memory-dir", str(memdir), "--verdicts", str(v)]
    if apply:
        args.append("--apply")
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout)


def body(corpus):
    return (corpus / "feedback_widget_rule.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("pair", [
    'promoted="maybe later"',
    'promoted="merged"',
    'promoted="merged into feedback_absent_rule.md"',
    'promoted="superseded"',
])
def test_a_value_the_validator_rejects_is_not_written(corpus, tmp_path, pair):
    rc, rep = run(corpus, f"feedback_widget_rule.md\t{pair}", tmp_path)
    assert rc == 1
    assert rep["changed"] == 0
    assert len(rep["failed"]) == 1
    assert rep["failed"][0].startswith(
        "feedback_widget_rule.md: refused by the state validator -- not written: ")
    assert body(corpus) == FILE


def test_a_valid_pointer_is_written(corpus, tmp_path):
    rc, rep = run(corpus, 'feedback_widget_rule.md\tpromoted="merged into feedback_gadget_rule.md"',
                  tmp_path)
    assert rc == 0, rep
    assert rep["changed"] == 1
    assert ('promoted: "merged into feedback_gadget_rule.md"' in body(corpus)
            or "promoted: merged into feedback_gadget_rule.md" in body(corpus))


def test_reopen_at_is_created_after_reopen_gate(corpus, tmp_path):
    rc, rep = run(corpus, "feedback_widget_rule.md\treopen_at=3", tmp_path)
    assert rc == 0, rep
    lines = body(corpus).splitlines()
    i = lines.index('  reopen_gate: "REOPEN on the 3rd fire"')
    assert lines[i + 1] == "  reopen_at: 3"


def test_a_bad_reopen_at_is_refused(corpus, tmp_path):
    rc, rep = run(corpus, "feedback_widget_rule.md\treopen_at=soon", tmp_path)
    assert rc == 1
    assert "refused by the state validator" in rep["failed"][0]
    assert body(corpus) == FILE


def test_a_skill_exit_without_its_record_is_refused(corpus, tmp_path):
    rc, rep = run(corpus, 'feedback_widget_rule.md\tpromoted="skill, 2026-11-22"\texit_path=skill',
                  tmp_path)
    assert rc == 1
    assert "refused by the state validator" in rep["failed"][0]
    assert body(corpus) == FILE


def test_a_skill_exit_with_its_record_is_written(corpus, tmp_path):
    line = ('feedback_widget_rule.md\tpromoted="skill, 2026-11-22"\texit_path=skill'
            '\tskill_file=.claude/skills/standup/SKILL.md\tskill_step=Step 2: show the flagged count')
    rc, rep = run(corpus, line, tmp_path)
    assert rc == 0, rep
    text = body(corpus)
    assert '  skill_file: ".claude/skills/standup/SKILL.md"' in text
    assert '  skill_step: "Step 2: show the flagged count"' in text
    assert text.endswith("**Rule:** invented prose that must never be touched.\n")


def test_an_older_defect_in_an_untouched_key_does_not_block(corpus, tmp_path):
    broken = FILE.replace("  promoted: no", "  promoted: someday")
    (corpus / "feedback_widget_rule.md").write_text(broken, encoding="utf-8")
    rc, rep = run(corpus, "feedback_widget_rule.md\treopen_at=3", tmp_path)
    assert rc == 0, rep
    assert "  reopen_at: 3" in body(corpus)


def test_the_validator_runs_in_dry_run_too(corpus, tmp_path):
    rc, rep = run(corpus, 'feedback_widget_rule.md\tpromoted="maybe later"', tmp_path, apply=False)
    assert rc == 1
    assert rep["changed"] == 0
    assert "refused by the state validator" in rep["failed"][0]


def test_new_keys_are_in_the_allowed_and_insertable_sets():
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import apply_memory_verdicts as amv
    for k in ("reopen_at", "skill_file", "skill_step"):
        assert k in amv.ALLOWED_KEYS
        assert k in amv.INSERTABLE_KEYS
        assert k in amv.INSERT_AFTER
    assert {"skill_file", "skill_step"} <= amv.QUOTED_KEYS
    assert "reopen_at" not in amv.QUOTED_KEYS
