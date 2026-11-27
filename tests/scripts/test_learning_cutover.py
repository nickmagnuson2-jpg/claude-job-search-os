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
    return _build_world(tmp_path)


def _build_world(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_digest_depends_on_content_not_on_where_or_when_it_is_read(tmp_path):
    """The same corpus built in two different folders gives the same digest, a 64-hex
    sha256. Replaces a test that compared one digest with itself, which could not fail
    (flagged by every S1 mutation record, 2026-11-25): this one fails if an absolute path,
    a directory listing order or a per-run value leaks into the digest."""
    a = _digest(_build_world(tmp_path / "a"))
    b = _digest(_build_world(tmp_path / "elsewhere" / "b"))
    assert a == b
    assert len(a) == 64 and int(a, 16) >= 0


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


# ---------------------------------------------------------------- pinned outputs (mutation r1)

FULL_RULES = {
    "feedback_closed.md": RULE.format(name="c", occ=3, promoted="yes - hook", gate="2nd fire"),
    "feedback_term.md": RULE.format(name="t", occ=3, promoted="no", gate="2nd fire") .replace(
        "  promoted: no", "  promoted: no\n  terminal: yes"),
    "feedback_na.md": RULE.format(name="n", occ=3, promoted="n/a", gate="2nd fire"),
    "feedback_low.md": RULE.format(name="l", occ=1, promoted="no", gate="2nd fire"),
}


def test_memory_transitions_pin_before_and_after_per_rule(world):
    mem, repo, inputs = world
    for name, text in FULL_RULES.items():
        (mem / name).write_text(text, encoding="utf-8")
    (mem / "MEMORY.md").write_text("not a rule\n", encoding="utf-8")
    rows = {r["file"]: (r["before"], r["after"])
            for r in lc.memory_transitions(mem, repo, lc.load_reopen(inputs["reopen_at"]), {})}
    assert rows == {
        "feedback_alpha.md": ("due", "due"),
        "feedback_beta.md": ("open (<2, not scanned)", "not_due (below 2)"),
        "feedback_closed.md": ("closed", "closed"),
        "feedback_term.md": ("terminal", "terminal"),
        "feedback_na.md": ("closed", "defect: promoted"),
        "feedback_low.md": ("open (<2, not scanned)", "not_due (below 2)"),
    }


def test_a_repair_is_applied_in_memory_only_when_current(world, tmp_path):
    mem, repo, inputs = world
    bad = RULE.format(name="b", occ=3, promoted="no", gate="2nd fire").replace(
        "description: d", "description: a: b")
    (mem / "feedback_bad.md").write_text(bad, encoding="utf-8")
    md = tmp_path / "props" / "fm.md"

    def write(sha):
        md.write_text(
            "## 1. `feedback_bad.md`\n\n- sha256 of the current file: `" + sha + "`\n\n"
            "Current line:\n\n```yaml\ndescription: a: b\n```\n\nProposed line:\n\n```yaml\n"
            "description: 'a: b'\n```\n", encoding="utf-8")
        return lc.load_frontmatter_repairs(md, mem)

    current = write(lc.file_sha(mem / "feedback_bad.md"))
    assert current[0]["stale"] is False and current[0]["applies"] is True
    after = {r["file"]: r["after"] for r in lc.memory_transitions(mem, repo, [], current)}
    assert after["feedback_bad.md"] == "no_number"
    stale = write("0" * 64)
    after = {r["file"]: r["after"] for r in lc.memory_transitions(mem, repo, [], stale)}
    assert after["feedback_bad.md"].startswith("defect: frontmatter")
    assert (mem / "feedback_bad.md").read_text(encoding="utf-8") == bad


def test_a_repair_whose_old_line_is_absent_does_not_apply(world, tmp_path):
    mem, _, _ = world
    md = tmp_path / "props" / "fm.md"
    md.write_text(
        "## 1. `feedback_alpha.md`\n\n- sha256 of the current file: `"
        + lc.file_sha(mem / "feedback_alpha.md") + "`\n\nCurrent line:\n\n```yaml\n  nope: 1\n```\n\n"
        "Proposed line:\n\n```yaml\n  nope: 2\n```\n", encoding="utf-8")
    assert lc.load_frontmatter_repairs(md, mem)[0]["applies"] is False
    assert lc.load_frontmatter_repairs(None, mem) == []


LESSONS_FULL = LESSONS + "| p3 | r3 | 1 | No | 2026-01-03 | <!-- tail -->\n"


def _lessons_props(tmp_path, sha, old, new, line):
    md = tmp_path / "props" / "les.md"
    md.write_text(f"**sha256 when these were made:** `{sha}`\n\n## L{line}\n\nCurrent line:\n\n```text\n"
                  f"{old}\n```\n\nProposed line:\n\n```text\n{new}\n```\n", encoding="utf-8")
    return md


def test_lessons_transitions_pin_states_and_apply_a_current_repair(world, tmp_path):
    mem, repo, _ = world
    (repo / "memory/lessons.md").write_text(LESSONS_FULL, encoding="utf-8")
    old = "| p3 | r3 | 1 | No | 2026-01-03 | <!-- tail -->"
    md = _lessons_props(tmp_path, lc.file_sha(repo / "memory/lessons.md"), old,
                        "| p3 | r3 | 1 | No | 2026-01-03 <!-- tail --> |", 9)
    repairs, stale = lc.load_lessons_repairs(md, repo)
    assert stale is False and repairs == [{"line": 9, "old": old, "applies": True,
                                           "new": "| p3 | r3 | 1 | No | 2026-01-03 <!-- tail --> |"}]
    rows = lc.lessons_transitions(repo, mem, repairs, stale)
    assert rows == [{"line": 7, "before": "open", "after": "open/due", "defect_kinds": ()},
                    {"line": 8, "before": "open", "after": "defect", "defect_kinds": ("promoted",)},
                    {"line": 9, "before": "open", "after": "open/not_due", "defect_kinds": ()}]
    unapplied = lc.lessons_transitions(repo, mem, repairs, True)
    assert unapplied[2] == {"line": 9, "before": "open", "after": "defect: row", "defect_kinds": ("row",)}


def test_lessons_repairs_bound_to_another_file_are_stale(world, tmp_path):
    _, repo, _ = world
    md = _lessons_props(tmp_path, "0" * 64, "a", "b", 7)
    assert lc.load_lessons_repairs(md, repo)[1] is True
    assert lc.load_lessons_repairs(None, repo) == ([], None)


def test_lessons_without_section_2_yields_no_rows(world):
    mem, repo, _ = world
    (repo / "memory/lessons.md").write_text("# Lessons\n| a | b | c | d | e |\n", encoding="utf-8")
    assert lc.lessons_transitions(repo, mem, [], None) == []


def test_closed_lesson_reads_closed_before_and_after(world):
    mem, repo, _ = world
    (repo / "memory/lessons.md").write_text(
        LESSONS.replace("| p1 | r1 | 2 | No |", "| p1 | r1 | 2 | Yes - skill |"), encoding="utf-8")
    assert lc.lessons_transitions(repo, mem, [], None)[0] == {"line": 7, "before": "closed", "after": "closed",
                                                              "defect_kinds": ()}


def test_report_proposed_changes_section_pins_every_count(world, tmp_path):
    mem, repo, inputs = world
    inputs["i7"] = tmp_path / "props" / "i7.jsonl"
    inputs["i7"].write_text('{"disposition": "GATE"}\n{"disposition": "SKILL"}\n{"disposition": "SKILL"}\n',
                            encoding="utf-8")
    inputs["lessons"] = _lessons_props(tmp_path, lc.file_sha(repo / "memory/lessons.md"), "x", "y", 8)
    text, _ = _report(world, tmp_path)
    assert "### reopen_at\n2 rows. Status: number 1, relative 1. Confidence: high 1, low 1." in text
    assert "### Lessons repairs\n1 rows: L8." in text
    assert "### I7 dispositions\n3 rows. GATE 1, SKILL 2." in text
    assert "### Frontmatter repairs\ninput missing" in text
    assert "1 distinct rows (1 low confidence, 1 relative, 1 both)" in text
    assert "| feedback_beta.md" not in text and "| `feedback_beta.md` | 1 | None | relative | low |" in text


def test_report_marks_each_missing_input(world, tmp_path):
    mem, repo, inputs = world
    for k in inputs:
        inputs[k] = None
    text, _ = _report(world, tmp_path)
    for sec in ("reopen_at", "Frontmatter repairs", "Lessons repairs", "I7 dispositions"):
        assert f"### {sec}\ninput missing" in text
    assert "  - reopen_at: input missing" in text


def test_report_frontmatter_section_counts_stale_and_unapplied(world, tmp_path):
    mem, repo, inputs = world
    md = tmp_path / "props" / "fm.md"
    md.write_text(
        "## 1. `feedback_alpha.md`\n\n- sha256 of the current file: `" + "0" * 64 + "`\n\n"
        "Current line:\n\n```yaml\n  nope: 1\n```\n\nProposed line:\n\n```yaml\n  nope: 2\n```\n",
        encoding="utf-8")
    inputs["frontmatter"] = md
    text, _ = _report(world, tmp_path)
    assert "1 repairs. Stale (file changed since proposed): 1. Old line not found exactly once: 1." in text
    assert "- `feedback_alpha.md` STALE DOES NOT APPLY" in text


def test_report_memory_table_lists_every_transition(world, tmp_path):
    text, _ = _report(world, tmp_path)
    table = text.split("### Memory rules", 1)[1].split("###", 1)[0]
    assert "| due | due | 1 |" in table
    assert "| open (<2, not scanned) | not_due (below 2) | 1 |" in table


def test_bad_friction_kinds_shape_is_an_error(world, tmp_path):
    _, repo, _ = world
    p = tmp_path / "props" / "k.json"
    p.write_text('{"rows": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        lc.load_friction_kinds(p, repo)
    assert lc.load_friction_kinds(None, repo) is None


def test_missing_inputs_raise_with_a_named_reason(world):
    mem, repo, inputs = world
    inputs["i7"] = repo / "nope.jsonl"
    with pytest.raises(FileNotFoundError, match="named but missing"):
        lc.entries(mem, repo, inputs)
    inputs["i7"] = None
    (repo / "memory/lessons.md").unlink()
    with pytest.raises(FileNotFoundError, match="corpus file missing"):
        lc.entries(mem, repo, inputs)


def test_load_reopen_of_no_input_is_empty(world):
    assert lc.load_reopen(None) == []
    assert lc.load_jsonl(None) == []
    assert len(lc.load_jsonl(world[2]["reopen_at"])) == 2


def test_report_cli_writes_the_report_and_uses_default_inputs(world, tmp_path, capsys):
    mem, repo, inputs = world
    dflt = repo / lc.DEFAULT_INPUTS["reopen_at"]
    dflt.parent.mkdir(parents=True)
    dflt.write_bytes(inputs["reopen_at"].read_bytes())
    out = tmp_path / "r.md"
    assert lc.main(["report", "--memory-dir", str(mem), "--repo-root", str(repo), "--out", str(out)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert out.read_text(encoding="utf-8").count(res["sha256"]) == 1
    assert f"reopen_at: `{lc.file_sha(dflt)}`" in out.read_text(encoding="utf-8")
    assert lc.main(["report", "--memory-dir", str(mem), "--repo-root", str(repo), "--out", str(out),
                    "--no-default-inputs"]) == 0
    assert "  - reopen_at: input missing" in out.read_text(encoding="utf-8")


def test_for_nick_counts_low_and_relative_separately(world, tmp_path):
    rows = [{"file": f"feedback_{n}.md", "occurrences": 2, "proposed_reopen_at": p, "status": s,
             "prose_fragment": "x", "confidence": c, "note": ""}
            for n, p, s, c in (("a", 2, "number", "low"), ("b", 3, "number", "low"),
                               ("c", None, "relative", "med"), ("d", 2, "number", "high"),
                               ("e", 2, "number", "high"))]
    world[2]["reopen_at"].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    text, _ = _report(world, tmp_path)
    assert "3 distinct rows (2 low confidence, 1 relative, 0 both)" in text


# ---------------------------------------------------------------- Codex B7 r1 F1-F7

def test_F1_a_file_changed_during_the_scan_refuses(world, monkeypatch):
    mem, repo, inputs = world
    expected = _digest(world)
    real = lc.file_sha
    state = {"done": False}

    def racing(path):
        if Path(path).name == "feedback_beta.md" and not state["done"]:
            state["done"] = True
            (mem / "feedback_alpha.md").write_text("changed mid-scan\n", encoding="utf-8")
            return real(path)
        return real(path)

    # The first pass hashed alpha before beta; alpha then changes. A single pass would
    # still report the old digest.
    (mem / "feedback_alpha.md").write_bytes((mem / "feedback_alpha.md").read_bytes())
    monkeypatch.setattr(lc, "file_sha", racing)
    ok, info = lc.verify(mem, repo, inputs, expected)
    assert ok is False and info["moved_during_scan"] is True
    assert info["changed"] == ["memory/feedback_alpha.md"]


def test_F2_before_uses_todays_reader_alone(world):
    mem, repo, _ = world
    (repo / "memory/lessons.md").write_text(
        LESSONS + "| p | r | 2 | Yes - x | 2026-01-01 | extra |\n| a | b | c |\n", encoding="utf-8")
    rows = lc.lessons_transitions(repo, mem, [], None)
    assert rows[2]["before"] == "closed" and rows[2]["after"] == "defect: row"
    assert rows[3]["before"] == "not read (fewer than 5 cells)"


def test_F3_every_repair_for_one_file_is_applied_in_order(world, tmp_path):
    mem, repo, _ = world
    bad = RULE.format(name="b", occ=3, promoted="no", gate="x").replace(
        "description: d", "description: a: b").replace("  type: feedback", "  type: f: g")
    (mem / "feedback_bad.md").write_text(bad, encoding="utf-8")
    sha = lc.file_sha(mem / "feedback_bad.md")
    md = tmp_path / "props" / "fm.md"
    sec = ("## {n}. `feedback_bad.md`\n\n- sha256 of the current file: `" + sha + "`\n\n"
           "Current line:\n\n```yaml\n{old}\n```\n\nProposed line:\n\n```yaml\n{new}\n```\n\n")
    md.write_text(sec.format(n=1, old="description: a: b", new="description: 'a: b'")
                  + sec.format(n=2, old="  type: f: g", new="  type: 'f: g'"), encoding="utf-8")
    repairs = lc.load_frontmatter_repairs(md, mem)
    assert len(repairs) == 2
    row = next(r for r in lc.memory_transitions(mem, repo, [], repairs) if r["file"] == "feedback_bad.md")
    assert row["unapplied"] == 0 and row["after"] == "no_number"


def test_F4_report_refuses_to_write_into_the_corpus(world, capsys):
    mem, repo, inputs = world
    common = ["--memory-dir", str(mem), "--repo-root", str(repo), "--no-default-inputs"]
    before = (repo / "memory/lessons.md").read_bytes()
    assert lc.main(["report", *common, "--out", str(repo / "memory/lessons.md")]) == 2
    assert lc.main(["report", *common, "--out", str(mem / "feedback_alpha.md")]) == 2
    assert (repo / "memory/lessons.md").read_bytes() == before
    assert "refused" in capsys.readouterr().out


def test_F5_a_section_that_does_not_parse_is_an_error(world, tmp_path):
    mem, repo, _ = world
    md = tmp_path / "props" / "fm.md"
    md.write_text("## 1. `feedback_alpha.md`\n\nno sha, no fences\n\n## 2. `feedback_beta.md`\n\n"
                  "- sha256 of the current file: `" + "0" * 64 + "`\n\nCurrent line:\n\n```yaml\na\n```"
                  "\n\nProposed line:\n\n```yaml\nb\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not parse"):
        lc.load_frontmatter_repairs(md, mem)
    les = _lessons_props(tmp_path, "0" * 64, "a", "b", 7)
    les.write_text(les.read_text(encoding="utf-8") + "\n## L8\n\nbroken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not parse"):
        lc.load_lessons_repairs(les, repo)


def test_F6_a_lessons_repair_whose_line_differs_does_not_apply_and_is_reported(world, tmp_path):
    mem, repo, inputs = world
    inputs["lessons"] = _lessons_props(tmp_path, lc.file_sha(repo / "memory/lessons.md"),
                                       "| not | the | row | No | 2026-01-01 |", "| x | y | 1 | No | 2026-01-01 |", 7)
    repairs, _ = lc.load_lessons_repairs(inputs["lessons"], repo)
    assert repairs[0]["applies"] is False
    assert lc.lessons_transitions(repo, mem, repairs, False)[0]["after"] == "open/due"
    text, _ = _report(world, tmp_path)
    assert "DOES NOT APPLY (the named line is not the old text): 1 (L7)" in text


def test_F7_only_promoted_defects_count_as_unknown_tokens(world, tmp_path):
    _, repo, _ = world
    (repo / "memory/lessons.md").write_text(
        LESSONS.replace("| p2 | r2 | 1 | Superseded | 2026-01-02 |", "| p2 | r2 | 1 | No | not-a-date |"),
        encoding="utf-8")
    text, _ = _report(world, tmp_path)
    assert "pointer token without a pointer: defect, stays open | 0 |" in text


def test_F1_report_refuses_when_the_corpus_moves_while_it_is_built(world, monkeypatch):
    mem, repo, inputs = world
    real = lc.memory_transitions

    def moving(*a, **k):
        rows = real(*a, **k)
        (mem / "feedback_alpha.md").write_text("moved\n", encoding="utf-8")
        return rows

    monkeypatch.setattr(lc, "memory_transitions", moving)
    with pytest.raises(lc.CorpusMoved, match="changed during the scan"):
        lc.build_report(mem, repo, inputs)


def test_F6_no_does_not_apply_line_when_every_lessons_repair_applies(world, tmp_path):
    _, repo, inputs = world
    inputs["lessons"] = _lessons_props(tmp_path, lc.file_sha(repo / "memory/lessons.md"),
                                       "| p1 | r1 | 2 | No | 2026-01-01 |", "| p1 | r1 | 2 | No | 2026-01-01 |", 7)
    text, _ = _report(world, tmp_path)
    assert "DOES NOT APPLY" not in text.split("### Lessons repairs", 1)[1].split("###", 1)[0]


# ---------------------------------------------------------------- Grok B7 r1 F2-F8

def test_G2_the_sidecar_is_the_hashed_map(world, tmp_path, monkeypatch, capsys):
    mem, repo, inputs = world
    real = lc.build_report_entries

    def then_move(*a, **k):
        out = real(*a, **k)
        (mem / "feedback_alpha.md").write_text("moved after hashing\n", encoding="utf-8")
        return out

    monkeypatch.setattr(lc, "build_report_entries", then_move)
    out = tmp_path / "r.md"
    assert lc.main(["report", "--memory-dir", str(mem), "--repo-root", str(repo), "--out", str(out),
                    "--reopen-at", str(inputs["reopen_at"]), "--no-default-inputs"]) == 0
    res = json.loads(capsys.readouterr().out)
    side = json.loads(out.with_suffix(".entries.json").read_text(encoding="utf-8"))
    assert lc.digest_of(side) == res["sha256"]


def test_G3_report_refuses_a_hard_link_to_the_corpus(world, tmp_path, capsys):
    mem, repo, _ = world
    link = tmp_path / "innocent.md"
    os_link = __import__("os").link
    os_link(repo / "memory/lessons.md", link)
    before = (repo / "memory/lessons.md").read_bytes()
    assert lc.main(["report", "--memory-dir", str(mem), "--repo-root", str(repo), "--out", str(link),
                    "--no-default-inputs"]) == 2
    assert (repo / "memory/lessons.md").read_bytes() == before


def _fm_section(n, f, sha, old="a", new="b"):
    return (f"## {n}. `{f}`\n\n- sha256 of the current file: `{sha}`\n\nCurrent line:\n\n```yaml\n{old}\n```"
            f"\n\nProposed line:\n\n```yaml\n{new}\n```\n\n")


def test_G4_a_second_pair_under_one_heading_is_an_error(world, tmp_path):
    mem, _, _ = world
    md = tmp_path / "props" / "fm.md"
    sec = _fm_section(1, "feedback_alpha.md", "0" * 64)
    md.write_text(sec + "Current line:\n\n```yaml\nc\n```\n\nProposed line:\n\n```yaml\nd\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not parse"):
        lc.load_frontmatter_repairs(md, mem)


def test_G5_a_repair_for_a_file_outside_the_hashed_scope_is_an_error(world, tmp_path):
    mem, _, _ = world
    md = tmp_path / "props" / "fm.md"
    md.write_text(_fm_section(1, "MEMORY.md", "0" * 64), encoding="utf-8")
    with pytest.raises(ValueError, match="not a memory rule file"):
        lc.load_frontmatter_repairs(md, mem)


def test_G6_two_lessons_repairs_for_one_line_are_an_error(world, tmp_path):
    _, repo, _ = world
    md = _lessons_props(tmp_path, "0" * 64, "a", "b", 7)
    md.write_text(md.read_text(encoding="utf-8") + "\n## L7\n\nCurrent line:\n\n```text\nc\n```\n\n"
                  "Proposed line:\n\n```text\nd\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="two sections for L7"):
        lc.load_lessons_repairs(md, repo)


def test_G7_section_2_runs_past_other_headings_as_read_lessons_does(world):
    mem, repo, _ = world
    (repo / "memory/lessons.md").write_text(
        LESSONS + "\n## Notes\n\n| p9 | r9 | 3 | No | 2026-01-09 |\n\n## Section 1\n\n| a | b | c | d |\n",
        encoding="utf-8")
    rows = lc.lessons_transitions(repo, mem, [], None)
    assert [r["line"] for r in rows] == [7, 8, 12]


def test_G8_promoted_and_terminal_reads_terminal_before_and_after(world):
    mem, repo, _ = world
    (mem / "feedback_both.md").write_text(
        RULE.format(name="b", occ=3, promoted="yes - hook", gate="x").replace(
            "  promoted: yes - hook", "  promoted: yes - hook\n  terminal: yes"), encoding="utf-8")
    row = next(r for r in lc.memory_transitions(mem, repo, [], []) if r["file"] == "feedback_both.md")
    assert (row["before"], row["after"]) == ("terminal", "terminal")



def _hand(world, tmp_path, rows):
    he = tmp_path / "props" / "hand.jsonl"
    he.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    world[2]["hand_edit"] = he
    return he


def _hand_block(text):
    return text.split("**Writer-refused lines (B1's writer refuses a `promoted` line carrying `#`):**", 1)[1].split("**Defects", 1)[0]


def test_hand_edit_rows_are_listed_and_bound(world, tmp_path):
    mem, repo, inputs = world
    before = _digest(world)
    (mem / "feedback_gamma.md").write_text(RULE.format(name="g", occ=1, promoted='"no"  # note', gate="x"),
                                           encoding="utf-8")
    (mem / "feedback_delta.md").write_text(RULE.format(name="d", occ=1, promoted='"yes (PR #1)"', gate="x"),
                                           encoding="utf-8")
    _hand(world, tmp_path, [
        {"file": "feedback_gamma.md", "promoted_lines_verbatim": ['  promoted: "no"  # note'],
         "value_as_read": "no", "hash_is_yaml_comment": True},
        {"file": "feedback_delta.md", "promoted_lines_verbatim": ['  promoted: "yes (PR #1)"'],
         "value_as_read": "yes (PR #1)", "hash_is_yaml_comment": False}])
    assert lc.cutover_digest(mem, repo, inputs) != before
    block = _hand_block(_report(world, tmp_path)[0])
    assert "2 files." in block
    assert ('| `feedback_gamma.md` | `promoted: "no"  # note` | `no` | '
            'UNRESOLVED: blocks apply (a YAML comment no repair removes) |') in block
    assert ('| `feedback_delta.md` | `promoted: "yes (PR #1)"` | `yes (PR #1)` | '
            'no change needed: the # is inside the value and no proposal rewrites the line |') in block
    inputs["hand_edit"] = None
    text, _ = _report(world, tmp_path)
    assert "carrying `#`):**\n\ninput missing" in text
    assert "**Apply: not blocked** by any unresolved writer-refused line." in text


def test_r2_F1_rewrites_only_when_a_current_repair_targets_the_promoted_line(world, tmp_path):
    mem, _, inputs = world
    sha = lc.file_sha(mem / "feedback_alpha.md")
    md = tmp_path / "props" / "fm.md"
    _hand(world, tmp_path, [{"file": "feedback_alpha.md", "promoted_lines_verbatim": ["  promoted: no"],
                             "value_as_read": "no", "hash_is_yaml_comment": False}])
    inputs["frontmatter"] = md
    md.write_text(_fm_section(1, "feedback_alpha.md", sha, old="  type: feedback", new="  type: 'feedback'"),
                  encoding="utf-8")
    none = "| no change needed: the # is inside the value and no proposal rewrites the line |"
    assert _hand_block(_report(world, tmp_path)[0]).rstrip().endswith(none)
    md.write_text(_fm_section(1, "feedback_alpha.md", sha, old="  promoted: no", new="  promoted: 'no'"),
                  encoding="utf-8")
    assert _hand_block(_report(world, tmp_path)[0]).rstrip().endswith("| covered: a current repair rewrites this line |")
    md.write_text(_fm_section(1, "feedback_alpha.md", "0" * 64, old="  promoted: no", new="  promoted: 'no'"),
                  encoding="utf-8")
    assert _hand_block(_report(world, tmp_path)[0]).rstrip().endswith(none)


def test_r2_F2_cells_keep_pipes_and_backticks(world, tmp_path):
    assert lc._code_cell("a|b") == "`a\\|b`"
    assert lc._code_cell("x `y` z") == "``x `y` z``"
    assert lc._code_cell("`edge`") == "`` `edge` ``"


def test_r2_F3_the_current_line_is_read_from_the_live_file(world, tmp_path):
    _hand(world, tmp_path, [{"file": "feedback_alpha.md", "promoted_lines_verbatim": ['  promoted: "old"'],
                             "value_as_read": "old", "hash_is_yaml_comment": False}])
    block = _hand_block(_report(world, tmp_path)[0])
    assert "`promoted: no STALE: the input listed a different line`" in block
    assert "UNRESOLVED: blocks apply (the input is stale)" in block
    _hand(world, tmp_path, [{"file": "feedback_gone.md", "promoted_lines_verbatim": ["  promoted: no"],
                             "value_as_read": "no", "hash_is_yaml_comment": False}])
    assert "(file or line not found) STALE" in _hand_block(_report(world, tmp_path)[0])


def test_r2_F3_a_missing_file_with_no_listed_line_is_not_stale(world, tmp_path):
    assert lc._promoted_lines(world[0] / "feedback_gone.md") == []
    _hand(world, tmp_path, [{"file": "feedback_gone.md", "promoted_lines_verbatim": [],
                             "value_as_read": "", "hash_is_yaml_comment": False}])
    block = _hand_block(_report(world, tmp_path)[0])
    assert "(file or line not found)`" in block and "STALE" not in block



def test_an_uncovered_comment_line_blocks_apply_and_the_cli_exits_3(world, tmp_path, capsys):
    mem, repo, inputs = world
    (mem / "feedback_gamma.md").write_text(RULE.format(name="g", occ=1, promoted='"no"  # note', gate="x"),
                                           encoding="utf-8")
    he = _hand(world, tmp_path, [{"file": "feedback_gamma.md", "promoted_lines_verbatim": ['  promoted: "no"  # note'],
                                  "value_as_read": "no", "hash_is_yaml_comment": True}])
    text, _, _, unresolved = lc.build_report_entries(mem, repo, inputs)
    assert unresolved == ["feedback_gamma.md"]
    assert "**Apply: BLOCKED.** 1 unresolved defect(s): `feedback_gamma.md`" in text
    assert "Hand edit" not in text and "your call" not in text
    out = tmp_path / "r.md"
    assert lc.main(["report", "--memory-dir", str(mem), "--repo-root", str(repo), "--out", str(out),
                    "--no-default-inputs", "--hand-edit", str(he)]) == 3
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "blocked" and res["unresolved"] == ["feedback_gamma.md"]


def test_a_comment_line_covered_by_a_current_repair_does_not_block(world, tmp_path):
    mem, repo, inputs = world
    (mem / "feedback_gamma.md").write_text(RULE.format(name="g", occ=1, promoted='"no"  # note', gate="x"),
                                           encoding="utf-8")
    _hand(world, tmp_path, [{"file": "feedback_gamma.md", "promoted_lines_verbatim": ['  promoted: "no"  # note'],
                             "value_as_read": "no", "hash_is_yaml_comment": True}])
    md = tmp_path / "props" / "fm.md"
    md.write_text(_fm_section(1, "feedback_gamma.md", lc.file_sha(mem / "feedback_gamma.md"),
                              old='  promoted: "no"  # note', new='  promoted: "no"'), encoding="utf-8")
    inputs["frontmatter"] = md
    _, _, _, unresolved = lc.build_report_entries(mem, repo, inputs)
    assert unresolved == []


def test_r3_F1_a_repair_that_keeps_the_comment_does_not_cover(world, tmp_path):
    mem, repo, inputs = world
    (mem / "feedback_gamma.md").write_text(RULE.format(name="g", occ=1, promoted='"no"  # note', gate="x"),
                                           encoding="utf-8")
    _hand(world, tmp_path, [{"file": "feedback_gamma.md", "promoted_lines_verbatim": ['  promoted: "no"  # note'],
                             "value_as_read": "no", "hash_is_yaml_comment": True}])
    md = tmp_path / "props" / "fm.md"
    md.write_text(_fm_section(1, "feedback_gamma.md", lc.file_sha(mem / "feedback_gamma.md"),
                              old='  promoted: "no"  # note', new='  promoted: "no"  # still here'), encoding="utf-8")
    inputs["frontmatter"] = md
    _, _, _, unresolved = lc.build_report_entries(mem, repo, inputs)
    assert unresolved == ["feedback_gamma.md"]


@pytest.mark.parametrize("line,expected", [
    ('promoted: "no"  # c', True), ('promoted: "yes (PR #1)"', False), ("promoted: 'a #b'", False),
    ("promoted: 'it''s #1'  # c", True), ("promoted: no # c", True), ("promoted: a#b", False),
    ('promoted: "a \\" #b"', False), ("promoted: # c", True), ("promoted: 'it''s #1'", False),
    ('promoted: "unterminated #x', False)])
def test_r3_F1_yaml_comment_detection(line, expected):
    assert lc._has_yaml_comment(line) is expected


def test_r3_F2_below_2_with_a_threshold_keeps_the_label(world):
    mem, repo, _ = world
    rows = lc.memory_transitions(mem, repo, [{"file": "feedback_beta.md", "proposed_reopen_at": 3,
                                              "status": "number"}], [])
    assert {r["file"]: r["after"] for r in rows}["feedback_beta.md"] == "not_due (below 2)"



def test_r3_F1_a_stale_repair_never_covers(world, tmp_path):
    mem, repo, inputs = world
    (mem / "feedback_gamma.md").write_text(RULE.format(name="g", occ=1, promoted='"no"  # note', gate="x"),
                                           encoding="utf-8")
    _hand(world, tmp_path, [{"file": "feedback_gamma.md", "promoted_lines_verbatim": ['  promoted: "no"  # note'],
                             "value_as_read": "no", "hash_is_yaml_comment": True}])
    md = tmp_path / "props" / "fm.md"
    md.write_text(_fm_section(1, "feedback_gamma.md", "0" * 64, old='  promoted: "no"  # note',
                              new='  promoted: "no"'), encoding="utf-8")
    inputs["frontmatter"] = md
    assert lc._repaired_text(mem / "feedback_gamma.md", lc.load_frontmatter_repairs(md, mem)) == \
        (mem / "feedback_gamma.md").read_text(encoding="utf-8")
    assert lc.build_report_entries(mem, repo, inputs)[3] == ["feedback_gamma.md"]
