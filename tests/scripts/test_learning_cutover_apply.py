"""Tests for learning_cutover apply (B7b): the step that carries out an approved report.

Everything runs on copies in tmp_path. The bar (org 72): no state change lands without its
sanctioned writer, and no partial apply goes unreported.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import learning_cutover as lc  # noqa: E402
import memory_frontmatter as mf  # noqa: E402

RULE = """---
name: {name}
description: {desc}
metadata:
  type: feedback
  occurrences: {occ}
  promoted: {promoted}
---
body of {name}
"""

PATTERN = "Closes a long enough pattern for set-promoted"
LESSONS = f"""# Lessons

## Section 2

| Pattern | Rule | Occurrences | Promoted | Date |
|---|---|---|---|---|
| {PATTERN} | r1 | 2 | Superseded | 2026-01-01 |
"""

FRICTION = """# Friction

## Entries

| Date | Surface | Nature | Fix | Occurrences | Promotion |
|---|---|---|---|---|---|
| 2026-01-01 | tool.py | broke on input | did x | 2 | feedback |
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fm_section(n, f, file_sha, old, new):
    return (f"## {n}. `{f}`\n\n- sha256 of the current file: `{file_sha}`\n\nCurrent line:\n\n```yaml\n{old}\n```"
            f"\n\nProposed line:\n\n```yaml\n{new}\n```\n\n")


@pytest.fixture
def aworld(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    mem = tmp_path / "mem"
    mem.mkdir()
    repo = tmp_path / "repo"
    (repo / "memory").mkdir(parents=True)
    (repo / "memory/lessons.md").write_text(LESSONS, encoding="utf-8")
    (repo / "memory/friction-log.md").write_text(FRICTION, encoding="utf-8")
    (mem / "feedback_bad.md").write_text(RULE.format(name="feedback_bad", desc="a: b", occ=1, promoted="no"),
                                         encoding="utf-8")
    (mem / "feedback_rule.md").write_text(RULE.format(name="feedback_rule", desc="d", occ=3, promoted="no"),
                                          encoding="utf-8")
    (mem / "feedback_na.md").write_text(
        RULE.format(name="feedback_na", desc="d", occ=2, promoted='"n/a -- merged; see feedback_rule"'),
        encoding="utf-8")
    props = tmp_path / "props"
    props.mkdir()
    fm = props / "fm.md"
    fm.write_text(
        fm_section(1, "feedback_bad.md", sha(mem / "feedback_bad.md"), "description: a: b", "description: 'a: b'")
        + fm_section(2, "feedback_na.md", sha(mem / "feedback_na.md"),
                     '  promoted: "n/a -- merged; see feedback_rule"',
                     "  promoted: 'merged into feedback_rule (2026-01-01; earlier value: n/a)'"),
        encoding="utf-8")
    reopen = props / "reopen.jsonl"
    reopen.write_text(json.dumps({"file": "feedback_rule.md", "occurrences": 3, "proposed_reopen_at": 2,
                                  "status": "number", "prose_fragment": "2nd", "confidence": "high",
                                  "note": ""}) + "\n", encoding="utf-8")
    old_row = f"| {PATTERN} | r1 | 2 | Superseded | 2026-01-01 |"
    les = props / "les.md"
    les.write_text(f"**sha256 when these were made:** `{sha(repo / 'memory/lessons.md')}`\n\n## L7\n\n"
                   f"Current line:\n\n```text\n{old_row}\n```\n\nProposed line:\n\n```text\n"
                   f"| {PATTERN} | r1 | 2 | superseded by 2026-01-01 ruling | 2026-01-01 |\n```\n",
                   encoding="utf-8")
    inputs = {"reopen_at": reopen, "frontmatter": fm, "lessons": les, "i7": None, "hand_edit": None,
              "friction_kinds": None}
    return mem, repo, inputs, tmp_path


def approve(aworld):
    mem, repo, inputs, tmp = aworld
    out = tmp / "report.md"
    text, digest, ents, unresolved = lc.build_report_entries(mem, repo, inputs)
    out.write_text(text, encoding="utf-8")
    entries = out.with_suffix(".entries.json")
    entries.write_text(json.dumps(ents), encoding="utf-8")
    return digest, entries


def snapshot(mem, repo):
    return {p: p.read_bytes() for p in list(mem.iterdir()) + list((repo / "memory").iterdir())}


def test_full_apply_on_copies_leaves_nothing_pending(aworld):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "applied", res
    assert [s["step"] for s in res["applied"]] == [
        "repair", "reopen_at", "promoted", "lessons-set-promoted", "friction-kinds"]
    assert lc.pending_changes(mem, repo, inputs) == []
    assert mf.read_file(mem / "feedback_bad.md")[0]["description"] == "a: b"
    assert mf.read_file(mem / "feedback_rule.md")[0]["reopen_at"] == 2
    assert mf.read_file(mem / "feedback_na.md")[0]["promoted"].startswith("merged into feedback_rule")
    assert "superseded by 2026-01-01 ruling" in (repo / "memory/lessons.md").read_text(encoding="utf-8")
    assert "| Kind |" in (repo / "memory/friction-log.md").read_text(encoding="utf-8")
    text, _ = lc.build_report(mem, repo, inputs)
    assert "**Pending changes:** 0 " in text


def test_apply_refuses_outside_claude_code(aworld, monkeypatch):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    monkeypatch.delenv("CLAUDECODE")
    before = snapshot(mem, repo)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "refused" and "CLAUDECODE=1" in res["reasons"][0]
    assert snapshot(mem, repo) == before


def test_apply_refuses_on_digest_drift(aworld):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    (mem / "feedback_rule.md").write_text((mem / "feedback_rule.md").read_text(encoding="utf-8") + "x\n",
                                          encoding="utf-8")
    before = snapshot(mem, repo)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "refused" and res["applied"] == []
    assert "memory/feedback_rule.md" in res["verify"]["changed"]
    assert snapshot(mem, repo) == before


def test_apply_refuses_a_digest_that_is_not_the_entries_map(aworld):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    other = json.loads(entries.read_text(encoding="utf-8"))
    other["memory/feedback_extra.md"] = "0" * 64
    entries.write_text(json.dumps(other), encoding="utf-8")
    assert lc.apply_cutover(mem, repo, inputs, digest, entries)["status"] == "refused"


def test_a_file_changed_mid_apply_stops_and_reports(aworld, monkeypatch):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    real = mf.repair

    def repair_then_touch(*a, **k):
        out = real(*a, **k)
        p = mem / "feedback_rule.md"
        p.write_text(p.read_text(encoding="utf-8") + "touched\n", encoding="utf-8")
        return out

    monkeypatch.setattr(mf, "repair", repair_then_touch)
    na_before = (mem / "feedback_na.md").read_bytes()
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "stopped"
    assert [s["step"] for s in res["applied"]] == ["repair"]
    assert res["stopped_at"] == {"step": "reopen_at", "file": "feedback_rule.md", "value": 2}
    assert "changed since the approved state" in res["reasons"][0]
    assert [s["step"] for s in res["not_applied"]] == [
        "reopen_at", "promoted", "lessons-set-promoted", "friction-kinds"]
    assert (mem / "feedback_na.md").read_bytes() == na_before


def test_a_writer_refusal_stops_without_retry(aworld, monkeypatch):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    calls = []

    def refusing(path, updates, **k):
        calls.append(updates)
        raise mf.WriteRefused(["refused for the test"])

    monkeypatch.setattr(mf, "write", refusing)
    lessons_before = (repo / "memory/lessons.md").read_bytes()
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "stopped" and res["reasons"] == ["refused for the test"]
    assert calls == [{"reopen_at": 2}]
    assert [s["step"] for s in res["applied"]] == ["repair"]
    assert (repo / "memory/lessons.md").read_bytes() == lessons_before


def test_drop_promoted_is_a_reported_writer_gap(aworld):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    na_before = (mem / "feedback_na.md").read_bytes()
    res = lc.apply_cutover(mem, repo, inputs, digest, entries, frozenset({"feedback_na.md"}))
    assert res["status"] == "stopped" and res["stopped_at"]["step"] == "drop-promoted"
    assert "writer gap" in res["reasons"][0]
    assert (mem / "feedback_na.md").read_bytes() == na_before


def test_a_lessons_row_beyond_the_promoted_cell_goes_to_repair_row(aworld):
    mem, repo, inputs, _ = aworld
    les = inputs["lessons"]
    les.write_text(les.read_text(encoding="utf-8").replace(
        "| superseded by 2026-01-01 ruling | 2026-01-01 |", "| Superseded | 2026-01-01 <!-- x --> |"),
        encoding="utf-8")
    digest, entries = approve(aworld)
    steps = lc.plan_apply(mem, repo, inputs)
    assert [s["step"] for s in steps][-2] == "lessons-repair-row"
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "stopped" and res["stopped_at"]["step"] == "lessons-repair-row"
    if "writer gap" in res["reasons"][0]:  # lessons_write without repair-row (B2 before its final tip)
        assert "repair-row" in res["reasons"][0]


def test_plan_routes_each_proposal_to_its_writer(aworld):
    mem, repo, inputs, _ = aworld
    steps = lc.plan_apply(mem, repo, inputs)
    assert [(s["step"], s.get("file", s.get("line"))) for s in steps] == [
        ("repair", "feedback_bad.md"), ("reopen_at", "feedback_rule.md"), ("promoted", "feedback_na.md"),
        ("lessons-set-promoted", 7), ("friction-kinds", None)]
    assert steps[3]["match"] == PATTERN and steps[3]["value"] == "superseded by 2026-01-01 ruling"


def test_cli_apply_exit_codes(aworld, capsys):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    args = ["apply", "--memory-dir", str(mem), "--repo-root", str(repo), "--no-default-inputs",
            "--reopen-at", str(inputs["reopen_at"]), "--frontmatter", str(inputs["frontmatter"]),
            "--lessons", str(inputs["lessons"]), "--entries", str(entries)]
    assert lc.main(args + ["--expect-sha", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert lc.main(args + ["--expect-sha", digest]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"


LIVE = os.environ.get("LEARNING_CUTOVER_LIVE_MEMORY_DIR")


@pytest.mark.skipif(not LIVE, reason="set LEARNING_CUTOVER_LIVE_MEMORY_DIR to copy a real memory folder")
def test_full_apply_over_a_copy_of_the_live_memory(tmp_path, monkeypatch):
    """Copies only: the live folder, lessons.md, the friction ledger and the proposal inputs.
    The live folder's bytes are compared before and after."""
    monkeypatch.setenv("CLAUDECODE", "1")
    import subprocess
    live = Path(LIVE)
    live_before = {p.name: sha(p) for p in live.glob("*.md")}
    # memory/ and output/ are gitignored, so they live only in the main checkout
    main = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                               cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()).parent
    mem, repo = tmp_path / "mem", tmp_path / "repo"
    shutil.copytree(live, mem, ignore=shutil.ignore_patterns("*.jsonl", "*.json"))
    (repo / "memory").mkdir(parents=True)
    for name in ("lessons.md", "friction-log.md"):
        shutil.copy(main / "memory" / name, repo / "memory" / name)
    inputs = {}
    for k, rel in lc.DEFAULT_INPUTS.items():
        src = main / rel
        if src.is_file():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, repo / rel)
            inputs[k] = repo / rel
        else:
            inputs[k] = None
    text, digest, ents, _ = lc.build_report_entries(mem, repo, inputs)
    entries = tmp_path / "e.json"
    entries.write_text(json.dumps(ents), encoding="utf-8")
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    pending = lc.pending_changes(mem, repo, inputs)
    if res["status"] == "applied":
        assert pending == []
    else:  # only a reported writer gap may stop it, and only the steps not applied are pending
        assert res["status"] == "stopped" and "writer gap" in res["reasons"][0], res
        assert len(pending) <= len(res["not_applied"])
    assert {p.name: sha(p) for p in live.glob("*.md")} == live_before


# ---------------------------------------------------------------- pinned helpers (mutation r1)

def test_promoted_value_routes_only_clean_single_promoted_lines():
    clean = RULE.format(name="x", desc="d", occ=1, promoted="no")
    fix = {"old": "  promoted: no", "new": "  promoted: 'yes - skill'"}
    assert lc._promoted_value(fix, clean) == "yes - skill"
    assert lc._promoted_value({"old": "  promoted: no\n  type: feedback", "new": "  promoted: x\n  type: feedback"},
                              clean) is None
    assert lc._promoted_value({"old": "  type: feedback", "new": "  type: 'feedback'"}, clean) is None
    commented = clean.replace("promoted: no", 'promoted: "no"  # c')
    assert lc._promoted_value({"old": '  promoted: "no"  # c', "new": '  promoted: "no"'}, commented) is None
    broken = clean.replace("description: d", "description: a: b")
    assert lc._promoted_value(fix, broken) is None
    assert lc._promoted_value({"old": "  promoted: no", "new": "  promoted: a: b"}, clean) is None


def test_repair_args_derive_block_intended_and_note():
    text = RULE.format(name="x", desc="a: b", occ=1, promoted='"no"  # gone')
    block, intended, note = lc._repair_args(text, {"old": "description: a: b", "new": "description: 'a: b'"})
    assert intended == {"description": "a: b"} and note is None and "description: 'a: b'" in block
    clean = RULE.format(name="x", desc="d", occ=1, promoted='"no"  # gone')
    old = '  promoted: "no"  # gone\n---'
    _, intended, note = lc._repair_args(clean, {"old": old, "new": '  promoted: "no"\n---\n\n> 2026-01-01 note: gone'})
    assert intended == {"promoted": "no"} and note == "> 2026-01-01 note: gone"
    with pytest.raises(ValueError, match="beyond one note line"):
        lc._repair_args(text, {"old": "---\nbody of x", "new": "---\nother body"})
    with pytest.raises(ValueError, match="beyond one note line"):
        lc._repair_args(clean, {"old": '  promoted: "no"  # gone\n---',
                               "new": '  promoted: "no"\n---\n\n> 2026-01-01 a\n> b'})
    with pytest.raises(ValueError, match="changes no key line"):
        lc._repair_args(clean, {"old": "  type: feedback", "new": "  type: feedback\n# a comment line"})
    with pytest.raises(ValueError, match="does not read"):
        lc._repair_args(clean, {"old": "  type: feedback", "new": "  type: f: g"})
    with pytest.raises(ValueError, match="no ---/--- block"):
        lc._repair_args("no block\n", {"old": "no block", "new": "x"})


def test_repair_args_count_a_removed_duplicate_line():
    text = RULE.format(name="x", desc="d", occ=1, promoted="no").replace(
        "  promoted: no\n", "  promoted: no\n  exit_path: exit1\n  last: 1\n  exit_path: exit1\n")
    _, intended, _ = lc._repair_args(text, {"old": "  last: 1\n  exit_path: exit1", "new": "  last: 1"})
    assert intended == {"exit_path": "exit1"}


def test_stop_result_lists_exactly_the_brief_fields(aworld, monkeypatch):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    monkeypatch.setattr(mf, "write", lambda *a, **k: (_ for _ in ()).throw(mf.WriteRefused(["no"])))
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["not_applied"][0] == {"step": "reopen_at", "file": "feedback_rule.md", "value": 2}
    assert res["not_applied"][-1] == {"step": "friction-kinds"}
    assert str(lc.ApplyStop({"step": "x"}, ["a", "b"])) == "a; b"


def test_lessons_set_promoted_refusal_stops(aworld):
    mem, repo, inputs, _ = aworld
    (repo / "memory/lessons.md").write_text(LESSONS + f"| {PATTERN} again | r | 1 | No | 2026-01-02 |\n",
                                            encoding="utf-8")
    les = inputs["lessons"]
    les.write_text(les.read_text(encoding="utf-8").replace(
        les.read_text(encoding="utf-8").split("`")[1], sha(repo / "memory/lessons.md")), encoding="utf-8")
    digest, entries = approve(aworld)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "stopped" and res["stopped_at"]["step"] == "lessons-set-promoted"
    assert "set-promoted exit 1" in res["reasons"][0] and "matches 2" in res["reasons"][0]
    assert [s["step"] for s in res["not_applied"]] == ["lessons-set-promoted", "friction-kinds"]


def test_lessons_repair_row_applies_a_text_preserving_move(aworld):
    mem, repo, inputs, _ = aworld
    row = f"| {PATTERN} | r1 | 2 | No | 2026-01-01 | <!-- c -->"
    (repo / "memory/lessons.md").write_text(LESSONS.replace(
        f"| {PATTERN} | r1 | 2 | Superseded | 2026-01-01 |", row), encoding="utf-8")
    new = f"| {PATTERN} | r1 | 2 | No | 2026-01-01 <!-- c --> |"
    inputs["lessons"].write_text(f"**sha256 when these were made:** `{sha(repo / 'memory/lessons.md')}`\n\n## L7\n\n"
                                 f"Current line:\n\n```text\n{row}\n```\n\nProposed line:\n\n```text\n{new}\n```\n",
                                 encoding="utf-8")
    digest, entries = approve(aworld)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "applied", res
    assert new in (repo / "memory/lessons.md").read_text(encoding="utf-8").split("\n")
    assert lc.pending_changes(mem, repo, inputs) == []


def test_lessons_repair_row_refusal_is_not_called_a_writer_gap(aworld):
    mem, repo, inputs, _ = aworld
    les = inputs["lessons"]
    les.write_text(les.read_text(encoding="utf-8").replace(
        "| superseded by 2026-01-01 ruling | 2026-01-01 |", "| Superseded | 2026-01-01 extra |"), encoding="utf-8")
    digest, entries = approve(aworld)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["stopped_at"]["step"] == "lessons-repair-row"
    assert "repair-row exit 1" in res["reasons"][0] and "writer gap" not in res["reasons"][0]


@pytest.mark.parametrize("which,result,expect", [
    ("--dry-run", (1, "boom"), "--dry-run exit 1"),
    ("--dry-run", (0, {"status": "ok"}), "--dry-run exit 0"),
    ("--dry-run", (0, "not json"), "--dry-run exit 0"),
    ("--apply", (1, "refused"), "--apply exit 1")])
def test_friction_refusals_stop(aworld, monkeypatch, which, result, expect):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    real = lc._cli
    monkeypatch.setattr(lc, "_cli", lambda s, a, e: result if (s == "friction_log.py" and which in a)
                        else real(s, a, e))
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["stopped_at"] == {"step": "friction-kinds"} and expect in res["reasons"][0]
    assert len(res["applied"]) == 4


def test_cli_parses_non_json_output():
    rc, out = lc._cli("lessons_write.py", ["--no-such-flag"], {})
    assert rc == 2 and isinstance(out, str) and "usage" in out


def test_pending_lists_each_kind_until_applied(aworld):
    mem, repo, inputs, _ = aworld
    assert lc.pending_changes(mem, repo, inputs) == [
        {"change": "frontmatter repair", "file": "feedback_bad.md"},
        {"change": "frontmatter repair", "file": "feedback_na.md"},
        {"change": "reopen_at", "file": "feedback_rule.md"},
        {"change": "lessons row", "line": 7}]
    inputs["lessons"] = None
    assert {"change": "lessons row", "line": 7} not in lc.pending_changes(mem, repo, inputs)
    rows = [json.loads(x) for x in inputs["reopen_at"].read_text(encoding="utf-8").splitlines()]
    rows[0]["status"] = "relative"
    inputs["reopen_at"].write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    assert all(p["change"] != "reopen_at" for p in lc.pending_changes(mem, repo, inputs))


def test_report_after_apply_marks_a_removed_comment_resolved(aworld, tmp_path):
    mem, repo, inputs, _ = aworld
    he = tmp_path / "hand.jsonl"
    he.write_text(json.dumps({"file": "feedback_rule.md", "promoted_lines_verbatim": ['  promoted: "no"  # c'],
                              "value_as_read": "no", "hash_is_yaml_comment": True}) + "\n", encoding="utf-8")
    inputs["hand_edit"] = he
    text, _, _, unresolved = lc.build_report_entries(mem, repo, inputs)
    assert unresolved == [] and "resolved: the line no longer carries a YAML comment" in text


def test_label_names_only_files_the_report_hashes():
    assert lc._label({"step": "repair", "file": "f.md"}) == "memory/f.md"
    assert lc._label({"step": "lessons-set-promoted", "line": 7}) == "repo/memory/lessons.md"
    assert lc._label({"step": "friction-kinds"}) is None


def test_two_steps_on_one_file_track_the_sha_between_them(aworld):
    mem, repo, inputs, _ = aworld
    rows = inputs["reopen_at"].read_text(encoding="utf-8")
    inputs["reopen_at"].write_text(rows + json.dumps({
        "file": "feedback_bad.md", "occurrences": 1, "proposed_reopen_at": 2, "status": "number",
        "prose_fragment": "x", "confidence": "high", "note": ""}) + "\n", encoding="utf-8")
    digest, entries = approve(aworld)
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "applied", res
    assert mf.read_file(mem / "feedback_bad.md")[0]["reopen_at"] == 2


def test_pending_without_a_lessons_input_needs_no_lessons_file(aworld):
    mem, repo, inputs, _ = aworld
    inputs["lessons"] = None
    (repo / "memory/lessons.md").unlink()
    assert {"change": "reopen_at", "file": "feedback_rule.md"} in lc.pending_changes(mem, repo, inputs)


def test_r1_F3_set_promoted_only_when_its_output_is_the_proposed_row(aworld):
    mem, repo, inputs, _ = aworld
    les = inputs["lessons"]
    les.write_text(les.read_text(encoding="utf-8").replace(
        "| superseded by 2026-01-01 ruling | 2026-01-01 |", "|  superseded by 2026-01-01 ruling | 2026-01-01 |"),
        encoding="utf-8")
    assert lc.plan_apply(mem, repo, inputs)[3]["step"] == "lessons-repair-row"


def test_r1_F4_an_unexpected_error_is_reported_with_what_was_applied(aworld, monkeypatch):
    mem, repo, inputs, _ = aworld
    digest, entries = approve(aworld)
    monkeypatch.setattr(mf, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    res = lc.apply_cutover(mem, repo, inputs, digest, entries)
    assert res["status"] == "stopped" and res["reasons"] == ["unexpected OSError: disk"]
    assert [s["step"] for s in res["applied"]] == ["repair"]
    assert res["not_applied"][0]["step"] == "reopen_at"
