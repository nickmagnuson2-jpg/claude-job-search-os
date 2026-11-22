"""Tests for tools/learning_cutover.py: the hash-bound cutover report and its verify check.

The report is what Nick approves; the hash in its header binds that approval to the exact
memory corpus and proposal inputs it was computed from. These tests pin the two properties
that make the binding real: any changed byte changes the hash, and verify refuses on drift.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import learning_cutover as lc  # noqa: E402

RULE = """---
name: {name}
description: d
metadata:
  type: feedback
  occurrences: {occ}
  promoted: {promoted}
  reopen_gate: "{gate}"
---
body
"""

LESSONS = """# Lessons

## Section 2

| Pattern | Rule | Occurrences | Promoted | Date |
|---|---|---|---|---|
| p1 | r1 | 2 | No | 2026-01-01 |
| p2 | r2 | 1 | Superseded | 2026-01-02 |
"""

FRICTION = """# Friction

## Entries

| Date | Surface | Nature | Fix | Occurrences | Promotion |
|---|---|---|---|---|---|
| 2026-01-01 | tool.py | broke | did x | 2 | feedback |
"""


@pytest.fixture
def world(tmp_path):
    mem = tmp_path / "mem"
    mem.mkdir()
    repo = tmp_path / "repo"
    (repo / "memory").mkdir(parents=True)
    (repo / "memory" / "lessons.md").write_text(LESSONS, encoding="utf-8")
    (repo / "memory" / "friction-log.md").write_text(FRICTION, encoding="utf-8")
    (mem / "feedback_alpha.md").write_text(
        RULE.format(name="feedback_alpha", occ=3, promoted="no", gate="3rd fire"), encoding="utf-8")
    (mem / "feedback_beta.md").write_text(
        RULE.format(name="feedback_beta", occ=1, promoted="no", gate="2nd fire"), encoding="utf-8")
    (mem / "MEMORY.md").write_text("index\n", encoding="utf-8")
    props = tmp_path / "props"
    props.mkdir()
    reopen = props / "reopen.jsonl"
    reopen.write_text(
        json.dumps({"file": "feedback_alpha.md", "occurrences": 3, "proposed_reopen_at": 3,
                    "status": "number", "prose_fragment": "3rd fire", "confidence": "high",
                    "note": ""}) + "\n"
        + json.dumps({"file": "feedback_beta.md", "occurrences": 1, "proposed_reopen_at": None,
                      "status": "relative", "prose_fragment": "2nd fire", "confidence": "low",
                      "note": "no baseline"}) + "\n", encoding="utf-8")
    inputs = {"reopen_at": reopen, "frontmatter": None, "lessons": None, "i7": None,
              "friction_kinds": None}
    return mem, repo, inputs


def _digest(world):
    mem, repo, inputs = world
    return lc.cutover_digest(mem, repo, inputs)


def test_digest_is_deterministic(world):
    assert _digest(world) == _digest(world)
    assert len(_digest(world)) == 64


@pytest.mark.parametrize("target", ["rule", "lessons", "input"])
def test_one_changed_byte_changes_the_digest(world, target):
    mem, repo, inputs = world
    before = _digest(world)
    path = {"rule": mem / "feedback_alpha.md",
            "lessons": repo / "memory" / "lessons.md",
            "input": inputs["reopen_at"]}[target]
    raw = bytearray(path.read_bytes())
    raw[-2] = ord("x") if raw[-2] != ord("x") else ord("y")
    path.write_bytes(bytes(raw))
    assert _digest(world) != before


def test_a_new_rule_file_changes_the_digest(world):
    mem, _, _ = world
    before = _digest(world)
    (mem / "reference_gamma.md").write_text("---\nname: g\n---\n", encoding="utf-8")
    assert _digest(world) != before


def test_a_deleted_rule_file_changes_the_digest(world):
    mem, _, _ = world
    before = _digest(world)
    (mem / "feedback_beta.md").unlink()
    assert _digest(world) != before


def test_files_outside_the_rule_scope_do_not_change_the_digest(world):
    mem, _, _ = world
    before = _digest(world)
    (mem / "MEMORY.md").write_text("changed index\n", encoding="utf-8")
    (mem / "index-tools.md").write_text("shard\n", encoding="utf-8")
    assert _digest(world) == before


def test_a_missing_input_that_appears_later_changes_the_digest(world):
    mem, repo, inputs = world
    before = _digest(world)
    late = inputs["reopen_at"].parent / "i7.jsonl"
    late.write_text("", encoding="utf-8")
    inputs["i7"] = late
    assert lc.cutover_digest(mem, repo, inputs) != before


def test_a_named_input_that_does_not_exist_is_an_error(world):
    mem, repo, inputs = world
    inputs["i7"] = inputs["reopen_at"].parent / "nope.jsonl"
    with pytest.raises(FileNotFoundError):
        lc.cutover_digest(mem, repo, inputs)


def test_a_missing_lessons_file_is_an_error_not_a_digest(world):
    mem, repo, inputs = world
    (repo / "memory" / "lessons.md").unlink()
    with pytest.raises(FileNotFoundError):
        lc.cutover_digest(mem, repo, inputs)


def test_verify_passes_on_the_same_state(world):
    mem, repo, inputs = world
    ok, info = lc.verify(mem, repo, inputs, _digest(world))
    assert ok is True
    assert info["actual"] == info["expected"]


def test_verify_refuses_on_drift_and_names_the_changed_path(world):
    mem, repo, inputs = world
    expected = _digest(world)
    baseline = lc.entries(mem, repo, inputs)
    (mem / "feedback_alpha.md").write_text(
        RULE.format(name="feedback_alpha", occ=4, promoted="no", gate="3rd fire"), encoding="utf-8")
    ok, info = lc.verify(mem, repo, inputs, expected, baseline=baseline)
    assert ok is False
    assert info["actual"] != expected
    assert "memory/feedback_alpha.md" in info["changed"]


def test_verify_cli_exit_codes(world, capsys):
    mem, repo, inputs = world
    expected = _digest(world)
    args = ["verify", "--memory-dir", str(mem), "--repo-root", str(repo),
            "--reopen-at", str(inputs["reopen_at"]), "--no-default-inputs"]
    assert lc.main(args + ["--expect-sha", expected]) == 0
    assert lc.main(args + ["--expect-sha", "0" * 64]) == 1
    out = capsys.readouterr().out
    assert "refused" in out


def _report(world, tmp_path):
    mem, repo, inputs = world
    out = tmp_path / "report.md"
    text, digest = lc.build_report(mem, repo, inputs)
    out.write_text(text, encoding="utf-8")
    return text, digest


def test_report_header_carries_the_digest_and_every_input_sha(world, tmp_path):
    text, digest = _report(world, tmp_path)
    head = text.split("\n## ", 1)[0]
    assert digest in head
    assert lc.file_sha(world[2]["reopen_at"]) in head
    assert "friction_kinds: input missing" in head


def test_report_lists_default_rule_counts_before_proposals(world, tmp_path):
    text, _ = _report(world, tmp_path)
    assert text.index("## Default rules") < text.index("## Proposed changes")


def test_report_flags_low_and_relative_reopen_rows_for_nick(world, tmp_path):
    text, _ = _report(world, tmp_path)
    flagged = text.split("## For Nick", 1)[1].split("\n## ", 1)[0]
    assert "feedback_beta.md" in flagged
    assert "feedback_alpha.md" not in flagged


def test_report_does_not_guess_friction_kinds_when_the_input_is_missing(world, tmp_path):
    text, _ = _report(world, tmp_path)
    fr = text.split("### Friction", 1)[1].split("\n### ", 1)[0]
    assert "input missing" in fr


def test_report_applies_nothing(world, tmp_path):
    mem, repo, _ = world
    snap = {p: p.read_bytes() for p in list(mem.iterdir()) + list((repo / "memory").iterdir())}
    _report(world, tmp_path)
    assert {p: p.read_bytes() for p in snap} == snap
    assert sorted(mem.iterdir()) == sorted(p for p in snap if p.parent == mem)


def test_memory_after_state_uses_the_proposed_reopen_at(world, tmp_path):
    mem, repo, inputs = world
    rows = lc.memory_transitions(mem, repo, lc.load_reopen(inputs["reopen_at"]), {})
    after = {r["file"]: r["after"] for r in rows}
    assert after["feedback_alpha.md"] == "due"          # occ 3 >= reopen_at 3
    assert after["feedback_beta.md"] == "not_due (below 2)"  # open, occ 1: not a defect


def test_frontmatter_repair_with_a_stale_sha_is_flagged(world, tmp_path):
    mem, repo, inputs = world
    md = tmp_path / "props" / "fm.md"
    md.write_text(
        "## 1. `feedback_alpha.md`\n\n- sha256 of the current file: `" + "0" * 64 + "`\n\n"
        "Current line:\n\n```yaml\n  promoted: no\n```\n\nProposed line:\n\n```yaml\n"
        "  promoted: 'no'\n```\n", encoding="utf-8")
    repairs = lc.load_frontmatter_repairs(md, mem)
    assert repairs[0]["stale"] is True


def test_verify_cli_names_the_changed_file_from_the_sidecar(world, tmp_path, capsys):
    mem, repo, inputs = world
    common = ["--memory-dir", str(mem), "--repo-root", str(repo),
              "--reopen-at", str(inputs["reopen_at"]), "--no-default-inputs"]
    out = tmp_path / "r.md"
    assert lc.main(["report", *common, "--out", str(out)]) == 0
    sha = json.loads(capsys.readouterr().out)["sha256"]
    (repo / "memory/lessons.md").write_text(LESSONS + "\n", encoding="utf-8")
    assert lc.main(["verify", *common, "--expect-sha", sha,
                    "--entries", str(out.with_suffix(".entries.json"))]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "refused" and res["changed"] == ["repo/memory/lessons.md"]



def test_the_friction_ledger_is_not_in_the_digest(world):
    _, repo, _ = world
    before = _digest(world)
    (repo / "memory/friction-log.md").write_text(FRICTION + "| 2026-02-02 | x | y | z | 1 | - |\n",
                                                 encoding="utf-8")
    assert _digest(world) == before
    (repo / "memory/friction-log.md").unlink()
    assert _digest(world) == before


def test_the_friction_dry_run_is_not_in_the_digest(world):
    mem, repo, inputs = world
    before = _digest(world)
    inputs["friction_kinds"] = _kinds(world, "f" * 64)
    assert lc.cutover_digest(mem, repo, inputs) == before


def _kinds(world, bound_sha):
    path = world[2]["reopen_at"].parent / "kinds.json"
    path.write_text(json.dumps({"summary": {"sha256": bound_sha, "rules": [
        {"rule": "default-friction", "rows": 7, "kind": "friction"},
        {"rule": "fp-nature", "rows": 2, "kind": "false-positive-block"}]}, "rows": []}),
        encoding="utf-8")
    world[2]["friction_kinds"] = path
    return path


def test_report_shows_friction_rules_labelled_approximate(world, tmp_path):
    _, repo, _ = world
    _kinds(world, lc.file_sha(repo / "memory/friction-log.md"))
    text, _ = _report(world, tmp_path)
    fr = text.split("### Friction", 1)[1].split("\n## ", 1)[0]
    assert "approximate" in text.split("### Friction", 1)[1].split("\n", 1)[0]
    assert "| fp-nature | 2 | false-positive-block |" in fr
    assert "as it is now" in fr


def test_report_says_when_the_friction_dry_run_predates_the_ledger(world, tmp_path):
    _kinds(world, "f" * 64)
    text, _ = _report(world, tmp_path)
    assert "has since changed" in text.split("### Friction", 1)[1]


def test_a_candidate_with_no_proposal_is_no_number_and_listed(world, tmp_path):
    mem, _, _ = world
    (mem / "feedback_gamma.md").write_text(
        RULE.format(name="feedback_gamma", occ=4, promoted="partial", gate="5th fire"), encoding="utf-8")
    text, _ = _report(world, tmp_path)
    assert "0 proposed with no number, 1 with no proposal" in text
    block = text.split("**Candidates with no reopen_at proposal (1):**", 1)[1].split("**Defects", 1)[0]
    assert "`feedback_gamma.md`" in block
    assert "feedback_alpha.md" not in block


def test_report_lists_defects_as_known_migration_items(world, tmp_path):
    mem, _, _ = world
    (mem / "feedback_delta.md").write_text("no frontmatter here\n", encoding="utf-8")
    text, _ = _report(world, tmp_path)
    block = text.split("**Defects (known migration items):**", 1)[1].split("\n## ", 1)[0]
    assert "`feedback_delta.md`: defect: frontmatter (no_frontmatter)" in block
